import json

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage

from src.db.conver_sqlite import (
    ConversationEvent,
    get_events_after,
    get_latest_summary,
)


def build_conversation_context(conversation_id: str) -> list[BaseMessage]:
    latest_summary = get_latest_summary(conversation_id)
    covered_event_id = latest_summary.covers_through_event_id if latest_summary else 0
    events = get_events_after(conversation_id, covered_event_id)

    history: list[BaseMessage] = []

    if latest_summary is not None:
        history.append(
            HumanMessage(
                content=(
                    "Conversation summary for context only. "
                    "This is not a new user request.\n\n"
                    f"{latest_summary.summary}"
                )
            )
        )

    # recent_tool_event_ids = _recent_tool_event_ids(events)
    # older_tool_context_lines: list[str] = []
    tool_group: list[ConversationEvent] = []

    # def flush_older_tool_context() -> None:
    #     nonlocal older_tool_context_lines

    #     if not older_tool_context_lines:
    #         return

    #     history.append(
    #         HumanMessage(
    #             content=(
    #                 "Older tool/execution context for reference only. "
    #                 "This is not a new user request.\n\n"
    #                 + "\n".join(older_tool_context_lines)
    #             )
    #         )
    #     )
    #     older_tool_context_lines = []

    def flush_tool_group() -> None:
        nonlocal tool_group

        if not tool_group:
            return

        _append_tool_group(history, tool_group)
        tool_group = []

    for event in events:
        if event.event_type in {"tool_call", "tool_result"}:
            tool_group.append(event)
            # if event.id in recent_tool_event_ids:
            #     tool_group.append(event)
            # else:
            #     flush_tool_group()
            #     older_tool_context_lines.append(_format_tool_event_note(event))
            continue

        flush_tool_group()
        # flush_older_tool_context()

        if event.event_type == "user_message" and event.content:
            history.append(HumanMessage(content=_format_user_message_for_context(event)))

        elif event.event_type == "assistant_message" and event.content:
            history.append(AIMessage(content=event.content))

        elif event.event_type == "run_error" and event.content:
            history.append(
                HumanMessage(
                    content=(
                        "Previous agent run error for context only. "
                        "This is not a new user request.\n\n"
                        f"{_shorten(event.content)}"
                    )
                )
            )

    flush_tool_group()
    # flush_older_tool_context()

    return history


# def _recent_tool_event_ids(events: list[ConversationEvent]) -> set[int]:
#     tool_events = [
#         event for event in events
#         if event.event_type in {"tool_call", "tool_result"}
#     ]
#     return {event.id for event in tool_events[-RECENT_TOOL_EVENT_LIMIT:]}


def _append_tool_group(
    history: list[BaseMessage],
    tool_group: list[ConversationEvent],
) -> None:
    batches = _split_replayable_tool_batches(tool_group)
    if batches is None:
        history.append(
            HumanMessage(
                content=(
                    "Recent tool/execution context for reference only. "
                    "This is not a new user request.\n\n"
                    + "\n".join(_format_tool_event_note(event) for event in tool_group)
                )
            )
        )
        return

    for tool_calls, tool_results in batches:
        history.append(
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": event.tool_name or "tool",
                        "args": _parse_tool_args(event.tool_args_json),
                        "id": event.tool_call_id,
                    }
                    for event in tool_calls
                ],
            )
        )

        for event in tool_results:
            history.append(
                ToolMessage(
                    content=event.tool_result or event.tool_result_preview or "",
                    tool_call_id=event.tool_call_id or "",
                )
            )


def _split_replayable_tool_batches(
    tool_group: list[ConversationEvent],
) -> list[tuple[list[ConversationEvent], list[ConversationEvent]]] | None:
    if not tool_group:
        return None

    batches: list[tuple[list[ConversationEvent], list[ConversationEvent]]] = []
    index = 0

    while index < len(tool_group):
        tool_calls: list[ConversationEvent] = []
        tool_call_batch_id = tool_group[index].tool_call_batch_id

        while (
            index < len(tool_group)
            and tool_group[index].event_type == "tool_call"
            and tool_group[index].tool_call_batch_id == tool_call_batch_id
        ):
            tool_calls.append(tool_group[index])
            index += 1

        if not tool_calls:
            return None

        tool_results: list[ConversationEvent] = []

        while (
            index < len(tool_group)
            and tool_group[index].event_type == "tool_result"
            and tool_group[index].tool_call_batch_id == tool_call_batch_id
            and len(tool_results) < len(tool_calls)
        ):
            tool_results.append(tool_group[index])
            index += 1

        if not _is_valid_tool_batch(tool_calls, tool_results):
            return None

        batches.append((tool_calls, tool_results))

    return batches


def _is_valid_tool_batch(
    tool_calls: list[ConversationEvent],
    tool_results: list[ConversationEvent],
) -> bool:
    if len(tool_calls) != len(tool_results):
        return False

    call_ids = [event.tool_call_id for event in tool_calls]
    result_ids = [event.tool_call_id for event in tool_results]
    batch_ids = {
        event.tool_call_batch_id
        for event in [*tool_calls, *tool_results]
    }

    if any(call_id is None for call_id in call_ids):
        return False

    if any(result_id is None for result_id in result_ids):
        return False

    if len(set(call_ids)) != len(call_ids):
        return False

    if len(batch_ids) != 1 or None in batch_ids:
        return False

    return set(call_ids) == set(result_ids)


def _parse_tool_args(tool_args_json: str | None) -> dict:
    if not tool_args_json:
        return {}

    try:
        parsed = json.loads(tool_args_json)
    except json.JSONDecodeError:
        return {}

    return parsed if isinstance(parsed, dict) else {}


def _format_tool_event_note(event: ConversationEvent) -> str:
    if event.event_type == "tool_call":
        return (
            f"- Tool call requested: {event.tool_name or 'tool'}"
            f"(tool_call_id={event.tool_call_id or 'unknown'}, "
            f"tool_call_batch_id={event.tool_call_batch_id or 'unknown'}, "
            f"args={event.tool_args_json or '{}'})"
        )

    if event.event_type == "tool_result":
        result = event.tool_result_preview or event.tool_result or ""
        return (
            f"- Tool result received: {event.tool_name or 'tool'}"
            f"(tool_call_id={event.tool_call_id or 'unknown'}, "
            f"status={event.status or 'unknown'}): {_shorten(result)}"
        )

    return f"- {event.event_type}: {_shorten(event.content or '')}"


def _format_user_message_for_context(event: ConversationEvent) -> str:
    if not event.runtime_context:
        return event.content or ""

    return f"{event.runtime_context}\n\nUser message: {event.content or ''}"


def _shorten(text: str, limit: int = 800) -> str:
    cleaned = text.strip()
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[:limit].rstrip() + "..."
