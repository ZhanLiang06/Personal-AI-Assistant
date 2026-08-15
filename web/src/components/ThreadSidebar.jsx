import { useEffect } from "react";
import { relativeDay } from "../lib/format.js";
import { routeLinkProps } from "../lib/router.js";

/**
 * Threads live on the left, where a reading eye starts. It is a permanent
 * column from the `lg` breakpoint up and a slide-in drawer below it, so the
 * same list serves both without a second implementation.
 */

function ThreadRow({ thread, active, onOpen, onDelete }) {
  return (
    <li className="group relative">
      <button
        type="button"
        onClick={() => onOpen(thread.id)}
        className="flex w-full items-baseline gap-2 py-2 pl-3 pr-9 text-left"
        style={{
          background: active ? "var(--surface-hover)" : "transparent",
          boxShadow: active ? "inset 2px 0 0 var(--accent)" : "none",
          transition: "background 120ms var(--ease)",
        }}
      >
        <span
          className="truncate text-[13px]"
          style={{ color: active ? "var(--text)" : "var(--muted)" }}
        >
          {thread.title || "Untitled thread"}
        </span>
        <span className="data ml-auto shrink-0 text-[9px]" style={{ color: "var(--faint)" }}>
          {relativeDay(thread.updated_at)}
        </span>
      </button>

      <button
        type="button"
        onClick={() => onDelete(thread)}
        aria-label={`Delete ${thread.title || "thread"}`}
        className="data absolute right-1 top-1.5 px-1.5 py-1 text-[10px] opacity-0 transition-opacity focus-visible:opacity-100 group-hover:opacity-100"
        style={{ color: "var(--faint)" }}
      >
        ✕
      </button>
    </li>
  );
}

const SECTIONS = [
  { href: "/", label: "chat", glyph: "▚", hint: "ask, plan, recall" },
  { href: "/finance", label: "finance", glyph: "▤", hint: "spend, budgets, goals" },
];

/** Where you are in the app, above the list of what you have said. */
function SectionNav({ path, navigate, theme }) {
  return (
    <nav className="mb-3" aria-label="Sections">
      <p className="label mb-1.5 px-1">{theme === "edgerunner" ? "sectors" : "sections"}</p>
      <div className="space-y-1">
        {SECTIONS.map((section) => {
          const active = path === section.href;
          return (
            <a
              key={section.href}
              {...routeLinkProps(section.href, navigate)}
              aria-current={active ? "page" : undefined}
              className="flex items-center gap-2.5 px-2 py-1.5"
              style={{
                background: active ? "var(--surface-hover)" : "transparent",
                boxShadow: active ? "inset 2px 0 0 var(--accent)" : "none",
                color: active ? "var(--text)" : "var(--muted)",
                transition: "background 120ms var(--ease), color 120ms var(--ease)",
              }}
            >
              <span
                className="data grid h-6 w-6 shrink-0 place-items-center text-[11px]"
                style={{
                  border: `1px solid ${active ? "var(--accent)" : "var(--line)"}`,
                  color: active ? "var(--accent)" : "var(--faint)",
                }}
                aria-hidden="true"
              >
                {section.glyph}
              </span>
              <span className="min-w-0">
                <span
                  className="data block text-[11px] uppercase"
                  style={{ letterSpacing: "0.12em" }}
                >
                  {section.label}
                </span>
                <span className="block truncate text-[11px]" style={{ color: "var(--faint)" }}>
                  {section.hint}
                </span>
              </span>
            </a>
          );
        })}
      </div>
    </nav>
  );
}

function SidebarBody({ threads, activeId, onOpen, onDelete, onNew, navigate, path, theme }) {
  return (
    <>
      <SectionNav path={path} navigate={navigate} theme={theme} />

      <button type="button" onClick={onNew} className="btn btn--accent mb-3 w-full">
        new thread
      </button>

      <p className="label mb-1 px-3">{theme === "edgerunner" ? "threads // recent" : "threads"}</p>

      <ul className="scroll-thin -mx-1 flex-1 overflow-y-auto">
        {threads.length === 0 ? (
          <li className="px-3 py-2 text-[13px]" style={{ color: "var(--faint)" }}>
            Nothing here yet. Ask something to start one.
          </li>
        ) : (
          threads.map((thread) => (
            <ThreadRow
              key={thread.id}
              thread={thread}
              active={thread.id === activeId}
              onOpen={onOpen}
              onDelete={onDelete}
            />
          ))
        )}
      </ul>

    </>
  );
}

export default function ThreadSidebar({ open, onClose, ...props }) {
  useEffect(() => {
    if (!open) return;
    const onKey = (event) => event.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  return (
    <>
      {/* Permanent column, large screens up. */}
      <aside
        className="relative z-20 hidden w-60 shrink-0 flex-col border-r p-3 lg:flex"
        style={{ borderColor: "var(--line)", background: "var(--bg-alt)" }}
        aria-label="Threads"
      >
        <SidebarBody {...props} />
      </aside>

      {/* Drawer, below large. */}
      {open && (
        <div className="fixed inset-0 z-50 lg:hidden">
          <button
            type="button"
            aria-label="Close threads"
            onClick={onClose}
            className="absolute inset-0"
            style={{ background: "var(--scrim)", backdropFilter: "blur(3px)" }}
          />
          <aside
            className="animate-rise absolute inset-y-0 left-0 flex w-[min(17rem,82vw)] flex-col border-r p-3"
            style={{ borderColor: "var(--line)", background: "var(--bg-alt)" }}
            aria-label="Threads"
          >
            <div className="mb-3 flex justify-end">
              <button type="button" onClick={onClose} className="btn btn--sm" aria-label="Close">
                ✕
              </button>
            </div>
            <SidebarBody {...props} />
          </aside>
        </div>
      )}
    </>
  );
}
