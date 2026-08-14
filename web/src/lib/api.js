/**
 * The one place that knows where the backend is and how to talk to it.
 *
 * In development Vite proxies /api, /chat and /conversations to FastAPI on
 * :8000, so the base is empty. In production the frontend is on Cloudflare
 * Pages and the API is behind a Cloudflare Tunnel on its own subdomain, so
 * every request is cross-origin and must carry the Access cookie.
 */

const LOCAL_HOSTS = new Set(["localhost", "127.0.0.1", "[::1]"]);

export const API_BASE_URL = LOCAL_HOSTS.has(window.location.hostname)
  ? ""
  : import.meta.env.VITE_API_BASE_URL ?? "https://api.bojiakpui-xyz-student-web-app.me";

class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.status = status;
  }
}

async function request(path, options = {}) {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    credentials: "include",
    headers: options.body ? { "Content-Type": "application/json" } : undefined,
    ...options,
  });

  if (!response.ok) {
    let detail = `${response.status}`;
    try {
      const body = await response.json();
      detail = body.detail ?? detail;
    } catch {
      // Non-JSON error body. The status alone will have to do.
    }
    throw new ApiError(typeof detail === "string" ? detail : JSON.stringify(detail), response.status);
  }

  return response.status === 204 ? null : response.json();
}

const json = (body) => ({ body: JSON.stringify(body) });

/* --- conversations ------------------------------------------------------- */

export const listConversations = () => request("/conversations");
export const getConversation = (id) => request(`/conversations/${id}`);
export const deleteConversation = (id) => request(`/conversations/${id}`, { method: "DELETE" });
export const renameConversation = (id, title) =>
  request(`/conversations/${id}/title`, { method: "PATCH", ...json({ title }) });

/* --- calendar ------------------------------------------------------------ */

export const getToday = () => request("/api/calendar/today");

/* --- finance ------------------------------------------------------------- */

export const getSummary = (month) => request(`/api/finance/summary?month=${month}`);
export const getOverview = (month) => request(`/api/finance/overview?month=${month}`);
export const listTransactions = (params) =>
  request(`/api/finance/transactions?${new URLSearchParams(params)}`);
export const createTransaction = (body) =>
  request("/api/finance/transactions", { method: "POST", ...json(body) });
export const updateTransaction = (code, body) =>
  request(`/api/finance/transactions/${code}`, { method: "PATCH", ...json(body) });
export const removeTransaction = (code) =>
  request(`/api/finance/transactions/${code}`, { method: "DELETE" });
export const listCategories = () => request("/api/finance/categories");
export const createCategory = (body) =>
  request("/api/finance/categories", { method: "POST", ...json(body) });
export const updateCategory = (name, body) =>
  request(`/api/finance/categories/${encodeURIComponent(name)}`, { method: "PATCH", ...json(body) });
export const listSubcategories = (category) =>
  request(`/api/finance/subcategories?category=${encodeURIComponent(category)}`);
export const listAccounts = () => request("/api/finance/accounts");
export const listBudgets = (month) => request(`/api/finance/budgets?month=${month}`);
export const setBudget = (body) => request("/api/finance/budgets", { method: "PUT", ...json(body) });
export const removeBudget = (code) => request(`/api/finance/budgets/${code}`, { method: "DELETE" });
export const getGoal = (month) => request(`/api/finance/goals?month=${month}`);
export const setGoal = (body) => request("/api/finance/goals", { method: "PUT", ...json(body) });
export const explainPeriod = (body) =>
  request("/api/finance/explain", { method: "POST", ...json(body) });

/* --- chat stream --------------------------------------------------------- */

/**
 * POST /chat/stream and hand back every SSE frame as it arrives.
 *
 * EventSource cannot POST, so this reads the body itself. Frames are
 * separated by a blank line; each carries an `event:` name and one `data:`
 * line of JSON, exactly as `_sse_event` in src/api/main.py writes them.
 */
export async function streamChat({ message, conversationId, signal, onFrame }) {
  const response = await fetch(`${API_BASE_URL}/chat/stream`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message, conversation_id: conversationId ?? null }),
    signal,
  });

  if (!response.ok || !response.body) {
    throw new ApiError(`Chat stream failed (${response.status})`, response.status);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });

    let split;
    while ((split = buffer.indexOf("\n\n")) !== -1) {
      const frame = buffer.slice(0, split);
      buffer = buffer.slice(split + 2);

      let name = "message";
      const dataLines = [];
      for (const line of frame.split("\n")) {
        if (line.startsWith("event:")) name = line.slice(6).trim();
        else if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
      }
      if (dataLines.length === 0) continue;

      try {
        onFrame(name, JSON.parse(dataLines.join("\n")));
      } catch {
        // A frame we cannot parse is not worth killing the run over.
      }
    }
  }
}
