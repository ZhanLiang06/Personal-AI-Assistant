import { useCallback, useEffect, useMemo, useState } from "react";

import ConfirmDialog from "../components/ConfirmDialog.jsx";
import { useToast } from "../components/Toast.jsx";
import {
  createCategory,
  createTransaction,
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
  localDateTimeValue,
  minor,
  monthKey,
  monthLabel,
  monthProgress,
  ratio,
  shiftMonth,
  shortDate,
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

const controlStyle = {
  background: "var(--surface-2)",
  border: "1px solid var(--line)",
  color: "var(--text)",
  padding: "0.5rem 0.6rem",
  fontSize: "14px",
  width: "100%",
};

const Input = (props) => <input {...props} style={{ ...controlStyle, ...props.style }} />;
const Select = (props) => <select {...props} style={{ ...controlStyle, ...props.style }} />;

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
   Record a transaction
   ========================================================================== */

const EMPTY_ENTRY = {
  amount: "",
  currency: "MYR",
  direction: "expense",
  category: "",
  subcategory: "",
  account: "",
  occurred_at: "",
  note: "",
};

function RecordForm({ overview, onChanged }) {
  const [entry, setEntry] = useState(EMPTY_ENTRY);
  const [error, setError] = useState(null);

  const set = (key) => (event) => setEntry((current) => ({ ...current, [key]: event.target.value }));

  const subcategories = overview.subcategories.filter((item) => item.category === entry.category);

  async function submit(event) {
    event.preventDefault();
    setError(null);
    try {
      await createTransaction({
        amount: entry.amount,
        currency: entry.currency.toUpperCase(),
        direction: entry.direction,
        category: entry.category,
        subcategory: entry.subcategory || null,
        account: entry.account || undefined,
        occurred_at: entry.occurred_at || null,
        note: entry.note || null,
      });
      setEntry({ ...EMPTY_ENTRY, occurred_at: "" });
      onChanged();
    } catch (failure) {
      setError(failure.message);
    }
  }

  return (
    <Card title="record a transaction" tick="var(--accent)">
      <form onSubmit={submit} className="grid gap-3 sm:grid-cols-2">
        <Field label="amount">
          <Input value={entry.amount} onChange={set("amount")} inputMode="decimal" placeholder="12.34" required />
        </Field>
        <Field label="currency">
          <Input value={entry.currency} onChange={set("currency")} maxLength={3} required />
        </Field>
        <Field label="direction">
          <Select value={entry.direction} onChange={set("direction")}>
            <option value="expense">Expense</option>
            <option value="income">Income</option>
          </Select>
        </Field>
        <Field label="category">
          <Select value={entry.category} onChange={set("category")} required>
            <option value="">Choose one</option>
            {overview.categories.map((item) => (
              <option key={item.name} value={item.name}>
                {item.emoji ? `${item.emoji} ` : ""}
                {item.name}
              </option>
            ))}
          </Select>
        </Field>
        <Field label="subcategory">
          <Select value={entry.subcategory} onChange={set("subcategory")} disabled={subcategories.length === 0}>
            <option value="">None</option>
            {subcategories.map((item) => (
              <option key={item.name} value={item.name}>
                {item.name}
              </option>
            ))}
          </Select>
        </Field>
        <Field label="account">
          <Select value={entry.account} onChange={set("account")}>
            <option value="">Default</option>
            {overview.accounts.map((item) => (
              <option key={item.name} value={item.name}>
                {item.name}
              </option>
            ))}
          </Select>
        </Field>
        <Field label="when">
          <Input
            type="datetime-local"
            value={entry.occurred_at}
            onChange={set("occurred_at")}
            max={localDateTimeValue()}
          />
        </Field>
        <Field label="note" wide>
          <Input value={entry.note} onChange={set("note")} placeholder="optional" />
        </Field>

        <div className="sm:col-span-2">
          <button type="submit" className="btn btn--accent">
            record
          </button>
          {error && (
            <span className="data ml-3 text-[11px]" style={{ color: "var(--pace-over)" }}>
              {error}
            </span>
          )}
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

function TransactionTable({ rows, loading, search, onSearch, filter, onFilter, categories, onDelete }) {
  return (
    <Card
      title="transactions"
      tick="var(--line-strong)"
      action={
        <div className="flex gap-2">
          <Input
            type="search"
            value={search}
            onChange={(e) => onSearch(e.target.value)}
            placeholder="Search note or code"
            aria-label="Search transactions"
            style={{ width: "11rem", padding: "0.35rem 0.5rem", fontSize: "13px" }}
          />
          <Select
            value={filter}
            onChange={(e) => onFilter(e.target.value)}
            aria-label="Filter by category"
            style={{ width: "9rem", padding: "0.35rem 0.5rem", fontSize: "13px" }}
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
        <p className="text-[13px]" style={{ color: "var(--faint)" }}>
          Nothing matches. Record one above, or ask the assistant to.
        </p>
      ) : (
        <div className="scroll-thin -mx-4 overflow-x-auto px-4">
          <table className="w-full min-w-[36rem] border-collapse">
            <thead>
              <tr>
                {["date", "category", "note", "amount", "base", ""].map((head, index) => (
                  <th
                    key={head || index}
                    scope="col"
                    className="label pb-2 text-left"
                    style={{
                      borderBottom: "1px solid var(--line)",
                      textAlign: index >= 3 && index <= 4 ? "right" : "left",
                    }}
                  >
                    {head}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.code} style={{ borderBottom: "1px solid var(--line)" }}>
                  <td className="data py-2 text-[11px]" style={{ color: "var(--muted)" }}>
                    {shortDate(row.occurred_at)}
                  </td>
                  <td className="py-2 text-[13px]">
                    {row.category}
                    {row.subcategory && (
                      <span style={{ color: "var(--faint)" }}> · {row.subcategory}</span>
                    )}
                  </td>
                  <td className="max-w-[14rem] truncate py-2 text-[13px]" style={{ color: "var(--muted)" }}>
                    {row.note ?? ""}
                  </td>
                  <td
                    className="data py-2 text-right text-[12px]"
                    style={{
                      color: row.direction === "income" ? "var(--pace-good)" : "var(--text)",
                    }}
                  >
                    {row.direction === "income" ? "+" : "−"}
                    {bare(row.amount)}
                  </td>
                  <td className="data py-2 text-right text-[11px]" style={{ color: "var(--faint)" }}>
                    {row.amount.currency === row.base_amount.currency ? "" : bare(row.base_amount)}
                  </td>
                  <td className="py-2 text-right">
                    <button
                      type="button"
                      className="data text-[10px]"
                      style={{ color: "var(--faint)" }}
                      onClick={() => onDelete(row)}
                      aria-label={`Delete transaction ${row.code}`}
                    >
                      ✕
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
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

            <TransactionTable
              rows={rows}
              loading={tableLoading}
              search={search}
              onSearch={setSearch}
              filter={filter}
              onFilter={setFilter}
              categories={overview.categories}
              onDelete={askDeleteTransaction}
            />

            <div className="grid gap-3 lg:grid-cols-2">
              <RecordForm overview={overview} onChanged={refresh} />
              <CategoryManager categories={overview.categories} onChanged={refresh} />
            </div>
          </div>
        )}
      </div>

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
