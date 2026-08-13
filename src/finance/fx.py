"""
Exchange-rate policy and resolution for the finance module.

`exchange_rate_settings` stores the current *policy* per currency, not a
history. Rate history lives implicitly on each transaction, because
`fx_rate_scaled` is locked in when the transaction is recorded and is
never recalculated afterwards. Editing a rate policy therefore changes
future transactions only, which is what keeps historical reports stable.

Auto mode resolves the rate for the transaction's own date rather than
today, so a backfilled entry gets the rate that actually applied then.

There is deliberately no silent fallback. If a rate cannot be resolved,
`FxRateUnavailableError` is raised so the caller can surface the problem
rather than persisting a wrong number that later looks authoritative.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path

import httpx

from src.db.finance_sqlite import FINANCE_DB_PATH, FX_RATE_SCALE, finance_db
from src.finance.money import scale_rate, to_decimal


BASE_CURRENCY = "MYR"

# Frankfurter publishes ECB reference rates, is free, needs no API key,
# and supports historical lookup by date. Both MYR and CNY are covered.
FRANKFURTER_BASE_URL = "https://api.frankfurter.dev/v1"

FX_REQUEST_TIMEOUT_SECONDS = 10.0

VALID_MODES = ("auto", "manual")


class FxRateUnavailableError(RuntimeError):
    """Raised when no trustworthy rate could be resolved."""


@dataclass(frozen=True)
class ExchangeRateSetting:
    """The current rate policy for one currency."""

    currency: str
    mode: str
    manual_rate_scaled: int | None
    updated_at: str

    @property
    def manual_rate(self) -> Decimal | None:
        if self.manual_rate_scaled is None:
            return None

        return Decimal(self.manual_rate_scaled) / Decimal(FX_RATE_SCALE)


def normalize_currency(currency: str) -> str:
    """Uppercase and validate a currency code against the schema's CHECK."""
    code = currency.strip().upper()

    if len(code) != 3 or not code.isalpha():
        raise ValueError(
            f"{currency!r} is not a 3-letter currency code."
        )

    return code


# --- Settings -------------------------------------------------------


def get_exchange_rate_setting(
    currency: str,
    db_path: str | Path | None = None,
) -> ExchangeRateSetting | None:
    """Return the stored policy for a currency, or None if unset."""
    code = normalize_currency(currency)

    with finance_db(db_path) as connection:
        row = connection.execute(
            """
            SELECT currency, mode, manual_rate_scaled, updated_at
            FROM exchange_rate_settings
            WHERE currency = ?
            """,
            (code,),
        ).fetchone()

    if row is None:
        return None

    return ExchangeRateSetting(
        currency=row["currency"],
        mode=row["mode"],
        manual_rate_scaled=row["manual_rate_scaled"],
        updated_at=row["updated_at"],
    )


def list_exchange_rate_settings(
    db_path: str | Path | None = None,
) -> list[ExchangeRateSetting]:
    """Return every stored rate policy, ordered by currency."""
    with finance_db(db_path) as connection:
        rows = connection.execute(
            """
            SELECT currency, mode, manual_rate_scaled, updated_at
            FROM exchange_rate_settings
            ORDER BY currency
            """
        ).fetchall()

    return [
        ExchangeRateSetting(
            currency=row["currency"],
            mode=row["mode"],
            manual_rate_scaled=row["manual_rate_scaled"],
            updated_at=row["updated_at"],
        )
        for row in rows
    ]


def set_exchange_rate_setting(
    currency: str,
    mode: str,
    manual_rate: Decimal | int | str | None = None,
    db_path: str | Path | None = None,
) -> ExchangeRateSetting:
    """
    Create or replace the rate policy for one currency.

    Changing a policy never rewrites existing transactions; their rates
    were locked in when they were recorded.
    """
    code = normalize_currency(currency)

    if mode not in VALID_MODES:
        raise ValueError(f"mode must be one of {VALID_MODES}, got {mode!r}.")

    if code == BASE_CURRENCY:
        raise ValueError(
            f"{BASE_CURRENCY} is the base currency and always converts at 1.0."
        )

    manual_rate_scaled: int | None = None

    if mode == "manual":
        if manual_rate is None:
            raise ValueError("manual mode requires a manual_rate.")

        manual_rate_scaled = scale_rate(manual_rate)

        if manual_rate_scaled <= 0:
            raise ValueError("manual_rate must be greater than zero.")

    with finance_db(db_path) as connection:
        connection.execute(
            """
            INSERT INTO exchange_rate_settings (
                currency, mode, manual_rate_scaled, updated_at
            ) VALUES (
                ?, ?, ?, strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
            )
            ON CONFLICT (currency) DO UPDATE SET
                mode = excluded.mode,
                manual_rate_scaled = excluded.manual_rate_scaled,
                updated_at = excluded.updated_at
            """,
            (code, mode, manual_rate_scaled),
        )

    setting = get_exchange_rate_setting(code, db_path)
    if setting is None:
        raise RuntimeError(f"Failed to store the rate policy for {code}.")

    return setting


# --- Rate resolution ------------------------------------------------


def fetch_historical_rate_scaled(
    currency: str,
    on_date: date,
    base_currency: str = BASE_CURRENCY,
) -> tuple[int, str]:
    """
    Fetch the rate from `currency` to `base_currency` for a given date.

    Returns the scaled rate and the date the provider actually used.
    Those differ on weekends and holidays, when Frankfurter falls back
    to the most recent publication, so the caller can report which
    day's rate was applied.
    """
    code = normalize_currency(currency)
    base = normalize_currency(base_currency)

    url = f"{FRANKFURTER_BASE_URL}/{on_date.isoformat()}"

    try:
        response = httpx.get(
            url,
            params={"base": code, "symbols": base},
            timeout=FX_REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        payload = response.json()
    except httpx.HTTPError as error:
        raise FxRateUnavailableError(
            f"Could not reach the exchange-rate service for "
            f"{code}->{base} on {on_date.isoformat()}: {error}"
        ) from error

    rate = payload.get("rates", {}).get(base)

    if rate is None:
        raise FxRateUnavailableError(
            f"The exchange-rate service returned no {base} rate for "
            f"{code} on {on_date.isoformat()}. It may not cover this currency; "
            f"set a manual rate for {code} instead."
        )

    # The provider returns JSON numbers, so round-trip through str to
    # keep the value out of binary floating point.
    return scale_rate(to_decimal(str(rate))), str(payload.get("date", on_date))


def resolve_fx_rate_scaled(
    currency: str,
    occurred_on: date,
    db_path: str | Path | None = None,
) -> int:
    """
    Resolve the rate to apply to a transaction, as a scaled integer.

    Called once when a transaction is recorded. The result is stored on
    the row and never recalculated.
    """
    code = normalize_currency(currency)

    if code == BASE_CURRENCY:
        return FX_RATE_SCALE

    setting = get_exchange_rate_setting(code, db_path)

    if setting is not None and setting.mode == "manual":
        if setting.manual_rate_scaled is None:
            raise FxRateUnavailableError(
                f"{code} is set to manual mode but has no stored rate."
            )

        return setting.manual_rate_scaled

    # No stored policy means auto, which matches the schema default.
    rate_scaled, _ = fetch_historical_rate_scaled(code, occurred_on)

    return rate_scaled
