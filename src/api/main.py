import json
from pathlib import Path
from typing import Any
from dotenv import load_dotenv

from fastapi import FastAPI
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from src.llm.langchain_agent import run_agent

load_dotenv()

BASE_DIR = Path(__file__).resolve().parents[2]
WEB_DIR = BASE_DIR / "src" / "web"

ALLOWED_ORIGINS = [
    "http://127.0.0.1:8000",
    "http://localhost:8000",
    "https://bojiakpui-xyz-student-web-app.me",
    "https://www.bojiakpui-xyz-student-web-app.me",
]

app = FastAPI(title="Personal Assistance with AI Agents", description="An API for personal assistance using AI agents.")

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)

app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")

class ChatRequest(BaseModel):
    message: str = Field(min_length=1, description="User message to send to the agent.")

class ChatResponse(BaseModel):
    reply: str

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
    reply = message[-1].content
    return ChatResponse(reply=reply)

@app.post("/chat/stream")
def chat_stream(request: ChatRequest) -> StreamingResponse:
    def event_generator():
        try:
            yield _sse_event("status", {"code": "starting_agent"})
            yield _sse_event("status", {"code": "running_agent"})

            messages = run_agent(request.message)
            reply = messages[-1].content

            yield _sse_event("status", {"code": "agent_completed"})
            yield _sse_event("final", {"reply": reply})

        except Exception as exc:
            yield _sse_event("error", {
                    "message": "agent_run_failed",
                    "detail": str(exc),
                },)
            
    return StreamingResponse(
        event_generator(), 
        media_type="text/event-stream", 
        headers={
            "Cache-Control": "no-cache", 
            "X-Accel-Buffering": "no"
        }
    )

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