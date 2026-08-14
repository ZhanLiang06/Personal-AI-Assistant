import { useMemo } from "react";
import DOMPurify from "dompurify";
import { marked } from "marked";

marked.setOptions({ breaks: true, gfm: true });

export default function Message({ role, text }) {
  const html = useMemo(
    () => (role === "assistant" ? DOMPurify.sanitize(marked.parse(text || "")) : null),
    [role, text],
  );

  if (role === "user") {
    return (
      <div className="animate-rise flex justify-end">
        <p
          className="panel max-w-[80%] px-3.5 py-2.5 text-[15px]"
          style={{ background: "var(--surface-2)" }}
        >
          {text}
        </p>
      </div>
    );
  }

  if (role === "error") {
    return (
      <div
        className="panel panel--ticked animate-rise px-3.5 py-2.5 text-[14px]"
        style={{ "--tick": "var(--pace-over)", color: "var(--pace-over)" }}
        role="alert"
      >
        {text}
      </div>
    );
  }

  // An assistant turn with no text yet is a run still in flight. The trace
  // below it is already reporting, so this stays empty rather than showing a
  // placeholder that competes with it.
  if (!text) return null;

  return (
    <div className="animate-rise">
      <div className="prose-kairos text-[15px]" dangerouslySetInnerHTML={{ __html: html }} />
    </div>
  );
}
