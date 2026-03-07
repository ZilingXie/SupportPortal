const wsStatusEl = document.getElementById("ws-status");
const ticketVolumeEl = document.getElementById("ticket-volume");
const resolutionRateEl = document.getElementById("resolution-rate");
const sentimentAlertsEl = document.getElementById("sentiment-alerts");
const eventStreamEl = document.getElementById("event-stream");
let storageMode = "unknown";

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
  const protocol = window.location.protocol === "https:" ? "wss" : "ws";
  const socket = new WebSocket(`${protocol}://${window.location.host}/ws/dashboard`);

  socket.onopen = () => {
    setRealtimeStatus("Realtime: connected");
  };
  socket.onclose = () => {
    setRealtimeStatus("Realtime: disconnected (reconnecting...)");
    setTimeout(setupWebSocket, 1500);
  };
  socket.onerror = () => {
    setRealtimeStatus("Realtime: error");
  };
  socket.onmessage = async (event) => {
    const payload = JSON.parse(event.data);
    appendEvent(payload);
    await loadMetrics();
  };

  setInterval(() => {
    if (socket.readyState === WebSocket.OPEN) {
      socket.send("ping");
    }
  }, 10000);
}

detectStorageMode()
  .then(() => Promise.all([loadMetrics(), loadRecentEvents()]))
  .then(() => {
    setRealtimeStatus("Realtime: connecting...");
    setupWebSocket();
  })
  .catch((error) => {
    setRealtimeStatus(`Failed to load metrics: ${error.message}`);
  });
