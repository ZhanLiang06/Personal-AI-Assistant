"""
Read-only summary aggregation for the finance module.

Every number here is computed by SQLite, never by an LLM. The agent and
the scheduled reports receive finished figures and are only allowed to
narrate them. One implementation serves the dashboard widgets and the
weekly/monthly reports, so the two can never disagree.

Three rules shape the queries below:

- Only live rows count: `deleted_at IS NULL` is on every query.
- Only `base_amount_minor` is ever summed. `amount_minor` mixes CNY and
  MYR in the same column, so adding it across rows is meaningless.
- Days are grouped with `date(occurred_at)`, which is correct precisely
  because `occurred_at` is naive local Malaysia time. See the time
  storage rule in `context.md` before changing that.

Period bounds are inclusive dates. They are translated into a half-open
timestamp range (`>= start T00:00:00`, `< day-after-end T00:00:00`) so
the query uses the `occurred_at` index and cannot miss a transaction
recorded late in the evening of the final day.
"""

from __future__ import annotations

from calendar import monthrange
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from src.db.finance_sqlite import FINANCE_DB_PATH, finance_db
from src.finance.fx import BASE_CURRENCY
from src.finance.money import format_minor_units


# Rows are excluded from every aggregate unless they are live.
_LIVE = "t.deleted_at IS NULL AND t.occurred_at >= ? AND t.occurred_at < ?"

# Summing with a CASE keeps expense and income in one pass over the
# rows, instead of running the same scan twice with different filters.
_EXPENSE_SUM = (
    "COALESCE(SUM(CASE WHEN t.direction = 'expense' "
    "THEN t.base_amount_minor ELSE 0 END), 0)"
)
_INCOME_SUM = (
    "COALESCE(SUM(CASE WHEN t.direction = 'income' "
    "THEN t.base_amount_minor ELSE 0 END), 0)"
)


@dataclass(frozen=True)
class CategoryTotal:
    """One category's contribution to a period."""

    category: str
    emoji: str | None
    expense_minor: int
    income_minor: int
    transaction_count: int

    @property
    def net_minor(self) -> int:
        return self.income_minor - self.expense_minor


@dataclass(frozen=True)
class SubcategoryTotal:
    """
    One subcategory's contribution to a period.

    `subcategory` is None for transactions filed under a category with
    no subcategory chosen. Those rows are included deliberately, so the
    subcategory breakdown always adds up to the category total.
    """

    category: str
    subcategory: str | None
    expense_minor: int
    income_minor: int
    transaction_count: int

    @property
    def net_minor(self) -> int:
        return self.income_minor - self.expense_minor


@dataclass(frozen=True)
class DailyTotal:
    """One local calendar day's totals."""

    day: date
    expense_minor: int
    income_minor: int
    transaction_count: int

    @property
    def net_minor(self) -> int:
        return self.income_minor - self.expense_minor


@dataclass(frozen=True)
class PeriodSummary:
    """
    Everything the dashboard and the report writer need for one period.

    All money is MYR minor units (sen). `net_minor` is income minus
    expense, so a spending month is negative.
    """

    period_start: date
    period_end: date
    base_currency: str

    total_expense_minor: int
    total_income_minor: int
    transaction_count: int

    by_category: tuple[CategoryTotal, ...]
    by_subcategory: tuple[SubcategoryTotal, ...]
    daily_totals: tuple[DailyTotal, ...]

    @property
    def net_minor(self) -> int:
        return self.total_income_minor - self.total_expense_minor

    @property
    def day_count(self) -> int:
        return (self.period_end - self.period_start).days + 1

    @property
    def average_daily_expense_minor(self) -> int:
        """Mean expense per calendar day, including days with no spend."""
        average = Decimal(self.total_expense_minor) / Decimal(self.day_count)

        return int(average.to_integral_value(rounding=ROUND_HALF_UP))


@dataclass(frozen=True)
class PeriodComparison:
    """Two periods side by side, for month-over-month style widgets."""

    current: PeriodSummary
    previous: PeriodSummary

    @property
    def expense_delta_minor(self) -> int:
        return self.current.total_expense_minor - self.previous.total_expense_minor

    @property
    def income_delta_minor(self) -> int:
        return self.current.total_income_minor - self.previous.total_income_minor

    @property
    def expense_change_percent(self) -> Decimal | None:
        """
        Percent change in expense, or None when the previous period had
        no spending at all. None rather than a sentinel, because
        "infinite increase" is not a number anyone should render.
        """
        return _percent_change(
            self.previous.total_expense_minor,
            self.current.total_expense_minor,
        )

    @property
    def income_change_percent(self) -> Decimal | None:
        return _percent_change(
            self.previous.total_income_minor,
            self.current.total_income_minor,
        )


def _percent_change(previous_minor: int, current_minor: int) -> Decimal | None:
    if previous_minor == 0:
        return None

    change = (
        Decimal(current_minor - previous_minor)
        * Decimal(100)
        / Decimal(previous_minor)
    )

    return change.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)


def _bounds(period_start: date, period_end: date) -> tuple[str, str]:
    """
    Translate inclusive dates into a half-open timestamp range.

    The upper bound is midnight of the day after `period_end`, so a
    transaction at 23:59 on the last day is still inside the period.
    """
    if period_end < period_start:
        raise ValueError(
            f"period_end {period_end} is before period_start {period_start}."
        )

    return (
        f"{period_start.isoformat()}T00:00:00",
        f"{(period_end + timedelta(days=1)).isoformat()}T00:00:00",
    )


# --- Aggregation ----------------------------------------------------


def summarize(
    period_start: date,
    period_end: date,
    fill_empty_days: bool = True,
    db_path: str | Path | None = None,
) -> PeriodSummary:
    """
    Aggregate one inclusive date range into a `PeriodSummary`.

    Four grouped queries run on one connection: totals, by category, by
    subcategory, and per day. They are kept separate rather than fused
    into one query with rollups, because SQLite would then return a
    ragged result set that Python has to unpick anyway.
    """
    lower, upper = _bounds(period_start, period_end)
    parameters = (lower, upper)

    with finance_db(db_path) as connection:
        totals = connection.execute(
            f"""
            SELECT {_EXPENSE_SUM} AS expense_minor,
                   {_INCOME_SUM} AS income_minor,
                   COUNT(*) AS transaction_count
            FROM transactions t
            WHERE {_LIVE}
            """,
            parameters,
        ).fetchone()

        category_rows = connection.execute(
            f"""
            SELECT c.name AS category,
                   c.emoji AS emoji,
                   {_EXPENSE_SUM} AS expense_minor,
                   {_INCOME_SUM} AS income_minor,
                   COUNT(*) AS transaction_count
            FROM transactions t
            JOIN categories c ON c.id = t.category_id
            WHERE {_LIVE}
            GROUP BY c.id, c.name, c.emoji
            ORDER BY expense_minor DESC, income_minor DESC, c.name
            """,
            parameters,
        ).fetchall()

        subcategory_rows = connection.execute(
            f"""
            SELECT c.name AS category,
                   s.name AS subcategory,
                   {_EXPENSE_SUM} AS expense_minor,
                   {_INCOME_SUM} AS income_minor,
                   COUNT(*) AS transaction_count
            FROM transactions t
            JOIN categories c ON c.id = t.category_id
            LEFT JOIN subcategories s ON s.id = t.subcategory_id
            WHERE {_LIVE}
            GROUP BY c.id, c.name, s.id, s.name
            ORDER BY c.name, expense_minor DESC, s.name
            """,
            parameters,
        ).fetchall()

        daily_rows = connection.execute(
            f"""
            SELECT date(t.occurred_at) AS day,
                   {_EXPENSE_SUM} AS expense_minor,
                   {_INCOME_SUM} AS income_minor,
                   COUNT(*) AS transaction_count
            FROM transactions t
            WHERE {_LIVE}
            GROUP BY day
            ORDER BY day
            """,
            parameters,
        ).fetchall()

    daily_totals = [
        DailyTotal(
            day=date.fromisoformat(row["day"]),
            expense_minor=row["expense_minor"],
            income_minor=row["income_minor"],
            transaction_count=row["transaction_count"],
        )
        for row in daily_rows
    ]

    if fill_empty_days:
        daily_totals = _fill_empty_days(daily_totals, period_start, period_end)

    return PeriodSummary(
        period_start=period_start,
        period_end=period_end,
        base_currency=BASE_CURRENCY,
        total_expense_minor=totals["expense_minor"],
        total_income_minor=totals["income_minor"],
        transaction_count=totals["transaction_count"],
        by_category=tuple(
            CategoryTotal(
                category=row["category"],
                emoji=row["emoji"],
                expense_minor=row["expense_minor"],
                income_minor=row["income_minor"],
                transaction_count=row["transaction_count"],
            )
            for row in category_rows
        ),
        by_subcategory=tuple(
            SubcategoryTotal(
                category=row["category"],
                subcategory=row["subcategory"],
                expense_minor=row["expense_minor"],
                income_minor=row["income_minor"],
                transaction_count=row["transaction_count"],
            )
            for row in subcategory_rows
        ),
        daily_totals=tuple(daily_totals),
    )


def _fill_empty_days(
    daily_totals: list[DailyTotal],
    period_start: date,
    period_end: date,
) -> list[DailyTotal]:
    """
    Insert zero rows for days with no transactions.

    A trend chart needs a continuous axis; SQL only returns days that
    exist. This adds no arithmetic, only absent days at zero.
    """
    by_day = {entry.day: entry for entry in daily_totals}
    filled: list[DailyTotal] = []

    current = period_start
    while current <= period_end:
        filled.append(
            by_day.get(
                current,
                DailyTotal(
                    day=current,
                    expense_minor=0,
                    income_minor=0,
                    transaction_count=0,
                ),
            )
        )
        current += timedelta(days=1)

    return filled


# --- Period helpers -------------------------------------------------


def month_bounds(year: int, month: int) -> tuple[date, date]:
    """Return the first and last day of a calendar month."""
    last_day = monthrange(year, month)[1]

    return date(year, month, 1), date(year, month, last_day)


def week_bounds(any_date_in_week: date) -> tuple[date, date]:
    """Return the Monday and Sunday of the ISO week containing a date."""
    monday = any_date_in_week - timedelta(days=any_date_in_week.weekday())

    return monday, monday + timedelta(days=6)


def month_summary(
    year: int,
    month: int,
    db_path: str | Path | None = None,
) -> PeriodSummary:
    """Summarize one calendar month."""
    start, end = month_bounds(year, month)

    return summarize(start, end, db_path=db_path)


def week_summary(
    any_date_in_week: date,
    db_path: str | Path | None = None,
) -> PeriodSummary:
    """Summarize the Monday-to-Sunday week containing a date."""
    start, end = week_bounds(any_date_in_week)

    return summarize(start, end, db_path=db_path)


def transaction_date_range(
    db_path: str | Path | None = None,
) -> tuple[date, date] | None:
    """
    Return the first and last local day that has a live transaction.

    None when the table is empty, which lets callers avoid summarizing a
    range that does not exist yet.
    """
    with finance_db(db_path) as connection:
        row = connection.execute(
            """
            SELECT min(date(occurred_at)) AS first_day,
                   max(date(occurred_at)) AS last_day
            FROM transactions
            WHERE deleted_at IS NULL
            """
        ).fetchone()

    if row is None or row["first_day"] is None:
        return None

    return date.fromisoformat(row["first_day"]), date.fromisoformat(row["last_day"])


def all_time_summary(
    db_path: str | Path | None = None,
) -> PeriodSummary | None:
    """Summarize every live transaction, or None when there are none."""
    bounds = transaction_date_range(db_path)
    if bounds is None:
        return None

    start, end = bounds

    # Filling six months of empty days would be pure noise here; the
    # all-time view is read for totals, not for a daily chart.
    return summarize(start, end, fill_empty_days=False, db_path=db_path)


# --- Comparison -----------------------------------------------------


def compare(current: PeriodSummary, previous: PeriodSummary) -> PeriodComparison:
    """Pair two already-computed summaries. Pure; runs no queries."""
    return PeriodComparison(current=current, previous=previous)


def compare_to_previous_period(
    period_start: date,
    period_end: date,
    db_path: str | Path | None = None,
) -> PeriodComparison:
    """
    Compare a range against the equally long range ending just before it.

    For calendar months use `month_over_month`, which respects differing
    month lengths instead of subtracting a fixed number of days.
    """
    if period_end < period_start:
        raise ValueError(
            f"period_end {period_end} is before period_start {period_start}."
        )

    length = (period_end - period_start).days + 1
    previous_end = period_start - timedelta(days=1)
    previous_start = previous_end - timedelta(days=length - 1)

    return compare(
        current=summarize(period_start, period_end, db_path=db_path),
        previous=summarize(previous_start, previous_end, db_path=db_path),
    )


def month_over_month(
    year: int,
    month: int,
    db_path: str | Path | None = None,
) -> PeriodComparison:
    """Compare a calendar month against the calendar month before it."""
    previous_year, previous_month = (
        (year - 1, 12) if month == 1 else (year, month - 1)
    )

    return compare(
        current=month_summary(year, month, db_path=db_path),
        previous=month_summary(previous_year, previous_month, db_path=db_path),
    )


# --- Narration input ------------------------------------------------


def format_period_summary(
    summary: PeriodSummary,
    top_categories: int = 8,
) -> str:
    """
    Render a summary as finished text for the agent and the reports.

    Everything here is already computed and formatted, which is the
    point: the model receives strings to narrate, so it has no reason
    and no opportunity to do arithmetic of its own.
    """
    money = format_minor_units
    currency = summary.base_currency

    lines = [
        f"Period: {summary.period_start.isoformat()} to "
        f"{summary.period_end.isoformat()} ({summary.day_count} days)",
        f"Transactions: {summary.transaction_count}",
        f"Total expense: {currency} {money(summary.total_expense_minor)}",
        f"Total income: {currency} {money(summary.total_income_minor)}",
        f"Net: {currency} {money(summary.net_minor)}",
        f"Average expense per day: {currency} "
        f"{money(summary.average_daily_expense_minor)}",
    ]

    spending = [entry for entry in summary.by_category if entry.expense_minor > 0]
    if spending:
        lines.append("")
        lines.append(f"Top categories by expense (top {top_categories}):")
        for entry in spending[:top_categories]:
            label = f"{entry.emoji} {entry.category}" if entry.emoji else entry.category
            noun = "transaction" if entry.transaction_count == 1 else "transactions"
            lines.append(
                f"  {label}: {currency} {money(entry.expense_minor)} "
                f"({entry.transaction_count} {noun})"
            )

    return "\n".join(lines)
