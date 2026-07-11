# src/llm/langchain_agent.py
import os

os.environ["HF_HUB_OFFLINE"] = "1"

from typing import Optional, Any
from collections.abc import Iterator
from dotenv import load_dotenv
from langchain.agents import create_agent
from langchain_core.messages import BaseMessage, HumanMessage
from langchain_groq import ChatGroq
from rich import print as rprint

from src.llm.prompts import SYSTEM_PROMPT
from src.tools.general import format_current_time, get_current_time
from src.tools.obsidian import (
    search_notes,
    list_daily_todos,
    add_daily_todos,
    update_daily_todos,
    delete_daily_todos,
)

load_dotenv()


MODEL_NAME = "openai/gpt-oss-120b"
MAX_AGENT_STEPS = 20

TOOLS = [
    get_current_time,
    search_notes,
    list_daily_todos,
    add_daily_todos,
    update_daily_todos,
    delete_daily_todos,
]



def build_agent():
    llm = ChatGroq(model=MODEL_NAME, temperature=0)
    return create_agent(
        model=llm,
        tools=TOOLS,
        system_prompt=SYSTEM_PROMPT,
    )

def run_agent(
    user_question: str,
    history: Optional[list[BaseMessage]] = None,
) -> list[BaseMessage]:
    
    agent = build_agent()
    # avoids mutating the original messages list before the agent succeeds
    new_message_list = history.copy() if history is not None else [] 

    current_time = format_current_time("Asia/Kuala_Lumpur")
    user_content = f"Runtime context: current Malaysia date/time is {current_time}.\n\nUser message: {user_question}"

    new_message_list.append(HumanMessage(content=user_content))
    
    result = agent.invoke(
        {"messages": new_message_list},
        config={"recursion_limit": MAX_AGENT_STEPS},
    )

    output_messages = result["messages"]
    return output_messages

def status_event(code: str, message: str, **extra: Any) -> dict[str, Any]:
    return {
        "event": "status",
        "data": {
            "code": code,
            "message": message,
            **extra,
        },
    }


def final_event(reply: str) -> dict[str, Any]:
    return {
        "event": "final",
        "data": {"reply": reply},
    }


def message_text(message: BaseMessage) -> str:
    content = message.content
    if isinstance(content, str):
        return content

    return str(content)

def node_messages(node_update: Any) -> list[BaseMessage]:
    if isinstance(node_update, dict) and isinstance(node_update.get("messages"), list):
        return node_update["messages"]

    return []


def stream_agent_events(
    user_question: str,
    history: Optional[list[BaseMessage]] = None,
) -> Iterator[dict[str, Any]]:
    
    agent = build_agent()
    # avoids mutating the original messages list before the agent succeeds
    new_message_list = history.copy() if history is not None else [] 

    current_time = format_current_time("Asia/Kuala_Lumpur")
    user_content = f"Runtime context: current Malaysia date/time is {current_time}.\n\nUser message: {user_question}"

    new_message_list.append(HumanMessage(content=user_content))
    latest_messages: list[BaseMessage] = []

    yield status_event("agent_started", "Starting assistant")

    update_stream = agent.stream(
        {"messages": new_message_list},
        config={"recursion_limit": MAX_AGENT_STEPS},
        stream_mode="updates"
    )

    for update in update_stream:
        rprint(f"RAW Update: {update}\n\n")
        if "model" in update:
            messages = node_messages(update["model"])
            if messages:
                latest_messages = messages
                last_message = messages[-1]
                tool_calls = getattr(last_message, "tool_calls", None) or []

                if tool_calls:
                    for tool_call in tool_calls:
                        tool_name = tool_call.get("name", "tool")
                        tool_call_id = tool_call.get("id")

                        yield status_event(
                            "tool_call_requested",
                            f"Planning to use tool: {tool_name}",
                            tool_name=tool_name,
                            tool_call_id=tool_call_id,
                        )
                else:
                    yield status_event(
                        "agent_thinking", "Preparing final response"
                    )
        
        if "tools" in update:
            messages = node_messages(update["tools"])

            for tool_message in messages:
                tool_call_id = getattr(tool_message, "tool_call_id", None)

                yield status_event(
                    "tool_result_received",
                    "Tool result received",
                    tool_call_id=tool_call_id,
                )
        
    yield status_event("agent_finished", "Assistant finished")

    if latest_messages:
        yield final_event(message_text(latest_messages[-1]))
    else:
        yield final_event("")


if __name__ == "__main__":
    messages = []
    questions = ["wat did i do at my rnd intern at goreal"]

    for question in questions:
        messages = run_agent(question, messages)
        print(messages[-1].content)

    rprint("\n\nConversation history:")
    for msg in messages:
        rprint(msg)

