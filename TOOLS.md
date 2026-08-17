# Tool reference

Every tool the Kairos agent can call, grouped by domain. The list is registered in
[`src/llm/langchain_agent.py`](src/llm/langchain_agent.py); the rules governing *when*
the model is allowed to reach for each one live in
[`src/llm/prompts.py`](src/llm/prompts.py).

Two conventions run through all of them:

- **Read before write.** Mutating tools work from a fresh read — a todo index plus its
  expected text, a calendar `event_id` plus its expected title, a transaction's design
  code. If the read is stale, the write is refused rather than applied to the wrong row.
- **The model never computes money.** Finance tools return preformatted strings; there
  is no raw minor-unit integer for the model to add up.

| Group | Tools | Backed by |
| --- | --- | --- |
| [General](#general) | 1 | system clock |
| [Obsidian notes](#obsidian-notes) | 2 | ChromaDB + vault manifest |
| [Daily todos](#daily-todos) | 4 | markdown files in the vault |
| [Google Calendar](#google-calendar) | 4 | Google Calendar API |
| [Finance](#finance) | 15 | SQLite ledger via `src/finance/service.py` |

---

## General

### `get_current_time`
Returns the current date and time for a requested IANA timezone, defaulting to
`Asia/Kuala_Lumpur`.

*Used when* the runtime context line is missing or stale, when the user asks the time
outright, or when they ask for another location's time.

---

## Obsidian notes

### `list_vault_structure`
Lists the vault's canonical folders and standalone root files with their descriptions,
read from the folder manifest.

*Used when* the user asks how the vault is organized or where something is kept, or
before the first `search_notes` call in a request so the agent has real paths to scope
with. Capped at one call per request — the result is reused. It describes organization
only and is never treated as evidence about note content.

### `search_notes`
Semantic search over the embedded vault. Optionally restricted to an exact folder or
file scope returned by `list_vault_structure`.

*Used when* a question could plausibly depend on the user's own notes. Capped at three
calls per request, with near-synonymous retries discouraged. A folder scope searches
that folder and its declared descendants; a file scope searches only that file. If a
scoped search finds nothing, the agent says so rather than silently widening.

---

## Daily todos

Todos are checkbox lines in dated markdown files under `Journal/to-dos` in the vault.
There are no stable IDs, so mutations address items by **0-based index plus the exact
expected text** — and every mutation requires a fresh `list_daily_todos` for the same
date.

### `list_daily_todos`
Reads the todos for a target date. Read-only; it does not create the file.

*Used when* the user asks what is on the list, and as the mandatory precondition for
every add, update, or delete.

### `add_daily_todos`
Adds one or more unchecked todos to a date's file.

*Used when* the user asks to add something. The agent first compares against the fresh
list and asks before adding anything that looks like a reworded duplicate.

### `update_daily_todos`
Checks, unchecks, or edits existing todos — text and note.

*Used when* the user reports having done something. Matching is on meaning, not wording:
"I have gotten fruits from sunshine" checks off "get fruits from sunshine". Checking is
reversible, so it happens without asking. If several items could match, the agent asks
which.

### `delete_daily_todos`
Removes todos from a date's file.

*Used when* the user asks to drop an item. Like updates, it needs the index and expected
text from the latest fresh list; a mismatch forces a re-read instead of a guess.

---

## Google Calendar

Calendar is for scheduled events that matter, not minor daily tasks — those are todos.
Confirmation scales with blast radius: one or two writes go through, three or more need
explicit confirmation, and every deletion needs it.

### `list_google_calendar_events`
Lists upcoming events, returning the `event_id` and title that updates and deletes must
quote back.

*Used when* the user asks what is on the calendar, and always before an update or a
delete.

### `create_google_calendar_events`
Creates one or more events, tagged with a category — career, learning, personal,
finance, health, travel, or important.

*Used when* the user asks to schedule something. A date with no time becomes an all-day
event; a start with no end becomes one hour. Three or more events at once require
`confirmed_by_user=True`.

### `update_google_calendar_events`
Edits existing events, matched on `event_id` plus expected title.

*Used when* the user moves, reschedules, or renames something already on the calendar.
Three or more updates at once require confirmation.

### `delete_google_calendar_events`
Removes events, matched on `event_id` plus expected title.

*Used when* the user asks to cancel something — always with `confirmed_by_user=True`,
though the confirmation may arrive in the same message ("confirm delete X"). Raw event
IDs never appear in the reply.

---

## Finance

Fifteen tools over one ledger. Three constraints shape all of them: the agent does no
arithmetic, it addresses records by **design code** (`TXN-000339`) rather than list
position, and it cannot invent reference data — an unknown category comes back as an
error listing the valid ones.

### Transactions

#### `record_finance_transaction`
Records one expense or income. The amount is always positive and `direction` carries the
sign; the exchange rate and MYR equivalent are resolved server-side.

*Used when* the user reports spending or receiving money — including as the second half
of a message whose first half was a completed todo. Returns the design code to use from
then on.

#### `update_finance_transaction`
Edits a stored transaction by code. Omitted fields stay as they are; `-` clears a
subcategory, note, or description.

*Used when* the user corrects a record — "its under lunch". Changing the currency or
date resolves a new rate; changing only the amount keeps the rate it was booked at.

#### `delete_finance_transaction`
Soft-deletes a transaction by code. It stops counting towards every total but remains
recoverable.

*Used when* the user asks to remove a record — always after confirming.

#### `restore_finance_transaction`
Reverses a deletion by code, restoring the original amount and date.

*Used when* the user wants a deletion undone. The agent confirms and names which
transaction is coming back.

#### `list_deleted_finance_transactions`
Lists recently deleted transactions, most recent first.

*Used when* the user asks to undo a deletion without naming one — this is how the agent
finds the code.

#### `list_finance_transactions`
Lists recorded transactions with their design codes, newest first, defaulting to the
current month.

*Used when* the user asks what they recorded, and before any edit or delete so the code
acted on is current. Never used as raw material for a total.

### Reporting

#### `get_finance_summary`
Returns already-calculated totals for a period — expense, income, net, per-day, category
breakdown, comparison with the previous period.

*Used when* the user asks any "how much" question. The figures are final and are
reported exactly as returned.

#### `get_finance_budgets`
Shows each budget for a month next to the actual spend against it, precomputed.

*Used when* the user asks how a budget is tracking, and before overwriting one.

### Categories

#### `list_finance_categories`
Lists available categories and subcategories.

*Used when* the agent is unsure where a transaction belongs — a transaction can only be
filed under a category that already exists.

#### `add_finance_category`
Creates a new category, optionally with an emoji.

*Used when* nothing existing fits. The agent checks the list first: a near-duplicate
splits reporting permanently and cannot be merged later without editing every affected
transaction.

#### `add_finance_subcategory`
Creates a subcategory under an existing category.

*Used when* the user wants finer breakdown inside a category that already exists.

#### `update_finance_category`
Renames a category or changes its emoji, carrying the entire history with it.

*Used when* fixing a typo or wording. Deactivating a misnamed category and creating a
replacement is explicitly disallowed, and renaming onto an existing name is refused
rather than silently merging two categories.

#### `update_finance_subcategory`
Renames a subcategory within its category.

*Used when* fixing subcategory wording. Subcategories cannot move between categories —
that requires creating the new one and moving transactions across individually.

### Budgets and goals

#### `set_finance_budget`
Sets or replaces one category's spending limit for a month.

*Used when* the user sets a limit. Overwriting an existing budget is confirmed first,
after checking the current one.

#### `set_finance_goal`
Sets the income and savings targets for a month; omitted targets are left alone.

*Used when* the user sets monthly targets. Overwriting an existing goal is confirmed
first.
