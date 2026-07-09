from langchain_groq import ChatGroq
import os
from src.tools.obsidian import search_notes

os.environ["HF_HUB_OFFLINE"] = "1"  # still needed here per open item #1 — not yet moved into vault_db.py

llm = ChatGroq(model="llama-3.3-70b-versatile")
llm_with_tools = llm.bind_tools([search_notes])

response = llm_with_tools.invoke("What have I written about IE rotation and career fit?")

print(response.tool_calls)

if response.tool_calls:
    call = response.tool_calls[0]
    result = search_notes.invoke(call["args"])  # this DOES execute the real function
    print(result)