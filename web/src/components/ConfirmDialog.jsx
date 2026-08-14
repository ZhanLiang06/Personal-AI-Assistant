import { useEffect, useRef } from "react";

/**
 * A modal that names exactly what is about to go.
 *
 * The system prompt already requires the agent to state a transaction's date,
 * category, and amount before deleting one. The dashboard holds itself to the
 * same standard, so a delete reads the same whichever way you reached it.
 */
export default function ConfirmDialog({
  open,
  title,
  detail,
  confirmLabel = "delete",
  tone = "bad",
  onConfirm,
  onCancel,
}) {
  const confirmRef = useRef(null);
  const dialogRef = useRef(null);

  useEffect(() => {
    if (!open) return;

    confirmRef.current?.focus();

    const onKey = (event) => {
      if (event.key === "Escape") {
        event.preventDefault();
        onCancel();
        return;
      }

      // Keep Tab inside the dialog: two buttons, so this is a short loop.
      if (event.key === "Tab") {
        const focusable = dialogRef.current?.querySelectorAll("button");
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
      }
    };

    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onCancel]);

  if (!open) return null;

  const accent = tone === "bad" ? "var(--pace-over)" : "var(--accent)";

  return (
    <div className="fixed inset-0 z-[70] flex items-center justify-center p-4">
      <button
        type="button"
        aria-label="Cancel"
        onClick={onCancel}
        className="absolute inset-0"
        style={{ background: "var(--scrim)", backdropFilter: "blur(3px)" }}
      />

      <div
        ref={dialogRef}
        role="alertdialog"
        aria-modal="true"
        aria-labelledby="confirm-title"
        aria-describedby={detail ? "confirm-detail" : undefined}
        className="panel panel--ticked animate-rise relative w-full max-w-sm p-4"
        style={{ "--tick": accent, boxShadow: "var(--shadow)" }}
      >
        <h2 id="confirm-title" className="display text-[19px]">
          {title}
        </h2>

        {detail && (
          <p id="confirm-detail" className="mt-2 text-[14px]" style={{ color: "var(--muted)" }}>
            {detail}
          </p>
        )}

        <div className="mt-4 flex justify-end gap-2">
          <button type="button" className="btn" onClick={onCancel}>
            cancel
          </button>
          <button
            ref={confirmRef}
            type="button"
            className="btn"
            onClick={onConfirm}
            style={{ background: accent, borderColor: accent, color: "var(--accent-ink)" }}
          >
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
