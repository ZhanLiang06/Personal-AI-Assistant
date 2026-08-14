import { ContextRail } from "./ContextStrip.jsx";
import { routeLinkProps } from "../lib/router.js";

const THEMES = [
  { id: "telemetry", short: "TLM", full: "telemetry" },
  { id: "edgerunner", short: "EDG", full: "edgerunner" },
];

export default function HeaderRail({
  theme,
  mode,
  setTheme,
  toggleMode,
  rail,
  onOpenThreads,
  navigate,
  children,
}) {
  return (
    <header
      className="sticky top-0 z-30 flex items-center gap-3 border-b px-3 py-2.5 backdrop-blur sm:px-4"
      style={{ borderColor: "var(--line)", background: "var(--scrim)" }}
    >
      <button
        type="button"
        onClick={onOpenThreads}
        className="btn px-2.5 py-1.5 lg:hidden"
        aria-label="Open threads"
      >
        ☰
      </button>

      <a
        {...routeLinkProps("/", navigate)}
        className="glitch flex items-baseline gap-2"
        aria-label="Kairos home"
      >
        <span
          className="inline-block h-3 w-1.5"
          style={{ background: "var(--accent)" }}
          aria-hidden="true"
        />
        <span className="display text-[17px] uppercase">Kairos</span>
        {theme === "edgerunner" && (
          <span className="data text-[9px]" style={{ color: "var(--faint)" }} aria-hidden="true">
            カイロス
          </span>
        )}
      </a>

      {rail && <div className="animate-rise ml-2">{rail}</div>}
      {children}

      <div className="ml-auto flex items-center gap-2">
        <div
          className="flex border"
          style={{ borderColor: "var(--line)" }}
          role="group"
          aria-label="Visual theme"
        >
          {THEMES.map((option) => {
            const active = option.id === theme;
            return (
              <button
                key={option.id}
                type="button"
                onClick={() => setTheme(option.id)}
                aria-pressed={active}
                className="data px-2.5 py-1.5 text-[10px] uppercase"
                style={{
                  letterSpacing: "0.12em",
                  background: active ? "var(--accent)" : "transparent",
                  color: active ? "var(--accent-ink)" : "var(--faint)",
                  transition: "background 140ms var(--ease), color 140ms var(--ease)",
                }}
              >
                <span className="hidden sm:inline">{option.full}</span>
                <span className="sm:hidden">{option.short}</span>
              </button>
            );
          })}
        </div>

        <button
          type="button"
          onClick={toggleMode}
          className="btn px-2.5 py-1.5"
          aria-label={mode === "dark" ? "Switch to light mode" : "Switch to dark mode"}
        >
          {mode === "dark" ? "☾" : "☀"}
        </button>
      </div>
    </header>
  );
}

export { ContextRail };
