const wsStatusEl = document.getElementById("ws-status");
const headerUserControlsEl = document.getElementById("header-user-controls");
const refreshButtonEl = document.getElementById("refresh-button");
const ticketVolumeEl = document.getElementById("ticket-volume");
const resolutionRateEl = document.getElementById("resolution-rate");
const sentimentAlertsEl = document.getElementById("sentiment-alerts");
const waitingForEngineerEl = document.getElementById("waiting-for-engineer");
const queueHealthTitleEl = document.getElementById("queue-health-title");
const queueHealthDetailEl = document.getElementById("queue-health-detail");
const openTicketCountEl = document.getElementById("open-ticket-count");
const resolvedTicketCountEl = document.getElementById("resolved-ticket-count");
const managedTicketCountEl = document.getElementById("managed-ticket-count");
const takeoverTicketCountEl = document.getElementById("takeover-ticket-count");
const urgentTicketCountEl = document.getElementById("urgent-ticket-count");
const waitingTicketChipEl = document.getElementById("waiting-ticket-chip");
const managedTicketChipEl = document.getElementById("managed-ticket-chip");
const takeoverTicketChipEl = document.getElementById("takeover-ticket-chip");
const escalationWatchTitleEl = document.getElementById("escalation-watch-title");
const escalationWatchDetailEl = document.getElementById("escalation-watch-detail");
const operatorSummaryTitleEl = document.getElementById("operator-summary-title");
const operatorSummaryDetailEl = document.getElementById("operator-summary-detail");
const eventVolumeBarsEl = document.getElementById("event-volume-bars");
const statusBreakdownEl = document.getElementById("status-breakdown");
const priorityBreakdownEl = document.getElementById("priority-breakdown");
const modeBreakdownEl = document.getElementById("mode-breakdown");
const eventStreamEl = document.getElementById("event-stream");

const DASHBOARD_USER = {
  username: "admin",
  role: "ADMIN",
};

const EVENT_STREAM_LIMIT = 16;

let socket = null;
let heartbeatTimer = null;
let reconnectTimer = null;
let logoutLoading = false;
let refreshLoading = false;

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
      // Keep fallback error reason.
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
  const date = value ? new Date(value) : null;
  if (!date || Number.isNaN(date.getTime())) {
    return "-";
  }
  return date.toLocaleString();
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

function userInitial(username) {
  const normalized = normalizeString(username);
  return normalized ? normalized[0].toUpperCase() : "A";
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
  refreshButtonEl.textContent = isLoading ? "Refreshing..." : "Refresh Feed";
}

function setRealtimeStatus(text) {
  setText(wsStatusEl, text);
}

function isTicketEvent(payload) {
  return !normalizeString(payload?.ingestion_id) && !normalizeString(payload?.event).toLowerCase().startsWith("knowledge_ingestion_");
}

function eventTone(payload) {
  const priority = normalizeString(payload?.priority).toLowerCase();
  const status = normalizeString(payload?.status).toLowerCase();
  const mode = normalizeString(payload?.engineer_mode).toLowerCase();

  if (priority === "urgent" || priority === "high") {
    return priority;
  }
  if (status === "waiting_for_engineer") {
    return "waiting";
  }
  if (mode === "takeover" || mode === "managed") {
    return mode;
  }
  return "default";
}

function renderHeaderUserControls() {
  if (!headerUserControlsEl) {
    return;
  }

  const role = normalizeString(DASHBOARD_USER.role || "ADMIN").toUpperCase();
  const roleClass = `user-role-${escapeHtml(role.toLowerCase())}`;

  headerUserControlsEl.innerHTML = `
    <div class="user-profile-chip" aria-label="Current user">
      <span class="user-avatar" aria-hidden="true">${escapeHtml(userInitial(DASHBOARD_USER.username))}</span>
      <div class="user-meta">
        <p class="user-name">${escapeHtml(DASHBOARD_USER.username)}</p>
        <p class="user-role ${roleClass}">${escapeHtml(role)}</p>
      </div>
    </div>
    <button
      id="logout-btn"
      class="logout-icon-btn"
      type="button"
      title="Logout"
      aria-label="Logout"
      ${logoutLoading ? "disabled" : ""}
    >
      <svg class="logout-icon" viewBox="0 0 24 24" fill="none" aria-hidden="true">
        <path d="M14 8L18 12L14 16" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"></path>
        <path d="M18 12H9" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"></path>
        <path d="M10 4H7C5.9 4 5 4.9 5 6V18C5 19.1 5.9 20 7 20H10" stroke="currentColor" stroke-width="1.9" stroke-linecap="round"></path>
      </svg>
    </button>
  `;

  document.getElementById("logout-btn")?.addEventListener("click", () => {
    handleLogoutClick().catch((error) => {
      setRealtimeStatus(`Logout failed: ${error.message}`);
    });
  });
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
              style="height: ${height}%; ${value > 0 ? `min-height: 14px;` : ""}"
            ></span>
          </div>
          <span class="throughput-bar-label timestamp">${escapeHtml(label)}</span>
        </div>
      `;
    })
    .join("");
}

function renderEventItem(event) {
  const eventName = humanizeToken(event?.event || "ticket_updated");
  const eventMessage =
    normalizeString(event?.message) || normalizeString(event?.title) || "Ticket activity updated.";
  const ticketId = normalizeString(event?.ticket_id) || "-";
  const status = normalizeString(event?.status);
  const priority = normalizeString(event?.priority);
  const mode = normalizeString(event?.engineer_mode);
  const createdAt = formatDateTime(event?.created_at);
  const tone = eventTone(event);
  const title = ticketId === "-" ? eventName : ticketId;

  return `
    <li class="event-item">
      <div class="event-topline">
        <span class="event-chip event-chip-${escapeHtml(tone)}">${escapeHtml(eventName)}</span>
        <span class="event-time timestamp">${escapeHtml(createdAt)}</span>
      </div>
      <h3 class="event-title">${escapeHtml(title)}</h3>
      <p class="event-copy">${escapeHtml(eventMessage)}</p>
      <div class="event-meta">
        <span class="timestamp">${escapeHtml(ticketId)}</span>
        ${status ? `<span>Status ${escapeHtml(humanizeToken(status))}</span>` : ""}
        ${priority ? `<span>Priority ${escapeHtml(humanizeToken(priority))}</span>` : ""}
        ${mode ? `<span>Mode ${escapeHtml(humanizeToken(mode))}</span>` : ""}
      </div>
    </li>
  `;
}

function renderEventStream(events) {
  if (!eventStreamEl) {
    return;
  }

  const items = (Array.isArray(events) ? events : []).filter(isTicketEvent);
  if (!items.length) {
    eventStreamEl.innerHTML = '<li class="event-empty">No ticket events yet. New dashboard traffic will appear here.</li>';
    return;
  }

  eventStreamEl.innerHTML = items.map(renderEventItem).join("");
}

function prependEvent(event) {
  if (!eventStreamEl || !isTicketEvent(event)) {
    return;
  }

  if (eventStreamEl.firstElementChild?.classList.contains("event-empty")) {
    eventStreamEl.innerHTML = "";
  }

  eventStreamEl.insertAdjacentHTML("afterbegin", renderEventItem(event));
  while (eventStreamEl.children.length > EVENT_STREAM_LIMIT) {
    eventStreamEl.removeChild(eventStreamEl.lastElementChild);
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
  setText(waitingForEngineerEl, formatNumber(cards?.waiting_for_engineer_count));

  setText(queueHealthTitleEl, normalizeString(summaries?.queue_health_label) || "Monitoring live queue balance.");
  setText(
    queueHealthDetailEl,
    normalizeString(summaries?.queue_health_detail) || "Loading the newest queue health summary and throughput pattern.",
  );
  setText(openTicketCountEl, formatNumber(cards?.open_ticket_count));
  setText(resolvedTicketCountEl, formatNumber(cards?.resolved_ticket_count));
  setText(managedTicketCountEl, formatNumber(cards?.managed_ticket_count));
  setText(takeoverTicketCountEl, formatNumber(cards?.takeover_ticket_count));
  setText(urgentTicketCountEl, formatNumber(cards?.urgent_ticket_count));
  setText(waitingTicketChipEl, formatNumber(cards?.waiting_for_engineer_count));
  setText(managedTicketChipEl, formatNumber(cards?.managed_ticket_count));
  setText(takeoverTicketChipEl, formatNumber(cards?.takeover_ticket_count));
  setText(
    escalationWatchTitleEl,
    normalizeString(summaries?.escalation_summary_title) || "Watching live queue pressure.",
  );
  setText(
    escalationWatchDetailEl,
    normalizeString(summaries?.escalation_summary_detail) || "Loading the latest escalation signal.",
  );
  setText(
    operatorSummaryTitleEl,
    normalizeString(summaries?.operator_summary_title) || "Reading operator workload.",
  );
  setText(
    operatorSummaryDetailEl,
    normalizeString(summaries?.operator_summary_detail) || "Loading managed and takeover balance.",
  );

  renderEventVolumeBars(charts?.event_volume_12h);
  renderBreakdownList(statusBreakdownEl, charts?.status_breakdown);
  renderBreakdownList(priorityBreakdownEl, charts?.priority_breakdown);
  renderBreakdownList(modeBreakdownEl, charts?.mode_breakdown);
}

async function loadRecentEvents() {
  const payload = await fetchJson(`/api/dashboard/events?limit=${EVENT_STREAM_LIMIT}`);
  renderEventStream(payload?.events || []);
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

    prependEvent(payload);
    try {
      await loadMetrics();
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
  renderHeaderUserControls();
  try {
    await fetchJson("/api/v1/auth/logout", { method: "POST" });
    stopDashboardSocket();
    window.location.assign("/login");
    window.location.reload();
  } finally {
    logoutLoading = false;
    renderHeaderUserControls();
  }
}

async function refreshDashboard() {
  if (refreshLoading) {
    return;
  }

  setRefreshLoading(true);
  try {
    await Promise.all([loadMetrics(), loadRecentEvents()]);
  } finally {
    setRefreshLoading(false);
  }
}

async function initializeDashboard() {
  renderHeaderUserControls();
  refreshButtonEl?.addEventListener("click", () => {
    refreshDashboard().catch((error) => {
      setRealtimeStatus(`Refresh failed: ${error.message}`);
    });
  });

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
