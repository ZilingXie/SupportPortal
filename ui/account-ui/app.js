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
const serializeRichComposerHtmlToMarkdown =
  SharedComposer.serializeRichComposerHtmlToMarkdown || ((value) => String(value || ""));
const buildDefaultComposerToolbarState =
  SharedComposer.buildDefaultComposerToolbarState ||
  (() => ({
    bold: false,
    italic: false,
    list: false,
    codeBlock: false,
  }));
const renderSharedComposerFormattingToolbarButtons =
  SharedComposer.renderComposerFormattingToolbarButtons || (() => "");
const applySharedComposerToolbarStateToButtons =
  SharedComposer.applyComposerToolbarStateToButtons || (() => {});

const PAGE_SIZE = 30;

const state = {
  view: "create",
  title: "",
  question: "",
  customerEmail: "",
  source: "manual",
  isSubmitting: false,
  history: [],
  currentPage: 1,
  pagination: {
    page: 1,
    pageSize: PAGE_SIZE,
    total: 0,
    totalPages: 1,
    hasMore: false,
  },
  activeItem: null,
  error: "",
  composerToolbarState: buildDefaultComposerToolbarState(),
  statusFilter: "unreviewed",
  replyMessage: "",
  isSubmittingReply: false,
  replyError: "",
  correctionScope: "",
  correctionAction: "",
  isSubmittingCorrection: false,
  correctionError: "",
  routeErrorSummary: null,
  routeCorrectionExpanded: false,
  isSubmittingReview: false,
  reviewError: "",
};

let composerRuntime = null;
let isFetchingRouteErrorSummary = false;
let replyPollTimer = null;

const ACTIVE_AI_REPLY_STATUSES = new Set(["queued", "preparing", "scheduled", "publishing"]);
const ROUTE_LABEL_FILTERS = new Set([
  "human_review",
  "agora_technical",
  "agora_non_technical",
  "non_agora",
]);
const ROUTE_LABEL_FILTER_MATCHES = {
  human_review: "Human Review",
  agora_technical: "Agora Technical",
  agora_non_technical: "Agora Non-technical",
  non_agora: "Non-Agora",
};

const ROUTE_TUPLE_OPTIONS = [
  { scope: "ticket_resolution", action: "resolve_ticket", label: "Conversation / Resolve" },
  { scope: "conversation", action: "follow_up", label: "Conversation / Follow-up" },
  { scope: "conversation", action: "human_review_required", label: "Conversation / Human Review" },
  { scope: "non_agora", action: "human_review_required", label: "Support Request / Non-Agora" },
  { scope: "agora_technical", action: "rag", label: "Support Request / Agora Technical" },
  { scope: "agora_non_technical", action: "web_search", label: "Support Request / Agora Non-technical" },
  { scope: "human_review", action: "human_review_required", label: "Support Request / Human Review" },
  { scope: "unclear", action: "human_review_required", label: "Unclear / Human Review" },
  { scope: "automation", action: "account_verification", label: "Automation / Account verification" },
  { scope: "automation", action: "account_suspension", label: "Automation / Account suspension" },
  { scope: "automation", action: "detailed_invoice", label: "Automation / Detailed invoice" },
  { scope: "automation", action: "enablement", label: "Automation / Enablement" },
];

const DEFAULT_ROUTE_TUPLE_SELECT_VALUE = "scope|action";

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function statusLabel(status) {
  const labels = {
    automation: "Automation",
    automated: "Automated",
    needs_more_info: "Needs more info",
    not_automated: "Not automated",
  };
  return labels[status] || "Not automated";
}

function routeClass(route) {
  if (route === "detailed_invoice") return "route-invoice";
  if (route === "account_suspension") return "route-suspension";
  return "route-other";
}

function classificationLabels(item) {
  const primary = String(item?.primary_label || "").trim();
  const secondary = String(item?.secondary_label || "").trim();
  return { primary, secondary };
}

function renderClassificationBadges(item) {
  const { primary, secondary } = classificationLabels(item);
  if (!primary && !secondary) return "";
  return `
    <span class="route-labels" aria-label="Route classification">
      ${primary ? `<span class="route-label route-label--primary">${escapeHtml(primary)}</span>` : ""}
      ${secondary ? `<span class="route-label route-label--secondary">${escapeHtml(secondary)}</span>` : ""}
    </span>
  `;
}

// Build the readable "Route result" string: scope_label / route_family / route.
// Falls back to just `route` for legacy tickets missing the new routing fields,
// and to "manual review" when nothing is present.
function routeResultLabel(item) {
  const { primary, secondary } = classificationLabels(item);
  if (primary || secondary) return [primary, secondary].filter(Boolean).join(" / ");
  const parts = [
    item.category || item.scope_label,
    item.subcategory,
    item.route_family,
    item.subcategory ? null : item.execution_action || item.route,
  ]
    .map((value) => String(value || "").trim())
    .filter((value) => value.length > 0);
  return parts.length ? parts.join(" / ") : "manual review";
}

function internalEmailResponseLinkStatus(item) {
  const payload = item && typeof item.internal_email_payload === "object" ? item.internal_email_payload : null;
  const body = String(payload?.body || "");
  return body.includes("/response?token=") ? "Generated" : "Not generated";
}

function safeSourceLink(source) {
  let link = "";
  if (source && typeof source === "object") {
    link = String(source.Link || source.link || source.url || "");
  } else if (typeof source === "string") {
    link = source;
  }
  link = link.trim();
  if (!link) return "";
  try {
    const parsed = new URL(link);
    if (parsed.protocol === "http:" || parsed.protocol === "https:") {
      return link;
    }
  } catch {}
  return "";
}

function normalizeSource(source) {
  if (safeSourceLink(source)) {
    return "api";
  }
  const normalized = String(source || "").trim().toLowerCase().replaceAll("_", "-");
  if (normalized === "api" || normalized === "http" || normalized === "account-http" || normalized === "/account-http") {
    return "api";
  }
  return "manual";
}

function sourceLabel(source) {
  if (normalizeSource(source) === "api") return "API";
  return "Manual";
}

function sourceClass(source) {
  if (normalizeSource(source) === "api") return "source-api";
  return "source-manual";
}

function zendeskTicketId(link) {
  try {
    const parsed = new URL(link);
    const host = parsed.hostname.toLowerCase();
    if (host === "zendesk.com" || host.endsWith(".zendesk.com")) {
      const m = parsed.pathname.match(/^\/(?:agent|api\/v2)\/tickets\/(\d+)(?:\.json)?$/);
      if (m) return m[1];
    }
  } catch {
    return "";
  }
  return "";
}

function zendeskTicketLabel(link) {
  const ticketId = zendeskTicketId(link);
  return ticketId ? "zen#" + ticketId : "";
}

function accountTicketNumber(item) {
  return zendeskTicketId(safeSourceLink(item?.source))
    || String(item?.ticket_id || item?.client_ticket_id || "").trim();
}

function renderSourceValue(source) {
  const link = safeSourceLink(source);
  if (link) {
    const label = zendeskTicketLabel(link) || "Link";
    return `<a class="source-link" href="${escapeHtml(link)}" target="_blank" rel="noopener noreferrer">${escapeHtml(label)}</a>`;
  }
  return `<span class="source-badge ${sourceClass(source)}">${escapeHtml(sourceLabel(source))}</span>`;
}

function showToast(message) {
  if (!toastRoot) return;
  toastRoot.innerHTML = `<div class="toast">${escapeHtml(message)}</div>`;
  window.setTimeout(() => {
    toastRoot.innerHTML = "";
  }, 3200);
}

function formatMessageTimestamp(value) {
  const date = new Date(String(value || ""));
  if (Number.isNaN(date.getTime())) return "Time unavailable";
  return new Intl.DateTimeFormat(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    timeZoneName: "short",
  }).format(date);
}

function renderAiReplyState(item) {
  const status = String(item?.ai_reply_status || "");
  if (!status || status === "published" || status === "cancelled") return "";
  const scheduled = formatMessageTimestamp(item.ai_reply_scheduled_for);
  if (ACTIVE_AI_REPLY_STATUSES.has(status)) {
    return `
      <div class="ai-reply-state" role="status" aria-live="polite">
        <span class="ai-reply-state__pulse" aria-hidden="true"></span>
        <span><strong>AI reply scheduled</strong><span>${escapeHtml(scheduled)}</span></span>
      </div>
    `;
  }
  const message = status === "manual_attention"
    ? "AI could not prepare a reliable reply. Manual attention is required."
    : item.ai_reply_error || "AI reply preparation failed.";
  return `<div class="ai-reply-state ai-reply-state--attention" role="status" aria-live="polite">${escapeHtml(message)}</div>`;
}

function updateReplyPolling() {
  if (replyPollTimer) {
    window.clearTimeout(replyPollTimer);
    replyPollTimer = null;
  }
  const item = state.activeItem;
  if (state.view !== "detail" || !item || !ACTIVE_AI_REPLY_STATUSES.has(String(item.ai_reply_status || ""))) return;
  replyPollTimer = window.setTimeout(async () => {
    if (document.hidden || state.view !== "detail" || !state.activeItem) {
      updateReplyPolling();
      return;
    }
    const ticketId = state.activeItem.ticket_id || state.activeItem.client_ticket_id || "";
    const detail = ticketId ? await fetchTicketDetail(ticketId) : null;
    if (detail) {
      state.activeItem = detail;
      render();
      return;
    }
    updateReplyPolling();
  }, 12000);
}

async function fetchTickets() {
  try {
    const params = new URLSearchParams({
      page: String(state.currentPage),
      page_size: String(PAGE_SIZE),
    });
    if (state.statusFilter === "unreviewed") {
      params.set("review_status", "pending");
    } else if (state.statusFilter === "reviewed") {
      params.set("review_status", "reviewed");
    } else if (state.statusFilter === "automation") {
      params.set("route_status", "automated");
    } else if (state.statusFilter === "not_automated") {
      params.set("route_status", "not_automated");
    } else if (state.statusFilter === "route_errors") {
      params.set("route_errors", "true");
    } else if (ROUTE_LABEL_FILTERS.has(state.statusFilter)) {
      params.set("route_label", state.statusFilter);
    }
    const response = await fetch(`/api/account/cases?${params.toString()}`);
    if (!response.ok) return;
    const data = await response.json();
    state.history = data.cases || data.tickets || data.billing_tickets || [];
    state.pagination = {
      page: Number(data.page || state.currentPage || 1),
      pageSize: Number(data.page_size || PAGE_SIZE),
      total: Number(data.total || 0),
      totalPages: Math.max(1, Number(data.total_pages || 1)),
      hasMore: Boolean(data.has_more),
    };
    state.currentPage = state.pagination.page;
  } catch {
    state.history = [];
    state.pagination = {
      page: state.currentPage,
      pageSize: PAGE_SIZE,
      total: 0,
      totalPages: 1,
      hasMore: false,
    };
  }
}

async function fetchTicketDetail(ticketId) {
  try {
    const response = await fetch(`/api/account/cases/${ticketId}`);
    if (!response.ok) return null;
    return await response.json();
  } catch {
    return null;
  }
}

async function fetchRouteErrorSummary() {
  if (isFetchingRouteErrorSummary) return;
  isFetchingRouteErrorSummary = true;
  try {
    const response = await fetch("/api/account/route-errors/summary?limit=100");
    if (!response.ok) return;
    state.routeErrorSummary = await response.json();
  } catch {
    state.routeErrorSummary = null;
  } finally {
    isFetchingRouteErrorSummary = false;
    if (state.statusFilter === "route_errors") {
      render();
    }
  }
}

function resetCorrectionState(item = null) {
  const currentScope = item?.category === "automation" ? "automation" : item?.scope_label;
  const selected = ROUTE_TUPLE_OPTIONS.find(
    (option) => option.scope === currentScope && option.action === (item?.subcategory || item?.execution_action || item?.route)
  );
  state.correctionScope = selected ? selected.scope : "";
  state.correctionAction = selected ? selected.action : "";
  state.isSubmittingCorrection = false;
  state.correctionError = "";
  state.routeCorrectionExpanded = false;
  state.isSubmittingReview = false;
  state.reviewError = "";
}

async function openTicket(ticketId) {
  const detail = await fetchTicketDetail(ticketId);
  if (!detail) {
    showToast("Failed to load Account Case details.");
    return;
  }
  state.activeItem = detail;
  state.view = "detail";
  state.replyMessage = "";
  state.replyError = "";
  resetCorrectionState(detail);
  render();
}

function openCreateView() {
  state.activeItem = null;
  state.view = "create";
  state.error = "";
  state.replyMessage = "";
  state.replyError = "";
  resetCorrectionState();
  render();
}

async function submitAccountIntake(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const formData = new FormData(form);
  state.title = String(formData.get("title") || "").trim();
  state.question = String(formData.get("question") || "").trim();
  state.customerEmail = String(formData.get("customerEmail") || "").trim();

  if (!state.title || !state.question) {
    state.error = "Title and question are required.";
    render();
    return;
  }

  state.isSubmitting = true;
  state.error = "";
  render();

  try {
    const response = await fetch("/account", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        title: state.title,
        question: state.question,
        customer_email: state.customerEmail || null,
        source: state.source,
      }),
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(payload.detail || "Account intake failed.");
    }
    showToast(payload.ticket_id ? `Ticket ${payload.ticket_id} created` : "Ticket created");
    await fetchTickets();
    if (payload.ticket_id) {
      await openTicket(payload.ticket_id);
    }
    state.title = "";
    state.question = "";
    state.customerEmail = "";
  } catch (err) {
    state.error = err instanceof Error ? err.message : "Account intake failed.";
  } finally {
    state.isSubmitting = false;
    render();
  }
}

function isAutomationStatus(status) {
  return status === "automation" || status === "automated";
}

function isAutomatedRoute(item) {
  const routeStatus = String(item?.route_status || "").trim();
  if (routeStatus) return routeStatus === "automated";
  return isAutomationStatus(item?.status || item?.automation_status || "not_automated");
}

function displayRouteStatus(item) {
  return isAutomatedRoute(item) ? "automation" : "not_automated";
}

function matchesFilter(item) {
  const itemStatus = item.status || item.automation_status || "not_automated";
  const reviewStatus = item.route_review_status || "pending";
  const { secondary } = classificationLabels(item);
  if (state.statusFilter === "all") return true;
  if (state.statusFilter === "unreviewed") return reviewStatus !== "reviewed";
  if (state.statusFilter === "reviewed") return reviewStatus === "reviewed";
  if (state.statusFilter === "automation") return isAutomatedRoute(item);
  if (state.statusFilter === "not_automated") return !isAutomatedRoute(item);
  if (state.statusFilter === "route_errors") return Boolean(item.route_error);
  if (ROUTE_LABEL_FILTERS.has(state.statusFilter)) {
    return secondary === ROUTE_LABEL_FILTER_MATCHES[state.statusFilter];
  }
  return true;
}

function renderFilterControls() {
  const filters = [
    { value: "unreviewed", label: "Unreviewed" },
    { value: "reviewed", label: "Reviewed" },
    { value: "all", label: "All" },
    { value: "automation", label: "Automation" },
    { value: "not_automated", label: "Not automated" },
    { value: "route_errors", label: "Route errors" },
    { value: "human_review", label: "Human Review" },
    { value: "agora_technical", label: "Agora Technical" },
    { value: "agora_non_technical", label: "Agora Non-technical" },
    { value: "non_agora", label: "Non-Agora" },
  ];
  return `
    <div class="filter-chips">
      ${filters
        .map(
          (f) => `
        <button
          class="filter-chip ${state.statusFilter === f.value ? "filter-chip--active" : ""}"
          type="button"
          data-action="set-filter"
          data-value="${escapeHtml(f.value)}"
        >${escapeHtml(f.label)}</button>
      `
        )
        .join("")}
    </div>
  `;
}

function paginationPages(currentPage, totalPages) {
  const pages = [];
  const add = (value) => {
    if (!pages.includes(value)) pages.push(value);
  };
  add(1);
  add(currentPage - 1);
  add(currentPage);
  add(currentPage + 1);
  add(totalPages);
  return pages
    .filter((value) => value >= 1 && value <= totalPages)
    .sort((a, b) => a - b)
    .reduce((items, value, index, source) => {
      if (index > 0 && value - source[index - 1] > 1) {
        items.push("ellipsis");
      }
      items.push(value);
      return items;
    }, []);
}

function renderPaginationControls() {
  const totalPages = Math.max(1, state.pagination.totalPages || 1);
  if (totalPages <= 1) return "";
  const currentPage = Math.min(Math.max(1, state.currentPage || 1), totalPages);
  const pageItems = paginationPages(currentPage, totalPages);
  return `
    <nav class="history-pagination" aria-label="Account case pages">
      <button
        class="pagination-button"
        type="button"
        data-action="set-page"
        data-page="${currentPage - 1}"
        ${currentPage <= 1 ? "disabled" : ""}
        aria-label="Previous page"
      >
        <span class="material-symbols-outlined">chevron_left</span>
      </button>
      ${pageItems
        .map((item) => {
          if (item === "ellipsis") {
            return `<span class="pagination-ellipsis" aria-hidden="true">...</span>`;
          }
          const isActive = item === currentPage;
          return `
            <button
              class="pagination-button ${isActive ? "pagination-button--active" : ""}"
              type="button"
              data-action="set-page"
              data-page="${item}"
              ${isActive ? 'aria-current="page"' : ""}
            >${item}</button>
          `;
        })
        .join("")}
      <button
        class="pagination-button"
        type="button"
        data-action="set-page"
        data-page="${currentPage + 1}"
        ${currentPage >= totalPages ? "disabled" : ""}
        aria-label="Next page"
      >
        <span class="material-symbols-outlined">chevron_right</span>
      </button>
    </nav>
  `;
}

function renderHistorySidebar() {
  const visibleItems = state.history.filter(matchesFilter);
  if (!state.history.length) {
    return `
      <div class="history-empty">
        <span class="material-symbols-outlined">receipt_long</span>
        <p>No Account Cases yet</p>
      </div>
    `;
  }
  if (!visibleItems.length) {
    return `
      ${renderFilterControls()}
      <div class="history-empty">
        <span class="material-symbols-outlined">filter_alt_off</span>
        <p>No Account Cases match this filter</p>
      </div>
    `;
  }
  return `
    ${renderFilterControls()}
    <div class="history-section-title">${
      state.statusFilter === "reviewed"
        ? "Reviewed Account Cases"
        : state.statusFilter === "unreviewed"
          ? "Unreviewed Account Cases"
          : "Recent Account Cases"
    }</div>
    ${visibleItems
      .map(
        (item) => {
          const itemId = item.account_case_id || item.ticket_id || item.billing_ticket_id || "";
          const itemTicketId = item.ticket_id || item.client_ticket_id || "";
          const activeTicketId = state.activeItem ? (state.activeItem.ticket_id || state.activeItem.client_ticket_id || "") : "";
          const activeBillingId = state.activeItem ? (state.activeItem.account_case_id || state.activeItem.billing_ticket_id || "") : "";
          const isActive = (activeBillingId && activeBillingId === itemId) || (activeTicketId && activeTicketId === itemTicketId);
          const itemSource = item.source || "";
          const itemStatus = displayRouteStatus(item);
          const ticketNumber = accountTicketNumber(item);
          return `
    <button class="history-item ${isActive ? "history-item--active" : ""}" type="button" data-action="open-ticket" data-id="${escapeHtml(itemId)}">
      <div class="history-item-header">
        <div class="history-item-identity">
          <span class="history-ticket-number">#${escapeHtml(ticketNumber)}</span>
          <strong>${escapeHtml(item.title || "")}</strong>
        </div>
        ${renderSourceValue(itemSource)}
      </div>
      ${renderClassificationBadges(item)}
      <div class="history-item-meta">
        <span class="status-badge status-badge--${escapeHtml(itemStatus)}">${escapeHtml(statusLabel(itemStatus))}</span>
        <span class="history-time">${escapeHtml((item.updated_at || item.created_at || "").slice(0, 16).replace("T", " "))}</span>
      </div>
    </button>
  `;
        }
      )
      .join("")}
    ${renderPaginationControls()}
  `;
}

function renderCreateForm() {
  return `
    <div class="panel form-stack">
      <div class="form-header">
        <h3>Create a ticket</h3>
        <p class="form-desc">Submit an account case for routing and automated processing.</p>
      </div>
      <form data-account-form>
        <label class="field">
          <span class="field-label">Title</span>
          <input class="input" name="title" value="${escapeHtml(state.title)}" placeholder="Detailed invoice request" autocomplete="off" />
        </label>
        <label class="field">
          <span class="field-label">Customer email</span>
          <input class="input" name="customerEmail" value="${escapeHtml(state.customerEmail)}" placeholder="customer@example.com" autocomplete="off" />
        </label>
        <label class="field">
          <span class="field-label">Question</span>
          <textarea class="textarea" name="question" placeholder="Issue date: 6 May 2026&#10;Transaction ID: 1104245232004173824&#10;Amount: USD 705.97">${escapeHtml(state.question)}</textarea>
        </label>
        <div class="actions">
          <button class="primary-button" type="submit" ${state.isSubmitting ? "disabled" : ""}>
            <span class="material-symbols-outlined">send</span>
            ${state.isSubmitting ? "Creating..." : "Create ticket"}
          </button>
        </div>
      </form>
      ${
        state.error
          ? `<div class="error-banner"><span class="material-symbols-outlined">error</span>${escapeHtml(state.error)}</div>`
          : ""
      }
    </div>
  `;
}

function renderMessageThread() {
  const item = state.activeItem;
  if (!item) return "";
  const messages = Array.isArray(item.messages) ? item.messages : [];
  if (!messages.length) return "";

  return `
    <div class="message-thread">
      <div class="detail-section-title">Conversation</div>
      ${messages
        .map((msg) => {
          const role = String(msg.role || "").toLowerCase();
          const content = String(msg.content || "");
          const isCustomer = role === "customer" || role === "user";
          const bubbleClass = isCustomer ? "msg-bubble--customer" : "msg-bubble--assistant";
          const label = isCustomer ? "Customer" : "AI";
          const timestamp = formatMessageTimestamp(msg.created_at);
          return `
        <div class="msg-row ${isCustomer ? "msg-row--customer" : "msg-row--assistant"}">
          <div class="msg-bubble ${bubbleClass}">
            <div class="msg-header"><span class="msg-label">${escapeHtml(label)}</span><time datetime="${escapeHtml(msg.created_at || "")}">${escapeHtml(timestamp)}</time></div>
            <div class="msg-content">${renderMarkdownMessage(content)}</div>
          </div>
        </div>
      `;
        })
        .join("")}
      ${renderAiReplyState(item)}
    </div>
  `;
}

function renderReplyComposer() {
  const item = state.activeItem;
  if (!item) return "";
  return `
    <div class="reply-composer">
      <div class="detail-section-title">Add customer message</div>
      <textarea
        class="reply-textarea"
        placeholder="Add the customer's latest message..."
        data-reply-input
        ${state.isSubmittingReply ? "disabled" : ""}
      >${escapeHtml(state.replyMessage)}</textarea>
      <div class="reply-actions">
        <button
          class="primary-button primary-button--small"
          type="button"
          data-action="submit-reply"
          ${state.isSubmittingReply ? "disabled" : ""}
        >
          <span class="material-symbols-outlined">send</span>
          ${state.isSubmittingReply ? "Adding..." : "Add message"}
        </button>
      </div>
      ${
        state.replyError
          ? `<div class="error-banner"><span class="material-symbols-outlined">error</span>${escapeHtml(state.replyError)}</div>`
          : ""
      }
    </div>
  `;
}

function routeTupleSelectValue() {
  if (!state.correctionScope || !state.correctionAction) return DEFAULT_ROUTE_TUPLE_SELECT_VALUE;
  return `${state.correctionScope}|${state.correctionAction}`;
}

function renderRouteCorrectionPanel() {
  const item = state.activeItem;
  if (!item) return "";
  const currentCorrection = item.route_correction || {};
  const selectedValue = routeTupleSelectValue();
  const hasCorrection = Boolean(item.route_corrected || currentCorrection.corrected_execution_action);
  const originalAction =
    currentCorrection.original_execution_action || item.execution_action || item.route || "";
  const correctedAction = currentCorrection.corrected_execution_action || "";
  const showChangeRecord = Boolean(originalAction && correctedAction && originalAction !== correctedAction);
  return `
    <div class="route-correction detail-section" ${state.routeCorrectionExpanded ? "" : "hidden"}>
      <div class="detail-section-title">Route correction</div>
      ${
        hasCorrection
          ? `<div class="route-correction-current">
              <span class="meta-label">Current correction</span>
              <span class="meta-value">${escapeHtml(
                [
                  currentCorrection.corrected_scope_label || item.scope_label,
                  currentCorrection.corrected_route_family || item.route_family,
                  currentCorrection.corrected_execution_action || item.execution_action || item.route,
                ]
                  .filter(Boolean)
                  .join(" / ")
              )}</span>
            </div>`
          : ""
      }
      ${
        showChangeRecord
          ? `<div class="route-change-record">route changed from ${escapeHtml(originalAction)} to ${escapeHtml(correctedAction)}</div>`
          : ""
      }
      <label class="field">
        <span class="field-label">Correct route tuple</span>
        <select class="input" data-correction-select ${state.isSubmittingCorrection ? "disabled" : ""}>
          <option value="${DEFAULT_ROUTE_TUPLE_SELECT_VALUE}" ${selectedValue === DEFAULT_ROUTE_TUPLE_SELECT_VALUE ? "selected" : ""}>Select scope / action</option>
          ${ROUTE_TUPLE_OPTIONS.map((option) => {
            const value = `${option.scope}|${option.action}`;
            return `<option value="${escapeHtml(value)}" ${selectedValue === value ? "selected" : ""}>${escapeHtml(option.label)}</option>`;
          }).join("")}
        </select>
      </label>
      <div class="reply-actions">
        <button
          class="primary-button primary-button--small"
          type="button"
          data-action="submit-route-correction"
          ${state.isSubmittingCorrection ? "disabled" : ""}
        >
          <span class="material-symbols-outlined">rule_settings</span>
          ${state.isSubmittingCorrection ? "Saving..." : "Save correction"}
        </button>
      </div>
      ${
        state.correctionError
          ? `<div class="error-banner"><span class="material-symbols-outlined">error</span>${escapeHtml(state.correctionError)}</div>`
          : ""
      }
    </div>
  `;
}

function renderRouteErrorSummaryPanel() {
  if (state.statusFilter !== "route_errors") return "";
  const summary = state.routeErrorSummary;
  if (!summary) {
    return `
      <div class="route-summary detail-section">
        <div class="detail-section-title">Route error summary</div>
        <p class="result-copy">Loading route error summary...</p>
      </div>
    `;
  }
  const transitions = summary.transitions || summary.top_transitions || summary.predicted_to_corrected || [];
  return `
    <div class="route-summary detail-section">
      <div class="detail-section-title">Route error summary</div>
      <div class="route-summary-grid">
        <div><span class="meta-label">Total</span><strong>${escapeHtml(summary.total_error_cases ?? summary.total ?? 0)}</strong></div>
        <div><span class="meta-label">Corrected</span><strong>${escapeHtml(summary.corrected_count ?? 0)}</strong></div>
        <div><span class="meta-label">Low confidence</span><strong>${escapeHtml(summary.low_confidence_count ?? 0)}</strong></div>
      </div>
      ${
        transitions.length
          ? `<div class="route-transition-list">${transitions
              .slice(0, 6)
              .map((entry) => {
                if (entry.transition) {
                  return `<div class="route-transition"><span>${escapeHtml(entry.transition)}</span><strong>${escapeHtml(entry.count ?? "")}</strong></div>`;
                }
                const from = entry.predicted || entry.original || entry.from || entry.original_execution_action || "Unknown";
                const to = entry.corrected || entry.to || entry.corrected_execution_action || "Uncorrected";
                const count = entry.count ?? entry.total ?? "";
                return `<div class="route-transition"><span>${escapeHtml(from)} -> ${escapeHtml(to)}</span><strong>${escapeHtml(count)}</strong></div>`;
              })
              .join("")}</div>`
          : ""
      }
    </div>
  `;
}

function renderDetailView() {
  const item = state.activeItem;
  if (!item) return "";

  let missingFields = [];
  if (Array.isArray(item.missing_fields)) missingFields = item.missing_fields;
  else if (typeof item.missing_fields === "string") {
    try { missingFields = JSON.parse(item.missing_fields || "[]"); } catch {}
  }

  let collectedFields = {};
  if (typeof item.collected_fields === "object" && item.collected_fields !== null) {
    collectedFields = item.collected_fields;
  }

  const itemSource = item.source || "";
  const itemStatus = displayRouteStatus(item);
  const ticketId = item.ticket_id || item.client_ticket_id || "";
  const ticketNumber = accountTicketNumber(item);
  const accountCaseId = item.account_case_id || item.billing_ticket_id || "";
  const hasDifferentInternalTicketId = Boolean(ticketId && ticketNumber && String(ticketId) !== ticketNumber);

  return `
    <div class="panel detail-stack">
      <div class="detail-header">
        <div class="detail-title">
          <span class="detail-ticket-number">Ticket #${escapeHtml(ticketNumber)}</span>
          <h3>${escapeHtml(item.title || "")}</h3>
        </div>
        ${renderClassificationBadges(item)}
      </div>
      <div class="meta-grid">
        <div class="meta-row">
          <span class="meta-label">Account Case ID</span>
          <span class="meta-value">${escapeHtml(accountCaseId)}</span>
        </div>
        <div class="meta-row">
          <span class="meta-label">Ticket #</span>
          <span class="meta-value">#${escapeHtml(ticketNumber)}</span>
        </div>
        ${hasDifferentInternalTicketId ? `<div class="meta-row"><span class="meta-label">Internal Ticket ID</span><span class="meta-value">${escapeHtml(ticketId)}</span></div>` : ""}

        <div class="meta-row">
          <span class="meta-label">Source</span>
          <span class="meta-value">${renderSourceValue(itemSource)}</span>
        </div>
        <div class="meta-row">
          <span class="meta-label">Status</span>
          <span class="meta-value status-badge status-badge--${escapeHtml(itemStatus)}">${escapeHtml(statusLabel(itemStatus))}</span>
        </div>
        <div class="meta-row meta-row--route-result">
          <span class="meta-label">Route result</span>
          <div class="meta-row--route-result-value">
            <span class="meta-value">${escapeHtml(routeResultLabel(item))}</span>
            <button
              class="filter-chip correct-route-toggle"
              type="button"
              data-action="toggle-route-correction"
              aria-expanded="${state.routeCorrectionExpanded ? "true" : "false"}"
              ${state.isSubmittingReview ? "disabled" : ""}
            >
              correct route
            </button>
            ${
              item.route_review_status === "reviewed"
                ? `<button
                    class="filter-chip unreview-toggle"
                    type="button"
                    data-action="unreview-route"
                    ${state.isSubmittingReview ? "disabled" : ""}
                  >unreview</button>`
                : `<button
                    class="filter-chip pass-route-toggle"
                    type="button"
                    data-action="pass-route"
                    ${state.isSubmittingReview ? "disabled" : ""}
                  >pass</button>`
            }
          </div>
        </div>
        <div class="meta-row">
          <span class="meta-label">Internal email</span>
          <span class="meta-value">${escapeHtml(item.internal_email_send_status || "not_applicable")}</span>
        </div>
        <div class="meta-row">
          <span class="meta-label">Response link</span>
          <span class="meta-value">${escapeHtml(internalEmailResponseLinkStatus(item))}</span>
        </div>
        ${
          item.internal_email_send_reason
            ? `<div class="meta-row"><span class="meta-label">Email reason</span><span class="meta-value">${escapeHtml(item.internal_email_send_reason)}</span></div>`
            : ""
        }
        ${
          item.route_reason
            ? `<div class="meta-row"><span class="meta-label">Route reason</span><span class="meta-value">${escapeHtml(item.route_reason)}</span></div>`
            : ""
        }
        ${
          item.created_at
            ? `<div class="meta-row"><span class="meta-label">Created</span><span class="meta-value">${escapeHtml(item.created_at.slice(0, 16).replace("T", " "))}</span></div>`
            : ""
        }
      </div>
      ${renderRouteCorrectionPanel()}
      ${
        missingFields.length
          ? `<div class="detail-section warning"><div class="detail-section-title">Missing fields</div><ul class="missing-list">${missingFields
              .map((field) => `<li>${escapeHtml(field)}</li>`)
              .join("")}</ul></div>`
          : ""
      }
      ${
        Object.keys(collectedFields).length
          ? `<div class="detail-section"><div class="detail-section-title">Collected fields</div><ul class="collected-list">${Object.entries(collectedFields)
              .map(([k, v]) => `<li><strong>${escapeHtml(k)}</strong>: ${escapeHtml(String(v))}</li>`)
              .join("")}</ul></div>`
          : ""
      }
      ${renderMessageThread()}
      ${renderReplyComposer()}
    </div>
  `;
}

async function submitRouteCorrection() {
  const item = state.activeItem;
  if (!item) return;
  if (!state.correctionScope || !state.correctionAction) {
    state.correctionError = "Select a route tuple.";
    render();
    return;
  }

  state.isSubmittingCorrection = true;
  state.correctionError = "";
  render();

  try {
    const billingTicketId = item.account_case_id || item.billing_ticket_id || item.ticket_id || "";
    const response = await fetch(`/api/account/cases/${billingTicketId}/route-correction`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(
        state.correctionScope === "automation"
          ? { category: "automation", subcategory: state.correctionAction, corrector: "operator" }
          : {
              scope_label: state.correctionScope,
              execution_action: state.correctionAction,
              corrector: "operator",
            }
      ),
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(payload.detail || "Route correction failed.");
    }
    state.activeItem = payload;
    resetCorrectionState(payload);
    showToast("Route correction saved");
    await fetchTickets();
    if (state.statusFilter === "route_errors") {
      await fetchRouteErrorSummary();
    }
  } catch (err) {
    state.correctionError = err instanceof Error ? err.message : "Route correction failed.";
  } finally {
    state.isSubmittingCorrection = false;
    render();
  }
}

async function submitRouteReview(reviewStatus) {
  const item = state.activeItem;
  if (!item) return;

  state.isSubmittingReview = true;
  state.reviewError = "";
  render();

  try {
    const billingTicketId = item.account_case_id || item.billing_ticket_id || item.ticket_id || "";
    const response = await fetch(`/api/account/cases/${billingTicketId}/route-review`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        review_status: reviewStatus,
        reviewer: "operator",
      }),
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(payload.detail || "Route review failed.");
    }
    state.activeItem = payload;
    showToast(reviewStatus === "reviewed" ? "Route marked as reviewed" : "Route moved back to unreviewed");
    await fetchTickets();
  } catch (err) {
    state.reviewError = err instanceof Error ? err.message : "Route review failed.";
  } finally {
    state.isSubmittingReview = false;
    render();
  }
}

async function submitReply() {
  const item = state.activeItem;
  if (!item) return;

  const message = state.replyMessage.trim();
  if (!message) {
    state.replyError = "Reply cannot be empty.";
    render();
    return;
  }

  state.isSubmittingReply = true;
  state.replyError = "";
  render();

  try {
    const billingTicketId = item.account_case_id || item.billing_ticket_id || item.ticket_id || "";
    const response = await fetch(`/api/account/cases/${billingTicketId}/reply`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message }),
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(payload.detail || "Message failed.");
    }
    state.activeItem = payload;

    state.replyMessage = "";
    showToast("Customer message added");
    await fetchTickets();
  } catch (err) {
    state.replyError = err instanceof Error ? err.message : "Reply failed.";
  } finally {
    state.isSubmittingReply = false;
    render();
  }
}

function render() {
  appRoot.innerHTML = `
    <main class="account-shell">
      <aside class="side-panel">
        <div class="brand">
          <div class="brand-mark"><span class="material-symbols-outlined">support_agent</span></div>
          <div>
            <div class="eyebrow">Account intake</div>
            <h1>Support Portal</h1>
          </div>
        </div>
        <div class="side-actions">
          <button class="primary-button primary-button--small" type="button" data-action="new-ticket">
            <span class="material-symbols-outlined">add</span>
            New Account Case
          </button>
        </div>
        <div class="history-stack" id="history-list">
          ${renderHistorySidebar()}
        </div>
      </aside>
      <section class="workbench">
        <div class="workbench-header">
          <div>
            <span class="pill"><span class="material-symbols-outlined">route</span>HTTP or manual</span>
            <h2>${state.view === "create" ? "Create and route an Account Case" : "Account Case detail"}</h2>
          </div>
          ${state.view === "detail" ? `<button class="ghost-button" type="button" data-action="back-to-create">Back to create</button>` : ""}
        </div>
        <div class="intake-grid">
          ${renderRouteErrorSummaryPanel()}
          ${state.view === "create" ? renderCreateForm() : ""}
          ${state.view === "detail" ? renderDetailView() : ""}
        </div>
      </section>
    </main>
  `;
  bind();
  updateReplyPolling();
}

document.addEventListener("visibilitychange", () => {
  if (!document.hidden) updateReplyPolling();
});

function bind() {
  const form = document.querySelector("[data-account-form]");
  if (form) {
    form.addEventListener("submit", submitAccountIntake);
  }
  const historyList = document.getElementById("history-list");
  if (historyList) {
    historyList.addEventListener("click", (event) => {
      const button = event.target.closest("[data-action='open-ticket']");
      if (button) {
        const id = button.dataset.id;
        if (id) openTicket(id);
        return;
      }
      const filterBtn = event.target.closest("[data-action='set-filter']");
      if (filterBtn) {
        state.statusFilter = filterBtn.dataset.value || "unreviewed";
        state.currentPage = 1;
        if (state.statusFilter === "route_errors") {
          state.routeErrorSummary = null;
          void fetchRouteErrorSummary();
        }
        void fetchTickets().then(() => render());
        return;
      }
      const pageBtn = event.target.closest("[data-action='set-page']");
      if (pageBtn && !pageBtn.disabled) {
        const targetPage = Number(pageBtn.dataset.page || "1");
        const totalPages = Math.max(1, state.pagination.totalPages || 1);
        state.currentPage = Math.min(Math.max(1, targetPage), totalPages);
        void fetchTickets().then(() => render());
        return;
      }
    });
  }
  document.querySelectorAll("[data-action='new-ticket']").forEach((el) => {
    el.addEventListener("click", openCreateView);
  });
  document.querySelectorAll("[data-action='back-to-create']").forEach((el) => {
    el.addEventListener("click", openCreateView);
  });
  document.querySelectorAll("[data-action='submit-reply']").forEach((el) => {
    el.addEventListener("click", submitReply);
  });
  document.querySelectorAll("[data-action='submit-route-correction']").forEach((el) => {
    el.addEventListener("click", submitRouteCorrection);
  });
  document.querySelectorAll("[data-action='toggle-route-correction']").forEach((el) => {
    el.addEventListener("click", () => {
      state.routeCorrectionExpanded = !state.routeCorrectionExpanded;
      render();
    });
  });
  document.querySelectorAll("[data-action='pass-route']").forEach((el) => {
    el.addEventListener("click", () => void submitRouteReview("reviewed"));
  });
  document.querySelectorAll("[data-action='unreview-route']").forEach((el) => {
    el.addEventListener("click", () => void submitRouteReview("pending"));
  });
  const replyInput = document.querySelector("[data-reply-input]");
  if (replyInput) {
    replyInput.addEventListener("input", (event) => {
      state.replyMessage = event.target.value;
    });
  }
  const correctionSelect = document.querySelector("[data-correction-select]");
  if (correctionSelect) {
    correctionSelect.addEventListener("change", (event) => {
      const [scope, action] = String(event.target.value || "").split("|");
      const selected = ROUTE_TUPLE_OPTIONS.find((option) => option.scope === scope && option.action === action);
      state.correctionScope = selected ? selected.scope : "";
      state.correctionAction = selected ? selected.action : "";
    });
  }
  applySharedComposerToolbarStateToButtons(document, state.composerToolbarState);
}

renderSharedComposerFormattingToolbarButtons(state.composerToolbarState);
serializeRichComposerHtmlToMarkdown("");
void composerRuntime;
(async () => {
  await fetchTickets();
  render();
})();
