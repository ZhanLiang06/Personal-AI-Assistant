import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parents[2]
FINANCE_DB_PATH = BASE_DIR / "data" / "finance.sqlite3"

SCHEMA_VERSION = 1
FX_RATE_SCALE = 100_000_000


def utc_now() -> str:
    """Return the current UTC time as an ISO 8601 string."""
    return datetime.now(UTC).isoformat()


def connect_finance_db(
    db_path: str | Path = FINANCE_DB_PATH,
) -> sqlite3.Connection:
    """Open the finance database and enable SQLite safety settings."""
    resolved_path = Path(db_path)
    resolved_path.parent.mkdir(parents=True, exist_ok=True)

    connection = sqlite3.connect(
        resolved_path,
        timeout=5.0,
    )
    connection.row_factory = sqlite3.Row

    # SQLite does not enforce foreign keys unless this is enabled
    # separately for every connection.
    connection.execute("PRAGMA foreign_keys = ON")

    # Wait briefly when another connection is writing instead of
    # immediately raising "database is locked".
    connection.execute("PRAGMA busy_timeout = 5000")

    return connection


@contextmanager
def finance_db(
    db_path: str | Path = FINANCE_DB_PATH,
) -> Iterator[sqlite3.Connection]:
    """
    Provide a transaction-scoped finance database connection.

    Successful operations are committed. Failed operations are rolled
    back. The connection is always closed.
    """
    connection = connect_finance_db(db_path)

    try:
        with connection:
            yield connection
    finally:
        connection.close()


def init_finance_db(
    db_path: str | Path = FINANCE_DB_PATH,
) -> None:
    """Create the initial finance database schema."""
    with finance_db(db_path) as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS accounts (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL COLLATE NOCASE
                    UNIQUE
                    CHECK (length(trim(name)) > 0),
                is_active INTEGER NOT NULL DEFAULT 1
                    CHECK (is_active IN (0, 1)),
                created_at TEXT NOT NULL DEFAULT (
                    strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                ),
                updated_at TEXT NOT NULL DEFAULT (
                    strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                )
            );

            CREATE TABLE IF NOT EXISTS categories (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL COLLATE NOCASE
                    UNIQUE
                    CHECK (length(trim(name)) > 0),
                emoji TEXT,
                is_active INTEGER NOT NULL DEFAULT 1
                    CHECK (is_active IN (0, 1)),
                created_at TEXT NOT NULL DEFAULT (
                    strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                ),
                updated_at TEXT NOT NULL DEFAULT (
                    strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                )
            );

            CREATE TABLE IF NOT EXISTS subcategories (
                id INTEGER PRIMARY KEY,
                category_id INTEGER NOT NULL,
                name TEXT NOT NULL COLLATE NOCASE
                    CHECK (length(trim(name)) > 0),
                is_active INTEGER NOT NULL DEFAULT 1
                    CHECK (is_active IN (0, 1)),
                created_at TEXT NOT NULL DEFAULT (
                    strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                ),
                updated_at TEXT NOT NULL DEFAULT (
                    strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                ),

                FOREIGN KEY (category_id)
                    REFERENCES categories(id),

                UNIQUE (category_id, name)
            );

            CREATE TABLE IF NOT EXISTS exchange_rate_settings (
                currency TEXT PRIMARY KEY
                    CHECK (
                        length(currency) = 3
                        AND currency = upper(currency)
                    ),
                mode TEXT NOT NULL DEFAULT 'auto'
                    CHECK (mode IN ('auto', 'manual')),

                -- Rate to MYR multiplied by FX_RATE_SCALE.
                -- Example: 0.58487395 becomes 58487395.
                manual_rate_scaled INTEGER,

                updated_at TEXT NOT NULL DEFAULT (
                    strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                ),

                CHECK (
                    mode = 'auto'
                    OR (
                        mode = 'manual'
                        AND manual_rate_scaled IS NOT NULL
                        AND manual_rate_scaled > 0
                    )
                )
            );

            CREATE TABLE IF NOT EXISTS transactions (
                id INTEGER PRIMARY KEY,

                -- ISO 8601 timestamp, normalized by the service layer.
                occurred_at TEXT NOT NULL,

                account_id INTEGER NOT NULL,
                category_id INTEGER NOT NULL,
                subcategory_id INTEGER,

                note TEXT,
                description TEXT,

                direction TEXT NOT NULL
                    CHECK (direction IN ('expense', 'income')),

                -- Original amount in the currency's minor unit.
                -- Example: CNY 11.90 becomes 1190.
                amount_minor INTEGER NOT NULL
                    CHECK (amount_minor > 0),

                currency TEXT NOT NULL
                    CHECK (
                        length(currency) = 3
                        AND currency = upper(currency)
                    ),

                -- Locked FX rate multiplied by FX_RATE_SCALE.
                fx_rate_scaled INTEGER NOT NULL
                    CHECK (fx_rate_scaled > 0),

                -- Converted MYR amount in sen.
                -- Example: MYR 6.96 becomes 696.
                base_amount_minor INTEGER NOT NULL
                    CHECK (base_amount_minor > 0),

                base_currency TEXT NOT NULL DEFAULT 'MYR'
                    CHECK (base_currency = 'MYR'),

                source TEXT NOT NULL DEFAULT 'manual'
                    CHECK (
                        source IN ('manual', 'agent', 'import')
                    ),

                created_at TEXT NOT NULL DEFAULT (
                    strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                ),
                updated_at TEXT NOT NULL DEFAULT (
                    strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                ),

                -- Non-null means the transaction was soft-deleted.
                deleted_at TEXT,

                FOREIGN KEY (account_id)
                    REFERENCES accounts(id),

                FOREIGN KEY (category_id)
                    REFERENCES categories(id),

                FOREIGN KEY (subcategory_id)
                    REFERENCES subcategories(id)
            );

            CREATE TABLE IF NOT EXISTS budgets (
                id INTEGER PRIMARY KEY,

                -- Stored as YYYY-MM-01.
                month TEXT NOT NULL,

                category_id INTEGER NOT NULL,

                -- Budget limit in MYR sen.
                limit_minor INTEGER NOT NULL
                    CHECK (limit_minor > 0),

                created_at TEXT NOT NULL DEFAULT (
                    strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                ),
                updated_at TEXT NOT NULL DEFAULT (
                    strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                ),

                FOREIGN KEY (category_id)
                    REFERENCES categories(id),

                UNIQUE (month, category_id),

                CHECK (
                    date(month) IS NOT NULL
                    AND month = date(month)
                    AND substr(month, 9, 2) = '01'
                )
            );

            CREATE TABLE IF NOT EXISTS goals (
                id INTEGER PRIMARY KEY,

                -- Stored as YYYY-MM-01.
                month TEXT NOT NULL UNIQUE,

                -- Both values use MYR sen.
                target_income_minor INTEGER
                    CHECK (
                        target_income_minor IS NULL
                        OR target_income_minor >= 0
                    ),

                target_savings_minor INTEGER
                    CHECK (
                        target_savings_minor IS NULL
                        OR target_savings_minor >= 0
                    ),

                notes TEXT,

                created_at TEXT NOT NULL DEFAULT (
                    strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                ),
                updated_at TEXT NOT NULL DEFAULT (
                    strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                ),

                CHECK (
                    date(month) IS NOT NULL
                    AND month = date(month)
                    AND substr(month, 9, 2) = '01'
                )
            );

            CREATE INDEX IF NOT EXISTS
                idx_subcategories_category_active
            ON subcategories (
                category_id,
                is_active
            );

            CREATE INDEX IF NOT EXISTS
                idx_transactions_active_occurred_at
            ON transactions (
                occurred_at DESC
            )
            WHERE deleted_at IS NULL;

            CREATE INDEX IF NOT EXISTS
                idx_transactions_active_category_date
            ON transactions (
                category_id,
                occurred_at DESC
            )
            WHERE deleted_at IS NULL;

            CREATE INDEX IF NOT EXISTS
                idx_transactions_active_account_date
            ON transactions (
                account_id,
                occurred_at DESC
            )
            WHERE deleted_at IS NULL;

            CREATE INDEX IF NOT EXISTS
                idx_transactions_currency
            ON transactions (
                currency
            );

            CREATE INDEX IF NOT EXISTS
                idx_budgets_month
            ON budgets (
                month
            );
            """
        )

        # SQLite provides this integer for applications to track their
        # own schema version.
        connection.execute(
            f"PRAGMA user_version = {SCHEMA_VERSION}"
        )


def get_schema_version(
    db_path: str | Path = FINANCE_DB_PATH,
) -> int:
    """Return the current finance database schema version."""
    with finance_db(db_path) as connection:
        row = connection.execute(
            "PRAGMA user_version"
        ).fetchone()

    return int(row[0])


if __name__ == "__main__":
    init_finance_db()

    print(
        f"Finance database initialized at {FINANCE_DB_PATH}"
    )
    print(f"Schema version: {get_schema_version()}")