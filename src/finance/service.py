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
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from src.db.finance_sqlite import FINANCE_DB_PATH, finance_db
from src.finance.codes import code_for_table, next_code
from src.finance.fx import BASE_CURRENCY, normalize_currency, resolve_fx_rate_scaled
from src.finance.money import convert_to_base_minor, to_minor_units
from src.finance.summary import month_bounds, summarize

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
    code: str | None
    name: str
    is_active: bool


@dataclass(frozen=True)
class Category:
    id: int
    code: str | None
    name: str
    emoji: str | None
    is_active: bool


@dataclass(frozen=True)
class Subcategory:
    id: int
    code: str | None
    category_id: int
    category_name: str
    name: str
    is_active: bool


@dataclass(frozen=True)
class Transaction:
    """
    A stored transaction with its lookups already resolved to names.

    `code` is the identity to show the user and accept back from the
    agent. `id` stays internal; see `src/finance/codes.py`.
    """

    id: int
    code: str | None
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


@dataclass(frozen=True)
class TransactionPage:
    """One page of transactions plus the total matching the filter."""

    transactions: tuple[Transaction, ...]
    total: int
    limit: int
    offset: int

    @property
    def has_more(self) -> bool:
        return self.offset + len(self.transactions) < self.total


@dataclass(frozen=True)
class Budget:
    id: int
    code: str | None
    month: str
    category: str
    category_emoji: str | None
    limit_minor: int


@dataclass(frozen=True)
class Goal:
    id: int
    code: str | None
    month: str
    target_income_minor: int | None
    target_savings_minor: int | None
    notes: str | None


@dataclass(frozen=True)
class BudgetProgress:
    """A budget alongside what has actually been spent against it."""

    budget: Budget
    spent_minor: int

    @property
    def remaining_minor(self) -> int:
        """Negative once the budget is exceeded."""
        return self.budget.limit_minor - self.spent_minor

    @property
    def percent_used(self) -> Decimal:
        return (
            Decimal(self.spent_minor) * 100 / Decimal(self.budget.limit_minor)
        ).quantize(Decimal("0.1"))

    @property
    def is_over(self) -> bool:
        return self.spent_minor > self.budget.limit_minor


class _Unset:
    """Sentinel distinguishing "leave alone" from "set to None"."""

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "UNSET"


UNSET = _Unset()


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
    db_path: str | Path | None = None,
) -> list[Account]:
    """Return accounts, active-only unless asked otherwise."""
    clause = "" if include_inactive else "WHERE is_active = 1"

    with finance_db(db_path) as connection:
        rows = connection.execute(
            f"SELECT id, code, name, is_active FROM accounts {clause} ORDER BY name"
        ).fetchall()

    return [
        Account(
            id=row["id"],
            code=row["code"],
            name=row["name"],
            is_active=bool(row["is_active"]),
        )
        for row in rows
    ]


def list_categories(
    include_inactive: bool = False,
    db_path: str | Path | None = None,
) -> list[Category]:
    """Return categories, active-only unless asked otherwise."""
    clause = "" if include_inactive else "WHERE is_active = 1"

    with finance_db(db_path) as connection:
        rows = connection.execute(
            f"SELECT id, code, name, emoji, is_active "
            f"FROM categories {clause} ORDER BY name"
        ).fetchall()

    return [
        Category(
            id=row["id"],
            code=row["code"],
            name=row["name"],
            emoji=row["emoji"],
            is_active=bool(row["is_active"]),
        )
        for row in rows
    ]


def list_subcategories(
    category: str | None = None,
    include_inactive: bool = False,
    db_path: str | Path | None = None,
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
            SELECT s.id, s.code, s.category_id, c.name AS category_name,
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
            code=row["code"],
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


def _lookup_any_category_id(connection: sqlite3.Connection, name: str) -> int:
    """
    Find a category by name whether or not it is active.

    Editing an inactive category must stay possible: correcting its name
    before bringing it back is a reasonable thing to want, and
    `categories.name` is globally unique regardless of `is_active`, so
    the match is still unambiguous.
    """
    row = connection.execute(
        "SELECT id FROM categories WHERE name = ?",
        (name.strip(),),
    ).fetchone()

    if row is None:
        available = connection.execute(
            "SELECT name FROM categories ORDER BY name"
        ).fetchall()
        raise UnknownCategoryError(
            f"Unknown category {name!r}. "
            f"Available: {', '.join(item['name'] for item in available) or 'none'}."
        )

    return int(row["id"])


def _lookup_any_subcategory_id(
    connection: sqlite3.Connection,
    category_id: int,
    category_name: str,
    name: str,
) -> int:
    """Find a subcategory under a category, active or not."""
    row = connection.execute(
        "SELECT id FROM subcategories WHERE category_id = ? AND name = ?",
        (category_id, name.strip()),
    ).fetchone()

    if row is None:
        available = connection.execute(
            "SELECT name FROM subcategories WHERE category_id = ? ORDER BY name",
            (category_id,),
        ).fetchall()
        raise UnknownSubcategoryError(
            f"Unknown subcategory {name!r} under {category_name!r}. "
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
    db_path: str | Path | None = None,
) -> Category:
    """Create a category. Names are unique case-insensitively."""
    clean_name = name.strip()
    if not clean_name:
        raise FinanceError("Category name cannot be empty.")

    with finance_db(db_path) as connection:
        try:
            connection.execute(
                "INSERT INTO categories (code, name, emoji) VALUES (?, ?, ?)",
                (
                    next_code(connection, "categories"),
                    clean_name,
                    (emoji or "").strip() or None,
                ),
            )
        except sqlite3.IntegrityError as error:
            raise FinanceError(
                f"A category named {clean_name!r} already exists."
            ) from error

    for category in list_categories(include_inactive=True, db_path=db_path):
        if category.name.casefold() == clean_name.casefold():
            return category

    raise FinanceError(f"Failed to create category {clean_name!r}.")


def add_account(
    name: str,
    db_path: str | Path | None = None,
) -> Account:
    """Create an account. Names are unique case-insensitively."""
    clean_name = name.strip()
    if not clean_name:
        raise FinanceError("Account name cannot be empty.")

    with finance_db(db_path) as connection:
        try:
            connection.execute(
                "INSERT INTO accounts (code, name) VALUES (?, ?)",
                (next_code(connection, "accounts"), clean_name),
            )
        except sqlite3.IntegrityError as error:
            raise FinanceError(
                f"An account named {clean_name!r} already exists."
            ) from error

    for account in list_accounts(include_inactive=True, db_path=db_path):
        if account.name.casefold() == clean_name.casefold():
            return account

    raise FinanceError(f"Failed to create account {clean_name!r}.")


def add_subcategory(
    category: str,
    name: str,
    db_path: str | Path | None = None,
) -> Subcategory:
    """Create a subcategory under an existing active category."""
    clean_name = name.strip()
    if not clean_name:
        raise FinanceError("Subcategory name cannot be empty.")

    with finance_db(db_path) as connection:
        category_id = _lookup_category_id(connection, category)

        try:
            connection.execute(
                "INSERT INTO subcategories (code, category_id, name) "
                "VALUES (?, ?, ?)",
                (next_code(connection, "subcategories"), category_id, clean_name),
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
    db_path: str | Path | None = None,
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
                code, occurred_at, account_id, category_id, subcategory_id,
                note, description, direction, amount_minor, currency,
                fx_rate_scaled, base_amount_minor, base_currency, source
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                next_code(connection, "transactions"),
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


_TRANSACTION_SELECT = """
    SELECT t.id, t.code, t.occurred_at, a.name AS account,
           c.name AS category, s.name AS subcategory,
           t.note, t.description, t.direction, t.amount_minor,
           t.currency, t.fx_rate_scaled, t.base_amount_minor,
           t.base_currency, t.source
    FROM transactions t
    JOIN accounts a ON a.id = t.account_id
    JOIN categories c ON c.id = t.category_id
    LEFT JOIN subcategories s ON s.id = t.subcategory_id
"""


def update_category(
    name: str,
    new_name: str | None = None,
    emoji: str | None | _Unset = UNSET,
    db_path: str | Path | None = None,
) -> Category:
    """
    Rename a category and/or change its emoji.

    Renaming is safe and is the right way to fix a typo: transactions
    reference a category by id, so every past transaction follows the new
    name automatically. Deactivating the old one and creating a
    replacement would instead split the history permanently, which is
    what the Money Manager import had to clean up by hand.

    A rename that only changes capitalisation is allowed, because
    `categories.name` is `COLLATE NOCASE UNIQUE` and the row is matching
    itself. Renaming onto a *different* existing category is rejected;
    merging two categories is a separate operation that would have to
    decide what happens to both sets of transactions.

    Passing None for `emoji` clears it; omitting it leaves it alone. The
    design code never changes: it identifies the record, not its label.
    """
    with finance_db(db_path) as connection:
        category_id = _lookup_any_category_id(connection, name)

        if new_name is not None:
            clean_name = new_name.strip()
            if not clean_name:
                raise FinanceError("Category name cannot be empty.")

            clash = connection.execute(
                "SELECT name FROM categories WHERE name = ? AND id != ?",
                (clean_name, category_id),
            ).fetchone()

            if clash is not None:
                raise FinanceError(
                    f"A different category is already named {clash['name']!r}. "
                    "Rename that one first, or move the transactions across "
                    "individually; categories cannot be merged automatically."
                )

            connection.execute(
                "UPDATE categories SET name = ?, "
                "updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') WHERE id = ?",
                (clean_name, category_id),
            )

        if not isinstance(emoji, _Unset):
            connection.execute(
                "UPDATE categories SET emoji = ?, "
                "updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') WHERE id = ?",
                ((emoji or "").strip() or None, category_id),
            )

    for category in list_categories(include_inactive=True, db_path=db_path):
        if category.id == category_id:
            return category

    raise FinanceError(f"Failed to update category {name!r}.")


def update_subcategory(
    category: str,
    name: str,
    new_name: str,
    db_path: str | Path | None = None,
) -> Subcategory:
    """
    Rename a subcategory within its existing category.

    There is deliberately no way to move a subcategory to a different
    parent. Transactions store `category_id` and `subcategory_id`
    independently, so reparenting would leave historical rows pointing at
    a subcategory that no longer belongs to their category, and every
    past report would start contradicting itself.
    """
    clean_name = new_name.strip()
    if not clean_name:
        raise FinanceError("Subcategory name cannot be empty.")

    with finance_db(db_path) as connection:
        category_id = _lookup_any_category_id(connection, category)
        subcategory_id = _lookup_any_subcategory_id(
            connection, category_id, category, name
        )

        clash = connection.execute(
            """
            SELECT name FROM subcategories
            WHERE category_id = ? AND name = ? AND id != ?
            """,
            (category_id, clean_name, subcategory_id),
        ).fetchone()

        if clash is not None:
            raise FinanceError(
                f"{category} already has a different subcategory named "
                f"{clash['name']!r}."
            )

        connection.execute(
            "UPDATE subcategories SET name = ?, "
            "updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') WHERE id = ?",
            (clean_name, subcategory_id),
        )

    for subcategory in list_subcategories(
        include_inactive=True, db_path=db_path
    ):
        if subcategory.id == subcategory_id:
            return subcategory

    raise FinanceError(f"Failed to rename subcategory {name!r}.")


def deactivate_category(
    name: str,
    db_path: str | Path | None = None,
) -> Category:
    """
    Soft-delete a category by clearing `is_active`.

    Never a hard delete: historical transactions still reference it, and
    removing the row would break their category name in every past
    report.
    """
    with finance_db(db_path) as connection:
        category_id = _lookup_category_id(connection, name)
        connection.execute(
            "UPDATE categories SET is_active = 0, "
            "updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') WHERE id = ?",
            (category_id,),
        )
        # Subcategories of an inactive category cannot be selected either.
        connection.execute(
            "UPDATE subcategories SET is_active = 0, "
            "updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') "
            "WHERE category_id = ?",
            (category_id,),
        )

    for category in list_categories(include_inactive=True, db_path=db_path):
        if category.id == category_id:
            return category

    raise FinanceError(f"Failed to deactivate category {name!r}.")


def deactivate_subcategory(
    category: str,
    name: str,
    db_path: str | Path | None = None,
) -> Subcategory:
    """Soft-delete a subcategory, for the same reason as categories."""
    with finance_db(db_path) as connection:
        category_id = _lookup_category_id(connection, category)
        subcategory_id = _lookup_subcategory_id(
            connection, category_id, category, name
        )
        connection.execute(
            "UPDATE subcategories SET is_active = 0, "
            "updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') WHERE id = ?",
            (subcategory_id,),
        )

    for subcategory in list_subcategories(
        category=category, include_inactive=True, db_path=db_path
    ):
        if subcategory.id == subcategory_id:
            return subcategory

    raise FinanceError(f"Failed to deactivate subcategory {name!r}.")


# --- Budgets and goals ----------------------------------------------


def normalize_month(month: date | str) -> str:
    """
    Coerce a month to the stored `YYYY-MM-01` form.

    Accepts a date, `2026-08`, or `2026-08-17`; all mean August 2026. The
    schema's CHECK requires the first of the month, so normalizing here
    keeps that constraint from surfacing as a confusing IntegrityError.
    """
    if isinstance(month, date):
        return month.replace(day=1).isoformat()

    text = month.strip()

    if len(text) == 7:
        text = f"{text}-01"

    try:
        parsed = date.fromisoformat(text)
    except ValueError as error:
        raise FinanceError(
            f"{month!r} is not a month. Use YYYY-MM, such as 2026-08."
        ) from error

    return parsed.replace(day=1).isoformat()


def set_budget(
    month: date | str,
    category: str,
    limit: Decimal | int | str,
    db_path: str | Path | None = None,
) -> Budget:
    """
    Create or replace one category's budget for a month.

    `limit` is a MYR major-unit amount and must be positive. Replacing an
    existing budget keeps its design code, because it is the same budget
    with a new number rather than a different budget.
    """
    stored_month = normalize_month(month)
    limit_minor = to_minor_units(limit)

    if limit_minor <= 0:
        raise FinanceError(f"Budget limit must be greater than zero, got {limit}.")

    with finance_db(db_path) as connection:
        category_id = _lookup_category_id(connection, category)

        existing = connection.execute(
            "SELECT id FROM budgets WHERE month = ? AND category_id = ?",
            (stored_month, category_id),
        ).fetchone()

        if existing is None:
            connection.execute(
                """
                INSERT INTO budgets (code, month, category_id, limit_minor)
                VALUES (?, ?, ?, ?)
                """,
                (
                    next_code(connection, "budgets"),
                    stored_month,
                    category_id,
                    limit_minor,
                ),
            )
        else:
            connection.execute(
                """
                UPDATE budgets SET limit_minor = ?,
                    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                WHERE id = ?
                """,
                (limit_minor, existing["id"]),
            )

    for budget in list_budgets(stored_month, db_path=db_path):
        if budget.category.casefold() == category.strip().casefold():
            return budget

    raise FinanceError(f"Failed to store the budget for {category!r}.")


def list_budgets(
    month: date | str,
    db_path: str | Path | None = None,
) -> list[Budget]:
    """Return every budget for a month, ordered by category name."""
    stored_month = normalize_month(month)

    with finance_db(db_path) as connection:
        rows = connection.execute(
            """
            SELECT b.id, b.code, b.month, c.name AS category,
                   c.emoji AS category_emoji, b.limit_minor
            FROM budgets b
            JOIN categories c ON c.id = b.category_id
            WHERE b.month = ?
            ORDER BY c.name
            """,
            (stored_month,),
        ).fetchall()

    return [Budget(**dict(row)) for row in rows]


def delete_budget(
    code: str,
    db_path: str | Path | None = None,
) -> Budget:
    """
    Remove a budget outright.

    A budget carries no history worth preserving, so unlike a category
    this is a real delete. Its design code is still never reused: the
    counter in `code_sequences` only moves forward.
    """
    normalized = code_for_table(code, "budgets")

    with finance_db(db_path) as connection:
        row = connection.execute(
            """
            SELECT b.id, b.code, b.month, c.name AS category,
                   c.emoji AS category_emoji, b.limit_minor
            FROM budgets b
            JOIN categories c ON c.id = b.category_id
            WHERE b.code = ?
            """,
            (normalized,),
        ).fetchone()

        if row is None:
            raise FinanceError(f"No budget with code {normalized}.")

        budget = Budget(**dict(row))
        connection.execute("DELETE FROM budgets WHERE id = ?", (budget.id,))

    return budget


def budget_progress(
    month: date | str,
    db_path: str | Path | None = None,
) -> list[BudgetProgress]:
    """
    Pair each budget with the actual spend in its category that month.

    Spend comes from the same aggregation the dashboard and reports use,
    so a budget widget can never disagree with the category breakdown
    sitting next to it.
    """
    stored_month = normalize_month(month)
    period = date.fromisoformat(stored_month)
    start, end = month_bounds(period.year, period.month)

    summary = summarize(start, end, fill_empty_days=False, db_path=db_path)
    spent_by_category = {
        entry.category.casefold(): entry.expense_minor
        for entry in summary.by_category
    }

    return [
        BudgetProgress(
            budget=budget,
            spent_minor=spent_by_category.get(budget.category.casefold(), 0),
        )
        for budget in list_budgets(stored_month, db_path=db_path)
    ]


def set_goal(
    month: date | str,
    target_income: Decimal | int | str | None = None,
    target_savings: Decimal | int | str | None = None,
    notes: str | None | _Unset = UNSET,
    db_path: str | Path | None = None,
) -> Goal:
    """
    Create or replace the income and savings targets for a month.

    Either target may be None, meaning "no target set". Zero is a
    different statement from None and is stored as given.
    """
    stored_month = normalize_month(month)

    income_minor = (
        to_minor_units(target_income) if target_income is not None else None
    )
    savings_minor = (
        to_minor_units(target_savings) if target_savings is not None else None
    )

    for label, value in (
        ("target_income", income_minor),
        ("target_savings", savings_minor),
    ):
        if value is not None and value < 0:
            raise FinanceError(f"{label} cannot be negative.")

    existing = get_goal(stored_month, db_path=db_path)

    with finance_db(db_path) as connection:
        if existing is None:
            connection.execute(
                """
                INSERT INTO goals (
                    code, month, target_income_minor,
                    target_savings_minor, notes
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    next_code(connection, "goals"),
                    stored_month,
                    income_minor,
                    savings_minor,
                    _resolve_optional_text(notes, None),
                ),
            )
        else:
            connection.execute(
                """
                UPDATE goals SET
                    target_income_minor = ?,
                    target_savings_minor = ?,
                    notes = ?,
                    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                WHERE month = ?
                """,
                (
                    income_minor
                    if target_income is not None
                    else existing.target_income_minor,
                    savings_minor
                    if target_savings is not None
                    else existing.target_savings_minor,
                    _resolve_optional_text(notes, existing.notes),
                    stored_month,
                ),
            )

    stored = get_goal(stored_month, db_path=db_path)
    if stored is None:
        raise FinanceError(f"Failed to store the goal for {stored_month}.")

    return stored


def get_goal(
    month: date | str,
    db_path: str | Path | None = None,
) -> Goal | None:
    """Return the goal for a month, or None if none is set."""
    stored_month = normalize_month(month)

    with finance_db(db_path) as connection:
        row = connection.execute(
            """
            SELECT id, code, month, target_income_minor,
                   target_savings_minor, notes
            FROM goals WHERE month = ?
            """,
            (stored_month,),
        ).fetchone()

    if row is None:
        return None

    return Goal(**dict(row))


# --- Transactions ---------------------------------------------------


def get_transaction(
    transaction_id: int,
    db_path: str | Path | None = None,
) -> Transaction | None:
    """
    Return one transaction by internal id, or None if missing or deleted.

    Prefer `get_transaction_by_code` anywhere a user or the agent picked
    the record; the id is an implementation detail.
    """
    with finance_db(db_path) as connection:
        row = connection.execute(
            f"{_TRANSACTION_SELECT} WHERE t.id = ? AND t.deleted_at IS NULL",
            (transaction_id,),
        ).fetchone()

    if row is None:
        return None

    return Transaction(**dict(row))


def update_transaction(
    code: str,
    amount: Decimal | int | str | None = None,
    currency: str | None = None,
    category: str | None = None,
    subcategory: str | None | _Unset = UNSET,
    account: str | None = None,
    direction: str | None = None,
    occurred_at: datetime | None = None,
    note: str | None | _Unset = UNSET,
    description: str | None | _Unset = UNSET,
    db_path: str | Path | None = None,
) -> Transaction:
    """
    Edit a stored transaction, identified by its design code.

    Omitted arguments are left alone. Passing None explicitly to
    `subcategory`, `note`, or `description` clears that field.

    Exchange-rate handling is the subtle part:

    - Editing the amount alone reuses the rate already locked on the row,
      so correcting a typo cannot silently re-rate the transaction at
      today's rate.
    - Changing the currency, or moving the transaction to a different
      date, resolves a fresh rate, because the stored one no longer
      describes the transaction being recorded.

    Neither path is a "re-rate history" feature: a rate *policy* change
    still never touches an existing row.
    """
    normalized = code_for_table(code, "transactions")

    existing = get_transaction_by_code(normalized, db_path)
    if existing is None:
        raise FinanceError(f"No live transaction with code {normalized}.")

    if direction is not None and direction not in VALID_DIRECTIONS:
        raise FinanceError(
            f"direction must be one of {VALID_DIRECTIONS}, got {direction!r}."
        )

    new_currency = (
        normalize_currency(currency) if currency is not None else existing.currency
    )

    if occurred_at is not None:
        moment = occurred_at
        if moment.tzinfo is not None:
            moment = moment.astimezone(
                _resolve_timezone(DEFAULT_TIMEZONE)
            ).replace(tzinfo=None)
        moment = moment.replace(microsecond=0)
    else:
        moment = datetime.fromisoformat(existing.occurred_at)

    amount_minor = (
        to_minor_units(amount) if amount is not None else existing.amount_minor
    )

    if amount_minor <= 0:
        raise FinanceError(
            f"Amount must be greater than zero, got {amount}. "
            "Use direction='expense' or 'income' to indicate the sign."
        )

    date_changed = moment.date() != datetime.fromisoformat(
        existing.occurred_at
    ).date()

    if new_currency != existing.currency or date_changed:
        fx_rate_scaled = resolve_fx_rate_scaled(new_currency, moment.date(), db_path)
    else:
        fx_rate_scaled = existing.fx_rate_scaled

    base_amount_minor = convert_to_base_minor(amount_minor, fx_rate_scaled)

    if base_amount_minor <= 0:
        raise FinanceError(
            f"{amount} {new_currency} converts to {base_amount_minor} sen, "
            "which the schema rejects."
        )

    with finance_db(db_path) as connection:
        account_id = _lookup_account_id(
            connection, account if account is not None else existing.account
        )

        category_name = category if category is not None else existing.category
        category_id = _lookup_category_id(connection, category_name)

        # A category change invalidates the old subcategory, since
        # subcategories belong to exactly one category.
        if isinstance(subcategory, _Unset):
            wanted_subcategory = (
                existing.subcategory if category is None else None
            )
        else:
            wanted_subcategory = subcategory

        subcategory_id: int | None = None
        if wanted_subcategory:
            subcategory_id = _lookup_subcategory_id(
                connection, category_id, category_name, wanted_subcategory
            )

        connection.execute(
            """
            UPDATE transactions SET
                occurred_at = ?, account_id = ?, category_id = ?,
                subcategory_id = ?, note = ?, description = ?,
                direction = ?, amount_minor = ?, currency = ?,
                fx_rate_scaled = ?, base_amount_minor = ?,
                updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
            WHERE code = ? AND deleted_at IS NULL
            """,
            (
                moment.isoformat(),
                account_id,
                category_id,
                subcategory_id,
                _resolve_optional_text(note, existing.note),
                _resolve_optional_text(description, existing.description),
                direction if direction is not None else existing.direction,
                amount_minor,
                new_currency,
                fx_rate_scaled,
                base_amount_minor,
                normalized,
            ),
        )

    updated = get_transaction_by_code(normalized, db_path)
    if updated is None:
        raise FinanceError(f"{normalized} vanished during the update.")

    return updated


def _resolve_optional_text(
    supplied: str | None | _Unset,
    current: str | None,
) -> str | None:
    """UNSET keeps the stored value; None or blank clears it."""
    if isinstance(supplied, _Unset):
        return current

    if supplied is None:
        return None

    return supplied.strip() or None


def delete_transaction(
    code: str,
    db_path: str | Path | None = None,
) -> Transaction:
    """
    Soft-delete a transaction, identified by its design code.

    The row stays, so its code is never recycled and historical reports
    that named it remain interpretable. Returns the transaction as it was
    immediately before deletion, so a caller can confirm what went.
    """
    normalized = code_for_table(code, "transactions")

    existing = get_transaction_by_code(normalized, db_path)
    if existing is None:
        raise FinanceError(f"No live transaction with code {normalized}.")

    with finance_db(db_path) as connection:
        connection.execute(
            """
            UPDATE transactions
            SET deleted_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now'),
                updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
            WHERE code = ? AND deleted_at IS NULL
            """,
            (normalized,),
        )

    return existing


def list_transactions(
    period_start: date | None = None,
    period_end: date | None = None,
    category: str | None = None,
    account: str | None = None,
    search: str | None = None,
    limit: int = 100,
    offset: int = 0,
    db_path: str | Path | None = None,
) -> TransactionPage:
    """
    List live transactions newest-first, filtered and paginated.

    Date bounds are inclusive and translated to the same half-open
    timestamp range the summary uses, so a listing and a summary over
    the same period always cover exactly the same rows.
    """
    conditions = ["t.deleted_at IS NULL"]
    parameters: list[object] = []

    if period_start is not None:
        conditions.append("t.occurred_at >= ?")
        parameters.append(f"{period_start.isoformat()}T00:00:00")

    if period_end is not None:
        conditions.append("t.occurred_at < ?")
        parameters.append(
            f"{(period_end + timedelta(days=1)).isoformat()}T00:00:00"
        )

    if category:
        conditions.append("c.name = ?")
        parameters.append(category.strip())

    if account:
        conditions.append("a.name = ?")
        parameters.append(account.strip())

    if search:
        conditions.append(
            "(t.note LIKE ? OR t.description LIKE ? OR t.code LIKE ?)"
        )
        pattern = f"%{search.strip()}%"
        parameters.extend([pattern, pattern, pattern.upper()])

    where = " AND ".join(conditions)

    if limit < 1:
        raise FinanceError("limit must be at least 1.")

    if offset < 0:
        raise FinanceError("offset cannot be negative.")

    with finance_db(db_path) as connection:
        total = int(
            connection.execute(
                f"""
                SELECT count(*) AS total
                FROM transactions t
                JOIN accounts a ON a.id = t.account_id
                JOIN categories c ON c.id = t.category_id
                WHERE {where}
                """,
                parameters,
            ).fetchone()["total"]
        )

        rows = connection.execute(
            f"""
            {_TRANSACTION_SELECT}
            WHERE {where}
            ORDER BY t.occurred_at DESC, t.id DESC
            LIMIT ? OFFSET ?
            """,
            [*parameters, limit, offset],
        ).fetchall()

    return TransactionPage(
        transactions=tuple(Transaction(**dict(row)) for row in rows),
        total=total,
        limit=limit,
        offset=offset,
    )


def get_transaction_by_code(
    code: str,
    db_path: str | Path | None = None,
) -> Transaction | None:
    """
    Return one transaction by design code, or None if missing or deleted.

    Raises `InvalidCodeError` if the string is not a transaction code, so
    a mistyped or wrong-table code fails loudly instead of quietly
    matching nothing.
    """
    normalized = code_for_table(code, "transactions")

    with finance_db(db_path) as connection:
        row = connection.execute(
            f"{_TRANSACTION_SELECT} WHERE t.code = ? AND t.deleted_at IS NULL",
            (normalized,),
        ).fetchone()

    if row is None:
        return None

    return Transaction(**dict(row))
