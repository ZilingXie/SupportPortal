const app = document.getElementById("app");
const environmentMatch = window.location.pathname.match(
  /^\/automation\/(preproduction|production)(?:\/|$)/,
);
if (!environmentMatch) throw new Error("Unsupported Automation dashboard path");
const basePath = `/automation/${environmentMatch[1]}`;
const environmentLabel = environmentMatch[1] === "preproduction" ? "Preproduction" : "Production";
document.title = `${environmentLabel} Automation Cases | Stellarix Support`;

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
  hermesReview: null,
  hermesReviewLoading: false,
  activeView: "cases",
  knowledge: { items: [], page: 1, pageSize: 20, total: 0, loading: false, error: null,
    filters: { status: "human_review", slack_review_status: "", decision: "", source_type: "" } },
  knowledgeDetail: null,
  knowledgeDetailLoading: false,
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
  if (["completed", "ready", "solved", "closed", "confirmed", "published", "delivered", "queued"].includes(normalized)) return "success";
  if (["failed", "error", "prepare_failed"].includes(normalized)) return "danger";
  if (["human_review", "pending", "hold", "manual_attention", "awaiting_approval", "approved"].includes(normalized)) return "warning";
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
  state.hermesReview = null;
  state.detailLoading = true;
  state.showMobileDetail = true;
  render();
  try {
    const detail = await request(`/dashboard/api/cases/${encodeURIComponent(ticketId)}`);
    if (state.selectedTicketId !== ticketId) return;
    state.detail = detail;
    const executionId = matchedExecutionId || detail.current_execution_id || detail.executions?.[0]?.execution_id || "";
    await loadExecutionAudit(executionId, false);
    await loadHermesReview(ticketId, false);
  } catch (error) {
    state.error = error.message;
  } finally {
    if (state.selectedTicketId === ticketId) state.detailLoading = false;
    render();
  }
}

async function loadHermesReview(ticketId, shouldRender = true) {
  if (state.selectedTicketId !== ticketId) return;
  state.hermesReviewLoading = true;
  if (shouldRender) render();
  try {
    const review = await request(`/dashboard/api/cases/${encodeURIComponent(ticketId)}/hermes-review`);
    if (state.selectedTicketId === ticketId) state.hermesReview = review;
  } catch (error) {
    if (state.selectedTicketId === ticketId) state.hermesReview = null;
  } finally {
    state.hermesReviewLoading = false;
    if (shouldRender) render();
  }
}

async function approveHermesDraft(ticketId, draftId) {
  state.error = "";
  try {
    await request(`/dashboard/api/cases/${encodeURIComponent(ticketId)}/hermes-review/drafts/${encodeURIComponent(draftId)}/approve`, {
      method: "POST",
      body: "{}",
    });
    await loadHermesReview(ticketId);
  } catch (error) {
    state.error = error.message;
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
        <h1 id="login-title">${environmentLabel} cases</h1>
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
  return `<section class="case-list-pane" aria-labelledby="case-list-title"><div class="pane-heading"><div><span class="eyebrow">${environmentLabel} queue</span><h1 id="case-list-title">Tickets</h1></div><span class="result-count">${escapeHtml(state.total)}</span></div>${renderCaseItems()}${renderPagination()}</section>`;
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

function renderHermesReview() {
  if (!state.hermesReview) return "";
  const review = state.hermesReview;
  const binding = review.binding || {};
  const investigation = binding.investigation || null;
  const activeTurn = review.active_turn || null;
  const drafts = review.drafts || [];
  const canAct = environmentMatch[1] === "preproduction";
  const turnStatus = activeTurn
    ? `Turn ${activeTurn.status}${activeTurn.run_id ? ` / run ${String(activeTurn.run_id).slice(0, 14)}` : ""}`
    : "No active turn";
  const investigationBlock = investigation
    ? `<div class="hermes-investigation">
        ${investigation.summary ? `<p class="message-body">${escapeHtml(investigation.summary)}</p>` : ""}
        ${investigation.evidence?.length ? `<h5>Evidence</h5><ul>${investigation.evidence.map((item) => `<li>${escapeHtml(typeof item === "string" ? item : JSON.stringify(item))}</li>`).join("")}</ul>` : ""}
        ${investigation.blockers?.length ? `<h5>Blockers</h5><ul>${investigation.blockers.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul>` : ""}
        ${investigation.next_steps?.length ? `<h5>Next steps</h5><ul>${investigation.next_steps.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul>` : ""}
      </div>`
    : `<p class="section-empty">No investigation summary yet.</p>`;
  const awaitingReviewTurn = (review.turns || []).find(
    (turn) => turn.turn_kind === "normal"
      && turn.direction === "investigation"
      && turn.status === "completed"
      && turn.result?.status === "awaiting_investigation_review"
      && !turn.result?.continued_turn_id,
  );
  const investigationReviewBlock = awaitingReviewTurn
    ? `<article class="message message-support hermes-draft">
        <header><strong>Investigation ${escapeHtml(String(awaitingReviewTurn.turn_id).slice(0, 18))}</strong><span>human gate</span>${statusMarkup("pending")}</header>
        <p class="message-body">The Hermes investigation result above is awaiting your review${investigation?.blockers?.length ? ` (note: ${investigation.blockers.length} open blocker${investigation.blockers.length > 1 ? "s" : ""} listed above)` : ""}. Continue only when the result is good enough to draft the customer reply from.</p>
        ${canAct
          ? `<button class="button button-secondary" data-hermes-continue-turn-id="${escapeHtml(awaitingReviewTurn.turn_id)}" type="button">Continue to customer reply</button>`
          : ""}
      </article>`
    : "";
  const draftBlock = drafts.length
    ? drafts.map((draft) => {
        const guardrail = draft.guardrail || {};
        const approveButton = canAct && draft.status === "awaiting_approval"
          ? `<button class="button button-secondary" data-hermes-approve-draft-id="${escapeHtml(draft.draft_id)}" type="button">Approve &amp; send</button>`
          : "";
        const retryButton = canAct && draft.status === "prepare_failed"
          ? `<button class="button button-secondary" data-hermes-approve-draft-id="${escapeHtml(draft.draft_id)}" type="button">Retry Prepare &amp; Send</button>`
          : "";
        const changesButton = canAct && draft.status === "awaiting_approval" && draft.publish_policy === "manual"
          ? `<button class="button button-quiet" data-hermes-changes-draft-id="${escapeHtml(draft.draft_id)}" type="button">Request changes</button>`
          : "";
        const prepError = draft.status === "prepare_failed" && draft.prep_error
          ? `<p class="hermes-prep-error">Preparation error: ${escapeHtml(draft.prep_error)}</p>`
          : "";
        return `<article class="message message-support hermes-draft">
          <header><strong>Draft ${escapeHtml(String(draft.draft_id).slice(0, 18))}</strong><span>${escapeHtml(draft.publish_policy)}</span>${statusMarkup(draft.status)}</header>
          <p class="message-body">${escapeHtml(draft.content)}</p>
          ${prepError}
          <footer>Guardrail: ${escapeHtml(String(guardrail.decision || "not run"))}${guardrail.blockers?.length ? ` — ${escapeHtml(guardrail.blockers.join("; "))}` : ""}${draft.approved_by ? ` / approved by ${escapeHtml(draft.approved_by)}` : ""}</footer>
          ${approveButton}${retryButton}${changesButton}
        </article>`;
      }).join("")
    : `<p class="section-empty">No reviewable drafts.</p>`;
  const feedbackForm = "";
  const caseInfo = review.case || {};
  const activeCustomer = caseInfo.active_customer || {};
  const customerLabel = activeCustomer.name || activeCustomer.email || "Customer";
  return `<section class="detail-section hermes-review-section">
    <div class="section-heading"><h3>Hermes agent review</h3><span>${escapeHtml(turnStatus)}</span></div>
    ${renderFacts([
      ["Direction", null, statusMarkup(binding.direction || "pending")],
      ["Case revision", caseInfo.case_revision ?? binding.conversation_version],
      ["Active customer", customerLabel],
      ["Case status", binding.status],
      ["Direction reason", binding.direction_reason],
    ])}
    <h4>Investigation</h4>
    ${investigationBlock}
    ${investigationReviewBlock}
    <h4>Reply drafts</h4>
    ${draftBlock}
    ${feedbackForm}
  </section>`;
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
    ${renderHermesReview()}
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
      <div class="app-identity"><div class="brand-mark" aria-hidden="true">S</div><div><strong>${environmentLabel} Automation</strong><small>Ticket operations</small></div></div>
      <div class="header-actions"><nav class="view-tabs" aria-label="Dashboard views"><button class="view-tab ${state.activeView === "cases" ? "is-active" : ""}" id="tab-cases" type="button" aria-current="${state.activeView === "cases"}">Cases</button><button class="view-tab ${state.activeView === "knowledge" ? "is-active" : ""}" id="tab-knowledge" type="button" aria-current="${state.activeView === "knowledge"}">Knowledge Review</button></nav><button class="button button-secondary filter-toggle" id="open-filters" type="button" aria-expanded="${state.filterOpen}">Filters</button><button class="button button-secondary" id="refresh" type="button">Refresh</button><button class="button button-quiet header-signout" id="logout" type="button">Sign out</button></div>
    </header>
    ${renderRuntimeStrip()}
    ${state.error ? `<div class="global-error" role="alert"><span>${escapeHtml(state.error)}</span><button class="button button-quiet" id="dismiss-error" type="button">Dismiss</button></div>` : ""}
    ${state.activeView === "knowledge" ? renderKnowledgeReview() : `<main class="${workspaceClasses}">${renderFilterRail()}${renderCaseList()}${renderDetail()}</main>`}`;
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
  document.getElementById("refresh")?.addEventListener("click", async () => {
    await refreshAll();
    if (state.activeView === "knowledge") {
      if (state.knowledgeDetail?.promotion_id) await loadKnowledgeDetail(state.knowledgeDetail.promotion_id, false);
      await loadKnowledgePromotions({ selectFirst: false });
    }
  });
  document.getElementById("dismiss-error")?.addEventListener("click", () => { state.error = ""; render(); });
  document.getElementById("open-filters")?.addEventListener("click", () => { state.filterOpen = true; render(); });
  document.getElementById("close-filters")?.addEventListener("click", () => { state.filterOpen = false; render(); });
  document.getElementById("filter-overlay")?.addEventListener("click", () => { state.filterOpen = false; render(); });
  document.getElementById("mobile-back")?.addEventListener("click", () => { state.showMobileDetail = false; render(); });

  document.querySelectorAll("[data-hermes-approve-draft-id]").forEach((button) => {
    button.addEventListener("click", async () => {
      const draftId = String(button.dataset.hermesApproveDraftId || "");
      const ticketId = state.selectedTicketId;
      if (draftId && ticketId) await approveHermesDraft(ticketId, draftId);
    });
  });
  document.querySelectorAll("[data-hermes-changes-draft-id]").forEach((button) => {
    button.addEventListener("click", async () => {
      const draftId = String(button.dataset.hermesChangesDraftId || "");
      const ticketId = state.selectedTicketId;
      const feedback = window.prompt("What should change in this draft?") || "";
      if (draftId && ticketId && feedback.trim()) {
        state.error = "";
        try {
          await request(`/dashboard/api/cases/${encodeURIComponent(ticketId)}/hermes-review/drafts/${encodeURIComponent(draftId)}/request-changes`, {
            method: "POST",
            body: JSON.stringify({ feedback: feedback.trim() }),
          });
          await loadHermesReview(ticketId);
        } catch (error) {
          state.error = error.message;
          render();
        }
      }
    });
  });

  document.querySelectorAll("[data-hermes-continue-turn-id]").forEach((button) => {
    button.addEventListener("click", async () => {
      const ticketId = state.selectedTicketId;
      if (!ticketId) return;
      const review = state.hermesReview || {};
      const blockers = review.binding?.investigation?.blockers || [];
      const confirmText = blockers.length
        ? `Continue this investigation into a customer reply draft?\n\nThe investigation still lists ${blockers.length} open blocker${blockers.length > 1 ? "s" : ""}:\n${blockers.slice(0, 5).map((item) => `- ${item}`).join("\n")}${blockers.length > 5 ? "\n- …" : ""}\n\nContinue anyway?`
        : "Continue this investigation into a customer reply draft?";
      if (!window.confirm(confirmText)) return;
      state.error = "";
      try {
        await request(`/dashboard/api/cases/${encodeURIComponent(ticketId)}/hermes-review/investigation/continue`, {
          method: "POST",
        });
        await loadHermesReview(ticketId);
      } catch (error) {
        state.error = error.message;
        render();
      }
    });
  });

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
    app.innerHTML = `<main class="boot-state"><span class="spinner" aria-hidden="true"></span><strong>Loading ${environmentLabel} cases</strong></main>`;
  } else if (!state.authenticated) {
    app.innerHTML = renderLogin();
  } else {
    app.innerHTML = renderDashboard();
  }
  bindEvents();
}

initialize();

// ------------------------------------------------ Knowledge Review (stage 5)

function knowledgeStatusTone(status) {
  const normalized = String(status || "").toLowerCase();
  if (["accepted", "delivered", "ready", "available", "completed"].includes(normalized)) return "ok";
  if (["queued", "pending", "running", "sending", "active", "processing"].includes(normalized)) return "info";
  if (["failed", "malformed", "missing", "invalidated", "rejected"].includes(normalized)) return "danger";
  if (["outcome_unknown", "unavailable", "lineage_conflict", "not_recorded"].includes(normalized)) return "unknown";
  if (["human_review"].includes(normalized)) return "warning";
  return "unknown";
}

function knowledgeQualityTone(state) {
  const normalized = String(state || "").toLowerCase();
  if (normalized === "available") return "ok";
  if (normalized === "not_recorded") return "info";
  if (normalized === "malformed") return "danger";
  return "unknown";
}

function deliverySummaryBadges(summary) {
  const entries = Object.entries(summary || {});
  if (!entries.length) {
    return `<span class="knowledge-muted">No delivery rows</span>`;
  }
  return entries.map(([target, status]) =>
    `<span class="delivery-badge target-${escapeHtml(target)}"><span class="delivery-target">${escapeHtml(target.replace("_", " "))}</span>${statusMarkup(status, true)}</span>`
  ).join("");
}

function knowledgeSlackLink(slack) {
  if (!slack) return `<span class="knowledge-muted">Not available</span>`;
  if (slack.thread_url) {
    return `<a class="knowledge-thread-link" href="${escapeHtml(slack.thread_url)}" target="_blank" rel="noopener noreferrer">Open thread</a>`;
  }
  const reasons = {
    no_slack_channel_recorded: "No Slack channel recorded",
    no_slack_thread_recorded: "No Slack thread recorded",
    slack_workspace_not_configured: "Slack workspace not configured",
  };
  return `<span class="knowledge-muted">${escapeHtml(reasons[slack.thread_url_reason] || slack.thread_url_reason || "Link unavailable")}</span>`;
}

function knowledgeAuditLine(audit) {
  if (!audit || !audit.decision) return `<span class="knowledge-muted">No human decision</span>`;
  const parts = [`${escapeHtml(audit.decision)}`];
  if (audit.email) parts.push(`by ${escapeHtml(audit.email)}`);
  if (audit.slack_user_id) parts.push(`(${escapeHtml(audit.slack_user_id)})`);
  if (audit.decided_at) parts.push(`at ${escapeHtml(audit.decided_at)}`);
  return parts.join(" ");
}

function knowledgeDetailSection(title, body, extraClass = "") {
  return `<section class="knowledge-section ${extraClass}"><h3>${escapeHtml(title)}</h3>${body}</section>`;
}

function knowledgeStateRow(label, state, reason) {
  const tone = knowledgeQualityTone(state);
  return `<div class="knowledge-state-row"><span class="knowledge-state-label">${escapeHtml(label)}</span><span class="status-pill tone-${tone}"><span class="status-dot" aria-hidden="true"></span>${escapeHtml(String(state || "unknown"))}</span>${reason ? `<span class="knowledge-muted">${escapeHtml(String(reason))}</span>` : ""}</div>`;
}

function renderKnowledgeListItem(item) {
  const selected = state.knowledgeDetail?.promotion_id === item.promotion_id;
  return `
    <li>
      <button class="knowledge-item ${selected ? "is-selected" : ""}" type="button" data-knowledge-promotion="${escapeHtml(item.promotion_id)}">
        <div class="knowledge-item-head">
          ${statusMarkup(item.status, true)}
          <span class="knowledge-item-decision">${escapeHtml(item.decision || "—")}</span>
          <span class="knowledge-item-type">${escapeHtml(item.candidate_type || "")}</span>
        </div>
        <div class="knowledge-item-title">${escapeHtml(item.title || item.statement || item.promotion_id)}</div>
        <div class="knowledge-item-meta">
          <span class="knowledge-item-source">${escapeHtml(item.source?.type || "")}:${escapeHtml(item.source?.id || "")}</span>
          <span class="knowledge-item-date">${escapeHtml(item.created_at || "")}</span>
        </div>
        <div class="knowledge-item-deliveries">${deliverySummaryBadges(item.delivery_summary)}</div>
      </button>
    </li>`;
}

function knowledgeFilterBar() {
  const filters = state.knowledge.filters;
  const statuses = ["", "human_review", "queued", "active", "accepted", "failed", "outcome_unknown", "invalidated", "rejected"];
  const slackStatuses = ["", "none", "queued", "sending", "delivered", "failed", "outcome_unknown"];
  return `
    <form id="knowledge-filters" class="knowledge-filters">
      <label class="filter-field"><span>Promotion status</span><select name="status">${statuses.map((value) => `<option value="${escapeHtml(value)}" ${filters.status === value ? "selected" : ""}>${escapeHtml(value === "" ? "All statuses" : value === "human_review" ? "human_review (default)" : value)}</option>`).join("")}</select></label>
      <label class="filter-field"><span>Decision</span><input name="decision" value="${escapeHtml(filters.decision)}" placeholder="e.g. merge" /></label>
      <label class="filter-field"><span>Source type</span><input name="source_type" value="${escapeHtml(filters.source_type)}" placeholder="e.g. knowledge_source_review" /></label>
      <label class="filter-field"><span>Slack review</span><select name="slack_review_status">${slackStatuses.map((value) => `<option value="${escapeHtml(value)}" ${filters.slack_review_status === value ? "selected" : ""}>${escapeHtml(value === "" ? "All" : value)}</option>`).join("")}</select></label>
      <div class="filter-actions"><button class="button button-primary" type="submit">Apply</button></div>
    </form>`;
}

function renderKnowledgePagination() {
  const { page, pageSize, total } = state.knowledge;
  const pages = Math.max(1, Math.ceil(total / pageSize));
  return `
    <nav class="knowledge-pagination" aria-label="Knowledge review pages">
      <button class="button button-secondary" type="button" data-knowledge-page="${Math.max(1, page - 1)}" ${page <= 1 ? "disabled" : ""}>Previous</button>
      <span>Page ${escapeHtml(String(page))} of ${escapeHtml(String(pages))} · ${escapeHtml(String(total))} candidates</span>
      <button class="button button-secondary" type="button" data-knowledge-page="${Math.min(pages, page + 1)}" ${page >= pages ? "disabled" : ""}>Next</button>
    </nav>`;
}

function renderKnowledgeList() {
  if (state.knowledge.loading) {
    return `<div class="knowledge-empty"><span class="spinner" aria-hidden="true"></span><strong>Loading knowledge review queue</strong></div>`;
  }
  if (state.knowledge.error) {
    return `<div class="knowledge-empty knowledge-error" role="alert"><strong>${escapeHtml(state.knowledge.error)}</strong></div>`;
  }
  if (!state.knowledge.items.length) {
    return `<div class="knowledge-empty"><strong>No candidates match the filters</strong></div>`;
  }
  return `
    <ul class="knowledge-list">
      ${state.knowledge.items.map(renderKnowledgeListItem).join("")}
    </ul>
    ${renderKnowledgePagination()}`;
}

function renderKnowledgeDetail() {
  if (state.knowledgeDetailLoading) {
    return `<div class="knowledge-detail knowledge-empty"><span class="spinner" aria-hidden="true"></span><strong>Loading candidate evidence</strong></div>`;
  }
  const detail = state.knowledgeDetail;
  if (!detail) {
    return `<div class="knowledge-detail knowledge-empty"><strong>Select a candidate to review its evidence</strong><p class="knowledge-muted">Approve or reject in the Slack thread — this page is read-only.</p></div>`;
  }
  const sections = [];
  sections.push(knowledgeDetailSection(
    "Candidate",
    `
    <dl class="knowledge-fields">
      <div><dt>Decision requested</dt><dd>${statusMarkup(detail.decision || "unknown", true)}</dd></div>
      <div><dt>Target object</dt><dd>${renderMaybeCode(detail.target_object_id)}</dd></div>
      <div><dt>Base version</dt><dd>${renderMaybeCode(detail.base_version)}</dd></div>
      <div><dt>Content hash</dt><dd>${renderMaybeCode(detail.source?.content_hash)}</dd></div>
    </dl>
    ${detail.candidate_payload?.content ? `<details class="knowledge-body" open><summary>Complete candidate body</summary><pre>${escapeHtml(detail.candidate_payload.content)}</pre></details>` : ""}
    ${detail.candidate_payload?.merged_content ? `<details class="knowledge-body"><summary>Merged body</summary><pre>${escapeHtml(detail.candidate_payload.merged_content)}</pre></details>` : ""}
    ${detail.candidate_payload?.note ? `<p class="knowledge-rationale"><strong>Review rationale:</strong> ${escapeHtml(detail.candidate_payload.note)}</p>` : ""}`
  ));
  const snap = detail.source_snapshot;
  sections.push(knowledgeDetailSection(
    "Source snapshot",
    snap?.state === "available"
      ? `
        <dl class="knowledge-fields">
          <div><dt>Source</dt><dd>${escapeHtml(snap.source_type || "")}:${escapeHtml(snap.source_id || "")}</dd></div>
          <div><dt>Version</dt><dd>${renderMaybeCode(snap.source_version)}</dd></div>
          <div><dt>Updated at</dt><dd>${renderMaybeCode(snap.source_updated_at)}</dd></div>
        </dl>
        <details class="knowledge-body"><summary>Payload</summary><pre>${escapeHtml(JSON.stringify(snap.payload ?? {}, null, 2))}</pre></details>
        ${snap.references ? `<details class="knowledge-body"><summary>References</summary><pre>${escapeHtml(JSON.stringify(snap.references, null, 2))}</pre></details>` : ""}`
      : `${knowledgeStateRow("Snapshot", snap?.state || "unavailable", snap?.reason)}`
  ));
  const summary = detail.summary;
  sections.push(knowledgeDetailSection(
    "AI Summary",
    summary?.state === "available"
      ? `${knowledgeStateRow("Summary task", summary.status || "unknown", summary.error)}<dl class="knowledge-fields"><div><dt>Session</dt><dd>${renderMaybeCode(summary.session_id)}</dd></div><div><dt>Run</dt><dd>${renderMaybeCode(summary.run_id)}</dd></div></dl>
        ${summary.packet ? `<details class="knowledge-body"><summary>Summary packet</summary><pre>${escapeHtml(JSON.stringify(summary.packet, null, 2))}</pre></details>` : ""}`
      : `${knowledgeStateRow("Summary", summary?.state || "unavailable", summary?.reason)}`
  ));
  const review = detail.review;
  sections.push(knowledgeDetailSection(
    "AI Review",
    review?.state === "available"
      ? `${knowledgeStateRow("Review task", review.status || "unknown", review.error)}<dl class="knowledge-fields"><div><dt>Session</dt><dd>${renderMaybeCode(review.session_id)}</dd></div><div><dt>Run</dt><dd>${renderMaybeCode(review.run_id)}</dd></div></dl>
        ${review.report ? `<details class="knowledge-body" open><summary>Review report</summary><pre>${escapeHtml(JSON.stringify(review.report, null, 2))}</pre></details>` : ""}`
      : `${knowledgeStateRow("Review", review?.state || "unavailable", review?.reason || review?.malformed_reason)}`
  ));
  const dup = detail.duplicate_evidence;
  sections.push(knowledgeDetailSection(
    "Duplicate evidence",
    dup?.state === "available"
      ? `<details class="knowledge-body" open><summary>Retrieval surfaces (hits / availability)</summary><pre>${escapeHtml(JSON.stringify(dup.retrieval_surfaces ?? {}, null, 2))}</pre></details>`
      : `${knowledgeStateRow("Duplicate evidence", dup?.state || "unavailable", dup?.reason)}`
  ));
  const deliveries = detail.deliveries || {};
  const deliveryBody = Object.entries(deliveries).length
    ? Object.entries(deliveries).map(([target, d]) => `
        <div class="delivery-detail target-${escapeHtml(target)}">
          <div class="delivery-detail-head">${statusMarkup(d.status, true)}<strong>${escapeHtml(target.replace("_", " "))}</strong></div>
          <dl class="knowledge-fields">
            <div><dt>External object</dt><dd>${renderMaybeCode(d.external_object_id)}</dd></div>
            <div><dt>External version</dt><dd>${renderMaybeCode(d.external_version)}</dd></div>
            <div><dt>Attempts</dt><dd>${escapeHtml(String(d.attempt_count ?? "—"))}</dd></div>
          </dl>
          ${d.failure_code ? `<p class="knowledge-failure"><strong>${escapeHtml(d.failure_code)}</strong>${d.failure_detail ? `: ${escapeHtml(d.failure_detail)}` : ""}</p>` : ""}
          ${d.operation_receipt ? `<details class="knowledge-body"><summary>Receipt</summary><pre>${escapeHtml(JSON.stringify(d.operation_receipt, null, 2))}</pre></details>` : ""}
          ${d.readback_result ? `<details class="knowledge-body"><summary>Readback</summary><pre>${escapeHtml(JSON.stringify(d.readback_result, null, 2))}</pre></details>` : ""}
        </div>`).join("")
    : `<span class="knowledge-muted">No delivery rows recorded</span>`;
  sections.push(knowledgeDetailSection("Dual-target delivery", deliveryBody, "knowledge-deliveries"));
  sections.push(knowledgeDetailSection(
    "Human audit",
    `<p class="knowledge-audit">${knowledgeAuditLine(detail.human_audit)}</p>
     <dl class="knowledge-fields"><div><dt>Slack thread</dt><dd>${knowledgeSlackLink(detail.slack)}</dd></div>${
       detail.slack?.review_status ? `<div><dt>Notification</dt><dd>${statusMarkup(detail.slack.review_status, true)}${detail.slack.failure_code ? ` <span class="knowledge-muted">${escapeHtml(detail.slack.failure_code)}</span>` : ""}</dd></div>` : ""
     }</dl>
     <p class="knowledge-muted knowledge-readonly-note">Approve or reject by replying in the Slack thread. This page is read-only.</p>`
  ));
  return `
    <article class="knowledge-detail" aria-labelledby="knowledge-detail-title">
      <header class="knowledge-detail-head">
        <h2 id="knowledge-detail-title">${escapeHtml(detail.title || detail.statement || detail.promotion_id)}</h2>
        ${statusMarkup(detail.status)}
        <code class="knowledge-promotion-id">${escapeHtml(detail.promotion_id)}</code>
      </header>
      ${sections.join("")}
    </article>`;
}

function renderMaybeCode(value) {
  if (value === null || value === undefined || String(value).trim() === "") {
    return `<span class="knowledge-muted">—</span>`;
  }
  return `<code>${escapeHtml(String(value))}</code>`;
}

function renderKnowledgeReview() {
  return `
    <main class="knowledge-workspace" aria-label="Knowledge review">
      <div class="knowledge-queue">
        <header class="knowledge-queue-head">
          <div><span class="eyebrow">Knowledge governance</span><h2>Review queue</h2></div>
        </header>
        ${knowledgeFilterBar()}
        ${renderKnowledgeList()}
      </div>
      ${renderKnowledgeDetail()}
    </main>`;
}

async function loadKnowledgePromotions({ selectFirst = true } = {}) {
  state.knowledge.loading = true;
  state.knowledge.error = null;
  render();
  try {
    const filters = state.knowledge.filters;
    const params = new URLSearchParams();
    if (filters.status) params.set("status", filters.status);
    if (filters.decision) params.set("decision", filters.decision);
    if (filters.source_type) params.set("source_type", filters.source_type);
    if (filters.slack_review_status) params.set("slack_review_status", filters.slack_review_status);
    params.set("page", String(state.knowledge.page));
    params.set("page_size", String(state.knowledge.pageSize));
    const result = await request(`/dashboard/api/knowledge/promotions?${params.toString()}`);
    state.knowledge.items = result.items || [];
    state.knowledge.total = result.total || 0;
    state.knowledge.page = result.page || 1;
    state.knowledge.pageSize = result.page_size || 20;
    if (selectFirst && state.knowledge.items.length && !state.knowledge.items.some((item) => item.promotion_id === state.knowledgeDetail?.promotion_id)) {
      await loadKnowledgeDetail(state.knowledge.items[0].promotion_id, false);
    }
  } catch (error) {
    state.knowledge.error = String(error?.message || error);
  } finally {
    state.knowledge.loading = false;
    render();
  }
}

async function loadKnowledgeDetail(promotionId, shouldRender = true) {
  state.knowledgeDetailLoading = true;
  if (shouldRender) render();
  try {
    const detail = await request(`/dashboard/api/knowledge/promotions/${encodeURIComponent(promotionId)}`);
    state.knowledgeDetail = detail;
  } catch (error) {
    state.knowledgeDetail = null;
    state.knowledge.error = String(error?.message || error);
  } finally {
    state.knowledgeDetailLoading = false;
    if (shouldRender) render();
  }
}
