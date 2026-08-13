/*
 * Finance dashboard client.
 *
 * Plain HTML/CSS/JS, matching the chat shell. No framework, no build step.
 *
 * Two rules shape this file:
 *
 * - Money is never parsed into a JavaScript number. The API sends integer
 *   minor units plus a preformatted display string; this file renders the
 *   string and does arithmetic only on the integers. `parseFloat` on money
 *   would reintroduce exactly the precision bug the backend avoids.
 * - The page reloads its snapshot after every edit it makes. Widgets are
 *   rebuilt from one /overview response, so a chart can never disagree
 *   with the table beneath it.
 */

const API_BASE_URL = (window.APP_CONFIG && window.APP_CONFIG.API_BASE_URL) || "";
const API = `${API_BASE_URL}/api/finance`;

const state = {
  month: null,
  overview: null,
  categoryFilter: "",
  search: "",
};

const el = (id) => document.getElementById(id);

// --- Utilities ------------------------------------------------------

function currentMonthString() {
  const now = new Date();
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}`;
}

function shiftMonth(month, delta) {
  const [year, index] = month.split("-").map(Number);
  const shifted = new Date(year, index - 1 + delta, 1);
  return `${shifted.getFullYear()}-${String(shifted.getMonth() + 1).padStart(2, "0")}`;
}

function setStatus(text) {
  el("statusText").textContent = text;
}

function showError(message) {
  const banner = el("errorBanner");
  banner.textContent = message;
  banner.hidden = !message;
}

async function api(path, options = {}) {
  const response = await fetch(`${API}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });

  if (!response.ok) {
    let detail = `Request failed (${response.status})`;
    try {
      const body = await response.json();
      if (body && body.detail) {
        // FastAPI validation errors arrive as a list of objects.
        detail = typeof body.detail === "string"
          ? body.detail
          : body.detail.map((item) => item.msg || String(item)).join("; ");
      }
    } catch {
      // Keep the status-code message.
    }
    throw new Error(detail);
  }

  if (response.status === 204) return null;
  return response.json();
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (character) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[character]));
}

function formatDay(isoTimestamp) {
  // occurred_at is naive local time, so it is already the wall clock the
  // user recorded. Slicing avoids Date() shifting it by a timezone.
  const [datePart, timePart = ""] = isoTimestamp.split("T");
  const [, month, day] = datePart.split("-");
  return `${day}/${month} ${timePart.slice(0, 5)}`.trim();
}

// --- Charts ---------------------------------------------------------

/*
 * Daily spend: one measure over time, so a single-hue bar chart with a
 * zero baseline. No legend (one series), no per-bar labels; the hover
 * tooltip carries the exact figure.
 */
function renderDailyChart(daily) {
  const host = el("dailyChart");
  const tooltip = el("dailyTooltip");

  if (!daily.length) {
    host.innerHTML = "";
    return;
  }

  const width = 720;
  const height = 180;
  const padding = { top: 12, right: 8, bottom: 22, left: 8 };
  const plotWidth = width - padding.left - padding.right;
  const plotHeight = height - padding.top - padding.bottom;

  const highest = Math.max(...daily.map((entry) => entry.expense.minor), 1);
  const slot = plotWidth / daily.length;
  // 2px of surface between adjacent fills.
  const barWidth = Math.max(2, slot - 2);

  const bars = daily.map((entry, index) => {
    const value = entry.expense.minor;
    const barHeight = value > 0 ? Math.max(2, (value / highest) * plotHeight) : 1;
    const x = padding.left + index * slot;
    const y = padding.top + plotHeight - barHeight;

    return `<rect class="bar${value > 0 ? "" : " is-empty"}"
      x="${x.toFixed(2)}" y="${y.toFixed(2)}"
      width="${barWidth.toFixed(2)}" height="${barHeight.toFixed(2)}"
      data-day="${entry.day}" data-amount="${entry.expense.display}"
      data-count="${entry.transaction_count}"></rect>`;
  }).join("");

  // Only the first, middle and last day are labelled: a label per day
  // would collide on a 31-day month.
  const ticks = [0, Math.floor(daily.length / 2), daily.length - 1]
    .map((index) => {
      const x = padding.left + index * slot + barWidth / 2;
      const label = daily[index].day.slice(-2);
      const anchor = index === 0 ? "start" : index === daily.length - 1 ? "end" : "middle";
      return `<text class="axis-label" x="${x.toFixed(2)}" y="${height - 6}"
        text-anchor="${anchor}">${label}</text>`;
    }).join("");

  host.innerHTML = `<svg viewBox="0 0 ${width} ${height}" preserveAspectRatio="none"
      role="img" aria-label="Daily spending for the month">
      ${bars}
      <line class="axis-line" x1="${padding.left}" y1="${padding.top + plotHeight}"
        x2="${width - padding.right}" y2="${padding.top + plotHeight}"></line>
      ${ticks}
    </svg>`;

  host.querySelectorAll(".bar").forEach((bar) => {
    bar.addEventListener("mousemove", (event) => {
      const count = bar.dataset.count;
      tooltip.hidden = false;
      tooltip.innerHTML = `<strong>${bar.dataset.day}</strong><br>MYR ${bar.dataset.amount}`
        + `<br>${count} transaction${count === "1" ? "" : "s"}`;
      tooltip.style.left = `${event.clientX + 14}px`;
      tooltip.style.top = `${event.clientY - 10}px`;
    });
    bar.addEventListener("mouseleave", () => { tooltip.hidden = true; });
  });
}

/*
 * Category ranking: magnitude of one measure, so bars share a single hue
 * and each row is directly labelled. Colour carries no identity here.
 */
function renderCategoryChart(byCategory) {
  const host = el("categoryChart");
  const spending = byCategory.filter((entry) => entry.expense.minor > 0);

  if (!spending.length) {
    host.innerHTML = '<p class="card-note">No spending recorded this month.</p>';
    return;
  }

  const highest = Math.max(...spending.map((entry) => entry.expense.minor));

  host.innerHTML = spending.map((entry) => {
    const share = (entry.expense.minor / highest) * 100;
    const label = entry.emoji ? `${entry.emoji} ${entry.category}` : entry.category;
    const active = state.categoryFilter === entry.category ? " is-active" : "";

    return `<button class="category-row${active}" type="button"
        data-category="${escapeHtml(entry.category)}"
        aria-pressed="${state.categoryFilter === entry.category}">
        <span class="category-name">${escapeHtml(label)}</span>
        <span class="category-amount">${entry.expense.display}</span>
        <span class="category-track">
          <span class="category-fill" style="width: ${share.toFixed(1)}%"></span>
        </span>
      </button>`;
  }).join("");

  host.querySelectorAll(".category-row").forEach((row) => {
    row.addEventListener("click", () => {
      const chosen = row.dataset.category;
      state.categoryFilter = state.categoryFilter === chosen ? "" : chosen;
      el("categoryFilter").value = state.categoryFilter;
      refreshTable();
      renderCategoryChart(state.overview.summary.by_category);
    });
  });
}

// --- Sections -------------------------------------------------------

function renderStats(overview) {
  const summary = overview.summary;
  const comparison = overview.comparison;

  el("statExpense").textContent = `MYR ${summary.total_expense.display}`;
  el("statIncome").textContent = `MYR ${summary.total_income.display}`;
  el("statNet").textContent = `MYR ${summary.net.display}`;
  el("statAverage").textContent = `MYR ${summary.average_daily_expense.display}`;
  el("statCount").textContent = `${summary.transaction_count} transaction${
    summary.transaction_count === 1 ? "" : "s"}`;

  const delta = el("statExpenseDelta");
  delta.classList.remove("is-up", "is-down");

  if (comparison.expense_change_percent === null) {
    delta.textContent = comparison.previous_expense.minor === 0
      ? "no spending the month before"
      : "";
    return;
  }

  // Comparison against zero is excluded above, so the sign is meaningful.
  const percent = comparison.expense_change_percent;
  const rising = comparison.expense_delta.minor > 0;
  delta.classList.add(rising ? "is-up" : "is-down");
  // The words carry the direction; the colour only reinforces it.
  delta.textContent = `${rising ? "up" : "down"} ${percent.replace("-", "")}% vs last month`;
}

function renderBudgets(budgets) {
  const host = el("budgetList");

  if (!budgets.length) {
    host.innerHTML = '<p class="card-note">No budgets set for this month.</p>';
    return;
  }

  host.innerHTML = budgets.map((budget) => {
    const used = Number(budget.percent_used);
    const capped = Math.min(used, 100);
    // Status is named in words as well as colour.
    const [stateClass, stateText] = budget.is_over
      ? ["is-over", `Over by MYR ${budget.remaining.display.replace("-", "")}`]
      : used >= 80
        ? ["is-warning", `MYR ${budget.remaining.display} left`]
        : ["is-good", `MYR ${budget.remaining.display} left`];

    const label = budget.category_emoji
      ? `${budget.category_emoji} ${budget.category}`
      : budget.category;

    return `<div class="budget-row">
        <span class="budget-name">${escapeHtml(label)}</span>
        <span class="budget-numbers">${budget.spent.display} / ${budget.limit.display}</span>
        <span class="budget-track">
          <span class="budget-fill ${stateClass}" style="width: ${capped.toFixed(1)}%"></span>
        </span>
        <span class="budget-state ${stateClass}">
          ${escapeHtml(stateText)} &middot; ${budget.percent_used}% used
          <button class="row-button is-danger" type="button"
            data-budget="${escapeHtml(budget.code)}">Remove</button>
        </span>
      </div>`;
  }).join("");

  host.querySelectorAll("[data-budget]").forEach((button) => {
    button.addEventListener("click", async () => {
      await mutate(() => api(`/budgets/${button.dataset.budget}`, { method: "DELETE" }));
    });
  });
}

function renderGoal(goal, summary) {
  el("goalIncome").value = goal && goal.target_income ? goal.target_income.decimal : "";
  el("goalSavings").value = goal && goal.target_savings ? goal.target_savings.decimal : "";
  el("goalNotes").value = goal && goal.notes ? goal.notes : "";

  const progress = el("goalProgress");

  if (!goal || !goal.target_savings) {
    progress.textContent = "";
    return;
  }

  // Saved = income minus expense, which is exactly the net the backend
  // already computed. No arithmetic on money strings here.
  const savedMinor = summary.net.minor;
  const targetMinor = goal.target_savings.minor;
  const reached = savedMinor >= targetMinor;

  progress.textContent = reached
    ? `Savings target met: net MYR ${summary.net.display} against a target of MYR ${goal.target_savings.display}.`
    : `Net so far is MYR ${summary.net.display}, against a savings target of MYR ${goal.target_savings.display}.`;
}

function renderTable(transactions) {
  const body = el("transactionBody");
  el("tableEmpty").hidden = transactions.length > 0;

  // Kept so an inline editor can read the row it is editing without
  // refetching it.
  state.visible = transactions;

  body.innerHTML = transactions.map((item) => {
    const isIncome = item.direction === "income";
    const foreign = item.amount.currency !== item.base_amount.currency;

    return `<tr>
        <td class="transaction-code">${escapeHtml(item.code)}</td>
        <td>${escapeHtml(formatDay(item.occurred_at))}</td>
        <td>${escapeHtml(item.category)}${
          item.subcategory ? ` &middot; ${escapeHtml(item.subcategory)}` : ""}</td>
        <td class="transaction-note" title="${escapeHtml(item.note || "")}">${
          escapeHtml(item.note || "")}</td>
        <td class="numeric">${foreign
          ? `${escapeHtml(item.amount.currency)} ${item.amount.display}`
          : ""}</td>
        <td class="numeric${isIncome ? " direction-income" : ""}">${
          isIncome ? "+" : "&minus;"}${item.base_amount.display}</td>
        <td>
          <div class="row-actions">
            <button class="row-button" type="button" data-edit="${escapeHtml(item.code)}">Edit</button>
            <button class="row-button is-danger" type="button" data-delete="${escapeHtml(item.code)}">Delete</button>
          </div>
        </td>
      </tr>`;
  }).join("");

  body.querySelectorAll("[data-delete]").forEach((button) => {
    button.addEventListener("click", async () => {
      const code = button.dataset.delete;
      if (!window.confirm(`Delete ${code}? This can be undone only in the database.`)) {
        return;
      }
      await mutate(() => api(`/transactions/${code}`, { method: "DELETE" }));
    });
  });

  body.querySelectorAll("[data-edit]").forEach((button) => {
    button.addEventListener("click", () => startEdit(button.dataset.edit));
  });
}

/*
 * Inline transaction editor.
 *
 * Replaces the row in place rather than using window.prompt: prompt is
 * unavailable in sandboxed frames and several automation contexts, where
 * it throws instead of returning, and it cannot show the currency or
 * validate anything.
 */
function startEdit(code) {
  const item = (state.visible || []).find((entry) => entry.code === code);
  if (!item) return;

  const row = el("transactionBody").querySelector(
    `[data-edit="${CSS.escape(code)}"]`,
  ).closest("tr");

  row.innerHTML = `<td class="transaction-code">${escapeHtml(code)}</td>
      <td colspan="5">
        <div class="manager-edit">
          <input class="amount-input" type="text" inputmode="decimal"
            value="${escapeHtml(item.amount.decimal)}"
            aria-label="Amount in ${escapeHtml(item.amount.currency)}" />
          <span class="card-note">${escapeHtml(item.amount.currency)}</span>
          <input class="note-input" type="text" placeholder="Note"
            value="${escapeHtml(item.note || "")}" aria-label="Note" />
        </div>
      </td>
      <td>
        <div class="row-actions">
          <button class="row-button" type="button" data-save-edit>Save</button>
          <button class="row-button" type="button" data-cancel-edit>Cancel</button>
        </div>
      </td>`;

  const amountInput = row.querySelector(".amount-input");
  const noteInput = row.querySelector(".note-input");
  amountInput.focus();
  amountInput.select();

  row.querySelector("[data-cancel-edit]").addEventListener("click", refreshTable);

  row.querySelector("[data-save-edit]").addEventListener("click", async () => {
    const payload = {};
    const amount = amountInput.value.trim();
    const note = noteInput.value.trim();

    if (amount && amount !== item.amount.decimal) payload.amount = amount;
    if (note !== (item.note || "")) payload.note = note || null;

    if (!Object.keys(payload).length) {
      await refreshTable();
      return;
    }

    await mutate(() => api(`/transactions/${code}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    }));
  });

  for (const input of [amountInput, noteInput]) {
    input.addEventListener("keydown", (event) => {
      if (event.key === "Enter") row.querySelector("[data-save-edit]").click();
      if (event.key === "Escape") refreshTable();
    });
  }
}

/*
 * Category manager.
 *
 * Renaming is the point of this panel. A category is referenced by id,
 * so renaming carries every past transaction with it; deactivating and
 * recreating would split the history in two. The row states how many
 * transactions are affected before the user commits.
 */
function renderCategoryManager(overview) {
  const host = el("categoryManager");
  const spendByCategory = new Map(
    overview.summary.by_category.map((entry) => [
      entry.category,
      entry.transaction_count,
    ]),
  );

  const children = new Map();
  for (const sub of overview.subcategories) {
    if (!children.has(sub.category)) children.set(sub.category, []);
    children.get(sub.category).push(sub);
  }

  if (!overview.categories.length) {
    host.innerHTML = '<p class="card-note">No categories yet.</p>';
    return;
  }

  host.innerHTML = overview.categories.map((category) => {
    const label = category.emoji
      ? `${category.emoji} ${category.name}`
      : category.name;
    const used = spendByCategory.get(category.name) || 0;

    const parent = `<div class="manager-row" data-category-row="${escapeHtml(category.name)}">
        <span class="manager-label">${escapeHtml(label)}</span>
        <span class="manager-code">${escapeHtml(category.code)}</span>
        <span class="manager-actions">
          <button class="row-button" type="button"
            data-edit-category="${escapeHtml(category.name)}"
            data-emoji="${escapeHtml(category.emoji || "")}"
            data-used="${used}">Rename</button>
          <button class="row-button is-danger" type="button"
            data-hide-category="${escapeHtml(category.name)}">Hide</button>
        </span>
      </div>`;

    const subs = (children.get(category.name) || []).map((sub) =>
      `<div class="manager-row is-child" data-subcategory-row="${escapeHtml(sub.code)}">
        <span class="manager-label">${escapeHtml(sub.name)}</span>
        <span class="manager-code">${escapeHtml(sub.code)}</span>
        <span class="manager-actions">
          <button class="row-button" type="button"
            data-edit-subcategory="${escapeHtml(sub.name)}"
            data-parent="${escapeHtml(category.name)}">Rename</button>
        </span>
      </div>`).join("");

    return parent + subs;
  }).join("");

  host.querySelectorAll("[data-edit-category]").forEach((button) => {
    button.addEventListener("click", () => startCategoryEdit(button));
  });

  host.querySelectorAll("[data-edit-subcategory]").forEach((button) => {
    button.addEventListener("click", () => startSubcategoryEdit(button));
  });

  host.querySelectorAll("[data-hide-category]").forEach((button) => {
    button.addEventListener("click", async () => {
      const name = button.dataset.hideCategory;
      if (!window.confirm(
        `Hide "${name}"? Past transactions keep it, but it can no longer be `
        + "chosen for new ones.",
      )) {
        return;
      }
      await mutate(() => api(`/categories/${encodeURIComponent(name)}`, {
        method: "DELETE",
      }));
    });
  });
}

function startCategoryEdit(button) {
  const name = button.dataset.editCategory;
  const emoji = button.dataset.emoji;
  const used = Number(button.dataset.used);
  const row = button.closest(".manager-row");

  // A rename is visible throughout the user's history, so say how far
  // it reaches before they commit to it.
  const warning = used > 0
    ? `<p class="manager-warning">Renaming also relabels ${used} transaction${
      used === 1 ? "" : "s"} this month, and all past ones.</p>`
    : "";

  row.innerHTML = `<div class="manager-edit">
      <input class="emoji-input" type="text" maxlength="4" value="${escapeHtml(emoji)}"
        aria-label="Emoji" placeholder="none" />
      <input class="name-input" type="text" value="${escapeHtml(name)}" aria-label="Category name" />
      <button class="primary-button" type="button" data-save>Save</button>
      <button class="ghost-button" type="button" data-cancel>Cancel</button>
      ${warning}
    </div>`;

  const nameInput = row.querySelector(".name-input");
  const emojiInput = row.querySelector(".emoji-input");
  nameInput.focus();
  nameInput.select();

  row.querySelector("[data-cancel]").addEventListener("click", () => {
    renderCategoryManager(state.overview);
  });

  row.querySelector("[data-save]").addEventListener("click", async () => {
    const payload = {};
    const nextName = nameInput.value.trim();
    const nextEmoji = emojiInput.value.trim();

    if (nextName && nextName !== name) payload.new_name = nextName;
    // Sending null clears the emoji; omitting it leaves it alone.
    if (nextEmoji !== emoji) payload.emoji = nextEmoji || null;

    if (!Object.keys(payload).length) {
      renderCategoryManager(state.overview);
      return;
    }

    await mutate(() => api(`/categories/${encodeURIComponent(name)}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    }));
  });

  nameInput.addEventListener("keydown", (event) => {
    if (event.key === "Enter") row.querySelector("[data-save]").click();
    if (event.key === "Escape") renderCategoryManager(state.overview);
  });
}

function startSubcategoryEdit(button) {
  const name = button.dataset.editSubcategory;
  const parent = button.dataset.parent;
  const row = button.closest(".manager-row");

  row.innerHTML = `<div class="manager-edit">
      <input class="name-input" type="text" value="${escapeHtml(name)}"
        aria-label="Subcategory name" />
      <button class="primary-button" type="button" data-save>Save</button>
      <button class="ghost-button" type="button" data-cancel>Cancel</button>
      <p class="manager-warning">Stays under ${escapeHtml(parent)}; a subcategory cannot move.</p>
    </div>`;

  const nameInput = row.querySelector(".name-input");
  nameInput.focus();
  nameInput.select();

  row.querySelector("[data-cancel]").addEventListener("click", () => {
    renderCategoryManager(state.overview);
  });

  row.querySelector("[data-save]").addEventListener("click", async () => {
    const nextName = nameInput.value.trim();

    if (!nextName || nextName === name) {
      renderCategoryManager(state.overview);
      return;
    }

    await mutate(() => api(
      `/subcategories/${encodeURIComponent(parent)}/${encodeURIComponent(name)}`,
      { method: "PATCH", body: JSON.stringify({ new_name: nextName }) },
    ));
  });

  nameInput.addEventListener("keydown", (event) => {
    if (event.key === "Enter") row.querySelector("[data-save]").click();
    if (event.key === "Escape") renderCategoryManager(state.overview);
  });
}

/*
 * On-demand narration. Costs a model call, so it runs only when asked,
 * and it is always regenerated against the month currently on screen
 * rather than cached. That way the prose can never describe a period the
 * widgets are no longer showing.
 */
function resetExplanation() {
  const text = el("explainText");
  text.className = "explain-text is-placeholder";
  text.textContent =
    "The numbers above are always current. Press the button for a short written summary of them.";

  const figures = document.getElementById("explainFigures");
  if (figures) figures.remove();
}

async function explainPeriod() {
  const button = el("explainButton");
  const text = el("explainText");

  button.disabled = true;
  text.className = "explain-text is-pending";
  text.textContent = "Writing a summary of this month...";

  try {
    const result = await api("/explain", {
      method: "POST",
      body: JSON.stringify({ month: state.month }),
    });

    text.className = "explain-text";
    text.textContent = result.commentary;

    const existing = document.getElementById("explainFigures");
    if (existing) existing.remove();

    // The model was unavailable, so the panel is showing raw figures.
    // Say so plainly rather than passing them off as commentary.
    if (!result.narrated) {
      text.className = "explain-text is-placeholder";
      text.textContent = "Written commentary is unavailable, so here are the figures:";

      const block = document.createElement("pre");
      block.id = "explainFigures";
      block.className = "explain-figures";
      block.textContent = result.figures;
      text.after(block);
    }
  } catch (error) {
    text.className = "explain-text is-placeholder";
    text.textContent = `Could not generate a summary: ${error.message}`;
  } finally {
    button.disabled = false;
  }
}

// --- Loading --------------------------------------------------------

async function loadOverview() {
  setStatus("Loading");
  showError("");

  try {
    state.overview = await api(`/overview?month=${state.month}`);
  } catch (error) {
    setStatus("Error");
    showError(error.message);
    return;
  }

  const overview = state.overview;

  renderStats(overview);
  renderDailyChart(overview.summary.daily_totals);
  renderCategoryChart(overview.summary.by_category);
  renderBudgets(overview.budgets);
  renderGoal(overview.goal, overview.summary);
  renderCategoryManager(overview);

  populateSelect(el("addCategory"), overview.categories.map((c) => c.name));
  populateSelect(el("addAccount"), overview.accounts.map((a) => a.name));
  populateSelect(el("budgetCategory"), overview.categories.map((c) => c.name));
  populateSelect(
    el("categoryFilter"),
    overview.categories.map((c) => c.name),
    "All categories",
  );
  el("categoryFilter").value = state.categoryFilter;

  await refreshTable();
  // The previous month's prose must not linger over new numbers.
  resetExplanation();
  setStatus("Ready");
}

function populateSelect(select, values, emptyLabel) {
  const previous = select.value;
  const options = values.map(
    (value) => `<option value="${escapeHtml(value)}">${escapeHtml(value)}</option>`,
  );

  if (emptyLabel !== undefined) {
    options.unshift(`<option value="">${escapeHtml(emptyLabel)}</option>`);
  }

  select.innerHTML = options.join("");
  if (values.includes(previous) || previous === "") select.value = previous;
}

async function refreshTable() {
  const [year, month] = state.month.split("-");
  const lastDay = new Date(Number(year), Number(month), 0).getDate();

  const parameters = new URLSearchParams({
    start: `${state.month}-01`,
    end: `${state.month}-${String(lastDay).padStart(2, "0")}`,
    limit: "200",
  });

  if (state.categoryFilter) parameters.set("category", state.categoryFilter);
  if (state.search) parameters.set("search", state.search);

  try {
    const page = await api(`/transactions?${parameters}`);
    renderTable(page.transactions);
  } catch (error) {
    showError(error.message);
  }
}

/*
 * Every mutation goes through here, so the page always reloads its
 * snapshot afterwards. That is the "refresh after your own edits" rule:
 * no polling, but never a stale widget after something you changed.
 */
async function mutate(action) {
  showError("");
  setStatus("Saving");

  try {
    await action();
  } catch (error) {
    setStatus("Error");
    showError(error.message);
    return false;
  }

  await loadOverview();
  return true;
}

// --- Wiring ---------------------------------------------------------

function setMonth(month) {
  state.month = month;
  el("monthInput").value = month;
  loadOverview();
}

function wireTheme() {
  const root = document.documentElement;

  document.querySelectorAll("[data-brand-choice]").forEach((button) => {
    button.addEventListener("click", () => {
      const brand = button.dataset.brandChoice;
      root.dataset.brand = brand;
      try { localStorage.setItem("personal-assistant-brand", brand); } catch {}
      syncThemeButtons();
    });
  });

  el("modeToggle").addEventListener("click", () => {
    const next = root.dataset.mode === "dark" ? "light" : "dark";
    root.dataset.mode = next;
    try { localStorage.setItem("personal-assistant-mode", next); } catch {}
    syncThemeButtons();
  });

  syncThemeButtons();
}

function syncThemeButtons() {
  const root = document.documentElement;

  document.querySelectorAll("[data-brand-choice]").forEach((button) => {
    button.setAttribute(
      "aria-pressed",
      String(button.dataset.brandChoice === root.dataset.brand),
    );
  });

  const dark = root.dataset.mode === "dark";
  el("modeIcon").innerHTML = dark ? "&#9790;" : "&#9728;";
  el("modeLabel").textContent = dark ? "Dark" : "Light";
  el("modeToggle").setAttribute("aria-pressed", String(dark));
  el("modeToggle").setAttribute(
    "aria-label",
    dark ? "Switch to light mode" : "Switch to dark mode",
  );
}

function wireForms() {
  el("addForm").addEventListener("submit", async (event) => {
    event.preventDefault();

    const payload = {
      amount: el("addAmount").value.trim(),
      category: el("addCategory").value,
      currency: el("addCurrency").value.trim().toUpperCase() || "MYR",
      direction: el("addDirection").value,
      account: el("addAccount").value,
    };

    const subcategory = el("addSubcategory").value;
    if (subcategory) payload.subcategory = subcategory;

    const when = el("addWhen").value;
    if (when) payload.occurred_at = when.length === 16 ? `${when}:00` : when;

    const note = el("addNote").value.trim();
    if (note) payload.note = note;

    const saved = await mutate(() => api("/transactions", {
      method: "POST",
      body: JSON.stringify(payload),
    }));

    if (saved) {
      el("addAmount").value = "";
      el("addNote").value = "";
    }
  });

  // Subcategory choices depend on the chosen category.
  el("addCategory").addEventListener("change", async () => {
    const category = el("addCategory").value;
    const select = el("addSubcategory");

    if (!category) {
      select.innerHTML = '<option value="">None</option>';
      return;
    }

    try {
      const subs = await api(`/subcategories?category=${encodeURIComponent(category)}`);
      select.innerHTML = '<option value="">None</option>'
        + subs.map((s) => `<option value="${escapeHtml(s.name)}">${escapeHtml(s.name)}</option>`).join("");
    } catch (error) {
      showError(error.message);
    }
  });

  el("explainButton").addEventListener("click", explainPeriod);

  el("newCategoryForm").addEventListener("submit", async (event) => {
    event.preventDefault();

    const payload = { name: el("newCategoryName").value.trim() };
    const emoji = el("newCategoryEmoji").value.trim();
    if (emoji) payload.emoji = emoji;

    const saved = await mutate(() => api("/categories", {
      method: "POST",
      body: JSON.stringify(payload),
    }));

    if (saved) {
      el("newCategoryName").value = "";
      el("newCategoryEmoji").value = "";
    }
  });

  el("addBudgetButton").addEventListener("click", () => {
    el("budgetForm").hidden = !el("budgetForm").hidden;
  });

  el("cancelBudget").addEventListener("click", () => {
    el("budgetForm").hidden = true;
  });

  el("budgetForm").addEventListener("submit", async (event) => {
    event.preventDefault();

    const saved = await mutate(() => api("/budgets", {
      method: "PUT",
      body: JSON.stringify({
        month: state.month,
        category: el("budgetCategory").value,
        limit: el("budgetLimit").value.trim(),
      }),
    }));

    if (saved) {
      el("budgetLimit").value = "";
      el("budgetForm").hidden = true;
    }
  });

  el("goalForm").addEventListener("submit", async (event) => {
    event.preventDefault();

    const payload = { month: state.month };
    const income = el("goalIncome").value.trim();
    const savings = el("goalSavings").value.trim();

    if (income) payload.target_income = income;
    if (savings) payload.target_savings = savings;
    payload.notes = el("goalNotes").value.trim() || null;

    await mutate(() => api("/goals", {
      method: "PUT",
      body: JSON.stringify(payload),
    }));
  });
}

function wireFilters() {
  el("previousMonth").addEventListener("click", () => setMonth(shiftMonth(state.month, -1)));
  el("nextMonth").addEventListener("click", () => setMonth(shiftMonth(state.month, 1)));
  el("monthInput").addEventListener("change", (event) => {
    if (event.target.value) setMonth(event.target.value);
  });

  el("categoryFilter").addEventListener("change", (event) => {
    state.categoryFilter = event.target.value;
    renderCategoryChart(state.overview.summary.by_category);
    refreshTable();
  });

  let searchTimer = null;
  el("searchInput").addEventListener("input", (event) => {
    window.clearTimeout(searchTimer);
    searchTimer = window.setTimeout(() => {
      state.search = event.target.value.trim();
      refreshTable();
    }, 250);
  });
}

function start() {
  wireTheme();
  wireForms();
  wireFilters();
  setMonth(currentMonthString());
}

start();
