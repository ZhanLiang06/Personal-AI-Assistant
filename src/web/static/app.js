const API_BASE_URL = window.APP_CONFIG?.API_BASE_URL || "";
const form = document.querySelector("#chatForm");
const input = document.querySelector("#messageInput");
const messages = document.querySelector("#messages");
const statusText = document.querySelector("#statusText");
const sendButton = document.querySelector("#sendButton");
const statusLabels = {
  agent_started: "Starting assistant...",
  assistant_response_ready: "Response ready",
  reasoning_available: "Reasoning step completed",
  tool_call_requested: "Preparing a tool...",
  tool_result_received: "Tool result received",
  agent_finished: "Done",
};

function renderMarkdown(target, text) {
  const html = marked.parse(text);
  target.innerHTML = DOMPurify.sanitize(html);
}

function addMessage(role, text = "") {
  const message = document.createElement("div");
  message.className = `message ${role}`;

  if (role === "assistant") {
    renderMarkdown(message, text);
  } else {
    message.textContent = text;
  }

  messages.appendChild(message);
  messages.scrollTop = messages.scrollHeight;
  return message;
}

function createAssistantRunMessage() {
  const message = document.createElement("div");
  message.className = "message assistant";

  const answer = document.createElement("div");
  answer.className = "assistant-answer";

  const details = document.createElement("details");
  details.className = "agent-trace";
  details.open = true;

  const summary = document.createElement("summary");
  summary.textContent = "View process";

  const list = document.createElement("div");
  list.className = "agent-trace-list";

  details.appendChild(summary);
  details.appendChild(list);
  message.appendChild(answer);
  message.appendChild(details);

  messages.appendChild(message);
  messages.scrollTop = messages.scrollHeight;

  return { message, answer, traceList: list, details };
}

function appendTraceEvent(traceList, data) {
  const item = document.createElement("div");
  item.className = "agent-trace-item";

  const title = document.createElement("div");
  title.className = "agent-trace-title";
  title.textContent = data.message || statusLabels[data.code] || data.code;

  item.appendChild(title);

  const detailLines = [];
  if (Number.isInteger(data.elapsed_ms)) {
    detailLines.push(`elapsed: ${data.elapsed_ms} ms`);
  }

  if (Number.isInteger(data.step_ms)) {
    detailLines.push(`step: ${data.step_ms} ms`);
  }

  if (data.tool_name) detailLines.push(`tool: ${data.tool_name}`);
  if (data.tool_args) detailLines.push(`args: ${JSON.stringify(data.tool_args)}`);
  if (data.result_preview) detailLines.push(`preview: ${data.result_preview}`);

  if (detailLines.length > 0) {
    const pre = document.createElement("pre");
    pre.className = "agent-trace-detail";
    pre.textContent = detailLines.join("\n");
    item.appendChild(pre);
  }

  traceList.appendChild(item);
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
  const assistantRun = createAssistantRunMessage();
  statusText.textContent = "Starting...";
  sendButton.disabled = true;

  const response = await fetch(`${API_BASE_URL}/chat/stream`, {
    method: "POST",
    credentials: "include",
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
        statusText.textContent = parsed.data.message || statusLabels[code] || code;
        appendTraceEvent(assistantRun.traceList, parsed.data);
      }

      if (parsed.event === "final") {
        renderMarkdown(assistantRun.answer, parsed.data.reply);
        assistantRun.details.open = false;
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