import { ContextRail } from "./ContextStrip.jsx";
import { routeLinkProps } from "../lib/router.js";

const THEMES = [
  { id: "telemetry", short: "TLM", full: "telemetry" },
  { id: "edgerunner", short: "EDG", full: "edgerunner" },
];

const SECTIONS = [
  { href: "/", label: "chat", glyph: "▚" },
  { href: "/finance", label: "finance", glyph: "▤" },
];

export default function HeaderRail({
  theme,
  mode,
  setTheme,
  toggleMode,
  rail,
  path,
  navigate,
  onOpenThreads,
  onNewThread,
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
        className="btn btn--sm lg:hidden"
        aria-label="Open threads"
      >
        ☰
      </button>

      {/* The wordmark is the way back to a blank slate: it opens a new thread
          rather than merely routing home, which is what "back to the start"
          means in a chat app. */}
      <a
        href="/"
        onClick={(event) => {
          if (event.metaKey || event.ctrlKey || event.shiftKey || event.button !== 0) return;
          event.preventDefault();
          onNewThread();
        }}
        className="glitch wordmark flex items-baseline gap-2"
        aria-label="New thread"
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

      {/* Section tabs live in the header, not only in the sidebar, because the
          sidebar is a drawer below `lg` - finance was otherwise two taps away
          on a phone and invisible until you found the hamburger. */}
      <nav className="seg ml-1 sm:ml-3" aria-label="Sections">
        {SECTIONS.map((section) => {
          const active = section.href === path;
          return (
            <a
              key={section.href}
              {...routeLinkProps(section.href, navigate)}
              aria-current={active ? "page" : undefined}
            >
              <span aria-hidden="true">{section.glyph}</span>
              <span className="hidden sm:inline">{section.label}</span>
            </a>
          );
        })}
      </nav>

      {rail && <div className="animate-rise ml-2 hidden md:block">{rail}</div>}
      {children}

      <div className="ml-auto flex items-center gap-2">
        <div className="seg" role="group" aria-label="Visual theme">
          {THEMES.map((option) => {
            const active = option.id === theme;
            return (
              <button
                key={option.id}
                type="button"
                onClick={() => setTheme(option.id)}
                aria-pressed={active}
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
          className="btn btn--sm"
          aria-label={mode === "dark" ? "Switch to light mode" : "Switch to dark mode"}
        >
          {mode === "dark" ? "☾" : "☀"}
        </button>
      </div>
    </header>
  );
}

export { ContextRail };
