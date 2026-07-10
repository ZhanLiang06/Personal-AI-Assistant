from datetime import datetime, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dateutil import tz
from langchain_core.tools import tool
from pydantic import BaseModel, Field


DEFAULT_TIMEZONE = "Asia/Kuala_Lumpur"


class GetCurrentTimeInput(BaseModel):
    timezone: str = Field(
        default=DEFAULT_TIMEZONE,
        description=(
            "IANA timezone name, e.g. 'Asia/Kuala_Lumpur', "
            "'America/New_York', or 'Europe/London'. Defaults to Malaysia time."
        ),
    )


def _resolve_timezone(timezone: str) -> tzinfo:
    try:
        return ZoneInfo(timezone)
    except ZoneInfoNotFoundError:
        resolved = tz.gettz(timezone)
        if resolved is None:
            raise ValueError(
                f"Unknown timezone: {timezone!r}. Use an IANA timezone name such as "
                "'Asia/Kuala_Lumpur', 'America/New_York', or 'Europe/London'."
            )
        return resolved


def format_current_time(timezone: str = DEFAULT_TIMEZONE) -> str:
    tzinfo_obj = _resolve_timezone(timezone)
    now = datetime.now(tzinfo_obj)
    offset = now.strftime("%z")
    offset_label = f"{offset[:3]}:{offset[3:]}" if offset else "unknown"
    zone_label = now.tzname() or timezone
    return (
        f"{now.strftime('%Y-%m-%d %H:%M:%S')} {zone_label} "
        f"(UTC{offset_label}), timezone={timezone}"
    )


@tool("get_current_time", args_schema=GetCurrentTimeInput)
def get_current_time(timezone: str = DEFAULT_TIMEZONE) -> str:
    """Return the current date and time for the requested IANA timezone."""
    try:
        return format_current_time(timezone)
    except ValueError as exc:
        return str(exc)