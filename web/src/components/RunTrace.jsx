import { useMemo, useState } from "react";
import { PACE_COLOR, rankSteps, seconds } from "../lib/trace.js";

/**
 * The run trace, and the one place this design spends its boldness.
 *
 * The agent reports elapsed_ms and step_ms on every event, so this shows what
 * the run actually cost rather than a spinner. Under telemetry it reads as a
 * timing tower; under edgerunner the same ranking becomes a daemon stack.
 */
export default function RunTrace({ steps: raw, totalMs, theme, running }) {
  const [open, setOpen] = useState(false);
  const steps = useMemo(() => rankSteps(raw), [raw]);

  if (steps.length === 0 && !running) return null;

  const edge = theme === "edgerunner";
  const toolCount = steps.filter((step) => step.kind === "tool").length;
  const summary = edge
    ? `${toolCount} daemon${toolCount === 1 ? "" : "s"}`
    : `${toolCount} stop${toolCount === 1 ? "" : "s"}`;

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
            <li
              key={`${step.name}-${index}`}
              className="grid items-center gap-3 py-1.5"
              style={{ gridTemplateColumns: "1.5rem 1fr 4.5rem" }}
            >
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
              </span>

              <span
                className="data text-right text-[11px]"
                style={{ color: PACE_COLOR[step.pace] }}
              >
                {!step.done ? "•••" : typeof step.ms === "number" ? seconds(step.ms) : "—"}
              </span>
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}
