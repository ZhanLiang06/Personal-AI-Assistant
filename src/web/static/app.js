const API_BASE_URL = window.APP_CONFIG?.API_BASE_URL || "";
let currentConversationId = null;

const form = document.querySelector("#chatForm");
const input = document.querySelector("#messageInput");
const messages = document.querySelector("#messages");
const statusText = document.querySelector("#statusText");
const sendButton = document.querySelector("#sendButton");
const conversationList = document.querySelector("#conversationList");
const newChatButton = document.querySelector("#newChatButton");

const statusLabels = {
  agent_started: "Starting assistant...",
  assistant_response_ready: "Response ready",
  reasoning_available: "Reasoning step completed",
  tool_call_requested: "Preparing a tool...",
  tool_result_received: "Tool result received",
  agent_finished: "Done",
  conversation_ready: "Conversation ready",
  run_error: "Run stopped",
};

const hiddenTraceCodes = new Set([
  "conversation_ready",
  "assistant_response_ready",
]);


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
    body: JSON.stringify({
      message,
      conversation_id: currentConversationId,
    }),
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

        if (code === "conversation_ready") {
          currentConversationId = parsed.data.conversation_id;
          await loadConversations();
        }

        if (!hiddenTraceCodes.has(code)) {
          appendTraceEvent(assistantRun.traceList, parsed.data);
        }
      }

      if (parsed.event === "final") {
        renderMarkdown(assistantRun.answer, parsed.data.reply);
        assistantRun.details.open = false;
        statusText.textContent = "Ready";
      }

      if (parsed.event === "error") {
        renderMarkdown(assistantRun.answer, "Run stopped before final response.");
        appendTraceEvent(assistantRun.traceList, {
          code: "run_error",
          message: "Run stopped before final response",
          result_preview: parsed.data.detail,
        });
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

newChatButton.addEventListener("click", async () => {
  currentConversationId = null;
  messages.innerHTML = "";
  statusText.textContent = "Ready";
  await loadConversations();
});

async function loadConversations() {
  const response = await fetch(`${API_BASE_URL}/conversations`, {
    credentials: "include",
  });

  if (!response.ok) return;

  const conversations = await response.json();
  conversationList.innerHTML = "";

  for (const conversation of conversations) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "conversation-item";
    if (conversation.id === currentConversationId) {
      button.classList.add("active");
    }

    button.textContent = conversation.title || "New conversation";
    button.addEventListener("click", () => openConversation(conversation.id));
    conversationList.appendChild(button);
  }
}

async function openConversation(conversationId) {
  const response = await fetch(`${API_BASE_URL}/conversations/${conversationId}`, {
    credentials: "include",
  });

  if (!response.ok) {
    addMessage("assistant", `Error: failed to open conversation ${conversationId}`);
    return;
  }

  const detail = await response.json();
  currentConversationId = detail.conversation.id;
  renderConversationEvents(detail.events);
  await loadConversations();
}

function renderConversationEvents(events) {
  messages.innerHTML = "";
  const runs = new Map();

  for (const event of events) {
    if (event.event_type === "user_message") {
      addMessage("user", event.content || "");
      continue;
    }

    if (event.event_type === "assistant_message") {
      const run = getOrCreateRun(runs, event.run_id);
      renderMarkdown(run.answer, event.content || "");
      run.details.open = false;
      continue;
    }

    if (event.event_type === "tool_call") {
      const run = getOrCreateRun(runs, event.run_id);
      appendTraceEvent(run.traceList, {
        code: "tool_call_requested",
        message: `Tool call requested: ${event.tool_name || "tool"}`,
        tool_name: event.tool_name,
        tool_args: parseJsonOrNull(event.tool_args_json),
      });
      continue;
    }

    if (event.event_type === "tool_result") {
      const run = getOrCreateRun(runs, event.run_id);
      appendTraceEvent(run.traceList, {
        code: "tool_result_received",
        message: `Tool result received from ${event.tool_name || "tool"}`,
        tool_name: event.tool_name,
        result_preview: event.tool_result_preview || event.tool_result,
      });
      continue;
    }

    if (event.event_type === "run_error") {
      const run = getOrCreateRun(runs, event.run_id);
      renderMarkdown(run.answer, "Run stopped before final response.");
      appendTraceEvent(run.traceList, {
        code: "run_error",
        message: "Run stopped before final response",
        result_preview: event.content,
      });
      run.details.open = true;
    }
  }

  messages.scrollTop = messages.scrollHeight;
}

function getOrCreateRun(runs, runId) {
  const key = runId || `run-${runs.size}`;

  if (!runs.has(key)) {
    runs.set(key, createAssistantRunMessage());
  }

  return runs.get(key);
}

function parseJsonOrNull(value) {
  if (!value) return null;

  try {
    return JSON.parse(value);
  } catch {
    return value;
  }
}

loadConversations();
