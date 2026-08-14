import { display, displayMinor, minor, monthProgress, ratio } from "../lib/format.js";
import { routeLinkProps } from "../lib/router.js";

/**
 * The two things worth glancing at on arrival: what today costs in time, and
 * what the month is costing in money. Both retract into the header rail once a
 * conversation starts.
 */

function minutesNow() {
  const now = new Date();
  return now.getHours() * 60 + now.getMinutes();
}

function toMinutes(time) {
  const [hours, mins] = time.split(":").map(Number);
  return hours * 60 + mins;
}

function Empty({ children }) {
  return (
    <p className="text-[13px]" style={{ color: "var(--faint)" }}>
      {children}
    </p>
  );
}

function ScheduleTile({ today, error, theme }) {
  const now = minutesNow();
  const events = today?.events ?? [];
  const nextIndex = events.findIndex((item) => item.start && toMinutes(item.start) > now);

  return (
    <section className="panel panel--ticked p-4" style={{ "--tick": "var(--pace-good)" }}>
      <header className="mb-3 flex items-baseline justify-between">
        <h2 className="label">{theme === "edgerunner" ? "schedule // today" : "today"}</h2>
        {events.length > 0 && (
          <span className="data text-[10px]" style={{ color: "var(--faint)" }}>
            {events.length} {events.length === 1 ? "event" : "events"}
          </span>
        )}
      </header>

      {error ? (
        <Empty>Calendar didn&rsquo;t load. Ask me to check it instead.</Empty>
      ) : !today ? (
        <Empty>Reading your calendar…</Empty>
      ) : events.length === 0 ? (
        <Empty>Nothing scheduled. The day is yours.</Empty>
      ) : (
        <ul className="space-y-2">
          {events.slice(0, 4).map((item, index) => {
            const past = item.start ? toMinutes(item.start) <= now : false;
            const isNext = index === nextIndex;
            return (
              <li key={`${item.title}-${index}`} className="flex items-baseline gap-3">
                <span
                  className="data w-11 shrink-0 text-[12px]"
                  style={{
                    color: isNext ? "var(--accent)" : past ? "var(--faint)" : "var(--muted)",
                  }}
                >
                  {item.all_day ? "all day" : item.start}
                </span>
                <span
                  className="truncate text-[14px]"
                  style={{
                    color: past ? "var(--faint)" : "var(--text)",
                    textDecoration: past ? "line-through" : "none",
                    textDecorationColor: "var(--faint)",
                  }}
                >
                  {item.title}
                </span>
                {item.location && (
                  <span
                    className="data ml-auto hidden shrink-0 text-[10px] sm:inline"
                    style={{ color: "var(--faint)" }}
                  >
                    {item.location}
                  </span>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}

function SpendTile({ overview, weekSpend, error, theme, navigate }) {
  const summary = overview?.summary;
  const spentMinor = minor(summary?.total_expense);

  // Budget headroom is the sum of what has actually been budgeted. With no
  // budgets set there is no pace to be ahead of, so the tile says so instead
  // of inventing a target.
  const budgetMinor = (overview?.budgets ?? []).reduce((sum, item) => sum + minor(item.limit), 0);
  const hasBudget = budgetMinor > 0;

  const budgetShare = ratio(spentMinor, budgetMinor);
  const throughMonth = monthProgress(overview?.month ?? "");
  const paceDelta = budgetShare - throughMonth;

  const paceColor = !hasBudget
    ? "var(--line-strong)"
    : paceDelta > 0.08
      ? "var(--pace-over)"
      : paceDelta > 0
        ? "var(--pace-warn)"
        : "var(--pace-good)";

  const top = summary?.by_category?.[0];

  return (
    <section className="panel panel--ticked p-4" style={{ "--tick": paceColor }}>
      <header className="mb-3 flex items-baseline justify-between">
        <h2 className="label">{theme === "edgerunner" ? "spend // month" : "this month"}</h2>
        <a
          {...routeLinkProps("/finance", navigate)}
          className="data text-[10px]"
          style={{ color: "var(--faint)" }}
        >
          open finance →
        </a>
      </header>

      {error ? (
        <Empty>Spending didn&rsquo;t load.</Empty>
      ) : !summary ? (
        <Empty>Adding up the month…</Empty>
      ) : (
        <>
          <p className="display text-[30px]" style={{ lineHeight: 1 }}>
            {display(summary.total_expense)}
          </p>

          {hasBudget ? (
            <>
              {/* The fill is budget used. The tick is how far through the
                  month we are. The gap between them is the only reading
                  that matters. */}
              <div className="relative mt-3 h-1.5" style={{ background: "var(--surface-2)" }}>
                <div
                  className="absolute inset-y-0 left-0"
                  style={{ width: `${Math.min(budgetShare, 1) * 100}%`, background: paceColor }}
                />
                <div
                  className="absolute inset-y-[-4px] w-px"
                  style={{ left: `${throughMonth * 100}%`, background: "var(--text)", opacity: 0.7 }}
                  aria-hidden="true"
                />
              </div>

              <div className="mt-2 flex items-baseline justify-between">
                <span className="data text-[11px]" style={{ color: paceColor }}>
                  {paceDelta >= 0 ? "+" : "−"}
                  {Math.abs(paceDelta * 100).toFixed(0)}%{" "}
                  {paceDelta >= 0 ? "ahead of pace" : "under pace"}
                </span>
                <span className="data text-[11px]" style={{ color: "var(--faint)" }}>
                  of {displayMinor(budgetMinor, summary.base_currency)}
                </span>
              </div>
            </>
          ) : (
            <p className="data mt-2 text-[11px]" style={{ color: "var(--faint)" }}>
              No budget set for this month.
            </p>
          )}

          <div
            className="mt-3 flex items-baseline justify-between border-t pt-2"
            style={{ borderColor: "var(--line)" }}
          >
            <span className="label">last 7 days</span>
            <span className="data text-[12px]">{weekSpend ?? "—"}</span>
          </div>

          {top && (
            <p className="data mt-1 text-[10px]" style={{ color: "var(--faint)" }}>
              most of it on {top.category.toLowerCase()} — {display(top.expense)}
            </p>
          )}
        </>
      )}
    </section>
  );
}

export default function ContextStrip({ today, todayError, overview, overviewError, weekSpend, theme, navigate }) {
  return (
    <div className="grid gap-3 md:grid-cols-2">
      <ScheduleTile today={today} error={todayError} theme={theme} />
      <SpendTile
        overview={overview}
        weekSpend={weekSpend}
        error={overviewError}
        theme={theme}
        navigate={navigate}
      />
    </div>
  );
}

/** The retracted form: the same two readings, compressed into the header rail. */
export function ContextRail({ today, overview }) {
  const now = minutesNow();
  const events = today?.events ?? [];
  const next = events.find((item) => item.start && toMinutes(item.start) > now);
  const summary = overview?.summary;

  if (!next && !summary) return null;

  return (
    <div className="hidden items-center gap-4 sm:flex">
      {next && (
        <span className="flex items-baseline gap-2">
          <span className="data text-[11px]" style={{ color: "var(--pace-good)" }}>
            {next.start}
          </span>
          <span className="max-w-[16ch] truncate text-[12px]" style={{ color: "var(--muted)" }}>
            {next.title}
          </span>
        </span>
      )}
      {next && summary && (
        <span className="h-3 w-px" style={{ background: "var(--line)" }} aria-hidden="true" />
      )}
      {summary && (
        <span className="data text-[11px]" style={{ color: "var(--muted)" }}>
          {display(summary.total_expense)}
        </span>
      )}
    </div>
  );
}
