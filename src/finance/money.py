"""
Money and exchange-rate arithmetic for the finance module.

Every amount in the finance database is an integer in the currency's
minor unit (sen, fen, cents), and every rate is an integer scaled by
`FX_RATE_SCALE`. Floats never touch money here: binary floating point
cannot represent 0.01 exactly, so repeated conversion drifts.

All rounding is ROUND_HALF_UP, which is what a person expects when
reading a receipt, rather than Python's default banker's rounding.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from src.db.finance_sqlite import FX_RATE_SCALE


# Number of minor units in one major unit. Every currency this module
# handles (MYR, CNY) uses two decimal places.
MINOR_UNITS_PER_MAJOR = 100

_FX_RATE_SCALE_DECIMAL = Decimal(FX_RATE_SCALE)


def to_decimal(value: Decimal | int | str) -> Decimal:
    """
    Coerce a money-ish value to Decimal without going through float.

    Floats are rejected rather than converted, because
    `Decimal(0.1)` yields 0.1000000000000000055511151231257827.
    """
    if isinstance(value, Decimal):
        return value

    if isinstance(value, bool) or isinstance(value, float):
        raise TypeError(
            f"Refusing to build a money value from {type(value).__name__}. "
            "Pass a str, int, or Decimal so no precision is lost."
        )

    return Decimal(value)


def to_minor_units(value: Decimal | int | str) -> int:
    """Convert a major-unit amount (12.34) to minor units (1234)."""
    amount = to_decimal(value) * MINOR_UNITS_PER_MAJOR

    return int(amount.to_integral_value(rounding=ROUND_HALF_UP))


def from_minor_units(minor_units: int) -> Decimal:
    """Convert minor units (1234) back to a major-unit Decimal (12.34)."""
    return (
        Decimal(minor_units) / MINOR_UNITS_PER_MAJOR
    ).quantize(Decimal("0.01"))


def format_minor_units(minor_units: int) -> str:
    """Render minor units for display, with thousands separators."""
    return f"{from_minor_units(minor_units):,.2f}"


def scale_rate(rate: Decimal | int | str) -> int:
    """Convert a human rate (0.58487395) to its stored integer form."""
    scaled = to_decimal(rate) * _FX_RATE_SCALE_DECIMAL

    return int(scaled.to_integral_value(rounding=ROUND_HALF_UP))


def unscale_rate(rate_scaled: int) -> Decimal:
    """Convert a stored integer rate back to a human Decimal rate."""
    return Decimal(rate_scaled) / _FX_RATE_SCALE_DECIMAL


def derive_rate_scaled(
    amount: Decimal | int | str,
    base_amount: Decimal | int | str,
) -> int:
    """
    Derive the rate implied by an amount and its converted base amount.

    Used by the Money Manager import, where the converted MYR column is
    the source of truth and the rate is inferred from it.
    """
    amount_decimal = to_decimal(amount)
    if amount_decimal == 0:
        raise ValueError("Cannot derive an exchange rate from a zero amount.")

    return scale_rate(to_decimal(base_amount) / amount_decimal)


def convert_to_base_minor(amount_minor: int, fx_rate_scaled: int) -> int:
    """
    Apply a stored rate to a minor-unit amount, returning base minor units.

    Rounded once, at the end, so a long chain of conversions cannot
    accumulate half-sen errors.
    """
    converted = (
        Decimal(amount_minor) * Decimal(fx_rate_scaled) / _FX_RATE_SCALE_DECIMAL
    )

    return int(converted.to_integral_value(rounding=ROUND_HALF_UP))
