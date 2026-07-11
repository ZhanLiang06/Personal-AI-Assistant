import json
from datetime import datetime
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parents[2]
EVENT_LOG_PATH = BASE_DIR / "data" / "event_logs" / "agent_events.jsonl"


def append_agent_event(event: dict[str, Any]) -> None:
    EVENT_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)

    record = {
        "created_at": datetime.now().isoformat(),
        **event,
    }

    with EVENT_LOG_PATH.open("a", encoding="utf-8") as file:
        file.write(json.dumps(record, ensure_ascii=False) + "\n")