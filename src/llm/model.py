import os
from dotenv import load_dotenv
from groq import Groq

load_dotenv()

client = Groq(api_key=os.environ["GROQ_API_KEY"])

# This list IS the memory. The model itself remembers nothing between calls -
# we are responsible for resending the full history every time.
history = []

def ask(user_message: str) -> str:
    history.append({"role": "user", "content": user_message})

    response = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=history,   # <-- the whole conversation so far, not just this message
        temperature=0,       # deterministic output, easier to reason about while learning
    )

    reply = response.choices[0].message.content
    history.append({"role": "assistant", "content": reply})  # store the model's own reply too
    return reply
