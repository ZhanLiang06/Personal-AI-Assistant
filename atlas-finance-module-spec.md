# Atlas Finance Module — Implementation Plan & Specification

## 0. Scope

Extends Atlas (FastAPI + SQLModel/SQLite + Cloudflare Pages dashboard + LangChain ReAct agent) with a finance tracker: agent-recorded transactions, budgets/goals, weekly/monthly analysis reports, and an editable web dashboard. Designed to stay round-trip compatible with the existing iOS Money Manager CSV export/import.

---

## 1. Data Model

### 1.1 Category / Subcategory

Normalized lookup tables. Categories/subcategories can be added and renamed, never hard-deleted (soft-delete via `is_active`) — preserves referential integrity for historical transactions and matches the product requirement ("can add, can't delete, can update").

```python
class Category(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    name: str                    # "Food"
    emoji: str | None            # "🍜"
    is_active: bool = True
    created_at: datetime

class Subcategory(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    category_id: int = Field(foreign_key="category.id")
    name: str                    # "Dinner"
    is_active: bool = True
    created_at: datetime

    __table_args__ = (UniqueConstraint("category_id", "name"),)
```

Endpoints expose `PATCH` (rename/re-emoji) and `deactivate` — no `DELETE`. Deactivated categories drop out of "add transaction" pickers but remain valid FK targets for existing rows.

### 1.2 Exchange Rate Setting

Current *policy* per currency, not a history table. One row per currency.

```python
class ExchangeRateSetting(SQLModel, table=True):
    currency: str = Field(primary_key=True)   # "CNY"
    mode: str                                  # "auto" | "manual"
    manual_rate: float | None                  # to MYR; used only if mode == "manual"
    updated_at: datetime
```

Rate *history* lives implicitly on each transaction via `fx_rate_used` (below) — no separate log table unless a "rate over time" report is explicitly wanted later.

### 1.3 Transaction

```python
class Transaction(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    date: date
    account: str                     # "Bank Accounts"
    category_id: int = Field(foreign_key="category.id")
    subcategory_id: int | None = Field(foreign_key="subcategory.id")
    note: str | None
    description: str | None
    direction: str                   # "Exp." | "Inc." — matches app vocabulary
    amount: float                    # original transaction currency
    currency: str                    # "CNY"
    fx_rate_used: float              # locked in at record time
    base_amount: float               # amount * fx_rate_used, always MYR
    base_currency: str = "MYR"
    source: str = "manual"           # manual | agent | import
    created_at: datetime
```

`base_amount` is always computed server-side, never agent-supplied directly (arithmetic-hallucination risk). Dashboard always displays `base_amount` / `base_currency`.

### 1.4 Budget / Goal

```python
class Budget(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    month: date                      # first-of-month convention
    category_id: int = Field(foreign_key="category.id")
    limit_amount: float              # MYR

    __table_args__ = (UniqueConstraint("month", "category_id"),)

class Goal(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    month: date
    target_income: float | None
    target_savings: float | None
    notes: str | None
```

---

## 2. FX Resolution

```python
def resolve_fx_rate(currency: str, txn_date: date) -> float:
    if currency == "MYR":
        return 1.0
    setting = get_exchange_rate_setting(currency)
    if setting.mode == "manual":
        return setting.manual_rate
    return fetch_historical_rate(currency, "MYR", txn_date)  # e.g. Frankfurter / exchangerate.host
```

- Called once at `record_transaction` time; result stored in `fx_rate_used` and never recalculated retroactively.
- Auto mode always passes `txn_date` (not "today") so backfilled entries get the correct historical rate.
- No MCP here — this is internal backend logic, not a cross-context tool. A plain HTTP call wrapped in a Python function is sufficient.

---

## 3. Agent Tools (finance domain)

Added to the existing LangChain tool list, following current conventions (index-based addressing + `expected_text` cross-check for mutations, forbidden-prefix validate-then-mutate, conversational HITL confirmation before destructive/bulk actions):

| Tool | Behavior |
|---|---|
| `record_transaction` | Validates category/subcategory exist and are active, resolves FX rate, computes `base_amount`, inserts row. |
| `edit_transaction` | Index-based addressing + `expected_text` cross-check (same pattern as `manage_daily_todos`). |
| `delete_transaction` | Conversational confirmation before delete. |
| `set_budget` | Confirms before overwriting an existing month/category budget. |
| `set_goal` | Confirms before overwriting an existing month's goal. |
| `get_summary` | Read-only aggregation (day/week/month, grouped by category). **Pure SQL, no LLM arithmetic.** |
| `add_category` / `add_subcategory` | Insert only — no delete tool exposed to the agent. |

`get_summary` output is handed to the LLM only for *narration* of already-computed numbers, never for computing them.

---

## 4. API Endpoints (dashboard-facing, FastAPI)

Dashboard talks to these directly — it does not go through the agent for simple CRUD, only the agent's natural-language entry point does.

```
GET    /transactions?from=&to=&category=&account=
POST   /transactions
PATCH  /transactions/{id}
DELETE /transactions/{id}

GET    /categories
POST   /categories
PATCH  /categories/{id}
POST   /categories/{id}/deactivate

GET    /subcategories?category_id=
POST   /subcategories
PATCH  /subcategories/{id}
POST   /subcategories/{id}/deactivate

GET    /budgets?month=
PUT    /budgets/{month}/{category_id}
GET    /goals?month=
PUT    /goals/{month}

GET    /summary?period=week|month&group_by=category
GET    /exchange-rate-settings
PUT    /exchange-rate-settings/{currency}

GET    /export/money-manager-csv?from=&to=
POST   /import/money-manager-csv
```

`/summary` is reused by both the dashboard widgets and the scheduled report generator — one aggregation implementation, two consumers.

---

## 5. Dashboard (Cloudflare Pages)

- **Transactions table** — inline edit/delete, filter by date range/category/account, always displays `base_amount` (MYR).
- **Quick-add form** — mirrors `record_transaction` fields; category/subcategory pickers sourced from `is_active=true` rows only.
- **Budget & goal editor** — one row per category per month; monthly income/savings target form.
- **Summary widgets** — pull from `GET /summary`; spend-by-category, budget-vs-actual, month-over-month trend.
- **Exchange rate settings panel** — per-currency auto/manual toggle + manual rate input.

---

## 6. Reporting

- Weekly and monthly jobs (FastAPI background task on a schedule, or a simple cron script) call `GET /summary` for the relevant period.
- Result is handed to the LLM as a narration-only prompt — pre-computed numbers in, prose report out. No agent tool-calling loop needed for this step.
- Delivery: dashboard notification and/or next-chat-turn summary for now; Telegram push once that integration exists (per existing roadmap).

---

## 7. Money Manager Import/Export Compatibility

Source export columns: `Period, Accounts, Category, Subcategory, Note, MYR, Income/Expense, Description, Amount, Currency, Accounts(dup)`.

- **Import**: map `Amount`/`Currency` → `transaction.amount`/`.currency`; `MYR` → `base_amount` (also derive `fx_rate_used = MYR / Amount` for historical rows); `Category`/`Subcategory` → look up or create (with emoji parsed out of `Category` string); tag `source="import"`.
- **Export**: reconstruct the exact original column order/headers, including the duplicate trailing `Accounts` column (mirrors `base_amount`), so the file re-imports cleanly into Money Manager.
- Keep this mapping isolated in one module (e.g. `src/finance/money_manager_io.py`) — internal schema stays clean, all app-specific quirks live at this one boundary.

---

## 8. System Prompt Strategy (deferred decision)

Not resolved yet — flagged here so it isn't lost:

- Option considered: per-turn domain classification (finance / calendar / todo / notes) that dynamically assembles only the relevant system-prompt section + tool subset for that turn. Keeps single ReAct loop (not multi-agent), avoids paying context cost for irrelevant domain rules.
- Decision: **defer.** Add finance rules to the shared prompt first, observe actual length/behavior, and only build the dynamic-assembly layer if the prompt is demonstrably too long. If built later, the classifier doubles as the router node for the already-planned Phase 4 LangGraph supervisor — no wasted work.

---

## 9. Build Order

1. **Schema & migration** — create tables; write `money_manager_io.py`; import existing export as seed data (`source="import"`); seed `Category`/`Subcategory` from real historical categories.
2. **`record_transaction` tool** — wire into agent; test FX resolution (both manual and auto mode) against real entries. This alone makes daily use possible.
3. **`get_summary` endpoint** — build and validate aggregation queries against imported historical data before any UI depends on it.
4. **Dashboard CRUD** — transactions table + quick-add form + category/budget/goal editors, all calling the FastAPI endpoints directly.
5. **`edit_transaction` / `delete_transaction` / `set_budget` / `set_goal` tools** — agent-side mutation with confirmation flows.
6. **Scheduled reporting** — weekly/monthly job calling `/summary`, narrated by LLM, delivered to dashboard/chat.
7. **Export path** — verify round-trip: import → edit via dashboard/agent → export → re-import into Money Manager cleanly.

Each step reuses infrastructure from the previous one rather than building parallel paths (dashboard and reports share `/summary`; agent and dashboard share the same underlying tables and validation).

---

## 10. Open Questions to Confirm Before Starting

- Confirm the duplicate trailing `Accounts` column in the source export is actually required on re-import, or safely droppable.
- Confirm FX API choice (e.g. Frankfurter — free, supports historical-by-date) and rate-limit handling for `auto` mode.
- Confirm whether `Account` (e.g. "Bank Accounts") needs its own lookup table now or can stay free-text until multi-account tracking is actually needed.
