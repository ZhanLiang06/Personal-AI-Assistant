import os
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Literal, Optional

from dotenv import load_dotenv
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from langchain_core.tools import tool
from pydantic import BaseModel, Field, field_validator, model_validator
from rich import print as rprint

load_dotenv()

DEFAULT_TIMEZONE = "Asia/Kuala_Lumpur"
SCOPES = ["https://www.googleapis.com/auth/calendar.events"]
TOKEN_PATH = Path(os.environ.get("GOOGLE_CALENDAR_TOKEN_PATH", "data/google_calendar_token.json"))
CALENDAR_ID = os.environ.get("GOOGLE_CALENDAR_ID", "primary")

CalendarCategory = Literal[
    "career",
    "learning",
    "personal",
    "finance",
    "health",
    "travel",
    "important",
]

CATEGORY_COLOR_ID: dict[str, str] = {
    "career": "9",     # blue
    "learning": "3",   # purple
    "personal": "7",   # cyan
    "finance": "5",    # yellow
    "health": "10",    # green
    "travel": "11",    # red
    "important": "6",  # orange
}


class CalendarEventDraft(BaseModel):
    title: str = Field(description="Event title.")
    event_date: str = Field(description="Event date in YYYY-MM-DD format.")
    start_time: Optional[str] = Field(
        default=None,
        description="Start time in HH:MM 24-hour format or ISO datetime. Omit for an all-day event.",
    )
    end_time: Optional[str] = Field(
        default=None,
        description="End time in HH:MM 24-hour format or ISO datetime. If omitted with start_time, defaults to 1 hour.",
    )
    timezone: str = Field(default=DEFAULT_TIMEZONE)
    category: CalendarCategory = Field(default="important")
    description: Optional[str] = None
    location: Optional[str] = None

    @field_validator("title", "event_date", "timezone")
    @classmethod
    def not_blank(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("Field cannot be blank.")
        return value.strip()


class CreateGoogleCalendarEventInput(CalendarEventDraft):
    pass


class CreateGoogleCalendarEventsInput(BaseModel):
    events: list[CalendarEventDraft] = Field(
        description="One or more Google Calendar events to create."
    )
    confirmed_by_user: bool = Field(
        default=False,
        description="Required when creating 3 or more events in one tool call.",
    )

    @model_validator(mode="after")
    def validate_batch_confirmation(self):
        if len(self.events) == 0:
            raise ValueError("At least one event is required.")
        if len(self.events) >= 3 and not self.confirmed_by_user:
            raise ValueError("Creating 3 or more calendar events requires explicit user confirmation.")
        return self


class ListGoogleCalendarEventsInput(BaseModel):
    time_min: Optional[str] = Field(
        default=None,
        description="Lower time bound as YYYY-MM-DD or ISO datetime. Defaults to now.",
    )
    time_max: Optional[str] = Field(
        default=None,
        description="Upper time bound as YYYY-MM-DD or ISO datetime. Optional.",
    )
    query: Optional[str] = Field(
        default=None,
        description="Free-text search query for matching calendar events.",
    )
    max_results: int = Field(
        default=10,
        ge=1,
        le=10,
        description="Maximum events to return.",
    )

class UpdateGoogleCalendarEventPatch(BaseModel):
    event_id: str = Field(description="Google Calendar event ID from latest list_google_calendar_events result.")
    expected_title: str = Field(description="Current event title from latest list_google_calendar_events result.")

    new_title: Optional[str] = None
    new_event_date: Optional[str] = Field(
        default=None,
        description="New event date in YYYY-MM-DD format.",
    )
    new_start_time: Optional[str] = Field(
        default=None,
        description="New start time in HH:MM 24-hour format or ISO datetime. Omit with new_event_date to keep all-day events all-day or keep timed events at their current start time.",
    )
    new_end_time: Optional[str] = Field(
        default=None,
        description="New end time in HH:MM 24-hour format or ISO datetime. If omitted with new_start_time, defaults to 1 hour.",
    )
    new_timezone: str = Field(default=DEFAULT_TIMEZONE)
    new_category: Optional[CalendarCategory] = None
    new_description: Optional[str] = None
    new_location: Optional[str] = None

    @field_validator("event_id", "expected_title")
    @classmethod
    def not_blank(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("Field cannot be blank.")
        return value.strip()


class UpdateGoogleCalendarEventsInput(BaseModel):
    updates: list[UpdateGoogleCalendarEventPatch]
    confirmed_by_user: bool = Field(
        default=False,
        description="Required when updating 3 or more events in one tool call.",
    )

    @model_validator(mode="after")
    def validate_update_confirmation(self):
        if len(self.updates) == 0:
            raise ValueError("At least one update is required.")
        if len(self.updates) >= 3 and not self.confirmed_by_user:
            raise ValueError("Updating 3 or more calendar events requires explicit user confirmation.")
        return self


class DeleteGoogleCalendarEventRef(BaseModel):
    event_id: str = Field(description="Google Calendar event ID from latest list_google_calendar_events result.")
    expected_title: str = Field(description="Current event title from latest list_google_calendar_events result.")

    @field_validator("event_id", "expected_title")
    @classmethod
    def not_blank(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("Field cannot be blank.")
        return value.strip()


class DeleteGoogleCalendarEventsInput(BaseModel):
    delete_events: list[DeleteGoogleCalendarEventRef]
    confirmed_by_user: bool = Field(
        default=False,
        description="Required for all Google Calendar deletions.",
    )

    @model_validator(mode="after")
    def validate_delete_confirmation(self):
        if len(self.delete_events) == 0:
            raise ValueError("At least one event is required.")
        if not self.confirmed_by_user:
            raise ValueError("Deleting calendar events requires explicit user confirmation.")
        return self

def _load_credentials() -> Credentials:
    if not TOKEN_PATH.exists():
        raise RuntimeError(
            f"Google Calendar token not found at {TOKEN_PATH}. "
            "Run the OAuth setup script first."
        )

    creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), SCOPES)

    if creds.expired and creds.refresh_token:
        creds.refresh(Request())

    if not creds.valid:
        raise RuntimeError("Google Calendar credentials are invalid. Re-run OAuth setup.")

    return creds


def _calendar_service():
    creds = _load_credentials()
    return build("calendar", "v3", credentials=creds)


def _parse_date(value: str) -> date:
    return date.fromisoformat(value)


def _parse_time(value: str) -> time:
    cleaned = value.strip().replace("Z", "+00:00")
    if "T" in cleaned:
        return datetime.fromisoformat(cleaned).time().replace(tzinfo=None)
    return time.fromisoformat(cleaned)


def _to_rfc3339_bound(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None

    value = value.strip()
    if "T" in value:
        return value

    parsed_date = _parse_date(value)
    return datetime.combine(parsed_date, time.min).isoformat() + "+08:00"


def _build_event_body(event: CalendarEventDraft) -> dict:
    resolved_date = _parse_date(event.event_date)

    body = {
        "summary": event.title,
        "colorId": CATEGORY_COLOR_ID[event.category],
    }

    if event.description:
        body["description"] = event.description

    if event.location:
        body["location"] = event.location

    if event.start_time is None:
        end_date = resolved_date + timedelta(days=1)
        body["start"] = {"date": resolved_date.isoformat()}
        body["end"] = {"date": end_date.isoformat()}
        return body

    start_dt = datetime.combine(resolved_date, _parse_time(event.start_time))

    if event.end_time is None:
        end_dt = start_dt + timedelta(hours=1)
    else:
        end_dt = datetime.combine(resolved_date, _parse_time(event.end_time))

    if end_dt <= start_dt:
        raise ValueError("end_time must be after start_time.")

    body["start"] = {
        "dateTime": start_dt.isoformat(),
        "timeZone": event.timezone,
    }
    body["end"] = {
        "dateTime": end_dt.isoformat(),
        "timeZone": event.timezone,
    }
    return body


def _format_event_line(index: int, event: dict) -> str:
    title = event.get("summary", "(no title)")
    event_id = event.get("id", "")
    color_id = event.get("colorId", "default")
    html_link = event.get("htmlLink", "")

    start = event.get("start", {})
    end = event.get("end", {})

    if "date" in start:
        when = f"all-day {start.get('date')}"
    else:
        when = f"{start.get('dateTime')} -> {end.get('dateTime')}"

    return (
        f"[{index}] {title}\n"
        f"id: {event_id}\n"
        f"when: {when}\n"
        f"colorId: {color_id}\n"
        f"link: {html_link}"
    )

def _title_matches(expected_title: str, actual_title: str) -> bool:
    return expected_title.strip().lower() == actual_title.strip().lower()


def _event_start_date(event: dict) -> date:
    start = event.get("start", {})
    if "date" in start:
        return _parse_date(start["date"])
    date_time = start.get("dateTime", "")
    return datetime.fromisoformat(date_time.replace("Z", "+00:00")).date()


def _event_is_all_day(event: dict) -> bool:
    return "date" in event.get("start", {})


def _event_start_time(event: dict) -> time:
    start = event.get("start", {})
    date_time = start.get("dateTime")
    if not date_time:
        raise ValueError("Cannot reuse a start time from an all-day event.")
    return datetime.fromisoformat(date_time.replace("Z", "+00:00")).time().replace(tzinfo=None)


def _event_end_time(event: dict) -> time:
    end = event.get("end", {})
    date_time = end.get("dateTime")
    if not date_time:
        raise ValueError("Cannot reuse an end time from an all-day event.")
    return datetime.fromisoformat(date_time.replace("Z", "+00:00")).time().replace(tzinfo=None)


def _build_update_body(current_event: dict, update: UpdateGoogleCalendarEventPatch) -> dict:
    body = {}

    if update.new_title is not None:
        body["summary"] = update.new_title

    if update.new_category is not None:
        body["colorId"] = CATEGORY_COLOR_ID[update.new_category]

    if update.new_description is not None:
        body["description"] = update.new_description

    if update.new_location is not None:
        body["location"] = update.new_location

    changing_time = (
        update.new_event_date is not None
        or update.new_start_time is not None
        or update.new_end_time is not None
    )

    if not changing_time:
        return body

    resolved_date = _parse_date(update.new_event_date) if update.new_event_date else _event_start_date(current_event)
    current_is_all_day = _event_is_all_day(current_event)

    if current_is_all_day and update.new_start_time is None:
        if update.new_end_time is not None:
            raise ValueError("new_end_time cannot be set without new_start_time for an all-day event.")
        end_date = resolved_date + timedelta(days=1)
        body["start"] = {"date": resolved_date.isoformat()}
        body["end"] = {"date": end_date.isoformat()}
        return body

    if update.new_start_time is not None:
        start_time = _parse_time(update.new_start_time)
    else:
        start_time = _event_start_time(current_event)

    start_dt = datetime.combine(resolved_date, start_time)

    if update.new_end_time is not None:
        end_dt = datetime.combine(resolved_date, _parse_time(update.new_end_time))
    elif update.new_start_time is not None:
        end_dt = start_dt + timedelta(hours=1)
    else:
        end_dt = datetime.combine(resolved_date, _event_end_time(current_event))

    if end_dt <= start_dt:
        raise ValueError("new_end_time must be after new_start_time.")

    body["start"] = {
        "dateTime": start_dt.isoformat(),
        "timeZone": update.new_timezone,
    }
    body["end"] = {
        "dateTime": end_dt.isoformat(),
        "timeZone": update.new_timezone,
    }
    return body

@tool("create_google_calendar_events", args_schema=CreateGoogleCalendarEventsInput)
def create_google_calendar_events(
    events: list[CalendarEventDraft],
    confirmed_by_user: bool = False,
) -> str:
    """
    Create one or more Google Calendar events.

    Rules:
    - One or two events can be created without confirmation.
    - Three or more events require confirmed_by_user=True.
    """
    if len(events) >= 3 and not confirmed_by_user:
        return (
            "Google Calendar events were not created. "
            "Creating 3 or more events requires explicit user confirmation."
        )

    created = []
    errors = []

    try:
        service = _calendar_service()
    except Exception as exc:
        return f"Could not connect to Google Calendar: {exc}"

    for i, draft in enumerate(events):
        try:
            body = _build_event_body(draft)
            event = service.events().insert(calendarId=CALENDAR_ID, body=body).execute()
            created.append(
                f"[{i}] {event.get('summary', draft.title)} - {event.get('htmlLink', '')}"
            )
        except Exception as exc:
            errors.append(f"[{i}] {draft.title}: {exc}")

    parts = []
    if created:
        parts.append("Created Google Calendar event(s):\n" + "\n".join(created))
    if errors:
        parts.append("Failed Google Calendar event(s):\n" + "\n".join(errors))

    return "\n\n".join(parts) if parts else "No Google Calendar events were created."


@tool("list_google_calendar_events", args_schema=ListGoogleCalendarEventsInput)
def list_google_calendar_events(
    time_min: Optional[str] = None,
    time_max: Optional[str] = None,
    query: Optional[str] = None,
    max_results: int = 10,
) -> str:
    """
    List upcoming Google Calendar events.

    Use this before updating or deleting calendar events so the agent has
    a fresh event_id and expected title.
    """
    try:
        service = _calendar_service()

        request_kwargs = {
            "calendarId": CALENDAR_ID,
            "maxResults": max_results,
            "singleEvents": True,
            "orderBy": "startTime",
        }

        resolved_time_min = _to_rfc3339_bound(time_min)
        resolved_time_max = _to_rfc3339_bound(time_max)

        if resolved_time_min:
            request_kwargs["timeMin"] = resolved_time_min
        else:
            request_kwargs["timeMin"] = datetime.utcnow().isoformat() + "Z"

        if resolved_time_max:
            request_kwargs["timeMax"] = resolved_time_max

        if query:
            request_kwargs["q"] = query

        result = service.events().list(**request_kwargs).execute()
        items = result.get("items", [])
        rprint("[bold cyan]Raw Google Calendar events:[/bold cyan]")
        rprint(items)

    except Exception as exc:
        return f"Could not list Google Calendar events: {exc}"

    if not items:
        return "No matching Google Calendar events found."

    lines = [_format_event_line(i, event) for i, event in enumerate(items)]
    return "Google Calendar events:\n\n" + "\n\n".join(lines)


@tool("update_google_calendar_events", args_schema=UpdateGoogleCalendarEventsInput)
def update_google_calendar_events(
    updates: list[UpdateGoogleCalendarEventPatch],
    confirmed_by_user: bool = False,
) -> str:
    """
    Update one or more Google Calendar events.

    Rules:
    - Must use event_id + expected_title from a fresh list_google_calendar_events result.
    - Updating 1 or 2 events does not require confirmation.
    - Updating 3 or more events requires confirmed_by_user=True.
    """
    if len(updates) >= 3 and not confirmed_by_user:
        return (
            "Google Calendar events were not updated. "
            "Updating 3 or more events requires explicit user confirmation."
        )

    try:
        service = _calendar_service()
    except Exception as exc:
        return f"Could not connect to Google Calendar: {exc}"

    updated = []
    errors = []

    for i, update in enumerate(updates):
        try:
            current_event = service.events().get(
                calendarId=CALENDAR_ID,
                eventId=update.event_id,
            ).execute()
            rprint("Current event:", current_event)
            current_title = current_event.get("summary", "")
            if not _title_matches(update.expected_title, current_title):
                errors.append(
                    f"[{i}] {update.expected_title}: expected title did not match current title "
                    f"'{current_title}'. List events again before retrying."
                )
                continue

            body = _build_update_body(current_event, update)
            if not body:
                errors.append(f"[{i}] {current_title}: no update fields were provided.")
                continue

            event = service.events().patch(
                calendarId=CALENDAR_ID,
                eventId=update.event_id,
                body=body,
            ).execute()

            updated.append(
                f"[{i}] {event.get('summary', current_title)} - {event.get('htmlLink', '')}"
            )
        except Exception as exc:
            errors.append(f"[{i}] {update.expected_title}: {exc}")

    parts = []
    if updated:
        parts.append("Updated Google Calendar event(s):\n" + "\n".join(updated))
    if errors:
        parts.append("Failed Google Calendar update(s):\n" + "\n".join(errors))

    return "\n\n".join(parts) if parts else "No Google Calendar events were updated."


@tool("delete_google_calendar_events", args_schema=DeleteGoogleCalendarEventsInput)
def delete_google_calendar_events(
    delete_events: list[DeleteGoogleCalendarEventRef],
    confirmed_by_user: bool = False,
) -> str:
    """
    Delete one or more Google Calendar events.

    Rules:
    - Must use event_id + expected_title from a fresh list_google_calendar_events result.
    - All deletions require confirmed_by_user=True.
    """
    if not confirmed_by_user:
        return (
            "Google Calendar event(s) were not deleted. "
            "Deleting calendar events requires explicit user confirmation."
        )

    try:
        service = _calendar_service()
    except Exception as exc:
        return f"Could not connect to Google Calendar: {exc}"

    deleted = []
    errors = []

    for i, ref in enumerate(delete_events):
        try:
            current_event = service.events().get(
                calendarId=CALENDAR_ID,
                eventId=ref.event_id,
            ).execute()

            current_title = current_event.get("summary", "")
            if not _title_matches(ref.expected_title, current_title):
                errors.append(
                    f"[{i}] {ref.expected_title}: expected title did not match current title "
                    f"'{current_title}'. List events again before retrying."
                )
                continue

            service.events().delete(
                calendarId=CALENDAR_ID,
                eventId=ref.event_id,
            ).execute()

            deleted.append(f"[{i}] {current_title}")
        except Exception as exc:
            errors.append(f"[{i}] {ref.expected_title}: {exc}")

    parts = []
    if deleted:
        parts.append("Deleted Google Calendar event(s):\n" + "\n".join(deleted))
    if errors:
        parts.append("Failed Google Calendar deletion(s):\n" + "\n".join(errors))

    return "\n\n".join(parts) if parts else "No Google Calendar events were deleted."
