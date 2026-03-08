const wsStatusEl = document.getElementById("ws-status");
const headerUserControlsEl = document.getElementById("header-user-controls");
const ticketVolumeEl = document.getElementById("ticket-volume");
const resolutionRateEl = document.getElementById("resolution-rate");
const sentimentAlertsEl = document.getElementById("sentiment-alerts");
const eventStreamEl = document.getElementById("event-stream");
let storageMode = "unknown";
let logoutLoading = false;
let socket = null;
let heartbeatTimer = null;
let reconnectTimer = null;

const DASHBOARD_USER = {
  username: "admin",
  role: "ADMIN",
};

async function fetchJson(url, options = undefined) {
  const response = await fetch(url, options);
  if (!response.ok) {
    let reason = `HTTP ${response.status}`;
    try {
      const payload = await response.json();
      reason = payload?.detail || reason;
    } catch {
      // Keep fallback reason.
    }
    throw new Error(reason);
  }
  return response.json();
}

function userInitial(username) {
  const value = String(username || "").trim();
  if (!value) {
    return "U";
  }
  return value[0].toUpperCase();
}

function UserProfileChip({ username, role }) {
  const roleLabel = String(role || "ADMIN").toUpperCase() === "ADMIN" ? "ADMIN" : "OPERATOR";
  const roleClass = roleLabel === "ADMIN" ? "user-role-admin" : "user-role-operator";
  return `
    <div class="user-profile-chip" aria-label="Current user">
      <span class="user-avatar" aria-hidden="true">${userInitial(username)}</span>
      <div class="user-meta">
        <p class="user-name">${username}</p>
        <p class="user-role ${roleClass}">${roleLabel}</p>
      </div>
    </div>
  `;
}

function LogoutButton({ loading = false } = {}) {
  return `
    <button
      id="logout-btn"
      class="logout-icon-btn"
      type="button"
      title="Logout"
      aria-label="Logout"
      ${loading ? "disabled" : ""}
    >
      <svg class="logout-icon" viewBox="0 0 24 24" fill="none" aria-hidden="true">
        <path d="M14 8L18 12L14 16" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"></path>
        <path d="M18 12H9" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"></path>
        <path d="M10 4H7C5.9 4 5 4.9 5 6V18C5 19.1 5.9 20 7 20H10" stroke="currentColor" stroke-width="1.9" stroke-linecap="round"></path>
      </svg>
    </button>
  `;
}

function renderHeaderUserControls() {
  if (!headerUserControlsEl) {
    return;
  }
  headerUserControlsEl.innerHTML = [
    UserProfileChip(DASHBOARD_USER),
    LogoutButton({ loading: logoutLoading }),
  ].join("");
  const logoutBtn = document.getElementById("logout-btn");
  logoutBtn?.addEventListener("click", () => {
    handleLogoutClick().catch((error) => {
      setRealtimeStatus(`Logout failed: ${error.message}`);
    });
  });
}

async function loadMetrics() {
  const data = await fetchJson("/api/dashboard/metrics");
  ticketVolumeEl.textContent = data.today_ticket_count;
  resolutionRateEl.textContent = `${data.resolution_rate}%`;
  sentimentAlertsEl.textContent = data.sentiment_alert_count;
}

function setRealtimeStatus(text) {
  const suffix = storageMode === "unknown" ? "" : ` | Storage: ${storageMode}`;
  wsStatusEl.textContent = `${text}${suffix}`;
}

function closeDashboardSocket() {
  if (reconnectTimer) {
    clearTimeout(reconnectTimer);
    reconnectTimer = null;
  }
  if (heartbeatTimer) {
    clearInterval(heartbeatTimer);
    heartbeatTimer = null;
  }
  if (socket) {
    socket.onclose = null;
    socket.close();
    socket = null;
  }
}

async function detectStorageMode() {
  try {
    const payload = await fetchJson("/health");
    const mode = String(payload?.ticket_storage || "").toLowerCase();
    if (mode === "postgres" || mode === "memory") {
      storageMode = mode;
    } else {
      storageMode = "unknown";
    }
  } catch {
    storageMode = "unknown";
  }
}

function normalizeEvent(event) {
  return {
    event: String(event?.event || "ticket_updated"),
    ticket_id: String(event?.ticket_id || "-"),
    message: event?.message ? String(event.message) : "",
    status: event?.status ? String(event.status) : "",
    created_at: String(event?.created_at || new Date().toISOString()),
  };
}

function appendEvent(event) {
  const normalized = normalizeEvent(event);
  const item = document.createElement("li");
  item.className = `event-item ${normalized.event === "sentiment_alert" ? "alert" : ""}`;
  item.innerHTML = `
    <div><strong>${normalized.event}</strong> - Ticket ${normalized.ticket_id}</div>
    <div>${normalized.message || normalized.status || "Update received"}</div>
    <div class="event-meta">${normalized.created_at}</div>
  `;
  eventStreamEl.prepend(item);
  while (eventStreamEl.children.length > 20) {
    eventStreamEl.removeChild(eventStreamEl.lastChild);
  }
}

async function loadRecentEvents() {
  const payload = await fetchJson("/api/dashboard/events?limit=20");
  const events = Array.isArray(payload?.events) ? payload.events : [];
  eventStreamEl.innerHTML = "";
  for (let index = events.length - 1; index >= 0; index -= 1) {
    appendEvent(events[index]);
  }
}

function setupWebSocket() {
  closeDashboardSocket();
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
    appendEvent(payload);
    await loadMetrics();
  };

  heartbeatTimer = setInterval(() => {
    if (socket.readyState === WebSocket.OPEN) {
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
    closeDashboardSocket();
    window.location.assign("/login");
    window.location.reload();
  } finally {
    logoutLoading = false;
    renderHeaderUserControls();
  }
}

renderHeaderUserControls();
detectStorageMode()
  .then(() => Promise.all([loadMetrics(), loadRecentEvents()]))
  .then(() => {
    setRealtimeStatus("Realtime: connecting...");
    setupWebSocket();
  })
  .catch((error) => {
    setRealtimeStatus(`Failed to load metrics: ${error.message}`);
  });
