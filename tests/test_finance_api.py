"""
Tests for the finance HTTP endpoints.

The client is bound to a throwaway database by the `api_client` fixture,
so nothing here can reach real financial data.
"""

from __future__ import annotations

import pytest


API = "/api/finance"


@pytest.fixture
def stocked(api_client):
    """Reference data plus a few March transactions, created over HTTP."""
    api_client.post(f"{API}/accounts", json={"name": "Bank Accounts"})
    api_client.post(f"{API}/categories", json={"name": "Food", "emoji": "🍜"})
    api_client.post(f"{API}/categories", json={"name": "Transport", "emoji": "🚌"})
    api_client.post(
        f"{API}/subcategories", json={"category": "Food", "name": "Groceries"}
    )

    for day, amount, category in (
        (2, "10.00", "Food"),
        (5, "25.50", "Food"),
        (8, "7.25", "Transport"),
    ):
        response = api_client.post(
            f"{API}/transactions",
            json={
                "amount": amount,
                "category": category,
                "occurred_at": f"2026-03-{day:02d}T12:00:00",
            },
        )
        assert response.status_code == 201, response.text

    return api_client


# --- Reference data -------------------------------------------------


def test_create_and_list_categories(api_client):
    created = api_client.post(
        f"{API}/categories", json={"name": "Food", "emoji": "🍜"}
    )

    assert created.status_code == 201
    assert created.json()["code"] == "CAT-001"
    assert created.json()["emoji"] == "🍜"

    listed = api_client.get(f"{API}/categories")
    assert [item["name"] for item in listed.json()] == ["Food"]


def test_duplicate_category_is_a_400(api_client):
    api_client.post(f"{API}/categories", json={"name": "Food"})
    again = api_client.post(f"{API}/categories", json={"name": "Food"})

    assert again.status_code == 400
    assert "already exists" in again.json()["detail"]


def test_deactivating_a_category_hides_it_from_the_list(stocked):
    removed = stocked.delete(f"{API}/categories/Food")

    assert removed.status_code == 200
    assert removed.json()["is_active"] is False

    names = [item["name"] for item in stocked.get(f"{API}/categories").json()]
    assert names == ["Transport"]

    # But its transactions survive with their category intact.
    page = stocked.get(f"{API}/transactions", params={"category": "Food"}).json()
    assert page["total"] == 2


def test_patch_category_renames_and_keeps_history(stocked):
    renamed = stocked.patch(
        f"{API}/categories/Food", json={"new_name": "Meals"}
    )

    assert renamed.status_code == 200
    assert renamed.json()["name"] == "Meals"
    assert renamed.json()["code"] == "CAT-001"

    # The transactions came with it rather than being orphaned.
    assert stocked.get(f"{API}/transactions", params={"category": "Meals"}).json()[
        "total"
    ] == 2
    summary = stocked.get(f"{API}/summary", params={"month": "2026-03"}).json()
    assert "Meals" in {item["category"] for item in summary["by_category"]}


def test_patch_category_changes_emoji_independently(stocked):
    changed = stocked.patch(f"{API}/categories/Food", json={"emoji": "🥗"}).json()

    assert changed["emoji"] == "🥗"
    assert changed["name"] == "Food"


def test_patch_category_distinguishes_absent_from_null_emoji(stocked):
    renamed = stocked.patch(f"{API}/categories/Food", json={"new_name": "Meals"})
    assert renamed.json()["emoji"] == "🍜"

    cleared = stocked.patch(f"{API}/categories/Meals", json={"emoji": None})
    assert cleared.json()["emoji"] is None


def test_patch_category_allows_a_case_only_rename(stocked):
    stocked.post(f"{API}/categories", json={"name": "fitness"})

    renamed = stocked.patch(
        f"{API}/categories/fitness", json={"new_name": "Fitness"}
    )

    assert renamed.status_code == 200
    assert renamed.json()["name"] == "Fitness"


def test_patch_category_onto_another_is_a_400(stocked):
    response = stocked.patch(
        f"{API}/categories/Food", json={"new_name": "Transport"}
    )

    assert response.status_code == 400
    assert "cannot be merged automatically" in response.json()["detail"]


def test_patch_unknown_category_is_a_404(stocked):
    response = stocked.patch(f"{API}/categories/Ghost", json={"new_name": "X"})

    assert response.status_code == 404
    assert "Available" in response.json()["detail"]


def test_patch_category_rejects_an_empty_name(stocked):
    response = stocked.patch(f"{API}/categories/Food", json={"new_name": ""})

    assert response.status_code == 422


def test_patch_subcategory_renames(stocked):
    renamed = stocked.patch(
        f"{API}/subcategories/Food/Groceries", json={"new_name": "Supermarket"}
    )

    assert renamed.status_code == 200
    assert renamed.json()["name"] == "Supermarket"
    assert renamed.json()["category"] == "Food"

    listed = stocked.get(f"{API}/subcategories", params={"category": "Food"}).json()
    assert [item["name"] for item in listed] == ["Supermarket"]


def test_patch_subcategory_onto_a_sibling_is_a_400(stocked):
    stocked.post(f"{API}/subcategories", json={"category": "Food", "name": "Dining"})

    response = stocked.patch(
        f"{API}/subcategories/Food/Groceries", json={"new_name": "Dining"}
    )

    assert response.status_code == 400


def test_patch_unknown_subcategory_is_a_404(stocked):
    response = stocked.patch(
        f"{API}/subcategories/Food/Ghost", json={"new_name": "X"}
    )

    assert response.status_code == 404


def test_subcategory_under_unknown_category_is_a_404(api_client):
    response = api_client.post(
        f"{API}/subcategories", json={"category": "Ghost", "name": "Thing"}
    )

    assert response.status_code == 404
    assert "Available" in response.json()["detail"]


# --- Transactions ---------------------------------------------------


def test_create_transaction_returns_a_code_and_base_amount(stocked):
    response = stocked.post(
        f"{API}/transactions",
        json={
            "amount": "12.34",
            "category": "Food",
            "subcategory": "Groceries",
            "note": "Lunch",
            "occurred_at": "2026-03-10T13:00:00",
        },
    )

    assert response.status_code == 201
    body = response.json()

    assert body["code"] == "TXN-000004"
    assert body["base_amount"]["minor"] == 1234
    assert body["base_amount"]["display"] == "12.34"
    assert body["base_amount"]["currency"] == "MYR"
    assert body["subcategory"] == "Groceries"
    assert body["source"] == "manual"


def test_money_never_crosses_the_wire_as_a_json_number(stocked):
    """
    A JSON number is an IEEE double by the time a browser sees it.

    Minor units are the authority; the decimal form is a string so no
    client can parse it into a float by accident.
    """
    body = stocked.get(f"{API}/transactions").json()["transactions"][0]

    assert isinstance(body["amount"]["minor"], int)
    assert isinstance(body["amount"]["decimal"], str)
    assert isinstance(body["amount"]["display"], str)
    assert isinstance(body["fx_rate"], str)


def test_create_rejects_a_float_amount(stocked):
    """The pattern blocks it at the edge, before money.py has to."""
    response = stocked.post(
        f"{API}/transactions", json={"amount": 12.34, "category": "Food"}
    )

    assert response.status_code == 422


def test_create_rejects_an_unknown_category(stocked):
    response = stocked.post(
        f"{API}/transactions", json={"amount": "5.00", "category": "Ghost"}
    )

    assert response.status_code == 404
    assert "Available" in response.json()["detail"]


def test_create_rejects_a_bad_direction(stocked):
    response = stocked.post(
        f"{API}/transactions",
        json={"amount": "5.00", "category": "Food", "direction": "refund"},
    )

    assert response.status_code == 400


def test_create_rejects_a_malformed_timestamp(stocked):
    response = stocked.post(
        f"{API}/transactions",
        json={
            "amount": "5.00",
            "category": "Food",
            "occurred_at": "last Tuesday",
        },
    )

    assert response.status_code == 400
    assert "ISO 8601" in response.json()["detail"]


def test_list_filters_and_paginates(stocked):
    page = stocked.get(
        f"{API}/transactions", params={"category": "Food", "limit": 1}
    ).json()

    assert page["total"] == 2
    assert len(page["transactions"]) == 1
    assert page["has_more"] is True


def test_list_rejects_an_out_of_range_limit(stocked):
    assert stocked.get(f"{API}/transactions", params={"limit": 0}).status_code == 422
    assert (
        stocked.get(f"{API}/transactions", params={"limit": 9999}).status_code == 422
    )


def test_get_one_transaction_by_code(stocked):
    found = stocked.get(f"{API}/transactions/TXN-000001")

    assert found.status_code == 200
    assert found.json()["code"] == "TXN-000001"


def test_get_one_transaction_rejects_a_wrong_prefix(stocked):
    response = stocked.get(f"{API}/transactions/CAT-001")

    assert response.status_code == 422
    assert "categories record" in response.json()["detail"]


def test_missing_transaction_is_a_404(stocked):
    assert stocked.get(f"{API}/transactions/TXN-999999").status_code == 404


def test_patch_leaves_omitted_fields_alone(stocked):
    before = stocked.get(f"{API}/transactions/TXN-000001").json()

    after = stocked.patch(
        f"{API}/transactions/TXN-000001", json={"note": "Corrected"}
    ).json()

    assert after["note"] == "Corrected"
    assert after["amount"]["minor"] == before["amount"]["minor"]
    assert after["category"] == before["category"]
    assert after["occurred_at"] == before["occurred_at"]


def test_patch_distinguishes_absent_from_explicit_null(stocked):
    """
    The reason the route reads model_fields_set instead of attributes.

    Omitting `note` must keep it; sending `"note": null` must clear it.
    """
    stocked.patch(f"{API}/transactions/TXN-000001", json={"note": "keep me"})

    untouched = stocked.patch(
        f"{API}/transactions/TXN-000001", json={"description": "d"}
    ).json()
    assert untouched["note"] == "keep me"

    cleared = stocked.patch(
        f"{API}/transactions/TXN-000001", json={"note": None}
    ).json()
    assert cleared["note"] is None


def test_delete_is_soft_and_returns_what_went(stocked):
    removed = stocked.delete(f"{API}/transactions/TXN-000002")

    assert removed.status_code == 200
    assert removed.json()["amount"]["minor"] == 2550

    assert stocked.get(f"{API}/transactions/TXN-000002").status_code == 404
    assert stocked.get(f"{API}/transactions").json()["total"] == 2


def test_deleting_twice_is_a_400(stocked):
    stocked.delete(f"{API}/transactions/TXN-000002")
    again = stocked.delete(f"{API}/transactions/TXN-000002")

    assert again.status_code == 400


# --- Summary --------------------------------------------------------


def test_summary_for_a_month(stocked):
    body = stocked.get(f"{API}/summary", params={"month": "2026-03"}).json()

    assert body["period_start"] == "2026-03-01"
    assert body["period_end"] == "2026-03-31"
    assert body["total_expense"]["minor"] == 4275
    assert body["total_expense"]["display"] == "42.75"
    assert body["transaction_count"] == 3
    assert len(body["daily_totals"]) == 31

    categories = {item["category"]: item for item in body["by_category"]}
    assert categories["Food"]["expense"]["minor"] == 3550
    assert categories["Food"]["transaction_count"] == 2


def test_summary_for_an_explicit_range(stocked):
    body = stocked.get(
        f"{API}/summary", params={"start": "2026-03-05", "end": "2026-03-08"}
    ).json()

    assert body["transaction_count"] == 2
    assert body["total_expense"]["minor"] == 3275


def test_summary_rejects_a_backwards_range(stocked):
    response = stocked.get(
        f"{API}/summary", params={"start": "2026-03-08", "end": "2026-03-05"}
    )

    assert response.status_code == 400


def test_summary_rejects_a_nonsense_month(stocked):
    response = stocked.get(f"{API}/summary", params={"month": "March"})

    assert response.status_code == 400


def test_summary_and_listing_cover_the_same_rows(stocked):
    """A widget and the table beneath it must never disagree."""
    summary = stocked.get(f"{API}/summary", params={"month": "2026-03"}).json()
    listing = stocked.get(
        f"{API}/transactions",
        params={"start": "2026-03-01", "end": "2026-03-31", "limit": 500},
    ).json()

    assert summary["transaction_count"] == listing["total"]

    listed_total = sum(
        item["base_amount"]["minor"]
        for item in listing["transactions"]
        if item["direction"] == "expense"
    )
    assert listed_total == summary["total_expense"]["minor"]


# --- Budgets and goals ----------------------------------------------


def test_put_budget_then_read_progress(stocked):
    created = stocked.put(
        f"{API}/budgets",
        json={"month": "2026-03", "category": "Food", "limit": "50.00"},
    )

    assert created.status_code == 200
    body = created.json()
    assert body["code"] == "BGT-001"
    assert body["limit"]["minor"] == 5000
    assert body["spent"]["minor"] == 3550
    assert body["remaining"]["minor"] == 1450
    assert body["percent_used"] == "71.0"
    assert body["is_over"] is False


def test_budget_reports_being_over(stocked):
    over = stocked.put(
        f"{API}/budgets",
        json={"month": "2026-03", "category": "Transport", "limit": "5.00"},
    ).json()

    assert over["is_over"] is True
    assert over["remaining"]["minor"] == -225


def test_put_budget_replaces_rather_than_duplicating(stocked):
    stocked.put(
        f"{API}/budgets",
        json={"month": "2026-03", "category": "Food", "limit": "50.00"},
    )
    stocked.put(
        f"{API}/budgets",
        json={"month": "2026-03", "category": "Food", "limit": "80.00"},
    )

    budgets = stocked.get(f"{API}/budgets", params={"month": "2026-03"}).json()
    assert len(budgets) == 1
    assert budgets[0]["limit"]["minor"] == 8000
    assert budgets[0]["code"] == "BGT-001"


def test_budget_rejects_a_zero_limit(stocked):
    response = stocked.put(
        f"{API}/budgets",
        json={"month": "2026-03", "category": "Food", "limit": "0"},
    )

    assert response.status_code == 400


def test_delete_budget(stocked):
    stocked.put(
        f"{API}/budgets",
        json={"month": "2026-03", "category": "Food", "limit": "50.00"},
    )

    removed = stocked.delete(f"{API}/budgets/BGT-001")
    assert removed.status_code == 200
    assert stocked.get(f"{API}/budgets", params={"month": "2026-03"}).json() == []


def test_goal_round_trip(stocked):
    assert stocked.get(f"{API}/goals", params={"month": "2026-03"}).json() is None

    created = stocked.put(
        f"{API}/goals",
        json={
            "month": "2026-03",
            "target_income": "3000.00",
            "target_savings": "500.00",
            "notes": "Trip fund",
        },
    ).json()

    assert created["code"] == "GOL-001"
    assert created["target_income"]["minor"] == 300000
    assert created["notes"] == "Trip fund"

    updated = stocked.put(
        f"{API}/goals", json={"month": "2026-03", "target_savings": "600.00"}
    ).json()

    assert updated["target_savings"]["minor"] == 60000
    assert updated["target_income"]["minor"] == 300000
    assert updated["notes"] == "Trip fund"


# --- Exchange rates -------------------------------------------------


def test_exchange_rate_setting_round_trip(api_client):
    assert api_client.get(f"{API}/exchange-rate-settings").json() == []

    stored = api_client.put(
        f"{API}/exchange-rate-settings/CNY",
        json={"mode": "manual", "manual_rate": "0.6"},
    )

    assert stored.status_code == 200
    assert stored.json()["mode"] == "manual"
    assert stored.json()["currency"] == "CNY"

    assert len(api_client.get(f"{API}/exchange-rate-settings").json()) == 1


def test_manual_mode_without_a_rate_is_a_400(api_client):
    response = api_client.put(
        f"{API}/exchange-rate-settings/CNY", json={"mode": "manual"}
    )

    assert response.status_code == 400


def test_base_currency_cannot_have_a_policy(api_client):
    response = api_client.put(
        f"{API}/exchange-rate-settings/MYR", json={"mode": "auto"}
    )

    assert response.status_code == 400
    assert "base currency" in response.json()["detail"]


def test_foreign_currency_uses_the_manual_rate(stocked):
    stocked.put(
        f"{API}/exchange-rate-settings/CNY",
        json={"mode": "manual", "manual_rate": "0.6"},
    )

    created = stocked.post(
        f"{API}/transactions",
        json={
            "amount": "100.00",
            "currency": "CNY",
            "category": "Food",
            "occurred_at": "2026-03-12T12:00:00",
        },
    ).json()

    assert created["amount"]["minor"] == 10000
    assert created["amount"]["currency"] == "CNY"
    assert created["base_amount"]["minor"] == 6000
    assert created["base_amount"]["currency"] == "MYR"


# --- Overview -------------------------------------------------------


def test_overview_bundles_the_dashboard_in_one_call(stocked):
    stocked.put(
        f"{API}/budgets",
        json={"month": "2026-03", "category": "Food", "limit": "50.00"},
    )
    stocked.put(
        f"{API}/goals", json={"month": "2026-03", "target_savings": "500.00"}
    )

    body = stocked.get(f"{API}/overview", params={"month": "2026-03"}).json()

    assert body["month"] == "2026-03"
    assert body["summary"]["total_expense"]["minor"] == 4275
    assert body["comparison"]["previous_start"] == "2026-02-01"
    # February had no spending, so a percent change would be meaningless.
    assert body["comparison"]["expense_change_percent"] is None
    assert len(body["budgets"]) == 1
    assert body["goal"]["target_savings"]["minor"] == 50000
    assert {c["name"] for c in body["categories"]} == {"Food", "Transport"}
    assert len(body["recent"]) == 3


def test_overview_widgets_agree_with_each_other(stocked):
    """One snapshot, so the widgets cannot be built from different reads."""
    body = stocked.get(f"{API}/overview", params={"month": "2026-03"}).json()

    category_total = sum(
        item["expense"]["minor"] for item in body["summary"]["by_category"]
    )
    recent_total = sum(
        item["base_amount"]["minor"]
        for item in body["recent"]
        if item["direction"] == "expense"
    )

    assert category_total == body["summary"]["total_expense"]["minor"]
    assert recent_total == body["summary"]["total_expense"]["minor"]
