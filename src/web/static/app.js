const API_BASE_URL = window.APP_CONFIG?.API_BASE_URL || "";
let currentConversationId = null;
let renamingConversationId = null;

const form = document.querySelector("#chatForm");
const input = document.querySelector("#messageInput");
const messages = document.querySelector("#messages");
const statusText = document.querySelector("#statusText");
const sendButton = document.querySelector("#sendButton");
const conversationList = document.querySelector("#conversationList");
const newChatButton = document.querySelector("#newChatButton");
const conversationSidebar = document.querySelector("#conversationSidebar");
const openSidebarButton = document.querySelector("#openSidebarButton");
const closeSidebarButton = document.querySelector("#closeSidebarButton");
const sidebarBackdrop = document.querySelector("#sidebarBackdrop");
const brandButtons = document.querySelectorAll("[data-brand-choice]");
const modeToggle = document.querySelector("#modeToggle");
const modeIcon = document.querySelector("#modeIcon");
const modeLabel = document.querySelector("#modeLabel");
const themeColorMeta = document.querySelector('meta[name="theme-color"]');
const mobileLayout = window.matchMedia("(max-width: 760px)");

const BRAND_STORAGE_KEY = "personal-assistant-brand";
const MODE_STORAGE_KEY = "personal-assistant-mode";
const BRANDS = new Set(["mercedes", "ferrari"]);
const MODES = new Set(["light", "dark"]);

const statusLabels = {
  agent_started: "Starting assistant...",
  assistant_response_ready: "Response ready",
  reasoning_available: "Reasoning step completed",
  tool_call_requested: "Preparing a tool...",
  tool_result_received: "Tool result received",
  agent_finished: "Done",
  conversation_ready: "Conversation ready",
  conversation_title_updated: "Conversation titled",
  run_error: "Run stopped",
};

const hiddenTraceCodes = new Set([
  "conversation_ready",
  "assistant_response_ready",
  "conversation_title_updated",
]);

function applyAppearance(brand, mode) {
  const nextBrand = BRANDS.has(brand) ? brand : "mercedes";
  const nextMode = MODES.has(mode) ? mode : "light";
  const isDark = nextMode === "dark";

  document.documentElement.dataset.brand = nextBrand;
  document.documentElement.dataset.mode = nextMode;

  for (const button of brandButtons) {
    button.setAttribute(
      "aria-pressed",
      String(button.dataset.brandChoice === nextBrand),
    );
  }

  modeToggle.setAttribute("aria-pressed", String(isDark));
  modeToggle.setAttribute(
    "aria-label",
    isDark ? "Switch to light mode" : "Switch to dark mode",
  );
  modeIcon.textContent = isDark ? "☾" : "☀";
  modeLabel.textContent = isDark ? "Dark" : "Light";

  const themeColors = {
    "mercedes-light": "#f3f5f5",
    "mercedes-dark": "#0b0f10",
    "ferrari-light": "#f6f3ef",
    "ferrari-dark": "#110e0d",
  };
  themeColorMeta.setAttribute("content", themeColors[`${nextBrand}-${nextMode}`]);

  try {
    localStorage.setItem(BRAND_STORAGE_KEY, nextBrand);
    localStorage.setItem(MODE_STORAGE_KEY, nextMode);
  } catch {
    // The selected appearance still applies when browser storage is unavailable.
  }
}

function setConversationDrawer(isOpen, restoreFocus = false) {
  const shouldOpen = isOpen && mobileLayout.matches;
  conversationSidebar.classList.toggle("is-open", shouldOpen);
  sidebarBackdrop.classList.toggle("is-visible", shouldOpen);
  openSidebarButton.setAttribute("aria-expanded", String(shouldOpen));

  if (shouldOpen) {
    closeSidebarButton.focus();
  } else if (restoreFocus && mobileLayout.matches) {
    openSidebarButton.focus();
  }
}

function resizeMessageInput() {
  input.style.height = "auto";
  input.style.height = `${Math.min(input.scrollHeight, 160)}px`;
}

function clearWelcomeState() {
  document.querySelector("#welcomeState")?.remove();
}

function renderWelcomeState() {
  messages.innerHTML = `
    <div id="welcomeState" class="welcome-state">
      <span class="welcome-mark" aria-hidden="true">AI</span>
      <h2>How can I help?</h2>
      <p>Ask about your notes, schedule, todos, or anything you are working through.</p>
    </div>
  `;
}

applyAppearance(
  document.documentElement.dataset.brand,
  document.documentElement.dataset.mode,
);

for (const button of brandButtons) {
  button.addEventListener("click", () => {
    applyAppearance(
      button.dataset.brandChoice,
      document.documentElement.dataset.mode,
    );
  });
}

modeToggle.addEventListener("click", () => {
  const nextMode = document.documentElement.dataset.mode === "dark" ? "light" : "dark";
  applyAppearance(document.documentElement.dataset.brand, nextMode);
});

openSidebarButton.addEventListener("click", () => setConversationDrawer(true));
closeSidebarButton.addEventListener("click", () => setConversationDrawer(false, true));
sidebarBackdrop.addEventListener("click", () => setConversationDrawer(false, true));

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && conversationSidebar.classList.contains("is-open")) {
    setConversationDrawer(false, true);
  }
});

mobileLayout.addEventListener("change", () => setConversationDrawer(false));

input.addEventListener("input", resizeMessageInput);
input.addEventListener("keydown", (event) => {
  if (
    event.key === "Enter"
    && !event.shiftKey
    && !event.isComposing
    && !sendButton.disabled
  ) {
    event.preventDefault();
    form.requestSubmit();
  }
});


function renderMarkdown(target, text) {
  const html = marked.parse(text);
  target.innerHTML = DOMPurify.sanitize(html);
}

function addMessage(role, text = "") {
  clearWelcomeState();
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
  clearWelcomeState();
  const message = document.createElement("div");
  message.className = "message assistant";

  const answer = document.createElement("div");
  answer.className = "assistant-answer";

  const details = document.createElement("details");
  details.className = "agent-trace";
  details.open = true;
  details.hidden = true;

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
  const details = traceList.closest(".agent-trace");
  if (details) details.hidden = false;
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
        if (code !== "conversation_title_updated") {
          statusText.textContent = parsed.data.message || statusLabels[code] || code;
        }

        if (code === "conversation_ready") {
          currentConversationId = parsed.data.conversation_id;
          await loadConversations();
        }

        if (code === "conversation_title_updated") {
          if (parsed.data.conversation_id !== renamingConversationId) {
            await loadConversations();
          }
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

  if (sendButton.disabled) return;

  const message = input.value.trim();
  if (!message) return;

  input.value = "";
  resizeMessageInput();

  try {
    await sendMessage(message);
  } catch (error) {
    addMessage("assistant", `Error: ${error.message}`);
    statusText.textContent = "Error";
    sendButton.disabled = false;
  }
});

newChatButton.addEventListener("click", async () => {
  setConversationDrawer(false, true);
  currentConversationId = null;
  renderWelcomeState();
  statusText.textContent = "Ready";
  await loadConversations();
});

async function loadConversations() {
  const response = await fetch(`${API_BASE_URL}/conversations`, {
    credentials: "include",
  });

  if (!response.ok) return [];

  const conversations = await response.json();
  conversationList.innerHTML = "";

  if (conversations.length === 0) {
    const empty = document.createElement("p");
    empty.className = "conversation-empty";
    empty.textContent = "Your conversations will appear here.";
    conversationList.appendChild(empty);
    return conversations;
  }

  for (const conversation of conversations) {
    const row = document.createElement("div");
    row.className = "conversation-row";

    const button = document.createElement("button");
    button.type = "button";
    button.className = "conversation-item";
    if (conversation.id === currentConversationId) {
      button.classList.add("active");
    }

    button.textContent = conversation.title || "New conversation";
    button.title = button.textContent;
    button.addEventListener("click", () => openConversation(conversation.id));

    const editButton = document.createElement("button");
    editButton.type = "button";
    editButton.className = "conversation-edit-button";
    editButton.setAttribute("aria-label", `Rename ${button.textContent}`);
    editButton.title = "Rename conversation";
    editButton.textContent = "\u270E";
    editButton.addEventListener("click", () => {
      startConversationRename(row, conversation);
    });

    row.appendChild(button);
    row.appendChild(editButton);
    conversationList.appendChild(row);
  }

  return conversations;
}

function startConversationRename(row, conversation) {
  renamingConversationId = conversation.id;
  const renameForm = document.createElement("form");
  renameForm.className = "conversation-rename-form";

  const titleInput = document.createElement("input");
  titleInput.className = "conversation-title-input";
  titleInput.type = "text";
  titleInput.value = conversation.title || "";
  titleInput.maxLength = 80;
  titleInput.required = true;
  titleInput.setAttribute("aria-label", "Conversation title");

  const saveButton = document.createElement("button");
  saveButton.className = "conversation-rename-action save";
  saveButton.type = "submit";
  saveButton.textContent = "Save";

  const cancelButton = document.createElement("button");
  cancelButton.className = "conversation-rename-action";
  cancelButton.type = "button";
  cancelButton.textContent = "Cancel";
  cancelButton.addEventListener("click", () => {
    renamingConversationId = null;
    loadConversations();
  });

  titleInput.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      event.preventDefault();
      renamingConversationId = null;
      loadConversations();
    }
  });

  renameForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const title = titleInput.value.trim();
    if (!title) return;

    saveButton.disabled = true;
    let response;
    try {
      response = await fetch(
        `${API_BASE_URL}/conversations/${conversation.id}/title`,
        {
          method: "PATCH",
          credentials: "include",
          headers: {
            "Content-Type": "application/json",
          },
          body: JSON.stringify({ title }),
        },
      );
    } catch {
      statusText.textContent = "Could not rename conversation";
      saveButton.disabled = false;
      return;
    }

    if (!response.ok) {
      statusText.textContent = "Could not rename conversation";
      saveButton.disabled = false;
      return;
    }

    statusText.textContent = "Ready";
    renamingConversationId = null;
    await loadConversations();
  });

  renameForm.appendChild(titleInput);
  renameForm.appendChild(saveButton);
  renameForm.appendChild(cancelButton);
  row.replaceChildren(renameForm);
  titleInput.focus();
  titleInput.select();
}

async function openConversation(conversationId) {
  setConversationDrawer(false, true);
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
