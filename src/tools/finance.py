"""
LangChain tools exposing the finance module to the agent.

Every tool delegates to `src.finance.service` or `src.finance.summary`.
No SQL, no arithmetic, and no FX resolution happens here, so the agent
path and the dashboard path cannot drift apart.

Three rules shape what the model is allowed to see and do:

- **It never does arithmetic.** Tool output carries preformatted money
  strings, never raw minor units, so there is nothing to add up and no
  temptation to try. `get_finance_summary` in particular exists to hand
  over numbers that are already final.
- **It addresses records by design code**, never by list position. A
  code such as TXN-000038 is stable across refreshes, unlike the index +
  expected_text pattern the todo tools have to use.
- **It cannot invent reference data.** An unknown category raises an
  error listing the valid options, so the model corrects itself instead
  of quietly creating a near-duplicate.

Confirmation policy lives in the system prompt, not here: deletions and
budget or goal overwrites must be confirmed with the user first. These
tools deliberately still execute when called, because a tool that
second-guesses the agent mid-run cannot be reasoned about.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal, Optional

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from src.finance import service, summary
from src.finance.money import format_minor_units


MAX_LISTED_TRANSACTIONS = 40


# --- Schemas --------------------------------------------------------


class RecordTransactionInput(BaseModel):
    amount: str = Field(
        description=(
            "Amount in the transaction's own currency, as a string, always "
            "positive. Example: '12.34'. Never send a converted amount; the "
            "server does the conversion."
        )
    )
    category: str = Field(description="An existing category name.")
    subcategory: Optional[str] = Field(
        default=None,
        description="An existing subcategory of that category, if any.",
    )
    currency: str = Field(
        default="MYR", description="Three-letter code, e.g. MYR or CNY."
    )
    direction: Literal["expense", "income"] = Field(default="expense")
    account: Optional[str] = Field(
        default=None, description="Account name. Defaults to the main account."
    )
    occurred_at: Optional[str] = Field(
        default=None,
        description=(
            "Local Malaysia time as 'YYYY-MM-DDTHH:MM:SS', or 'YYYY-MM-DD' "
            "for a date without a time. Defaults to now."
        ),
    )
    note: Optional[str] = Field(default=None, description="Short note.")
    description: Optional[str] = Field(default=None)


class UpdateTransactionInput(BaseModel):
    code: str = Field(description="Design code, e.g. TXN-000038.")
    amount: Optional[str] = None
    category: Optional[str] = None
    subcategory: Optional[str] = Field(
        default=None,
        description="Send '-' to clear the subcategory.",
    )
    currency: Optional[str] = None
    direction: Optional[Literal["expense", "income"]] = None
    account: Optional[str] = None
    occurred_at: Optional[str] = None
    note: Optional[str] = Field(default=None, description="Send '-' to clear.")
    description: Optional[str] = Field(default=None, description="Send '-' to clear.")


class DeleteTransactionInput(BaseModel):
    code: str = Field(description="Design code, e.g. TXN-000038.")


class RestoreTransactionInput(BaseModel):
    code: str = Field(
        description="Design code of a deleted transaction, e.g. TXN-000038.",
    )


class ListDeletedTransactionsInput(BaseModel):
    limit: int = Field(default=10, ge=1, le=MAX_LISTED_TRANSACTIONS)


class ListTransactionsInput(BaseModel):
    start: Optional[str] = Field(default=None, description="YYYY-MM-DD, inclusive.")
    end: Optional[str] = Field(default=None, description="YYYY-MM-DD, inclusive.")
    month: Optional[str] = Field(
        default=None, description="YYYY-MM. Ignored when start and end are given."
    )
    category: Optional[str] = None
    search: Optional[str] = Field(
        default=None, description="Matches note, description, or code."
    )
    limit: int = Field(default=20, ge=1, le=MAX_LISTED_TRANSACTIONS)


class SummaryInput(BaseModel):
    month: Optional[str] = Field(
        default=None, description="YYYY-MM. Defaults to the current month."
    )
    start: Optional[str] = Field(default=None, description="YYYY-MM-DD, inclusive.")
    end: Optional[str] = Field(default=None, description="YYYY-MM-DD, inclusive.")
    compare_to_previous: bool = Field(
        default=False,
        description="Include the previous period's totals for comparison.",
    )


class BudgetInput(BaseModel):
    month: str = Field(description="YYYY-MM.")
    category: str = Field(description="An existing category name.")
    limit: str = Field(description="Monthly limit in MYR, e.g. '250.00'.")


class BudgetQueryInput(BaseModel):
    month: str = Field(description="YYYY-MM.")


class GoalInput(BaseModel):
    month: str = Field(description="YYYY-MM.")
    target_income: Optional[str] = Field(default=None, description="MYR amount.")
    target_savings: Optional[str] = Field(default=None, description="MYR amount.")
    notes: Optional[str] = None


class CategoryInput(BaseModel):
    name: str = Field(description="New category name.")
    emoji: Optional[str] = Field(default=None, description="Optional single emoji.")


class SubcategoryInput(BaseModel):
    category: str = Field(description="Existing parent category.")
    name: str = Field(description="New subcategory name.")


class UpdateCategoryInput(BaseModel):
    name: str = Field(description="The category's current name.")
    new_name: Optional[str] = Field(
        default=None, description="New name. Omit to keep the current one."
    )
    emoji: Optional[str] = Field(
        default=None,
        description="New emoji. Send '-' to remove it. Omit to leave unchanged.",
    )


class UpdateSubcategoryInput(BaseModel):
    category: str = Field(description="The parent category, which cannot change.")
    name: str = Field(description="The subcategory's current name.")
    new_name: str = Field(description="New name.")


class ListCategoriesInput(BaseModel):
    include_subcategories: bool = Field(default=True)


# --- Helpers --------------------------------------------------------


def _parse_moment(value: Optional[str]) -> Optional[datetime]:
    """Accept a date or a date-time; a bare date means midnight."""
    if not value:
        return None

    text = value.strip()

    try:
        if len(text) == 10:
            return datetime.combine(date.fromisoformat(text), datetime.min.time())
        return datetime.fromisoformat(text)
    except ValueError as error:
        raise ValueError(
            f"{value!r} is not a valid date or date-time. Use YYYY-MM-DD or "
            "YYYY-MM-DDTHH:MM:SS."
        ) from error


def _parse_day(value: Optional[str]) -> Optional[date]:
    if not value:
        return None

    try:
        return date.fromisoformat(value.strip())
    except ValueError as error:
        raise ValueError(f"{value!r} is not a date. Use YYYY-MM-DD.") from error


def _clearable(value: Optional[str]):
    """
    Translate the agent-facing convention into the service sentinel.

    Matches the todo tools: omitting a field leaves it alone, and '-'
    clears it. The service layer uses an UNSET sentinel for the same
    distinction.
    """
    if value is None:
        return service.UNSET

    return None if value.strip() == "-" else value


def _resolve_period(
    month: Optional[str],
    start: Optional[str],
    end: Optional[str],
) -> tuple[date, date]:
    if start and end:
        first, last = _parse_day(start), _parse_day(end)
        assert first is not None and last is not None

        if last < first:
            raise ValueError("end is before start.")

        return first, last

    reference = (
        date.fromisoformat(service.normalize_month(month))
        if month
        else service.local_now().date()
    )

    return summary.month_bounds(reference.year, reference.month)


def _describe(transaction: service.Transaction) -> str:
    """One transaction as a single readable line."""
    sign = "+" if transaction.direction == "income" else "-"
    amount = format_minor_units(transaction.base_amount_minor)

    parts = [
        f"{transaction.code}",
        transaction.occurred_at.replace("T", " "),
        transaction.category
        + (f" / {transaction.subcategory}" if transaction.subcategory else ""),
        f"{sign}MYR {amount}",
    ]

    if transaction.currency != transaction.base_currency:
        parts.append(
            f"({transaction.currency} "
            f"{format_minor_units(transaction.amount_minor)})"
        )

    if transaction.note:
        parts.append(f'"{transaction.note}"')

    return " | ".join(parts)


# --- Tools ----------------------------------------------------------


@tool("record_finance_transaction", args_schema=RecordTransactionInput)
def record_finance_transaction(
    amount: str,
    category: str,
    subcategory: Optional[str] = None,
    currency: str = "MYR",
    direction: str = "expense",
    account: Optional[str] = None,
    occurred_at: Optional[str] = None,
    note: Optional[str] = None,
    description: Optional[str] = None,
) -> str:
    """
    Record one spending or income transaction.

    The amount is always positive; `direction` carries the sign. The
    exchange rate and the MYR equivalent are resolved server-side, so
    never calculate a converted amount yourself.

    Returns the stored transaction including its design code, which is
    how it should be referred to afterwards.
    """
    try:
        stored = service.record_transaction(
            amount=amount,
            category=category,
            subcategory=subcategory,
            currency=currency,
            direction=direction,
            account=account or service.DEFAULT_ACCOUNT,
            occurred_at=_parse_moment(occurred_at),
            note=note,
            description=description,
            source="agent",
        )
    except Exception as error:
        return f"Could not record the transaction: {error}"

    return f"Recorded: {_describe(stored)}"


@tool("update_finance_transaction", args_schema=UpdateTransactionInput)
def update_finance_transaction(
    code: str,
    amount: Optional[str] = None,
    category: Optional[str] = None,
    subcategory: Optional[str] = None,
    currency: Optional[str] = None,
    direction: Optional[str] = None,
    account: Optional[str] = None,
    occurred_at: Optional[str] = None,
    note: Optional[str] = None,
    description: Optional[str] = None,
) -> str:
    """
    Edit a stored transaction, identified by its design code.

    Omit any field to leave it unchanged. Send '-' for subcategory, note,
    or description to clear that field.

    List the transaction first so the code is fresh. Changing the
    currency or the date resolves a new exchange rate; changing only the
    amount keeps the rate the transaction was recorded at.
    """
    try:
        updated = service.update_transaction(
            code,
            amount=amount,
            category=category,
            currency=currency,
            direction=direction,
            account=account,
            occurred_at=_parse_moment(occurred_at),
            subcategory=_clearable(subcategory),
            note=_clearable(note),
            description=_clearable(description),
        )
    except Exception as error:
        return f"Could not update {code}: {error}"

    return f"Updated: {_describe(updated)}"


@tool("delete_finance_transaction", args_schema=DeleteTransactionInput)
def delete_finance_transaction(code: str) -> str:
    """
    Delete a transaction, identified by its design code.

    Always confirm with the user before calling this. The deletion is a
    soft delete, so the record stops counting towards every total but is
    still recoverable from the database.
    """
    try:
        removed = service.delete_transaction(code)
    except Exception as error:
        return f"Could not delete {code}: {error}"

    return f"Deleted: {_describe(removed)}"


@tool("list_deleted_finance_transactions", args_schema=ListDeletedTransactionsInput)
def list_deleted_finance_transactions(limit: int = 10) -> str:
    """
    List recently deleted transactions, most recently deleted first.

    Use this to find the code to hand to `restore_finance_transaction` when
    the user asks to undo a deletion without naming one.
    """
    try:
        removed = service.list_deleted_transactions(limit)
    except Exception as error:
        return f"Could not list deleted transactions: {error}"

    if not removed:
        return "Nothing has been deleted."

    lines = [f"{index}. {_describe(item)}" for index, item in enumerate(removed, start=1)]
    return "Deleted transactions, most recent first:\n" + "\n".join(lines)


@tool("restore_finance_transaction", args_schema=RestoreTransactionInput)
def restore_finance_transaction(code: str) -> str:
    """
    Undo a deletion, identified by the transaction's design code.

    The record starts counting towards every total again at its original
    amount and date. Always confirm with the user before calling this, and
    say which transaction is coming back.
    """
    try:
        restored = service.restore_transaction(code)
    except Exception as error:
        return f"Could not restore {code}: {error}"

    return f"Restored: {_describe(restored)}"


@tool("list_finance_transactions", args_schema=ListTransactionsInput)
def list_finance_transactions(
    start: Optional[str] = None,
    end: Optional[str] = None,
    month: Optional[str] = None,
    category: Optional[str] = None,
    search: Optional[str] = None,
    limit: int = 20,
) -> str:
    """
    List recorded transactions, newest first, with their design codes.

    Use this before editing or deleting, so the code you act on is
    current. Defaults to the current month when no period is given.

    For totals, use get_finance_summary instead of adding these up.
    """
    try:
        first, last = _resolve_period(month, start, end)
        page = service.list_transactions(
            period_start=first,
            period_end=last,
            category=category,
            search=search,
            limit=min(limit, MAX_LISTED_TRANSACTIONS),
        )
    except Exception as error:
        return f"Could not list transactions: {error}"

    if not page.transactions:
        return (
            f"No transactions between {first.isoformat()} and {last.isoformat()}"
            + (f" in {category}" if category else "")
            + "."
        )

    lines = [_describe(item) for item in page.transactions]
    header = (
        f"Transactions {first.isoformat()} to {last.isoformat()} "
        f"(showing {len(lines)} of {page.total}):"
    )

    footer = (
        "\n\nMore exist beyond this page. Narrow the period or use "
        "get_finance_summary for totals."
        if page.has_more
        else ""
    )

    return f"{header}\n" + "\n".join(lines) + footer


@tool("get_finance_summary", args_schema=SummaryInput)
def get_finance_summary(
    month: Optional[str] = None,
    start: Optional[str] = None,
    end: Optional[str] = None,
    compare_to_previous: bool = False,
) -> str:
    """
    Get spending totals for a period, already calculated.

    Every figure returned is final. Report these numbers as given and do
    not add, subtract, or convert them; the database computed them.

    Defaults to the current month.
    """
    try:
        first, last = _resolve_period(month, start, end)
        period = summary.summarize(first, last, fill_empty_days=False)
    except Exception as error:
        return f"Could not summarize: {error}"

    report = summary.format_period_summary(period)

    if not compare_to_previous:
        return report

    try:
        comparison = summary.compare_to_previous_period(first, last)
    except Exception as error:
        return f"{report}\n\n(Comparison unavailable: {error})"

    previous = comparison.previous
    change = comparison.expense_change_percent

    trend = (
        "no spending in the previous period, so no percentage change"
        if change is None
        else f"{'up' if comparison.expense_delta_minor > 0 else 'down'} "
        f"{str(change).lstrip('-')}%"
    )

    return (
        f"{report}\n\n"
        f"Previous period {previous.period_start.isoformat()} to "
        f"{previous.period_end.isoformat()}: "
        f"expense MYR {format_minor_units(previous.total_expense_minor)}\n"
        f"Change: {trend}"
    )


@tool("list_finance_categories", args_schema=ListCategoriesInput)
def list_finance_categories(include_subcategories: bool = True) -> str:
    """
    List the categories and subcategories available for transactions.

    Call this when unsure which category to use. Transactions can only be
    filed under a category that already exists.
    """
    try:
        categories = service.list_categories()
        subcategories = service.list_subcategories() if include_subcategories else []
    except Exception as error:
        return f"Could not list categories: {error}"

    if not categories:
        return "No categories exist yet."

    children: dict[str, list[str]] = {}
    for item in subcategories:
        children.setdefault(item.category_name, []).append(item.name)

    lines = []
    for category in categories:
        label = f"{category.emoji} {category.name}" if category.emoji else category.name
        listed = children.get(category.name)
        lines.append(
            f"- {label}" + (f" (subcategories: {', '.join(listed)})" if listed else "")
        )

    return "Available categories:\n" + "\n".join(lines)


@tool("add_finance_category", args_schema=CategoryInput)
def add_finance_category(name: str, emoji: Optional[str] = None) -> str:
    """
    Create a new spending category.

    Check list_finance_categories first: a near-duplicate of an existing
    category splits historical reporting and cannot be merged later
    without editing every affected transaction.
    """
    try:
        created = service.add_category(name, emoji=emoji)
    except Exception as error:
        return f"Could not create the category: {error}"

    return f"Created category {created.code}: {created.emoji or ''} {created.name}".strip()


@tool("add_finance_subcategory", args_schema=SubcategoryInput)
def add_finance_subcategory(category: str, name: str) -> str:
    """Create a new subcategory under an existing category."""
    try:
        created = service.add_subcategory(category, name)
    except Exception as error:
        return f"Could not create the subcategory: {error}"

    return f"Created subcategory {created.code}: {created.category_name} / {created.name}"


@tool("update_finance_category", args_schema=UpdateCategoryInput)
def update_finance_category(
    name: str,
    new_name: Optional[str] = None,
    emoji: Optional[str] = None,
) -> str:
    """
    Rename a category or change its emoji.

    Use this to fix a typo or a wording change. Renaming carries the
    whole history with it, so every past transaction in that category
    shows the new name. Never deactivate a misnamed category and create
    a replacement instead; that splits the spending history permanently.

    Renaming onto the name of a different existing category is refused,
    because merging two categories is not something this can decide.
    """
    try:
        updated = service.update_category(
            name,
            new_name=new_name,
            emoji=_clearable(emoji),
        )
    except Exception as error:
        return f"Could not update the category: {error}"

    label = f"{updated.emoji} {updated.name}" if updated.emoji else updated.name

    return f"Updated category {updated.code}: now {label}"


@tool("update_finance_subcategory", args_schema=UpdateSubcategoryInput)
def update_finance_subcategory(category: str, name: str, new_name: str) -> str:
    """
    Rename a subcategory, keeping it under the same category.

    A subcategory cannot be moved to a different category. If that is
    what the user wants, create the subcategory under the new category
    and move the affected transactions across individually.
    """
    try:
        updated = service.update_subcategory(category, name, new_name)
    except Exception as error:
        return f"Could not rename the subcategory: {error}"

    return (
        f"Renamed subcategory {updated.code}: "
        f"{updated.category_name} / {updated.name}"
    )


@tool("set_finance_budget", args_schema=BudgetInput)
def set_finance_budget(month: str, category: str, limit: str) -> str:
    """
    Set or replace one category's spending limit for a month.

    Confirm with the user before overwriting a budget that already
    exists; check with get_finance_budgets first.
    """
    try:
        stored = service.set_budget(month, category, limit)
        progress = {
            item.budget.code: item for item in service.budget_progress(month)
        }.get(stored.code)
    except Exception as error:
        return f"Could not set the budget: {error}"

    if progress is None:
        return (
            f"Budget {stored.code} set: {stored.category} "
            f"MYR {format_minor_units(stored.limit_minor)} for {stored.month[:7]}."
        )

    state = "over budget" if progress.is_over else "within budget"

    return (
        f"Budget {stored.code} set: {stored.category} "
        f"MYR {format_minor_units(stored.limit_minor)} for {stored.month[:7]}. "
        f"Spent so far MYR {format_minor_units(progress.spent_minor)} "
        f"({progress.percent_used}% used, {state})."
    )


@tool("get_finance_budgets", args_schema=BudgetQueryInput)
def get_finance_budgets(month: str) -> str:
    """
    Show each budget for a month alongside the actual spend against it.

    All figures are already calculated; report them as given.
    """
    try:
        progress = service.budget_progress(month)
    except Exception as error:
        return f"Could not read budgets: {error}"

    if not progress:
        return f"No budgets set for {month}."

    lines = []
    for item in progress:
        remaining = format_minor_units(abs(item.remaining_minor))
        state = (
            f"over by MYR {remaining}"
            if item.is_over
            else f"MYR {remaining} remaining"
        )
        lines.append(
            f"- {item.budget.category}: spent MYR "
            f"{format_minor_units(item.spent_minor)} of "
            f"MYR {format_minor_units(item.budget.limit_minor)} "
            f"({item.percent_used}% used, {state})"
        )

    return f"Budgets for {month}:\n" + "\n".join(lines)


@tool("set_finance_goal", args_schema=GoalInput)
def set_finance_goal(
    month: str,
    target_income: Optional[str] = None,
    target_savings: Optional[str] = None,
    notes: Optional[str] = None,
) -> str:
    """
    Set the income and savings targets for a month.

    Confirm with the user before overwriting an existing goal. Omitted
    targets are left unchanged.
    """
    try:
        stored = service.set_goal(
            month,
            target_income=target_income,
            target_savings=target_savings,
            notes=_clearable(notes),
        )
    except Exception as error:
        return f"Could not set the goal: {error}"

    parts = [f"Goal {stored.code} for {stored.month[:7]}:"]

    if stored.target_income_minor is not None:
        parts.append(
            f"income target MYR {format_minor_units(stored.target_income_minor)}"
        )

    if stored.target_savings_minor is not None:
        parts.append(
            f"savings target MYR {format_minor_units(stored.target_savings_minor)}"
        )

    if stored.notes:
        parts.append(f'note "{stored.notes}"')

    return " ".join(parts)


FINANCE_TOOLS = [
    record_finance_transaction,
    update_finance_transaction,
    delete_finance_transaction,
    restore_finance_transaction,
    list_deleted_finance_transactions,
    list_finance_transactions,
    get_finance_summary,
    list_finance_categories,
    add_finance_category,
    add_finance_subcategory,
    update_finance_category,
    update_finance_subcategory,
    set_finance_budget,
    get_finance_budgets,
    set_finance_goal,
]
