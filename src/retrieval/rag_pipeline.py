# src/retrieval/rag_pipeline.py

import os
from dotenv import load_dotenv
from groq import Groq

from src.retrieval.search_notes import search_notes

load_dotenv()

client = Groq(api_key=os.environ["GROQ_API_KEY"])

SYSTEM_PROMPT = """You are a personal assistant answering questions using the user's Obsidian notes as your primary source.

You will be given CONTEXT retrieved from the user's notes, followed by a QUESTION.

Rules:
1. If the CONTEXT contains information relevant to the QUESTION, answer using it, and cite the note title(s) you drew from.
2. If the CONTEXT is empty or irrelevant to the QUESTION, and the QUESTION is asking about the user's own notes, project, decisions, or personal knowledge base specifically — respond with exactly: "I couldn't find anything about this in your notes."
3. If the CONTEXT is empty or irrelevant, but the QUESTION is a general knowledge question unrelated to the user's personal notes — answer it using your own knowledge, but clearly prefix your answer with: "[Not from your notes — general knowledge:]"
4. Never blend unlabeled general knowledge into an answer that claims to be from the user's notes.
"""


def format_context(chunks: list[dict]) -> str:
    if not chunks:
        return "(no relevant notes found)"

    blocks = [c["text"] for c in chunks]
    return "\n\n---\n\n".join(blocks)


def ask_notes(query: str, k: int = 5, where: dict | None = None) -> str:
    chunks = search_notes(query, k=k, where=where)
    context = format_context(chunks)

    user_prompt = f"CONTEXT:\n{context}\n\nQUESTION:\n{query}"

    # Stateless: a fresh, one-off call with no shared history.
    # See earlier note on why ask_notes should NOT reuse the global-history ask().
    response = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0,
    )

    return response.choices[0].message.content


# run with: uv run python -m src.retrieval.rag_pipeline
if __name__ == "__main__":
    answer = ask_notes("what did I decide about my rent budget")
    print(answer)