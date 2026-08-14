/**
 * Fold an agent run into one row per unit of work.
 *
 * Two sources feed this. A live run arrives as SSE status frames carrying
 * step_ms, so its rows have real times. A thread reloaded from SQLite has the
 * tool calls but not the timings, so those rows carry `ms: null` and render as
 * a dash rather than a fabricated number.
 */

const TOOL_LABELS = {
  search_notes: "search notes",
  list_daily_todos: "read todos",
  add_daily_todos: "write todos",
  update_daily_todos: "update todos",
  delete_daily_todos: "delete todos",
  list_google_calendar_events: "read calendar",
  create_google_calendar_events: "create events",
  update_google_calendar_events: "update events",
  delete_google_calendar_events: "delete events",
  get_current_time: "clock",
};

const PHASE_LABELS = {
  reasoning_available: "reasoning",
  assistant_response_ready: "compose reply",
};

export const toolLabel = (name) => TOOL_LABELS[name] ?? name;

/** Live SSE frames. */
export function stepsFromEvents(events) {
  const steps = [];
  const open = new Map();

  for (const event of events) {
    const { code, tool_name: tool, step_ms: step = 0 } = event;

    if (code === "tool_call_requested") {
      open.set(tool, { ms: step });
      continue;
    }

    if (code === "tool_result_received") {
      const pending = open.get(tool);
      open.delete(tool);
      steps.push({ kind: "tool", name: toolLabel(tool), ms: (pending?.ms ?? 0) + step, done: true });
      continue;
    }

    if (PHASE_LABELS[code]) {
      steps.push({ kind: "phase", name: PHASE_LABELS[code], ms: step, done: true });
    }
  }

  for (const [tool] of open) {
    steps.push({ kind: "tool", name: toolLabel(tool), ms: null, done: false });
  }

  return steps;
}

/** Stored conversation events, which have names but no timings. */
export function stepsFromHistory(events) {
  return events
    .filter((event) => event.event_type === "tool_call")
    .map((event) => ({
      kind: "tool",
      name: toolLabel(event.tool_name),
      ms: null,
      done: true,
    }));
}

/**
 * Rank the rows. Purple for the fastest step of the run, green for anything
 * quicker than the run's own average, yellow for the rest - the same reading
 * a sector time gets on a timing screen. Rows without a time are unranked.
 */
export function rankSteps(steps) {
  const timed = steps.filter((step) => step.done && typeof step.ms === "number");

  if (timed.length === 0) {
    return steps.map((step) => ({ ...step, pace: step.done ? "none" : "live", share: 0 }));
  }

  const slowest = Math.max(...timed.map((step) => step.ms));
  const fastest = Math.min(...timed.map((step) => step.ms));
  const average = timed.reduce((sum, step) => sum + step.ms, 0) / timed.length;

  return steps.map((step) => {
    if (!step.done) return { ...step, pace: "live", share: 0 };
    if (typeof step.ms !== "number") return { ...step, pace: "none", share: 0 };
    return {
      ...step,
      share: slowest > 0 ? step.ms / slowest : 0,
      pace: step.ms === fastest ? "fast" : step.ms <= average ? "good" : "warn",
    };
  });
}

export const PACE_COLOR = {
  fast: "var(--pace-fast)",
  good: "var(--pace-good)",
  warn: "var(--pace-warn)",
  live: "var(--accent)",
  none: "var(--line-strong)",
};

export const seconds = (ms) => (ms / 1000).toFixed(3);
