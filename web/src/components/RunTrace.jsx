import { useEffect, useMemo, useState } from "react";
import { PACE_COLOR, argSummary, payloadBlock, rankSteps, resultSummary, seconds } from "../lib/trace.js";

/**
 * The run trace, and the one place this design spends its boldness.
 *
 * The agent reports elapsed_ms and step_ms on every event, so this shows what
 * the run actually cost rather than a spinner. Under telemetry it reads as a
 * timing tower; under edgerunner the same ranking becomes a daemon stack.
 *
 * Each tool row carries the call and its return value. The collapsed row shows
 * a clipped one-line reading of both - enough to follow a run at a glance - and
 * opening the row shows the full payloads. During a run the arguments are on
 * screen from the moment the call is requested, and the result fills in beneath
 * them when the tool returns.
 */
export default function RunTrace({ steps: raw, totalMs, theme, running }) {
  const [open, setOpen] = useState(false);
  const [openRows, setOpenRows] = useState(() => new Set());
  const steps = useMemo(() => rankSteps(raw), [raw]);

  // A run that is happening is worth watching, so the trace opens itself when
  // one starts. It does not close itself again - the results are the point, and
  // pulling them off screen the moment the run lands would undo that.
  useEffect(() => {
    if (running) setOpen(true);
  }, [running]);

  if (steps.length === 0 && !running) return null;

  const edge = theme === "edgerunner";
  const toolCount = steps.filter((step) => step.kind === "tool").length;
  const summary = edge
    ? `${toolCount} daemon${toolCount === 1 ? "" : "s"}`
    : `${toolCount} stop${toolCount === 1 ? "" : "s"}`;

  const toggleRow = (index) =>
    setOpenRows((current) => {
      const next = new Set(current);
      next.has(index) ? next.delete(index) : next.add(index);
      return next;
    });

  return (
    <div className="panel panel--ticked mt-3" style={{ "--tick": "var(--accent)" }}>
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        className="flex w-full items-center gap-3 px-3 py-2 text-left"
        aria-expanded={open}
      >
        <span className="label" style={{ color: "var(--accent)" }}>
          {edge ? "daemons" : "run trace"}
        </span>
        <span className="label">{summary}</span>
        <span className="ml-auto flex items-center gap-2">
          {typeof totalMs === "number" && (
            <span
              className="data text-[12px]"
              style={{
                color: running ? "var(--accent)" : "var(--muted)",
                animation: running ? "blink 1s steps(2) infinite" : "none",
              }}
            >
              {seconds(totalMs)}
              <span style={{ color: "var(--faint)" }}>s</span>
            </span>
          )}
          <span
            className="data text-[10px]"
            style={{
              color: "var(--faint)",
              transform: open ? "rotate(90deg)" : "none",
              transition: "transform 160ms var(--ease)",
            }}
            aria-hidden="true"
          >
            ▶
          </span>
        </span>
      </button>

      {open && (
        <ol className="border-t px-3 py-2" style={{ borderColor: "var(--line)" }}>
          {steps.map((step, index) => (
            <TraceRow
              key={`${step.name}-${index}`}
              step={step}
              index={index}
              edge={edge}
              open={openRows.has(index)}
              onToggle={() => toggleRow(index)}
            />
          ))}
        </ol>
      )}
    </div>
  );
}

function TraceRow({ step, index, edge, open, onToggle }) {
  const args = argSummary(step.args);
  const result = resultSummary(step.result);
  // A phase row has nothing behind it, so it stays inert rather than offering
  // an expander that opens onto nothing.
  const expandable = step.kind === "tool" && (step.args != null || step.result != null);

  const head = (
    <>
      <div className="grid items-center gap-3" style={{ gridTemplateColumns: "1.5rem 1fr 4.5rem" }}>
        <span className="data text-[10px]" style={{ color: "var(--faint)" }} aria-hidden="true">
          {edge ? "◆" : String(index + 1).padStart(2, "0")}
        </span>

        <span className="flex items-center gap-2">
          <span
            className="data truncate text-[11px]"
            style={{
              color: step.kind === "tool" ? "var(--text)" : "var(--muted)",
              letterSpacing: edge ? "0.02em" : "0.04em",
            }}
          >
            {step.name}
          </span>
          <span
            className="h-[3px] flex-1 origin-left"
            style={{
              background: PACE_COLOR[step.pace],
              opacity: step.pace === "none" ? 0.3 : 1,
              transform: `scaleX(${Math.max(step.share ?? 0, 0.06)})`,
              boxShadow: edge && step.pace !== "none" ? `0 0 8px ${PACE_COLOR[step.pace]}` : "none",
              animation: "sweep 260ms var(--ease) both",
            }}
            aria-hidden="true"
          />
          {expandable && (
            <span
              className="data text-[9px]"
              style={{
                color: "var(--faint)",
                transform: open ? "rotate(90deg)" : "none",
                transition: "transform 160ms var(--ease)",
              }}
              aria-hidden="true"
            >
              ▶
            </span>
          )}
        </span>

        <span className="data text-right text-[11px]" style={{ color: PACE_COLOR[step.pace] }}>
          {!step.done ? "•••" : typeof step.ms === "number" ? seconds(step.ms) : "—"}
        </span>
      </div>

      {expandable && !open && (
        <div className="pr-[4.5rem]" style={{ paddingLeft: "2.25rem" }}>
          {args && <PayloadLine mark="→" text={args} />}
          {step.result != null ? (
            <PayloadLine mark="←" text={result} />
          ) : (
            <PayloadLine mark="←" text={edge ? "daemon running" : "awaiting result"} pending />
          )}
        </div>
      )}
    </>
  );

  return (
    <li className="py-1.5">
      {expandable ? (
        <button type="button" onClick={onToggle} className="block w-full text-left" aria-expanded={open}>
          {head}
        </button>
      ) : (
        head
      )}

      {expandable && open && (
        <div className="mt-1.5 space-y-1.5 pr-1" style={{ paddingLeft: "2.25rem" }}>
          <Payload
            label={edge ? "input" : "called with"}
            // A tool taking no arguments is worth saying in words. `{}` reads
            // as a payload that failed to arrive.
            body={
              step.args == null
                ? "no arguments recorded"
                : isEmptyArgs(step.args)
                  ? "no arguments"
                  : payloadBlock(step.args)
            }
            muted={step.args == null || isEmptyArgs(step.args)}
          />
          <Payload
            label={edge ? "output" : "returned"}
            body={
              step.result == null
                ? edge
                  ? "daemon running"
                  : "awaiting result"
                : payloadBlock(step.result) || "empty result"
            }
            muted={step.result == null}
            pending={step.result == null}
          />
        </div>
      )}
    </li>
  );
}

const isEmptyArgs = (args) =>
  typeof args === "object" && args !== null && !Array.isArray(args) && Object.keys(args).length === 0;

/** The clipped one-liner on a closed row. */
function PayloadLine({ mark, text, pending }) {
  return (
    <p className="data flex gap-1.5 truncate text-[10px]" style={{ color: "var(--faint)" }}>
      <span aria-hidden="true" style={{ color: "var(--line-strong)" }}>
        {mark}
      </span>
      <span
        className="truncate"
        style={{ animation: pending ? "blink 1.2s steps(2) infinite" : "none" }}
      >
        {text}
      </span>
    </p>
  );
}

/** The full payload on an open row. */
function Payload({ label, body, muted, pending }) {
  return (
    <div className="border-l-2 pl-2" style={{ borderColor: "var(--line-strong)" }}>
      <span className="label">{label}</span>
      <pre
        className="data scroll-thin mt-0.5 max-h-52 overflow-auto whitespace-pre-wrap break-words text-[10.5px] leading-[1.5]"
        style={{
          color: muted ? "var(--faint)" : "var(--muted)",
          animation: pending ? "blink 1.2s steps(2) infinite" : "none",
        }}
      >
        {body}
      </pre>
    </div>
  );
}
