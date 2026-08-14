import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";

/**
 * Toasts, with an optional action.
 *
 * A delete toast carries "undo" rather than just announcing the deletion,
 * which is what makes the confirm dialog affordable: the dialog stops the
 * accident, the toast fixes the mistake.
 */

const ToastContext = createContext(null);

export const useToast = () => useContext(ToastContext);

const TONE_COLOR = {
  info: "var(--accent)",
  good: "var(--pace-good)",
  warn: "var(--pace-warn)",
  bad: "var(--pace-over)",
};

let nextId = 0;

export function ToastProvider({ children }) {
  const [toasts, setToasts] = useState([]);
  const timers = useRef(new Map());

  const dismiss = useCallback((id) => {
    setToasts((current) => current.filter((toast) => toast.id !== id));
    const timer = timers.current.get(id);
    if (timer) {
      clearTimeout(timer);
      timers.current.delete(id);
    }
  }, []);

  const show = useCallback(
    // A toast you are meant to act on needs long enough to be noticed, read,
    // and reached. One you only need to read does not.
    ({ message, tone = "info", action, duration = action ? 12000 : 5000 }) => {
      const id = ++nextId;
      setToasts((current) => [...current, { id, message, tone, action }]);
      timers.current.set(
        id,
        setTimeout(() => dismiss(id), duration),
      );
      return id;
    },
    [dismiss],
  );

  useEffect(() => {
    const pending = timers.current;
    return () => pending.forEach(clearTimeout);
  }, []);

  const value = useMemo(() => ({ show, dismiss }), [show, dismiss]);

  return (
    <ToastContext.Provider value={value}>
      {children}

      <div
        className="pointer-events-none fixed inset-x-0 bottom-0 z-[60] flex flex-col items-center gap-2 p-4 sm:items-end"
        role="status"
        aria-live="polite"
      >
        {toasts.map((toast) => (
          <div
            key={toast.id}
            className="panel panel--ticked animate-rise pointer-events-auto flex w-full max-w-sm items-center gap-3 px-3 py-2.5"
            style={{ "--tick": TONE_COLOR[toast.tone], boxShadow: "var(--shadow)" }}
          >
            <span className="flex-1 text-[13px]">{toast.message}</span>

            {toast.action && (
              <button
                type="button"
                className="btn shrink-0"
                onClick={() => {
                  dismiss(toast.id);
                  toast.action.run();
                }}
              >
                {toast.action.label}
              </button>
            )}

            <button
              type="button"
              onClick={() => dismiss(toast.id)}
              className="data shrink-0 text-[11px]"
              style={{ color: "var(--faint)" }}
              aria-label="Dismiss"
            >
              ✕
            </button>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}
