"""Todo parsing, identity, serialization, and LangChain tools."""

import hashlib
import os
import re
from datetime import date, datetime, time
from pathlib import Path
from typing import Literal, Optional

from dotenv import load_dotenv
from langchain_core.tools import tool
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
)


load_dotenv()

_vault_path = os.environ.get("VAULT_PATH")

TODO_FOLDER: Path | None = (
    Path(_vault_path) / "Journal" / "to-dos"
    if _vault_path
    else None
)


_CHECKBOX_LINE_RE = re.compile(
    r"^\s*-\s\[([ xX])\]\s(.+?)\s*$"
)
_TIME_LINE_RE = re.compile(
    r"^\s*Time:\s*(.*)$"
)
_NOTE_LINE_RE = re.compile(
    r"^\s*Note:\s*(.*)$"
)
_LEGACY_TODO_ID_LINE_RE = re.compile(
    r"^\s*<!--\s*todo-id:.*?-->\s*$",
    re.IGNORECASE,
)
_TIME_VALUE_RE = re.compile(
    r"^(?:[01]\d|2[0-3]):[0-5]\d$"
)
_SOURCE_KEY_RE = re.compile(r"^[0-9a-f]{64}$")


class TodoFormatError(ValueError):
    """Raised when a todo Markdown document has invalid structure."""


class TodoItem(BaseModel):
    """
    Structured representation of one daily todo.

    `source_key` is calculated from the date, normalized todo text,
    and duplicate occurrence. It is never written into Obsidian.
    """

    model_config = ConfigDict(extra="forbid")

    source_key: str
    duplicate_ordinal: int = Field(default=0, ge=0)

    todo_date: date
    item_text: str = Field(min_length=1)
    note: str = "-"
    completed: bool = False
    due_time: time | None = None

    @field_validator("source_key")
    @classmethod
    def validate_source_key(cls, value: str) -> str:
        cleaned = value.strip().lower()

        if not _SOURCE_KEY_RE.fullmatch(cleaned):
            raise ValueError(
                "Todo source_key must be a SHA-256 hexadecimal digest."
            )

        return cleaned

    @field_validator("item_text")
    @classmethod
    def validate_item_text(cls, value: str) -> str:
        cleaned = value.strip()

        if not cleaned:
            raise ValueError("Todo item text cannot be blank.")

        if "\n" in cleaned or "\r" in cleaned:
            raise ValueError(
                "Todo item text cannot contain line breaks."
            )

        return cleaned

    @field_validator("note")
    @classmethod
    def validate_note(cls, value: str) -> str:
        cleaned = value.strip() or "-"

        if "\n" in cleaned or "\r" in cleaned:
            raise ValueError(
                "Todo note cannot contain line breaks."
            )

        return cleaned


def normalize_todo_text(value: str) -> str:
    """
    Normalize text for identity calculation.

    Case and repeated whitespace do not affect the source key.
    """

    return " ".join(value.casefold().split())


def build_todo_source_key(
    todo_date: date,
    item_text: str,
    duplicate_ordinal: int = 0,
) -> str:
    """
    Calculate the invisible source key for a todo.

    The key intentionally excludes completion, time, and note.
    """

    if duplicate_ordinal < 0:
        raise ValueError(
            "duplicate_ordinal cannot be negative."
        )

    payload = "\0".join(
        [
            todo_date.isoformat(),
            normalize_todo_text(item_text),
            str(duplicate_ordinal),
        ]
    )

    return hashlib.sha256(
        payload.encode("utf-8")
    ).hexdigest()


def _parse_due_time(
    raw_value: str,
    line_number: int,
) -> time | None:
    value = raw_value.strip()

    if not value or value == "-":
        return None

    if not _TIME_VALUE_RE.fullmatch(value):
        raise TodoFormatError(
            f"Invalid todo time on line {line_number}: "
            f"{value!r}. Use HH:MM in 24-hour format or '-'."
        )

    return time.fromisoformat(value)


def recalculate_todo_source_keys(
    todos: list[TodoItem],
) -> list[TodoItem]:
    """
    Recalculate keys and duplicate ordinals in document order.

    Call this after adding, deleting, moving, or renaming todos.
    """

    occurrence_counts: dict[str, int] = {}
    recalculated: list[TodoItem] = []

    for todo in todos:
        normalized_text = normalize_todo_text(
            todo.item_text
        )

        duplicate_ordinal = occurrence_counts.get(
            normalized_text,
            0,
        )

        occurrence_counts[normalized_text] = (
            duplicate_ordinal + 1
        )

        source_key = build_todo_source_key(
            todo_date=todo.todo_date,
            item_text=todo.item_text,
            duplicate_ordinal=duplicate_ordinal,
        )

        recalculated.append(
            todo.model_copy(
                update={
                    "source_key": source_key,
                    "duplicate_ordinal": duplicate_ordinal,
                }
            )
        )

    return recalculated


def create_todo_item(
    *,
    todo_date: date,
    item_text: str,
    note: str = "-",
    due_time: time | None = None,
    completed: bool = False,
    existing_todos: list[TodoItem] | None = None,
) -> TodoItem:
    """
    Create a new todo with a calculated source key.

    Existing todos are used to determine the duplicate occurrence.
    """

    existing_todos = (existing_todos or [])
    normalized_text = normalize_todo_text(item_text)

    duplicate_ordinal = sum(
        1
        for todo in existing_todos
        if normalize_todo_text(todo.item_text)
        == normalized_text
    )

    source_key = build_todo_source_key(
        todo_date=todo_date,
        item_text=item_text,
        duplicate_ordinal=duplicate_ordinal,
    )

    return TodoItem(
        source_key=source_key,
        duplicate_ordinal=duplicate_ordinal,
        todo_date=todo_date,
        item_text=item_text,
        note=note,
        completed=completed,
        due_time=due_time,
    )


def parse_todo_document(
    content: str,
    todo_date: date,
) -> list[TodoItem]:
    """
    Parse a daily todo Markdown document.

    Supported legacy block:

        - [ ] Buy groceries
        Note: Buy oat milk

    Supported canonical block:

        - [ ] Submit report
        Time: 14:00
        Note: Attach PDF

    Old UUID comments are accepted during transition but ignored.
    """

    todos: list[TodoItem] = []
    occurrence_counts: dict[str, int] = {}

    lines = content.splitlines()
    index = 0

    while index < len(lines):
        checkbox_match = _CHECKBOX_LINE_RE.match(
            lines[index]
        )

        if checkbox_match is None:
            index += 1
            continue

        completed = (
            checkbox_match.group(1).lower() == "x"
        )
        item_text = checkbox_match.group(2).strip()

        due_time: time | None = None
        note = "-"

        found_time = False
        found_note = False

        metadata_index = index + 1

        while metadata_index < len(lines):
            metadata_line = lines[metadata_index]

            if not metadata_line.strip():
                break

            if _CHECKBOX_LINE_RE.match(metadata_line):
                break

            time_match = _TIME_LINE_RE.match(
                metadata_line
            )

            if time_match:
                if found_time:
                    raise TodoFormatError(
                        "Duplicate Time field on line "
                        f"{metadata_index + 1}."
                    )

                due_time = _parse_due_time(
                    time_match.group(1),
                    metadata_index + 1,
                )

                found_time = True
                metadata_index += 1
                continue

            note_match = _NOTE_LINE_RE.match(
                metadata_line
            )

            if note_match:
                if found_note:
                    raise TodoFormatError(
                        "Duplicate Note field on line "
                        f"{metadata_index + 1}."
                    )

                note = (
                    note_match.group(1).strip()
                    or "-"
                )

                found_note = True
                metadata_index += 1
                continue

            if _LEGACY_TODO_ID_LINE_RE.match(
                metadata_line
            ):
                metadata_index += 1
                continue

            # This line is not metadata for the current todo.
            break

        normalized_text = normalize_todo_text(
            item_text
        )

        duplicate_ordinal = occurrence_counts.get(
            normalized_text,
            0,
        )

        occurrence_counts[normalized_text] = (
            duplicate_ordinal + 1
        )

        source_key = build_todo_source_key(
            todo_date=todo_date,
            item_text=item_text,
            duplicate_ordinal=duplicate_ordinal,
        )

        todos.append(
            TodoItem(
                source_key=source_key,
                duplicate_ordinal=duplicate_ordinal,
                todo_date=todo_date,
                item_text=item_text,
                note=note,
                completed=completed,
                due_time=due_time,
            )
        )

        index = max(
            metadata_index,
            index + 1,
        )

    return todos


def serialize_todo_document(
    todo_date: date,
    todos: list[TodoItem],
) -> str:
    """
    Serialize todos without writing source keys into Markdown.
    """

    canonical_todos = recalculate_todo_source_keys(
        todos
    )

    blocks: list[str] = []

    for todo in canonical_todos:
        if todo.todo_date != todo_date:
            raise TodoFormatError(
                f"Todo {todo.item_text!r} belongs to "
                f"{todo.todo_date.isoformat()}, not "
                f"{todo_date.isoformat()}."
            )

        checkbox = "x" if todo.completed else " "

        time_value = (
            todo.due_time.strftime("%H:%M")
            if todo.due_time is not None
            else "-"
        )

        blocks.append(
            "\n".join(
                [
                    f"- [{checkbox}] {todo.item_text}",
                    f"Time: {time_value}",
                    f"Note: {todo.note}",
                ]
            )
        )

    header = (
        f"# {todo_date.isoformat()}\n\n"
        "## To Do"
    )

    if not blocks:
        return header + "\n"

    return (
        header
        + "\n\n"
        + "\n\n".join(blocks)
        + "\n"
    )

# ------------------------------------------------------------------
# LangChain todo tools
# ------------------------------------------------------------------


class ListDailyTodosInput(BaseModel):
    target_date: Optional[str] = Field(
        default=None,
        description=(
            "ISO date string in YYYY-MM-DD format. "
            "Defaults to today when omitted."
        ),
    )


class AddTodoItem(BaseModel):
    item_text: str = Field(
        min_length=1,
        description="Todo text to add.",
    )
    note_text: str = Field(
        default="-",
        description=(
            "Optional note for the todo. "
            "Use '-' when there is no note."
        ),
    )
    due_time: Optional[str] = Field(
        default=None,
        description=(
            "Optional due time in 24-hour HH:MM format. "
            "Use '-' or null for an untimed todo."
        ),
    )

    @field_validator("due_time")
    @classmethod
    def validate_due_time(
        cls,
        value: Optional[str],
    ) -> Optional[str]:
        return _validate_tool_time(value)


class AddDailyTodosInput(BaseModel):
    items: list[AddTodoItem] = Field(
        description=(
            "Todos to add. Compare them against the latest "
            "list_daily_todos result before calling this tool."
        ),
    )
    target_date: Optional[str] = Field(
        default=None,
        description=(
            "ISO date string in YYYY-MM-DD format. "
            "Defaults to today when omitted."
        ),
    )


class TodoReference(BaseModel):
    index: int = Field(
        ge=0,
        description=(
            "Zero-based index from the latest "
            "list_daily_todos result."
        ),
    )
    expected_text: str = Field(
        min_length=1,
        description=(
            "Full todo text from the latest "
            "list_daily_todos result."
        ),
    )


class UpdateTodoItem(TodoReference):
    operation: Literal[
        "check",
        "uncheck",
        "edit",
    ] = Field(
        description=(
            "check marks the todo completed, uncheck marks it "
            "incomplete, and edit changes its content."
        ),
    )
    new_item_text: Optional[str] = Field(
        default=None,
        description=(
            "Replacement todo text for operation='edit'. "
            "Null leaves the text unchanged."
        ),
    )
    new_note: Optional[str] = Field(
        default=None,
        description=(
            "Replacement note for operation='edit'. "
            "Null leaves the note unchanged. "
            "Use '-' to remove the note."
        ),
    )
    new_due_time: Optional[str] = Field(
        default=None,
        description=(
            "Replacement time in HH:MM format. "
            "Null leaves the time unchanged. "
            "Use '-' to remove the time."
        ),
    )

    @field_validator("new_due_time")
    @classmethod
    def validate_new_due_time(
        cls,
        value: Optional[str],
    ) -> Optional[str]:
        return _validate_tool_time(value)


class UpdateDailyTodosInput(BaseModel):
    updates: list[UpdateTodoItem] = Field(
        description=(
            "Todo mutations using index and expected_text from "
            "the latest list_daily_todos result."
        ),
    )
    target_date: Optional[str] = Field(
        default=None,
        description=(
            "ISO date string in YYYY-MM-DD format. "
            "Defaults to today when omitted."
        ),
    )


class DeleteDailyTodosInput(BaseModel):
    delete_items: list[TodoReference] = Field(
        description=(
            "Todos to delete using index and expected_text from "
            "the latest list_daily_todos result."
        ),
    )
    target_date: Optional[str] = Field(
        default=None,
        description=(
            "ISO date string in YYYY-MM-DD format. "
            "Defaults to today when omitted."
        ),
    )


def _validate_tool_time(
    value: Optional[str],
) -> Optional[str]:
    """
    Validate a tool-facing due-time value.

    None means unspecified.
    '-' means explicitly no due time.
    """

    if value is None:
        return None

    cleaned = value.strip()

    if cleaned == "-":
        return cleaned

    if not _TIME_VALUE_RE.fullmatch(cleaned):
        raise ValueError(
            "Todo time must use HH:MM in 24-hour format "
            "or '-' for no due time."
        )

    return cleaned


def _parse_tool_time(
    value: Optional[str],
) -> time | None:
    if value is None or value == "-":
        return None

    return time.fromisoformat(value)


def _resolve_target_date(
    target_date: Optional[str],
) -> date:
    if target_date is None:
        return date.today()

    try:
        return date.fromisoformat(target_date)
    except ValueError as exc:
        raise ValueError(
            "target_date must use YYYY-MM-DD format, "
            f"got {target_date!r}."
        ) from exc


def _get_todo_file_path(
    target_date: date,
) -> Path:
    if TODO_FOLDER is None:
        raise RuntimeError(
            "VAULT_PATH is not configured. Add it to the "
            "environment or .env file before using todo tools."
        )

    return TODO_FOLDER / f"{target_date.isoformat()}.md"


def _clean_item_text(
    value: str,
) -> str:
    cleaned = " ".join(
        part.strip()
        for part in value.splitlines()
    ).strip()

    if not cleaned:
        raise ValueError(
            "Todo item text cannot be blank."
        )

    return cleaned


def _clean_note(
    value: Optional[str],
) -> str:
    if value is None:
        return "-"

    cleaned = " ".join(
        part.strip()
        for part in value.splitlines()
    ).strip()

    return cleaned or "-"


def _read_todos(
    file_path: Path,
    target_date: date,
) -> list[TodoItem]:
    if not file_path.exists():
        return []

    content = file_path.read_text(
        encoding="utf-8"
    )

    return parse_todo_document(
        content=content,
        todo_date=target_date,
    )


def _write_todos(
    file_path: Path,
    target_date: date,
    todos: list[TodoItem],
) -> None:
    file_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    content = serialize_todo_document(
        todo_date=target_date,
        todos=todos,
    )

    file_path.write_text(
        content,
        encoding="utf-8",
    )


def _format_due_time(
    todo: TodoItem,
) -> str:
    if todo.due_time is None:
        return "-"

    return todo.due_time.strftime("%H:%M")


def _validate_todo_references(
    todos: list[TodoItem],
    references: list[TodoReference],
) -> list[str]:
    errors: list[str] = []
    seen_indices: set[int] = set()

    for reference in references:
        index = reference.index
        expected = reference.expected_text.strip()

        if index in seen_indices:
            errors.append(
                f"Index {index} was provided more than once."
            )

        seen_indices.add(index)

        if index >= len(todos):
            errors.append(
                f"Index {index} is out of range; "
                f"only {len(todos)} todo(s) exist."
            )
            continue

        actual = todos[index].item_text.strip()

        if expected.casefold() != actual.casefold():
            errors.append(
                f"Index {index} expected {expected!r} "
                f"but currently contains {actual!r}."
            )

    return errors


@tool(
    "list_daily_todos",
    args_schema=ListDailyTodosInput,
)
def list_daily_todos(
    target_date: Optional[str] = None,
) -> str:
    """
    List daily todos from the Obsidian vault.

    This operation is read-only and does not create files.
    """

    resolved_date = _resolve_target_date(
        target_date
    )
    file_path = _get_todo_file_path(
        resolved_date
    )
    todos = _read_todos(
        file_path,
        resolved_date,
    )

    if not todos:
        return (
            "No todos found for "
            f"{resolved_date.isoformat()}."
        )

    lines: list[str] = []

    for index, todo in enumerate(todos):
        checkbox = (
            "[x]"
            if todo.completed
            else "[ ]"
        )

        lines.append(
            f"[{index}] {checkbox} {todo.item_text} "
            f"(Time: {_format_due_time(todo)}; "
            f"Note: {todo.note})"
        )

    listed_at = (
        datetime.now()
        .astimezone()
        .isoformat(timespec="seconds")
    )

    return (
        f"Todos for {resolved_date.isoformat()} "
        f"(listed_at: {listed_at}):\n"
        + "\n".join(lines)
    )


@tool(
    "add_daily_todos",
    args_schema=AddDailyTodosInput,
)
def add_daily_todos(
    items: list[AddTodoItem],
    target_date: Optional[str] = None,
) -> str:
    """
    Add unchecked todos to a daily Obsidian todo file.

    The agent must compare against a fresh list result before
    calling this tool.
    """

    if not items:
        raise ValueError(
            "add_daily_todos requires "
            "at least one item."
        )

    resolved_date = _resolve_target_date(
        target_date
    )
    file_path = _get_todo_file_path(
        resolved_date
    )
    todos = _read_todos(
        file_path,
        resolved_date,
    )

    added_names: list[str] = []

    for item in items:
        item_text = _clean_item_text(
            item.item_text
        )
        note = _clean_note(
            item.note_text
        )

        todo = create_todo_item(
            todo_date=resolved_date,
            item_text=item_text,
            note=note,
            due_time=_parse_tool_time(
                item.due_time
            ),
            existing_todos=todos,
        )

        todos.append(todo)
        added_names.append(todo.item_text)

    _write_todos(
        file_path,
        resolved_date,
        todos,
    )

    return (
        f"Added {len(added_names)} item(s) to "
        f"{resolved_date.isoformat()}: "
        + ", ".join(added_names)
    )


@tool(
    "update_daily_todos",
    args_schema=UpdateDailyTodosInput,
)
def update_daily_todos(
    updates: list[UpdateTodoItem],
    target_date: Optional[str] = None,
) -> str:
    """
    Check, uncheck, or edit existing daily todos.

    Each update must use index and expected_text from the latest
    list_daily_todos result.
    """

    if not updates:
        raise ValueError(
            "update_daily_todos requires "
            "at least one update."
        )

    resolved_date = _resolve_target_date(
        target_date
    )
    file_path = _get_todo_file_path(
        resolved_date
    )
    todos = _read_todos(
        file_path,
        resolved_date,
    )

    errors = _validate_todo_references(
        todos,
        updates,
    )

    for update in updates:
        if update.operation != "edit":
            continue

        if (
            update.new_item_text is None
            and update.new_note is None
            and update.new_due_time is None
        ):
            errors.append(
                f"Index {update.index} has no new text, "
                "note, or due time."
            )

        if update.new_item_text is not None:
            try:
                _clean_item_text(
                    update.new_item_text
                )
            except ValueError as exc:
                errors.append(
                    f"Index {update.index}: {exc}"
                )

    if errors:
        return (
            f"Could not update todos for "
            f"{resolved_date.isoformat()}: "
            + " ".join(errors)
            + " Call list_daily_todos again before retrying."
        )

    summaries: list[str] = []

    for update in updates:
        index = update.index
        current = todos[index]

        if update.operation == "check":
            todos[index] = current.model_copy(
                update={
                    "completed": True,
                }
            )
            summaries.append(
                f"marked done: {current.item_text}"
            )
            continue

        if update.operation == "uncheck":
            todos[index] = current.model_copy(
                update={
                    "completed": False,
                }
            )
            summaries.append(
                f"marked not done: {current.item_text}"
            )
            continue

        updated_values = current.model_dump()

        if update.new_item_text is not None:
            updated_values["item_text"] = (
                _clean_item_text(
                    update.new_item_text
                )
            )

        if update.new_note is not None:
            updated_values["note"] = _clean_note(
                update.new_note
            )

        if update.new_due_time is not None:
            updated_values["due_time"] = (
                _parse_tool_time(
                    update.new_due_time
                )
            )

        todos[index] = TodoItem.model_validate(
            updated_values
        )

        summaries.append(
            f"edited: {todos[index].item_text} "
            f"(Time: {_format_due_time(todos[index])}; "
            f"Note: {todos[index].note})"
        )

    _write_todos(
        file_path,
        resolved_date,
        todos,
    )

    return (
        f"Updated on {resolved_date.isoformat()}: "
        + "; ".join(summaries)
    )


@tool(
    "delete_daily_todos",
    args_schema=DeleteDailyTodosInput,
)
def delete_daily_todos(
    delete_items: list[TodoReference],
    target_date: Optional[str] = None,
) -> str:
    """
    Delete existing daily todos.

    Each deletion must use index and expected_text from the latest
    list_daily_todos result.
    """

    if not delete_items:
        raise ValueError(
            "delete_daily_todos requires "
            "at least one todo reference."
        )

    resolved_date = _resolve_target_date(
        target_date
    )
    file_path = _get_todo_file_path(
        resolved_date
    )
    todos = _read_todos(
        file_path,
        resolved_date,
    )

    errors = _validate_todo_references(
        todos,
        delete_items,
    )

    if errors:
        return (
            f"Could not delete todos for "
            f"{resolved_date.isoformat()}: "
            + " ".join(errors)
            + " Call list_daily_todos again before retrying."
        )

    indices_to_delete = {
        item.index
        for item in delete_items
    }

    deleted_names = ", ".join(
        todos[index].item_text
        for index in sorted(indices_to_delete)
    )

    remaining_todos = [
        todo
        for index, todo in enumerate(todos)
        if index not in indices_to_delete
    ]

    _write_todos(
        file_path,
        resolved_date,
        remaining_todos,
    )

    return (
        f"Deleted from {resolved_date.isoformat()}: "
        f"{deleted_names}"
    )