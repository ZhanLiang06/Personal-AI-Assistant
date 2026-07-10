import os
from dotenv import load_dotenv
from datetime import date
from pathlib import Path
from pydantic import BaseModel, Field
from typing import Optional, Literal
from langchain_core.tools import tool
from datetime import datetime
import re
from src.retrieval.search_notes import search_notes as _search_notes_impl

load_dotenv()
VAULT_ROOT = Path(os.environ.get("VAULT_PATH"))
DAILY_FOLDER = VAULT_ROOT / "Journal" / "to-dos"
_FORBIDDEN_PREFIXES = ("- [", "Note:")
 
_CHECKBOX_LINE_RE = re.compile(r"^\s*-\s\[([ xX])\]\s(.*)$")
_NOTE_LINE_RE = re.compile(r"^Note:\s?(.*)$")



# Search Notes Tool
class SearchNotesInput(BaseModel):
    query: str = Field(
        description="A clean, focused search query extracted from the user's question — not the raw user message verbatim."
    )
    k: int = Field(
        default=5,
        description="Number of top matching chunks to retrieve."
    )
    # where: Optional[dict] = Field(
    #     default=None,
    #     description="Optional metadata filter, e.g. {'folder': 'Career'}."
    # )

@tool(args_schema=SearchNotesInput)
def search_notes(query: str, k: int = 5, where: Optional[dict] = None) -> str:
    """Search the user's personal Obsidian notes relevant content"""
    results = _search_notes_impl(
        query=query,
        k=k
    )

    if not results:
        return "No matching notes found."

    formatted = []
    for r in results:
        title = r["metadata"].get("title", "unknown")
        header_title = r["metadata"].get("header_title")
        heading = f"{title} > {header_title}" if header_title else title
        formatted.append(f"[{heading}]\n{r['text']}")
    return "\n---\n".join(formatted)


"""
To do tool
 
Write tool for managing a daily to-do list inside the Obsidian vault.
Parallel to src/tools/notes_tool.py (read-only search_notes).
 
Design decisions (see project handoff notes for full rationale):
- Path is Journal/todos/{YYYY-MM-DD}.md, one file per day (ISO format, sorts
  correctly in a file browser).
- Default date is date.today(); the model may explicitly override target_date.
- Each todo item is a 2-line block:
      - [ ] item text
      Note: note text
  followed by a blank line separator. "- [ ]" / "- [x]" is real Obsidian
  checkbox syntax (space after '-', space inside brackets).
- Matching for check/uncheck/update is by 0-based index (from the most recent
  action="list" output), cross-checked against an expected substring supplied
  alongside each index. This catches stale/drifted indices (returns an error
  telling the caller to re-list) but does not itself decide what counts as
  "the right item" semantically — that judgment lives with the model.
- check/uncheck take `check_items`: a list of (index, expected_text) tuples,
  batch-capable. update takes `update_items`: a list of (index, expected_text,
  new_item_text, new_note) tuples, also batch-capable — new_item_text/new_note
  are null-able per tuple (null = leave that part unchanged), but at least one
  of the two must be set per tuple.
- Duplicate detection for "add" is NOT done in this tool at all. The model is
  expected to compare new items against its most recent action="list" output
  itself (including differently-worded items with the same meaning) and ask
  the user before adding anything that looks like a duplicate.
- Agent-level rule (enforced in REACT_SYSTEM_PROMPT, NOT in this tool):
  the agent must call action="list" immediately before every add/check/
  uncheck/update call, no exceptions — indices and duplicate judgment both
  depend on a fresh snapshot.
- delete is NOT implemented yet (deferred).
"""

# Manage To Do

class ListDailyTodosInput(BaseModel):
    target_date: Optional[str] = Field(
        default=None,
        description="ISO date string (YYYY-MM-DD). Defaults to today if omitted.",
    )


class AddTodoItem(BaseModel):
    item_text: str = Field(description="Todo text to add.")
    note_text: str = Field(
        default="-",
        description="Optional note for the todo. Use '-' if there is no note.",
    )


class AddDailyTodosInput(BaseModel):
    items: list[AddTodoItem] = Field(
        description=(
            "Todos to add. Before calling this tool, compare against the latest "
            "list_daily_todos result and ask the user for confirmation if a "
            "new item looks semantically duplicate."
        )
    )
    target_date: Optional[str] = Field(
        default=None,
        description="ISO date string (YYYY-MM-DD). Defaults to today if omitted.",
    )


class TodoReference(BaseModel):
    index: int = Field(description="0-based index from the latest list_daily_todos result.")
    expected_text: str = Field(
        description="Full todo item text from the latest list_daily_todos result."
    )


class UpdateTodoItem(TodoReference):
    operation: Literal["check", "uncheck", "edit"] = Field(
        description="check marks done, uncheck marks not done, edit changes text and/or note."
    )
    new_item_text: Optional[str] = Field(
        default=None,
        description="For operation='edit': replacement todo text. Null leaves text unchanged.",
    )
    new_note: Optional[str] = Field(
        default=None,
        description="For operation='edit': replacement note. Null leaves note unchanged.",
    )


class UpdateDailyTodosInput(BaseModel):
    updates: list[UpdateTodoItem] = Field(
        description=(
            "Existing todo mutations. Each item must use index + expected_text "
            "from the latest list_daily_todos result."
        )
    )
    target_date: Optional[str] = Field(
        default=None,
        description="ISO date string (YYYY-MM-DD). Defaults to today if omitted.",
    )


class DeleteDailyTodosInput(BaseModel):
    delete_items: list[TodoReference] = Field(
        description=(
            "Todos to delete. Each item must use index + expected_text from the "
            "latest list_daily_todos result."
        )
    )
    target_date: Optional[str] = Field(
        default=None,
        description="ISO date string (YYYY-MM-DD). Defaults to today if omitted.",
    )


class TodoBlock:
    """In-memory representation of one '- [ ] item / Note: ...' block."""
 
    __slots__ = ("checked", "item_text", "note")
 
    def __init__(self, checked: bool, item_text: str, note: str):
        self.checked = checked
        self.item_text = item_text
        self.note = note

def _resolve_target_date(target_date: Optional[str]) -> date:
    if target_date is None:
        return date.today()
    try:
        return date.fromisoformat(target_date)
    except ValueError:
        raise ValueError(
            f"target_date must be an ISO date string (YYYY-MM-DD), got: {target_date!r}"
        )

def _get_todo_file_path(target_date: date) -> Path:
    return DAILY_FOLDER / f"{target_date.isoformat()}.md"

def _forbidden_prefix_error(text: Optional[str], field_name: str) -> Optional[str]:
    """Return an error message if text starts with a structural delimiter, else None."""
    if text is None:
        return None
    stripped = text.strip()
    for prefix in _FORBIDDEN_PREFIXES:
        if stripped.startswith(prefix):
            return (
                f"{field_name} cannot start with {prefix!r} — this string is used as a "
                f"structural delimiter in the todo file and would break parsing."
            )
    return None

def _clean_free_text(text: Optional[str]) -> Optional[str]:
    """Flatten embedded newlines. Assumes _forbidden_prefix_error has already been checked."""
    if text is None:
        return text
    return text.strip().replace("\n", " ")

def _parse_todo_blocks(content: str) -> list[TodoBlock]:
    """Parse file content into a list of TodoBlock objects."""
    blocks: list[TodoBlock] = []
    lines = content.splitlines()
    i = 0
    while i < len(lines):
        checkbox_match = _CHECKBOX_LINE_RE.match(lines[i])
        if checkbox_match:
            checked = checkbox_match.group(1).lower() == "x"
            item_text = checkbox_match.group(2).strip()
            note = "-"
            if i + 1 < len(lines):
                note_match = _NOTE_LINE_RE.match(lines[i + 1])
                if note_match:
                    note = note_match.group(1).strip() or "-"
                    i += 1  # consume the paired note line too
            blocks.append(TodoBlock(checked=checked, item_text=item_text, note=note))
        i += 1
    return blocks

def _serialize_todo_blocks(target_date: date, blocks: list[TodoBlock]) -> str:
    """Serialize the full block list back into the file's text content."""
    header = f"# {target_date.isoformat()}\n\n## To Do\n\n"
    body_parts = []
    for block in blocks:
        box = "x" if block.checked else " "
        body_parts.append(f"- [{box}] {block.item_text}\nNote: {block.note}\n")
    return header + "\n".join(body_parts) + ("\n" if body_parts else "")
 
 
def _read_blocks(file_path: Path) -> list[TodoBlock]:
    if not file_path.exists():
        return []
    content = file_path.read_text(encoding="utf-8")
    return _parse_todo_blocks(content)
 
 
def _write_blocks(file_path: Path, target_date: date, blocks: list[TodoBlock]) -> None:
    # mkdir happens here — every mutating call, idempotent, never for 'list'.
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(_serialize_todo_blocks(target_date, blocks), encoding="utf-8")

def _validate_todo_references(blocks: list[TodoBlock], refs) -> list[str]:
    errors = []
    seen_indices = set()

    for ref in refs:
        idx = ref.index
        expected = ref.expected_text

        if idx in seen_indices:
            errors.append(f"index {idx} was provided more than once.")
        seen_indices.add(idx)

        if idx < 0 or idx >= len(blocks):
            errors.append(f"index {idx} is out of range (only {len(blocks)} todo(s) exist).")
        elif expected.strip().lower() != blocks[idx].item_text.strip().lower():
            errors.append(
                f"index {idx} expected text '{expected}' but found "
                f"'{blocks[idx].item_text}'."
            )

    return errors


@tool("list_daily_todos", args_schema=ListDailyTodosInput)
def list_daily_todos(target_date: Optional[str] = None) -> str:
    """List daily todos from the Obsidian vault. Read-only; does not create files."""
    resolved_date = _resolve_target_date(target_date)
    file_path = _get_todo_file_path(resolved_date)
    blocks = _read_blocks(file_path)

    if not blocks:
        return f"No todos found for {resolved_date.isoformat()}."

    lines = [
        f"[{i}] {'[x]' if b.checked else '[ ]'} {b.item_text} (Note: {b.note})"
        for i, b in enumerate(blocks)
    ]
    listed_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S GMT+8")
    return (
        f"Todos for {resolved_date.isoformat()} "
        f"(listed_at: {listed_at}):\n"
        + "\n".join(lines)
    )


@tool("add_daily_todos", args_schema=AddDailyTodosInput)
def add_daily_todos(
    items: list[AddTodoItem],
    target_date: Optional[str] = None,
) -> str:
    """
    Add new unchecked daily todos. The agent must call list_daily_todos first
    and handle semantic duplicate confirmation before using this tool.
    """
    resolved_date = _resolve_target_date(target_date)
    file_path = _get_todo_file_path(resolved_date)
    blocks = _read_blocks(file_path)

    if not items:
        raise ValueError("add_daily_todos requires a non-empty items list.")

    errors = []
    for item in items:
        raw_item_text = item.item_text
        raw_note = item.note_text or "-"

        err = _forbidden_prefix_error(raw_item_text, "item_text")
        if err:
            errors.append(err)
        err = _forbidden_prefix_error(raw_note, "note_text")
        if err:
            errors.append(err)

    if errors:
        return f"Could not add to {resolved_date.isoformat()} - " + " ".join(errors)

    to_add = []
    for item in items:
        clean_item_text = _clean_free_text(item.item_text)
        clean_note = _clean_free_text(item.note_text or "-")
        to_add.append((clean_item_text, clean_note))

    for clean_item_text, clean_note in to_add:
        blocks.append(TodoBlock(checked=False, item_text=clean_item_text, note=clean_note))

    _write_blocks(file_path, resolved_date, blocks)
    added_names = ", ".join(t[0] for t in to_add)
    return f"Added {len(to_add)} item(s) to {resolved_date.isoformat()}: {added_names}"


@tool("update_daily_todos", args_schema=UpdateDailyTodosInput)
def update_daily_todos(
    updates: list[UpdateTodoItem],
    target_date: Optional[str] = None,
) -> str:
    """
    Check, uncheck, or edit existing daily todos. Each update must use
    index + expected_text from the latest list_daily_todos result.
    """
    resolved_date = _resolve_target_date(target_date)
    file_path = _get_todo_file_path(resolved_date)
    blocks = _read_blocks(file_path)

    if not updates:
        raise ValueError("update_daily_todos requires a non-empty updates list.")

    errors = _validate_todo_references(blocks, updates)
    for update in updates:
        idx = update.index
        operation = update.operation
        new_text = update.new_item_text
        new_note = update.new_note

        if operation == "edit":
            if new_text is None and new_note is None:
                errors.append(
                    f"index {idx} has neither new_item_text nor new_note - nothing to edit."
                )
            err = _forbidden_prefix_error(new_text, "new_item_text")
            if err:
                errors.append(f"index {idx}: {err}")
            err = _forbidden_prefix_error(new_note, "new_note")
            if err:
                errors.append(f"index {idx}: {err}")

    if errors:
        return (
            f"Could not update on {resolved_date.isoformat()} - indices look stale: "
            + " ".join(errors)
            + " Call list_daily_todos again to get fresh indices before retrying."
        )

    summaries = []
    for update in updates:
        idx = update.index
        operation = update.operation
        new_text = update.new_item_text
        new_note = update.new_note

        if operation == "check":
            blocks[idx].checked = True
            summaries.append(f"marked done: {blocks[idx].item_text}")
        elif operation == "uncheck":
            blocks[idx].checked = False
            summaries.append(f"marked not done: {blocks[idx].item_text}")
        elif operation == "edit":
            if new_text is not None:
                blocks[idx].item_text = _clean_free_text(new_text)
            if new_note is not None:
                blocks[idx].note = _clean_free_text(new_note)
            summaries.append(f"edited: {blocks[idx].item_text} (Note: {blocks[idx].note})")

    _write_blocks(file_path, resolved_date, blocks)
    return f"Updated on {resolved_date.isoformat()}: " + "; ".join(summaries)


@tool("delete_daily_todos", args_schema=DeleteDailyTodosInput)
def delete_daily_todos(
    delete_items: list[TodoReference],
    target_date: Optional[str] = None,
) -> str:
    """
    Delete existing daily todos. Each delete item must use index + expected_text
    from the latest list_daily_todos result.
    """
    resolved_date = _resolve_target_date(target_date)
    file_path = _get_todo_file_path(resolved_date)
    blocks = _read_blocks(file_path)

    if not delete_items:
        raise ValueError("delete_daily_todos requires a non-empty delete_items list.")

    errors = _validate_todo_references(blocks, delete_items)
    if errors:
        return (
            f"Could not delete on {resolved_date.isoformat()} - indices look stale: "
            + " ".join(errors)
            + " Call list_daily_todos again to get fresh indices before retrying."
        )

    indices_to_delete = {item.index for item in delete_items}
    deleted_names = ", ".join(blocks[idx].item_text for idx in sorted(indices_to_delete))
    blocks = [block for i, block in enumerate(blocks) if i not in indices_to_delete]

    _write_blocks(file_path, resolved_date, blocks)
    return f"Deleted from {resolved_date.isoformat()}: {deleted_names}"

# -----------------------------------------   Old codes  -----------------------------------------
class ManageTodoInput(BaseModel):
    action: Literal["add", "check", "uncheck", "update", "delete", "list"] = Field(
        ..., description="Which operation to perform."
    )
    items: Optional[list[tuple[str, str]]] = Field(
        default=None,
        description=(
            "For action='add' only: list of (item_text, note_text) tuples. "
            "Use '-' for note_text if there is no note. Before adding, compare "
            "against the most recent action='list' output yourself (including "
            "differently-worded items with the same meaning) and ask the user "
            "to confirm before calling add if something looks like a duplicate."
        ),
    )
    check_items: Optional[list[tuple[int, str]]] = Field(
        default=None,
        description=(
            "For action='check'/'uncheck': list of (index, expected_text) tuples, "
            "each from the most recent action='list' output. Supports multiple "
            "items per call. expected_text verifies the index still points at "
            "the item you think it does before anything is mutated."
        ),
    )
    update_items: Optional[list[tuple[int, str, Optional[str], Optional[str]]]] = Field(
        default=None,
        description=(
            "For action='update': list of (index, expected_text, new_item_text, "
            "new_note) tuples, each from the most recent action='list' output. "
            "expected_text verifies the index is still valid. Use null for "
            "new_item_text or new_note to leave that part unchanged — but at "
            "least one of the two must be non-null per tuple."
        ),
    )
    delete_items: Optional[list[tuple[int, str]]] = Field(
        default=None,
        description=(
            "For action='delete': list of (index, expected_text) tuples, each from "
            "the most recent action='list' output. Supports deleting multiple items "
            "per call. expected_text verifies the index still points at the item you "
            "intend to delete before anything is removed."
        ),
    )
    target_date: Optional[str] = Field(
        default=None,
        description=(
            "ISO date string (YYYY-MM-DD) to target a specific day's todo file. "
            "Defaults to today if omitted."
        ),
    )

@tool("manage_daily_todos", args_schema=ManageTodoInput)
def manage_daily_todos(
    action: Literal["add", "check", "uncheck", "update", "delete", "list"],
    items: Optional[list[tuple[str, str]]] = None,
    check_items: Optional[list[tuple[int, str]]] = None,
    update_items: Optional[list[tuple[int, str, Optional[str], Optional[str]]]] = None,
    delete_items: Optional[list[tuple[int, str]]] = None,
    target_date: Optional[str] = None,
) -> str:
    """
    Manage a daily to-do list stored in the Obsidian vault at Journal/todos/{date}.md.
 
    IMPORTANT (agent-level rule, enforced by system prompt, not by this tool):
    always call action='list' before calling action='add' or action='update',
    so the current state can be relayed to the user before mutating it.
    """
    resolved_date = _resolve_target_date(target_date)
    file_path = _get_todo_file_path(resolved_date)
    blocks = _read_blocks(file_path)
 
    if action == "list":
        if not blocks:
            return f"No todos found for {resolved_date.isoformat()}."
        lines = [
            f"[{i}] {'[x]' if b.checked else '[ ]'} {b.item_text} (Note: {b.note})"
            for i, b in enumerate(blocks)
        ]
        return f"Todos for {resolved_date.isoformat()}:\n" + "\n".join(lines)
 
    if action == "add":
        if not items:
            raise ValueError(
                "action='add' requires a non-empty 'items' list of (item_text, note_text) tuples."
            )
        
        errors = []
        for raw_item_text, raw_note in items:
            err = _forbidden_prefix_error(raw_item_text, "item_text")
            if err:
                errors.append(err)
            err = _forbidden_prefix_error(raw_note, "note_text")
            if err:
                errors.append(err)
 
        if errors:
            return f"Could not add to {resolved_date.isoformat()} — " + " ".join(errors)

        to_add = []
        for raw_item_text, raw_note in items:
            clean_item_text = _clean_free_text(raw_item_text)
            clean_note = _clean_free_text(raw_note) if raw_note else "-"
            to_add.append((clean_item_text, clean_note))
 
        for clean_item_text, clean_note in to_add:
            blocks.append(TodoBlock(checked=False, item_text=clean_item_text, note=clean_note))
 
        _write_blocks(file_path, resolved_date, blocks)
        added_names = ", ".join(t[0] for t in to_add)
        return f"Added {len(to_add)} item(s) to {resolved_date.isoformat()}: {added_names}"
 
    if action in ("check", "uncheck"):
        if not check_items:
            raise ValueError(
                f"action={action!r} requires a non-empty 'check_items' list of "
                f"(index, expected_text) tuples."
            )
        
        errors = []
        for idx, expected in check_items:
            if idx < 0 or idx >= len(blocks):
                errors.append(f"index {idx} is out of range (only {len(blocks)} todo(s) exist).")
            elif expected.lower() not in blocks[idx].item_text.lower():
                errors.append(
                    f"index {idx} expected text containing '{expected}' but found "
                    f"'{blocks[idx].item_text}'."
                )
        
        if errors:
            return (
                f"Could not {action} on {resolved_date.isoformat()} — indices look stale: "
                + " ".join(errors)
                + " Call action='list' again to get fresh indices before retrying."
            )
        
        new_checked_value = action == "check"
        for idx, _ in check_items:
            blocks[idx].checked = new_checked_value
 
        _write_blocks(file_path, resolved_date, blocks)
        changed_names = ", ".join(blocks[idx].item_text for idx, _ in check_items)
        state_word = "done" if new_checked_value else "not done"
        return f"Marked as {state_word} on {resolved_date.isoformat()}: {changed_names}"
    
    if action == "update":
        if not update_items:
            raise ValueError(
                "action='update' requires a non-empty 'update_items' list of "
                "(index, expected_text, new_item_text, new_note) tuples."
            )
        errors = []
        for idx, expected, new_text, new_note in update_items:
            if idx < 0 or idx >= len(blocks):
                errors.append(f"index {idx} is out of range (only {len(blocks)} todo(s) exist).")
            elif expected.lower() not in blocks[idx].item_text.lower():
                errors.append(
                    f"index {idx} expected text containing '{expected}' but found "
                    f"'{blocks[idx].item_text}'."
                )
            elif new_text is None and new_note is None:
                errors.append(f"index {idx} has neither a new_item_text nor a new_note — nothing to update for it.")
            else:
                err = _forbidden_prefix_error(new_text, "new_item_text")
                if err:
                    errors.append(f"index {idx}: {err}")
                err = _forbidden_prefix_error(new_note, "new_note")
                if err:
                    errors.append(f"index {idx}: {err}")
        
        if errors:
            return (
                f"Could not update on {resolved_date.isoformat()} — "
                + " ".join(errors)
                + " Call action='list' again to get fresh indices before retrying."
            )
        
        updated_summaries = []
        for idx, expected, new_text, new_note in update_items:
            if new_text is not None:
                blocks[idx].item_text = _clean_free_text(new_text)
            if new_note is not None:
                blocks[idx].note = _clean_free_text(new_note)
            updated_summaries.append(f"'{blocks[idx].item_text}' (Note: {blocks[idx].note})")

        _write_blocks(file_path, resolved_date, blocks)
        return f"Updated on {resolved_date.isoformat()}: " + "; ".join(updated_summaries)
    
    if action == "delete":
        if not delete_items:
            raise ValueError(
                "action='delete' requires a non-empty 'delete_items' list of "
                "(index, expected_text) tuples."
            )

        errors = []
        seen_indices = set()

        for idx, expected in delete_items:
            if idx in seen_indices:
                errors.append(f"index {idx} was provided more than once.")
            seen_indices.add(idx)

            if idx < 0 or idx >= len(blocks):
                errors.append(f"index {idx} is out of range (only {len(blocks)} todo(s) exist).")
            elif expected.strip().lower() != blocks[idx].item_text.strip().lower():
                errors.append(
                    f"index {idx} expected text '{expected}' but found "
                    f"'{blocks[idx].item_text}'."
                )

        if errors:
            return (
                f"Could not delete on {resolved_date.isoformat()} — indices look stale: "
                + " ".join(errors)
                + " Call action='list' again to get fresh indices before retrying."
            )

        indices_to_delete = {idx for idx, _ in delete_items}
        deleted_names = ", ".join(blocks[idx].item_text for idx in sorted(indices_to_delete))
        blocks = [block for i, block in enumerate(blocks) if i not in indices_to_delete]

        _write_blocks(file_path, resolved_date, blocks)
        return f"Deleted from {resolved_date.isoformat()}: {deleted_names}"
 
    raise ValueError(f"Unknown action: {action!r}")

# -------------------------------------   End of Old codes  --------------------------------------