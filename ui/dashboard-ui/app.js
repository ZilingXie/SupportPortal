const wsStatusEl = document.getElementById("ws-status");
const headerUserControlsEl = document.getElementById("header-user-controls");
const ticketVolumeEl = document.getElementById("ticket-volume");
const resolutionRateEl = document.getElementById("resolution-rate");
const sentimentAlertsEl = document.getElementById("sentiment-alerts");
const refreshButtonEl = document.getElementById("refresh-button");
const eventStreamEl = document.getElementById("event-stream");
const latestEventLabelEl = document.getElementById("latest-event-label");
const prioritySignalEl = document.getElementById("priority-signal");
const opsBriefBodyEl = document.getElementById("ops-brief-body");
const opsBriefTitleEl = document.getElementById("ops-brief-title");
const opsBriefDetailEl = document.getElementById("ops-brief-detail");
const activeFocusEl = document.getElementById("active-focus");
const activeFocusDetailEl = document.getElementById("active-focus-detail");
const liveHealthTitleEl = document.getElementById("live-health-title");
const liveHealthDetailEl = document.getElementById("live-health-detail");

const DASHBOARD_USER = {
  username: "admin",
  role: "ADMIN",
};

const EVENT_STREAM_LIMIT = 16;

let socket = null;
let heartbeatTimer = null;
let reconnectTimer = null;
let logoutLoading = false;
let latestTicketEvent = null;

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
      } else if (payload?.detail?.message) {
        reason = payload.detail.message;
      }
    } catch {
      // Keep fallback error.
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

function userInitial(username) {
  const normalized = normalizeString(username);
  return normalized ? normalized[0].toUpperCase() : "A";
}

function humanizeToken(value) {
  const normalized = normalizeString(value);
  if (!normalized) {
    return "-";
  }
  return normalized
    .replaceAll("_", " ")
    .replace(/\b\w/g, (match) => match.toUpperCase());
}

function isTicketEvent(payload) {
  return !normalizeString(payload?.ingestion_id) && !normalizeString(payload?.event).startsWith("knowledge_ingestion_");
}

function eventTone(payload) {
  const priority = normalizeString(payload?.priority).toLowerCase();
  const mode = normalizeString(payload?.engineer_mode).toLowerCase();
  if (priority === "urgent" || priority === "high") {
    return priority;
  }
  if (mode === "managed" || mode === "takeover") {
    return mode;
  }
  return "default";
}

function renderHeaderUserControls() {
  if (!headerUserControlsEl) {
    return;
  }
  const role = normalizeString(DASHBOARD_USER.role || "ADMIN").toUpperCase();
  headerUserControlsEl.innerHTML = `
    <div class="user-profile-chip" aria-label="Current user">
      <span class="user-avatar" aria-hidden="true">${escapeHtml(userInitial(DASHBOARD_USER.username))}</span>
      <div class="user-meta">
        <p class="user-name">${escapeHtml(DASHBOARD_USER.username)}</p>
        <p class="user-role">${escapeHtml(role)}</p>
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

function setRealtimeStatus(text) {
  if (wsStatusEl) {
    wsStatusEl.textContent = text;
  }
}

function renderEventItem(event) {
  const eventName = humanizeToken(event?.event || "ticket_updated");
  const eventMessage = normalizeString(event?.message) || normalizeString(event?.title) || "Ticket activity updated.";
  const ticketId = normalizeString(event?.ticket_id) || "-";
  const status = normalizeString(event?.status);
  const priority = normalizeString(event?.priority);
  const mode = normalizeString(event?.engineer_mode);
  const createdAt = formatDateTime(event?.created_at);
  const tone = eventTone(event);

  return `
    <li class="event-item">
      <div class="event-topline">
        <span class="event-chip event-chip-${escapeHtml(tone)}">${escapeHtml(eventName)}</span>
        <span>${escapeHtml(createdAt)}</span>
      </div>
      <h3 class="event-title">${escapeHtml(ticketId === "-" ? eventName : ticketId)}</h3>
      <p class="event-copy">${escapeHtml(eventMessage)}</p>
      <div class="event-meta">
        <span>Ticket ${escapeHtml(ticketId)}</span>
        ${status ? `<span>Status ${escapeHtml(humanizeToken(status))}</span>` : ""}
        ${priority ? `<span>Priority ${escapeHtml(humanizeToken(priority))}</span>` : ""}
        ${mode ? `<span>Mode ${escapeHtml(humanizeToken(mode))}</span>` : ""}
      </div>
    </li>
  `;
}

function renderEventStream(events) {
  const items = (Array.isArray(events) ? events : []).filter(isTicketEvent);
  if (!items.length) {
    eventStreamEl.innerHTML = '<li class="event-empty">No ticket events yet. New dashboard traffic will appear here.</li>';
    syncCallouts(null);
    return;
  }
  eventStreamEl.innerHTML = items.map(renderEventItem).join("");
  latestTicketEvent = items[0];
  syncCallouts(latestTicketEvent);
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
  latestTicketEvent = event;
  syncCallouts(event);
}

function syncCallouts(event) {
  const message = normalizeString(event?.message) || normalizeString(event?.title) || "No live events yet.";
  const eventLabel = humanizeToken(event?.event || "queue_idle");
  const priority = normalizeString(event?.priority);
  const status = normalizeString(event?.status);
  const mode = normalizeString(event?.engineer_mode);
  const ticketId = normalizeString(event?.ticket_id);

  latestEventLabelEl.textContent = eventLabel;
  prioritySignalEl.textContent = priority ? humanizeToken(priority) : "Stable";
  opsBriefBodyEl.textContent = message;
  opsBriefTitleEl.textContent = ticketId ? `${ticketId} is the latest active signal.` : "Ticket ops stays operational.";
  opsBriefDetailEl.textContent = event
    ? `${eventLabel} arrived ${formatDateTime(event.created_at)}. Open the RAG workbench only when you need retrieval or eval detail.`
    : "Use this page for workload, websocket health, and event triage. The RAG workbench now lives on a separate page.";
  activeFocusEl.textContent = ticketId || "Watching the queue.";
  activeFocusDetailEl.textContent = event
    ? `${message}${mode ? ` Current mode: ${humanizeToken(mode)}.` : ""}`
    : "The dashboard will summarize the newest customer-facing movement here.";
  liveHealthTitleEl.textContent = status
    ? `Latest status: ${humanizeToken(status)}`
    : "Waiting for dashboard traffic.";
  liveHealthDetailEl.textContent = event
    ? `${ticketId || "Ticket"} updated ${formatDateTime(event.created_at)}.${priority ? ` Priority is ${humanizeToken(priority)}.` : ""}`
    : "Once events arrive, this card will call out the strongest current operational signal.";
}

async function loadMetrics() {
  const payload = await fetchJson("/api/dashboard/metrics");
  ticketVolumeEl.textContent = formatNumber(payload.today_ticket_count);
  resolutionRateEl.textContent = `${formatDecimal(payload.resolution_rate)}%`;
  sentimentAlertsEl.textContent = formatNumber(payload.sentiment_alert_count);
}

async function loadRecentEvents() {
  const payload = await fetchJson("/api/dashboard/events?limit=16");
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
    const payload = JSON.parse(event.data);
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
  await Promise.all([loadMetrics(), loadRecentEvents()]);
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

initializeDashboard().catch((error) => {
  setRealtimeStatus(`Dashboard failed to initialize: ${error.message}`);
});
