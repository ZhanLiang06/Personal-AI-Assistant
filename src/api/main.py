import json
from pathlib import Path
from typing import Any
from rich import print as rprint
from dotenv import load_dotenv

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from src.llm.langchain_agent import build_runtime_context, run_agent, stream_agent_events, message_text
from src.llm.conversation_context import build_conversation_context
from src.logging.agent_event_log import append_agent_event

from src.db.conver_sqlite import (
    add_assistant_message,
    add_tool_call,
    add_tool_result,
    add_user_message,
    create_conversation,
    get_conversation,
    init_conversation_db,
    delete_conversation,
    add_run_error,
    list_conversations,
    get_conversation_events,
)

load_dotenv()

BASE_DIR = Path(__file__).resolve().parents[2]
WEB_DIR = BASE_DIR / "src" / "web"

ALLOWED_ORIGINS = [
    "http://127.0.0.1:8000",
    "http://localhost:8000",
    "https://bojiakpui-xyz-student-web-app.me",
    "https://www.bojiakpui-xyz-student-web-app.me"
]

app = FastAPI(title="Personal Assistance with AI Agents", description="An API for personal assistance using AI agents.")

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)

STATIC_DIR = WEB_DIR / "static"
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

init_conversation_db()

class ChatRequest(BaseModel):
    message: str = Field(min_length=1, description="User message to send to the agent.")
    conversation_id: str | None = Field(
        default=None,
        description="Selected conversation ID. If omitted, a new conversation is created.",
    )

class ChatResponse(BaseModel):
    reply: str

class ConversationResponse(BaseModel):
    id: str
    title: str
    created_at: str
    updated_at: str


class ConversationEventResponse(BaseModel):
    id: int
    event_type: str
    content: str | None = None
    tool_name: str | None = None
    tool_call_id: str | None = None
    tool_call_batch_id: str | None = None
    tool_args_json: str | None = None
    tool_result: str | None = None
    tool_result_preview: str | None = None
    status: str | None = None
    run_id: str | None = None
    created_at: str


class ConversationDetailResponse(BaseModel):
    conversation: ConversationResponse
    events: list[ConversationEventResponse]

def _resolve_conversation_id(requested_conversation_id: str | None) -> tuple[str, bool]:
    if requested_conversation_id is None:
        return create_conversation(), True

    conversation = get_conversation(requested_conversation_id)
    if conversation is None:
        raise HTTPException(
            status_code=404,
            detail="Conversation not found.",
        )

    return requested_conversation_id, False

def _sse_event(event: str, data: dict[str, Any]) -> str:
    payload = json.dumps(data, ensure_ascii=False)
    return f"event: {event}\ndata: {payload}\n\n"

@app.get("/")
def web_app() -> FileResponse:
    return FileResponse(WEB_DIR / "index.html")

@app.get("/health")
def health_check() -> dict[str, str]:
    return {"status": "ok", "service": "Personal Assistance with AI Agents"}

@app.post("/chat", response_model=ChatResponse)
def chat(request: ChatRequest) -> ChatResponse:
    message = run_agent(request.message)
    reply = message_text(message[-1])
    return ChatResponse(reply=reply)

@app.post("/chat/stream")
def chat_stream(request: ChatRequest) -> StreamingResponse:
    conversation_id, created_new_conversation = _resolve_conversation_id(request.conversation_id)
    
    def event_generator():
        stored_tool_event = False
        final_reply: str | None = None
        latest_run_id: str | None = None
        tool_call_batch_by_id: dict[str, str] = {}

        try:
            runtime_context = build_runtime_context()
            history = build_conversation_context(conversation_id)
            rprint(f"[bold green]Conversation history for ID {conversation_id}:[/bold green] {history}")
            add_user_message(
                conversation_id,
                request.message,
                runtime_context=runtime_context,
            )

            yield _sse_event("status", {
                "code": "conversation_ready",
                "message": "Conversation ready",
                "conversation_id": conversation_id,
            })

            for agent_event in stream_agent_events(
                request.message,
                history=history,
                runtime_context=runtime_context,
            ):
                append_agent_event(agent_event)
                print(agent_event)

                event_data = agent_event["data"]
                latest_run_id = event_data.get("run_id", latest_run_id)

                stored_tool_event, event_final_reply = _add_conversation_event_to_db(
                    conversation_id,
                    agent_event,
                    stored_tool_event,
                    tool_call_batch_by_id,
                )
                if event_final_reply is not None:
                    final_reply = event_final_reply

                yield _sse_event(agent_event["event"], agent_event["data"])
            
            if final_reply is None or not final_reply.strip():
                add_run_error(
                    conversation_id=conversation_id,
                    error_message="Agent run ended without a final response.",
                    run_id=latest_run_id,
                )


        except Exception as exc:
            yield _sse_event("error", {
                    "message": "agent_run_failed",
                    "detail": str(exc),
                },)
            add_run_error(conversation_id, str(exc),run_id=latest_run_id)
            
    return StreamingResponse(
        event_generator(), 
        media_type="text/event-stream", 
        headers={
            "Cache-Control": "no-cache", 
            "X-Accel-Buffering": "no"
        }
    )

@app.get("/conversations", response_model=list[ConversationResponse])
def conversations() -> list[ConversationResponse]:
    return [
        ConversationResponse(
            id=conversation.id,
            title=conversation.title,
            created_at=conversation.created_at,
            updated_at=conversation.updated_at,
        )
        for conversation in list_conversations()
    ]

@app.get("/conversations/{conversation_id}", response_model=ConversationDetailResponse)
def conversation_detail(conversation_id: str) -> ConversationDetailResponse:
    conversation = get_conversation(conversation_id)
    if conversation is None:
        raise HTTPException(
            status_code=404,
            detail="Conversation not found.",
        )

    events = get_conversation_events(conversation_id)

    return ConversationDetailResponse(
        conversation=ConversationResponse(
            id=conversation.id,
            title=conversation.title,
            created_at=conversation.created_at,
            updated_at=conversation.updated_at,
        ),
        events=[
            ConversationEventResponse(
                id=event.id,
                event_type=event.event_type,
                content=event.content,
                tool_name=event.tool_name,
                tool_call_id=event.tool_call_id,
                tool_call_batch_id=event.tool_call_batch_id,
                tool_args_json=event.tool_args_json,
                tool_result=event.tool_result,
                tool_result_preview=event.tool_result_preview,
                status=event.status,
                run_id=event.run_id,
                created_at=event.created_at,
            )
            for event in events
        ],
    )

def _add_conversation_event_to_db(
    conversation_id: str,
    agent_event: dict[str, Any],
    stored_tool_event: bool,
    tool_call_batch_by_id: dict[str, str],
) -> tuple[bool, str | None]:
    event_name = agent_event["event"]
    event_data = agent_event["data"]
    run_id = event_data.get("run_id")
    final_reply: str | None = None

    if event_name == "status":
        code = event_data.get("code")

        if code == "tool_call_requested":
            stored_tool_event = True
            tool_call_id = event_data.get("tool_call_id")
            tool_call_batch_id = event_data.get("tool_call_batch_id")
            if tool_call_id and tool_call_batch_id:
                tool_call_batch_by_id[tool_call_id] = tool_call_batch_id

            add_tool_call(
                conversation_id=conversation_id,
                tool_name=event_data.get("tool_name", "tool"),
                tool_call_id=tool_call_id,
                tool_call_batch_id=tool_call_batch_id,
                tool_args=event_data.get("tool_args", {}),
                run_id=run_id,
            )

        if code == "tool_result_received":
            stored_tool_event = True
            result_preview = event_data.get("result_preview", "")
            tool_call_id = event_data.get("tool_call_id")
            add_tool_result(
                conversation_id=conversation_id,
                tool_name=event_data.get("tool_name", "tool"),
                tool_call_id=tool_call_id,
                tool_call_batch_id=tool_call_batch_by_id.get(tool_call_id),
                tool_result=event_data.get("result"),
                result_preview=result_preview,
                run_id=run_id,
            )

    elif event_name == "final":
        final_reply = event_data.get("reply", "")
        if final_reply.strip():
            add_assistant_message(
                conversation_id=conversation_id,
                content=final_reply,
                run_id=run_id,
            )
    
    return stored_tool_event, final_reply

# def require_api_key(
#     x_assistant_api_key: str | None = Header(default=None),
# ) -> None:
#     if not ASSISTANT_API_KEY:
#         return

#     if x_assistant_api_key is None or not compare_digest(
#         x_assistant_api_key,
#         ASSISTANT_API_KEY,
#     ):
#         raise HTTPException(
#             status_code=status.HTTP_401_UNAUTHORIZED,
#             detail="Invalid or missing API key.",
#         )
