import { useEffect, useMemo, useRef, useState } from "react";

import { createTransaction, updateTransaction } from "../lib/api.js";
import { localDateTimeValue } from "../lib/format.js";

/**
 * One form for both halves of a transaction's life.
 *
 * Recording and correcting take exactly the same eight fields, so they share a
 * sheet rather than a page-bottom form and a separate row editor. What differs
 * is the verb and what gets sent: a create posts everything, an edit PATCHes
 * only the fields you actually touched, because the API treats an omitted
 * field as "leave it alone" and an explicit null as "clear it".
 */

const DIRECTIONS = [
  { value: "expense", label: "Expense" },
  { value: "income", label: "Income" },
];

/** The API sends `2026-08-13T21:02:21`; `datetime-local` wants it to the minute. */
const toInputMoment = (iso) => (iso ? iso.slice(0, 16) : "");

function blankDraft(defaultCurrency) {
  return {
    amount: "",
    currency: defaultCurrency,
    direction: "expense",
    category: "",
    subcategory: "",
    account: "",
    occurred_at: localDateTimeValue(),
    note: "",
  };
}

function draftFrom(transaction, defaultCurrency) {
  if (!transaction) return blankDraft(defaultCurrency);
  return {
    amount: transaction.amount.decimal ?? "",
    currency: transaction.amount.currency ?? defaultCurrency,
    direction: transaction.direction,
    category: transaction.category ?? "",
    subcategory: transaction.subcategory ?? "",
    account: transaction.account ?? "",
    occurred_at: toInputMoment(transaction.occurred_at),
    note: transaction.note ?? "",
  };
}

function Field({ label, wide, hint, children }) {
  return (
    <label className={`flex flex-col gap-1.5 ${wide ? "sm:col-span-2" : ""}`}>
      <span className="label">{label}</span>
      {children}
      {hint && (
        <span className="data text-[10px]" style={{ color: "var(--faint)" }}>
          {hint}
        </span>
      )}
    </label>
  );
}

export default function TransactionDialog({
  open,
  transaction,
  overview,
  onClose,
  onSaved,
}) {
  const editing = Boolean(transaction);
  const defaultCurrency = overview?.summary?.base_currency ?? "MYR";

  const [draft, setDraft] = useState(() => draftFrom(transaction, defaultCurrency));
  const [error, setError] = useState(null);
  const [saving, setSaving] = useState(false);

  const dialogRef = useRef(null);
  const firstFieldRef = useRef(null);

  // Reset every time the sheet opens, so a cancelled edit leaves nothing behind
  // and the next one starts from the row you actually clicked.
  useEffect(() => {
    if (!open) return;
    setDraft(draftFrom(transaction, defaultCurrency));
    setError(null);
    setSaving(false);
    const focus = requestAnimationFrame(() => firstFieldRef.current?.focus());
    return () => cancelAnimationFrame(focus);
  }, [open, transaction, defaultCurrency]);

  useEffect(() => {
    if (!open) return;

    const onKey = (event) => {
      if (event.key === "Escape") {
        event.preventDefault();
        onClose();
        return;
      }

      if (event.key !== "Tab") return;
      const focusable = dialogRef.current?.querySelectorAll(
        "button, input, select, textarea, [href]",
      );
      if (!focusable?.length) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };

    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  const subcategories = useMemo(
    () => (overview?.subcategories ?? []).filter((item) => item.category === draft.category),
    [overview, draft.category],
  );

  if (!open) return null;

  const set = (key) => (event) =>
    setDraft((current) => {
      const next = { ...current, [key]: event.target.value };
      // A subcategory only means anything under its own parent, so changing the
      // category drops one that no longer belongs.
      if (key === "category") next.subcategory = "";
      return next;
    });

  function changedFields() {
    const original = draftFrom(transaction, defaultCurrency);
    const payload = {};

    for (const key of Object.keys(original)) {
      if (draft[key] === original[key]) continue;
      // Empty means "clear it" for the nullable fields and "leave the default"
      // for the rest, which is why they are not treated alike.
      if (key === "subcategory" || key === "note") payload[key] = draft[key] || null;
      else if (draft[key] !== "") payload[key] = draft[key];
    }

    if (payload.currency) payload.currency = payload.currency.toUpperCase();
    return payload;
  }

  async function submit(event) {
    event.preventDefault();
    setError(null);
    setSaving(true);

    try {
      if (editing) {
        const payload = changedFields();
        if (Object.keys(payload).length === 0) {
          onClose();
          return;
        }
        const updated = await updateTransaction(transaction.code, payload);
        onSaved(updated, "edit");
      } else {
        const created = await createTransaction({
          amount: draft.amount,
          currency: draft.currency.toUpperCase(),
          direction: draft.direction,
          category: draft.category,
          subcategory: draft.subcategory || null,
          account: draft.account || undefined,
          occurred_at: draft.occurred_at || null,
          note: draft.note || null,
        });
        onSaved(created, "create");
      }
    } catch (failure) {
      setError(failure.message);
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="fixed inset-0 z-[70] flex items-end justify-center p-0 sm:items-center sm:p-4">
      <button
        type="button"
        aria-label="Close"
        onClick={onClose}
        className="absolute inset-0"
        style={{ background: "var(--scrim)", backdropFilter: "blur(3px)" }}
      />

      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="tx-dialog-title"
        className="panel panel--ticked animate-rise scroll-thin relative max-h-[92vh] w-full max-w-lg overflow-y-auto p-4 sm:p-5"
        style={{
          "--tick": editing ? "var(--pace-fast)" : "var(--accent)",
          boxShadow: "var(--shadow)",
        }}
      >
        <header className="mb-4 flex items-start justify-between gap-3">
          <div>
            <h2 id="tx-dialog-title" className="display text-[20px]">
              {editing ? "Edit transaction" : "New transaction"}
            </h2>
            <p className="data mt-1 text-[10px]" style={{ color: "var(--faint)" }}>
              {editing ? transaction.code : "amount and category are all it needs"}
            </p>
          </div>
          <button type="button" className="btn btn--sm" onClick={onClose} aria-label="Close">
            ✕
          </button>
        </header>

        <form onSubmit={submit} className="grid gap-3 sm:grid-cols-2">
          <Field label="amount">
            <input
              ref={firstFieldRef}
              className="control data"
              value={draft.amount}
              onChange={set("amount")}
              inputMode="decimal"
              placeholder="12.34"
              required
            />
          </Field>

          <Field label="currency">
            <input
              className="control data"
              value={draft.currency}
              onChange={set("currency")}
              maxLength={3}
              style={{ textTransform: "uppercase" }}
              required
            />
          </Field>

          <Field label="direction">
            <select className="control" value={draft.direction} onChange={set("direction")}>
              {DIRECTIONS.map((item) => (
                <option key={item.value} value={item.value}>
                  {item.label}
                </option>
              ))}
            </select>
          </Field>

          <Field label="when">
            <input
              type="datetime-local"
              className="control data"
              value={draft.occurred_at}
              onChange={set("occurred_at")}
              max={localDateTimeValue()}
            />
          </Field>

          <Field label="category">
            <select className="control" value={draft.category} onChange={set("category")} required>
              <option value="">Choose one</option>
              {(overview?.categories ?? []).map((item) => (
                <option key={item.name} value={item.name}>
                  {item.emoji ? `${item.emoji} ` : ""}
                  {item.name}
                </option>
              ))}
            </select>
          </Field>

          <Field
            label="subcategory"
            hint={
              draft.category && subcategories.length === 0
                ? `no subcategories under ${draft.category}`
                : undefined
            }
          >
            <select
              className="control"
              value={draft.subcategory}
              onChange={set("subcategory")}
              disabled={subcategories.length === 0}
            >
              <option value="">None</option>
              {subcategories.map((item) => (
                <option key={item.name} value={item.name}>
                  {item.name}
                </option>
              ))}
            </select>
          </Field>

          <Field label="account">
            <select className="control" value={draft.account} onChange={set("account")}>
              <option value="">Default</option>
              {(overview?.accounts ?? []).map((item) => (
                <option key={item.name} value={item.name}>
                  {item.name}
                </option>
              ))}
            </select>
          </Field>

          <Field label="note" wide>
            <input
              className="control"
              value={draft.note}
              onChange={set("note")}
              placeholder="what it was for"
            />
          </Field>

          {error && (
            <p
              className="data sm:col-span-2 text-[11px]"
              style={{ color: "var(--pace-over)" }}
              role="alert"
            >
              {error}
            </p>
          )}

          <div className="mt-1 flex justify-end gap-2 sm:col-span-2">
            <button type="button" className="btn" onClick={onClose}>
              cancel
            </button>
            <button type="submit" className="btn btn--accent" disabled={saving}>
              {saving ? "saving…" : editing ? "save changes" : "record"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
