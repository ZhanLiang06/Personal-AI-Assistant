import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parents[2]
FINANCE_DB_PATH = BASE_DIR / "data" / "finance.sqlite3"

# Redirects the default database. Resolved at call time rather than
# baked into function defaults, so a test or a script can point the whole
# service layer at a throwaway file without threading a path through
# every call. Only ever set via `use_finance_db`.
_ACTIVE_DB_PATH: Path | None = None


def active_db_path() -> Path:
    """The database used when a caller does not name one."""
    return _ACTIVE_DB_PATH if _ACTIVE_DB_PATH is not None else FINANCE_DB_PATH


@contextmanager
def use_finance_db(db_path: str | Path) -> Iterator[Path]:
    """
    Temporarily make `db_path` the default finance database.

    Intended for tests and one-off scripts. Restores the previous value
    on exit, including when the body raises.
    """
    global _ACTIVE_DB_PATH

    previous = _ACTIVE_DB_PATH
    _ACTIVE_DB_PATH = Path(db_path)

    try:
        yield _ACTIVE_DB_PATH
    finally:
        _ACTIVE_DB_PATH = previous

SCHEMA_VERSION = 2
FX_RATE_SCALE = 100_000_000


def utc_now() -> str:
    """Return the current UTC time as an ISO 8601 string."""
    return datetime.now(UTC).isoformat()


def connect_finance_db(
    db_path: str | Path | None = None,
) -> sqlite3.Connection:
    """Open the finance database and enable SQLite safety settings."""
    resolved_path = Path(db_path) if db_path is not None else active_db_path()
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
    db_path: str | Path | None = None,
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
    db_path: str | Path | None = None,
) -> None:
    """Create the initial finance database schema."""
    with finance_db(db_path) as connection:
        connection.executescript(
            """
            -- Monotonic counters behind the design codes. Deliberately
            -- separate from the data: a hard DELETE lowers MAX(code), so
            -- allocating from the data alone would reuse a freed code and
            -- silently repoint any report that named it.
            CREATE TABLE IF NOT EXISTS code_sequences (
                table_name TEXT PRIMARY KEY,
                next_value INTEGER NOT NULL
                    CHECK (next_value > 0)
            );

            CREATE TABLE IF NOT EXISTS accounts (
                id INTEGER PRIMARY KEY,

                -- Human-readable design code, e.g. ACC-001. Assigned by
                -- the service layer; see src/finance/codes.py.
                code TEXT,

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

                -- Design code, e.g. CAT-007.
                code TEXT,

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

                -- Design code, e.g. SUB-012.
                code TEXT,

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

                -- Design code, e.g. TXN-000038. This is the identity the
                -- agent and any report use; never expose the raw id.
                code TEXT,

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

                -- Design code, e.g. BGT-004.
                code TEXT,

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

                -- Design code, e.g. GOL-003.
                code TEXT,

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

    # The design-code columns and their unique indexes are created by the
    # migration, not here. On an existing v1 database the CREATE TABLE
    # statements above are no-ops, so the code column would still be
    # missing and a CREATE UNIQUE INDEX over it would fail. Delegating
    # also means this function stamps the schema version only once the
    # database genuinely matches it.
    migrate_finance_db(db_path)


def get_schema_version(
    db_path: str | Path | None = None,
) -> int:
    """Return the current finance database schema version."""
    with finance_db(db_path) as connection:
        row = connection.execute(
            "PRAGMA user_version"
        ).fetchone()

    return int(row[0])


def _table_has_column(
    connection: sqlite3.Connection,
    table: str,
    column: str,
) -> bool:
    rows = connection.execute(f"PRAGMA table_info({table})").fetchall()

    return any(row["name"] == column for row in rows)


def migrate_finance_db(
    db_path: str | Path | None = None,
) -> int:
    """
    Bring an existing finance database up to `SCHEMA_VERSION`.

    Idempotent: running it on an already-current database does nothing.
    Returns the resulting schema version.

    v1 -> v2 adds the design-code column to every table and backfills it.
    Transactions are numbered in `occurred_at` order, so TXN-000001 is
    the oldest recorded transaction. That matters because the Money
    Manager import inserted newest-first, making row id order the exact
    reverse of chronological order. Once assigned, codes are frozen.
    """
    from src.finance.codes import CODE_SPECS, backfill_missing_codes

    version = get_schema_version(db_path)

    if version >= SCHEMA_VERSION:
        return version

    with finance_db(db_path) as connection:
        if version < 2:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS code_sequences (
                    table_name TEXT PRIMARY KEY,
                    next_value INTEGER NOT NULL
                        CHECK (next_value > 0)
                )
                """
            )

            for spec in CODE_SPECS.values():
                if not _table_has_column(connection, spec.table, "code"):
                    connection.execute(
                        f"ALTER TABLE {spec.table} ADD COLUMN code TEXT"
                    )

            backfill_missing_codes(connection)

            connection.executescript(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS
                    idx_accounts_code ON accounts (code);
                CREATE UNIQUE INDEX IF NOT EXISTS
                    idx_categories_code ON categories (code);
                CREATE UNIQUE INDEX IF NOT EXISTS
                    idx_subcategories_code ON subcategories (code);
                CREATE UNIQUE INDEX IF NOT EXISTS
                    idx_transactions_code ON transactions (code);
                CREATE UNIQUE INDEX IF NOT EXISTS
                    idx_budgets_code ON budgets (code);
                CREATE UNIQUE INDEX IF NOT EXISTS
                    idx_goals_code ON goals (code);
                """
            )

        connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    return get_schema_version(db_path)


if __name__ == "__main__":
    init_finance_db()

    print(
        f"Finance database initialized at {FINANCE_DB_PATH}"
    )
    print(f"Schema version: {get_schema_version()}")