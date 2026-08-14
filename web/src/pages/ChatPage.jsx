import { useCallback, useEffect, useRef, useState } from "react";

import ContextStrip from "../components/ContextStrip.jsx";
import Composer from "../components/Composer.jsx";
import Message from "../components/Message.jsx";
import RunTrace from "../components/RunTrace.jsx";
import { getConversation, streamChat } from "../lib/api.js";
import { greeting, longDate } from "../lib/format.js";
import { stepsFromEvents, stepsFromHistory } from "../lib/trace.js";

const OPENERS = [
  { label: "notes", prompt: "What did I write about the Goreal internship?" },
  { label: "todos", prompt: "What's left on my todo list today?" },
  { label: "calendar", prompt: "What does my week look like?" },
  { label: "spending", prompt: "Where did my money go this week?" },
];

/** Rebuild displayable turns from stored conversation events. */
function turnsFromHistory(events) {
  const turns = [];
  let pendingTools = [];

  for (const event of events) {
    if (event.event_type === "user_message") {
      turns.push({ role: "user", text: event.content ?? "" });
      pendingTools = [];
      continue;
    }
    if (event.event_type === "tool_call") {
      pendingTools.push(event);
      continue;
    }
    if (event.event_type === "assistant_message") {
      turns.push({ role: "assistant", text: event.content ?? "", steps: stepsFromHistory(pendingTools) });
      pendingTools = [];
      continue;
    }
    if (event.event_type === "run_error") {
      turns.push({ role: "error", text: event.content ?? "The run failed.", steps: [] });
      pendingTools = [];
    }
  }

  return turns;
}

export default function ChatPage({
  theme,
  navigate,
  conversationId,
  setConversationId,
  onThreadsChanged,
  context,
}) {
  const [draft, setDraft] = useState("");
  const [turns, setTurns] = useState([]);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState(null);

  const abortRef = useRef(null);
  const endRef = useRef(null);
  const composerRef = useRef(null);

  // A run on a fresh thread reports its new id mid-stream. That id must not be
  // mistaken for the user opening a different thread, or the history load
  // would wipe the turns the run is still writing.
  const selfAssigned = useRef(null);

  const started = turns.length > 0;

  /* --- thread loading ---------------------------------------------------- */

  useEffect(() => {
    if (!conversationId) {
      setTurns([]);
      return;
    }

    if (conversationId === selfAssigned.current) return;

    let live = true;
    getConversation(conversationId)
      .then((detail) => live && setTurns(turnsFromHistory(detail.events)))
      .catch(() => live && setError("That thread could not be opened."));

    return () => {
      live = false;
    };
  }, [conversationId]);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [turns, running]);

  useEffect(() => () => abortRef.current?.abort(), []);

  // Enter puts the cursor in the composer from anywhere on the page, so you can
  // start typing without reaching for the mouse. It stays out of the way when
  // you are already in a field, or using Enter to press something.
  useEffect(() => {
    const onKey = (event) => {
      if (event.key !== "Enter" || event.metaKey || event.ctrlKey || event.altKey) return;

      const active = document.activeElement;
      const tag = active?.tagName;
      if (
        tag === "TEXTAREA" ||
        tag === "INPUT" ||
        tag === "SELECT" ||
        tag === "BUTTON" ||
        tag === "A" ||
        active?.isContentEditable
      ) {
        return;
      }

      event.preventDefault();
      composerRef.current?.focus();
    };

    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  /* --- sending ----------------------------------------------------------- */

  const send = useCallback(
    async (text) => {
      const message = (text ?? draft).trim();
      if (message === "" || running) return;

      setDraft("");
      setError(null);
      setRunning(true);
      setTurns((current) => [
        ...current,
        { role: "user", text: message },
        { role: "assistant", text: "", events: [], steps: [], totalMs: 0 },
      ]);

      const patchLast = (change) =>
        setTurns((current) => {
          const next = [...current];
          const last = next[next.length - 1];
          next[next.length - 1] = { ...last, ...change(last) };
          return next;
        });

      const controller = new AbortController();
      abortRef.current = controller;
      let threadId = conversationId;

      try {
        await streamChat({
          message,
          conversationId: threadId,
          signal: controller.signal,
          onFrame: (name, data) => {
            if (name === "status") {
              if (data.code === "conversation_ready") {
                threadId = data.conversation_id;
                selfAssigned.current = data.conversation_id;
                setConversationId(data.conversation_id);
                return;
              }
              if (data.code === "conversation_title_updated") {
                onThreadsChanged();
                return;
              }

              patchLast((last) => {
                const events = [...last.events, data];
                return {
                  events,
                  steps: stepsFromEvents(events),
                  totalMs: data.elapsed_ms ?? last.totalMs,
                };
              });
              return;
            }

            if (name === "final") {
              patchLast((last) => ({
                text: data.reply || "The run finished without a reply.",
                totalMs: data.elapsed_ms ?? last.totalMs,
              }));
              return;
            }

            if (name === "error") {
              patchLast(() => ({ role: "error", text: data.detail ?? "The run failed." }));
            }
          },
        });
        onThreadsChanged();
      } catch (streamError) {
        if (streamError.name !== "AbortError") {
          setError(streamError.message);
          patchLast(() => ({ role: "error", text: streamError.message }));
        }
      } finally {
        setRunning(false);
        abortRef.current = null;
        // Land the cursor back in the composer so the next message needs no click.
        composerRef.current?.focus();
      }
    },
    [draft, running, conversationId, setConversationId, onThreadsChanged],
  );

  const stop = useCallback(() => {
    abortRef.current?.abort();
    setRunning(false);
  }, []);

  /* --- render ------------------------------------------------------------ */

  return (
    <>
      <main className="scroll-thin relative z-10 flex-1 overflow-y-auto">
        <div className="mx-auto w-full max-w-3xl px-4 pb-4">
          {!started ? (
            <div className="flex min-h-[calc(100dvh-9rem)] flex-col justify-center gap-6 py-8">
              <div className="animate-rise">
                <ContextStrip {...context} theme={theme} navigate={navigate} />
              </div>

              <div className="animate-rise" style={{ animationDelay: "60ms" }}>
                <p className="label mb-1.5">{longDate()}</p>
                <h1 className="display text-[34px] sm:text-[44px]">{greeting()}, zhan</h1>
              </div>

              <div className="animate-rise" style={{ animationDelay: "120ms" }}>
                <Composer
                  value={draft}
                  onChange={setDraft}
                  onSubmit={send}
                  onStop={stop}
                  running={running}
                  theme={theme}
                  fieldRef={composerRef}
                  autoFocus
                />
                <div className="mt-3 flex flex-wrap gap-2">
                  {OPENERS.map((opener) => (
                    <button
                      key={opener.label}
                      type="button"
                      onClick={() => send(opener.prompt)}
                      className="btn"
                    >
                      {opener.label}
                    </button>
                  ))}
                </div>
              </div>
            </div>
          ) : (
            <div className="space-y-5 py-6">
              {turns.map((turn, index) => (
                <div key={index}>
                  <Message role={turn.role} text={turn.text} />
                  {turn.role !== "user" && (
                    <RunTrace
                      steps={turn.steps ?? []}
                      totalMs={turn.totalMs}
                      theme={theme}
                      running={running && index === turns.length - 1}
                    />
                  )}
                </div>
              ))}
              <div ref={endRef} />
            </div>
          )}
        </div>
      </main>

      {started && (
        <div
          className="relative z-20 border-t px-4 py-3"
          style={{ borderColor: "var(--line)", background: "var(--scrim)" }}
        >
          <div className="mx-auto w-full max-w-3xl">
            {error && (
              <p className="data mb-2 text-[11px]" style={{ color: "var(--pace-over)" }}>
                {error}
              </p>
            )}
            <Composer
              value={draft}
              onChange={setDraft}
              onSubmit={send}
              onStop={stop}
              running={running}
              theme={theme}
              fieldRef={composerRef}
            />
          </div>
        </div>
      )}
    </>
  );
}
