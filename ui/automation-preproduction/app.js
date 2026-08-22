const appRoot = document.getElementById("app");
const toastRoot = document.getElementById("toast-root");
const SharedComposer = globalThis.SupportPortalComposer || {};

const renderMarkdownMessage =
  SharedComposer.renderMarkdownMessage ||
  ((value) =>
    String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll("\n", "<br>"));

const ENV = {
  environment: "preproduction",
  template: "production",
  apiBase: "/automation/preproduction",
  tokenKey: "automation-preproduction-execution-token",
  brandEyebrow: "AUTOMATION / PREPRODUCTION",
  brandTitle: "Automation preproduction",
  loginTagline: "Run allowlisted ticket executions with forced internal Zendesk delivery.",
  workbenchPill: "Zendesk internal only · allowlisted tickets",
  submitLabel: "Run Preproduction",
  requiresTicket: true,
  ticketHint: "Must be in the preproduction allowlist",
  visibilityMode: "fixed",
  fixedVisibility: "internal",
  casePlaceholder: "AC-PREPRODUCTION-001",
/*__RERUN_START__*/
  rerunWritesZendesk: true,
/*__RERUN_END__*/
};

const PAGE_SIZE = 10;

const STATUS_META = {
  pending: { label: "Pending", className: "status-badge--automation" },
  prepared: { label: "Prepared", className: "status-badge--automation" },
  completed: { label: "Completed", className: "status-badge--automated" },
  human_review: { label: "Human review", className: "status-badge--needs" },
  failed: { label: "Failed", className: "status-badge--not" },
  outcome_unknown: { label: "Outcome unknown", className: "status-badge--needs" },
};
const STATUS_ORDER = ["pending", "prepared", "completed", "human_review", "failed", "outcome_unknown"];

const state = {
  authorized: false,
  tokenError: "",
  tokenChecking: false,
  capabilities: null,
  view: "create",
  executions: [],
  total: 0,
  statusCounts: {},
  page: 1,
  statusFilter: "all",
  historyLoading: false,
  historyError: "",
  caseSearchQuery: "",
  caseSearchError: "",
  activeExecution: null,
  detailLoading: false,
  detailError: "",
  form: { caseId: "", title: "", customerEmail: "", question: "", ticketId: "", visibility: "" },
  isSubmitting: false,
  submitError: "",
  submitFollowUpExecutionId: "",
/*__RERUN_START__*/
  rerunConfirmation: null,
  isRerunning: false,
/*__RERUN_END__*/
  resetConfirmationOpen: false,
  isResetting: false,
  isReconciling: false,
  reconcileError: "",
};

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function formatTimestamp(value) {
  const raw = String(value || "").trim();
  if (!raw) return "";
  const parsed = new Date(raw);
  if (Number.isNaN(parsed.getTime())) return raw;
  return parsed.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

function showToast(message) {
  if (!toastRoot) return;
  toastRoot.innerHTML = `<div class="toast">${escapeHtml(message)}</div>`;
  window.setTimeout(() => {
    toastRoot.innerHTML = "";
  }, 3200);
}

class ApiError extends Error {
  constructor(status, payload) {
    super(`API request failed (${status})`);
    this.status = status;
    this.payload = payload;
  }
}

async function apiRequest(path, options = {}) {
  const token = localStorage.getItem(ENV.tokenKey) || "";
  const headers = { ...(options.headers || {}) };
  if (token) headers.Authorization = `Bearer ${token}`;
  if (options.body) headers["Content-Type"] = "application/json";
  const response = await fetch(`${ENV.apiBase}${path}`, { ...options, cache: "no-store", headers });
  let payload = null;
  try {
    payload = await response.json();
  } catch {
    payload = null;
  }
  if (response.status === 401) {
    localStorage.removeItem(ENV.tokenKey);
    state.authorized = false;
    state.tokenError = "Execution token was rejected. Enter a valid token.";
  }
  if (!response.ok) throw new ApiError(response.status, payload);
  return payload;
}

function describeApiError(error, fallback = "Request failed") {
  const detail = error?.payload?.detail;
  if (typeof detail === "string" && detail.trim()) return detail;
  if (detail && typeof detail === "object") {
    const code = String(detail.code || "").trim();
    if (code) return code.replaceAll("_", " ");
  }
  if (error?.status) return `${fallback} (${error.status})`;
  return fallback;
}

function errorExecutionId(error) {
  const detail = error?.payload?.detail;
  if (detail && typeof detail === "object" && detail.execution && typeof detail.execution === "object") {
    return String(detail.execution.execution_id || "").trim();
  }
  return "";
}

async function loadCapabilities() {
  try {
    const response = await fetch(`${ENV.apiBase}/v1/capabilities`, { cache: "no-store" });
    if (!response.ok) throw new Error(`Capabilities request failed (${response.status})`);
    state.capabilities = await response.json();
  } catch {
    state.capabilities = null;
  }
}

async function connectToken(event) {
  event?.preventDefault();
  const form = event?.target instanceof HTMLFormElement ? event.target : null;
  const token = form ? String(new FormData(form).get("execution_token") || "").trim() : "";
  if (!token) {
    state.tokenError = "Enter the execution bearer token.";
    render();
    return;
  }
  localStorage.setItem(ENV.tokenKey, token);
  state.tokenChecking = true;
  state.tokenError = "";
  render();
  try {
    await apiRequest(`/v1/executions?page=1&page_size=1`);
    state.authorized = true;
    state.tokenError = "";
    await loadExecutions();
  } catch (error) {
    if (error instanceof ApiError && error.status === 401) {
      state.authorized = false;
    } else {
      state.tokenError = describeApiError(error, "Could not reach the automation runtime");
    }
  } finally {
    state.tokenChecking = false;
    render();
  }
}

function disconnectToken() {
  localStorage.removeItem(ENV.tokenKey);
  state.authorized = false;
  state.tokenError = "";
  state.executions = [];
  state.total = 0;
  state.statusCounts = {};
  state.page = 1;
  state.activeExecution = null;
  state.view = "create";
  render();
}

function executionsQueryString({ page = state.page, status = state.statusFilter } = {}) {
  const params = new URLSearchParams();
  params.set("page", String(page));
  params.set("page_size", String(PAGE_SIZE));
  if (status && status !== "all") params.set("status", status);
  return params.toString();
}

async function loadExecutions({ page = state.page, renderOnUpdate = true } = {}) {
  state.historyLoading = true;
  state.historyError = "";
  if (renderOnUpdate) render();
  try {
    const payload = await apiRequest(`/v1/executions?${executionsQueryString({ page })}`);
    state.executions = Array.isArray(payload.executions) ? payload.executions : [];
    state.total = Number(payload.total || 0);
    state.statusCounts = payload.status_counts && typeof payload.status_counts === "object" ? payload.status_counts : {};
    state.page = Number(payload.page || page);
  } catch (error) {
    if (error instanceof ApiError && error.status === 401) {
      state.authorized = false;
    } else {
      state.historyError = describeApiError(error, "Could not load executions");
    }
  } finally {
    state.historyLoading = false;
    if (renderOnUpdate) render();
  }
}

async function openExecution(executionId) {
  const id = String(executionId || "").trim();
  if (!id) return;
  state.view = "detail";
  state.detailLoading = true;
  state.detailError = "";
  state.reconcileError = "";
  state.activeExecution = null;
  render();
  try {
    const payload = await apiRequest(`/v1/executions/${encodeURIComponent(id)}`);
    state.activeExecution = payload.execution && typeof payload.execution === "object" ? payload.execution : null;
    if (!state.activeExecution) state.detailError = "Execution record is empty.";
  } catch (error) {
    if (error instanceof ApiError && error.status === 401) {
      state.authorized = false;
      state.view = "create";
    } else {
      state.detailError = describeApiError(error, "Could not load execution");
    }
  } finally {
    state.detailLoading = false;
    render();
  }
}

async function searchByCaseId(event) {
  event?.preventDefault();
  const query = state.caseSearchQuery.trim();
  if (!query) {
    state.caseSearchError = "Enter a Case ID.";
    render();
    return;
  }
  state.caseSearchError = "";
  state.historyLoading = true;
  render();
  try {
    const params = new URLSearchParams({ page: "1", page_size: "1", case_id: query });
    const payload = await apiRequest(`/v1/executions?${params.toString()}`);
    const executions = Array.isArray(payload.executions) ? payload.executions : [];
    if (!executions.length) {
      state.caseSearchError = `Case ${query} not found`;
      return;
    }
    await openExecution(executions[0].execution_id);
    return;
  } catch (error) {
    if (error instanceof ApiError && error.status === 401) {
      state.authorized = false;
    } else {
      state.caseSearchError = describeApiError(error, "Could not search executions");
    }
  } finally {
    state.historyLoading = false;
    render();
  }
}

function buildExecutionPayload() {
  const payload = {
    request_id: crypto.randomUUID(),
    case_id: state.form.caseId.trim(),
    subject: state.form.title.trim(),
    question: state.form.question.trim(),
  };
  const email = state.form.customerEmail.trim();
  if (email) payload.customer_email = email;
  if (ENV.requiresTicket) payload.zendesk_ticket_id = state.form.ticketId.trim();
  if (ENV.visibilityMode === "fixed") payload.comment_visibility = ENV.fixedVisibility;
  if (ENV.visibilityMode === "select") payload.comment_visibility = state.form.visibility;
  return payload;
}

async function submitExecution(event) {
  event?.preventDefault();
  if (state.isSubmitting) return;
  if (!state.form.caseId.trim() || !state.form.question.trim()) {
    state.submitError = "Case ID and Question are required.";
    render();
    return;
  }
  if (ENV.requiresTicket && !state.form.ticketId.trim()) {
    state.submitError = "Zendesk ticket ID is required in this environment.";
    render();
    return;
  }
  if (ENV.visibilityMode === "select" && !state.form.visibility) {
    state.submitError = "Select an explicit comment visibility before running in production.";
    render();
    return;
  }
  state.isSubmitting = true;
  state.submitError = "";
  state.submitFollowUpExecutionId = "";
  render();
  try {
    const payload = await apiRequest("/v1/cases", { method: "POST", body: JSON.stringify(buildExecutionPayload()) });
    const execution = payload.execution && typeof payload.execution === "object" ? payload.execution : null;
    showToast(`Execution ${String(payload.status || "submitted").replaceAll("_", " ")}`);
    state.view = "detail";
    state.activeExecution = execution;
    state.detailError = execution ? "" : "Execution created but no record was returned.";
    await loadExecutions({ page: 1, renderOnUpdate: false });
  } catch (error) {
    if (error instanceof ApiError && error.status === 401) {
      state.authorized = false;
    } else {
      state.submitError = describeApiError(error, "Execution failed");
      state.submitFollowUpExecutionId = errorExecutionId(error);
    }
  } finally {
    state.isSubmitting = false;
    render();
  }
}

/*__RERUN_START__*/
function openRerunConfirmation() {
  if (!state.capabilities?.rerun) return;
  const execution = state.activeExecution;
  if (!execution) return;
  state.rerunConfirmation = {
    executionId: String(execution.execution_id || ""),
    caseId: String(execution.case_id || ""),
    ticketId: String(execution.request?.zendesk_ticket_id || ""),
  };
  render();
}

async function confirmRerun() {
  const snapshot = state.rerunConfirmation;
  if (!snapshot || state.isRerunning) return;
  state.isRerunning = true;
  render();
  try {
    const payload = await apiRequest("/v1/reruns", {
      method: "POST",
      body: JSON.stringify({
        request_id: crypto.randomUUID(),
        case_id: snapshot.caseId,
        rerun_of_execution_id: snapshot.executionId,
      }),
    });
    const execution = payload.execution && typeof payload.execution === "object" ? payload.execution : null;
    showToast(`Rerun ${String(payload.status || "submitted").replaceAll("_", " ")}`);
    state.rerunConfirmation = null;
    state.view = "detail";
    state.activeExecution = execution;
    state.detailError = execution ? "" : "Rerun created but no record was returned.";
    await loadExecutions({ page: 1, renderOnUpdate: false });
  } catch (error) {
    if (error instanceof ApiError && error.status === 401) {
      state.authorized = false;
      state.rerunConfirmation = null;
    } else {
      state.rerunConfirmation = null;
      state.detailError = describeApiError(error, "Rerun failed");
    }
  } finally {
    state.isRerunning = false;
    render();
  }
}
/*__RERUN_END__*/

function openResetConfirmation() {
  if (!state.capabilities?.reset) return;
  state.resetConfirmationOpen = true;
  render();
}

async function confirmReset() {
  if (!state.resetConfirmationOpen || state.isResetting) return;
  state.isResetting = true;
  render();
  try {
    const payload = await apiRequest("/v1/reset", { method: "POST", body: JSON.stringify({}) });
    const deleted = Number(payload?.deleted_count || 0);
    showToast(`Environment reset · ${deleted} execution${deleted === 1 ? "" : "s"} deleted`);
    state.resetConfirmationOpen = false;
    state.activeExecution = null;
    state.view = "create";
    await loadExecutions({ page: 1, renderOnUpdate: false });
  } catch (error) {
    if (error instanceof ApiError && error.status === 401) {
      state.authorized = false;
      state.resetConfirmationOpen = false;
    } else {
      state.resetConfirmationOpen = false;
      state.historyError = describeApiError(error, "Reset failed");
    }
  } finally {
    state.isResetting = false;
    render();
  }
}

async function runReconcile() {
  const execution = state.activeExecution;
  if (!execution || state.isReconciling) return;
  const unknownOperations = (Array.isArray(execution.delivery_ledger) ? execution.delivery_ledger : []).filter(
    (item) => String(item?.status || "") === "outcome_unknown"
  );
  if (!unknownOperations.length) {
    state.reconcileError = "No outcome-unknown delivery operations to reconcile.";
    render();
    return;
  }
  state.isReconciling = true;
  state.reconcileError = "";
  render();
  try {
    const payload = await apiRequest(`/v1/executions/${encodeURIComponent(execution.execution_id)}/reconcile`, {
      method: "POST",
      body: JSON.stringify({ operations: unknownOperations }),
    });
    const updated = payload.execution && typeof payload.execution === "object" ? payload.execution : null;
    if (updated) state.activeExecution = updated;
    showToast("Execution reconciled");
    await loadExecutions({ renderOnUpdate: false });
  } catch (error) {
    if (error instanceof ApiError && error.status === 401) {
      state.authorized = false;
    } else {
      state.reconcileError = describeApiError(error, "Reconcile failed");
    }
  } finally {
    state.isReconciling = false;
    render();
  }
}

function statusMeta(status) {
  const key = String(status || "").trim();
  return STATUS_META[key] || { label: key || "Unknown", className: "status-badge--automation" };
}

function renderStatusBadge(status) {
  const meta = statusMeta(status);
  return `<span class="status-badge ${meta.className}">${escapeHtml(meta.label)}</span>`;
}

function routeParts(execution) {
  const route = execution?.route_result?.route;
  if (!route || typeof route !== "object") return { primary: "", secondary: "" };
  return {
    primary: String(route.category || route.scope_label || "").trim(),
    secondary: String(route.subcategory || route.execution_action || "").trim(),
  };
}

function renderRouteBadges(execution) {
  const { primary, secondary } = routeParts(execution);
  if (!primary && !secondary) return "";
  return `
    <span class="route-labels" aria-label="Route classification">
      ${primary ? `<span class="route-label route-label--primary">${escapeHtml(primary)}</span>` : ""}
      ${secondary ? `<span class="route-label route-label--secondary">${escapeHtml(secondary)}</span>` : ""}
    </span>
  `;
}

function visibilityLabel(value) {
  const raw = String(value || "").trim().toLowerCase();
  if (raw === "internal") return "Internal comment";
  if (raw === "external") return "External comment";
  return "";
}

function executionTicketId(execution) {
  const fromRequest = String(execution?.request?.zendesk_ticket_id || "").trim();
  if (fromRequest) return fromRequest;
  const ledger = Array.isArray(execution?.delivery_ledger) ? execution.delivery_ledger : [];
  const fromLedger = String((ledger.find((item) => item?.ticket_id) || {}).ticket_id || "").trim();
  return fromLedger;
}

function totalPages() {
  return Math.max(1, Math.ceil(state.total / PAGE_SIZE));
}

function paginationPages(currentPage, total) {
  const pages = [];
  const add = (value) => {
    if (!pages.includes(value)) pages.push(value);
  };
  add(1);
  add(currentPage - 1);
  add(currentPage);
  add(currentPage + 1);
  add(total);
  return pages
    .filter((value) => value >= 1 && value <= total)
    .sort((a, b) => a - b)
    .reduce((items, value, index, source) => {
      if (index > 0 && value - source[index - 1] > 1) items.push("ellipsis");
      items.push(value);
      return items;
    }, []);
}

function renderPaginationControls() {
  const total = totalPages();
  if (total <= 1) return "";
  const currentPage = Math.min(Math.max(1, state.page), total);
  return `
    <nav class="history-pagination" aria-label="Execution pages">
      <button class="pagination-button" type="button" data-action="goto-page" data-value="${currentPage - 1}" ${currentPage <= 1 ? "disabled" : ""} aria-label="Previous page">‹</button>
      ${paginationPages(currentPage, total)
        .map((item) =>
          item === "ellipsis"
            ? '<span class="pagination-ellipsis">…</span>'
            : `<button class="pagination-button ${item === currentPage ? "pagination-button--active" : ""}" type="button" data-action="goto-page" data-value="${item}">${item}</button>`
        )
        .join("")}
      <button class="pagination-button" type="button" data-action="goto-page" data-value="${currentPage + 1}" ${currentPage >= total ? "disabled" : ""} aria-label="Next page">›</button>
    </nav>
  `;
}

function renderCaseSearch() {
  return `
    <form class="account-case-search" data-form="case-search" role="search">
      <input
        class="input"
        name="caseSearch"
        value="${escapeHtml(state.caseSearchQuery)}"
        placeholder="Case ID"
        aria-label="Search execution by Case ID"
        autocomplete="off"
      />
      <button class="icon-button" type="submit" aria-label="Search"><span class="material-symbols-outlined">search</span></button>
    </form>
    ${state.caseSearchError ? `<p class="account-login-error" role="alert">${escapeHtml(state.caseSearchError)}</p>` : ""}
  `;
}

function renderFilterControls() {
  const counts = state.statusCounts || {};
  const allCount = Object.values(counts).reduce((sum, value) => sum + Number(value || 0), 0);
  const chips = [{ id: "all", label: "All", count: allCount }].concat(
    STATUS_ORDER.map((status) => ({ id: status, label: statusMeta(status).label, count: Number(counts[status] || 0) }))
  );
  return `
    <div class="route-filter" role="group" aria-label="Execution status filters">
      ${chips
        .map(
          (chip) => `
            <button
              class="filter-chip ${state.statusFilter === chip.id ? "filter-chip--active" : ""}"
              type="button"
              data-action="set-status-filter"
              data-value="${escapeHtml(chip.id)}"
              aria-pressed="${state.statusFilter === chip.id}"
            >${escapeHtml(chip.label)}<span class="filter-count">${chip.count}</span></button>
          `
        )
        .join("")}
    </div>
  `;
}

function renderHistorySidebar() {
  if (state.historyLoading && !state.executions.length) {
    return `
      ${renderCaseSearch()}
      ${renderFilterControls()}
      <div class="history-loading" role="status" aria-label="Loading executions">
        ${Array.from({ length: 5 }, () => '<span class="loading-line"></span>').join("")}
      </div>
    `;
  }
  if (!state.executions.length) {
    return `
      ${renderCaseSearch()}
      ${renderFilterControls()}
      <div class="history-empty">
        <span class="material-symbols-outlined">receipt_long</span>
        <p>No executions yet</p>
      </div>
    `;
  }
  const activeId = String(state.activeExecution?.execution_id || "");
  return `
    ${renderCaseSearch()}
    ${renderFilterControls()}
    <div class="history-section-title">${state.statusFilter === "all" ? "All" : statusMeta(state.statusFilter).label} executions (${state.total})</div>
    ${state.executions
      .map(
        (execution) => `
        <button class="history-item ${activeId && activeId === String(execution.execution_id || "") ? "history-item--active" : ""}" type="button" data-action="open-execution" data-id="${escapeHtml(execution.execution_id)}">
          <div class="history-item-header">
            <div class="history-item-identity">
              <span class="history-ticket-number">${escapeHtml(execution.case_id || "")}</span>
              <strong>${escapeHtml(execution.request?.subject || "")}</strong>
            </div>
          </div>
          ${renderRouteBadges(execution)}
          <div class="history-item-meta">
            ${renderStatusBadge(execution.status)}
            <span class="history-time">${escapeHtml(formatTimestamp(execution.created_at))}</span>
          </div>
        </button>
      `
      )
      .join("")}
    ${renderPaginationControls()}
  `;
}

function renderCapabilityLine() {
  const caps = state.capabilities;
  if (!caps) return "Capabilities unavailable";
  const parts = [];
/*__RERUN_START__*/
  parts.push(`rerun ${caps.rerun ? "enabled" : "disabled"}`);
/*__RERUN_END__*/
  parts.push(`reset ${caps.reset ? "enabled" : "disabled"}`);
  const visibility = Array.isArray(caps.comment_visibility) && caps.comment_visibility.length
    ? caps.comment_visibility.join(" / ")
    : "—";
  parts.push(`visibility ${visibility}`);
  return parts.join(" · ");
}

function renderTokenGate() {
  return `
    <section class="account-login-page">
      <header class="account-login-header">
        <div class="account-login-brand" aria-label="${escapeHtml(ENV.brandTitle)}">
          <span class="account-login-brand-icon material-symbols-outlined" aria-hidden="true">smart_toy</span>
          <strong>${escapeHtml(ENV.brandTitle)}</strong>
        </div>
      </header>
      <main class="account-login-main">
        <div class="account-login-content">
          <header class="account-login-heading">
            <h1>Execution access</h1>
            <p>${escapeHtml(ENV.loginTagline)}</p>
          </header>
          <section class="account-login-card" aria-label="Execution token">
            <form class="account-login-form" data-form="token">
              <label class="account-login-field">
                <span>Execution token</span>
                <span class="account-login-input-wrap">
                  <span class="material-symbols-outlined" aria-hidden="true">key</span>
                  <input name="execution_token" type="password" autocomplete="off" placeholder="Automation execution bearer token" required />
                </span>
              </label>
              <p class="account-login-error" data-token-error role="alert">${escapeHtml(state.tokenError)}</p>
              <button class="account-login-submit" type="submit" ${state.tokenChecking ? "disabled" : ""}>
                <span>${state.tokenChecking ? "Connecting..." : "Open console"}</span>
                <span class="material-symbols-outlined" aria-hidden="true">login</span>
              </button>
            </form>
          </section>
          <div class="account-login-orbit" aria-hidden="true">
            <span class="material-symbols-outlined">data_usage</span>
          </div>
        </div>
      </main>
      <footer class="account-login-footer">
        <strong>&copy; 2026 SupportPortal. Secure Automation Workspace.</strong>
        <nav aria-label="Automation resources">
          <a href="https://status.agora.io/" target="_blank" rel="noopener noreferrer">System Status</a>
        </nav>
      </footer>
    </section>
  `;
}

function renderVisibilityField() {
  if (ENV.visibilityMode === "none") return "";
  if (ENV.visibilityMode === "fixed") {
    return `
      <label class="field">
        <span class="field-label">Comment visibility</span>
        <input class="input" value="${escapeHtml(visibilityLabel(ENV.fixedVisibility))}" disabled />
        <span class="form-desc">Preproduction policy forces internal comments.</span>
      </label>
    `;
  }
  return `
    <label class="field">
      <span class="field-label">Comment visibility</span>
      <select class="input" name="visibility" required>
        <option value="" ${state.form.visibility ? "" : "selected"} disabled>Select visibility</option>
        <option value="internal" ${state.form.visibility === "internal" ? "selected" : ""}>Internal comment</option>
        <option value="external" ${state.form.visibility === "external" ? "selected" : ""}>External comment</option>
      </select>
      <span class="form-desc">Production requires an explicit internal or external choice.</span>
    </label>
  `;
}

function renderCreateForm() {
  return `
    <div class="panel form-stack">
      <div class="form-header">
        <h3>New execution</h3>
        <p class="form-desc">Submit an execution for routing and environment-policy processing.</p>
      </div>
      <form data-form="create">
        <label class="field">
          <span class="field-label">Case ID</span>
          <input class="input" name="caseId" value="${escapeHtml(state.form.caseId)}" placeholder="${escapeHtml(ENV.casePlaceholder)}" autocomplete="off" required />
        </label>
        ${ENV.requiresTicket ? `
        <label class="field">
          <span class="field-label">Zendesk ticket ID</span>
          <input class="input" name="ticketId" value="${escapeHtml(state.form.ticketId)}" placeholder="123456" autocomplete="off" required />
          ${ENV.ticketHint ? `<span class="form-desc">${escapeHtml(ENV.ticketHint)}.</span>` : ""}
        </label>
        ` : ""}
        <label class="field">
          <span class="field-label">Title</span>
          <input class="input" name="title" value="${escapeHtml(state.form.title)}" placeholder="Case subject" autocomplete="off" />
        </label>
        <label class="field">
          <span class="field-label">Customer email</span>
          <input class="input" name="customerEmail" value="${escapeHtml(state.form.customerEmail)}" placeholder="customer@example.com" autocomplete="off" />
        </label>
        ${renderVisibilityField()}
        <label class="field">
          <span class="field-label">Question</span>
          <textarea class="textarea" name="question" rows="6" placeholder="Customer request" required>${escapeHtml(state.form.question)}</textarea>
        </label>
        <div class="actions">
          <button class="primary-button" type="submit" ${state.isSubmitting ? "disabled" : ""}>
            <span class="material-symbols-outlined">send</span>
            ${state.isSubmitting ? "Preparing..." : escapeHtml(ENV.submitLabel)}
          </button>
        </div>
      </form>
      ${
        state.submitError
          ? `<div class="error-banner" role="alert"><span class="material-symbols-outlined">error</span><span>${escapeHtml(state.submitError)}${
              state.submitFollowUpExecutionId
                ? ` <button class="ghost-button" type="button" data-action="open-execution" data-id="${escapeHtml(state.submitFollowUpExecutionId)}">Open execution</button>`
                : ""
            }</span></div>`
          : ""
      }
    </div>
  `;
}

function ledgerStatusLabel(status) {
  const raw = String(status || "").trim();
  const labels = { pending: "Pending", completed: "Completed", outcome_unknown: "Outcome unknown", failed: "Failed" };
  return labels[raw] || raw || "Unknown";
}

function renderDeliveryLedger(execution) {
  const ledger = Array.isArray(execution.delivery_ledger) ? execution.delivery_ledger : [];
  if (!ledger.length) return "";
  const operationLabels = { take_ownership: "Take ownership", comment: "Zendesk comment", status: "Ticket status" };
  return `
    <div class="detail-section">
      <div class="detail-section-title">Zendesk delivery ledger</div>
      ${ledger
        .map((item) => {
          const operation = String(item.operation || "");
          const parts = [];
          if (item.ticket_id) parts.push(`ticket #${escapeHtml(item.ticket_id)}`);
          if (item.visibility) parts.push(escapeHtml(visibilityLabel(item.visibility) || item.visibility));
          if (operation === "status" && item.target_status) parts.push(`target ${escapeHtml(item.target_status)}`);
          return `
            <div class="meta-row meta-row--inline">
              <span class="meta-label">${escapeHtml(operationLabels[operation] || operation)}</span>
              <span class="meta-value">${parts.length ? `${parts.join(" · ")} · ` : ""}${escapeHtml(ledgerStatusLabel(item.status))}</span>
            </div>
          `;
        })
        .join("")}
    </div>
  `;
}

function renderConversation(execution) {
  const request = execution.request && typeof execution.request === "object" ? execution.request : {};
  const actionPlan = execution.route_result?.action_plan;
  const replyBody = String(actionPlan?.reply_body || "").trim();
  const ledger = Array.isArray(execution.delivery_ledger) ? execution.delivery_ledger : [];
  const commentOperation = ledger.find((item) => String(item?.operation || "") === "comment");
  let deliveryState = "Not delivered (environment policy)";
  if (commentOperation) {
    deliveryState = `Zendesk delivery: ${ledgerStatusLabel(commentOperation.status)}`;
  } else if (String(execution.status || "") === "human_review") {
    deliveryState = "Held for human review";
  }
  return `
    <div class="message-thread">
      <div class="detail-section-title">Conversation</div>
      <div class="msg-row msg-row--customer">
        <div class="msg-bubble msg-bubble--customer">
          <div class="msg-header"><span class="msg-label">CUSTOMER REQUEST</span><time datetime="${escapeHtml(execution.created_at || "")}">${escapeHtml(formatTimestamp(execution.created_at))}</time></div>
          <div class="msg-content">${renderMarkdownMessage(request.question || "")}</div>
        </div>
      </div>
      ${
        replyBody
          ? `
        <div class="msg-row msg-row--assistant">
          <div class="msg-bubble msg-bubble--assistant">
            <div class="msg-header"><span class="msg-label">AI REPLY</span><time datetime="${escapeHtml(execution.updated_at || "")}">${escapeHtml(formatTimestamp(execution.updated_at))}</time></div>
            <div class="msg-content">${renderMarkdownMessage(replyBody)}</div>
            <p class="ai-reply-state">${escapeHtml(deliveryState)}</p>
          </div>
        </div>
      `
          : `<p class="conversation-empty">No AI reply was generated for this execution.</p>`
      }
    </div>
  `;
}

function renderDetailView() {
  const execution = state.activeExecution;
  if (state.detailLoading) {
    return `
      <div class="panel detail-stack">
        <div class="detail-loading" role="status" aria-label="Loading execution">
          ${Array.from({ length: 6 }, () => '<span class="loading-line"></span>').join("")}
        </div>
      </div>
    `;
  }
  if (!execution) {
    return `
      <div class="panel detail-stack">
        <div class="error-banner" role="alert"><span class="material-symbols-outlined">error</span>${escapeHtml(state.detailError || "Execution not found")}</div>
      </div>
    `;
  }
  const request = execution.request && typeof execution.request === "object" ? execution.request : {};
  const automation = execution.route_result?.automation;
  const actionPlan = execution.route_result?.action_plan;
  const ticketId = executionTicketId(execution);
  const visibility = visibilityLabel(execution.policy?.comment_visibility);
/*__RERUN_START__*/
  const canRerun = Boolean(state.capabilities?.rerun);
/*__RERUN_END__*/
  const needsReconcile = String(execution.status || "") === "outcome_unknown";
  const { primary, secondary } = routeParts(execution);
  return `
    <div class="panel detail-stack">
      <header class="detail-header">
        <div class="detail-ticket-number">${escapeHtml(execution.case_id || "")}</div>
        <h3 class="detail-title">${escapeHtml(request.subject || "Untitled execution")}</h3>
        <div class="route-labels">
          ${renderStatusBadge(execution.status)}
          ${renderRouteBadges(execution)}
          ${visibility ? `<span class="route-label">${escapeHtml(visibility)}</span>` : ""}
          ${execution.reconciled ? '<span class="route-label">Reconciled</span>' : ""}
        </div>
        <div class="actions">
/*__RERUN_START__*/
          ${canRerun ? `<button class="danger-button" type="button" data-action="open-rerun-confirmation" ${state.isRerunning ? "disabled" : ""}><span class="material-symbols-outlined">sync</span>Rerun this execution</button>` : ""}
/*__RERUN_END__*/
          ${needsReconcile ? `<button class="primary-button primary-button--small" type="button" data-action="run-reconcile" ${state.isReconciling ? "disabled" : ""}><span class="material-symbols-outlined">fact_check</span>${state.isReconciling ? "Reconciling..." : "Reconcile"}</button>` : ""}
        </div>
      </header>
      ${execution.failure_code ? `<div class="error-banner" aria-live="polite"><span class="material-symbols-outlined">error</span>Failure code: ${escapeHtml(execution.failure_code)}</div>` : ""}
      ${state.reconcileError ? `<div class="error-banner" role="alert"><span class="material-symbols-outlined">error</span>${escapeHtml(state.reconcileError)}</div>` : ""}
      <div class="meta-grid">
        <div class="meta-row"><span class="meta-label">Execution ID</span><span class="meta-value">${escapeHtml(execution.execution_id || "")}</span></div>
        <div class="meta-row"><span class="meta-label">Request ID</span><span class="meta-value">${escapeHtml(execution.request_id || "")}</span></div>
        <div class="meta-row"><span class="meta-label">Case ID</span><span class="meta-value">${escapeHtml(execution.case_id || "")}</span></div>
        ${ticketId ? `<div class="meta-row"><span class="meta-label">Ticket #</span><span class="meta-value">${escapeHtml(ticketId)}</span></div>` : ""}
        <div class="meta-row"><span class="meta-label">Source</span><span class="meta-value">${escapeHtml(`automation-${ENV.environment}`)}</span></div>
        <div class="meta-row"><span class="meta-label">Status</span><span class="meta-value">${renderStatusBadge(execution.status)}</span></div>
        <div class="meta-row meta-row--route-result"><span class="meta-label">Route result</span><span class="meta-value meta-row--route-result-value">${escapeHtml([primary, secondary].filter(Boolean).join(" / ") || "manual review")}</span></div>
        <div class="meta-row"><span class="meta-label">Automation eligible</span><span class="meta-value">${escapeHtml(String(automation?.eligible ?? "unknown"))}</span></div>
        <div class="meta-row"><span class="meta-label">Preparation status</span><span class="meta-value">${escapeHtml(String(actionPlan?.preparation_status || "—"))}</span></div>
/*__RERUN_START__*/
        ${execution.rerun_of_execution_id ? `<div class="meta-row"><span class="meta-label">Rerun of</span><span class="meta-value"><button class="ghost-button" type="button" data-action="open-execution" data-id="${escapeHtml(execution.rerun_of_execution_id)}">${escapeHtml(execution.rerun_of_execution_id)}</button></span></div>` : ""}
/*__RERUN_END__*/
        <div class="meta-row"><span class="meta-label">Created</span><span class="meta-value"><time datetime="${escapeHtml(execution.created_at || "")}">${escapeHtml(formatTimestamp(execution.created_at))}</time></span></div>
        <div class="meta-row"><span class="meta-label">Updated</span><span class="meta-value"><time datetime="${escapeHtml(execution.updated_at || "")}">${escapeHtml(formatTimestamp(execution.updated_at))}</time></span></div>
      </div>
      ${
        request.question
          ? `
        <div class="detail-section">
          <div class="detail-section-title">Request</div>
          ${request.customer_email ? `<div class="meta-row"><span class="meta-label">Customer email</span><span class="meta-value">${escapeHtml(request.customer_email)}</span></div>` : ""}
          <div class="msg-content">${renderMarkdownMessage(request.question)}</div>
        </div>
      `
          : ""
      }
      ${renderConversation(execution)}
      ${renderDeliveryLedger(execution)}
      <details class="detail-section">
        <summary class="detail-section-title">Raw execution JSON</summary>
        <pre style="white-space: pre-wrap; word-break: break-word; font-size: 12px; margin: 8px 0 0; overflow-x: auto;">${escapeHtml(JSON.stringify(execution, null, 2))}</pre>
      </details>
    </div>
  `;
}

/*__RERUN_START__*/
function renderRerunConfirmation() {
  if (!state.rerunConfirmation) return "";
  const snapshot = state.rerunConfirmation;
  return `
    <div class="reroute-modal-backdrop" data-action="close-rerun-confirmation">
      <section class="reroute-modal" role="dialog" aria-modal="true" aria-labelledby="rerun-dialog-title">
        <div class="reroute-modal__heading">
          <span class="material-symbols-outlined" aria-hidden="true">warning</span>
          <div>
            <h2 id="rerun-dialog-title">Rerun case ${escapeHtml(snapshot.caseId)}?</h2>
            <p>This creates a new execution from the persisted original request.</p>
          </div>
        </div>
        <ul>
          <li>Case ID is frozen to <strong>${escapeHtml(snapshot.caseId)}</strong>${snapshot.ticketId ? ` and Zendesk ticket to <strong>#${escapeHtml(snapshot.ticketId)}</strong>` : ""}.</li>
          <li>The original execution record stays unchanged.</li>
          <li>The new execution runs the full route and environment-policy pipeline again.</li>
          ${ENV.rerunWritesZendesk ? "<li>An eligible route will write a new internal Zendesk comment on the ticket.</li>" : ""}
        </ul>
        <div class="reroute-modal__actions">
          <button class="ghost-button" type="button" data-action="close-rerun-confirmation">Cancel</button>
          <button class="danger-button" type="button" data-action="confirm-rerun" ${state.isRerunning ? "disabled" : ""}>
            ${state.isRerunning ? "Rerunning..." : "Rerun execution"}
          </button>
        </div>
      </section>
    </div>
  `;
}
/*__RERUN_END__*/

function renderResetConfirmation() {
  if (!state.resetConfirmationOpen) return "";
  return `
    <div class="reroute-modal-backdrop" data-action="close-reset-confirmation">
      <section class="reroute-modal" role="dialog" aria-modal="true" aria-labelledby="reset-dialog-title">
        <div class="reroute-modal__heading">
          <span class="material-symbols-outlined" aria-hidden="true">delete_forever</span>
          <div>
            <h2 id="reset-dialog-title">Reset ${escapeHtml(ENV.environment)} environment?</h2>
            <p>This permanently deletes every execution record in this environment's ledger.</p>
          </div>
        </div>
        <ul>
          <li>All ${state.total} execution record${state.total === 1 ? "" : "s"} in ${escapeHtml(ENV.environment)} will be deleted.</li>
          <li>Zendesk tickets are not modified by this reset.</li>
          <li>This action cannot be undone.</li>
        </ul>
        <div class="reroute-modal__actions">
          <button class="ghost-button" type="button" data-action="close-reset-confirmation">Cancel</button>
          <button class="danger-button" type="button" data-action="confirm-reset" ${state.isResetting ? "disabled" : ""}>
            ${state.isResetting ? "Resetting..." : "Reset environment"}
          </button>
        </div>
      </section>
    </div>
  `;
}

function render() {
  if (!state.authorized) {
    appRoot.innerHTML = renderTokenGate();
    return;
  }
  const showReset = Boolean(state.capabilities?.reset);
  appRoot.innerHTML = `
    <main class="account-shell">
      <aside class="side-panel">
        <div class="brand">
          <div class="brand-mark"><span class="material-symbols-outlined">support_agent</span></div>
          <div>
            <div class="eyebrow">${escapeHtml(ENV.brandEyebrow)}</div>
            <h1>${escapeHtml(ENV.brandTitle)}</h1>
          </div>
        </div>
        <div class="side-actions">
          <button class="primary-button primary-button--small" type="button" data-action="new-execution">
            <span class="material-symbols-outlined">add</span>
            New execution
          </button>
          ${showReset ? `
          <button class="reroute-button" type="button" data-action="open-reset-confirmation" ${state.isResetting ? "disabled" : ""}>
            <span class="material-symbols-outlined">delete_sweep</span>
            Reset environment
          </button>
          ` : ""}
          <button class="ghost-button account-signout-button" type="button" data-action="disconnect-token">
            <span class="material-symbols-outlined" aria-hidden="true">logout</span>
            Disconnect
          </button>
        </div>
        <div class="account-session">
          <span class="material-symbols-outlined" aria-hidden="true">key</span>
          <span><strong>Execution token</strong><small>${escapeHtml(renderCapabilityLine())}</small></span>
          <button class="icon-button" type="button" data-action="disconnect-token" aria-label="Disconnect token"><span class="material-symbols-outlined">logout</span></button>
        </div>
        ${state.historyError ? `<div class="error-banner" role="alert"><span class="material-symbols-outlined">error</span>${escapeHtml(state.historyError)}</div>` : ""}
        <div class="history-stack" id="history-list">
          ${renderHistorySidebar()}
        </div>
      </aside>
      <section class="workbench">
        <div class="workbench-header">
          <div>
            <span class="pill"><span class="material-symbols-outlined">route</span>${escapeHtml(ENV.workbenchPill)}</span>
            <h2>${state.view === "create" ? "Create and run an execution" : "Execution detail"}</h2>
          </div>
          ${state.view === "detail" ? `<button class="ghost-button" type="button" data-action="back-to-create">Back to create</button>` : ""}
        </div>
        <div class="intake-grid">
          ${state.view === "create" ? renderCreateForm() : ""}
          ${state.view === "detail" ? renderDetailView() : ""}
        </div>
      </section>
    </main>
/*__RERUN_START__*/
    ${renderRerunConfirmation()}
/*__RERUN_END__*/
    ${renderResetConfirmation()}
  `;
}

function handleAction(action, target) {
  const id = String(target?.dataset?.id || "").trim();
  const value = String(target?.dataset?.value || "").trim();
  switch (action) {
    case "new-execution":
      state.view = "create";
      render();
      break;
    case "disconnect-token":
      disconnectToken();
      break;
    case "set-status-filter":
      state.statusFilter = value || "all";
      state.page = 1;
      void loadExecutions({ page: 1 });
      break;
    case "goto-page": {
      const page = Math.min(Math.max(1, Number(value) || 1), totalPages());
      if (page !== state.page) void loadExecutions({ page });
      break;
    }
    case "open-execution":
      void openExecution(id);
      break;
    case "back-to-create":
      state.view = "create";
      render();
      break;
/*__RERUN_START__*/
    case "open-rerun-confirmation":
      openRerunConfirmation();
      break;
    case "close-rerun-confirmation":
      state.rerunConfirmation = null;
      render();
      break;
    case "confirm-rerun":
      void confirmRerun();
      break;
/*__RERUN_END__*/
    case "open-reset-confirmation":
      openResetConfirmation();
      break;
    case "close-reset-confirmation":
      state.resetConfirmationOpen = false;
      render();
      break;
    case "confirm-reset":
      void confirmReset();
      break;
    case "run-reconcile":
      void runReconcile();
      break;
    default:
      break;
  }
}

appRoot.addEventListener("click", (event) => {
  const target = event.target instanceof Element ? event.target.closest("[data-action]") : null;
  if (!target) return;
  event.preventDefault();
  handleAction(String(target.dataset.action || ""), target);
});

appRoot.addEventListener("submit", (event) => {
  const form = event.target instanceof HTMLFormElement ? event.target : null;
  if (!form) return;
  const kind = String(form.dataset.form || "");
  if (kind === "token") {
    void connectToken(event);
  } else if (kind === "create") {
    void submitExecution(event);
  } else if (kind === "case-search") {
    void searchByCaseId(event);
  }
});

appRoot.addEventListener("input", (event) => {
  const input = event.target instanceof HTMLInputElement || event.target instanceof HTMLTextAreaElement ? event.target : null;
  if (!input || !input.name) return;
  if (input.name === "caseSearch") {
    state.caseSearchQuery = String(input.value || "");
    state.caseSearchError = "";
    return;
  }
  if (Object.prototype.hasOwnProperty.call(state.form, input.name)) {
    state.form[input.name] = String(input.value || "");
  }
});

appRoot.addEventListener("change", (event) => {
  const select = event.target instanceof HTMLSelectElement ? event.target : null;
  if (!select || !select.name) return;
  if (Object.prototype.hasOwnProperty.call(state.form, select.name)) {
    state.form[select.name] = String(select.value || "");
  }
});

document.addEventListener("keydown", (event) => {
  if (event.key !== "Escape") return;
/*__RERUN_START__*/
  if (state.rerunConfirmation) {
    state.rerunConfirmation = null;
    render();
    return;
  }
/*__RERUN_END__*/
  if (state.resetConfirmationOpen) {
    state.resetConfirmationOpen = false;
    render();
  }
});

document.addEventListener("visibilitychange", () => {
  if (document.hidden || !state.authorized) return;
  void loadExecutions();
  if (state.view === "detail" && state.activeExecution?.execution_id) {
    const executionId = String(state.activeExecution.execution_id);
    void apiRequest(`/v1/executions/${encodeURIComponent(executionId)}`)
      .then((payload) => {
        if (payload?.execution && state.view === "detail" && String(state.activeExecution?.execution_id || "") === executionId) {
          state.activeExecution = payload.execution;
          render();
        }
      })
      .catch(() => {});
  }
});

async function init() {
  await loadCapabilities();
  state.authorized = Boolean(localStorage.getItem(ENV.tokenKey));
  render();
  if (state.authorized) {
    await loadExecutions();
  }
}

void init();
