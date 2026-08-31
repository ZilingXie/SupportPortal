const BASE = "/automation/production";

const state = {
  authenticated: false,
  loading: true,
  error: "",
  items: [],
  page: 1,
  pageSize: 25,
  pages: 0,
  total: 0,
  selectedId: "",
  detail: null,
  runtime: null,
  filters: {
    zendesk_ticket_id: "",
    execution_id: "",
    status: "",
    event_type: "",
  },
};

const app = document.getElementById("app");

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function formatTime(value) {
  if (!value) return "-";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString();
}

function statusLabel(value) {
  return String(value || "unknown").replaceAll("_", " ");
}

function statusMarkup(value) {
  const normalized = String(value || "unknown").toLowerCase();
  return `<span class="status status-${escapeHtml(normalized)}">${escapeHtml(statusLabel(normalized))}</span>`;
}

async function request(path, options = {}) {
  const response = await fetch(`${BASE}${path}`, {
    ...options,
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
  });
  if (response.status === 401) {
    state.authenticated = false;
    state.detail = null;
    if (!path.endsWith("/login")) render();
  }
  const body = response.status === 204 ? null : await response.json().catch(() => null);
  if (!response.ok) {
    throw new Error(body?.detail || `Request failed (${response.status})`);
  }
  return body;
}

async function loadSession() {
  try {
    await request("/dashboard/auth/session");
    state.authenticated = true;
    await Promise.all([loadExecutions(), loadRuntime()]);
  } catch (_error) {
    state.authenticated = false;
  } finally {
    state.loading = false;
    render();
  }
}

async function login(username, password) {
  state.error = "";
  try {
    await request("/dashboard/auth/login", {
      method: "POST",
      body: JSON.stringify({ username, password }),
    });
    state.authenticated = true;
    await Promise.all([loadExecutions(), loadRuntime()]);
  } catch (error) {
    state.error = error.message;
  }
  render();
}

async function logout() {
  await request("/dashboard/auth/logout", { method: "POST", body: "{}" }).catch(() => null);
  Object.assign(state, {
    authenticated: false,
    items: [],
    detail: null,
    runtime: null,
    selectedId: "",
    error: "",
  });
  render();
}

function executionQuery() {
  const params = new URLSearchParams({
    page: String(state.page),
    page_size: String(state.pageSize),
  });
  Object.entries(state.filters).forEach(([key, value]) => {
    if (value) params.set(key, value);
  });
  return params.toString();
}

async function loadExecutions() {
  const result = await request(`/dashboard/api/executions?${executionQuery()}`);
  Object.assign(state, {
    items: result.items,
    page: result.page,
    pages: result.pages,
    total: result.total,
  });
  if (state.selectedId && !state.items.some((item) => item.execution_id === state.selectedId)) {
    state.selectedId = "";
    state.detail = null;
  }
}

async function loadRuntime() {
  state.runtime = await request("/dashboard/api/runtime");
}

async function selectExecution(executionId) {
  state.selectedId = executionId;
  state.detail = null;
  render();
  try {
    state.detail = await request(`/dashboard/api/executions/${encodeURIComponent(executionId)}`);
  } catch (error) {
    state.error = error.message;
  }
  render();
}

function renderLogin() {
  app.innerHTML = `
    <main class="login-shell">
      <section class="login-panel" aria-labelledby="login-title">
        <span class="brand-mark" aria-hidden="true">S</span>
        <h1 id="login-title">Production Automation</h1>
        <p>Administrator access to the read-only ECS execution ledger.</p>
        <form id="login-form">
          <div class="field">
            <label for="username">Administrator</label>
            <input id="username" name="username" autocomplete="username" required />
          </div>
          <div class="field">
            <label for="password">Password</label>
            <input id="password" name="password" type="password" autocomplete="current-password" required />
          </div>
          <button class="primary" type="submit">Sign in</button>
          ${state.error ? `<p class="error" role="alert">${escapeHtml(state.error)}</p>` : ""}
        </form>
      </section>
    </main>`;
  document.getElementById("login-form").addEventListener("submit", (event) => {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    login(String(data.get("username") || ""), String(data.get("password") || ""));
  });
}

function renderRuntimeBar() {
  const runtime = state.runtime;
  const provenance = runtime?.api?.provenance || {};
  return `
    <div class="runtime-bar">
      <span>${runtime?.ready ? statusMarkup("ready") : statusMarkup("failed")}</span>
      <span>Release <strong>${escapeHtml(provenance.release_id || "-")}</strong></span>
      <span>Prompt <strong>${escapeHtml(provenance.prompt_release_id || "-")}</strong></span>
      <span>Commit <strong>${escapeHtml(String(provenance.git_commit || "-").slice(0, 12))}</strong></span>
      <span>Executions <strong>${escapeHtml(state.total)}</strong></span>
    </div>`;
}

function renderFilters() {
  const values = state.filters;
  return `
    <form class="filters" id="filters-form">
      <div class="filter"><label for="ticket-filter">Ticket ID</label><input id="ticket-filter" name="zendesk_ticket_id" inputmode="numeric" value="${escapeHtml(values.zendesk_ticket_id)}" /></div>
      <div class="filter"><label for="execution-filter">Execution ID</label><input id="execution-filter" name="execution_id" value="${escapeHtml(values.execution_id)}" /></div>
      <div class="filter"><label for="status-filter">Status</label><select id="status-filter" name="status">
        <option value="">All statuses</option>
        ${["route_pending", "routing", "processing_pending", "processing", "completed", "human_review", "failed", "outcome_unknown"].map((value) => `<option value="${value}" ${values.status === value ? "selected" : ""}>${statusLabel(value)}</option>`).join("")}
      </select></div>
      <div class="filter"><label for="event-filter">Event type</label><select id="event-filter" name="event_type">
        <option value="">All events</option>
        ${["ticket.created", "ticket.updated", "comment.created"].map((value) => `<option value="${value}" ${values.event_type === value ? "selected" : ""}>${value}</option>`).join("")}
      </select></div>
      <div class="filter"><label for="page-size">Rows</label><select id="page-size" name="page_size">
        ${[25, 50, 100].map((value) => `<option value="${value}" ${state.pageSize === value ? "selected" : ""}>${value}</option>`).join("")}
      </select></div>
      <div class="filter-actions"><button class="secondary" type="submit">Apply</button><button class="quiet" id="clear-filters" type="button">Clear</button></div>
    </form>`;
}

function renderTable() {
  if (!state.items.length) return '<div class="empty">No executions match the current filters.</div>';
  return `
    <div class="table-wrap">
      <table>
        <thead><tr><th style="width: 28%">Execution</th><th style="width: 12%">Ticket</th><th style="width: 17%">Status</th><th style="width: 17%">Event</th><th style="width: 16%">Stage</th><th style="width: 18%">Updated</th></tr></thead>
        <tbody>${state.items.map((item) => `
          <tr class="${item.execution_id === state.selectedId ? "selected" : ""}">
            <td><button class="row-button" data-execution-id="${escapeHtml(item.execution_id)}">${escapeHtml(item.execution_id)}</button></td>
            <td>${escapeHtml(item.zendesk_ticket_id)}</td>
            <td>${statusMarkup(item.status)}</td>
            <td>${escapeHtml(item.event_type)}</td>
            <td>${escapeHtml(item.current_stage)}</td>
            <td>${escapeHtml(formatTime(item.updated_at))}</td>
          </tr>`).join("")}</tbody>
      </table>
    </div>`;
}

function renderPagination() {
  const start = state.total ? (state.page - 1) * state.pageSize + 1 : 0;
  const end = Math.min(state.total, state.page * state.pageSize);
  return `
    <div class="pagination">
      <span>${start}-${end} of ${state.total}</span>
      <div class="pagination-actions">
        <button class="secondary" data-page="${state.page - 1}" ${state.page <= 1 ? "disabled" : ""}>Previous</button>
        <button class="secondary" data-page="${state.page + 1}" ${state.page >= state.pages ? "disabled" : ""}>Next</button>
      </div>
    </div>`;
}

function renderFacts(items) {
  return `<dl class="facts">${items.map(([label, value]) => `<div><dt>${escapeHtml(label)}</dt><dd>${escapeHtml(value ?? "-")}</dd></div>`).join("")}</dl>`;
}

function renderTrace(items, kind) {
  if (!items?.length) return '<p class="empty">No records.</p>';
  return `<ul class="trace-list">${items.map((item) => {
    const title = item.step_name || item.event_type || item.kind || item.action_type || "record";
    const status = item.status || (kind === "timeline" ? "event" : "unknown");
    const meta = [item.error_code, item.claimed_by, item.attempt ? `attempt ${item.attempt}` : ""].filter(Boolean).join(" / ");
    return `<li class="trace-item"><span class="trace-dot" aria-hidden="true"></span><span class="trace-main"><strong>${escapeHtml(title)}</strong><small>${escapeHtml(meta || statusLabel(status))}</small></span><span class="trace-time">${escapeHtml(formatTime(item.updated_at || item.created_at || item.started_at))}</span></li>`;
  }).join("")}</ul>`;
}

function renderDetail() {
  if (!state.selectedId) return '<div class="empty">Select an execution to inspect its read-only trace.</div>';
  if (!state.detail) return '<div class="loading">Loading execution trace...</div>';
  const item = state.detail;
  const route = item.route || {};
  return `<div class="detail-content">
    <div class="pane-heading"><div><h2>${escapeHtml(item.execution_id)}</h2><p>Ticket ${escapeHtml(item.zendesk_ticket_id)} / ${escapeHtml(item.event_type)}</p></div>${statusMarkup(item.status)}</div>
    <section class="detail-section"><h3>Execution</h3>${renderFacts([
      ["Current stage", item.current_stage], ["Human review", item.requires_human_review ? "required" : "no"],
      ["Failure stage", item.failure_stage], ["Failure code", item.failure_code],
      ["Created", formatTime(item.created_at)], ["Updated", formatTime(item.updated_at)],
    ])}</section>
    <section class="detail-section"><h3>Route decision</h3>${renderFacts([
      ["Route family", route.route_family], ["Execution action", route.execution_action],
      ["Category", route.category || route.classification], ["Subcategory", route.subcategory],
      ["Persona", item.persona?.persona_key], ["Persona version", item.persona?.version],
    ])}</section>
    <section class="detail-section"><h3>Processing steps</h3>${renderTrace(item.steps, "steps")}</section>
    <section class="detail-section"><h3>Jobs</h3>${renderTrace(item.jobs, "jobs")}</section>
    <section class="detail-section"><h3>Delivery ledger</h3>${renderTrace(item.deliveries, "deliveries")}</section>
    <section class="detail-section"><h3>Status timeline</h3>${renderTrace(item.events, "timeline")}</section>
    <section class="detail-section"><h3>Provenance</h3>${renderFacts([
      ["Release", item.provenance?.release_id], ["Prompt release", item.provenance?.prompt_release_id],
      ["Commit", item.provenance?.git_commit], ["Build time", item.provenance?.build_time],
      ["Route release", item.route_provenance?.release_id], ["Schema", item.provenance?.schema_revision],
    ])}</section>
  </div>`;
}

function renderRuntime() {
  const runtime = state.runtime;
  if (!runtime) return '<div class="loading">Loading runtime identity...</div>';
  const entries = [runtime.api, ...(runtime.active_workers || runtime.workers || [])];
  return `<div class="runtime-grid">${entries.map((entry) => {
    const provenance = entry.provenance || {};
    const mismatch = entry.provenance_mismatches || [];
    const healthy = Number(entry.age_seconds) <= Number(runtime.max_age_seconds) && mismatch.length === 0;
    return `<div class="runtime-row"><div class="runtime-row-head"><strong>${escapeHtml(entry.role || "api")}</strong>${statusMarkup(healthy ? "ready" : "failed")}</div><code>${escapeHtml(provenance.release_id || "-")} / ${escapeHtml(String(provenance.git_commit || "-").slice(0, 12))} / ${escapeHtml(provenance.prompt_release_id || "-")} / age ${escapeHtml(Math.round(Number(entry.age_seconds) || 0))}s${mismatch.length ? ` / mismatch: ${escapeHtml(mismatch.join(", "))}` : ""}</code></div>`;
  }).join("")}</div>`;
}

function bindDashboardEvents() {
  document.getElementById("logout").addEventListener("click", logout);
  document.getElementById("filters-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    state.filters = {
      zendesk_ticket_id: String(data.get("zendesk_ticket_id") || "").trim(),
      execution_id: String(data.get("execution_id") || "").trim(),
      status: String(data.get("status") || ""),
      event_type: String(data.get("event_type") || ""),
    };
    state.pageSize = Number(data.get("page_size") || 25);
    state.page = 1;
    await loadExecutions();
    render();
  });
  document.getElementById("clear-filters").addEventListener("click", async () => {
    state.filters = { zendesk_ticket_id: "", execution_id: "", status: "", event_type: "" };
    state.page = 1;
    await loadExecutions();
    render();
  });
  document.querySelectorAll("[data-execution-id]").forEach((button) => {
    button.addEventListener("click", () => selectExecution(button.dataset.executionId));
  });
  document.querySelectorAll("[data-page]").forEach((button) => {
    button.addEventListener("click", async () => {
      state.page = Number(button.dataset.page);
      await loadExecutions();
      render();
    });
  });
}

function renderDashboard() {
  app.innerHTML = `
    <header class="app-header">
      <div class="app-title"><span class="brand-mark" aria-hidden="true">S</span><div><strong>Production Automation</strong><small>ECS read-only execution ledger</small></div></div>
      <button class="quiet" id="logout" type="button">Sign out</button>
    </header>
    ${renderRuntimeBar()}
    <main class="workspace">
      <section class="list-pane">
        <div class="pane-heading"><div><h1>Executions</h1><p>Newest persisted executions first</p></div></div>
        ${renderFilters()}
        ${renderTable()}
        ${renderPagination()}
      </section>
      <aside class="detail-pane">
        ${renderDetail()}
        <section class="detail-section"><h3>Runtime heartbeat</h3>${renderRuntime()}</section>
      </aside>
    </main>`;
  bindDashboardEvents();
}

function render() {
  if (state.loading) {
    app.innerHTML = '<div class="loading">Loading production runtime...</div>';
  } else if (!state.authenticated) {
    renderLogin();
  } else {
    renderDashboard();
  }
}

render();
loadSession();
