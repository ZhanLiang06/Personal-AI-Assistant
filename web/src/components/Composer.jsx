import { useEffect, useRef } from "react";

export default function Composer({ value, onChange, onSubmit, onStop, running, theme, autoFocus }) {
  const fieldRef = useRef(null);

  // Grow with the text, up to a point, then scroll.
  useEffect(() => {
    const field = fieldRef.current;
    if (!field) return;
    field.style.height = "auto";
    field.style.height = `${Math.min(field.scrollHeight, 180)}px`;
  }, [value]);

  useEffect(() => {
    if (autoFocus) fieldRef.current?.focus();
  }, [autoFocus]);

  function handleKeyDown(event) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      onSubmit();
    }
  }

  return (
    <form
      onSubmit={(event) => {
        event.preventDefault();
        onSubmit();
      }}
      className="panel panel--ticked flex items-end gap-2 p-2"
      style={{ "--tick": "var(--accent)" }}
    >
      <textarea
        ref={fieldRef}
        rows={1}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        onKeyDown={handleKeyDown}
        placeholder={theme === "edgerunner" ? "talk to kairos…" : "ask kairos…"}
        aria-label="Message"
        className="scroll-thin flex-1 resize-none bg-transparent px-2 py-2 text-[15px] outline-none"
        style={{ color: "var(--text)" }}
      />
      {running ? (
        <button type="button" onClick={onStop} className="btn shrink-0">
          stop
        </button>
      ) : (
        <button
          type="submit"
          className="btn btn--accent shrink-0"
          disabled={value.trim() === ""}
          style={{ opacity: value.trim() === "" ? 0.45 : 1 }}
        >
          send
        </button>
      )}
    </form>
  );
}
