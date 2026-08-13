"""
The finance service layer: the single write path into the finance database.

Both the dashboard API and the agent tools call these functions rather
than issuing SQL of their own, so validation, FX resolution, and money
arithmetic cannot drift between the two entry points.

Two rules are enforced here rather than trusted to callers:

- `base_amount_minor` is always computed server-side from the resolved
  rate. An LLM must never supply a converted amount, because arithmetic
  is exactly what it is least reliable at.
- Categories, subcategories, and accounts must already exist and be
  active. Nothing is auto-created on the write path, so a typo produces
  a clear error instead of silently spawning a near-duplicate category.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from src.db.finance_sqlite import FINANCE_DB_PATH, finance_db
from src.finance.fx import BASE_CURRENCY, normalize_currency, resolve_fx_rate_scaled
from src.finance.money import convert_to_base_minor, to_minor_units

# Reuses the existing timezone resolution, which falls back to dateutil
# when the IANA database is unavailable. Windows CPython builds do not
# ship tzdata, so ZoneInfo alone would fail here.
from src.tools.general import DEFAULT_TIMEZONE, _resolve_timezone


VALID_DIRECTIONS = ("expense", "income")
VALID_SOURCES = ("manual", "agent", "import")

DEFAULT_ACCOUNT = "Bank Accounts"


class FinanceError(Exception):
    """Base class for finance service errors that callers can surface."""


class UnknownAccountError(FinanceError):
    """The named account does not exist or is inactive."""


class UnknownCategoryError(FinanceError):
    """The named category does not exist or is inactive."""


class UnknownSubcategoryError(FinanceError):
    """The named subcategory does not exist under the given category."""


@dataclass(frozen=True)
class Account:
    id: int
    name: str
    is_active: bool


@dataclass(frozen=True)
class Category:
    id: int
    name: str
    emoji: str | None
    is_active: bool


@dataclass(frozen=True)
class Subcategory:
    id: int
    category_id: int
    category_name: str
    name: str
    is_active: bool


@dataclass(frozen=True)
class Transaction:
    """A stored transaction with its lookups already resolved to names."""

    id: int
    occurred_at: str
    account: str
    category: str
    subcategory: str | None
    note: str | None
    description: str | None
    direction: str
    amount_minor: int
    currency: str
    fx_rate_scaled: int
    base_amount_minor: int
    base_currency: str
    source: str


def local_now() -> datetime:
    """
    Return the current Malaysia wall-clock time, without a timezone.

    Naive on purpose: see the finance time-storage rule in `context.md`.
    """
    return datetime.now(_resolve_timezone(DEFAULT_TIMEZONE)).replace(
        tzinfo=None,
        microsecond=0,
    )


# --- Reference data -------------------------------------------------


def list_accounts(
    include_inactive: bool = False,
    db_path: str | Path = FINANCE_DB_PATH,
) -> list[Account]:
    """Return accounts, active-only unless asked otherwise."""
    clause = "" if include_inactive else "WHERE is_active = 1"

    with finance_db(db_path) as connection:
        rows = connection.execute(
            f"SELECT id, name, is_active FROM accounts {clause} ORDER BY name"
        ).fetchall()

    return [
        Account(id=row["id"], name=row["name"], is_active=bool(row["is_active"]))
        for row in rows
    ]


def list_categories(
    include_inactive: bool = False,
    db_path: str | Path = FINANCE_DB_PATH,
) -> list[Category]:
    """Return categories, active-only unless asked otherwise."""
    clause = "" if include_inactive else "WHERE is_active = 1"

    with finance_db(db_path) as connection:
        rows = connection.execute(
            f"SELECT id, name, emoji, is_active FROM categories {clause} ORDER BY name"
        ).fetchall()

    return [
        Category(
            id=row["id"],
            name=row["name"],
            emoji=row["emoji"],
            is_active=bool(row["is_active"]),
        )
        for row in rows
    ]


def list_subcategories(
    category: str | None = None,
    include_inactive: bool = False,
    db_path: str | Path = FINANCE_DB_PATH,
) -> list[Subcategory]:
    """Return subcategories, optionally restricted to one category."""
    conditions: list[str] = []
    parameters: list[object] = []

    if not include_inactive:
        conditions.append("s.is_active = 1")

    if category is not None:
        conditions.append("c.name = ?")
        parameters.append(category.strip())

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

    with finance_db(db_path) as connection:
        rows = connection.execute(
            f"""
            SELECT s.id, s.category_id, c.name AS category_name,
                   s.name, s.is_active
            FROM subcategories s
            JOIN categories c ON c.id = s.category_id
            {where}
            ORDER BY c.name, s.name
            """,
            parameters,
        ).fetchall()

    return [
        Subcategory(
            id=row["id"],
            category_id=row["category_id"],
            category_name=row["category_name"],
            name=row["name"],
            is_active=bool(row["is_active"]),
        )
        for row in rows
    ]


# --- Lookup helpers -------------------------------------------------


def _lookup_account_id(connection: sqlite3.Connection, name: str) -> int:
    row = connection.execute(
        "SELECT id FROM accounts WHERE name = ? AND is_active = 1",
        (name.strip(),),
    ).fetchone()

    if row is None:
        available = connection.execute(
            "SELECT name FROM accounts WHERE is_active = 1 ORDER BY name"
        ).fetchall()
        raise UnknownAccountError(
            f"Unknown or inactive account {name!r}. "
            f"Available: {', '.join(item['name'] for item in available) or 'none'}."
        )

    return int(row["id"])


def _lookup_category_id(connection: sqlite3.Connection, name: str) -> int:
    row = connection.execute(
        "SELECT id FROM categories WHERE name = ? AND is_active = 1",
        (name.strip(),),
    ).fetchone()

    if row is None:
        available = connection.execute(
            "SELECT name FROM categories WHERE is_active = 1 ORDER BY name"
        ).fetchall()
        raise UnknownCategoryError(
            f"Unknown or inactive category {name!r}. "
            f"Available: {', '.join(item['name'] for item in available) or 'none'}."
        )

    return int(row["id"])


def _lookup_subcategory_id(
    connection: sqlite3.Connection,
    category_id: int,
    category_name: str,
    name: str,
) -> int:
    row = connection.execute(
        """
        SELECT id FROM subcategories
        WHERE category_id = ? AND name = ? AND is_active = 1
        """,
        (category_id, name.strip()),
    ).fetchone()

    if row is None:
        available = connection.execute(
            """
            SELECT name FROM subcategories
            WHERE category_id = ? AND is_active = 1
            ORDER BY name
            """,
            (category_id,),
        ).fetchall()
        raise UnknownSubcategoryError(
            f"Unknown or inactive subcategory {name!r} under {category_name!r}. "
            f"Available: {', '.join(item['name'] for item in available) or 'none'}."
        )

    return int(row["id"])


# --- Reference-data mutation ----------------------------------------


def add_category(
    name: str,
    emoji: str | None = None,
    db_path: str | Path = FINANCE_DB_PATH,
) -> Category:
    """Create a category. Names are unique case-insensitively."""
    clean_name = name.strip()
    if not clean_name:
        raise FinanceError("Category name cannot be empty.")

    with finance_db(db_path) as connection:
        try:
            connection.execute(
                "INSERT INTO categories (name, emoji) VALUES (?, ?)",
                (clean_name, (emoji or "").strip() or None),
            )
        except sqlite3.IntegrityError as error:
            raise FinanceError(
                f"A category named {clean_name!r} already exists."
            ) from error

    for category in list_categories(include_inactive=True, db_path=db_path):
        if category.name.casefold() == clean_name.casefold():
            return category

    raise FinanceError(f"Failed to create category {clean_name!r}.")


def add_subcategory(
    category: str,
    name: str,
    db_path: str | Path = FINANCE_DB_PATH,
) -> Subcategory:
    """Create a subcategory under an existing active category."""
    clean_name = name.strip()
    if not clean_name:
        raise FinanceError("Subcategory name cannot be empty.")

    with finance_db(db_path) as connection:
        category_id = _lookup_category_id(connection, category)

        try:
            connection.execute(
                "INSERT INTO subcategories (category_id, name) VALUES (?, ?)",
                (category_id, clean_name),
            )
        except sqlite3.IntegrityError as error:
            raise FinanceError(
                f"{clean_name!r} already exists under {category!r}."
            ) from error

    for subcategory in list_subcategories(
        category=category, include_inactive=True, db_path=db_path
    ):
        if subcategory.name.casefold() == clean_name.casefold():
            return subcategory

    raise FinanceError(f"Failed to create subcategory {clean_name!r}.")


# --- Transactions ---------------------------------------------------


def record_transaction(
    amount: Decimal | int | str,
    category: str,
    currency: str = BASE_CURRENCY,
    direction: str = "expense",
    account: str = DEFAULT_ACCOUNT,
    subcategory: str | None = None,
    occurred_at: datetime | None = None,
    note: str | None = None,
    description: str | None = None,
    source: str = "manual",
    db_path: str | Path = FINANCE_DB_PATH,
) -> Transaction:
    """
    Record one transaction, resolving its exchange rate and base amount.

    `amount` is in the transaction's own currency and must be positive;
    `direction` carries the sign. Pass it as a str or Decimal, never a
    float, so no precision is lost before it reaches the database.
    """
    if direction not in VALID_DIRECTIONS:
        raise FinanceError(
            f"direction must be one of {VALID_DIRECTIONS}, got {direction!r}."
        )

    if source not in VALID_SOURCES:
        raise FinanceError(
            f"source must be one of {VALID_SOURCES}, got {source!r}."
        )

    code = normalize_currency(currency)
    amount_minor = to_minor_units(amount)

    if amount_minor <= 0:
        raise FinanceError(
            f"Amount must be greater than zero, got {amount}. "
            "Use direction='expense' or 'income' to indicate the sign."
        )

    moment = occurred_at or local_now()
    if moment.tzinfo is not None:
        # Callers may pass an aware datetime; store the local wall clock.
        moment = moment.astimezone(
            _resolve_timezone(DEFAULT_TIMEZONE)
        ).replace(tzinfo=None)

    moment = moment.replace(microsecond=0)

    # Resolved before opening the write transaction, because auto mode
    # makes a network call and should not hold a database lock.
    fx_rate_scaled = resolve_fx_rate_scaled(code, moment.date(), db_path)
    base_amount_minor = convert_to_base_minor(amount_minor, fx_rate_scaled)

    if base_amount_minor <= 0:
        raise FinanceError(
            f"{amount} {code} converts to {base_amount_minor} sen, which the "
            "schema rejects. The amount is below the smallest storable value."
        )

    with finance_db(db_path) as connection:
        account_id = _lookup_account_id(connection, account)
        category_id = _lookup_category_id(connection, category)

        subcategory_id: int | None = None
        if subcategory:
            subcategory_id = _lookup_subcategory_id(
                connection, category_id, category, subcategory
            )

        cursor = connection.execute(
            """
            INSERT INTO transactions (
                occurred_at, account_id, category_id, subcategory_id,
                note, description, direction, amount_minor, currency,
                fx_rate_scaled, base_amount_minor, base_currency, source
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                moment.isoformat(),
                account_id,
                category_id,
                subcategory_id,
                (note or "").strip() or None,
                (description or "").strip() or None,
                direction,
                amount_minor,
                code,
                fx_rate_scaled,
                base_amount_minor,
                BASE_CURRENCY,
                source,
            ),
        )
        transaction_id = int(cursor.lastrowid)

    stored = get_transaction(transaction_id, db_path)
    if stored is None:
        raise FinanceError("Transaction was inserted but could not be read back.")

    return stored


def get_transaction(
    transaction_id: int,
    db_path: str | Path = FINANCE_DB_PATH,
) -> Transaction | None:
    """Return one transaction by id, or None if missing or deleted."""
    with finance_db(db_path) as connection:
        row = connection.execute(
            """
            SELECT t.id, t.occurred_at, a.name AS account,
                   c.name AS category, s.name AS subcategory,
                   t.note, t.description, t.direction, t.amount_minor,
                   t.currency, t.fx_rate_scaled, t.base_amount_minor,
                   t.base_currency, t.source
            FROM transactions t
            JOIN accounts a ON a.id = t.account_id
            JOIN categories c ON c.id = t.category_id
            LEFT JOIN subcategories s ON s.id = t.subcategory_id
            WHERE t.id = ? AND t.deleted_at IS NULL
            """,
            (transaction_id,),
        ).fetchone()

    if row is None:
        return None

    return Transaction(**dict(row))
