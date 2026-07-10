# src/llm/langchain_agent.py
import os

os.environ["HF_HUB_OFFLINE"] = "1"

from typing import Optional

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


if __name__ == "__main__":
    messages = []
    questions = ["wat did i do at my rnd intern at goreal"]

    for question in questions:
        messages = run_agent(question, messages)
        print(messages[-1].content)

    rprint("\n\nConversation history:")
    for msg in messages:
        rprint(msg)

