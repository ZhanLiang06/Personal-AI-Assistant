import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

BASE_DIR = Path(__file__).resolve().parents[2]
DB_PATH = BASE_DIR / "data" / "conversations.sqlite3"

ConversationEventType = Literal[
    "user_message",
    "assistant_message",
    "tool_call",
    "tool_result",
    "confirmation_requested",
    "confirmation_resolved",
    "run_error",
]

@dataclass
class ConversationEvent:
    id:int
    conversation_id: str
    event_type: ConversationEventType
    content: str | None
    runtime_context: str | None
    tool_name: str | None
    tool_call_id: str | None
    tool_call_batch_id: str | None
    tool_args_json: str | None
    tool_result: str | None
    tool_result_preview: str | None
    status: str | None
    run_id: str | None
    created_at: str

@dataclass
class ConversationSummary:
    id: int
    conversation_id: str
    summary: str
    covers_through_event_id: int
    created_at: str

@dataclass
class Conversation:
    id: str
    title: str
    created_at: str
    updated_at: str

def utc_now() -> str:
    """Returns the current UTC time as an ISO 8601 string."""
    return datetime.now(timezone.utc).isoformat()

def connect() -> sqlite3.Connection:
    """Connects to the SQLite database, creating it if it doesn't exist."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_conversation_db() -> None:
    with connect() as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS conversations (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS conversation_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                content TEXT,
                runtime_context TEXT,
                tool_name TEXT,
                tool_call_id TEXT,
                tool_call_batch_id TEXT,
                tool_args_json TEXT,
                tool_result TEXT,
                tool_result_preview TEXT,
                status TEXT,
                run_id TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (conversation_id) REFERENCES conversations(id)
            );

            CREATE TABLE IF NOT EXISTS conversation_summaries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id TEXT NOT NULL,
                summary TEXT NOT NULL,
                covers_through_event_id INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (conversation_id) REFERENCES conversations(id)
            );

            CREATE INDEX IF NOT EXISTS idx_conversation_events_conversation_id_id
            ON conversation_events (conversation_id, id);

            CREATE INDEX IF NOT EXISTS idx_conversation_summaries_conversation_id_id
            ON conversation_summaries (conversation_id, id);
            """
        )
        _ensure_column(
            connection,
            table_name="conversation_events",
            column_name="tool_call_batch_id",
            column_definition="TEXT",
        )
        _ensure_column(
            connection,
            table_name="conversation_events",
            column_name="runtime_context",
            column_definition="TEXT",
        )


def create_conversation(title: str = "New conversation") -> str:
    conversation_id = str(uuid4())
    now = utc_now()

    with connect() as connection:
        connection.execute(
            """
            INSERT INTO conversations (id, title, created_at, updated_at)
            VALUES (?, ?, ?, ?)
            """,
            (conversation_id, title, now, now),
        )

    return conversation_id

def add_conversation_event(
    conversation_id: str,
    event_type: ConversationEventType,
    content: str | None = None,
    runtime_context: str | None = None,
    tool_name: str | None = None,
    tool_call_id: str | None = None,
    tool_call_batch_id: str | None = None,
    tool_args: dict[str, Any] | None = None,
    tool_result: str | None = None,
    tool_result_preview: str | None = None,
    status: str | None = None,
    run_id: str | None = None,
) -> int:
    now = utc_now()
    tool_args_json = json.dumps(tool_args, ensure_ascii=False) if tool_args is not None else None

    with connect() as connection:
        cursor = connection.execute(
            """
            INSERT INTO conversation_events (
                conversation_id,
                event_type,
                content,
                runtime_context,
                tool_name,
                tool_call_id,
                tool_call_batch_id,
                tool_args_json,
                tool_result,
                tool_result_preview,
                status,
                run_id,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                conversation_id,
                event_type,
                content,
                runtime_context,
                tool_name,
                tool_call_id,
                tool_call_batch_id,
                tool_args_json,
                tool_result,
                tool_result_preview,
                status,
                run_id,
                now,
            ),
        )

        connection.execute(
            """
            UPDATE conversations
            SET updated_at = ?
            WHERE id = ?
            """,
            (now, conversation_id),
        )

    return int(cursor.lastrowid)


def add_user_message(
    conversation_id: str,
    content: str,
    runtime_context: str | None = None,
) -> int:
    return add_conversation_event(
        conversation_id=conversation_id,
        event_type="user_message",
        content=content,
        runtime_context=runtime_context,
    )

def add_assistant_message(conversation_id: str, content: str, run_id: str | None = None) -> int:
    return add_conversation_event(
        conversation_id=conversation_id,
        event_type="assistant_message",
        content=content,
        run_id=run_id,
    )

def add_tool_call(
    conversation_id: str,
    tool_name: str,
    tool_call_id: str | None,
    tool_call_batch_id: str | None,
    tool_args: dict[str, Any],
    run_id: str | None,
) -> int:
    return add_conversation_event(
        conversation_id=conversation_id,
        event_type="tool_call",
        tool_name=tool_name,
        tool_call_id=tool_call_id,
        tool_call_batch_id=tool_call_batch_id,
        tool_args=tool_args,
        status="requested",
        run_id=run_id,
    )

def add_tool_result(
    conversation_id: str,
    tool_name: str,
    tool_call_id: str | None,
    tool_call_batch_id: str | None,
    tool_result: str | None,
    result_preview: str,
    run_id: str | None,
    status: str = "success",
) -> int:
    return add_conversation_event(
        conversation_id=conversation_id,
        event_type="tool_result",
        tool_name=tool_name,
        tool_call_id=tool_call_id,
        tool_call_batch_id=tool_call_batch_id,
        tool_result=tool_result,
        tool_result_preview=result_preview,
        status=status,
        run_id=run_id,
    )

def save_summary(
    conversation_id: str,
    summary: str,
    covers_through_event_id: int,
) -> int:
    with connect() as connection:
        cursor = connection.execute(
            """
            INSERT INTO conversation_summaries (
                conversation_id,
                summary,
                covers_through_event_id,
                created_at
            )
            VALUES (?, ?, ?, ?)
            """,
            (conversation_id, summary, covers_through_event_id, utc_now()),
        )

    return int(cursor.lastrowid)

def get_latest_summary(conversation_id: str) -> ConversationSummary | None:
    with connect() as connection:
        row = connection.execute(
            """
            SELECT id, conversation_id, summary, covers_through_event_id, created_at
            FROM conversation_summaries
            WHERE conversation_id = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (conversation_id,),
        ).fetchone()

    if row is None:
        return None

    return ConversationSummary(
        id=row["id"],
        conversation_id=row["conversation_id"],
        summary=row["summary"],
        covers_through_event_id=row["covers_through_event_id"],
        created_at=row["created_at"],
    )

def get_conversation(conversation_id: str) -> Conversation | None:
    with connect() as connection:
        row = connection.execute(
            """
            SELECT id, title, created_at, updated_at
            FROM conversations
            WHERE id = ?
            """,
            (conversation_id,),
        ).fetchone()

    if row is None:
        return None

    return Conversation(
        id=row["id"],
        title=row["title"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )

def get_conversation_events(conversation_id: str) -> list[ConversationEvent]:
    with connect() as connection:
        rows = connection.execute(
            """
            SELECT *
            FROM conversation_events
            WHERE conversation_id = ?
            ORDER BY id ASC
            """,
            (conversation_id,),
        ).fetchall()

    return [_row_to_event(row) for row in rows]

def get_events_after(
    conversation_id: str,
    event_id: int,
) -> list[ConversationEvent]:
    with connect() as connection:
        rows = connection.execute(
            """
            SELECT *
            FROM conversation_events
            WHERE conversation_id = ?
              AND id > ?
            ORDER BY id ASC
            """,
            (conversation_id, event_id),
        ).fetchall()

    return [_row_to_event(row) for row in rows]

#helper
def _row_to_event(row: sqlite3.Row) -> ConversationEvent:
    return ConversationEvent(
        id=row["id"],
        conversation_id=row["conversation_id"],
        event_type=row["event_type"],
        content=row["content"],
        runtime_context=row["runtime_context"],
        tool_name=row["tool_name"],
        tool_call_id=row["tool_call_id"],
        tool_call_batch_id=row["tool_call_batch_id"],
        tool_args_json=row["tool_args_json"],
        tool_result=row["tool_result"],
        tool_result_preview=row["tool_result_preview"],
        status=row["status"],
        run_id=row["run_id"],
        created_at=row["created_at"],
    )

def add_run_error(
    conversation_id: str,
    error_message: str,
    run_id: str | None = None,
) -> int:
    return add_conversation_event(
        conversation_id=conversation_id,
        event_type="run_error",
        content=error_message,
        status="failed",
        run_id=run_id,
    )

def delete_conversation(conversation_id: str) -> None:
    with connect() as connection:
        connection.execute(
            "DELETE FROM conversation_events WHERE conversation_id = ?",
            (conversation_id,),
        )
        connection.execute(
            "DELETE FROM conversation_summaries WHERE conversation_id = ?",
            (conversation_id,),
        )
        connection.execute(
            "DELETE FROM conversations WHERE id = ?",
            (conversation_id,),
        )   

def list_conversations() -> list[Conversation]:
    with connect() as connection:
        rows = connection.execute(
            """
            SELECT id, title, created_at, updated_at
            FROM conversations
            ORDER BY updated_at DESC
            """
        ).fetchall()

    return [
        Conversation(
            id=row["id"],
            title=row["title"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
        for row in rows
    ]

def _ensure_column(
    connection: sqlite3.Connection,
    table_name: str,
    column_name: str,
    column_definition: str,
) -> None:
    columns = connection.execute(f"PRAGMA table_info({table_name})").fetchall()
    existing_column_names = {column["name"] for column in columns}
    if column_name in existing_column_names:
        return

    connection.execute(
        f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_definition}"
    )
