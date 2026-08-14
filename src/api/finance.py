"""
Finance HTTP endpoints for the dashboard.

Every route delegates to `src.finance.service` or `src.finance.summary`
and issues no SQL of its own, so the browser and the agent cannot drift
apart on validation, FX resolution, or money arithmetic.

Money crosses this boundary as integer minor units plus a preformatted
display string, never as a JSON number. A JSON number is an IEEE double
by the time most clients see it, and 0.1 + 0.2 is exactly the class of
bug the whole finance module is built to avoid. Amounts arriving from
the browser are strings for the same reason.
"""

from __future__ import annotations

from datetime import date
from decimal import InvalidOperation
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from src.finance import service, summary
from src.finance.codes import InvalidCodeError
from src.finance.fx import (
    BASE_CURRENCY,
    FxRateUnavailableError,
    get_exchange_rate_setting,
    list_exchange_rate_settings,
    set_exchange_rate_setting,
)
from src.finance.money import format_minor_units, from_minor_units


# The dashboard *page* is served at /finance. Keeping the JSON under
# /api/finance means a future page route can never collide with a data
# route, and a 404 tells you immediately which of the two you missed.
router = APIRouter(prefix="/api/finance", tags=["finance"])

# An amount as typed by a person: digits with an optional decimal part.
# Kept as a string all the way into `money.py`, which parses it with
# Decimal.
AMOUNT_PATTERN = r"^-?\d{1,12}(\.\d{1,4})?$"


# --- Wire models ----------------------------------------------------


class MoneyResponse(BaseModel):
    """
    One money value, sent three ways.

    `minor` is authoritative. `decimal` is a string so no client can
    parse it into a float by accident, and `display` is ready to render.
    """

    minor: int
    decimal: str
    display: str
    currency: str = BASE_CURRENCY


def money(minor_units: int, currency: str = BASE_CURRENCY) -> MoneyResponse:
    return MoneyResponse(
        minor=minor_units,
        decimal=str(from_minor_units(minor_units)),
        display=format_minor_units(minor_units),
        currency=currency,
    )


class TransactionResponse(BaseModel):
    code: str | None
    occurred_at: str
    account: str
    category: str
    subcategory: str | None
    note: str | None
    description: str | None
    direction: str
    amount: MoneyResponse
    base_amount: MoneyResponse
    fx_rate: str
    source: str


def to_transaction_response(
    transaction: service.Transaction,
) -> TransactionResponse:
    from src.finance.money import unscale_rate

    return TransactionResponse(
        code=transaction.code,
        occurred_at=transaction.occurred_at,
        account=transaction.account,
        category=transaction.category,
        subcategory=transaction.subcategory,
        note=transaction.note,
        description=transaction.description,
        direction=transaction.direction,
        amount=money(transaction.amount_minor, transaction.currency),
        base_amount=money(transaction.base_amount_minor, transaction.base_currency),
        fx_rate=str(unscale_rate(transaction.fx_rate_scaled)),
        source=transaction.source,
    )


class TransactionPageResponse(BaseModel):
    transactions: list[TransactionResponse]
    total: int
    limit: int
    offset: int
    has_more: bool


class TransactionCreateRequest(BaseModel):
    amount: str = Field(pattern=AMOUNT_PATTERN, description="Major units, e.g. 12.34")
    category: str = Field(min_length=1)
    currency: str = Field(default=BASE_CURRENCY, min_length=3, max_length=3)
    direction: str = Field(default="expense")
    account: str = Field(default=service.DEFAULT_ACCOUNT, min_length=1)
    subcategory: str | None = None
    occurred_at: str | None = Field(
        default=None,
        description="Naive local time, e.g. 2026-08-13T21:02:21. Defaults to now.",
    )
    note: str | None = None
    description: str | None = None


class TransactionUpdateRequest(BaseModel):
    """
    Every field is optional. Omitting one leaves it alone; sending null
    for a nullable field clears it.
    """

    amount: str | None = Field(default=None, pattern=AMOUNT_PATTERN)
    category: str | None = None
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    direction: str | None = None
    account: str | None = None
    subcategory: str | None = None
    occurred_at: str | None = None
    note: str | None = None
    description: str | None = None


class CategoryResponse(BaseModel):
    code: str | None
    name: str
    emoji: str | None
    is_active: bool


class SubcategoryResponse(BaseModel):
    code: str | None
    category: str
    name: str
    is_active: bool


class AccountResponse(BaseModel):
    code: str | None
    name: str
    is_active: bool


class CategoryCreateRequest(BaseModel):
    name: str = Field(min_length=1)
    emoji: str | None = None


class SubcategoryCreateRequest(BaseModel):
    category: str = Field(min_length=1)
    name: str = Field(min_length=1)


class CategoryUpdateRequest(BaseModel):
    """
    Rename a category and/or change its emoji.

    Omitting `emoji` leaves it alone; sending null clears it. Both fields
    optional so a caller can change either independently.
    """

    new_name: str | None = Field(default=None, min_length=1)
    emoji: str | None = None


class SubcategoryUpdateRequest(BaseModel):
    new_name: str = Field(min_length=1)


class AccountCreateRequest(BaseModel):
    name: str = Field(min_length=1)


class CategoryTotalResponse(BaseModel):
    category: str
    emoji: str | None
    expense: MoneyResponse
    income: MoneyResponse
    transaction_count: int


class SubcategoryTotalResponse(BaseModel):
    category: str
    subcategory: str | None
    expense: MoneyResponse
    income: MoneyResponse
    transaction_count: int


class DailyTotalResponse(BaseModel):
    day: str
    expense: MoneyResponse
    income: MoneyResponse
    transaction_count: int


class SummaryResponse(BaseModel):
    period_start: str
    period_end: str
    day_count: int
    base_currency: str
    total_expense: MoneyResponse
    total_income: MoneyResponse
    net: MoneyResponse
    average_daily_expense: MoneyResponse
    transaction_count: int
    by_category: list[CategoryTotalResponse]
    by_subcategory: list[SubcategoryTotalResponse]
    daily_totals: list[DailyTotalResponse]


def to_summary_response(period: summary.PeriodSummary) -> SummaryResponse:
    return SummaryResponse(
        period_start=period.period_start.isoformat(),
        period_end=period.period_end.isoformat(),
        day_count=period.day_count,
        base_currency=period.base_currency,
        total_expense=money(period.total_expense_minor),
        total_income=money(period.total_income_minor),
        net=money(period.net_minor),
        average_daily_expense=money(period.average_daily_expense_minor),
        transaction_count=period.transaction_count,
        by_category=[
            CategoryTotalResponse(
                category=entry.category,
                emoji=entry.emoji,
                expense=money(entry.expense_minor),
                income=money(entry.income_minor),
                transaction_count=entry.transaction_count,
            )
            for entry in period.by_category
        ],
        by_subcategory=[
            SubcategoryTotalResponse(
                category=entry.category,
                subcategory=entry.subcategory,
                expense=money(entry.expense_minor),
                income=money(entry.income_minor),
                transaction_count=entry.transaction_count,
            )
            for entry in period.by_subcategory
        ],
        daily_totals=[
            DailyTotalResponse(
                day=entry.day.isoformat(),
                expense=money(entry.expense_minor),
                income=money(entry.income_minor),
                transaction_count=entry.transaction_count,
            )
            for entry in period.daily_totals
        ],
    )


class ComparisonResponse(BaseModel):
    previous_start: str
    previous_end: str
    previous_expense: MoneyResponse
    expense_delta: MoneyResponse
    expense_change_percent: str | None


class BudgetResponse(BaseModel):
    code: str | None
    month: str
    category: str
    category_emoji: str | None
    limit: MoneyResponse
    spent: MoneyResponse
    remaining: MoneyResponse
    percent_used: str
    is_over: bool


class BudgetRequest(BaseModel):
    month: str = Field(description="YYYY-MM or YYYY-MM-DD")
    category: str = Field(min_length=1)
    limit: str = Field(pattern=AMOUNT_PATTERN)


class GoalResponse(BaseModel):
    code: str | None
    month: str
    target_income: MoneyResponse | None
    target_savings: MoneyResponse | None
    notes: str | None


class GoalRequest(BaseModel):
    month: str
    target_income: str | None = Field(default=None, pattern=AMOUNT_PATTERN)
    target_savings: str | None = Field(default=None, pattern=AMOUNT_PATTERN)
    notes: str | None = None


class ExchangeRateResponse(BaseModel):
    currency: str
    mode: str
    manual_rate: str | None
    updated_at: str


class ExchangeRateRequest(BaseModel):
    mode: str = Field(pattern="^(auto|manual)$")
    manual_rate: str | None = None


class ExplainRequest(BaseModel):
    month: str | None = None
    start: date | None = None
    end: date | None = None


class ExplainResponse(BaseModel):
    """
    Narrated commentary plus the exact figures it was written from.

    `figures` is returned alongside the prose so the caller can always
    show the numbers, and so a reader can check the narration against
    its own source. `narrated` is False when the model was unavailable
    and `commentary` therefore holds the raw figures instead.
    """

    period_start: str
    period_end: str
    commentary: str
    figures: str
    narrated: bool


class OverviewResponse(BaseModel):
    """
    Everything the dashboard needs for one month, in a single call.

    The page would otherwise fire five requests on load and again after
    every edit; the widgets must agree with each other, and one snapshot
    guarantees that.
    """

    month: str
    summary: SummaryResponse
    comparison: ComparisonResponse
    budgets: list[BudgetResponse]
    goal: GoalResponse | None
    categories: list[CategoryResponse]
    subcategories: list[SubcategoryResponse]
    accounts: list[AccountResponse]
    recent: list[TransactionResponse]


# --- Error translation ----------------------------------------------


def _fail(error: Exception) -> HTTPException:
    """
    Map a service error onto a status code.

    The service's messages already list the valid options, so they are
    passed through verbatim rather than replaced with something vaguer.
    """
    if isinstance(error, InvalidCodeError):
        return HTTPException(status_code=422, detail=str(error))

    if isinstance(
        error,
        (
            service.UnknownAccountError,
            service.UnknownCategoryError,
            service.UnknownSubcategoryError,
        ),
    ):
        return HTTPException(status_code=404, detail=str(error))

    if isinstance(error, FxRateUnavailableError):
        return HTTPException(status_code=503, detail=str(error))

    if isinstance(error, (service.FinanceError, ValueError, InvalidOperation)):
        return HTTPException(status_code=400, detail=str(error))

    raise error


def _parse_month(month: str | None) -> date:
    """Resolve a `YYYY-MM` query parameter, defaulting to the current month."""
    if month is None:
        today = service.local_now().date()
        return today.replace(day=1)

    try:
        return date.fromisoformat(service.normalize_month(month))
    except service.FinanceError as error:
        raise _fail(error) from error


def _parse_moment(value: str | None):
    from datetime import datetime

    if value is None:
        return None

    try:
        return datetime.fromisoformat(value)
    except ValueError as error:
        raise HTTPException(
            status_code=400,
            detail=f"{value!r} is not an ISO 8601 date-time.",
        ) from error


# --- Reference data -------------------------------------------------


@router.get("/categories", response_model=list[CategoryResponse])
def get_categories(include_inactive: bool = False) -> list[CategoryResponse]:
    return [
        CategoryResponse(
            code=item.code,
            name=item.name,
            emoji=item.emoji,
            is_active=item.is_active,
        )
        for item in service.list_categories(include_inactive=include_inactive)
    ]


@router.post("/categories", response_model=CategoryResponse, status_code=201)
def create_category(request: CategoryCreateRequest) -> CategoryResponse:
    try:
        created = service.add_category(request.name, emoji=request.emoji)
    except Exception as error:
        raise _fail(error) from error

    return CategoryResponse(
        code=created.code,
        name=created.name,
        emoji=created.emoji,
        is_active=created.is_active,
    )


@router.patch("/categories/{name}", response_model=CategoryResponse)
def edit_category(name: str, request: CategoryUpdateRequest) -> CategoryResponse:
    """
    Rename a category or change its emoji.

    Renaming updates every past transaction's label at once, because
    transactions reference the category by id.
    """
    supplied = request.model_dump(exclude_unset=True)

    try:
        updated = service.update_category(
            name,
            new_name=supplied.get("new_name"),
            **({"emoji": supplied["emoji"]} if "emoji" in supplied else {}),
        )
    except Exception as error:
        raise _fail(error) from error

    return CategoryResponse(
        code=updated.code,
        name=updated.name,
        emoji=updated.emoji,
        is_active=updated.is_active,
    )


@router.delete("/categories/{name}", response_model=CategoryResponse)
def deactivate_category(name: str) -> CategoryResponse:
    """Soft delete. Historical transactions keep their category."""
    try:
        removed = service.deactivate_category(name)
    except Exception as error:
        raise _fail(error) from error

    return CategoryResponse(
        code=removed.code,
        name=removed.name,
        emoji=removed.emoji,
        is_active=removed.is_active,
    )


@router.get("/subcategories", response_model=list[SubcategoryResponse])
def get_subcategories(
    category: str | None = None,
    include_inactive: bool = False,
) -> list[SubcategoryResponse]:
    return [
        SubcategoryResponse(
            code=item.code,
            category=item.category_name,
            name=item.name,
            is_active=item.is_active,
        )
        for item in service.list_subcategories(
            category=category, include_inactive=include_inactive
        )
    ]


@router.post("/subcategories", response_model=SubcategoryResponse, status_code=201)
def create_subcategory(request: SubcategoryCreateRequest) -> SubcategoryResponse:
    try:
        created = service.add_subcategory(request.category, request.name)
    except Exception as error:
        raise _fail(error) from error

    return SubcategoryResponse(
        code=created.code,
        category=created.category_name,
        name=created.name,
        is_active=created.is_active,
    )


@router.patch(
    "/subcategories/{category}/{name}", response_model=SubcategoryResponse
)
def edit_subcategory(
    category: str, name: str, request: SubcategoryUpdateRequest
) -> SubcategoryResponse:
    """
    Rename a subcategory within its existing category.

    A subcategory cannot be moved to a different parent; see the service
    layer for why that would corrupt historical rows.
    """
    try:
        updated = service.update_subcategory(category, name, request.new_name)
    except Exception as error:
        raise _fail(error) from error

    return SubcategoryResponse(
        code=updated.code,
        category=updated.category_name,
        name=updated.name,
        is_active=updated.is_active,
    )


@router.get("/accounts", response_model=list[AccountResponse])
def get_accounts(include_inactive: bool = False) -> list[AccountResponse]:
    return [
        AccountResponse(code=item.code, name=item.name, is_active=item.is_active)
        for item in service.list_accounts(include_inactive=include_inactive)
    ]


@router.post("/accounts", response_model=AccountResponse, status_code=201)
def create_account(request: AccountCreateRequest) -> AccountResponse:
    try:
        created = service.add_account(request.name)
    except Exception as error:
        raise _fail(error) from error

    return AccountResponse(
        code=created.code, name=created.name, is_active=created.is_active
    )


# --- Transactions ---------------------------------------------------


@router.get("/transactions", response_model=TransactionPageResponse)
def get_transactions(
    start: date | None = None,
    end: date | None = None,
    category: str | None = None,
    account: str | None = None,
    search: str | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> TransactionPageResponse:
    try:
        page = service.list_transactions(
            period_start=start,
            period_end=end,
            category=category,
            account=account,
            search=search,
            limit=limit,
            offset=offset,
        )
    except Exception as error:
        raise _fail(error) from error

    return TransactionPageResponse(
        transactions=[to_transaction_response(item) for item in page.transactions],
        total=page.total,
        limit=page.limit,
        offset=page.offset,
        has_more=page.has_more,
    )


@router.post("/transactions", response_model=TransactionResponse, status_code=201)
def create_transaction(request: TransactionCreateRequest) -> TransactionResponse:
    try:
        created = service.record_transaction(
            amount=request.amount,
            category=request.category,
            currency=request.currency,
            direction=request.direction,
            account=request.account,
            subcategory=request.subcategory,
            occurred_at=_parse_moment(request.occurred_at),
            note=request.note,
            description=request.description,
            source="manual",
        )
    except Exception as error:
        raise _fail(error) from error

    return to_transaction_response(created)


@router.get("/transactions/deleted", response_model=list[TransactionResponse])
def get_deleted_transactions(
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> list[TransactionResponse]:
    """
    Recently deleted transactions, newest removal first.

    Declared before /transactions/{code} so the literal path wins the match:
    otherwise "deleted" would be read as a transaction code.
    """
    return [to_transaction_response(item) for item in service.list_deleted_transactions(limit)]


@router.post("/transactions/{code}/restore", response_model=TransactionResponse)
def restore_one_transaction(code: str) -> TransactionResponse:
    """Undo a soft delete. Returns the transaction as it is once restored."""
    try:
        restored = service.restore_transaction(code)
    except Exception as error:
        raise _fail(error) from error

    return to_transaction_response(restored)


@router.get("/transactions/{code}", response_model=TransactionResponse)
def get_one_transaction(code: str) -> TransactionResponse:
    try:
        found = service.get_transaction_by_code(code)
    except Exception as error:
        raise _fail(error) from error

    if found is None:
        raise HTTPException(status_code=404, detail=f"No transaction {code}.")

    return to_transaction_response(found)


@router.patch("/transactions/{code}", response_model=TransactionResponse)
def edit_transaction(
    code: str,
    request: TransactionUpdateRequest,
) -> TransactionResponse:
    # Distinguishing "absent" from "explicitly null" is the whole reason
    # this goes through model_fields_set rather than reading attributes.
    supplied = request.model_dump(exclude_unset=True)
    optional: dict[str, Any] = {}

    for field in ("subcategory", "note", "description"):
        if field in supplied:
            optional[field] = supplied[field]

    try:
        updated = service.update_transaction(
            code,
            amount=supplied.get("amount"),
            currency=supplied.get("currency"),
            category=supplied.get("category"),
            account=supplied.get("account"),
            direction=supplied.get("direction"),
            occurred_at=_parse_moment(supplied.get("occurred_at")),
            **optional,
        )
    except Exception as error:
        raise _fail(error) from error

    return to_transaction_response(updated)


@router.delete("/transactions/{code}", response_model=TransactionResponse)
def remove_transaction(code: str) -> TransactionResponse:
    """Soft delete. Returns the transaction as it was, so the UI can confirm."""
    try:
        removed = service.delete_transaction(code)
    except Exception as error:
        raise _fail(error) from error

    return to_transaction_response(removed)


# --- Summary --------------------------------------------------------


@router.get("/summary", response_model=SummaryResponse)
def get_summary(
    start: date | None = None,
    end: date | None = None,
    month: str | None = None,
) -> SummaryResponse:
    """
    Aggregate a period. Supply either `month`, or both `start` and `end`.

    With nothing supplied it reports the current month.
    """
    if start is not None and end is not None:
        if end < start:
            raise HTTPException(status_code=400, detail="end is before start.")
        period = summary.summarize(start, end)
    else:
        first = _parse_month(month)
        period = summary.month_summary(first.year, first.month)

    return to_summary_response(period)


# --- Budgets and goals ----------------------------------------------


def _budget_responses(month: date) -> list[BudgetResponse]:
    return [
        BudgetResponse(
            code=item.budget.code,
            month=item.budget.month,
            category=item.budget.category,
            category_emoji=item.budget.category_emoji,
            limit=money(item.budget.limit_minor),
            spent=money(item.spent_minor),
            remaining=money(item.remaining_minor),
            percent_used=str(item.percent_used),
            is_over=item.is_over,
        )
        for item in service.budget_progress(month)
    ]


@router.get("/budgets", response_model=list[BudgetResponse])
def get_budgets(month: str | None = None) -> list[BudgetResponse]:
    return _budget_responses(_parse_month(month))


@router.put("/budgets", response_model=BudgetResponse)
def put_budget(request: BudgetRequest) -> BudgetResponse:
    try:
        stored = service.set_budget(request.month, request.category, request.limit)
    except Exception as error:
        raise _fail(error) from error

    month = date.fromisoformat(stored.month)
    for item in _budget_responses(month):
        if item.code == stored.code:
            return item

    raise HTTPException(status_code=500, detail="Budget stored but not readable.")


@router.delete("/budgets/{code}")
def remove_budget(code: str) -> dict[str, str]:
    try:
        removed = service.delete_budget(code)
    except Exception as error:
        raise _fail(error) from error

    return {"code": removed.code or code, "category": removed.category}


def _goal_response(goal: service.Goal | None) -> GoalResponse | None:
    if goal is None:
        return None

    return GoalResponse(
        code=goal.code,
        month=goal.month,
        target_income=(
            money(goal.target_income_minor)
            if goal.target_income_minor is not None
            else None
        ),
        target_savings=(
            money(goal.target_savings_minor)
            if goal.target_savings_minor is not None
            else None
        ),
        notes=goal.notes,
    )


@router.get("/goals", response_model=GoalResponse | None)
def get_goal(month: str | None = None) -> GoalResponse | None:
    return _goal_response(service.get_goal(_parse_month(month)))


@router.put("/goals", response_model=GoalResponse)
def put_goal(request: GoalRequest) -> GoalResponse:
    supplied = request.model_dump(exclude_unset=True)

    try:
        stored = service.set_goal(
            request.month,
            target_income=supplied.get("target_income"),
            target_savings=supplied.get("target_savings"),
            **({"notes": supplied["notes"]} if "notes" in supplied else {}),
        )
    except Exception as error:
        raise _fail(error) from error

    response = _goal_response(stored)
    assert response is not None

    return response


# --- Exchange rates -------------------------------------------------


@router.get("/exchange-rate-settings", response_model=list[ExchangeRateResponse])
def get_exchange_rates() -> list[ExchangeRateResponse]:
    return [
        ExchangeRateResponse(
            currency=item.currency,
            mode=item.mode,
            manual_rate=str(item.manual_rate) if item.manual_rate else None,
            updated_at=item.updated_at,
        )
        for item in list_exchange_rate_settings()
    ]


@router.put(
    "/exchange-rate-settings/{currency}", response_model=ExchangeRateResponse
)
def put_exchange_rate(
    currency: str, request: ExchangeRateRequest
) -> ExchangeRateResponse:
    try:
        set_exchange_rate_setting(
            currency, request.mode, manual_rate=request.manual_rate
        )
    except Exception as error:
        raise _fail(error) from error

    stored = get_exchange_rate_setting(currency)
    if stored is None:
        raise HTTPException(status_code=500, detail="Setting stored but not readable.")

    return ExchangeRateResponse(
        currency=stored.currency,
        mode=stored.mode,
        manual_rate=str(stored.manual_rate) if stored.manual_rate else None,
        updated_at=stored.updated_at,
    )


# --- Narration ------------------------------------------------------


@router.post("/explain", response_model=ExplainResponse)
def explain_period(request: ExplainRequest) -> ExplainResponse:
    """
    Narrate a period in plain prose, on demand.

    Deliberately a POST and deliberately not part of /overview: it costs
    a model call, so it happens only when the user asks for it. The
    widgets stay live and free.

    The model never sees the database. It receives the same formatted
    text `format_period_summary` gives the agent, and every figure it may
    use is already in that text.
    """
    if request.start is not None and request.end is not None:
        if request.end < request.start:
            raise HTTPException(status_code=400, detail="end is before start.")
        period = summary.summarize(request.start, request.end, fill_empty_days=False)
    else:
        first = _parse_month(request.month)
        start, end = summary.month_bounds(first.year, first.month)
        period = summary.summarize(start, end, fill_empty_days=False)

    figures = summary.format_period_summary(period)

    # Imported here so the finance API does not pull in the model stack
    # unless narration is actually requested.
    from src.llm.finance_report import narrate_period_summary

    commentary, narrated = narrate_period_summary(figures)

    return ExplainResponse(
        period_start=period.period_start.isoformat(),
        period_end=period.period_end.isoformat(),
        commentary=commentary,
        figures=figures,
        narrated=narrated,
    )


# --- Dashboard bootstrap --------------------------------------------


@router.get("/overview", response_model=OverviewResponse)
def get_overview(month: str | None = None) -> OverviewResponse:
    """One snapshot powering the whole dashboard for a month."""
    first = _parse_month(month)

    comparison = summary.month_over_month(first.year, first.month)
    period = comparison.current
    previous = comparison.previous

    recent = service.list_transactions(
        period_start=period.period_start,
        period_end=period.period_end,
        limit=50,
    )

    return OverviewResponse(
        month=first.strftime("%Y-%m"),
        summary=to_summary_response(period),
        comparison=ComparisonResponse(
            previous_start=previous.period_start.isoformat(),
            previous_end=previous.period_end.isoformat(),
            previous_expense=money(previous.total_expense_minor),
            expense_delta=money(comparison.expense_delta_minor),
            expense_change_percent=(
                str(comparison.expense_change_percent)
                if comparison.expense_change_percent is not None
                else None
            ),
        ),
        budgets=_budget_responses(first),
        goal=_goal_response(service.get_goal(first)),
        categories=[
            CategoryResponse(
                code=item.code,
                name=item.name,
                emoji=item.emoji,
                is_active=item.is_active,
            )
            for item in service.list_categories()
        ],
        subcategories=[
            SubcategoryResponse(
                code=item.code,
                category=item.category_name,
                name=item.name,
                is_active=item.is_active,
            )
            for item in service.list_subcategories()
        ],
        accounts=[
            AccountResponse(code=item.code, name=item.name, is_active=item.is_active)
            for item in service.list_accounts()
        ],
        recent=[to_transaction_response(item) for item in recent.transactions],
    )
