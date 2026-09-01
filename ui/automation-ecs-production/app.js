const app = document.getElementById("app");
const basePath = "/automation/production";

const categoryFallback = [
  { id: "all", label: "All", children: [] },
  {
    id: "automation",
    label: "Automated",
    children: [
      { id: "fraud_account", label: "Account & Billing / Fraud Account" },
      { id: "enablement", label: "Backend Operation / Enablement" },
    ],
  },
  {
    id: "backend_operation",
    label: "Backend Operation",
    children: [
      { id: "enablement", label: "Enablement" },
      { id: "quota", label: "Quota" },
      { id: "unregistered", label: "Unregistered" },
    ],
  },
  {
    id: "account_billing",
    label: "Account & Billing",
    children: [
      { id: "account_suspension", label: "Account Suspension" },
      { id: "fraud_account", label: "Fraud Account" },
      { id: "detailed_invoice", label: "Detailed Invoice" },
      { id: "other", label: "Other" },
    ],
  },
  { id: "agora_technical", label: "Tech", children: [] },
  { id: "security_compliance", label: "Security & Compliance", children: [] },
  {
    id: "conversation",
    label: "Conversation",
    children: [
      { id: "resolve", label: "Resolve" },
      { id: "follow_up", label: "Follow-up" },
      { id: "human_review", label: "Human Review" },
    ],
  },
  {
    id: "human_review",
    label: "Human Review",
    children: [
      { id: "uncategorized", label: "Uncategorized" },
      { id: "uncertain", label: "Uncertain" },
      { id: "non_agora", label: "Non-Agora" },
      { id: "other", label: "Other" },
    ],
  },
];

const state = {
  loading: true,
  authenticated: false,
  casesLoading: false,
  detailLoading: false,
  auditLoading: false,
  error: "",
  items: [],
  page: 1,
  pageSize: 25,
  pages: 0,
  total: 0,
  facets: { route_groups: {}, route_subcategories: {}, ticket_statuses: {} },
  definitions: categoryFallback,
  filters: {
    route_group: "all",
    route_subcategory: "",
    ticket_status: "active",
    zendesk_ticket_id: "",
    execution_id: "",
    execution_status: "",
    event_type: "",
  },
  selectedTicketId: "",
  selectedMatchedExecutionId: "",
  selectedAuditExecutionId: "",
  detail: null,
  audit: null,
  runtime: null,
  filterOpen: false,
  advancedOpen: false,
  auditOpen: false,
  showMobileDetail: false,
};

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function formatLabel(value) {
  return String(value || "unknown")
    .replaceAll("_", " ")
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function fieldLabel(value) {
  const labels = {
    app_id: "App ID",
    app_ids: "App IDs",
    event_timezone: "Event timezone",
  };
  return labels[value] || formatLabel(value);
}

function shortId(value) {
  const normalized = String(value || "");
  return normalized.length > 22 ? `${normalized.slice(0, 19)}...` : normalized;
}

function statusLabel(value) {
  const labels = {
    completed: "Completed",
    human_review: "Human review",
    failed: "Failed",
    outcome_unknown: "Outcome unknown",
    route_pending: "Route pending",
    routing: "Routing",
    processing_pending: "Processing pending",
    processing: "Processing",
    ready: "Healthy",
    open: "Open",
    new: "New",
    pending: "Pending",
    hold: "On hold",
    solved: "Solved",
    closed: "Closed",
    unknown: "Unknown",
    automated: "Automated",
    internal_pending: "Internal pending",
    customer_follow_up: "Customer follow-up",
  };
  return labels[value] || formatLabel(value);
}

function statusTone(value) {
  const normalized = String(value || "unknown").toLowerCase();
  if (["completed", "ready", "solved", "closed", "confirmed", "published", "delivered"].includes(normalized)) return "success";
  if (["failed", "error"].includes(normalized)) return "danger";
  if (["human_review", "pending", "hold", "manual_attention"].includes(normalized)) return "warning";
  if (normalized === "outcome_unknown" || normalized === "unknown") return "unknown";
  if (["processing", "routing", "claimed", "preparing", "scheduled", "in_progress"].some((part) => normalized.includes(part))) return "active";
  return "neutral";
}

function statusMarkup(value, compact = false) {
  const normalized = String(value || "unknown").toLowerCase();
  return `<span class="status-pill tone-${statusTone(normalized)} ${compact ? "status-compact" : ""}"><span class="status-dot" aria-hidden="true"></span>${escapeHtml(statusLabel(normalized))}</span>`;
}

function formatTime(value) {
  if (!value) return "Not available";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return new Intl.DateTimeFormat("en", {
    year: "numeric",
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
    timeZoneName: "short",
  }).format(date);
}

function valueMarkup(value) {
  if (Array.isArray(value)) return escapeHtml(value.join(", "));
  if (value && typeof value === "object") return `<pre>${escapeHtml(JSON.stringify(value, null, 2))}</pre>`;
  return escapeHtml(value ?? "Not available");
}

async function request(path, options = {}) {
  const headers = { Accept: "application/json", ...(options.headers || {}) };
  if (options.body) headers["Content-Type"] = "application/json";
  const response = await fetch(`${basePath}${path}`, {
    credentials: "same-origin",
    cache: "no-store",
    ...options,
    headers,
  });
  if (!response.ok) {
    let detail = `Request failed (${response.status})`;
    try {
      const payload = await response.json();
      detail = typeof payload.detail === "string" ? payload.detail : detail;
    } catch (_error) {
      // Keep the bounded HTTP error above.
    }
    if (response.status === 401 && !path.includes("/auth/login")) state.authenticated = false;
    throw new Error(detail);
  }
  return response.json();
}

function casesQuery() {
  const params = new URLSearchParams({
    page: String(state.page),
    page_size: String(state.pageSize),
    ticket_status: state.filters.ticket_status || "active",
  });
  Object.entries(state.filters).forEach(([key, value]) => {
    if (!value || key === "ticket_status" || (key === "route_group" && value === "all")) return;
    params.set(key, value);
  });
  return params.toString();
}

async function loadRuntime() {
  state.runtime = await request("/dashboard/api/runtime");
}

async function loadExecutionAudit(executionId, shouldRender = true) {
  if (!executionId) {
    state.audit = null;
    return;
  }
  state.selectedAuditExecutionId = executionId;
  state.auditLoading = true;
  if (shouldRender) render();
  try {
    const audit = await request(`/dashboard/api/executions/${encodeURIComponent(executionId)}`);
    if (state.selectedAuditExecutionId === executionId) state.audit = audit;
  } catch (error) {
    state.error = error.message;
    state.audit = null;
  } finally {
    state.auditLoading = false;
    if (shouldRender) render();
  }
}

async function loadCaseDetail(ticketId, matchedExecutionId = "") {
  state.selectedTicketId = ticketId;
  state.selectedMatchedExecutionId = matchedExecutionId;
  state.detail = null;
  state.audit = null;
  state.detailLoading = true;
  state.showMobileDetail = true;
  render();
  try {
    const detail = await request(`/dashboard/api/cases/${encodeURIComponent(ticketId)}`);
    if (state.selectedTicketId !== ticketId) return;
    state.detail = detail;
    const executionId = matchedExecutionId || detail.current_execution_id || detail.executions?.[0]?.execution_id || "";
    await loadExecutionAudit(executionId, false);
  } catch (error) {
    state.error = error.message;
  } finally {
    if (state.selectedTicketId === ticketId) state.detailLoading = false;
    render();
  }
}

async function loadCases({ selectFirst = false } = {}) {
  state.casesLoading = true;
  state.error = "";
  render();
  try {
    const result = await request(`/dashboard/api/cases?${casesQuery()}`);
    Object.assign(state, {
      items: result.items || [],
      page: result.page,
      pageSize: result.page_size,
      pages: result.pages,
      total: result.total,
      facets: result.facets || state.facets,
      definitions: result.filter_definitions?.length ? result.filter_definitions : categoryFallback,
    });
    const selected = state.items.find((item) => item.zendesk_ticket_id === state.selectedTicketId);
    const matchedExecutionChanged = Boolean(
      selected && (selected.matched_execution_id || "") !== state.selectedMatchedExecutionId
    );
    const target = selected || (selectFirst ? state.items[0] : null);
    if (!selected && !target) {
      state.selectedTicketId = "";
      state.detail = null;
      state.audit = null;
      state.showMobileDetail = false;
    }
    state.casesLoading = false;
    render();
    if (target && (!selected || !state.detail || matchedExecutionChanged)) {
      await loadCaseDetail(target.zendesk_ticket_id, target.matched_execution_id || "");
    }
  } catch (error) {
    state.error = error.message;
    state.casesLoading = false;
    render();
  }
}

async function refreshAll() {
  state.error = "";
  try {
    await Promise.all([loadRuntime(), loadCases({ selectFirst: true })]);
  } catch (error) {
    state.error = error.message;
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
    await Promise.all([loadRuntime(), loadCases({ selectFirst: true })]);
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
    audit: null,
    runtime: null,
    selectedTicketId: "",
    error: "",
  });
  render();
}

async function initialize() {
  try {
    await request("/dashboard/auth/session");
    state.authenticated = true;
    state.loading = false;
    await Promise.all([loadRuntime(), loadCases({ selectFirst: true })]);
  } catch (_error) {
    state.authenticated = false;
  } finally {
    state.loading = false;
    render();
  }
}

function renderLogin() {
  return `
    <main class="login-shell">
      <section class="login-panel" aria-labelledby="login-title">
        <div class="brand-mark" aria-hidden="true">S</div>
        <p class="eyebrow">Stellarix Support</p>
        <h1 id="login-title">Production cases</h1>
        <form id="login-form">
          <label class="field" for="username"><span>Administrator</span><input id="username" name="username" autocomplete="username" required /></label>
          <label class="field" for="password"><span>Password</span><input id="password" name="password" type="password" autocomplete="current-password" required /></label>
          <button class="button button-primary" type="submit">Sign in</button>
          ${state.error ? `<p class="form-error" role="alert">${escapeHtml(state.error)}</p>` : ""}
        </form>
      </section>
    </main>`;
}

function renderRuntimeStrip() {
  const provenance = state.runtime?.api?.provenance || {};
  return `
    <div class="runtime-strip" role="status">
      ${statusMarkup(state.runtime?.ready ? "ready" : "failed", true)}
      <span>Release <strong>${escapeHtml(provenance.release_id || "Not available")}</strong></span>
      <span>Prompt <strong>${escapeHtml(provenance.prompt_release_id || "Not available")}</strong></span>
      <span>Commit <strong>${escapeHtml(String(provenance.git_commit || "Not available").slice(0, 12))}</strong></span>
      <span class="runtime-count"><strong>${escapeHtml(state.total)}</strong> tickets</span>
    </div>`;
}

function categoryCount(id) {
  return Number(state.facets.route_groups?.[id] || 0);
}

function renderCategoryFilters() {
  return state.definitions.map((definition) => {
    const selected = state.filters.route_group === definition.id;
    return `<button class="category-button ${selected ? "is-selected" : ""}" type="button" data-route-group="${escapeHtml(definition.id)}" aria-pressed="${selected}"><span>${escapeHtml(definition.label)}</span><strong>${categoryCount(definition.id)}</strong></button>`;
  }).join("");
}

function selectedCategory() {
  return state.definitions.find((definition) => definition.id === state.filters.route_group) || state.definitions[0];
}

function renderSubcategoryOptions() {
  const category = selectedCategory();
  const counts = state.facets.route_subcategories || {};
  const total = category.id === "all" ? categoryCount("all") : categoryCount(category.id);
  const allLabel = category.id === "all" ? "All categories" : `All ${category.label}`;
  return [
    `<option value="">${escapeHtml(allLabel)} (${total})</option>`,
    ...(category.children || []).map((child) => `<option value="${escapeHtml(child.id)}" ${state.filters.route_subcategory === child.id ? "selected" : ""}>${escapeHtml(child.label)} (${Number(counts[child.id] || 0)})</option>`),
  ].join("");
}

function renderFilterRail() {
  const statuses = ["active", "all", "new", "open", "pending", "hold", "solved", "closed", "unknown"];
  const statusCounts = state.facets.ticket_statuses || {};
  return `
    <aside class="filters-rail ${state.filterOpen ? "is-open" : ""}" aria-label="Ticket filters">
      <div class="rail-heading"><div><span class="eyebrow">View</span><h2>Filters</h2></div><button class="icon-text-button rail-close" id="close-filters" type="button">Close</button></div>
      <form id="filters-form">
        <fieldset class="filter-group"><legend>Category</legend><div class="category-grid">${renderCategoryFilters()}</div></fieldset>
        <label class="filter-field" for="subcategory-filter"><span>Subcategory</span><select id="subcategory-filter" name="route_subcategory">${renderSubcategoryOptions()}</select></label>
        <label class="filter-field" for="ticket-status-filter"><span>Ticket status</span><select id="ticket-status-filter" name="ticket_status">${statuses.map((value) => `<option value="${value}" ${state.filters.ticket_status === value ? "selected" : ""}>${escapeHtml(statusLabel(value))} (${Number(statusCounts[value] || 0)})</option>`).join("")}</select></label>
        <label class="filter-field" for="ticket-filter"><span>Ticket ID</span><input id="ticket-filter" name="zendesk_ticket_id" inputmode="numeric" pattern="[0-9]*" value="${escapeHtml(state.filters.zendesk_ticket_id)}" placeholder="e.g. 13119" /></label>
        <details class="advanced-filters" ${state.advancedOpen ? "open" : ""}>
          <summary>Advanced filters</summary>
          <div class="advanced-fields">
            <label class="filter-field" for="execution-filter"><span>Execution ID</span><input id="execution-filter" name="execution_id" value="${escapeHtml(state.filters.execution_id)}" /></label>
            <label class="filter-field" for="execution-status-filter"><span>Execution status</span><select id="execution-status-filter" name="execution_status"><option value="">All statuses</option>${["route_pending", "routing", "processing_pending", "processing", "completed", "human_review", "failed", "outcome_unknown"].map((value) => `<option value="${value}" ${state.filters.execution_status === value ? "selected" : ""}>${escapeHtml(statusLabel(value))}</option>`).join("")}</select></label>
            <label class="filter-field" for="event-filter"><span>Event type</span><select id="event-filter" name="event_type"><option value="">All event types</option>${["ticket.created", "ticket.updated", "comment.created"].map((value) => `<option value="${value}" ${state.filters.event_type === value ? "selected" : ""}>${escapeHtml(value)}</option>`).join("")}</select></label>
          </div>
        </details>
        <div class="filter-actions"><button class="button button-primary" type="submit">Apply filters</button><button class="button button-quiet" id="clear-filters" type="button">Clear</button></div>
      </form>
    </aside>
    <button class="filter-overlay ${state.filterOpen ? "is-visible" : ""}" id="filter-overlay" type="button" aria-label="Close filters"></button>`;
}

function routeText(route) {
  return [route?.product, route?.category_label, route?.subcategory_label].filter(Boolean).join(" / ");
}

function renderCaseItems() {
  if (state.casesLoading) return `<div class="list-state"><span class="spinner" aria-hidden="true"></span><strong>Loading tickets</strong></div>`;
  if (!state.items.length) return `<div class="list-state"><strong>No tickets found</strong><span>Adjust the current filters.</span></div>`;
  return `<div class="case-items">${state.items.map((item) => {
    const selected = item.zendesk_ticket_id === state.selectedTicketId;
    const executionStatus = item.current_execution?.status || "unknown";
    return `<button class="case-item ${selected ? "is-selected" : ""}" type="button" data-ticket-id="${escapeHtml(item.zendesk_ticket_id)}" data-execution-id="${escapeHtml(item.matched_execution_id || "")}" aria-pressed="${selected}">
      <span class="case-item-top"><strong>Ticket #${escapeHtml(item.zendesk_ticket_id)}</strong><time>${escapeHtml(formatTime(item.updated_at))}</time></span>
      <span class="case-title">${escapeHtml(item.title || "Untitled ticket")}</span>
      <span class="case-route">${escapeHtml(routeText(item.route) || "Route unavailable")}</span>
      <span class="case-statuses">
        <span class="case-status-group"><small>Zendesk</small>${statusMarkup(item.ticket_status, true)}</span>
        <span class="case-status-group"><small>Automation</small>${statusMarkup(item.automation_status, true)}</span>
        <span class="case-status-group"><small>Execution</small>${statusMarkup(executionStatus, true)}</span>
      </span>
    </button>`;
  }).join("")}</div>`;
}

function renderPagination() {
  const start = state.total ? (state.page - 1) * state.pageSize + 1 : 0;
  const end = Math.min(state.total, state.page * state.pageSize);
  return `<footer class="pagination"><span>${start}-${end} of ${state.total}</span><div><button class="button button-secondary" type="button" data-page="${state.page - 1}" ${state.page <= 1 ? "disabled" : ""}>Previous</button><button class="button button-secondary" type="button" data-page="${state.page + 1}" ${state.page >= state.pages ? "disabled" : ""}>Next</button></div></footer>`;
}

function renderCaseList() {
  return `<section class="case-list-pane" aria-labelledby="case-list-title"><div class="pane-heading"><div><span class="eyebrow">Production queue</span><h1 id="case-list-title">Tickets</h1></div><span class="result-count">${escapeHtml(state.total)}</span></div>${renderCaseItems()}${renderPagination()}</section>`;
}

function renderFacts(items) {
  return `<dl class="facts">${items.map(([label, value, markup]) => `<div><dt>${escapeHtml(label)}</dt><dd>${markup || escapeHtml(value ?? "Not available")}</dd></div>`).join("")}</dl>`;
}

function renderCollectedFields(fields) {
  const entries = Object.entries(fields || {});
  if (!entries.length) return `<p class="section-empty">No collected fields.</p>`;
  return `<dl class="collected-fields">${entries.map(([key, value]) => `<div><dt>${escapeHtml(fieldLabel(key))}</dt><dd>${valueMarkup(value)}</dd></div>`).join("")}</dl>`;
}

function conversationLabel(message) {
  if (message.author_kind === "customer" || message.author_kind === "end-user") return "Customer request";
  if (message.author_kind === "automation") return "AI reply";
  return message.visibility === "internal" ? "Internal note" : "Support reply";
}

function renderConversation(messages) {
  if (!messages?.length) return `<p class="section-empty">No conversation snapshot available.</p>`;
  return `<div class="conversation">${messages.map((message) => {
    const customer = ["customer", "end-user"].includes(message.author_kind);
    const internal = message.visibility === "internal";
    return `<article class="message ${customer ? "message-customer" : "message-support"} ${internal ? "message-internal" : ""}">
      <header><strong>${escapeHtml(conversationLabel(message))}</strong><span>${escapeHtml(internal ? "Internal" : "Public")}</span><time>${escapeHtml(formatTime(message.created_at))}</time></header>
      <p class="message-body">${escapeHtml(message.body)}</p>
      ${message.delivery_status ? `<footer>Delivery: ${escapeHtml(statusLabel(message.delivery_status))}</footer>` : ""}
    </article>`;
  }).join("")}</div>`;
}

function renderPendingReply(reply) {
  if (!reply) return `<p class="section-empty">No pending reply.</p>`;
  const preview = reply.preview_state === "preparing"
    ? `<div class="preview-pending"><span class="spinner" aria-hidden="true"></span><strong>Preparing preview</strong></div>`
    : reply.preview
      ? `<div class="reply-preview"><p>${escapeHtml(reply.preview)}</p></div>`
      : `<p class="section-empty">Preview unavailable.</p>`;
  return `<div class="pending-reply-meta">${statusMarkup(reply.status)}<span>Attempt ${escapeHtml(reply.attempt)}</span><span>Scheduled ${escapeHtml(formatTime(reply.scheduled_for))}</span></div>${preview}`;
}

function renderTrace(items, type) {
  if (!items?.length) return `<p class="section-empty">No records.</p>`;
  return `<ol class="trace-list">${items.map((item) => {
    const title = item.step_name || item.event_type || item.kind || item.action_type || type;
    const meta = [item.error_code, item.attempt ? `Attempt ${item.attempt}` : "", item.claimed_by].filter(Boolean).join(" / ");
    return `<li><span class="trace-marker" aria-hidden="true"></span><div><strong>${escapeHtml(formatLabel(title))}</strong><small>${escapeHtml(meta || statusLabel(item.status || "event"))}</small></div><time>${escapeHtml(formatTime(item.updated_at || item.created_at || item.started_at))}</time></li>`;
  }).join("")}</ol>`;
}

function renderExecutionAudit() {
  if (state.auditLoading) return `<div class="audit-loading"><span class="spinner" aria-hidden="true"></span>Loading execution audit</div>`;
  if (!state.audit) return `<p class="section-empty">Execution audit unavailable.</p>`;
  const item = state.audit;
  return `<div class="audit-body">
    <section><h4>Execution</h4>${renderFacts([
      ["Execution ID", item.execution_id],
      ["Status", null, statusMarkup(item.status)],
      ["Current stage", item.current_stage],
      ["Human review", item.requires_human_review ? "Required" : "No"],
      ["Failure stage", item.failure_stage],
      ["Failure code", item.failure_code],
    ])}</section>
    <section><h4>Processing steps</h4>${renderTrace(item.steps, "step")}</section>
    <section><h4>Jobs</h4>${renderTrace(item.jobs, "job")}</section>
    <section><h4>Delivery ledger</h4>${renderTrace(item.deliveries, "delivery")}</section>
    <section><h4>Status timeline</h4>${renderTrace(item.events, "event")}</section>
    <section><h4>Provenance</h4>${renderFacts([
      ["Release", item.provenance?.release_id],
      ["Prompt release", item.provenance?.prompt_release_id],
      ["Commit", item.provenance?.git_commit],
      ["Build time", formatTime(item.provenance?.build_time)],
      ["Route release", item.route_provenance?.release_id],
      ["Schema", item.provenance?.schema_revision],
    ])}</section>
  </div>`;
}

function renderRuntimeAudit() {
  if (!state.runtime) return `<p class="section-empty">Runtime heartbeat unavailable.</p>`;
  const entries = [state.runtime.api, ...(state.runtime.active_workers || state.runtime.workers || [])].filter(Boolean);
  return `<div class="heartbeat-list">${entries.map((entry) => {
    const provenance = entry.provenance || {};
    const mismatch = entry.provenance_mismatches || [];
    const healthy = Number(entry.age_seconds || 0) <= Number(state.runtime.max_age_seconds || 30) && mismatch.length === 0;
    return `<div class="heartbeat-row"><div><strong>${escapeHtml(entry.role === "route" ? "Route Worker" : entry.role === "worker" ? "Automation Worker" : "API")}</strong>${statusMarkup(healthy ? "ready" : "failed", true)}</div><dl><div><dt>Release</dt><dd>${escapeHtml(provenance.release_id || "Not available")}</dd></div><div><dt>Commit</dt><dd>${escapeHtml(String(provenance.git_commit || "Not available").slice(0, 12))}</dd></div><div><dt>Prompt</dt><dd>${escapeHtml(provenance.prompt_release_id || "Not available")}</dd></div><div><dt>Heartbeat</dt><dd>${Math.round(Number(entry.age_seconds || 0))}s ago</dd></div></dl>${mismatch.length ? `<p class="runtime-mismatch">Mismatch: ${escapeHtml(mismatch.join(", "))}</p>` : ""}</div>`;
  }).join("")}</div>`;
}

function renderExecutionHistory(executions) {
  if (!executions?.length) return `<p class="section-empty">No execution history.</p>`;
  return `<div class="execution-history">${executions.map((execution) => `<button type="button" data-audit-execution-id="${escapeHtml(execution.execution_id)}" aria-pressed="${execution.execution_id === state.selectedAuditExecutionId}"><span><strong>${escapeHtml(execution.execution_id)}</strong><small>${escapeHtml(execution.event_type || "Event unavailable")} / ${escapeHtml(formatTime(execution.created_at))}</small></span>${statusMarkup(execution.status, true)}</button>`).join("")}</div>`;
}

function renderDetail() {
  if (!state.selectedTicketId) return `<section class="detail-pane detail-empty"><div><strong>Select a ticket</strong><span>Case details will appear here.</span></div></section>`;
  if (state.detailLoading || !state.detail) return `<section class="detail-pane detail-empty"><div><span class="spinner" aria-hidden="true"></span><strong>Loading case</strong></div></section>`;
  const detail = state.detail;
  const route = routeText(detail.route);
  const persona = detail.persona;
  return `<section class="detail-pane" aria-labelledby="case-detail-title">
    <div class="detail-toolbar"><button class="button button-quiet mobile-back" id="mobile-back" type="button">Back to tickets</button><span>Updated ${escapeHtml(formatTime(detail.updated_at))}</span></div>
    <header class="detail-header"><div><span class="eyebrow">Case detail</span><h2 id="case-detail-title">Ticket #${escapeHtml(detail.zendesk_ticket_id)}</h2><p>${escapeHtml(detail.title || "Untitled ticket")}</p></div>${detail.source_url ? `<a class="source-link" href="${escapeHtml(detail.source_url)}" target="_blank" rel="noopener noreferrer">zen#${escapeHtml(detail.zendesk_ticket_id)}</a>` : ""}</header>
    <section class="detail-section detail-overview">${renderFacts([
      ["Automation status", null, statusMarkup(detail.automation_status)],
      ["Zendesk status", null, `${statusMarkup(detail.ticket_status)}<small>Synced ${escapeHtml(formatTime(detail.zendesk_status_synced_at))}</small>`],
      ["Persona", persona ? `${persona.display_name} / ${persona.persona_key}` : "Not assigned"],
      ["Persona version", persona?.version ? `v${persona.version}` : "Not available"],
      ["Route result", route || "Not available"],
      ["Current execution", shortId(detail.current_execution_id) || "Not available"],
    ])}</section>
    <section class="detail-section tonal-section"><h3>Collected fields</h3>${renderCollectedFields(detail.collected_fields)}</section>
    <section class="detail-section conversation-section"><div class="section-heading"><h3>Conversation</h3><span>${escapeHtml(detail.conversation?.length || 0)} messages</span></div>${renderConversation(detail.conversation)}</section>
    <section class="detail-section pending-section"><div class="section-heading"><h3>Pending reply preview</h3></div>${renderPendingReply(detail.pending_reply)}</section>
    <details class="runtime-audit" ${state.auditOpen ? "open" : ""}>
      <summary><span><strong>Runtime audit</strong><small>${escapeHtml(detail.executions?.length || 0)} executions</small></span><span class="audit-chevron" aria-hidden="true">+</span></summary>
      <div class="runtime-audit-content"><section><h3>Execution history</h3>${renderExecutionHistory(detail.executions)}</section>${renderExecutionAudit()}<section><h3>Runtime heartbeat</h3>${renderRuntimeAudit()}</section></div>
    </details>
  </section>`;
}

function renderDashboard() {
  const workspaceClasses = ["workspace", state.showMobileDetail ? "show-mobile-detail" : ""].filter(Boolean).join(" ");
  return `
    <header class="app-header">
      <div class="app-identity"><div class="brand-mark" aria-hidden="true">S</div><div><strong>Production Automation</strong><small>Ticket operations</small></div></div>
      <div class="header-actions"><button class="button button-secondary filter-toggle" id="open-filters" type="button" aria-expanded="${state.filterOpen}">Filters</button><button class="button button-secondary" id="refresh" type="button">Refresh</button><button class="button button-quiet header-signout" id="logout" type="button">Sign out</button></div>
    </header>
    ${renderRuntimeStrip()}
    ${state.error ? `<div class="global-error" role="alert"><span>${escapeHtml(state.error)}</span><button class="button button-quiet" id="dismiss-error" type="button">Dismiss</button></div>` : ""}
    <main class="${workspaceClasses}">${renderFilterRail()}${renderCaseList()}${renderDetail()}</main>`;
}

function updateFiltersFromForm(form) {
  const data = new FormData(form);
  state.filters = {
    ...state.filters,
    route_subcategory: String(data.get("route_subcategory") || ""),
    ticket_status: String(data.get("ticket_status") || "active"),
    zendesk_ticket_id: String(data.get("zendesk_ticket_id") || "").trim(),
    execution_id: String(data.get("execution_id") || "").trim(),
    execution_status: String(data.get("execution_status") || ""),
    event_type: String(data.get("event_type") || ""),
  };
}

function bindEvents() {
  const loginForm = document.getElementById("login-form");
  if (loginForm) {
    loginForm.addEventListener("submit", (event) => {
      event.preventDefault();
      const data = new FormData(event.currentTarget);
      login(String(data.get("username") || ""), String(data.get("password") || ""));
    });
    return;
  }
  document.getElementById("logout")?.addEventListener("click", logout);
  document.getElementById("refresh")?.addEventListener("click", refreshAll);
  document.getElementById("dismiss-error")?.addEventListener("click", () => { state.error = ""; render(); });
  document.getElementById("open-filters")?.addEventListener("click", () => { state.filterOpen = true; render(); });
  document.getElementById("close-filters")?.addEventListener("click", () => { state.filterOpen = false; render(); });
  document.getElementById("filter-overlay")?.addEventListener("click", () => { state.filterOpen = false; render(); });
  document.getElementById("mobile-back")?.addEventListener("click", () => { state.showMobileDetail = false; render(); });

  const filterForm = document.getElementById("filters-form");
  filterForm?.addEventListener("submit", async (event) => {
    event.preventDefault();
    updateFiltersFromForm(event.currentTarget);
    state.page = 1;
    state.filterOpen = false;
    await loadCases({ selectFirst: true });
  });
  document.querySelector(".advanced-filters")?.addEventListener("toggle", (event) => {
    state.advancedOpen = event.currentTarget.open;
  });
  document.querySelectorAll("[data-route-group]").forEach((button) => {
    button.addEventListener("click", async () => {
      updateFiltersFromForm(filterForm);
      state.filters.route_group = button.dataset.routeGroup;
      state.filters.route_subcategory = "";
      state.page = 1;
      await loadCases({ selectFirst: true });
    });
  });
  document.getElementById("clear-filters")?.addEventListener("click", async () => {
    state.filters = {
      route_group: "all",
      route_subcategory: "",
      ticket_status: "active",
      zendesk_ticket_id: "",
      execution_id: "",
      execution_status: "",
      event_type: "",
    };
    state.page = 1;
    await loadCases({ selectFirst: true });
  });
  document.querySelectorAll("[data-ticket-id]").forEach((button) => {
    button.addEventListener("click", () => loadCaseDetail(button.dataset.ticketId, button.dataset.executionId || ""));
  });
  document.querySelectorAll("[data-page]").forEach((button) => {
    button.addEventListener("click", async () => {
      state.page = Number(button.dataset.page);
      await loadCases({ selectFirst: true });
    });
  });
  const auditDetails = document.querySelector(".runtime-audit");
  auditDetails?.addEventListener("toggle", (event) => { state.auditOpen = event.currentTarget.open; });
  document.querySelectorAll("[data-audit-execution-id]").forEach((button) => {
    button.addEventListener("click", async () => {
      state.auditOpen = true;
      await loadExecutionAudit(button.dataset.auditExecutionId);
    });
  });
}

function render() {
  if (state.loading) {
    app.innerHTML = `<main class="boot-state"><span class="spinner" aria-hidden="true"></span><strong>Loading Production cases</strong></main>`;
  } else if (!state.authenticated) {
    app.innerHTML = renderLogin();
  } else {
    app.innerHTML = renderDashboard();
  }
  bindEvents();
}

initialize();
