"""
Tests for `src.finance.codes` and the v1 -> v2 design-code migration.

The migration is the risky part: it runs once against real financial
history and then the codes are frozen forever. These tests exercise it
against a copy of the real database as well as synthetic ones.
"""

from __future__ import annotations

import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

import pytest

from src.db.finance_sqlite import (
    SCHEMA_VERSION,
    finance_db,
    get_schema_version,
    init_finance_db,
    migrate_finance_db,
)
from src.finance.codes import (
    CODE_SPECS,
    InvalidCodeError,
    backfill_missing_codes,
    code_for_table,
    next_code,
    normalize_code,
    parse_code,
    spec_for,
)
from src.finance.service import (
    add_category,
    add_subcategory,
    get_transaction,
    get_transaction_by_code,
    list_categories,
    list_subcategories,
    record_transaction,
)


ACCOUNT = "Test Account"


@pytest.fixture
def coded_db(empty_db: Path) -> Path:
    """An empty v2 database with one account and one category."""
    with finance_db(empty_db) as connection:
        connection.execute(
            "INSERT INTO accounts (code, name) VALUES ('ACC-001', ?)", (ACCOUNT,)
        )

    add_category("Food", emoji="🍜", db_path=empty_db)

    return empty_db


# --- Formatting and parsing -----------------------------------------


def test_code_widths_match_the_agreed_format():
    assert spec_for("transactions").format(38) == "TXN-000038"
    assert spec_for("categories").format(7) == "CAT-007"
    assert spec_for("accounts").format(1) == "ACC-001"
    assert spec_for("subcategories").format(12) == "SUB-012"
    assert spec_for("budgets").format(4) == "BGT-004"
    assert spec_for("goals").format(3) == "GOL-003"


def test_codes_grow_past_their_padding():
    """Padding is a minimum width, not a ceiling."""
    assert spec_for("transactions").format(1_234_567) == "TXN-1234567"
    assert spec_for("categories").format(1000) == "CAT-1000"


def test_format_rejects_a_non_positive_sequence():
    with pytest.raises(ValueError, match="must be positive"):
        spec_for("transactions").format(0)


def test_spec_for_rejects_an_unknown_table():
    with pytest.raises(InvalidCodeError, match="no design code"):
        spec_for("reminders")


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("TXN-000038", "TXN-000038"),
        ("txn-000038", "TXN-000038"),
        ("  TXN-000038  ", "TXN-000038"),
        ("TXN - 000038", "TXN-000038"),
    ],
)
def test_normalize_accepts_sloppy_input(raw: str, expected: str):
    """A code typed in chat will not always arrive perfectly formed."""
    assert normalize_code(raw) == expected


@pytest.mark.parametrize(
    "raw",
    ["38", "TXN000038", "TX-000038", "TXNN-38", "", "TXN-", "-000038", "TXN-abc"],
)
def test_normalize_rejects_malformed_input(raw: str):
    with pytest.raises(InvalidCodeError):
        normalize_code(raw)


def test_bare_number_is_rejected():
    """
    Accepting `38` would reintroduce exactly the ambiguity the design
    code exists to remove.
    """
    with pytest.raises(InvalidCodeError, match="not a design code"):
        normalize_code("38")


def test_parse_code_returns_table_and_sequence():
    assert parse_code("TXN-000038") == ("transactions", 38)
    assert parse_code("CAT-007") == ("categories", 7)


def test_parse_code_rejects_an_unknown_prefix():
    with pytest.raises(InvalidCodeError, match="unknown prefix"):
        parse_code("ZZZ-001")


def test_code_for_table_catches_a_cross_table_mixup():
    """An agent passing a category code where a transaction was wanted."""
    with pytest.raises(InvalidCodeError, match="identifies a categories record"):
        code_for_table("CAT-003", "transactions")

    assert code_for_table("txn-000038", "transactions") == "TXN-000038"


# --- Allocation -----------------------------------------------------


def test_next_code_starts_at_one(empty_db: Path):
    with finance_db(empty_db) as connection:
        assert next_code(connection, "transactions") == "TXN-000001"
        assert next_code(connection, "categories") == "CAT-001"


def test_next_code_orders_numerically_not_lexically(empty_db: Path):
    """
    The classic string-sort bug: 'TXN-000009' > 'TXN-000010' as text.

    Taking MAX over the numeric suffix is what prevents a duplicate code
    once the sequence crosses a power of ten.
    """
    with finance_db(empty_db) as connection:
        for sequence in range(1, 11):
            connection.execute(
                "INSERT INTO categories (code, name) VALUES (?, ?)",
                (f"CAT-{sequence:03d}", f"Category {sequence}"),
            )

        assert next_code(connection, "categories") == "CAT-011"


def test_codes_are_not_reused_after_a_hard_delete(coded_db: Path):
    """
    A code must never point at a different record than it once did.

    A max-based allocator fails here: deleting CAT-003 lowers the maximum
    to 2, so the next category would be handed CAT-003 again. Budgets and
    goals have no soft-delete column, so this is a real path.
    """
    add_category("Transport", db_path=coded_db)
    add_category("Travel", db_path=coded_db)

    with finance_db(coded_db) as connection:
        connection.execute("DELETE FROM categories WHERE code = 'CAT-003'")

    reused = add_category("Something Else", db_path=coded_db)
    assert reused.code == "CAT-004"


def test_sequence_survives_deleting_every_row(coded_db: Path):
    """Even an empty table must not restart at 001."""
    add_category("Transport", db_path=coded_db)

    with finance_db(coded_db) as connection:
        connection.execute("DELETE FROM categories")
        assert next_code(connection, "categories") == "CAT-003"


def test_sequence_recovers_if_the_counter_table_is_lost(coded_db: Path):
    """
    The data high-water mark is a floor under the stored counter.

    If code_sequences were cleared or restored from an older backup, the
    allocator must still not hand out a code that is already in use.
    """
    add_category("Transport", db_path=coded_db)

    with finance_db(coded_db) as connection:
        connection.execute("DELETE FROM code_sequences")
        assert next_code(connection, "categories") == "CAT-003"


# --- Service layer assigns codes ------------------------------------


def test_new_category_and_subcategory_get_codes(coded_db: Path):
    category = add_category("Transport", emoji="🚌", db_path=coded_db)
    subcategory = add_subcategory("Transport", "Taxi", db_path=coded_db)

    assert category.code == "CAT-002"
    assert subcategory.code == "SUB-001"

    stored = {item.name: item.code for item in list_categories(db_path=coded_db)}
    assert stored["Food"] == "CAT-001"
    assert stored["Transport"] == "CAT-002"

    subs = list_subcategories(category="Transport", db_path=coded_db)
    assert [item.code for item in subs] == ["SUB-001"]


def test_recorded_transactions_get_sequential_codes(coded_db: Path):
    first = record_transaction(
        amount="10.00",
        category="Food",
        account=ACCOUNT,
        occurred_at=datetime(2026, 3, 1, 12, 0, 0),
        db_path=coded_db,
    )
    second = record_transaction(
        amount="20.00",
        category="Food",
        account=ACCOUNT,
        occurred_at=datetime(2026, 3, 2, 12, 0, 0),
        db_path=coded_db,
    )

    assert first.code == "TXN-000001"
    assert second.code == "TXN-000002"


def test_lookup_by_code_round_trips(coded_db: Path):
    recorded = record_transaction(
        amount="12.34",
        category="Food",
        account=ACCOUNT,
        occurred_at=datetime(2026, 3, 1, 12, 0, 0),
        db_path=coded_db,
    )

    assert recorded.code is not None
    found = get_transaction_by_code(recorded.code, db_path=coded_db)

    assert found is not None
    assert found.id == recorded.id
    assert found.base_amount_minor == 1234

    # Case and spacing forgiveness reaches all the way through.
    assert get_transaction_by_code("txn-000001", db_path=coded_db) is not None


def test_lookup_by_code_returns_none_for_a_missing_record(coded_db: Path):
    assert get_transaction_by_code("TXN-999999", db_path=coded_db) is None


def test_lookup_by_code_rejects_the_wrong_table(coded_db: Path):
    with pytest.raises(InvalidCodeError):
        get_transaction_by_code("CAT-001", db_path=coded_db)


def test_soft_deleted_transaction_is_not_found_by_code(coded_db: Path):
    recorded = record_transaction(
        amount="5.00",
        category="Food",
        account=ACCOUNT,
        occurred_at=datetime(2026, 3, 1, 12, 0, 0),
        db_path=coded_db,
    )
    assert recorded.code is not None

    with finance_db(coded_db) as connection:
        connection.execute(
            "UPDATE transactions SET deleted_at = '2026-03-02T00:00:00Z' WHERE id = ?",
            (recorded.id,),
        )

    assert get_transaction_by_code(recorded.code, db_path=coded_db) is None
    assert get_transaction(recorded.id, db_path=coded_db) is None


def test_code_survives_the_sequence_after_a_soft_delete(coded_db: Path):
    """Soft-deleted rows keep their code, so the next one does not collide."""
    first = record_transaction(
        amount="5.00",
        category="Food",
        account=ACCOUNT,
        occurred_at=datetime(2026, 3, 1, 12, 0, 0),
        db_path=coded_db,
    )

    with finance_db(coded_db) as connection:
        connection.execute(
            "UPDATE transactions SET deleted_at = '2026-03-02T00:00:00Z' WHERE id = ?",
            (first.id,),
        )

    second = record_transaction(
        amount="6.00",
        category="Food",
        account=ACCOUNT,
        occurred_at=datetime(2026, 3, 3, 12, 0, 0),
        db_path=coded_db,
    )

    assert second.code == "TXN-000002"


# --- Migration ------------------------------------------------------


def _build_v1_database(path: Path) -> None:
    """
    Create a database shaped like schema v1: no code columns at all.

    Built by hand rather than by checking out the old module, so the
    migration is tested against the shape it will actually meet.
    """
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.executescript(
        """
        CREATE TABLE accounts (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            is_active INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE categories (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            emoji TEXT,
            is_active INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE subcategories (
            id INTEGER PRIMARY KEY,
            category_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            is_active INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE transactions (
            id INTEGER PRIMARY KEY,
            occurred_at TEXT NOT NULL,
            account_id INTEGER NOT NULL,
            category_id INTEGER NOT NULL,
            subcategory_id INTEGER,
            note TEXT,
            description TEXT,
            direction TEXT NOT NULL,
            amount_minor INTEGER NOT NULL,
            currency TEXT NOT NULL,
            fx_rate_scaled INTEGER NOT NULL,
            base_amount_minor INTEGER NOT NULL,
            base_currency TEXT NOT NULL DEFAULT 'MYR',
            source TEXT NOT NULL DEFAULT 'manual',
            deleted_at TEXT
        );
        CREATE TABLE budgets (
            id INTEGER PRIMARY KEY,
            month TEXT NOT NULL,
            category_id INTEGER NOT NULL,
            limit_minor INTEGER NOT NULL
        );
        CREATE TABLE goals (
            id INTEGER PRIMARY KEY,
            month TEXT NOT NULL,
            target_income_minor INTEGER,
            target_savings_minor INTEGER,
            notes TEXT
        );

        INSERT INTO accounts (name) VALUES ('Bank Accounts');
        INSERT INTO categories (name, emoji) VALUES ('Food', '🍜');
        INSERT INTO categories (name, emoji) VALUES ('Travel', '✈️');
        """
    )

    # Inserted newest-first, exactly as the Money Manager import did.
    for row_id, occurred_at in enumerate(
        ["2026-08-13T21:02:21", "2026-08-11T18:50:20", "2026-02-08T12:05:42"],
        start=1,
    ):
        connection.execute(
            """
            INSERT INTO transactions (
                id, occurred_at, account_id, category_id, direction,
                amount_minor, currency, fx_rate_scaled, base_amount_minor,
                base_currency, source
            ) VALUES (?, ?, 1, 1, 'expense', 1000, 'MYR', 100000000, 1000,
                      'MYR', 'import')
            """,
            (row_id, occurred_at),
        )

    connection.execute("PRAGMA user_version = 1")
    connection.commit()
    connection.close()


@pytest.fixture
def v1_db(tmp_path: Path) -> Path:
    path = tmp_path / "v1.sqlite3"
    _build_v1_database(path)

    return path


def test_migration_reports_v1_before_running(v1_db: Path):
    assert get_schema_version(v1_db) == 1


def test_migration_adds_and_backfills_codes(v1_db: Path):
    assert migrate_finance_db(v1_db) == SCHEMA_VERSION

    with finance_db(v1_db) as connection:
        categories = connection.execute(
            "SELECT code, name FROM categories ORDER BY id"
        ).fetchall()
        assert [row["code"] for row in categories] == ["CAT-001", "CAT-002"]

        accounts = connection.execute("SELECT code FROM accounts").fetchall()
        assert [row["code"] for row in accounts] == ["ACC-001"]


def test_migration_numbers_transactions_chronologically(v1_db: Path):
    """
    The whole reason codes are not derived from row id.

    The import inserted newest-first, so id 1 is the *newest* row. After
    migration the oldest transaction must hold TXN-000001.
    """
    migrate_finance_db(v1_db)

    with finance_db(v1_db) as connection:
        rows = connection.execute(
            "SELECT id, code, occurred_at FROM transactions ORDER BY occurred_at"
        ).fetchall()

    assert [row["code"] for row in rows] == [
        "TXN-000001",
        "TXN-000002",
        "TXN-000003",
    ]
    # Oldest row holds the first code despite having the highest id.
    assert rows[0]["occurred_at"] == "2026-02-08T12:05:42"
    assert rows[0]["id"] == 3
    assert rows[-1]["id"] == 1


def test_migration_is_idempotent(v1_db: Path):
    migrate_finance_db(v1_db)

    with finance_db(v1_db) as connection:
        before = connection.execute(
            "SELECT id, code FROM transactions ORDER BY id"
        ).fetchall()

    assert migrate_finance_db(v1_db) == SCHEMA_VERSION
    assert migrate_finance_db(v1_db) == SCHEMA_VERSION

    with finance_db(v1_db) as connection:
        after = connection.execute(
            "SELECT id, code FROM transactions ORDER BY id"
        ).fetchall()

    assert [dict(row) for row in before] == [dict(row) for row in after]


def test_migration_leaves_money_untouched(v1_db: Path):
    with finance_db(v1_db) as connection:
        before = connection.execute(
            "SELECT id, amount_minor, base_amount_minor, fx_rate_scaled, "
            "occurred_at FROM transactions ORDER BY id"
        ).fetchall()

    migrate_finance_db(v1_db)

    with finance_db(v1_db) as connection:
        after = connection.execute(
            "SELECT id, amount_minor, base_amount_minor, fx_rate_scaled, "
            "occurred_at FROM transactions ORDER BY id"
        ).fetchall()

    assert [dict(row) for row in before] == [dict(row) for row in after]


def test_new_inserts_continue_the_migrated_sequence(v1_db: Path):
    migrate_finance_db(v1_db)

    recorded = record_transaction(
        amount="9.99",
        category="Food",
        account="Bank Accounts",
        occurred_at=datetime(2026, 9, 1, 12, 0, 0),
        db_path=v1_db,
    )

    assert recorded.code == "TXN-000004"


def test_unique_index_blocks_a_duplicate_code(v1_db: Path):
    migrate_finance_db(v1_db)

    with pytest.raises(sqlite3.IntegrityError):
        with finance_db(v1_db) as connection:
            connection.execute(
                "UPDATE categories SET code = 'CAT-001' WHERE code = 'CAT-002'"
            )


def test_init_on_a_v1_database_migrates_rather_than_mislabelling(v1_db: Path):
    """
    init_finance_db must not stamp v2 on a database that lacks the
    columns. Its CREATE TABLE statements are no-ops on an existing
    database, so it delegates to the migration.
    """
    init_finance_db(v1_db)

    assert get_schema_version(v1_db) == SCHEMA_VERSION

    with finance_db(v1_db) as connection:
        missing = connection.execute(
            "SELECT count(*) AS n FROM transactions WHERE code IS NULL"
        ).fetchone()

    assert missing["n"] == 0


def test_fresh_database_is_at_the_current_version(empty_db: Path):
    assert get_schema_version(empty_db) == SCHEMA_VERSION

    with finance_db(empty_db) as connection:
        for spec in CODE_SPECS.values():
            columns = connection.execute(
                f"PRAGMA table_info({spec.table})"
            ).fetchall()
            assert "code" in {row["name"] for row in columns}


def test_backfill_continues_rather_than_renumbering(coded_db: Path):
    """Rows that already hold a code must never be renumbered."""
    add_category("Transport", db_path=coded_db)

    with finance_db(coded_db) as connection:
        connection.execute(
            "INSERT INTO categories (name) VALUES ('Uncoded')"
        )
        assigned = backfill_missing_codes(connection)

        rows = connection.execute(
            "SELECT name, code FROM categories ORDER BY id"
        ).fetchall()

    assert assigned == {"categories": 1}
    assert [(row["name"], row["code"]) for row in rows] == [
        ("Food", "CAT-001"),
        ("Transport", "CAT-002"),
        ("Uncoded", "CAT-003"),
    ]


# --- Against a copy of the real database ----------------------------


def test_real_database_migrates_cleanly(imported_db: Path, tmp_path: Path):
    """
    Rehearses the one-way migration against real financial history.

    Asserts the two things that must hold: no money moves, and every row
    ends up with a unique code numbered oldest-first.
    """
    with finance_db(imported_db) as connection:
        before = connection.execute(
            """
            SELECT COALESCE(SUM(base_amount_minor), 0) AS total,
                   count(*) AS rows
            FROM transactions WHERE deleted_at IS NULL
            """
        ).fetchone()
        before_total, before_rows = before["total"], before["rows"]

    migrate_finance_db(imported_db)

    with finance_db(imported_db) as connection:
        after = connection.execute(
            """
            SELECT COALESCE(SUM(base_amount_minor), 0) AS total,
                   count(*) AS rows,
                   count(code) AS coded,
                   count(DISTINCT code) AS distinct_codes
            FROM transactions WHERE deleted_at IS NULL
            """
        ).fetchone()

        ordering = connection.execute(
            """
            SELECT code FROM transactions
            WHERE deleted_at IS NULL
            ORDER BY occurred_at, id
            """
        ).fetchall()

    assert after["total"] == before_total
    assert after["rows"] == before_rows
    assert after["coded"] == before_rows
    assert after["distinct_codes"] == before_rows

    # Chronological order must match code order exactly.
    codes = [row["code"] for row in ordering]
    assert codes == sorted(codes)
    assert codes[0] == "TXN-000001"


def test_real_database_summary_is_unchanged_by_migration(
    imported_db: Path, tmp_path: Path
):
    """The migration must not disturb a single aggregate."""
    from src.finance.summary import all_time_summary

    untouched = tmp_path / "untouched.sqlite3"
    shutil.copyfile(imported_db, untouched)

    before = all_time_summary(untouched)
    migrate_finance_db(imported_db)
    after = all_time_summary(imported_db)

    assert before is not None and after is not None
    assert before.total_expense_minor == after.total_expense_minor
    assert before.total_income_minor == after.total_income_minor
    assert before.transaction_count == after.transaction_count
    assert before.by_category == after.by_category
    assert before.daily_totals == after.daily_totals
