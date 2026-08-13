"""
Tests for `src.finance.summary`.

Split deliberately into two kinds:

- Exact-value tests run against `seeded_db`, a small database built here
  with known amounts. They stay valid forever, because recording a real
  transaction tomorrow cannot change them.
- Invariant tests run against `imported_db`, a copy of the real
  database. They assert properties that must hold for *any* data
  (breakdowns reconcile, months partition the timeline), so they keep
  guarding real history without pinning it to today's totals.

Only the seeded database is written to. The real one is copied first and
never modified.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from src.db.finance_sqlite import FX_RATE_SCALE, finance_db
from src.finance.fx import set_exchange_rate_setting
from src.finance.money import format_minor_units
from src.finance.service import add_category, add_subcategory, record_transaction
from src.finance.summary import (
    PeriodSummary,
    all_time_summary,
    compare_to_previous_period,
    format_period_summary,
    month_bounds,
    month_over_month,
    month_summary,
    summarize,
    transaction_date_range,
    week_bounds,
    week_summary,
)


ACCOUNT = "Test Account"


@pytest.fixture
def seeded_db(empty_db: Path) -> Path:
    """
    A small database with known transactions.

    March 2026 holds MYR 202.75 of expense and MYR 50.00 of income over
    seven transactions. Two rows sit deliberately on period boundaries,
    and one is CNY so the base-amount conversion is exercised.
    """
    with finance_db(empty_db) as connection:
        connection.execute("INSERT INTO accounts (name) VALUES (?)", (ACCOUNT,))

    add_category("Food", emoji="🍜", db_path=empty_db)
    add_category("Transport", emoji="🚌", db_path=empty_db)
    add_category("Salary", emoji="💰", db_path=empty_db)
    add_subcategory("Food", "Groceries", db_path=empty_db)

    # Manual mode keeps the suite offline; auto mode would call out to
    # Frankfurter and make these tests depend on the network.
    set_exchange_rate_setting("CNY", "manual", manual_rate="0.60", db_path=empty_db)

    entries = [
        ("2026-03-02T09:00:00", "10.00", "MYR", "expense", "Food", "Groceries"),
        ("2026-03-02T18:30:00", "5.50", "MYR", "expense", "Food", "Groceries"),
        ("2026-03-05T12:00:00", "20.00", "MYR", "expense", "Food", None),
        ("2026-03-10T08:15:00", "7.25", "MYR", "expense", "Transport", None),
        ("2026-03-15T10:00:00", "50.00", "MYR", "income", "Salary", None),
        # 100.00 CNY at the manual 0.60 rate becomes MYR 60.00.
        ("2026-03-20T14:00:00", "100.00", "CNY", "expense", "Food", None),
        # Last possible minute of March: must land inside a March period.
        ("2026-03-31T23:59:00", "100.00", "MYR", "expense", "Food", None),
        # First instant of April: must not.
        ("2026-04-01T00:00:00", "999.00", "MYR", "expense", "Food", None),
    ]

    for occurred_at, amount, currency, direction, category, subcategory in entries:
        record_transaction(
            amount=amount,
            category=category,
            currency=currency,
            direction=direction,
            account=ACCOUNT,
            subcategory=subcategory,
            occurred_at=datetime.fromisoformat(occurred_at),
            source="manual",
            db_path=empty_db,
        )

    return empty_db


# --- Pure helpers, no database --------------------------------------


def test_month_bounds_handles_month_lengths():
    assert month_bounds(2026, 2) == (date(2026, 2, 1), date(2026, 2, 28))
    assert month_bounds(2024, 2) == (date(2024, 2, 1), date(2024, 2, 29))
    assert month_bounds(2026, 8) == (date(2026, 8, 1), date(2026, 8, 31))


def test_week_bounds_runs_monday_to_sunday():
    # 2026-08-13 is a Thursday.
    assert week_bounds(date(2026, 8, 13)) == (date(2026, 8, 10), date(2026, 8, 16))
    # A Monday is its own week start.
    assert week_bounds(date(2026, 8, 10))[0] == date(2026, 8, 10)
    # A Sunday belongs to the week that began six days earlier.
    assert week_bounds(date(2026, 8, 16)) == (date(2026, 8, 10), date(2026, 8, 16))


def test_summarize_rejects_a_backwards_range(seeded_db: Path):
    with pytest.raises(ValueError, match="before period_start"):
        summarize(date(2026, 3, 31), date(2026, 3, 1), db_path=seeded_db)


# --- Exact values against seeded data -------------------------------


def test_seeded_month_totals(seeded_db: Path):
    march = month_summary(2026, 3, db_path=seeded_db)

    assert march.total_expense_minor == 20275
    assert march.total_income_minor == 5000
    assert march.net_minor == 5000 - 20275
    assert march.transaction_count == 7
    assert march.base_currency == "MYR"
    assert march.day_count == 31


def test_foreign_currency_is_summed_in_base_units(seeded_db: Path):
    """The CNY row must contribute its MYR 60.00, not its CNY 100.00."""
    day = summarize(date(2026, 3, 20), date(2026, 3, 20), db_path=seeded_db)

    assert day.transaction_count == 1
    assert day.total_expense_minor == 6000


def test_last_minute_of_the_month_is_inside_the_period(seeded_db: Path):
    """
    The half-open upper bound must include 23:59 on the final day.

    This is the bug the bound design exists to prevent: a naive
    `occurred_at <= '2026-03-31'` string comparison would drop it.
    """
    march = month_summary(2026, 3, db_path=seeded_db)
    april = month_summary(2026, 4, db_path=seeded_db)

    last_day = summarize(date(2026, 3, 31), date(2026, 3, 31), db_path=seeded_db)
    assert last_day.transaction_count == 1
    assert last_day.total_expense_minor == 10000

    # And the first instant of April belongs to April alone.
    assert april.transaction_count == 1
    assert april.total_expense_minor == 99900
    assert march.transaction_count == 7


def test_category_breakdown_values(seeded_db: Path):
    march = month_summary(2026, 3, db_path=seeded_db)
    by_name = {entry.category: entry for entry in march.by_category}

    # Food: 10.00 + 5.50 + 20.00 + 60.00 (CNY) + 100.00
    assert by_name["Food"].expense_minor == 19550
    assert by_name["Food"].transaction_count == 5
    assert by_name["Food"].emoji == "🍜"

    assert by_name["Transport"].expense_minor == 725
    assert by_name["Salary"].income_minor == 5000
    assert by_name["Salary"].expense_minor == 0
    assert by_name["Salary"].net_minor == 5000

    # Ordered by expense descending, so income-only categories sink.
    assert [entry.category for entry in march.by_category] == [
        "Food",
        "Transport",
        "Salary",
    ]


def test_subcategory_breakdown_keeps_unfiled_rows(seeded_db: Path):
    march = month_summary(2026, 3, db_path=seeded_db)
    food = [e for e in march.by_subcategory if e.category == "Food"]
    by_sub = {entry.subcategory: entry for entry in food}

    assert by_sub["Groceries"].expense_minor == 1550
    assert by_sub["Groceries"].transaction_count == 2

    # Rows with no subcategory are kept under None so the breakdown
    # still reconciles to the category total.
    assert by_sub[None].expense_minor == 18000
    assert by_sub[None].transaction_count == 3
    assert sum(e.expense_minor for e in food) == 19550


def test_daily_totals_are_gap_filled(seeded_db: Path):
    march = month_summary(2026, 3, db_path=seeded_db)

    assert len(march.daily_totals) == 31
    assert [entry.day.day for entry in march.daily_totals] == list(range(1, 32))

    by_day = {entry.day: entry for entry in march.daily_totals}
    assert by_day[date(2026, 3, 2)].expense_minor == 1550
    assert by_day[date(2026, 3, 2)].transaction_count == 2

    # A day with nothing recorded is present and zeroed, not missing.
    assert by_day[date(2026, 3, 3)].expense_minor == 0
    assert by_day[date(2026, 3, 3)].transaction_count == 0


def test_gap_filling_can_be_disabled(seeded_db: Path):
    march = summarize(
        date(2026, 3, 1),
        date(2026, 3, 31),
        fill_empty_days=False,
        db_path=seeded_db,
    )

    assert len(march.daily_totals) == 6
    assert all(entry.transaction_count > 0 for entry in march.daily_totals)


def test_average_daily_expense_rounds_half_up(seeded_db: Path):
    march = month_summary(2026, 3, db_path=seeded_db)

    # 20275 sen over 31 days is 654.03..., which rounds to 654.
    assert march.average_daily_expense_minor == 654


def test_soft_deleted_rows_are_excluded(seeded_db: Path):
    before = month_summary(2026, 3, db_path=seeded_db)

    with finance_db(seeded_db) as connection:
        connection.execute(
            """
            UPDATE transactions
            SET deleted_at = '2026-04-01T00:00:00Z'
            WHERE amount_minor = 725
            """
        )

    after = month_summary(2026, 3, db_path=seeded_db)

    assert after.transaction_count == before.transaction_count - 1
    assert after.total_expense_minor == before.total_expense_minor - 725
    assert "Transport" not in {entry.category for entry in after.by_category}


def test_empty_period_is_all_zeroes(seeded_db: Path):
    quiet = month_summary(2026, 1, db_path=seeded_db)

    assert quiet.transaction_count == 0
    assert quiet.total_expense_minor == 0
    assert quiet.total_income_minor == 0
    assert quiet.net_minor == 0
    assert quiet.by_category == ()
    assert quiet.by_subcategory == ()
    assert quiet.average_daily_expense_minor == 0


def test_transaction_date_range_and_all_time(seeded_db: Path):
    assert transaction_date_range(seeded_db) == (date(2026, 3, 2), date(2026, 4, 1))

    everything = all_time_summary(seeded_db)
    assert everything is not None
    assert everything.transaction_count == 8
    assert everything.total_expense_minor == 20275 + 99900

    # The all-time view opts out of gap filling.
    assert len(everything.daily_totals) == 7


def test_empty_database_has_no_range(empty_db: Path):
    assert transaction_date_range(empty_db) is None
    assert all_time_summary(empty_db) is None


# --- Comparison -----------------------------------------------------


def test_month_over_month_uses_the_calendar(seeded_db: Path):
    comparison = month_over_month(2026, 4, db_path=seeded_db)

    assert comparison.current.period_start == date(2026, 4, 1)
    assert comparison.previous.period_start == date(2026, 3, 1)
    assert comparison.expense_delta_minor == 99900 - 20275


def test_month_over_month_crosses_the_year_boundary(seeded_db: Path):
    comparison = month_over_month(2026, 1, db_path=seeded_db)

    assert comparison.previous.period_start == date(2025, 12, 1)
    assert comparison.previous.period_end == date(2025, 12, 31)


def test_percent_change_is_decimal(seeded_db: Path):
    comparison = month_over_month(2026, 4, db_path=seeded_db)
    change = comparison.expense_change_percent

    assert isinstance(change, Decimal)
    # 20275 -> 99900 is +79625, which is 392.726...%, quantized to 392.7.
    assert change == Decimal("392.7")


def test_percent_change_is_none_against_an_empty_period(seeded_db: Path):
    """No sentinel value: an increase from zero is not a percentage."""
    comparison = month_over_month(2026, 3, db_path=seeded_db)

    assert comparison.previous.total_expense_minor == 0
    assert comparison.expense_change_percent is None
    assert comparison.income_change_percent is None


def test_compare_to_previous_period_matches_length(seeded_db: Path):
    comparison = compare_to_previous_period(
        date(2026, 3, 15), date(2026, 3, 21), db_path=seeded_db
    )

    assert comparison.current.day_count == 7
    assert comparison.previous.day_count == 7
    assert comparison.previous.period_end == date(2026, 3, 14)
    assert comparison.previous.period_start == date(2026, 3, 8)


# --- Narration ------------------------------------------------------


def test_format_period_summary_contains_formatted_money(seeded_db: Path):
    text = format_period_summary(month_summary(2026, 3, db_path=seeded_db))

    assert "MYR 202.75" in text
    assert "MYR 50.00" in text
    assert "🍜 Food: MYR 195.50" in text
    # Raw minor units must not leak to the model.
    assert "20275" not in text


def test_format_period_summary_pluralizes(seeded_db: Path):
    text = format_period_summary(month_summary(2026, 3, db_path=seeded_db))

    assert "(1 transaction)" in text
    assert "(5 transactions)" in text


def test_format_period_summary_omits_categories_when_empty(seeded_db: Path):
    text = format_period_summary(month_summary(2026, 1, db_path=seeded_db))

    assert "Top categories" not in text
    assert "MYR 0.00" in text


# --- Invariants against the real imported database ------------------


def _reconciles(summary: PeriodSummary) -> None:
    """Every breakdown must add back up to the headline totals."""
    assert sum(e.expense_minor for e in summary.by_category) == (
        summary.total_expense_minor
    )
    assert sum(e.income_minor for e in summary.by_category) == (
        summary.total_income_minor
    )
    assert sum(e.transaction_count for e in summary.by_category) == (
        summary.transaction_count
    )

    assert sum(e.expense_minor for e in summary.by_subcategory) == (
        summary.total_expense_minor
    )
    assert sum(e.transaction_count for e in summary.by_subcategory) == (
        summary.transaction_count
    )

    assert sum(d.expense_minor for d in summary.daily_totals) == (
        summary.total_expense_minor
    )
    assert sum(d.transaction_count for d in summary.daily_totals) == (
        summary.transaction_count
    )


def test_imported_history_reconciles(imported_db: Path):
    everything = all_time_summary(imported_db)

    assert everything is not None
    assert everything.transaction_count > 0
    _reconciles(everything)


def test_imported_months_partition_the_timeline(imported_db: Path):
    """
    Consecutive months must cover every transaction exactly once.

    This catches both a gap (a day belonging to no month) and an overlap
    (a day counted twice) without pinning the test to today's totals.
    """
    everything = all_time_summary(imported_db)
    assert everything is not None

    months: list[tuple[int, int]] = []
    year, month = everything.period_start.year, everything.period_start.month
    while (year, month) <= (everything.period_end.year, everything.period_end.month):
        months.append((year, month))
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)

    monthly = [month_summary(y, m, db_path=imported_db) for y, m in months]

    assert sum(s.total_expense_minor for s in monthly) == (
        everything.total_expense_minor
    )
    assert sum(s.total_income_minor for s in monthly) == everything.total_income_minor
    assert sum(s.transaction_count for s in monthly) == everything.transaction_count


def test_imported_day_boundaries_are_additive(imported_db: Path):
    """Splitting a range at any day must preserve its totals."""
    everything = all_time_summary(imported_db)
    assert everything is not None

    start, end = everything.period_start, everything.period_end
    split = start + (end - start) / 2

    first = summarize(start, split, db_path=imported_db)
    second = summarize(
        split.fromordinal(split.toordinal() + 1), end, db_path=imported_db
    )

    assert first.total_expense_minor + second.total_expense_minor == (
        everything.total_expense_minor
    )
    assert first.transaction_count + second.transaction_count == (
        everything.transaction_count
    )


def test_imported_base_amounts_are_the_only_thing_summed(imported_db: Path):
    """
    The summary total must equal SUM(base_amount_minor), never
    SUM(amount_minor), which mixes currencies in one column.
    """
    everything = all_time_summary(imported_db)
    assert everything is not None

    with finance_db(imported_db) as connection:
        row = connection.execute(
            """
            SELECT COALESCE(SUM(base_amount_minor), 0) AS base_total,
                   COALESCE(SUM(amount_minor), 0) AS raw_total
            FROM transactions
            WHERE deleted_at IS NULL
            """
        ).fetchone()

    assert (
        everything.total_expense_minor + everything.total_income_minor
        == row["base_total"]
    )
    # Guards against the two accidentally being interchangeable, which
    # would make the assertion above vacuous.
    assert row["raw_total"] != row["base_total"]


def test_imported_myr_rows_store_an_identity_rate(imported_db: Path):
    with finance_db(imported_db) as connection:
        row = connection.execute(
            """
            SELECT count(*) AS wrong
            FROM transactions
            WHERE deleted_at IS NULL AND currency = 'MYR' AND fx_rate_scaled != ?
            """,
            (FX_RATE_SCALE,),
        ).fetchone()

    assert row["wrong"] == 0


def test_imported_weeks_are_seven_days(imported_db: Path):
    everything = all_time_summary(imported_db)
    assert everything is not None

    week = week_summary(everything.period_end, db_path=imported_db)

    assert week.day_count == 7
    assert len(week.daily_totals) == 7
    assert week.period_start.weekday() == 0
    _reconciles(week)


def test_imported_narration_never_leaks_minor_units(imported_db: Path):
    everything = all_time_summary(imported_db)
    assert everything is not None

    text = format_period_summary(everything)

    assert format_minor_units(everything.total_expense_minor) in text
    assert str(everything.total_expense_minor) not in text
