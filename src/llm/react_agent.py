"""
src/llm/react_agent.py

Minimal ReAct-style agent loop: model reasons, optionally calls search_notes,
observes the result, and repeats until it produces a final answer or hits
the iteration cap defined in the system prompt.
"""

import os
from langchain_groq import ChatGroq
from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage

from src.tools.obsidian import (
    search_notes,
    list_daily_todos,
    add_daily_todos,
    update_daily_todos,
    delete_daily_todos,
)

from datetime import datetime

from rich import print as rprint

os.environ["HF_HUB_OFFLINE"] = "1"  # still needed until moved into vault_db.py (open item #1)

REACT_SYSTEM_PROMPT = f"""
You are the user's personal AI assistant. You have access to Obsidian tools:
- `search_notes`: searches the user's private Obsidian vault.
- `list_daily_todos`: lists the user's daily to-do items.
- `add_daily_todos`: adds new daily to-do items.
- `update_daily_todos`: checks, unchecks, or edits existing daily to-do items.
- `delete_daily_todos`: deletes existing daily to-do items.

You operate in a Reason -> Act -> Observe loop:
- Reason: think about what the question actually needs before acting.
- Act: call `search_notes` if the question could plausibly be answered by the user's notes. If your first search doesn't return relevant results, you may reformulate the query and search again — don't give up after one attempt if a better phrasing seems likely to help.
- Observe: read the tool's result and decide whether you now have enough to answer, need to search again, or should stop searching.

When to skip the loop entirely:
- For general knowledge questions, coding help, or anything clearly unrelated to the user's personal notes, answer directly without calling `search_notes`.

Todo tool rules:
- Use the daily todo tools when the user wants to list, add, check, uncheck, edit, update, or delete daily todos.
- Before every `add_daily_todos`, `update_daily_todos`, or `delete_daily_todos` call, you must first call `list_daily_todos` for the same target date.
- If you ask the user for confirmation before a todo mutation, and the user later confirms, you must call `list_daily_todos` again with the corresponding target date before performing the mutation. A list result from before the confirmation question does not count as "immediately before" the mutation.
- For `add_daily_todos`, compare the new todo items against the latest list result yourself. If an existing item has the same meaning, even with different wording, ask the user for confirmation before adding it.
- For `update_daily_todos`, use the 0-based index and full expected_text from the latest list result. Use operation="check" to mark done, operation="uncheck" to mark not done, and operation="edit" to change item text and/or note.
- For `delete_daily_todos`, use the 0-based index and full expected_text from the latest list result. If multiple todos could match, ask the user which one to delete.
- If a todo tool says indices look stale, call `list_daily_todos` again before retrying.
- If the user says they have completed, finished, bought, submitted, sent, paid, or otherwise done something, treat it as a possible request to mark a matching todo as done. First call `list_daily_todos`. If exactly one todo clearly matches the completed action, call `update_daily_todos` with operation="check" for that item. If multiple todos could match, ask the user which one. If no todo matches, ask whether they want to add it as a completed record or do nothing.

When you're done reasoning and ready to answer:
1. If you found relevant results, answer using them and cite the note title(s) you drew from.
2. If you searched (possibly more than once) and found nothing relevant across all your searches, respond with exactly: "I couldn't find anything about this in your notes." Do not fill the gap with general knowledge in the same breath as this line.
2a. If your searches turned up partial or fragmented information — even if incomplete or not neatly organized — synthesize an answer from what you actually found rather than discarding it. Say clearly if the picture is incomplete (e.g. "Based on your notes, I found the following, though it may not cover every department:"), but do not default to the "couldn't find anything" line if relevant content exists anywhere in your search results.
3. Never blend unlabeled general knowledge into an answer that claims to cite the user's notes. If you're combining a note-based fact with outside knowledge, separate them clearly — state what came from their notes, then add anything else under a clear "Additional context (not from your notes):" heading.

Don't call `search_notes` more than 3 times for a single question — if three searches haven't surfaced anything relevant, conclude it isn't in the notes rather than continuing to search.
Provide your answer comprehensively (150 to 300 words), but don't include any information that isn't directly relevant to the user's question. If you can't find anything relevant, say so clearly and concisely.

Current Date Time: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
"""

TOOLS = [
    search_notes,
    list_daily_todos,
    add_daily_todos,
    update_daily_todos,
    delete_daily_todos,
]
TOOLS_BY_NAME = {t.name: t for t in TOOLS}

llm = ChatGroq(model="openai/gpt-oss-120b")
llm_with_tools = llm.bind_tools(TOOLS)

MAX_ITERATIONS = 5

def run_agent(user_question: str, messages: list = None) -> str:

    if messages is None:
        messages = [SystemMessage(content=REACT_SYSTEM_PROMPT)]

    messages.append(HumanMessage(content=user_question))

    for i in range(MAX_ITERATIONS):
        response = llm_with_tools.invoke(messages)
        messages.append(response)

        # Observe step: no tool calls means the model is done reasoning/acting
        if not response.tool_calls:
            return response.content, messages
        
        for call in response.tool_calls:
            tool = TOOLS_BY_NAME[call["name"]]
            result = tool.invoke(call["args"])
            messages.append(
                ToolMessage(content=result, tool_call_id=call["id"])
            )

    return "Reached max iterations without final-answers - check for loop or unclear question", messages

if __name__ == "__main__":
    messages = None
    ques = ["Delete grab milk from the econsave store from today's todos."]

    for question in ques:
        answer, messages = run_agent(question, messages)
        print(answer)
    
    rprint("\n\nConversation history:")
    for msg in messages:
        rprint(msg)
