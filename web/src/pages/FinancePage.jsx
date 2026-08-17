import { useCallback, useEffect, useMemo, useState } from "react";

import ConfirmDialog from "../components/ConfirmDialog.jsx";
import TransactionDialog from "../components/TransactionDialog.jsx";
import { useToast } from "../components/Toast.jsx";
import {
  createCategory,
  explainPeriod,
  getOverview,
  listTransactions,
  removeBudget,
  removeTransaction,
  restoreTransaction,
  setBudget,
  setGoal,
  updateCategory,
} from "../lib/api.js";
import {
  bare,
  dayNumber,
  display,
  displayMinor,
  longDay,
  minor,
  monthKey,
  monthLabel,
  monthProgress,
  ratio,
  shiftMonth,
  shortDate,
  timeOfDay,
} from "../lib/format.js";

/* ==========================================================================
   Small shared pieces
   ========================================================================== */

function Card({ title, note, action, children, tick, className = "" }) {
  return (
    <section
      className={`panel ${tick ? "panel--ticked" : ""} p-4 ${className}`}
      style={tick ? { "--tick": tick } : undefined}
    >
      {(title || action) && (
        <header className="mb-3 flex flex-wrap items-baseline justify-between gap-2">
          <div>
            <h2 className="label">{title}</h2>
            {note && (
              <p className="mt-1 text-[12px]" style={{ color: "var(--faint)" }}>
                {note}
              </p>
            )}
          </div>
          {action}
        </header>
      )}
      {children}
    </section>
  );
}

function Field({ label, children, wide }) {
  return (
    <label className={`flex flex-col gap-1 ${wide ? "sm:col-span-2" : ""}`}>
      <span className="label">{label}</span>
      {children}
    </label>
  );
}

// Controls take their metrics from .control in base.css rather than from inline
// styles, so they keep one height whichever skin is on.
const Input = ({ className = "", ...props }) => (
  <input {...props} className={`control ${className}`} />
);
const Select = ({ className = "", ...props }) => (
  <select {...props} className={`control ${className}`} />
);

/* ==========================================================================
   Month totals
   ========================================================================== */

function StatRow({ summary, comparison }) {
  const changePercent = comparison?.expense_change_percent;
  const delta = minor(comparison?.expense_delta);
  const deltaColor = delta > 0 ? "var(--pace-warn)" : delta < 0 ? "var(--pace-good)" : "var(--faint)";

  const stats = [
    {
      label: "expense",
      value: display(summary.total_expense),
      note:
        changePercent != null ? (
          <span style={{ color: deltaColor }}>
            {delta > 0 ? "▲" : delta < 0 ? "▼" : "="} {Math.abs(Number(changePercent)).toFixed(1)}% on
            last month
          </span>
        ) : null,
    },
    { label: "income", value: display(summary.total_income) },
    {
      label: "net",
      value: display(summary.net),
      note: (
        <span style={{ color: minor(summary.net) >= 0 ? "var(--pace-good)" : "var(--pace-over)" }}>
          {minor(summary.net) >= 0 ? "saved" : "overspent"}
        </span>
      ),
    },
    {
      label: "per day",
      value: display(summary.average_daily_expense),
      note: `${summary.transaction_count} transactions`,
    },
  ];

  return (
    <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
      {stats.map((stat) => (
        <Card key={stat.label} tick="var(--line-strong)">
          <p className="label">{stat.label}</p>
          <p className="display mt-1 text-[26px]">{stat.value}</p>
          {stat.note && (
            <p className="data mt-1 text-[10px]" style={{ color: "var(--faint)" }}>
              {stat.note}
            </p>
          )}
        </Card>
      ))}
    </div>
  );
}

/* ==========================================================================
   Daily spend
   ========================================================================== */

function DailyChart({ summary, month }) {
  const [hover, setHover] = useState(null);
  const days = summary.daily_totals;
  const peak = Math.max(1, ...days.map((day) => minor(day.expense)));
  const todayIndex = monthKey() === month ? new Date().getDate() - 1 : -1;

  const active = hover != null ? days[hover] : null;

  return (
    <Card
      title="daily spend"
      note="Every day of the month, including the quiet ones."
      action={
        <span className="data text-[12px]" style={{ color: active ? "var(--text)" : "var(--faint)" }}>
          {active ? `${shortDate(active.day)} · ${display(active.expense)}` : `peak ${displayMinor(peak, summary.base_currency)}`}
        </span>
      }
      tick="var(--accent)"
    >
      <div
        className="flex h-32 items-end gap-[2px]"
        onMouseLeave={() => setHover(null)}
        role="img"
        aria-label={`Daily spending for ${monthLabel(month)}`}
      >
        {days.map((day, index) => {
          const height = ratio(minor(day.expense), peak);
          const isToday = index === todayIndex;
          return (
            <button
              key={day.day}
              type="button"
              onMouseEnter={() => setHover(index)}
              onFocus={() => setHover(index)}
              className="group relative flex h-full flex-1 items-end"
              aria-label={`${shortDate(day.day)}: ${display(day.expense)}`}
            >
              <span
                className="w-full"
                style={{
                  height: `${Math.max(height * 100, minor(day.expense) > 0 ? 2 : 0)}%`,
                  background:
                    hover === index
                      ? "var(--accent)"
                      : isToday
                        ? "var(--pace-fast)"
                        : "var(--line-strong)",
                  transition: "background 120ms var(--ease)",
                }}
              />
            </button>
          );
        })}
      </div>

      <div className="mt-1.5 flex justify-between">
        {[0, Math.floor(days.length / 2), days.length - 1].map((index) => (
          <span key={index} className="data text-[9px]" style={{ color: "var(--faint)" }}>
            {days[index] ? dayNumber(days[index].day) : ""}
          </span>
        ))}
      </div>
    </Card>
  );
}

/* ==========================================================================
   Category classification
   ========================================================================== */

function Classification({ summary, filter, onFilter }) {
  const rows = summary.by_category.filter((row) => minor(row.expense) > 0);
  const leader = minor(rows[0]?.expense ?? { minor: 0 });

  if (rows.length === 0) {
    return (
      <Card title="by category" tick="var(--line-strong)">
        <p className="text-[13px]" style={{ color: "var(--faint)" }}>
          Nothing recorded this month yet.
        </p>
      </Card>
    );
  }

  return (
    <Card
      title="by category"
      note="Ranked by spend, with the gap to the biggest. Select a row to filter the table."
      tick="var(--pace-fast)"
    >
      <ol>
        {rows.map((row, index) => {
          const amount = minor(row.expense);
          const selected = filter === row.category;
          const gap = leader - amount;

          return (
            <li key={row.category}>
              <button
                type="button"
                onClick={() => onFilter(selected ? "" : row.category)}
                aria-pressed={selected}
                className="grid w-full items-center gap-3 py-2 text-left"
                style={{
                  gridTemplateColumns: "1.4rem minmax(6rem, 9rem) 1fr auto",
                  borderBottom: "1px solid var(--line)",
                  background: selected ? "var(--surface-2)" : "transparent",
                }}
              >
                <span className="data text-[11px]" style={{ color: "var(--faint)" }}>
                  {index + 1}
                </span>

                <span className="truncate text-[14px]">
                  {row.emoji ? `${row.emoji} ` : ""}
                  {row.category}
                </span>

                <span className="flex items-center gap-2">
                  <span
                    className="h-[6px] origin-left"
                    style={{
                      width: `${ratio(amount, leader) * 100}%`,
                      background: index === 0 ? "var(--pace-fast)" : "var(--accent)",
                      opacity: index === 0 ? 1 : 0.55,
                      animation: "sweep 320ms var(--ease) both",
                    }}
                    aria-hidden="true"
                  />
                  {index > 0 && (
                    <span className="data shrink-0 text-[10px]" style={{ color: "var(--faint)" }}>
                      −{displayMinor(gap, row.expense.currency)}
                    </span>
                  )}
                </span>

                <span className="data text-right text-[12px]">{bare(row.expense)}</span>
              </button>
            </li>
          );
        })}
      </ol>
    </Card>
  );
}

/* ==========================================================================
   Budgets
   ========================================================================== */

function Budgets({ budgets, categories, month, onChanged, onRemove }) {
  const [adding, setAdding] = useState(false);
  const [category, setCategory] = useState("");
  const [limit, setLimit] = useState("");
  const [error, setError] = useState(null);

  const through = monthProgress(month);

  async function save(event) {
    event.preventDefault();
    setError(null);
    try {
      await setBudget({ month, category, limit });
      setAdding(false);
      setCategory("");
      setLimit("");
      onChanged();
    } catch (failure) {
      setError(failure.message);
    }
  }

  return (
    <Card
      title="budgets"
      note="The tick marks how far through the month you are."
      tick="var(--pace-warn)"
      action={
        <button type="button" className="btn" onClick={() => setAdding((value) => !value)}>
          {adding ? "cancel" : "set a budget"}
        </button>
      }
    >
      {adding && (
        <form onSubmit={save} className="mb-4 grid gap-3 sm:grid-cols-2">
          <Field label="category">
            <Select value={category} onChange={(e) => setCategory(e.target.value)} required>
              <option value="">Choose one</option>
              {categories.map((item) => (
                <option key={item.name} value={item.name}>
                  {item.name}
                </option>
              ))}
            </Select>
          </Field>
          <Field label="monthly limit">
            <Input
              value={limit}
              onChange={(e) => setLimit(e.target.value)}
              inputMode="decimal"
              placeholder="200.00"
              required
            />
          </Field>
          <div className="sm:col-span-2">
            <button type="submit" className="btn btn--accent">
              save budget
            </button>
            {error && (
              <span className="data ml-3 text-[11px]" style={{ color: "var(--pace-over)" }}>
                {error}
              </span>
            )}
          </div>
        </form>
      )}

      {budgets.length === 0 ? (
        <p className="text-[13px]" style={{ color: "var(--faint)" }}>
          No budgets yet. Set one and this becomes a pace reading.
        </p>
      ) : (
        <ul className="space-y-3">
          {budgets.map((budget) => {
            const used = ratio(minor(budget.spent), minor(budget.limit));
            const color = budget.is_over
              ? "var(--pace-over)"
              : used > through
                ? "var(--pace-warn)"
                : "var(--pace-good)";

            return (
              <li key={budget.category}>
                <div className="flex items-baseline justify-between gap-2">
                  <span className="text-[14px]">
                    {budget.category_emoji ? `${budget.category_emoji} ` : ""}
                    {budget.category}
                  </span>
                  <span className="data text-[11px]" style={{ color }}>
                    {bare(budget.spent)} / {bare(budget.limit)}
                  </span>
                </div>

                <div className="relative mt-1.5 h-1.5" style={{ background: "var(--surface-2)" }}>
                  <div
                    className="absolute inset-y-0 left-0"
                    style={{ width: `${Math.min(used, 1) * 100}%`, background: color }}
                  />
                  <div
                    className="absolute inset-y-[-3px] w-px"
                    style={{ left: `${through * 100}%`, background: "var(--text)", opacity: 0.65 }}
                    aria-hidden="true"
                  />
                </div>

                <div className="mt-1 flex items-baseline justify-between">
                  <span className="data text-[10px]" style={{ color: "var(--faint)" }}>
                    {budget.is_over ? "over by" : "left"} {bare(budget.remaining).replace("-", "")}
                  </span>
                  <button
                    type="button"
                    className="data text-[10px]"
                    style={{ color: "var(--faint)" }}
                    onClick={() => onRemove(budget)}
                  >
                    remove
                  </button>
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </Card>
  );
}

/* ==========================================================================
   Goal
   ========================================================================== */

function Goal({ goal, summary, month, onChanged }) {
  const [income, setIncome] = useState("");
  const [savings, setSavings] = useState("");
  const [notes, setNotes] = useState("");
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    setIncome(goal?.target_income?.decimal ?? "");
    setSavings(goal?.target_savings?.decimal ?? "");
    setNotes(goal?.notes ?? "");
  }, [goal]);

  async function save(event) {
    event.preventDefault();
    await setGoal({
      month,
      target_income: income || null,
      target_savings: savings || null,
      notes: notes || null,
    }).catch(() => {});
    setSaved(true);
    setTimeout(() => setSaved(false), 2000);
    onChanged();
  }

  const savedSoFar = minor(summary.net);
  const target = minor(goal?.target_savings);

  return (
    <Card title="monthly goal" tick="var(--pace-good)">
      {target > 0 && (
        <p className="data mb-3 text-[12px]">
          {displayMinor(savedSoFar, summary.base_currency)} of{" "}
          {display(goal.target_savings)} saved
          <span
            style={{ color: savedSoFar >= target ? "var(--pace-good)" : "var(--pace-warn)" }}
          >
            {" "}
            · {Math.round(ratio(savedSoFar, target) * 100)}%
          </span>
        </p>
      )}

      <form onSubmit={save} className="grid gap-3 sm:grid-cols-2">
        <Field label="income target">
          <Input value={income} onChange={(e) => setIncome(e.target.value)} inputMode="decimal" placeholder="optional" />
        </Field>
        <Field label="savings target">
          <Input value={savings} onChange={(e) => setSavings(e.target.value)} inputMode="decimal" placeholder="optional" />
        </Field>
        <Field label="notes" wide>
          <Input value={notes} onChange={(e) => setNotes(e.target.value)} placeholder="optional" />
        </Field>
        <div className="sm:col-span-2">
          <button type="submit" className="btn btn--accent">
            {saved ? "saved" : "save goal"}
          </button>
        </div>
      </form>
    </Card>
  );
}

/* ==========================================================================
   Categories
   ========================================================================== */

function CategoryManager({ categories, onChanged }) {
  const [name, setName] = useState("");
  const [emoji, setEmoji] = useState("");
  const [editing, setEditing] = useState(null);
  const [draft, setDraft] = useState("");

  async function add(event) {
    event.preventDefault();
    await createCategory({ name, emoji: emoji || null }).catch(() => {});
    setName("");
    setEmoji("");
    onChanged();
  }

  async function rename(original) {
    if (draft && draft !== original) {
      await updateCategory(original, { new_name: draft }).catch(() => {});
      onChanged();
    }
    setEditing(null);
  }

  return (
    <Card
      title="categories"
      note="Renaming updates every past transaction, so history stays together."
      tick="var(--line-strong)"
    >
      <ul className="mb-4 flex flex-wrap gap-2">
        {categories.map((item) => (
          <li key={item.name}>
            {editing === item.name ? (
              <Input
                autoFocus
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                onBlur={() => rename(item.name)}
                onKeyDown={(e) => e.key === "Enter" && rename(item.name)}
                style={{ width: "9rem", padding: "0.3rem 0.5rem" }}
              />
            ) : (
              <button
                type="button"
                className="btn"
                style={{ textTransform: "none", letterSpacing: 0 }}
                onClick={() => {
                  setEditing(item.name);
                  setDraft(item.name);
                }}
              >
                {item.emoji ? `${item.emoji} ` : ""}
                {item.name}
              </button>
            )}
          </li>
        ))}
      </ul>

      <form onSubmit={add} className="grid gap-3 sm:grid-cols-2">
        <Field label="new category">
          <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="Name" required />
        </Field>
        <Field label="emoji">
          <Input value={emoji} onChange={(e) => setEmoji(e.target.value)} maxLength={4} placeholder="optional" />
        </Field>
        <div className="sm:col-span-2">
          <button type="submit" className="btn btn--accent">
            add category
          </button>
        </div>
      </form>
    </Card>
  );
}

/* ==========================================================================
   Transactions
   ========================================================================== */

/** Group a page of transactions into the days they happened on, in order. */
function byDay(rows) {
  const days = new Map();
  for (const row of rows) {
    const key = row.occurred_at.slice(0, 10);
    if (!days.has(key)) days.set(key, []);
    days.get(key).push(row);
  }
  return [...days.entries()];
}

function TransactionRow({ row, emoji, showAccount, onEdit, onDelete }) {
  const income = row.direction === "income";
  const converted = row.amount.currency !== row.base_amount.currency;

  return (
    <li
      className="tx-row grid items-center gap-3 py-2.5"
      style={{ gridTemplateColumns: "1.75rem minmax(0,1fr) auto auto" }}
    >
      <span
        className="grid h-7 w-7 shrink-0 place-items-center text-[14px]"
        style={{
          background: "var(--surface-2)",
          border: "1px solid var(--line)",
          color: "var(--muted)",
        }}
        aria-hidden="true"
      >
        {emoji || (income ? "↓" : "↑")}
      </span>

      <span className="min-w-0">
        <span className="flex items-baseline gap-1.5">
          <span className="truncate text-[14px]">{row.category}</span>
          {row.subcategory && (
            <span className="truncate text-[12px]" style={{ color: "var(--faint)" }}>
              / {row.subcategory}
            </span>
          )}
        </span>
        <span className="flex items-baseline gap-2">
          <span className="data shrink-0 text-[10px]" style={{ color: "var(--faint)" }}>
            {timeOfDay(row.occurred_at)}
          </span>
          {row.note && (
            <span className="truncate text-[12px]" style={{ color: "var(--muted)" }}>
              {row.note}
            </span>
          )}
          {showAccount && row.account && (
            <span className="data shrink-0 text-[10px]" style={{ color: "var(--faint)" }}>
              · {row.account}
            </span>
          )}
        </span>
      </span>

      <span className="text-right">
        <span
          className="data block text-[13px]"
          style={{ color: income ? "var(--pace-good)" : "var(--text)" }}
        >
          {income ? "+" : "−"}
          {bare(row.amount)}
        </span>
        {/* Only shown when the row was entered in another currency: otherwise
            it is the base currency the card header already names, repeated
            once per row. */}
        {converted && (
          <span className="data block text-[10px]" style={{ color: "var(--faint)" }}>
            {row.amount.currency} → {row.base_amount.currency} {bare(row.base_amount)}
          </span>
        )}
      </span>

      {/* Both actions sit in the same slot and share one reveal, so the row's
          width never depends on whether you are hovering it. */}
      <span className="tx-actions flex w-[4.5rem] shrink-0 justify-end gap-1">
        <button
          type="button"
          className="btn btn--sm"
          onClick={() => onEdit(row)}
          aria-label={`Edit transaction ${row.code}`}
        >
          edit
        </button>
        <button
          type="button"
          className="btn btn--sm"
          onClick={() => onDelete(row)}
          aria-label={`Delete transaction ${row.code}`}
          style={{ color: "var(--pace-over)" }}
        >
          ✕
        </button>
      </span>
    </li>
  );
}

function TransactionList({
  rows,
  loading,
  search,
  onSearch,
  filter,
  onFilter,
  categories,
  onEdit,
  onDelete,
  onAdd,
}) {
  const emojiFor = useMemo(() => {
    const lookup = new Map(categories.map((item) => [item.name, item.emoji]));
    return (name) => lookup.get(name) ?? "";
  }, [categories]);

  const groups = useMemo(() => byDay(rows), [rows]);

  // Naming the account on every row is only informative when there is more
  // than one in play; otherwise it is the same word thirty-four times.
  const showAccount = useMemo(
    () => new Set(rows.map((row) => row.account)).size > 1,
    [rows],
  );

  return (
    <Card
      title="transactions"
      note={
        rows.length > 0
          ? `${rows.length} shown · hover a row to edit or delete it`
          : undefined
      }
      tick="var(--line-strong)"
      action={
        <div className="flex flex-wrap gap-2">
          <Input
            type="search"
            className="control--sm data"
            value={search}
            onChange={(e) => onSearch(e.target.value)}
            placeholder="Search note or code"
            aria-label="Search transactions"
            style={{ width: "11rem" }}
          />
          <Select
            className="control--sm"
            value={filter}
            onChange={(e) => onFilter(e.target.value)}
            aria-label="Filter by category"
            style={{ width: "9rem" }}
          >
            <option value="">All categories</option>
            {categories.map((item) => (
              <option key={item.name} value={item.name}>
                {item.name}
              </option>
            ))}
          </Select>
        </div>
      }
    >
      {loading ? (
        <p className="text-[13px]" style={{ color: "var(--faint)" }}>
          Loading…
        </p>
      ) : rows.length === 0 ? (
        <div className="py-6 text-center">
          <p className="text-[14px]" style={{ color: "var(--muted)" }}>
            {search || filter ? "Nothing matches those filters." : "No transactions this month yet."}
          </p>
          <button type="button" className="btn btn--accent mt-3" onClick={onAdd}>
            add one
          </button>
          <p className="data mt-2 text-[10px]" style={{ color: "var(--faint)" }}>
            or just tell the assistant what you spent
          </p>
        </div>
      ) : (
        <div className="space-y-4">
          {groups.map(([day, entries]) => (
            <div key={day}>
              <div
                className="mb-1 flex items-baseline justify-between border-b pb-1"
                style={{ borderColor: "var(--line-strong)" }}
              >
                <span className="label" style={{ color: "var(--muted)" }}>
                  {longDay(day)}
                </span>
                <span className="data text-[11px]" style={{ color: "var(--faint)" }}>
                  {displayMinor(dayExpense(entries), entries[0].base_amount.currency)}
                </span>
              </div>

              <ul>
                {entries.map((row) => (
                  <TransactionRow
                    key={row.code}
                    row={row}
                    emoji={emojiFor(row.category)}
                    showAccount={showAccount}
                    onEdit={onEdit}
                    onDelete={onDelete}
                  />
                ))}
              </ul>
            </div>
          ))}
        </div>
      )}
    </Card>
  );
}

/** What a day's expenses came to. Summed in base currency, because a day can
    hold rows entered in more than one and those cannot simply be added. */
function dayExpense(entries) {
  return entries
    .filter((row) => row.direction !== "income")
    .reduce((sum, row) => sum + minor(row.base_amount), 0);
}

/* ==========================================================================
   Page
   ========================================================================== */

export default function FinancePage({ theme }) {
  const [month, setMonth] = useState(monthKey);
  const [overview, setOverview] = useState(null);
  const [error, setError] = useState(null);

  const [rows, setRows] = useState([]);
  const [tableLoading, setTableLoading] = useState(true);
  const [search, setSearch] = useState("");
  const [filter, setFilter] = useState("");

  const [commentary, setCommentary] = useState(null);
  const [explaining, setExplaining] = useState(false);

  const [version, setVersion] = useState(0);
  const refresh = useCallback(() => setVersion((value) => value + 1), []);

  const toast = useToast();
  // One dialog serves both kinds of deletion. It holds the copy and the action,
  // so adding a third deletable thing later means describing it, not rebuilding
  // the modal.
  const [pending, setPending] = useState(null);

  // `null` closed, `{ row: null }` recording a new one, `{ row }` correcting
  // that one. One piece of state, so the sheet cannot be in two of those at
  // once and cannot be open with nothing to show.
  const [editor, setEditor] = useState(null);

  const openNew = useCallback(() => setEditor({ row: null }), []);
  const openEdit = useCallback((row) => setEditor({ row }), []);

  const onSaved = useCallback(
    (saved, mode) => {
      setEditor(null);
      toast.show({
        message:
          mode === "create"
            ? `Recorded ${bare(saved.amount)} on ${saved.category}.`
            : `Updated ${saved.code}.`,
        tone: "good",
      });
      refresh();
    },
    [toast, refresh],
  );

  const askDeleteTransaction = useCallback((row) => {
    setPending({
      title: "Delete this transaction?",
      detail: `${shortDate(row.occurred_at)} · ${row.category} · ${
        row.direction === "income" ? "+" : "−"
      }${bare(row.amount)}${row.note ? ` · ${row.note}` : ""}`,
      confirmLabel: "delete",
      run: async () => {
        await removeTransaction(row.code);
        toast.show({
          message: `Deleted ${row.code}.`,
          tone: "warn",
          action: {
            label: "undo",
            run: async () => {
              try {
                await restoreTransaction(row.code);
                toast.show({ message: `Restored ${row.code}.`, tone: "good" });
              } catch (failure) {
                toast.show({ message: `Could not restore: ${failure.message}`, tone: "bad" });
              } finally {
                refresh();
              }
            },
          },
        });
      },
    });
  }, [toast, refresh]);

  const askRemoveBudget = useCallback((budget) => {
    setPending({
      title: "Remove this budget?",
      detail: `${budget.category} · limit ${bare(budget.limit)}. Your transactions are not touched — only the target goes.`,
      confirmLabel: "remove",
      run: async () => {
        await removeBudget(budget.code);
        // Budgets have no soft delete behind them, so this toast cannot offer
        // an undo. Setting it again is the recovery, and it is cheap.
        toast.show({ message: `Removed the ${budget.category} budget.`, tone: "warn" });
      },
    });
  }, [toast]);

  const runPending = useCallback(async () => {
    if (!pending) return;
    const action = pending;
    setPending(null);
    try {
      await action.run();
    } catch (failure) {
      toast.show({ message: failure.message, tone: "bad" });
    } finally {
      refresh();
    }
  }, [pending, toast, refresh]);

  useEffect(() => {
    let live = true;
    setError(null);
    getOverview(month)
      .then((data) => live && setOverview(data))
      .catch((failure) => live && setError(failure.message));
    return () => {
      live = false;
    };
  }, [month, version]);

  const range = useMemo(() => {
    const [year, index] = month.split("-").map(Number);
    const last = new Date(year, index, 0).getDate();
    return { start: `${month}-01`, end: `${month}-${String(last).padStart(2, "0")}` };
  }, [month]);

  useEffect(() => {
    let live = true;
    setTableLoading(true);

    const timer = setTimeout(() => {
      listTransactions({
        start: range.start,
        end: range.end,
        limit: 200,
        ...(filter ? { category: filter } : {}),
        ...(search ? { search } : {}),
      })
        .then((page) => live && setRows(page.transactions))
        .catch(() => live && setRows([]))
        .finally(() => live && setTableLoading(false));
    }, search ? 250 : 0);

    return () => {
      live = false;
      clearTimeout(timer);
    };
  }, [range, filter, search, version]);

  async function explain() {
    setExplaining(true);
    try {
      const result = await explainPeriod({ month });
      setCommentary(result);
    } catch (failure) {
      setCommentary({ commentary: failure.message, narrated: false });
    } finally {
      setExplaining(false);
    }
  }

  const isCurrentMonth = month === monthKey();

  return (
    <main className="scroll-thin relative z-10 flex-1 overflow-y-auto">
      <div className="mx-auto w-full max-w-6xl px-4 py-5">
        {/* Month control */}
        <div className="mb-4 flex flex-wrap items-center gap-3">
          <div className="flex items-center border" style={{ borderColor: "var(--line)" }}>
            <button
              type="button"
              className="btn border-0"
              onClick={() => setMonth(shiftMonth(month, -1))}
              aria-label="Previous month"
            >
              ‹
            </button>
            <span className="display px-3 text-[15px]">{monthLabel(month)}</span>
            <button
              type="button"
              className="btn border-0"
              onClick={() => setMonth(shiftMonth(month, 1))}
              disabled={isCurrentMonth}
              style={{ opacity: isCurrentMonth ? 0.35 : 1 }}
              aria-label="Next month"
            >
              ›
            </button>
          </div>

          {!isCurrentMonth && (
            <button type="button" className="btn" onClick={() => setMonth(monthKey())}>
              this month
            </button>
          )}

          <button type="button" className="btn ml-auto" onClick={explain} disabled={explaining}>
            {explaining ? "reading…" : theme === "edgerunner" ? "run analysis" : "explain this month"}
          </button>
        </div>

        {error && (
          <div
            className="panel panel--ticked mb-4 px-3 py-2 text-[13px]"
            style={{ "--tick": "var(--pace-over)", color: "var(--pace-over)" }}
            role="alert"
          >
            {error}
          </div>
        )}

        {!overview ? (
          <p className="text-[14px]" style={{ color: "var(--faint)" }}>
            Adding up {monthLabel(month)}…
          </p>
        ) : (
          <div className="space-y-3">
            <StatRow summary={overview.summary} comparison={overview.comparison} />

            {commentary && (
              <Card title="what happened" tick="var(--accent)">
                <p className="text-[14px]" style={{ whiteSpace: "pre-wrap" }}>
                  {commentary.commentary}
                </p>
                {commentary.narrated === false && (
                  <p className="data mt-2 text-[10px]" style={{ color: "var(--faint)" }}>
                    Written from the figures directly — the model was unavailable.
                  </p>
                )}
              </Card>
            )}

            <DailyChart summary={overview.summary} month={month} />

            <div className="grid gap-3 lg:grid-cols-2">
              <Classification summary={overview.summary} filter={filter} onFilter={setFilter} />
              <div className="space-y-3">
                <Budgets
                  budgets={overview.budgets}
                  categories={overview.categories}
                  month={month}
                  onChanged={refresh}
                  onRemove={askRemoveBudget}
                />
                <Goal goal={overview.goal} summary={overview.summary} month={month} onChanged={refresh} />
              </div>
            </div>

            <TransactionList
              rows={rows}
              loading={tableLoading}
              search={search}
              onSearch={setSearch}
              filter={filter}
              onFilter={setFilter}
              categories={overview.categories}
              onEdit={openEdit}
              onDelete={askDeleteTransaction}
              onAdd={openNew}
            />

            <CategoryManager categories={overview.categories} onChanged={refresh} />
          </div>
        )}
      </div>

      {/* Bottom right, clear of the content column's scroll. It is the only way
          to record a transaction by hand now, so it stays put rather than
          living at the far end of a long page. */}
      <button
        type="button"
        className="fab fixed bottom-5 right-5 z-40"
        onClick={openNew}
      >
        <span className="text-[18px] leading-none">+</span>
        <span className="hidden sm:inline">
          {theme === "edgerunner" ? "log entry" : "add transaction"}
        </span>
      </button>

      <TransactionDialog
        open={editor !== null}
        transaction={editor?.row ?? null}
        overview={overview}
        onClose={() => setEditor(null)}
        onSaved={onSaved}
      />

      <ConfirmDialog
        open={pending !== null}
        title={pending?.title}
        detail={pending?.detail}
        confirmLabel={pending?.confirmLabel}
        onConfirm={runPending}
        onCancel={() => setPending(null)}
      />
    </main>
  );
}
