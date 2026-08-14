"""
Tests for the finance service write path: listing, editing, deleting,
budgets, and goals.

All of these run against a seeded throwaway database. The FX policy is
set to manual so nothing here touches the network.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from src.db.finance_sqlite import FX_RATE_SCALE, finance_db
from src.finance.codes import InvalidCodeError
from src.finance.fx import set_exchange_rate_setting
from src.finance.service import (
    FinanceError,
    UnknownCategoryError,
    UnknownSubcategoryError,
    add_account,
    add_category,
    add_subcategory,
    budget_progress,
    deactivate_category,
    deactivate_subcategory,
    delete_budget,
    delete_transaction,
    get_goal,
    get_transaction_by_code,
    list_budgets,
    list_categories,
    list_deleted_transactions,
    list_subcategories,
    list_transactions,
    normalize_month,
    record_transaction,
    restore_transaction,
    set_budget,
    set_goal,
    update_category,
    update_subcategory,
    update_transaction,
)
from src.finance.summary import summarize


ACCOUNT = "Bank Accounts"


@pytest.fixture
def shop(empty_db: Path) -> Path:
    """A database with reference data and a handful of March transactions."""
    add_account(ACCOUNT, db_path=empty_db)
    add_account("Cash", db_path=empty_db)

    add_category("Food", emoji="🍜", db_path=empty_db)
    add_category("Transport", emoji="🚌", db_path=empty_db)
    add_category("Salary", emoji="💰", db_path=empty_db)
    add_subcategory("Food", "Groceries", db_path=empty_db)
    add_subcategory("Food", "Dining", db_path=empty_db)
    add_subcategory("Transport", "Taxi", db_path=empty_db)

    set_exchange_rate_setting("CNY", "manual", manual_rate="0.60", db_path=empty_db)

    for day, amount, category, subcategory, direction in (
        (2, "10.00", "Food", "Groceries", "expense"),
        (5, "25.00", "Food", "Dining", "expense"),
        (8, "7.50", "Transport", "Taxi", "expense"),
        (15, "1000.00", "Salary", None, "income"),
        (20, "40.00", "Food", None, "expense"),
    ):
        record_transaction(
            amount=amount,
            category=category,
            subcategory=subcategory,
            direction=direction,
            account=ACCOUNT,
            occurred_at=datetime(2026, 3, day, 12, 0, 0),
            db_path=empty_db,
        )

    return empty_db


# --- Listing --------------------------------------------------------


def test_list_returns_newest_first(shop: Path):
    page = list_transactions(db_path=shop)

    assert page.total == 5
    assert len(page.transactions) == 5
    assert page.transactions[0].occurred_at.startswith("2026-03-20")
    assert page.transactions[-1].occurred_at.startswith("2026-03-02")
    assert page.has_more is False


def test_list_filters_by_category_and_account(shop: Path):
    assert list_transactions(category="Food", db_path=shop).total == 3
    assert list_transactions(account="Cash", db_path=shop).total == 0
    assert list_transactions(account=ACCOUNT, db_path=shop).total == 5


def test_list_filters_by_inclusive_date_range(shop: Path):
    page = list_transactions(
        period_start=date(2026, 3, 5),
        period_end=date(2026, 3, 8),
        db_path=shop,
    )

    assert page.total == 2


def test_list_date_filter_includes_the_final_day(shop: Path):
    """Same half-open bound as the summary, so listings and totals agree."""
    record_transaction(
        amount="3.00",
        category="Food",
        account=ACCOUNT,
        occurred_at=datetime(2026, 3, 31, 23, 59, 0),
        db_path=shop,
    )

    page = list_transactions(
        period_start=date(2026, 3, 1),
        period_end=date(2026, 3, 31),
        db_path=shop,
    )

    assert page.total == 6


def test_list_paginates(shop: Path):
    first = list_transactions(limit=2, db_path=shop)
    second = list_transactions(limit=2, offset=2, db_path=shop)

    assert first.total == second.total == 5
    assert first.has_more is True
    assert len(second.transactions) == 2

    codes = {t.code for t in first.transactions} | {
        t.code for t in second.transactions
    }
    assert len(codes) == 4


def test_list_searches_note_and_code(shop: Path):
    record_transaction(
        amount="5.00",
        category="Food",
        account=ACCOUNT,
        note="Birthday cake",
        occurred_at=datetime(2026, 3, 25, 12, 0, 0),
        db_path=shop,
    )

    assert list_transactions(search="birthday", db_path=shop).total == 1
    assert list_transactions(search="TXN-000006", db_path=shop).total == 1


def test_list_rejects_a_nonsense_page(shop: Path):
    with pytest.raises(FinanceError):
        list_transactions(limit=0, db_path=shop)

    with pytest.raises(FinanceError):
        list_transactions(offset=-1, db_path=shop)


# --- Editing --------------------------------------------------------


def test_update_changes_only_what_was_passed(shop: Path):
    before = get_transaction_by_code("TXN-000001", db_path=shop)
    assert before is not None

    after = update_transaction("TXN-000001", note="Corrected", db_path=shop)

    assert after.note == "Corrected"
    assert after.amount_minor == before.amount_minor
    assert after.category == before.category
    assert after.subcategory == before.subcategory
    assert after.occurred_at == before.occurred_at


def test_update_amount_keeps_the_locked_rate(shop: Path):
    """
    Correcting a typo must not silently re-rate at today's rate.

    The stored rate is reused, so only the base amount moves, and it
    moves by exactly the same factor.
    """
    original = record_transaction(
        amount="100.00",
        currency="CNY",
        category="Food",
        account=ACCOUNT,
        occurred_at=datetime(2026, 3, 10, 12, 0, 0),
        db_path=shop,
    )
    assert original.base_amount_minor == 6000

    updated = update_transaction(original.code, amount="200.00", db_path=shop)

    assert updated.fx_rate_scaled == original.fx_rate_scaled
    assert updated.base_amount_minor == 12000


def test_update_currency_resolves_a_fresh_rate(shop: Path):
    original = record_transaction(
        amount="50.00",
        currency="MYR",
        category="Food",
        account=ACCOUNT,
        occurred_at=datetime(2026, 3, 10, 12, 0, 0),
        db_path=shop,
    )
    assert original.fx_rate_scaled == FX_RATE_SCALE

    updated = update_transaction(original.code, currency="CNY", db_path=shop)

    assert updated.currency == "CNY"
    assert updated.fx_rate_scaled == 60_000_000
    assert updated.base_amount_minor == 3000


def test_update_clears_a_field_with_an_explicit_none(shop: Path):
    update_transaction("TXN-000001", note="temporary", db_path=shop)
    cleared = update_transaction("TXN-000001", note=None, db_path=shop)

    assert cleared.note is None


def test_update_moving_category_drops_a_stale_subcategory(shop: Path):
    """Subcategories belong to one category, so the old one cannot survive."""
    before = get_transaction_by_code("TXN-000001", db_path=shop)
    assert before is not None and before.subcategory == "Groceries"

    moved = update_transaction("TXN-000001", category="Transport", db_path=shop)

    assert moved.category == "Transport"
    assert moved.subcategory is None


def test_update_can_move_category_and_set_a_new_subcategory(shop: Path):
    moved = update_transaction(
        "TXN-000001", category="Transport", subcategory="Taxi", db_path=shop
    )

    assert (moved.category, moved.subcategory) == ("Transport", "Taxi")


def test_update_rejects_a_subcategory_from_another_category(shop: Path):
    with pytest.raises(UnknownSubcategoryError):
        update_transaction(
            "TXN-000001", category="Transport", subcategory="Dining", db_path=shop
        )


def test_update_rejects_unknown_reference_data(shop: Path):
    with pytest.raises(UnknownCategoryError, match="Available"):
        update_transaction("TXN-000001", category="Nonexistent", db_path=shop)


def test_update_rejects_a_bad_direction_and_amount(shop: Path):
    with pytest.raises(FinanceError, match="direction must be"):
        update_transaction("TXN-000001", direction="refund", db_path=shop)

    with pytest.raises(FinanceError, match="greater than zero"):
        update_transaction("TXN-000001", amount="0", db_path=shop)


def test_update_rejects_a_float_amount(shop: Path):
    with pytest.raises(TypeError):
        update_transaction("TXN-000001", amount=12.34, db_path=shop)


def test_update_rejects_an_unknown_code(shop: Path):
    with pytest.raises(FinanceError, match="No live transaction"):
        update_transaction("TXN-999999", note="x", db_path=shop)

    with pytest.raises(InvalidCodeError):
        update_transaction("CAT-001", note="x", db_path=shop)


# --- Deleting -------------------------------------------------------


def test_delete_is_soft_and_returns_what_went(shop: Path):
    removed = delete_transaction("TXN-000002", db_path=shop)

    assert removed.amount_minor == 2500
    assert get_transaction_by_code("TXN-000002", db_path=shop) is None
    assert list_transactions(db_path=shop).total == 4

    with finance_db(shop) as connection:
        row = connection.execute(
            "SELECT deleted_at, code FROM transactions WHERE code = 'TXN-000002'"
        ).fetchone()

    # The row survives, so its code can never be recycled.
    assert row is not None
    assert row["deleted_at"] is not None


def test_deleting_twice_fails_clearly(shop: Path):
    delete_transaction("TXN-000002", db_path=shop)

    with pytest.raises(FinanceError, match="No live transaction"):
        delete_transaction("TXN-000002", db_path=shop)


def test_restore_puts_a_deleted_transaction_back(shop: Path):
    before = list_transactions(db_path=shop).total
    removed = delete_transaction("TXN-000002", db_path=shop)

    restored = restore_transaction("TXN-000002", db_path=shop)

    assert restored.amount_minor == removed.amount_minor
    assert restored.occurred_at == removed.occurred_at
    assert restored.category == removed.category
    assert get_transaction_by_code("TXN-000002", db_path=shop) is not None
    assert list_transactions(db_path=shop).total == before


def test_restored_transaction_counts_towards_totals_again(shop: Path):
    period = summarize(date(2026, 3, 1), date(2026, 3, 31), db_path=shop)
    before = period.total_expense_minor

    delete_transaction("TXN-000002", db_path=shop)
    during = summarize(date(2026, 3, 1), date(2026, 3, 31), db_path=shop)
    assert during.total_expense_minor < before

    restore_transaction("TXN-000002", db_path=shop)
    after = summarize(date(2026, 3, 1), date(2026, 3, 31), db_path=shop)
    assert after.total_expense_minor == before


def test_restoring_a_live_transaction_fails_clearly(shop: Path):
    with pytest.raises(FinanceError, match="is not deleted"):
        restore_transaction("TXN-000002", db_path=shop)


def test_restoring_an_unknown_code_fails_clearly(shop: Path):
    with pytest.raises(FinanceError, match="No transaction with code"):
        restore_transaction("TXN-009999", db_path=shop)


def test_deleted_list_is_newest_removal_first(shop: Path):
    delete_transaction("TXN-000002", db_path=shop)
    delete_transaction("TXN-000004", db_path=shop)

    removed = list_deleted_transactions(db_path=shop)

    assert [item.code for item in removed] == ["TXN-000004", "TXN-000002"]

    restore_transaction("TXN-000004", db_path=shop)
    assert [item.code for item in list_deleted_transactions(db_path=shop)] == ["TXN-000002"]


def test_deleted_transaction_cannot_be_edited(shop: Path):
    delete_transaction("TXN-000002", db_path=shop)

    with pytest.raises(FinanceError, match="No live transaction"):
        update_transaction("TXN-000002", note="x", db_path=shop)


# --- Reference data -------------------------------------------------


def test_deactivate_category_cascades_to_subcategories(shop: Path):
    deactivate_category("Food", db_path=shop)

    active = {category.name for category in list_categories(db_path=shop)}
    assert "Food" not in active

    assert list_subcategories(category="Food", db_path=shop) == []
    assert (
        len(list_subcategories(category="Food", include_inactive=True, db_path=shop))
        == 2
    )


def test_deactivated_category_keeps_historical_transactions(shop: Path):
    """Soft delete exists precisely so past rows keep their category name."""
    deactivate_category("Food", db_path=shop)

    page = list_transactions(category="Food", db_path=shop)
    assert page.total == 3
    assert page.transactions[0].category == "Food"


def test_deactivated_category_cannot_receive_new_transactions(shop: Path):
    deactivate_category("Food", db_path=shop)

    with pytest.raises(UnknownCategoryError):
        record_transaction(
            amount="1.00", category="Food", account=ACCOUNT, db_path=shop
        )


def test_deactivate_subcategory_leaves_its_parent_alone(shop: Path):
    deactivate_subcategory("Food", "Dining", db_path=shop)

    remaining = [s.name for s in list_subcategories(category="Food", db_path=shop)]
    assert remaining == ["Groceries"]
    assert "Food" in {c.name for c in list_categories(db_path=shop)}


def test_rename_category_carries_history_with_it(shop: Path):
    """
    The reason rename exists at all.

    Transactions reference a category by id, so a rename fixes the label
    across every past transaction at once. Deactivating and recreating
    would split the history permanently.
    """
    before = list_transactions(category="Food", db_path=shop)
    assert before.total == 3

    renamed = update_category("Food", new_name="Groceries & Food", db_path=shop)
    assert renamed.name == "Groceries & Food"

    after = list_transactions(category="Groceries & Food", db_path=shop)
    assert after.total == 3
    assert list_transactions(category="Food", db_path=shop).total == 0

    # The summary follows too, rather than reporting two categories.
    period = summarize(date(2026, 3, 1), date(2026, 3, 31), db_path=shop)
    names = {entry.category for entry in period.by_category}
    assert "Groceries & Food" in names
    assert "Food" not in names


def test_rename_category_keeps_its_design_code(shop: Path):
    """The code identifies the record, not its label."""
    before = {c.name: c.code for c in list_categories(db_path=shop)}

    renamed = update_category("Food", new_name="Meals", db_path=shop)

    assert renamed.code == before["Food"]


def test_rename_category_allows_a_case_only_change(shop: Path):
    """
    'fitness' -> 'Fitness' must work.

    categories.name is COLLATE NOCASE UNIQUE, so this looks like a
    collision unless the row is allowed to match itself. The Money
    Manager import had to fix exactly this kind of casing.
    """
    add_category("fitness", db_path=shop)

    renamed = update_category("fitness", new_name="Fitness", db_path=shop)

    assert renamed.name == "Fitness"
    assert len([c for c in list_categories(db_path=shop) if c.name == "Fitness"]) == 1


def test_rename_category_onto_another_is_rejected(shop: Path):
    with pytest.raises(FinanceError, match="already named"):
        update_category("Food", new_name="Transport", db_path=shop)

    # Rejected case-insensitively too, matching the schema's constraint.
    with pytest.raises(FinanceError, match="already named"):
        update_category("Food", new_name="transport", db_path=shop)


def test_rename_category_rejects_an_empty_name(shop: Path):
    with pytest.raises(FinanceError, match="cannot be empty"):
        update_category("Food", new_name="   ", db_path=shop)


def test_update_category_changes_and_clears_the_emoji(shop: Path):
    changed = update_category("Food", emoji="🥗", db_path=shop)
    assert changed.emoji == "🥗"
    assert changed.name == "Food"

    cleared = update_category("Food", emoji=None, db_path=shop)
    assert cleared.emoji is None


def test_update_category_leaves_the_emoji_alone_when_omitted(shop: Path):
    renamed = update_category("Food", new_name="Meals", db_path=shop)

    assert renamed.emoji == "🍜"


def test_update_category_can_edit_an_inactive_category(shop: Path):
    """Correcting a name before bringing a category back must be possible."""
    deactivate_category("Food", db_path=shop)

    renamed = update_category("Food", new_name="Old Food", db_path=shop)

    assert renamed.name == "Old Food"
    assert renamed.is_active is False


def test_update_category_reports_an_unknown_name(shop: Path):
    with pytest.raises(UnknownCategoryError, match="Available"):
        update_category("Ghost", new_name="Spirit", db_path=shop)


def test_rename_subcategory(shop: Path):
    renamed = update_subcategory("Food", "Dining", "Eating out", db_path=shop)

    assert renamed.name == "Eating out"
    assert renamed.category_name == "Food"

    names = {s.name for s in list_subcategories(category="Food", db_path=shop)}
    assert names == {"Groceries", "Eating out"}


def test_rename_subcategory_carries_history(shop: Path):
    update_subcategory("Food", "Groceries", "Supermarket", db_path=shop)

    period = summarize(date(2026, 3, 1), date(2026, 3, 31), db_path=shop)
    subs = {entry.subcategory for entry in period.by_subcategory}

    assert "Supermarket" in subs
    assert "Groceries" not in subs


def test_rename_subcategory_keeps_its_code(shop: Path):
    before = {
        s.name: s.code for s in list_subcategories(category="Food", db_path=shop)
    }

    renamed = update_subcategory("Food", "Dining", "Eating out", db_path=shop)

    assert renamed.code == before["Dining"]


def test_rename_subcategory_onto_a_sibling_is_rejected(shop: Path):
    with pytest.raises(FinanceError, match="already has a different subcategory"):
        update_subcategory("Food", "Dining", "Groceries", db_path=shop)


def test_same_subcategory_name_under_another_category_is_fine(shop: Path):
    """UNIQUE is (category_id, name), so siblings are what collide."""
    renamed = update_subcategory("Transport", "Taxi", "Groceries", db_path=shop)

    assert renamed.name == "Groceries"
    assert renamed.category_name == "Transport"


def test_rename_subcategory_rejects_an_empty_name(shop: Path):
    with pytest.raises(FinanceError, match="cannot be empty"):
        update_subcategory("Food", "Dining", "  ", db_path=shop)


def test_rename_subcategory_reports_an_unknown_name(shop: Path):
    with pytest.raises(UnknownSubcategoryError, match="Available"):
        update_subcategory("Food", "Ghost", "Spirit", db_path=shop)


def test_duplicate_reference_data_is_rejected(shop: Path):
    with pytest.raises(FinanceError, match="already exists"):
        add_category("Food", db_path=shop)

    with pytest.raises(FinanceError, match="already exists"):
        add_account(ACCOUNT, db_path=shop)

    with pytest.raises(FinanceError, match="already exists"):
        add_subcategory("Food", "Dining", db_path=shop)


# --- Months ---------------------------------------------------------


@pytest.mark.parametrize(
    "supplied",
    ["2026-08", "2026-08-01", "2026-08-17", date(2026, 8, 31)],
)
def test_normalize_month_accepts_several_forms(supplied):
    assert normalize_month(supplied) == "2026-08-01"


def test_normalize_month_rejects_nonsense():
    with pytest.raises(FinanceError, match="not a month"):
        normalize_month("August 2026")


# --- Budgets --------------------------------------------------------


def test_set_budget_creates_then_replaces(shop: Path):
    created = set_budget("2026-03", "Food", "100.00", db_path=shop)

    assert created.limit_minor == 10000
    assert created.month == "2026-03-01"
    assert created.code == "BGT-001"

    replaced = set_budget("2026-03", "Food", "150.00", db_path=shop)

    assert replaced.limit_minor == 15000
    # Same budget with a new number, so the code is preserved.
    assert replaced.code == "BGT-001"
    assert len(list_budgets("2026-03", db_path=shop)) == 1


def test_budget_rejects_a_non_positive_limit(shop: Path):
    with pytest.raises(FinanceError, match="greater than zero"):
        set_budget("2026-03", "Food", "0", db_path=shop)


def test_budget_progress_matches_the_summary(shop: Path):
    set_budget("2026-03", "Food", "100.00", db_path=shop)
    set_budget("2026-03", "Transport", "5.00", db_path=shop)

    progress = {item.budget.category: item for item in budget_progress(
        "2026-03", db_path=shop
    )}

    # Food: 10.00 + 25.00 + 40.00 = 75.00 of a 100.00 budget.
    food = progress["Food"]
    assert food.spent_minor == 7500
    assert food.remaining_minor == 2500
    assert food.percent_used == Decimal("75.0")
    assert food.is_over is False

    # Transport: 7.50 against a 5.00 budget.
    transport = progress["Transport"]
    assert transport.spent_minor == 750
    assert transport.remaining_minor == -250
    assert transport.is_over is True


def test_budget_progress_ignores_other_months(shop: Path):
    set_budget("2026-04", "Food", "100.00", db_path=shop)

    progress = budget_progress("2026-04", db_path=shop)

    assert len(progress) == 1
    assert progress[0].spent_minor == 0


def test_delete_budget_does_not_recycle_its_code(shop: Path):
    set_budget("2026-03", "Food", "100.00", db_path=shop)
    removed = delete_budget("BGT-001", db_path=shop)

    assert removed.category == "Food"
    assert list_budgets("2026-03", db_path=shop) == []

    # Budgets have no soft-delete column, so this is the case the
    # persistent code counter exists for.
    recreated = set_budget("2026-03", "Food", "50.00", db_path=shop)
    assert recreated.code == "BGT-002"


def test_delete_budget_rejects_a_missing_code(shop: Path):
    with pytest.raises(FinanceError, match="No budget"):
        delete_budget("BGT-404", db_path=shop)


# --- Goals ----------------------------------------------------------


def test_set_goal_creates_and_updates(shop: Path):
    created = set_goal(
        "2026-03",
        target_income="3000.00",
        target_savings="800.00",
        notes="Save for a trip",
        db_path=shop,
    )

    assert created.code == "GOL-001"
    assert created.target_income_minor == 300000
    assert created.target_savings_minor == 80000
    assert created.notes == "Save for a trip"

    updated = set_goal("2026-03", target_savings="900.00", db_path=shop)

    assert updated.code == "GOL-001"
    assert updated.target_savings_minor == 90000
    # Untouched fields survive the update.
    assert updated.target_income_minor == 300000
    assert updated.notes == "Save for a trip"


def test_goal_zero_is_not_the_same_as_unset(shop: Path):
    goal = set_goal("2026-03", target_savings="0", db_path=shop)

    assert goal.target_savings_minor == 0
    assert goal.target_income_minor is None


def test_goal_rejects_a_negative_target(shop: Path):
    with pytest.raises(FinanceError, match="cannot be negative"):
        set_goal("2026-03", target_income="-5", db_path=shop)


def test_get_goal_returns_none_when_unset(shop: Path):
    assert get_goal("2026-05", db_path=shop) is None
