const wsStatusEl = document.getElementById("ws-status");
const realtimeStatusItemEl = document.querySelector('[data-rail-footer-status="realtime"]');
const logoutButtonEl = document.getElementById("logout-btn");
const refreshButtonEl = document.getElementById("refresh-button");
const opsHeaderEl = document.getElementById("ops-header");
const opsHeaderBodyEl = document.getElementById("ops-header-body");
const ticketOpsButtonEl = document.querySelector('[data-dashboard-nav="ticket-ops"]');
const ticketDetailGroupEl = document.querySelector("[data-ticket-detail-group]");
const ticketDetailGroupToggleEl = document.querySelector("[data-ticket-detail-group-toggle]");
const ticketDetailSubnavEl = document.getElementById("ticket-detail-subnav");
const ticketDetailStatusButtons = Array.from(document.querySelectorAll("[data-ticket-detail-status]"));
const dashboardViewRegionEl = document.getElementById("dashboard-view-region");
const ticketOpsOverviewEl = document.getElementById("ticket-ops-overview");
const ticketBoardRegionEl = document.getElementById("ticket-board-region");
const ticketDetailModalEl = document.getElementById("ticket-detail-modal");
const ticketDetailDialogEl = document.getElementById("ticket-detail-dialog");
const ticketDetailTitleEl = document.getElementById("ticket-detail-title");
const ticketDetailBodyEl = document.getElementById("ticket-detail-body");
const ticketVolumeEl = document.getElementById("ticket-volume");
const resolutionRateEl = document.getElementById("resolution-rate");
const sentimentAlertsEl = document.getElementById("sentiment-alerts");
const waitingForEngineerEl = document.getElementById("waiting-for-engineer");
const queueHealthTitleEl = document.getElementById("queue-health-title");
const queueHealthDetailEl = document.getElementById("queue-health-detail");
const openTicketCountEl = document.getElementById("open-ticket-count");
const resolvedTicketCountEl = document.getElementById("resolved-ticket-count");
const communicatingTicketCountEl = document.getElementById("communicating-ticket-count");
const escalatedTicketCountEl = document.getElementById("escalated-ticket-count");
const badSentimentTicketCountEl = document.getElementById("bad-sentiment-ticket-count");
const waitingTicketChipEl = document.getElementById("waiting-ticket-chip");
const communicatingTicketChipEl = document.getElementById("communicating-ticket-chip");
const escalatedTicketChipEl = document.getElementById("escalated-ticket-chip");
const escalationWatchTitleEl = document.getElementById("escalation-watch-title");
const escalationWatchDetailEl = document.getElementById("escalation-watch-detail");
const operatorSummaryTitleEl = document.getElementById("operator-summary-title");
const operatorSummaryDetailEl = document.getElementById("operator-summary-detail");
const eventVolumeBarsEl = document.getElementById("event-volume-bars");
const statusBreakdownEl = document.getElementById("status-breakdown");
const sentimentBreakdownEl = document.getElementById("sentiment-breakdown");
const flowBreakdownEl = document.getElementById("flow-breakdown");

const DEFAULT_HEADER_BODY =
  "Real-time ticket throughput, escalation awareness, and operator workload in a calmer AI-managed control surface.";
const TICKET_DETAIL_STATUSES = ["investigating", "escalated", "communicating", "resolved"];
const TICKET_VIEW_COPY = {
  investigating: {
    title: "Investigating Tickets",
    detail:
      "Primary client tickets with at least one active linked sub ticket. Review the root ticket first, then inspect each sub ticket before switching into the engineer workspace.",
    summary: "One card per client ticket, even when multiple linked sub tickets are active or recently closed.",
  },
  escalated: {
    title: "Escalated Tickets",
    detail:
      "Tickets where the customer explicitly asked for engineer assistance and the support flow needs closer coordination.",
    summary: "Customer-visible escalations that may need triage, expectation-setting, or a deeper investigation handoff.",
  },
  communicating: {
    title: "Communicating Tickets",
    detail:
      "Tickets still moving through the normal AI-managed conversation flow, with the latest customer context visible in one place.",
    summary: "Active tickets where the AI is still communicating and the queue needs visibility rather than direct intervention.",
  },
  resolved: {
    title: "Resolved Tickets",
    detail:
      "Closed tickets shown in the same rail taxonomy so operators can confirm the final state and recent customer context.",
    summary: "Recently closed tickets for fast spot-checking, audit, and sentiment follow-through.",
  },
};

let currentDashboardView = "ticket-ops";
let ticketBoardViewMode = "grid";
let ticketDetailsExpanded = true;
let socket = null;
let heartbeatTimer = null;
let reconnectTimer = null;
let logoutLoading = false;
let refreshLoading = false;
let selectedTicketId = "";
let selectedTicketDetail = null;
let ticketDetailLoading = false;
let ticketDetailError = "";
let ticketDetailSummary = "";
let ticketDetailNextAction = "";
let ticketDetailSummaryLoading = false;
let ticketDetailSummaryFailed = false;
let ticketDetailSummaryModel = "";
let ticketDetailRuntimeExpanded = false;
let ticketDetailExpandedSubTicketIds = new Set();
let lastTicketDetailFocusEl = null;

const ticketBoardStore = Object.fromEntries(TICKET_DETAIL_STATUSES.map((status) => [status, []]));
const ticketBoardLoadingByStatus = Object.fromEntries(
  TICKET_DETAIL_STATUSES.map((status) => [status, false])
);
const ticketBoardErrorByStatus = Object.fromEntries(TICKET_DETAIL_STATUSES.map((status) => [status, ""]));

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

async function fetchJson(url, options = undefined) {
  const response = await fetch(url, options);
  if (!response.ok) {
    let reason = `HTTP ${response.status}`;
    try {
      const payload = await response.json();
      if (typeof payload?.detail === "string") {
        reason = payload.detail;
      } else if (typeof payload?.detail?.message === "string") {
        reason = payload.detail.message;
      }
    } catch {
      // Keep the fallback message.
    }
    throw new Error(reason);
  }
  return response.json();
}

function formatNumber(value) {
  return new Intl.NumberFormat().format(Number(value || 0));
}

function formatDecimal(value, maximumFractionDigits = 1) {
  return new Intl.NumberFormat(undefined, {
    minimumFractionDigits: 0,
    maximumFractionDigits,
  }).format(Number(value || 0));
}

function formatDateTime(value) {
  if (!value) {
    return "-";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return "-";
  }
  return date.toLocaleString(undefined, {
    hour12: false,
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function normalizeString(value) {
  return String(value ?? "").trim();
}

function humanizeToken(value) {
  const normalized = normalizeString(value);
  if (!normalized) {
    return "-";
  }
  return normalized.replaceAll("_", " ").replace(/\b\w/g, (match) => match.toUpperCase());
}

function setText(element, value) {
  if (element) {
    element.textContent = value;
  }
}

function setRefreshLoading(isLoading) {
  refreshLoading = isLoading;
  if (!refreshButtonEl) {
    return;
  }
  refreshButtonEl.disabled = isLoading;
  refreshButtonEl.textContent = isLoading ? "Refreshing..." : "Refresh Dashboard";
}

function setRealtimeStatus(text) {
  const normalizedText = normalizeString(text) || "Realtime: connecting...";
  setText(wsStatusEl, normalizedText);

  if (!realtimeStatusItemEl) {
    return;
  }

  const normalizedStatus = normalizedText.toLowerCase();
  const statusTone = normalizedStatus.includes("error") || normalizedStatus.includes("failed")
    ? "error"
    : normalizedStatus.includes("disconnected")
      ? "disconnected"
      : normalizedStatus.includes("connected")
        ? "connected"
        : "connecting";

  realtimeStatusItemEl.dataset.state = statusTone;
  realtimeStatusItemEl.classList.toggle("is-connecting", statusTone === "connecting");
  realtimeStatusItemEl.classList.toggle("is-connected", statusTone === "connected");
  realtimeStatusItemEl.classList.toggle("is-disconnected", statusTone === "disconnected");
  realtimeStatusItemEl.classList.toggle("is-error", statusTone === "error");
  realtimeStatusItemEl.title = normalizedText;
  realtimeStatusItemEl.setAttribute("aria-label", normalizedText);
}

function renderRailFooter() {
  if (!logoutButtonEl) {
    return;
  }

  logoutButtonEl.disabled = logoutLoading;
  logoutButtonEl.title = logoutLoading ? "Logging out..." : "Logout";
  logoutButtonEl.setAttribute("aria-label", logoutLoading ? "Logging out..." : "Logout");
}

function normalizeDashboardView(value) {
  const normalized = String(value || "").trim().toLowerCase();
  return TICKET_DETAIL_STATUSES.includes(normalized) ? normalized : "ticket-ops";
}

function normalizeTicketBoardViewMode(value) {
  return String(value || "").toLowerCase() === "list" ? "list" : "grid";
}

function applyTicketBoardViewMode(value, { render = true } = {}) {
  ticketBoardViewMode = normalizeTicketBoardViewMode(value);
  if (render && TICKET_DETAIL_STATUSES.includes(currentDashboardView)) {
    renderTicketBoard();
  }
  return ticketBoardViewMode;
}

function normalizeStatusValue(value) {
  const normalized = String(value || "open").toLowerCase();
  if (normalized === "waiting_for_engineer") {
    return "investigating";
  }
  if (normalized === "escalated") {
    return "escalated";
  }
  if (normalized === "communicating") {
    return "communicating";
  }
  if (normalized === "resolved") {
    return "resolved";
  }
  return normalized === "investigating" ? "investigating" : "open";
}

function statusLabel(value) {
  const normalized = normalizeStatusValue(value);
  if (normalized === "communicating") {
    return "Communicating";
  }
  if (normalized === "escalated") {
    return "Escalated";
  }
  if (normalized === "investigating") {
    return "Investigating";
  }
  if (normalized === "resolved") {
    return "Resolved";
  }
  return "Open";
}

function statusClass(value) {
  const normalized = normalizeStatusValue(value);
  if (normalized === "resolved") {
    return "status-resolved";
  }
  if (normalized === "investigating") {
    return "status-investigating";
  }
  if (normalized === "escalated") {
    return "status-escalated";
  }
  if (normalized === "communicating") {
    return "status-communicating";
  }
  return "status-open";
}

function statusSurfaceClass(value) {
  const normalized = normalizeStatusValue(value);
  if (normalized === "resolved") {
    return "status-surface-resolved";
  }
  if (normalized === "investigating") {
    return "status-surface-investigating";
  }
  if (normalized === "escalated") {
    return "status-surface-escalated";
  }
  if (normalized === "communicating") {
    return "status-surface-communicating";
  }
  return "status-surface-open";
}

function normalizeSentimentLabel(value) {
  const normalized = String(value || "").trim().toLowerCase();
  if (normalized === "bad" || normalized === "neutral" || normalized === "good") {
    return normalized;
  }
  return "";
}

function roleLabel(role) {
  const normalized = String(role || "").toLowerCase();
  if (normalized === "customer") {
    return "Customer";
  }
  if (normalized === "assistant") {
    return "AI";
  }
  if (normalized === "engineer_ai") {
    return "Engineer AI";
  }
  if (normalized === "engineer") {
    return "Engineer";
  }
  return "System";
}

function getActiveInvestigation(ticket) {
  if (!ticket || typeof ticket !== "object") {
    return null;
  }
  return ticket.active_investigation && typeof ticket.active_investigation === "object"
    ? ticket.active_investigation
    : null;
}

function getLatestClosedInvestigation(ticket) {
  if (!ticket || typeof ticket !== "object") {
    return null;
  }
  const history = Array.isArray(ticket.investigation_history) ? ticket.investigation_history : [];
  return history.find((item) => item && typeof item === "object") || null;
}

function getDisplayInvestigation(ticket) {
  return getActiveInvestigation(ticket) || getLatestClosedInvestigation(ticket);
}

function ticketSubTickets(ticket) {
  return Array.isArray(ticket?.sub_tickets)
    ? ticket.sub_tickets.filter((item) => item && typeof item === "object")
    : [];
}

function latestLinkedSubTicketUpdate(ticket) {
  const directUpdate = normalizeString(ticket?.latest_sub_ticket_update);
  if (directUpdate) {
    return directUpdate;
  }

  const orderedSubTickets = ticketSubTickets(ticket).slice().sort((left, right) => {
    const leftStamp = String(left?.updated_at || left?.created_at || "");
    const rightStamp = String(right?.updated_at || right?.created_at || "");
    return rightStamp.localeCompare(leftStamp);
  });

  for (const item of orderedSubTickets) {
    const latestUpdate = latestInvestigationUpdate(item);
    if (latestUpdate) {
      return latestUpdate;
    }
  }
  return "";
}

function latestInvestigationUpdate(ticket) {
  const activeInvestigation = getActiveInvestigation(ticket);
  if (activeInvestigation) {
    const messages = Array.isArray(activeInvestigation.messages) ? activeInvestigation.messages : [];
    for (let index = messages.length - 1; index >= 0; index -= 1) {
      const content = String(messages[index]?.content || "").trim();
      if (content) {
        return content;
      }
    }
  }
  const latestClosedInvestigation = getLatestClosedInvestigation(ticket);
  if (latestClosedInvestigation) {
    const messages = Array.isArray(latestClosedInvestigation.messages)
      ? latestClosedInvestigation.messages
      : [];
    for (let index = messages.length - 1; index >= 0; index -= 1) {
      const content = String(messages[index]?.content || "").trim();
      if (content) {
        return content;
      }
    }
  }
  const linkedSubTicketUpdate = latestLinkedSubTicketUpdate(ticket);
  if (linkedSubTicketUpdate) {
    return linkedSubTicketUpdate;
  }
  return "";
}

function investigationStateLabel(value) {
  const normalized = String(value || "active").toLowerCase();
  if (normalized === "awaiting_confirmation") {
    return "Awaiting Engineer Approval";
  }
  if (normalized === "closed") {
    return "Closed";
  }
  return "Open Engineer Ticket";
}

function latestTicketMessage(ticket, roles) {
  const roleSet = Array.isArray(roles) ? roles.map((value) => String(value || "").toLowerCase()) : [];
  const messages = Array.isArray(ticket?.messages) ? ticket.messages : [];
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const item = messages[index];
    const content = normalizeString(item?.content);
    if (!content) {
      continue;
    }
    const role = String(item?.role || "").toLowerCase();
    if (!roleSet.length || roleSet.includes(role)) {
      return item;
    }
  }
  return null;
}

function truncateText(value, maxLength = 220) {
  const normalized = normalizeString(value);
  if (!normalized) {
    return "";
  }
  return normalized.length > maxLength ? `${normalized.slice(0, maxLength)}...` : normalized;
}

function ticketRequester(ticket) {
  return String(ticket?.requester || ticket?.customer_id || "Unknown");
}

function ticketSubject(ticket) {
  return String(ticket?.subject || "(No subject)");
}

function buildLocalSummaryFallback(ticket) {
  const status = statusLabel(normalizeStatusValue(ticket?.status || "open"));
  const latestCustomer = latestTicketMessage(ticket, ["customer"]);
  const latestAssistant = latestTicketMessage(ticket, ["assistant"]);
  const activeInvestigation = getActiveInvestigation(ticket);
  const latestInternal = latestInvestigationUpdate(ticket);
  const subTickets = ticketSubTickets(ticket);
  const summaryLines = [`Ticket is currently ${status}.`];

  if (latestCustomer?.content) {
    summaryLines.push(`Latest customer request: ${truncateText(latestCustomer.content, 220)}`);
  }
  if (latestAssistant?.content) {
    summaryLines.push(`Latest AI response: ${truncateText(latestAssistant.content, 220)}`);
  }
  if (activeInvestigation) {
    summaryLines.push(
      `Engineer ticket is ${investigationStateLabel(activeInvestigation.state).toLowerCase()}.`
    );
  }
  if (!activeInvestigation && subTickets.length) {
    summaryLines.push(
      `Linked sub tickets: ${subTickets.length} total.`
    );
  }
  if (latestInternal) {
    summaryLines.push(`Latest engineer ticket update: ${truncateText(latestInternal, 220)}`);
  }

  return {
    summary: summaryLines.join(" "),
    nextAction: activeInvestigation
      ? "Use the engineer workspace if the investigation needs another update, approval, or workflow transition."
      : "Continue monitoring in dashboard or switch to the engineer workspace if the ticket now needs intervention.",
  };
}

function resetTicketDetailState({ clearSelection = true } = {}) {
  if (clearSelection) {
    selectedTicketId = "";
  }
  selectedTicketDetail = null;
  ticketDetailLoading = false;
  ticketDetailError = "";
  ticketDetailSummary = "";
  ticketDetailNextAction = "";
  ticketDetailSummaryLoading = false;
  ticketDetailSummaryFailed = false;
  ticketDetailSummaryModel = "";
  ticketDetailRuntimeExpanded = false;
  ticketDetailExpandedSubTicketIds = new Set();
}

function buildDefinitionGrid(items) {
  const safeItems = (Array.isArray(items) ? items : []).filter(
    (item) => normalizeString(item?.value) && normalizeString(item?.value) !== "-"
  );
  if (!safeItems.length) {
    return '<div class="detail-empty-state compact">No structured fields available yet.</div>';
  }
  return `
    <div class="definition-grid">
      ${safeItems
        .map(
          (item) => `
            <div class="definition-item">
              <span class="definition-label">${escapeHtml(item.label || "-")}</span>
              <span class="definition-value">${escapeHtml(item.value || "-")}</span>
            </div>
          `
        )
        .join("")}
    </div>
  `;
}

function buildTokenUsagePanel(tokenUsage) {
  const usage = tokenUsage && typeof tokenUsage === "object" ? tokenUsage : {};
  if (!Object.keys(usage).length) {
    return '<div class="detail-empty-state">No token usage is available for this ticket family yet.</div>';
  }
  return `
    ${buildDefinitionGrid([
      { label: "canonical_ticket_id", value: normalizeString(usage.canonical_ticket_id) || "-" },
      { label: "related_ticket_ids", value: Array.isArray(usage.related_ticket_ids) ? usage.related_ticket_ids.join(", ") : "-" },
      { label: "total_input_tokens", value: formatNumber(usage.total_input_tokens) },
      { label: "total_output_tokens", value: formatNumber(usage.total_output_tokens) },
      { label: "total_embedding_tokens", value: formatNumber(usage.total_embedding_tokens) },
    ])}
    ${
      Array.isArray(usage.token_by_model) && usage.token_by_model.length
        ? `<p class="detail-note">token_by_model: ${escapeHtml(JSON.stringify(usage.token_by_model))}</p>`
        : `<p class="detail-note">token_by_model: []</p>`
    }
  `;
}

function buildClientAgentRuntimeSummaryCard(agentLabel, agentSummary) {
  const summary = agentSummary && typeof agentSummary === "object" ? agentSummary : {};
  const decision = normalizeString(summary.decision);
  const reason = normalizeString(summary.reason);
  return `
    <article class="ticket-detail-runtime-agent-row">
      <div class="ticket-detail-runtime-agent-copy">
        <strong>${escapeHtml(agentLabel)}</strong>
        ${reason ? `<p>${escapeHtml(reason)}</p>` : ""}
      </div>
      <div class="ticket-detail-runtime-agent-meta">
        <span>${escapeHtml(humanizeToken(summary.phase || "queued"))}</span>
        <span>${escapeHtml(humanizeToken(summary.status || "queued"))}</span>
        ${decision ? `<span>${escapeHtml(humanizeToken(decision))}</span>` : ""}
        <span>${escapeHtml(formatDateTime(summary.updated_at))}</span>
      </div>
    </article>
  `;
}

function buildClientAgentRuntimePanel(ticket) {
  const runtimeState = ticket?.client_agent_runtime_state && typeof ticket.client_agent_runtime_state === "object"
    ? ticket.client_agent_runtime_state
    : null;
  if (!runtimeState) {
    return '<div class="detail-empty-state">No client agent runtime snapshot is available for this ticket yet.</div>';
  }

  return `
    ${buildDefinitionGrid([
      { label: "Run Id", value: normalizeString(runtimeState.active_run_id) || "-" },
      { label: "Runtime Status", value: humanizeToken(runtimeState.status || "queued") },
      { label: "Workflow Action", value: humanizeToken(runtimeState.workflow_action || "pending") },
      { label: "Product", value: humanizeToken(runtimeState.product || "-") },
      { label: "Message Id", value: normalizeString(runtimeState.message_id) || "-" },
      { label: "Updated", value: formatDateTime(runtimeState.updated_at) },
      { label: "Completed", value: formatDateTime(runtimeState.completed_at) },
    ])}
    <div class="ticket-detail-runtime-agent-list">
      ${buildClientAgentRuntimeSummaryCard("Main Agent", runtimeState.main_agent)}
      ${buildClientAgentRuntimeSummaryCard("Route Agent", runtimeState.route_agent)}
      ${buildClientAgentRuntimeSummaryCard("RAG Agent", runtimeState.rag_agent)}
      ${buildClientAgentRuntimeSummaryCard("Review Agent", runtimeState.review_agent)}
    </div>
  `;
}

function buildClientAgentEventCard(agentEvent) {
  const payload = agentEvent?.payload && typeof agentEvent.payload === "object" ? agentEvent.payload : {};
  const payloadJson = JSON.stringify(payload);
  return `
    <article class="ticket-detail-message">
      <header class="ticket-detail-message-header">
        <span class="ticket-detail-message-role">${escapeHtml(humanizeToken(agentEvent?.agent_name || "agent"))}</span>
        <div class="ticket-detail-message-meta">
          <span class="ticket-detail-message-time">${escapeHtml(formatDateTime(agentEvent?.created_at))}</span>
        </div>
      </header>
      ${buildDefinitionGrid([
        { label: "Run Id", value: normalizeString(agentEvent?.run_id) || "-" },
        { label: "Phase", value: humanizeToken(agentEvent?.phase || "queued") },
        { label: "Event", value: humanizeToken(agentEvent?.event_type || "unknown") },
        { label: "Message Id", value: normalizeString(agentEvent?.message_id) || "-" },
      ])}
      ${payloadJson && payloadJson !== "{}" ? `<p class="detail-note">Payload: ${escapeHtml(payloadJson)}</p>` : ""}
    </article>
  `;
}

function buildClientAgentEventsPanel(ticket) {
  const agentEvents = Array.isArray(ticket?.client_agent_events) ? ticket.client_agent_events : [];
  if (!agentEvents.length) {
    return '<div class="detail-empty-state">No agent events have been recorded for this ticket yet.</div>';
  }
  return `<div class="ticket-detail-message-list">${agentEvents.map(buildClientAgentEventCard).join("")}</div>`;
}

function subTicketIdentifier(subTicket) {
  return normalizeString(subTicket?.engineer_case_id) || normalizeString(subTicket?.ticket_id);
}

function subTicketThreadMessages(subTicket) {
  const displayInvestigation = getDisplayInvestigation(subTicket);
  return Array.isArray(displayInvestigation?.messages)
    ? displayInvestigation.messages.filter((item) => item && typeof item === "object")
    : [];
}

function buildSubTicketThreadDisclosure(subTicket) {
  const subTicketId = subTicketIdentifier(subTicket);
  const threadMessages = subTicketThreadMessages(subTicket);
  const isExpanded = ticketDetailExpandedSubTicketIds.has(subTicketId);
  const threadMeta = [
    threadMessages.length ? `${formatNumber(threadMessages.length)} internal messages` : "No internal messages",
    formatDateTime(subTicket?.updated_at || subTicket?.opened_at || subTicket?.created_at),
  ].filter((value) => normalizeString(value) && normalizeString(value) !== "-");

  return `
    <button
      type="button"
      class="sub-ticket-thread-toggle ${isExpanded ? "is-expanded" : ""}"
      data-sub-ticket-thread-toggle
      data-sub-ticket-id="${escapeHtml(subTicketId)}"
      aria-expanded="${isExpanded ? "true" : "false"}"
    >
      <div class="sub-ticket-thread-toggle-copy">
        <p class="eyebrow">Internal Thread</p>
        <h4>Internal Thread</h4>
        <p>Collapsed by default. Expand only when you need the engineer-side conversation.</p>
      </div>
      <div class="sub-ticket-thread-meta">
        ${threadMeta.map((value) => `<span class="sub-ticket-pill">${escapeHtml(value)}</span>`).join("")}
        <span class="material-symbols-outlined sub-ticket-thread-icon" aria-hidden="true">expand_more</span>
      </div>
    </button>
    <div class="sub-ticket-thread-body" ${isExpanded ? "" : "hidden"}>
      ${
        threadMessages.length
          ? `<div class="ticket-detail-message-list">${threadMessages.map(buildTicketDetailMessageCard).join("")}</div>`
          : '<div class="detail-empty-state compact">No internal thread messages have been recorded for this sub ticket yet.</div>'
      }
    </div>
  `;
}

function buildSubTicketCard(subTicket) {
  const subTicketId = subTicketIdentifier(subTicket) || "-";
  const displayInvestigation = getDisplayInvestigation(subTicket);
  const latestInternalUpdate = normalizeString(latestInvestigationUpdate(subTicket));
  const activeStateLabel = investigationStateLabel(
    displayInvestigation?.state || subTicket?.investigation_state || "active"
  );
  const title = normalizeString(subTicket?.subject || subTicket?.title) || subTicketId;

  return `
    <article class="sub-ticket-card">
      <div class="sub-ticket-card-header">
        <div class="sub-ticket-card-copy">
          <p class="eyebrow">Sub Ticket</p>
          <h4>${escapeHtml(subTicketId)}</h4>
          <p>${escapeHtml(title)}</p>
        </div>
        <div class="sub-ticket-card-badges">
          <span class="status-badge ${statusClass(subTicket?.status || "investigating")}">${escapeHtml(
            statusLabel(subTicket?.status || "investigating")
          )}</span>
          <span class="sub-ticket-pill">${escapeHtml(activeStateLabel)}</span>
        </div>
      </div>

      ${buildDefinitionGrid([
        { label: "Trigger Reason", value: humanizeToken(subTicket?.trigger_reason || "unknown") },
        { label: "Trigger Source", value: humanizeToken(subTicket?.trigger_source || "unknown") },
        { label: "Opened", value: formatDateTime(subTicket?.opened_at || subTicket?.created_at) },
        { label: "Updated", value: formatDateTime(subTicket?.updated_at) },
        { label: "Closed", value: subTicket?.closed_at ? formatDateTime(subTicket.closed_at) : "Still open" },
      ])}

      ${
        latestInternalUpdate
          ? `
            <div class="sub-ticket-latest-update">
              <span class="sub-ticket-latest-update-label">Latest Internal Update</span>
              <p>${escapeHtml(truncateText(latestInternalUpdate, 360))}</p>
            </div>
          `
          : ""
      }

      ${buildSubTicketThreadDisclosure(subTicket)}
    </article>
  `;
}

function buildLinkedSubTicketsSection(ticket) {
  const subTickets = ticketSubTickets(ticket);
  if (!subTickets.length) {
    return "";
  }

  const activeSubTicketCount = Number(ticket?.active_sub_ticket_count || 0);
  const latestUpdate = latestLinkedSubTicketUpdate(ticket);
  const sectionChip = activeSubTicketCount > 0
    ? `${formatNumber(activeSubTicketCount)} active / ${formatNumber(subTickets.length)} total`
    : `${formatNumber(subTickets.length)} total`;

  return `
    <section class="panel-card detail-panel">
      <div class="panel-header">
        <div>
          <h3>Linked Sub Tickets</h3>
          <p>Engineer-side cases attached to this client ticket. Active cases stay first, and each internal thread is collapsed by default.</p>
        </div>
        <span class="section-chip">${escapeHtml(sectionChip)}</span>
      </div>
      ${
        latestUpdate
          ? `<p class="detail-note">Latest linked update: ${escapeHtml(truncateText(latestUpdate, 320))}</p>`
          : ""
      }
      <div class="linked-sub-ticket-list">${subTickets.map(buildSubTicketCard).join("")}</div>
    </section>
  `;
}

function buildTicketSummaryPanel(fallbackSummary) {
  const canonicalSummary = normalizeString(ticketDetailSummary);
  const canonicalNextAction = normalizeString(ticketDetailNextAction);
  const summaryText = canonicalSummary || fallbackSummary.summary;
  const nextActionText = canonicalNextAction || fallbackSummary.nextAction;
  const summarySourceLabel = ticketDetailSummaryLoading
    ? "Loading summary"
    : canonicalSummary
      ? "Canonical summary"
      : "Dashboard fallback";
  const sourceToneClass = ticketDetailSummaryLoading
    ? "is-loading"
    : canonicalSummary
      ? "is-canonical"
      : "is-fallback";

  let bodyHtml = "";
  if (ticketDetailSummaryLoading && !canonicalSummary) {
    bodyHtml = `
      <div class="detail-summary-state" role="status" aria-live="polite" aria-busy="true">
        <span class="loading-spinner" aria-hidden="true"></span>
        <p>Generating the latest engineer-aligned summary for this ticket.</p>
      </div>
    `;
  } else {
    bodyHtml = `
      <p class="detail-summary-copy detail-summary-copy-strong">${escapeHtml(summaryText || "-")}</p>
      <div class="detail-summary-next-block">
        <p class="detail-summary-next-label">Next Action Needed</p>
        <p class="detail-note">${escapeHtml(nextActionText || "-")}</p>
      </div>
    `;
  }

  return `
    <section class="panel-card detail-panel detail-summary-panel">
      <div class="panel-header">
        <div>
          <p class="eyebrow">Ticket Summary</p>
          <h3>Ticket Summary</h3>
          <p>${
            canonicalSummary
              ? "Canonical engineer summary from the shared summary endpoint."
              : ticketDetailSummaryFailed
                ? "Canonical summary is unavailable, so this section falls back to the dashboard summary."
                : "The summary loads independently so the rest of the detail view stays available."
          }</p>
        </div>
        <span class="detail-summary-chip ${sourceToneClass}">${escapeHtml(summarySourceLabel)}</span>
      </div>
      ${bodyHtml}
      ${
        canonicalSummary && normalizeString(ticketDetailSummaryModel)
          ? `<p class="detail-note">Model: ${escapeHtml(ticketDetailSummaryModel)}</p>`
          : ""
      }
    </section>
  `;
}

function buildTicketRuntimeDisclosure(ticket) {
  const runtimeState = ticket?.client_agent_runtime_state && typeof ticket.client_agent_runtime_state === "object"
    ? ticket.client_agent_runtime_state
    : null;
  const agentEvents = Array.isArray(ticket?.client_agent_events) ? ticket.client_agent_events : [];
  const runtimeMeta = runtimeState
    ? [
        humanizeToken(runtimeState.status || "queued"),
        humanizeToken(runtimeState.workflow_action || "pending"),
        formatDateTime(runtimeState.updated_at),
      ]
    : [
        agentEvents.length ? `${formatNumber(agentEvents.length)} events` : "No runtime snapshot",
      ];

  return `
    <section class="panel-card detail-panel ticket-detail-runtime-disclosure ${
      ticketDetailRuntimeExpanded ? "is-expanded" : ""
    }">
      <button
        type="button"
        class="ticket-detail-runtime-toggle"
        data-ticket-detail-runtime-toggle
        aria-expanded="${ticketDetailRuntimeExpanded ? "true" : "false"}"
      >
        <div class="ticket-detail-runtime-toggle-copy">
          <p class="eyebrow">Client Agent Runtime</p>
          <h3>Client Agent Runtime</h3>
          <p>Collapsed by default. Expand only when you need the runtime snapshot and recent agent events.</p>
        </div>
        <div class="ticket-detail-runtime-toggle-meta">
          ${runtimeMeta
            .filter((value) => normalizeString(value) && normalizeString(value) !== "-")
            .map((value) => `<span class="ticket-detail-runtime-toggle-item">${escapeHtml(value)}</span>`)
            .join("")}
          <span class="material-symbols-outlined ticket-detail-runtime-icon" aria-hidden="true">expand_more</span>
        </div>
      </button>
      <div class="ticket-detail-runtime-body" ${ticketDetailRuntimeExpanded ? "" : "hidden"}>
        <div class="ticket-detail-runtime-section">
          <div class="panel-header">
            <div>
              <h3>Runtime Snapshot</h3>
              <p>Lean view of the current run, workflow action, and per-agent phase.</p>
            </div>
          </div>
          ${buildClientAgentRuntimePanel(ticket)}
        </div>
        <div class="ticket-detail-runtime-section">
          <div class="panel-header">
            <div>
              <h3>Recent Agent Events</h3>
              <p>Latest append-only runtime events captured for route, RAG, review, and main-agent decisions.</p>
            </div>
          </div>
          ${buildClientAgentEventsPanel(ticket)}
        </div>
      </div>
    </section>
  `;
}

function renderBreakdownList(element, items) {
  if (!element) {
    return;
  }

  const safeItems = Array.isArray(items) ? items : [];
  if (!safeItems.length) {
    element.innerHTML = '<li class="breakdown-empty">No live signal yet.</li>';
    return;
  }

  const maxValue = safeItems.reduce((largest, item) => Math.max(largest, Number(item?.value || 0)), 0);
  element.innerHTML = safeItems
    .map((item) => {
      const label = normalizeString(item?.label) || "-";
      const value = Number(item?.value || 0);
      const meterWidth = maxValue > 0 ? Math.max((value / maxValue) * 100, value > 0 ? 14 : 0) : 0;

      return `
        <li class="breakdown-item">
          <div class="breakdown-row">
            <span class="breakdown-label">${escapeHtml(label)}</span>
            <span class="breakdown-value">${escapeHtml(formatNumber(value))}</span>
          </div>
          <div class="breakdown-meter" aria-hidden="true">
            <span class="breakdown-meter-fill" style="width: ${meterWidth}%;"></span>
          </div>
        </li>
      `;
    })
    .join("");
}

function renderEventVolumeBars(points) {
  if (!eventVolumeBarsEl) {
    return;
  }

  const safePoints = Array.isArray(points) ? points : [];
  if (!safePoints.length) {
    eventVolumeBarsEl.innerHTML = '<p class="throughput-empty">No event volume yet.</p>';
    return;
  }

  const maxValue = safePoints.reduce((largest, item) => Math.max(largest, Number(item?.value || 0)), 0);
  eventVolumeBarsEl.innerHTML = safePoints
    .map((point) => {
      const label = normalizeString(point?.label) || "--";
      const value = Number(point?.value || 0);
      const height = maxValue > 0 ? Math.round((value / maxValue) * 100) : 0;

      return `
        <div class="throughput-bar ${value === 0 ? "is-empty" : ""}">
          <span class="throughput-bar-value">${escapeHtml(formatNumber(value))}</span>
          <div class="throughput-bar-track" aria-hidden="true">
            <span
              class="throughput-bar-fill"
              style="height: ${height}%; ${value > 0 ? "min-height: 14px;" : ""}"
            ></span>
          </div>
          <span class="throughput-bar-label timestamp">${escapeHtml(label)}</span>
        </div>
      `;
    })
    .join("");
}

function renderRailNav() {
  const statusViewActive = TICKET_DETAIL_STATUSES.includes(currentDashboardView);

  ticketOpsButtonEl?.classList.toggle("is-active", currentDashboardView === "ticket-ops");
  ticketOpsButtonEl?.setAttribute("aria-pressed", currentDashboardView === "ticket-ops" ? "true" : "false");

  ticketDetailGroupEl?.classList.toggle("is-active", statusViewActive);
  ticketDetailGroupEl?.classList.toggle("is-expanded", ticketDetailsExpanded);
  ticketDetailGroupToggleEl?.classList.toggle("is-active", statusViewActive);
  ticketDetailGroupToggleEl?.setAttribute("aria-expanded", ticketDetailsExpanded ? "true" : "false");
  ticketDetailSubnavEl.hidden = !ticketDetailsExpanded;

  ticketDetailStatusButtons.forEach((button) => {
    const status = normalizeStatusValue(button.dataset.ticketDetailStatus || "");
    const isActive = currentDashboardView === status;
    button.classList.toggle("is-active", isActive);
    button.setAttribute("aria-pressed", isActive ? "true" : "false");
  });
}

function renderDashboardView() {
  const isTicketOpsView = currentDashboardView === "ticket-ops";

  if (dashboardViewRegionEl) {
    dashboardViewRegionEl.dataset.activeView = currentDashboardView;
  }
  if (opsHeaderEl) {
    opsHeaderEl.hidden = !isTicketOpsView;
  }
  if (opsHeaderBodyEl) {
    opsHeaderBodyEl.textContent = isTicketOpsView
      ? DEFAULT_HEADER_BODY
      : TICKET_VIEW_COPY[currentDashboardView]?.detail || DEFAULT_HEADER_BODY;
  }
  if (ticketOpsOverviewEl) {
    ticketOpsOverviewEl.hidden = !isTicketOpsView;
    ticketOpsOverviewEl.classList.toggle("is-active", isTicketOpsView);
  }
  if (ticketBoardRegionEl) {
    ticketBoardRegionEl.hidden = isTicketOpsView;
  }

  if (!isTicketOpsView) {
    renderTicketBoard();
  }
}

function describeTicketBoardTicket(ticket) {
  const ticketId = String(ticket?.ticket_id || "-");
  const status = normalizeStatusValue(ticket?.status || currentDashboardView);
  const requester = ticketRequester(ticket);
  const latestCustomer = latestTicketMessage(ticket, ["customer"]);
  const latestAssistant = latestTicketMessage(ticket, ["assistant"]);
  const subTicketPreview = latestLinkedSubTicketUpdate(ticket);
  const investigationPreview = subTicketPreview || latestInvestigationUpdate(ticket);
  const latestSentiment = normalizeSentimentLabel(latestCustomer?.sentiment_label);

  let previewLabel = "Latest Update";
  let previewValue = "No recent ticket update recorded yet.";
  if (subTicketPreview) {
    previewLabel = "Latest Sub Ticket Update";
    previewValue = truncateText(subTicketPreview, 220);
  } else if (investigationPreview) {
    previewLabel = "Latest Investigation Update";
    previewValue = truncateText(investigationPreview, 220);
  } else if (latestCustomer?.content) {
    previewLabel = "Latest Customer Message";
    previewValue = truncateText(latestCustomer.content, 220);
  } else if (latestAssistant?.content) {
    previewLabel = "Latest AI Reply";
    previewValue = truncateText(latestAssistant.content, 220);
  }

  return {
    ticketId,
    subject: ticketSubject(ticket),
    requester,
    status,
    updatedAt: formatDateTime(ticket?.updated_at),
    createdAt: formatDateTime(ticket?.created_at),
    previewLabel,
    previewValue,
    sentiment: latestSentiment,
    surfaceClass: statusSurfaceClass(status),
  };
}

function ticketBoardViewToggleIcon(mode) {
  if (mode === "grid") {
    return `
      <svg class="ticket-board-view-toggle-icon" viewBox="0 0 20 20" fill="none" aria-hidden="true">
        <rect x="3" y="3" width="5" height="5" rx="1.3" stroke="currentColor" stroke-width="1.4"></rect>
        <rect x="12" y="3" width="5" height="5" rx="1.3" stroke="currentColor" stroke-width="1.4"></rect>
        <rect x="3" y="12" width="5" height="5" rx="1.3" stroke="currentColor" stroke-width="1.4"></rect>
        <rect x="12" y="12" width="5" height="5" rx="1.3" stroke="currentColor" stroke-width="1.4"></rect>
      </svg>
    `;
  }
  return `
    <svg class="ticket-board-view-toggle-icon" viewBox="0 0 20 20" fill="none" aria-hidden="true">
      <path d="M4 5H16" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"></path>
      <path d="M4 10H16" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"></path>
      <path d="M4 15H16" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"></path>
    </svg>
  `;
}

function buildTicketBoardViewToggleHtml() {
  const viewMode = normalizeTicketBoardViewMode(ticketBoardViewMode);
  return `
    <div class="ticket-board-view-toggle" role="group" aria-label="Ticket board layout">
      <button
        type="button"
        class="ticket-board-view-toggle-btn ${viewMode === "list" ? "is-active" : ""}"
        data-ticket-board-view-option="list"
        aria-label="List view"
        title="List view"
        aria-pressed="${viewMode === "list" ? "true" : "false"}"
      >
        ${ticketBoardViewToggleIcon("list")}
        <span class="sr-only">List view</span>
      </button>
      <button
        type="button"
        class="ticket-board-view-toggle-btn ${viewMode === "grid" ? "is-active" : ""}"
        data-ticket-board-view-option="grid"
        aria-label="Grid view"
        title="Grid view"
        aria-pressed="${viewMode === "grid" ? "true" : "false"}"
      >
        ${ticketBoardViewToggleIcon("grid")}
        <span class="sr-only">Grid view</span>
      </button>
    </div>
  `;
}

function renderTicketBoardCard(ticket) {
  const item = describeTicketBoardTicket(ticket);
  return `
    <article
      class="ticket-board-card ${item.surfaceClass}"
      role="button"
      tabindex="0"
      data-ticket-card="true"
      data-ticket-id="${escapeHtml(item.ticketId)}"
      aria-label="Open ticket ${escapeHtml(item.ticketId)} detail"
    >
      <div class="ticket-board-card-top">
        <div class="ticket-board-card-headline">
          <p class="ticket-card-kicker timestamp">${escapeHtml(item.ticketId)}</p>
          <h3 class="ticket-board-card-title">${escapeHtml(item.subject)}</h3>
        </div>
        <div class="ticket-board-card-badges">
          <span class="status-badge ${statusClass(item.status)}">${escapeHtml(statusLabel(item.status))}</span>
          ${
            item.sentiment
              ? `<span class="message-sentiment-pill sentiment-${escapeHtml(item.sentiment)}">${escapeHtml(
                  humanizeToken(item.sentiment)
                )}</span>`
              : ""
          }
        </div>
      </div>

      <div class="ticket-board-card-meta">
        <span><strong>Requester</strong> ${escapeHtml(item.requester)}</span>
        <span><strong>Updated</strong> ${escapeHtml(item.updatedAt)}</span>
        <span><strong>Created</strong> ${escapeHtml(item.createdAt)}</span>
      </div>

      <div class="ticket-board-card-preview">
        <span class="ticket-board-card-preview-label">${escapeHtml(item.previewLabel)}</span>
        <p>${escapeHtml(item.previewValue)}</p>
      </div>
    </article>
  `;
}

function renderTicketBoardGrid(boardRows) {
  return `
    <section class="ticket-board-grid" role="list">
      ${boardRows.map(renderTicketBoardCard).join("")}
    </section>
  `;
}

function renderTicketBoardListRow(ticket) {
  const item = describeTicketBoardTicket(ticket);
  return `
    <article
      class="ticket-board-row ${item.surfaceClass}"
      role="button"
      tabindex="0"
      data-ticket-card="true"
      data-ticket-id="${escapeHtml(item.ticketId)}"
      aria-label="Open ticket ${escapeHtml(item.ticketId)} detail"
    >
      <div class="ticket-board-row-main">
        <div class="ticket-board-row-top">
          <div class="ticket-board-row-headline">
            <p class="ticket-card-kicker timestamp">${escapeHtml(item.ticketId)}</p>
            <h3 class="ticket-board-row-title">${escapeHtml(item.subject)}</h3>
          </div>
          <div class="ticket-board-row-badges">
            <span class="status-badge ${statusClass(item.status)}">${escapeHtml(statusLabel(item.status))}</span>
            ${
              item.sentiment
                ? `<span class="message-sentiment-pill sentiment-${escapeHtml(item.sentiment)}">${escapeHtml(
                    humanizeToken(item.sentiment)
                  )}</span>`
                : ""
            }
          </div>
        </div>

        <div class="ticket-board-row-meta">
          <span><strong>Requester</strong> ${escapeHtml(item.requester)}</span>
          <span><strong>Updated</strong> ${escapeHtml(item.updatedAt)}</span>
          <span><strong>Created</strong> ${escapeHtml(item.createdAt)}</span>
        </div>
      </div>

      <div class="ticket-board-row-preview">
        <span class="ticket-board-card-preview-label">${escapeHtml(item.previewLabel)}</span>
        <p>${escapeHtml(item.previewValue)}</p>
      </div>
    </article>
  `;
}

function renderTicketBoardList(boardRows) {
  return `
    <section class="ticket-board-list" role="list">
      ${boardRows.map(renderTicketBoardListRow).join("")}
    </section>
  `;
}

function renderTicketBoard() {
  if (!ticketBoardRegionEl || !TICKET_DETAIL_STATUSES.includes(currentDashboardView)) {
    return;
  }

  const boardStatus = currentDashboardView;
  const viewMode = normalizeTicketBoardViewMode(ticketBoardViewMode);
  const boardRows = Array.isArray(ticketBoardStore[boardStatus]) ? ticketBoardStore[boardStatus] : [];
  const boardLoading = Boolean(ticketBoardLoadingByStatus[boardStatus]);
  const boardError = normalizeString(ticketBoardErrorByStatus[boardStatus]);
  const viewCopy = TICKET_VIEW_COPY[boardStatus] || {
    title: `${statusLabel(boardStatus)} Tickets`,
    detail: "Ticket board",
    summary: "Ticket board",
  };

  let boardContent = "";
  if (boardLoading && !boardRows.length) {
    boardContent = `
      <div class="ticket-board-empty" role="status" aria-live="polite" aria-busy="true">
        <span class="loading-spinner" aria-hidden="true"></span>
        <strong>Loading ${escapeHtml(statusLabel(boardStatus).toLowerCase())} tickets.</strong>
        <p>${escapeHtml(viewCopy.detail)}</p>
      </div>
    `;
  } else if (boardError && !boardRows.length) {
    boardContent = `
      <div class="ticket-board-empty">
        <strong>Unable to load ${escapeHtml(statusLabel(boardStatus).toLowerCase())} tickets.</strong>
        <p>${escapeHtml(boardError)}</p>
      </div>
    `;
  } else if (!boardRows.length) {
    boardContent = `
      <div class="ticket-board-empty">
        <strong>No ${escapeHtml(statusLabel(boardStatus).toLowerCase())} tickets right now.</strong>
        <p>${escapeHtml(viewCopy.summary)}</p>
      </div>
    `;
  } else {
    boardContent = viewMode === "list" ? renderTicketBoardList(boardRows) : renderTicketBoardGrid(boardRows);
  }

  ticketBoardRegionEl.innerHTML = `
    <section
      class="ticket-board"
      data-ticket-board-status="${escapeHtml(boardStatus)}"
      data-ticket-board-view-mode="${escapeHtml(viewMode)}"
    >
      <div class="section-head ticket-board-head">
        <div>
          <p class="eyebrow">Ticket Details</p>
          <h2>${escapeHtml(viewCopy.title)}</h2>
          <p>${escapeHtml(viewCopy.detail)}</p>
        </div>
        <div class="ticket-board-head-controls">
          ${buildTicketBoardViewToggleHtml()}
          <span class="section-chip">${escapeHtml(statusLabel(boardStatus))} Board</span>
        </div>
      </div>

      <div class="ticket-board-summary">
        <strong class="ticket-board-count">${escapeHtml(formatNumber(boardRows.length))}</strong>
        <p>${escapeHtml(viewCopy.summary)}</p>
      </div>

      ${boardError && boardRows.length ? `<p class="detail-note">${escapeHtml(boardError)}</p>` : ""}
      ${boardContent}
    </section>
  `;
}

function buildTicketDetailMessageCard(message) {
  const role = String(message?.role || "system").toLowerCase();
  const sentimentLabel = normalizeSentimentLabel(message?.sentiment_label);
  return `
    <article class="ticket-detail-message ${role === "customer" ? "is-customer" : ""}">
      <header class="ticket-detail-message-header">
        <span class="ticket-detail-message-role">${escapeHtml(roleLabel(role))}</span>
        <div class="ticket-detail-message-meta">
          ${
            sentimentLabel
              ? `<span class="message-sentiment-pill sentiment-${escapeHtml(sentimentLabel)}">${escapeHtml(
                  humanizeToken(sentimentLabel)
                )}</span>`
              : ""
          }
          <span class="ticket-detail-message-time">${escapeHtml(formatDateTime(message?.created_at))}</span>
        </div>
      </header>
      <div class="ticket-detail-message-content">${escapeHtml(normalizeString(message?.content) || "-")}</div>
    </article>
  `;
}

function renderTicketDetail() {
  if (!ticketDetailBodyEl) {
    return;
  }

  if (ticketDetailLoading) {
    ticketDetailBodyEl.innerHTML = `
      <div class="ticket-board-empty" role="status" aria-live="polite" aria-busy="true">
        <span class="loading-spinner" aria-hidden="true"></span>
        <strong>Loading ticket detail.</strong>
        <p>Pulling the latest support and investigation context.</p>
      </div>
    `;
    return;
  }

  if (ticketDetailError) {
    ticketDetailBodyEl.innerHTML = `
      <div class="ticket-board-empty">
        <strong>Unable to load this ticket.</strong>
        <p>${escapeHtml(ticketDetailError)}</p>
      </div>
    `;
    return;
  }

  if (!selectedTicketDetail) {
    ticketDetailBodyEl.innerHTML = `
      <div class="ticket-board-empty">
        <strong>Select a ticket to inspect its context.</strong>
        <p>The dashboard overlay stays read-only so actions still happen in the engineer workspace.</p>
      </div>
    `;
    return;
  }

  const ticket = selectedTicketDetail;
  const ticketId = String(ticket.ticket_id || selectedTicketId || "-");
  const status = normalizeStatusValue(ticket.status || "open");
  const fallbackSummary = buildLocalSummaryFallback(ticket);
  const latestAssistant = latestTicketMessage(ticket, ["assistant"]);
  const detailMessages = Array.isArray(ticket.messages) ? ticket.messages.slice(-4) : [];
  const requester = ticketRequester(ticket);
  const linkedSubTickets = ticketSubTickets(ticket);
  const linkedSubTicketCount = Number(ticket?.linked_sub_ticket_count || linkedSubTickets.length || 0);
  const activeSubTicketCount = Number(ticket?.active_sub_ticket_count || 0);
  const heroCopy = linkedSubTicketCount
    ? `Read-only client ticket context from the dashboard. Review the root ticket first, then inspect ${linkedSubTicketCount} linked sub ticket${linkedSubTicketCount === 1 ? "" : "s"} below before changing the workflow in the engineer workspace.`
    : "Read-only ticket context from the dashboard. Switch to the engineer workspace if you need to change the workflow.";

  ticketDetailTitleEl.textContent = `${ticketId} detail`;
  ticketDetailBodyEl.innerHTML = `
    <div class="detail-panel-stack">
      <section class="panel-card detail-panel ticket-detail-hero">
        <div class="panel-header">
          <div>
            <p class="eyebrow">Ticket Detail</p>
            <h3 class="ticket-detail-hero-title">${escapeHtml(ticketSubject(ticket))}</h3>
            <p>${escapeHtml(heroCopy)}</p>
          </div>
          <span class="status-badge ${statusClass(status)}">${escapeHtml(statusLabel(status))}</span>
        </div>
        ${buildDefinitionGrid([
          { label: "Ticket Id", value: ticketId },
          { label: "Requester", value: requester },
          { label: "Created", value: formatDateTime(ticket.created_at) },
          { label: "Updated", value: formatDateTime(ticket.updated_at) },
          { label: "Linked Sub Tickets", value: formatNumber(linkedSubTicketCount) },
          { label: "Active Sub Tickets", value: formatNumber(activeSubTicketCount) },
        ])}
      </section>

      ${buildTicketSummaryPanel(fallbackSummary)}

      <section class="panel-card detail-panel">
        <div class="panel-header">
          <div>
            <h3>Token Usage</h3>
            <p>Canonical ticket-family token summary for this client ticket and any related engineer case ids.</p>
          </div>
        </div>
        ${buildTokenUsagePanel(ticket.token_usage)}
      </section>

      <section class="panel-card detail-panel">
        <div class="panel-header">
          <div>
            <h3>Conversation Snapshot</h3>
            <p>Recent customer-visible messages from the support timeline.</p>
          </div>
        </div>
        ${
          detailMessages.length
            ? `<div class="ticket-detail-message-list">${detailMessages
                .map(buildTicketDetailMessageCard)
                .join("")}</div>`
            : '<div class="detail-empty-state">No customer-visible conversation is available yet.</div>'
        }
        ${
          latestAssistant?.content
            ? `<p class="detail-note">Latest AI reply: ${escapeHtml(truncateText(latestAssistant.content, 280))}</p>`
            : ""
        }
      </section>

      ${buildLinkedSubTicketsSection(ticket)}

      ${buildTicketRuntimeDisclosure(ticket)}
    </div>
  `;
}

function openTicketDetailModalShell(ticketId) {
  lastTicketDetailFocusEl =
    document.activeElement instanceof HTMLElement ? document.activeElement : lastTicketDetailFocusEl;
  ticketDetailTitleEl.textContent = ticketId ? `${ticketId} detail` : "Ticket detail";
  ticketDetailModalEl.hidden = false;
  ticketDetailModalEl.setAttribute("aria-hidden", "false");
  document.body.classList.add("modal-open");
  window.setTimeout(() => {
    ticketDetailDialogEl?.focus();
  }, 0);
}

function closeTicketDetailModal({ restoreFocus = true } = {}) {
  if (!ticketDetailModalEl || ticketDetailModalEl.hidden) {
    return;
  }

  ticketDetailModalEl.hidden = true;
  ticketDetailModalEl.setAttribute("aria-hidden", "true");
  document.body.classList.remove("modal-open");
  ticketDetailBodyEl.innerHTML = "";
  resetTicketDetailState();
  if (restoreFocus && lastTicketDetailFocusEl) {
    lastTicketDetailFocusEl.focus();
  }
}

function focusableElementsWithin(container) {
  if (!container) {
    return [];
  }
  return Array.from(
    container.querySelectorAll(
      'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'
    )
  ).filter((element) => element instanceof HTMLElement && !element.hidden);
}

function trapTicketDetailFocus(event) {
  if (ticketDetailModalEl.hidden || event.key !== "Tab") {
    return;
  }

  const focusable = focusableElementsWithin(ticketDetailDialogEl);
  if (!focusable.length) {
    event.preventDefault();
    ticketDetailDialogEl?.focus();
    return;
  }

  const first = focusable[0];
  const last = focusable[focusable.length - 1];
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault();
    first.focus();
  }
}

async function loadMetrics() {
  const payload = await fetchJson("/api/dashboard/metrics");
  const cards = payload?.cards || {};
  const summaries = payload?.summaries || {};
  const charts = payload?.charts || {};

  setText(ticketVolumeEl, formatNumber(payload?.today_ticket_count));
  setText(resolutionRateEl, `${formatDecimal(payload?.resolution_rate)}%`);
  setText(sentimentAlertsEl, formatNumber(payload?.sentiment_alert_count));
  setText(waitingForEngineerEl, formatNumber(cards?.investigating_ticket_count));
  setText(queueHealthTitleEl, normalizeString(summaries?.queue_health_label) || "Monitoring live queue balance.");
  setText(
    queueHealthDetailEl,
    normalizeString(summaries?.queue_health_detail) || "Loading the newest queue health summary and throughput pattern."
  );
  setText(openTicketCountEl, formatNumber(cards?.open_ticket_count));
  setText(resolvedTicketCountEl, formatNumber(cards?.resolved_ticket_count));
  setText(communicatingTicketCountEl, formatNumber(cards?.communicating_ticket_count));
  setText(escalatedTicketCountEl, formatNumber(cards?.escalated_ticket_count));
  setText(badSentimentTicketCountEl, formatNumber(cards?.bad_sentiment_ticket_count));
  setText(waitingTicketChipEl, formatNumber(cards?.investigating_ticket_count));
  setText(communicatingTicketChipEl, formatNumber(cards?.communicating_ticket_count));
  setText(escalatedTicketChipEl, formatNumber(cards?.escalated_ticket_count));
  setText(
    escalationWatchTitleEl,
    normalizeString(summaries?.escalation_summary_title) || "Watching live queue pressure."
  );
  setText(
    escalationWatchDetailEl,
    normalizeString(summaries?.escalation_summary_detail) || "Loading the latest escalation signal."
  );
  setText(
    operatorSummaryTitleEl,
    normalizeString(summaries?.operator_summary_title) || "Reading operator workload."
  );
  setText(
    operatorSummaryDetailEl,
    normalizeString(summaries?.operator_summary_detail) || "Loading communicating and escalated balance."
  );

  renderEventVolumeBars(charts?.event_volume_12h);
  renderBreakdownList(statusBreakdownEl, charts?.status_breakdown);
  renderBreakdownList(sentimentBreakdownEl, charts?.sentiment_breakdown);
  renderBreakdownList(flowBreakdownEl, charts?.flow_breakdown);
}

async function loadTicketBoard(status = currentDashboardView) {
  const requestedStatus = normalizeStatusValue(status);
  if (!TICKET_DETAIL_STATUSES.includes(requestedStatus)) {
    return;
  }

  ticketBoardLoadingByStatus[requestedStatus] = true;
  ticketBoardErrorByStatus[requestedStatus] = "";
  if (currentDashboardView === requestedStatus) {
    renderTicketBoard();
  }

  try {
    const params = new URLSearchParams({ status: requestedStatus });
    const payload = await fetchJson(`/api/dashboard/tickets?${params.toString()}`);
    ticketBoardStore[requestedStatus] = Array.isArray(payload?.tickets) ? payload.tickets : [];
  } catch (error) {
    ticketBoardErrorByStatus[requestedStatus] = `Failed to load tickets: ${error.message}`;
  } finally {
    ticketBoardLoadingByStatus[requestedStatus] = false;
    if (currentDashboardView === requestedStatus) {
      renderTicketBoard();
    }
  }
}

async function loadTicketDetail(ticketId, { silent = false } = {}) {
  const requestedTicketId = normalizeString(ticketId);
  if (!requestedTicketId) {
    return;
  }

  if (!silent) {
    ticketDetailLoading = true;
    ticketDetailError = "";
    renderTicketDetail();
  }

  try {
    const payload = await fetchJson(`/api/dashboard/tickets/${encodeURIComponent(requestedTicketId)}`);
    if (selectedTicketId !== requestedTicketId) {
      return;
    }
    selectedTicketDetail = payload?.ticket && typeof payload.ticket === "object" ? payload.ticket : null;
    ticketDetailError = "";
  } catch (error) {
    if (selectedTicketId !== requestedTicketId) {
      return;
    }
    ticketDetailError = `Failed to load ticket detail: ${error.message}`;
  } finally {
    if (selectedTicketId === requestedTicketId) {
      ticketDetailLoading = false;
      renderTicketDetail();
    }
  }
}

async function loadTicketDetailSummary(ticketId, { silent = false } = {}) {
  const requestedTicketId = normalizeString(ticketId);
  if (!requestedTicketId) {
    return;
  }

  if (!silent || !normalizeString(ticketDetailSummary)) {
    ticketDetailSummaryLoading = true;
    ticketDetailSummaryFailed = false;
    if (!ticketDetailLoading) {
      renderTicketDetail();
    }
  }

  try {
    const payload = await fetchJson(
      `/api/dashboard/tickets/${encodeURIComponent(requestedTicketId)}/summary`
    );
    if (selectedTicketId !== requestedTicketId) {
      return;
    }

    const fallbackSummary = selectedTicketDetail ? buildLocalSummaryFallback(selectedTicketDetail) : { nextAction: "" };
    const summary = normalizeString(payload?.summary);
    if (!summary) {
      ticketDetailSummary = "";
      ticketDetailNextAction = "";
      ticketDetailSummaryModel = "";
      ticketDetailSummaryFailed = true;
      return;
    }

    ticketDetailSummary = summary;
    ticketDetailNextAction = normalizeString(payload?.next_action_needed) || fallbackSummary.nextAction;
    ticketDetailSummaryModel = normalizeString(payload?.model);
    ticketDetailSummaryFailed = false;
  } catch {
    if (selectedTicketId !== requestedTicketId) {
      return;
    }
    ticketDetailSummary = "";
    ticketDetailNextAction = "";
    ticketDetailSummaryModel = "";
    ticketDetailSummaryFailed = true;
  } finally {
    if (selectedTicketId === requestedTicketId) {
      ticketDetailSummaryLoading = false;
      if (!ticketDetailLoading) {
        renderTicketDetail();
      }
    }
  }
}

function isTicketEvent(payload) {
  return (
    !normalizeString(payload?.ingestion_id)
    && !normalizeString(payload?.event).toLowerCase().startsWith("knowledge_ingestion_")
  );
}

function stopDashboardSocket() {
  if (heartbeatTimer) {
    clearInterval(heartbeatTimer);
    heartbeatTimer = null;
  }
  if (reconnectTimer) {
    clearTimeout(reconnectTimer);
    reconnectTimer = null;
  }
  if (socket) {
    socket.onclose = null;
    socket.onerror = null;
    socket.onmessage = null;
    socket.close();
    socket = null;
  }
}

function setupWebSocket() {
  stopDashboardSocket();

  const protocol = window.location.protocol === "https:" ? "wss" : "ws";
  socket = new WebSocket(`${protocol}://${window.location.host}/ws/dashboard`);

  socket.onopen = () => {
    setRealtimeStatus("Realtime: connected");
  };

  socket.onclose = () => {
    setRealtimeStatus("Realtime: disconnected (reconnecting...)");
    reconnectTimer = setTimeout(setupWebSocket, 1500);
  };

  socket.onerror = () => {
    setRealtimeStatus("Realtime: error");
  };

  socket.onmessage = async (event) => {
    let payload = null;
    try {
      payload = JSON.parse(event.data);
    } catch {
      return;
    }

    if (!isTicketEvent(payload)) {
      return;
    }

    try {
      await refreshDashboard({ showLoading: false });
    } catch (error) {
      setRealtimeStatus(`Realtime refresh failed: ${error.message}`);
    }
  };

  heartbeatTimer = setInterval(() => {
    if (socket?.readyState === WebSocket.OPEN) {
      socket.send("ping");
    }
  }, 10000);
}

async function handleLogoutClick() {
  if (logoutLoading) {
    return;
  }

  logoutLoading = true;
  renderRailFooter();
  try {
    await fetchJson("/api/v1/auth/logout", { method: "POST" });
    stopDashboardSocket();
    window.location.assign("/login");
    window.location.reload();
  } finally {
    logoutLoading = false;
    renderRailFooter();
  }
}

function setDashboardView(view) {
  const normalizedView = normalizeDashboardView(view);
  if (normalizedView === currentDashboardView && normalizedView === "ticket-ops") {
    renderRailNav();
    renderDashboardView();
    return;
  }

  currentDashboardView = normalizedView;
  if (TICKET_DETAIL_STATUSES.includes(normalizedView)) {
    ticketDetailsExpanded = true;
  }
  closeTicketDetailModal({ restoreFocus: false });
  renderRailNav();
  renderDashboardView();

  if (TICKET_DETAIL_STATUSES.includes(normalizedView)) {
    void loadTicketBoard(normalizedView);
  }
}

function openTicketDetail(ticketId, triggerElement = null) {
  const requestedTicketId = normalizeString(ticketId);
  if (!requestedTicketId) {
    return;
  }

  lastTicketDetailFocusEl = triggerElement instanceof HTMLElement ? triggerElement : null;
  resetTicketDetailState({ clearSelection: false });
  selectedTicketId = requestedTicketId;
  ticketDetailLoading = true;
  ticketDetailSummaryLoading = true;
  openTicketDetailModalShell(requestedTicketId);
  renderTicketDetail();
  void loadTicketDetail(requestedTicketId);
  void loadTicketDetailSummary(requestedTicketId);
}

async function refreshDashboard({ showLoading = true } = {}) {
  if (refreshLoading) {
    return;
  }

  if (showLoading) {
    setRefreshLoading(true);
  } else {
    refreshLoading = true;
  }

  try {
    await loadMetrics();
    if (TICKET_DETAIL_STATUSES.includes(currentDashboardView)) {
      await loadTicketBoard(currentDashboardView);
    }
    if (selectedTicketId && !ticketDetailModalEl.hidden) {
      await Promise.all([
        loadTicketDetail(selectedTicketId, { silent: true }),
        loadTicketDetailSummary(selectedTicketId, { silent: true }),
      ]);
    }
  } finally {
    setRefreshLoading(false);
  }
}

function handleDocumentClick(event) {
  const ticketDetailCloseTarget = event.target.closest("[data-close-ticket-detail]");
  if (ticketDetailCloseTarget) {
    closeTicketDetailModal();
    return;
  }

  const ticketOpsButton = event.target.closest('[data-dashboard-nav="ticket-ops"]');
  if (ticketOpsButton) {
    setDashboardView("ticket-ops");
    return;
  }

  const groupToggleButton = event.target.closest("[data-ticket-detail-group-toggle]");
  if (groupToggleButton) {
    ticketDetailsExpanded = !ticketDetailsExpanded;
    renderRailNav();
    return;
  }

  const statusButton = event.target.closest("[data-ticket-detail-status]");
  if (statusButton) {
    setDashboardView(String(statusButton.dataset.ticketDetailStatus || "investigating"));
    return;
  }

  const boardViewButton = event.target.closest("[data-ticket-board-view-option]");
  if (boardViewButton) {
    applyTicketBoardViewMode(String(boardViewButton.dataset.ticketBoardViewOption || "grid"));
    return;
  }

  const runtimeToggleButton = event.target.closest("[data-ticket-detail-runtime-toggle]");
  if (runtimeToggleButton) {
    ticketDetailRuntimeExpanded = !ticketDetailRuntimeExpanded;
    renderTicketDetail();
    return;
  }

  const subTicketThreadToggleButton = event.target.closest("[data-sub-ticket-thread-toggle]");
  if (subTicketThreadToggleButton) {
    const subTicketId = normalizeString(subTicketThreadToggleButton.dataset.subTicketId);
    if (!subTicketId) {
      return;
    }
    if (ticketDetailExpandedSubTicketIds.has(subTicketId)) {
      ticketDetailExpandedSubTicketIds.delete(subTicketId);
    } else {
      ticketDetailExpandedSubTicketIds.add(subTicketId);
    }
    renderTicketDetail();
    return;
  }

  const ticketCard = event.target.closest("[data-ticket-card]");
  if (ticketCard) {
    openTicketDetail(ticketCard.dataset.ticketId, ticketCard);
  }
}

function handleDocumentKeydown(event) {
  if (event.key === "Escape" && !ticketDetailModalEl.hidden) {
    closeTicketDetailModal();
    return;
  }

  const ticketCard = event.target.closest?.("[data-ticket-card]");
  if (
    ticketCard
    && (event.key === "Enter" || event.key === " ")
  ) {
    event.preventDefault();
    openTicketDetail(ticketCard.dataset.ticketId, ticketCard);
  }
}

async function initializeDashboard() {
  renderRailFooter();
  renderRailNav();
  renderDashboardView();

  refreshButtonEl?.addEventListener("click", () => {
    refreshDashboard().catch((error) => {
      setRealtimeStatus(`Refresh failed: ${error.message}`);
    });
  });
  logoutButtonEl?.addEventListener("click", () => {
    handleLogoutClick().catch((error) => {
      setRealtimeStatus(`Logout failed: ${error.message}`);
    });
  });
  document.addEventListener("click", handleDocumentClick);
  document.addEventListener("keydown", handleDocumentKeydown);
  ticketDetailDialogEl?.addEventListener("keydown", trapTicketDetailFocus);

  try {
    await refreshDashboard();
  } catch (error) {
    setRealtimeStatus(`Dashboard load failed: ${error.message}`);
  }

  setRealtimeStatus("Realtime: connecting...");
  setupWebSocket();
}

window.addEventListener("beforeunload", () => {
  stopDashboardSocket();
});

initializeDashboard().catch((error) => {
  setRealtimeStatus(`Dashboard failed to initialize: ${error.message}`);
});
