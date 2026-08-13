"""
Tests for the finance agent tools.

The tools are invoked through their LangChain `.invoke({...})` interface,
which is how the agent calls them, so schema coercion is exercised too.

Two properties matter most and are asserted repeatedly:

- Tool output carries formatted money, never raw minor units. A model
  that sees `131869` may well try to divide it.
- Errors come back as readable strings listing the valid options, rather
  than exceptions, so the agent can correct itself in the next step.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from src.db.finance_sqlite import use_finance_db
from src.finance.fx import set_exchange_rate_setting
from src.finance.service import (
    add_account,
    add_category,
    add_subcategory,
    get_transaction_by_code,
    list_transactions,
    record_transaction,
)
from src.tools.finance import (
    FINANCE_TOOLS,
    add_finance_category,
    delete_finance_transaction,
    get_finance_budgets,
    get_finance_summary,
    list_finance_categories,
    list_finance_transactions,
    record_finance_transaction,
    set_finance_budget,
    set_finance_goal,
    update_finance_category,
    update_finance_subcategory,
    update_finance_transaction,
)


ACCOUNT = "Bank Accounts"


@pytest.fixture
def agent_db(empty_db: Path):
    """
    Reference data and March transactions, with the tools pointed at it.

    The tools take no db_path argument by design: the agent must not be
    able to choose a database. `use_finance_db` is how a test redirects
    them.
    """
    add_account(ACCOUNT, db_path=empty_db)
    add_category("Food", emoji="🍜", db_path=empty_db)
    add_category("Transport", emoji="🚌", db_path=empty_db)
    add_subcategory("Food", "Groceries", db_path=empty_db)
    set_exchange_rate_setting("CNY", "manual", manual_rate="0.60", db_path=empty_db)

    for day, amount, category in ((2, "10.00", "Food"), (5, "25.50", "Food")):
        record_transaction(
            amount=amount,
            category=category,
            account=ACCOUNT,
            occurred_at=datetime(2026, 3, day, 12, 0, 0),
            db_path=empty_db,
        )

    with use_finance_db(empty_db):
        yield empty_db


# --- Registration ---------------------------------------------------


def test_every_tool_has_a_unique_name_and_description():
    names = [tool.name for tool in FINANCE_TOOLS]

    assert len(names) == len(set(names))
    assert all(tool.description for tool in FINANCE_TOOLS)


def test_tools_are_registered_with_the_agent():
    from src.llm.langchain_agent import TOOLS

    registered = {tool.name for tool in TOOLS}

    for tool in FINANCE_TOOLS:
        assert tool.name in registered


# --- Recording ------------------------------------------------------


def test_record_returns_the_design_code(agent_db: Path):
    result = record_finance_transaction.invoke(
        {
            "amount": "12.34",
            "category": "Food",
            "subcategory": "Groceries",
            "note": "Lunch",
            "occurred_at": "2026-03-10T13:00:00",
        }
    )

    assert "TXN-000003" in result
    assert "MYR 12.34" in result
    assert "Groceries" in result

    stored = get_transaction_by_code("TXN-000003", db_path=agent_db)
    assert stored is not None
    assert stored.source == "agent"


def test_record_accepts_a_bare_date(agent_db: Path):
    record_finance_transaction.invoke(
        {"amount": "5.00", "category": "Food", "occurred_at": "2026-03-11"}
    )

    stored = get_transaction_by_code("TXN-000003", db_path=agent_db)
    assert stored is not None
    assert stored.occurred_at == "2026-03-11T00:00:00"


def test_record_converts_foreign_currency_server_side(agent_db: Path):
    result = record_finance_transaction.invoke(
        {
            "amount": "100.00",
            "currency": "CNY",
            "category": "Food",
            "occurred_at": "2026-03-12T10:00:00",
        }
    )

    # Both the original and the converted amount appear, and the model
    # did not compute either.
    assert "MYR 60.00" in result
    assert "CNY 100.00" in result


def test_record_income_is_marked_positive(agent_db: Path):
    result = record_finance_transaction.invoke(
        {
            "amount": "1000.00",
            "category": "Food",
            "direction": "income",
            "occurred_at": "2026-03-15T10:00:00",
        }
    )

    assert "+MYR 1,000.00" in result


def test_record_with_unknown_category_lists_the_options(agent_db: Path):
    result = record_finance_transaction.invoke(
        {"amount": "5.00", "category": "Groceries"}
    )

    assert "Could not record" in result
    assert "Food" in result and "Transport" in result
    # An error, not an exception: the agent needs to read and retry.
    assert isinstance(result, str)


def test_record_rejects_a_bad_timestamp(agent_db: Path):
    result = record_finance_transaction.invoke(
        {"amount": "5.00", "category": "Food", "occurred_at": "next Friday"}
    )

    assert "not a valid date" in result


def test_record_rejects_a_negative_amount(agent_db: Path):
    result = record_finance_transaction.invoke(
        {"amount": "-5.00", "category": "Food"}
    )

    assert "greater than zero" in result


# --- Listing and summarising ----------------------------------------


def test_list_shows_codes_and_formatted_money(agent_db: Path):
    result = list_finance_transactions.invoke({"month": "2026-03"})

    assert "TXN-000001" in result
    assert "TXN-000002" in result
    assert "-MYR 25.50" in result
    # Raw minor units must never reach the model.
    assert "2550" not in result


def test_list_reports_an_empty_period_plainly(agent_db: Path):
    result = list_finance_transactions.invoke({"month": "2026-05"})

    assert "No transactions" in result


def test_list_caps_the_page_and_says_so(agent_db: Path):
    for day in range(1, 12):
        record_transaction(
            amount="1.00",
            category="Food",
            account=ACCOUNT,
            occurred_at=datetime(2026, 4, day, 12, 0, 0),
            db_path=agent_db,
        )

    result = list_finance_transactions.invoke({"month": "2026-04", "limit": 5})

    assert "showing 5 of 11" in result
    assert "get_finance_summary" in result


def test_summary_hands_over_finished_numbers(agent_db: Path):
    result = get_finance_summary.invoke({"month": "2026-03"})

    assert "MYR 35.50" in result
    assert "Top categories" in result
    assert "🍜 Food" in result
    assert "3550" not in result


def test_summary_comparison_states_the_direction_in_words(agent_db: Path):
    record_transaction(
        amount="100.00",
        category="Food",
        account=ACCOUNT,
        occurred_at=datetime(2026, 4, 3, 12, 0, 0),
        db_path=agent_db,
    )

    result = get_finance_summary.invoke(
        {"month": "2026-04", "compare_to_previous": True}
    )

    assert "Previous period" in result
    assert "up " in result
    assert "%" in result


def test_summary_handles_a_previous_period_of_zero(agent_db: Path):
    result = get_finance_summary.invoke(
        {"month": "2026-03", "compare_to_previous": True}
    )

    # No percentage is invented from a zero base.
    assert "no percentage change" in result


def test_summary_rejects_a_backwards_range(agent_db: Path):
    result = get_finance_summary.invoke(
        {"start": "2026-03-31", "end": "2026-03-01"}
    )

    assert "Could not summarize" in result


# --- Editing and deleting -------------------------------------------


def test_update_by_code(agent_db: Path):
    result = update_finance_transaction.invoke(
        {"code": "TXN-000001", "amount": "15.00", "note": "Corrected"}
    )

    assert "Updated" in result
    assert "MYR 15.00" in result
    assert "Corrected" in result


def test_update_accepts_a_lowercase_code(agent_db: Path):
    result = update_finance_transaction.invoke(
        {"code": "txn-000001", "note": "fine"}
    )

    assert "Updated" in result


def test_update_clears_a_field_with_a_dash(agent_db: Path):
    """Matches the todo convention: '-' means remove."""
    update_finance_transaction.invoke({"code": "TXN-000001", "note": "temporary"})
    update_finance_transaction.invoke({"code": "TXN-000001", "note": "-"})

    stored = get_transaction_by_code("TXN-000001", db_path=agent_db)
    assert stored is not None
    assert stored.note is None


def test_update_leaves_omitted_fields_alone(agent_db: Path):
    before = get_transaction_by_code("TXN-000001", db_path=agent_db)
    update_finance_transaction.invoke({"code": "TXN-000001", "note": "only this"})
    after = get_transaction_by_code("TXN-000001", db_path=agent_db)

    assert before is not None and after is not None
    assert after.amount_minor == before.amount_minor
    assert after.category == before.category
    assert after.occurred_at == before.occurred_at


def test_update_rejects_a_wrong_prefix(agent_db: Path):
    result = update_finance_transaction.invoke({"code": "CAT-001", "note": "x"})

    assert "Could not update" in result
    assert "categories record" in result


def test_update_reports_a_missing_code(agent_db: Path):
    result = update_finance_transaction.invoke({"code": "TXN-999999", "note": "x"})

    assert "No live transaction" in result


def test_delete_is_soft_and_reports_what_went(agent_db: Path):
    result = delete_finance_transaction.invoke({"code": "TXN-000002"})

    assert "Deleted" in result
    assert "MYR 25.50" in result

    assert get_transaction_by_code("TXN-000002", db_path=agent_db) is None
    assert list_transactions(db_path=agent_db).total == 1


def test_deleting_twice_reports_rather_than_raising(agent_db: Path):
    delete_finance_transaction.invoke({"code": "TXN-000002"})
    again = delete_finance_transaction.invoke({"code": "TXN-000002"})

    assert "Could not delete" in again


# --- Categories -----------------------------------------------------


def test_list_categories_includes_subcategories(agent_db: Path):
    result = list_finance_categories.invoke({})

    assert "🍜 Food" in result
    assert "Groceries" in result
    assert "🚌 Transport" in result


def test_list_categories_can_omit_subcategories(agent_db: Path):
    result = list_finance_categories.invoke({"include_subcategories": False})

    assert "Food" in result
    assert "Groceries" not in result


def test_add_category_returns_its_code(agent_db: Path):
    result = add_finance_category.invoke({"name": "Travel", "emoji": "✈️"})

    assert "CAT-003" in result
    assert "Travel" in result


def test_update_category_renames_and_reports_the_code(agent_db: Path):
    result = update_finance_category.invoke(
        {"name": "Food", "new_name": "Meals"}
    )

    assert "CAT-001" in result
    assert "Meals" in result
    # The emoji survives a rename that did not mention it.
    assert "🍜" in result


def test_update_category_carries_history(agent_db: Path):
    update_finance_category.invoke({"name": "Food", "new_name": "Meals"})

    listed = list_finance_transactions.invoke({"month": "2026-03"})

    assert "Meals" in listed
    assert "| Food |" not in listed


def test_update_category_clears_the_emoji_with_a_dash(agent_db: Path):
    result = update_finance_category.invoke({"name": "Food", "emoji": "-"})

    assert "🍜" not in result
    assert "Food" in result


def test_update_category_onto_another_is_reported(agent_db: Path):
    result = update_finance_category.invoke(
        {"name": "Food", "new_name": "Transport"}
    )

    assert "Could not update the category" in result
    assert "cannot be merged automatically" in result


def test_update_category_reports_an_unknown_name(agent_db: Path):
    result = update_finance_category.invoke({"name": "Ghost", "new_name": "X"})

    assert "Could not update the category" in result
    assert "Food" in result


def test_update_subcategory_renames(agent_db: Path):
    result = update_finance_subcategory.invoke(
        {"category": "Food", "name": "Groceries", "new_name": "Supermarket"}
    )

    assert "SUB-001" in result
    assert "Food / Supermarket" in result


def test_update_subcategory_onto_a_sibling_is_reported(agent_db: Path):
    add_subcategory("Food", "Dining", db_path=agent_db)

    result = update_finance_subcategory.invoke(
        {"category": "Food", "name": "Groceries", "new_name": "Dining"}
    )

    assert "Could not rename the subcategory" in result


def test_add_duplicate_category_is_reported(agent_db: Path):
    result = add_finance_category.invoke({"name": "Food"})

    assert "Could not create" in result
    assert "already exists" in result


# --- Budgets and goals ----------------------------------------------


def test_set_budget_reports_progress_against_it(agent_db: Path):
    result = set_finance_budget.invoke(
        {"month": "2026-03", "category": "Food", "limit": "50.00"}
    )

    assert "BGT-001" in result
    assert "MYR 50.00" in result
    assert "MYR 35.50" in result
    assert "within budget" in result


def test_budget_reports_being_over_in_words(agent_db: Path):
    result = set_finance_budget.invoke(
        {"month": "2026-03", "category": "Food", "limit": "10.00"}
    )

    assert "over budget" in result


def test_get_budgets_lists_actuals(agent_db: Path):
    set_finance_budget.invoke(
        {"month": "2026-03", "category": "Food", "limit": "50.00"}
    )

    result = get_finance_budgets.invoke({"month": "2026-03"})

    assert "Food" in result
    assert "MYR 35.50" in result
    assert "remaining" in result


def test_get_budgets_when_none_exist(agent_db: Path):
    result = get_finance_budgets.invoke({"month": "2026-09"})

    assert "No budgets set" in result


def test_budget_with_unknown_category_lists_options(agent_db: Path):
    result = set_finance_budget.invoke(
        {"month": "2026-03", "category": "Ghost", "limit": "10.00"}
    )

    assert "Could not set the budget" in result
    assert "Food" in result


def test_set_goal_reports_both_targets(agent_db: Path):
    result = set_finance_goal.invoke(
        {
            "month": "2026-03",
            "target_income": "3000.00",
            "target_savings": "500.00",
            "notes": "Trip fund",
        }
    )

    assert "GOL-001" in result
    assert "MYR 3,000.00" in result
    assert "MYR 500.00" in result
    assert "Trip fund" in result


def test_set_goal_leaves_omitted_targets_alone(agent_db: Path):
    set_finance_goal.invoke({"month": "2026-03", "target_income": "3000.00"})
    result = set_finance_goal.invoke(
        {"month": "2026-03", "target_savings": "500.00"}
    )

    assert "MYR 3,000.00" in result
    assert "MYR 500.00" in result


def test_set_goal_rejects_a_negative_target(agent_db: Path):
    result = set_finance_goal.invoke({"month": "2026-03", "target_income": "-1"})

    assert "Could not set the goal" in result


# --- The no-arithmetic guarantee ------------------------------------


def test_no_tool_output_leaks_raw_minor_units(agent_db: Path):
    """
    Sweep every read-shaped tool for a bare minor-unit integer.

    35.50 must never appear as 3550 anywhere the model can see it.
    """
    set_finance_budget.invoke(
        {"month": "2026-03", "category": "Food", "limit": "50.00"}
    )

    outputs = [
        list_finance_transactions.invoke({"month": "2026-03"}),
        get_finance_summary.invoke({"month": "2026-03"}),
        get_finance_budgets.invoke({"month": "2026-03"}),
        list_finance_categories.invoke({}),
    ]

    for text in outputs:
        assert "3550" not in text
        assert "5000" not in text
        assert "1000 " not in text
