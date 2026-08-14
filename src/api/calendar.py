"""
Read-only calendar endpoints for the web UI.

The agent reaches Google Calendar through tools that return prose. The
landing page needs the same events as data, so this router exposes a
narrow read-only view of them. There is deliberately no write path here:
creating, updating, and deleting events stays behind the agent, which
carries the confirmation rules described in the README.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta, timezone

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from src.tools.google_calendar import DEFAULT_TIMEZONE, fetch_raw_events

router = APIRouter(prefix="/api/calendar", tags=["calendar"])

# A fixed offset rather than ZoneInfo: Malaysia has never observed DST, the
# create/list tools already hardcode +08:00, and this avoids depending on a
# system tz database that Windows does not ship.
LOCAL_ZONE = timezone(timedelta(hours=8))

# Google's colorId, inverted from the mapping the create tool writes, so a
# category set by the agent survives the round trip back to the UI.
COLOR_ID_CATEGORY: dict[str, str] = {
    "9": "career",
    "3": "learning",
    "7": "personal",
    "5": "finance",
    "10": "health",
    "11": "travel",
    "6": "important",
}


class CalendarEventResponse(BaseModel):
    title: str
    start: str | None
    end: str | None
    all_day: bool
    location: str | None
    category: str | None


class TodayResponse(BaseModel):
    date: str
    timezone: str
    events: list[CalendarEventResponse]


def _local_time(value: dict) -> tuple[str | None, bool]:
    """Google sends either dateTime (timed) or date (all-day)."""
    if "dateTime" in value:
        moment = datetime.fromisoformat(value["dateTime"]).astimezone(LOCAL_ZONE)
        return moment.strftime("%H:%M"), False
    return None, True


def _to_response(item: dict) -> CalendarEventResponse:
    start, all_day = _local_time(item.get("start", {}))
    end, _ = _local_time(item.get("end", {}))

    return CalendarEventResponse(
        title=item.get("summary") or "Untitled event",
        start=start,
        end=end,
        all_day=all_day,
        location=item.get("location"),
        category=COLOR_ID_CATEGORY.get(item.get("colorId", "")),
    )


@router.get("/today", response_model=TodayResponse)
def today() -> TodayResponse:
    local_today = datetime.now(LOCAL_ZONE).date()
    start = datetime.combine(local_today, time.min, tzinfo=LOCAL_ZONE)
    end = start + timedelta(days=1)

    try:
        items = fetch_raw_events(start.isoformat(), end.isoformat(), max_results=20)
    except Exception as exc:
        # The token can expire or the OAuth setup may never have been run.
        # Say which, rather than returning an empty day that looks free.
        raise HTTPException(status_code=502, detail=f"Calendar unavailable: {exc}") from exc

    return TodayResponse(
        date=local_today.isoformat(),
        timezone=DEFAULT_TIMEZONE,
        events=[_to_response(item) for item in items],
    )
