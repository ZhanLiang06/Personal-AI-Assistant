const API_BASE_URL = window.APP_CONFIG?.API_BASE_URL || "";
const form = document.querySelector("#chatForm");
const input = document.querySelector("#messageInput");
const messages = document.querySelector("#messages");
const statusText = document.querySelector("#statusText");
const sendButton = document.querySelector("#sendButton");
const statusLabels = {
  starting_agent: "Starting assistant...",
  running_agent: "Thinking...",
  agent_completed: "Done",
};

function addMessage(role, text) {
  const message = document.createElement("div");
  message.className = `message ${role}`;

  if (role === "assistant") {
    const html = marked.parse(text);
    message.innerHTML = DOMPurify.sanitize(html);
  } else {
    message.textContent = text;
  }

  messages.appendChild(message);
  messages.scrollTop = messages.scrollHeight;
}

function parseSseEvent(rawEvent) {
  const lines = rawEvent.split("\n");
  let event = "message";
  let data = "";

  for (const line of lines) {
    if (line.startsWith("event:")) {
      event = line.slice(6).trim();
    }

    if (line.startsWith("data:")) {
      data += line.slice(5).trim();
    }
  }

  return {
    event,
    data: JSON.parse(data),
  };
}

async function sendMessage(message) {
  addMessage("user", message);
  statusText.textContent = "Starting...";
  sendButton.disabled = true;

  const response = await fetch(`${API_BASE_URL}/chat/stream`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ message }),
  });

  if (!response.ok || !response.body) {
    throw new Error(`Request failed with status ${response.status}`);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });

    const parts = buffer.split("\n\n");
    buffer = parts.pop();

    for (const part of parts) {
      if (!part.trim()) continue;

      const parsed = parseSseEvent(part);

      if (parsed.event === "status") {
        const code = parsed.data.code;
        statusText.textContent = statusLabels[code] || code;
      }

      if (parsed.event === "final") {
        addMessage("assistant", parsed.data.reply);
        statusText.textContent = "Ready";
      }

      if (parsed.event === "error") {
        addMessage("assistant", `Error: ${parsed.data.detail}`);
        statusText.textContent = "Error";
      }
    }
  }

  sendButton.disabled = false;
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();

  const message = input.value.trim();
  if (!message) return;

  input.value = "";

  try {
    await sendMessage(message);
  } catch (error) {
    addMessage("assistant", `Error: ${error.message}`);
    statusText.textContent = "Error";
    sendButton.disabled = false;
  }
});