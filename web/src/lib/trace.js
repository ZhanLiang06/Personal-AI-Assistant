/**
 * Fold an agent run into one row per unit of work.
 *
 * Two sources feed this. A live run arrives as SSE status frames carrying
 * step_ms, so its rows have real times. A thread reloaded from SQLite has the
 * tool calls but not the timings, so those rows carry `ms: null` and render as
 * a dash rather than a fabricated number.
 *
 * Both sources carry the call arguments and the returned text, so a row is not
 * just "which tool and how long" but "what was asked of it and what came back".
 * A live row is written at call time with its arguments already filled in, and
 * the result lands in that same row when the tool returns - so the trace is
 * readable mid-run, not only after it.
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
  list_vault_structure: "read vault",
  record_finance_transaction: "record spend",
  update_finance_transaction: "update spend",
  delete_finance_transaction: "delete spend",
  restore_finance_transaction: "restore spend",
  list_deleted_finance_transactions: "read deleted",
  list_finance_transactions: "read spend",
  get_finance_summary: "spend summary",
  list_finance_categories: "read categories",
  add_finance_category: "add category",
  add_finance_subcategory: "add subcategory",
  update_finance_category: "rename category",
  update_finance_subcategory: "rename subcategory",
  set_finance_budget: "set budget",
  get_finance_budgets: "read budgets",
  set_finance_goal: "set goal",
};

const PHASE_LABELS = {
  reasoning_available: "reasoning",
  assistant_response_ready: "compose reply",
};

export const toolLabel = (name) => TOOL_LABELS[name] ?? name;

/**
 * Live SSE frames.
 *
 * Rows are keyed by tool_call_id rather than by tool name: one model turn can
 * ask for the same tool twice, and keying by name would let the second call's
 * result close the first call's row.
 */
export function stepsFromEvents(events) {
  const steps = [];
  const rowByCall = new Map();

  for (const event of events) {
    const { code, tool_name: tool, tool_call_id: callId, step_ms: step = 0 } = event;

    if (code === "tool_call_requested") {
      const row = {
        kind: "tool",
        tool,
        name: toolLabel(tool),
        args: event.tool_args ?? null,
        result: null,
        ms: null,
        callMs: step,
        done: false,
      };
      steps.push(row);
      rowByCall.set(callId ?? `${tool}:${steps.length}`, row);
      continue;
    }

    if (code === "tool_result_received") {
      const key = callId ?? [...rowByCall.keys()].find((k) => k.startsWith(`${tool}:`));
      const row = rowByCall.get(key);
      rowByCall.delete(key);

      if (row) {
        // Patched in place so the row keeps the position it was called from.
        row.result = event.result ?? event.result_preview ?? "";
        row.ms = (row.callMs ?? 0) + step;
        row.done = true;
      } else {
        steps.push({
          kind: "tool",
          tool,
          name: toolLabel(tool),
          args: null,
          result: event.result ?? event.result_preview ?? "",
          ms: step,
          done: true,
        });
      }
      continue;
    }

    if (PHASE_LABELS[code]) {
      steps.push({ kind: "phase", name: PHASE_LABELS[code], ms: step, done: true });
    }
  }

  return steps;
}

/**
 * Stored conversation events, which have names and payloads but no timings.
 * The call and its result are two rows in SQLite; they are folded back into one
 * row here by tool_call_id, the same pairing the live path does.
 */
export function stepsFromHistory(events) {
  const steps = [];
  const rowByCall = new Map();

  for (const event of events) {
    if (event.event_type === "tool_call") {
      const row = {
        kind: "tool",
        tool: event.tool_name,
        name: toolLabel(event.tool_name),
        args: parseArgs(event.tool_args_json),
        result: null,
        ms: null,
        done: true,
      };
      steps.push(row);
      if (event.tool_call_id) rowByCall.set(event.tool_call_id, row);
      continue;
    }

    if (event.event_type === "tool_result") {
      const row = rowByCall.get(event.tool_call_id);
      const text = event.tool_result ?? event.tool_result_preview ?? "";

      if (row) {
        row.result = text;
        rowByCall.delete(event.tool_call_id);
      } else {
        steps.push({
          kind: "tool",
          tool: event.tool_name,
          name: toolLabel(event.tool_name),
          args: null,
          result: text,
          ms: null,
          done: true,
        });
      }
    }
  }

  return steps;
}

function parseArgs(json) {
  if (!json) return null;
  try {
    return JSON.parse(json);
  } catch {
    // A payload that will not parse is still worth showing verbatim.
    return json;
  }
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

/* --- payload rendering ----------------------------------------------------
   Two readings of the same data. The summary sits on the collapsed row and has
   to survive being one line in a narrow column, so it is aggressively clipped.
   The block is what you open the row to read, and is left intact.
   -------------------------------------------------------------------------- */

const SUMMARY_VALUE_MAX = 32;
const SUMMARY_MAX = 90;

const clip = (text, max) => (text.length > max ? `${text.slice(0, max - 1)}…` : text);

/** One argument value, flattened to a single line. */
function summaryValue(value) {
  if (value === null || value === undefined) return "—";
  if (typeof value === "string") return clip(value.replace(/\s+/g, " ").trim(), SUMMARY_VALUE_MAX);
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  if (Array.isArray(value)) return `[${value.length}]`;
  return `{${Object.keys(value).length}}`;
}

/** `query="goreal" limit=5` - the call, readable without opening the row. */
export function argSummary(args) {
  if (args === null || args === undefined) return "";
  if (typeof args !== "object") return clip(String(args), SUMMARY_MAX);

  const entries = Object.entries(args);
  if (entries.length === 0) return "no arguments";

  return clip(entries.map(([key, value]) => `${key}=${summaryValue(value)}`).join("  "), SUMMARY_MAX);
}

/** The first line of what came back, for the collapsed row. */
export function resultSummary(result) {
  if (result === null || result === undefined) return "";
  const text = String(result).replace(/\s+/g, " ").trim();
  return text === "" ? "empty result" : clip(text, SUMMARY_MAX);
}

/** The full payload, pretty-printed for the opened row. */
export function payloadBlock(value) {
  if (value === null || value === undefined) return "";
  if (typeof value === "string") return value;

  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}
