const wsStatusEl = document.getElementById("ws-status");
const headerUserControlsEl = document.getElementById("header-user-controls");
const ticketVolumeEl = document.getElementById("ticket-volume");
const resolutionRateEl = document.getElementById("resolution-rate");
const sentimentAlertsEl = document.getElementById("sentiment-alerts");
const knowledgeDocumentsTotalEl = document.getElementById("knowledge-documents-total");
const knowledgeChunksTotalEl = document.getElementById("knowledge-chunks-total");
const knowledgeBacklogCountEl = document.getElementById("knowledge-backlog-count");
const knowledgeFailures24hEl = document.getElementById("knowledge-failures-24h");
const knowledgeAvgProcessingEl = document.getElementById("knowledge-avg-processing");
const knowledgeAvgCharsEl = document.getElementById("knowledge-avg-chars");
const knowledgeStorageModeEl = document.getElementById("knowledge-storage-mode");
const knowledgeEmbeddingModelEl = document.getElementById("knowledge-embedding-model");
const knowledgeVectorTableEl = document.getElementById("knowledge-vector-table");
const knowledgeLatestCompletedEl = document.getElementById("knowledge-latest-completed");
const knowledgeAvgChunksPerDocumentEl = document.getElementById("knowledge-avg-chunks-per-document");
const knowledgeStatusBreakdownEl = document.getElementById("knowledge-status-breakdown");
const knowledgeDocumentsOfficialEl = document.getElementById("knowledge-documents-official");
const knowledgeDocumentsTechnicalEl = document.getElementById("knowledge-documents-technical");
const knowledgeChunksOfficialEl = document.getElementById("knowledge-chunks-official");
const knowledgeChunksTechnicalEl = document.getElementById("knowledge-chunks-technical");
const knowledgeIngestionsBodyEl = document.getElementById("knowledge-ingestions-body");
const knowledgeStatusFilterEl = document.getElementById("knowledge-status-filter");
const knowledgeTypeFilterEl = document.getElementById("knowledge-type-filter");
const eventStreamEl = document.getElementById("event-stream");

const DASHBOARD_USER = {
  username: "admin",
  role: "ADMIN",
};

const KNOWLEDGE_POLL_INTERVAL_MS = 10000;
const EVENT_STREAM_LIMIT = 20;

let ticketStorageMode = "unknown";
let knowledgeStorageMode = "unknown";
let logoutLoading = false;
let socket = null;
let heartbeatTimer = null;
let reconnectTimer = null;
let knowledgePollTimer = null;

const knowledgeFilters = {
  status: "all",
  knowledge_type: "all",
};

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

function formatNumber(value) {
  return new Intl.NumberFormat().format(Number(value || 0));
}

function formatDecimal(value, maximumFractionDigits = 2) {
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

function formatDuration(seconds) {
  const numeric = Number(seconds);
  if (!Number.isFinite(numeric) || numeric < 0) {
    return "-";
  }
  if (numeric < 60) {
    return `${formatDecimal(numeric, 1)}s`;
  }
  const minutes = Math.floor(numeric / 60);
  const remainingSeconds = Math.round(numeric % 60);
  if (minutes < 60) {
    return `${minutes}m ${remainingSeconds}s`;
  }
  const hours = Math.floor(minutes / 60);
  const remainingMinutes = minutes % 60;
  return `${hours}h ${remainingMinutes}m`;
}

function sanitizeHttpUrl(value) {
  const raw = String(value || "").trim();
  if (!raw) {
    return "";
  }
  try {
    const parsed = new URL(raw);
    if (parsed.protocol === "http:" || parsed.protocol === "https:") {
      return parsed.toString();
    }
  } catch {
    // Ignore malformed URLs.
  }
  return "";
}

function UserProfileChip({ username, role }) {
  const roleLabel = String(role || "ADMIN").toUpperCase() === "ADMIN" ? "ADMIN" : "OPERATOR";
  const roleClass = roleLabel === "ADMIN" ? "user-role-admin" : "user-role-operator";
  return `
    <div class="user-profile-chip" aria-label="Current user">
      <span class="user-avatar" aria-hidden="true">${escapeHtml(userInitial(username))}</span>
      <div class="user-meta">
        <p class="user-name">${escapeHtml(username)}</p>
        <p class="user-role ${roleClass}">${escapeHtml(roleLabel)}</p>
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

function setRealtimeStatus(text) {
  const suffixParts = [];
  if (ticketStorageMode !== "unknown") {
    suffixParts.push(`Ticket Storage: ${ticketStorageMode}`);
  }
  if (knowledgeStorageMode !== "unknown") {
    suffixParts.push(`Knowledge Storage: ${knowledgeStorageMode}`);
  }
  const suffix = suffixParts.length ? ` | ${suffixParts.join(" | ")}` : "";
  wsStatusEl.textContent = `${text}${suffix}`;
}

async function detectStorageModes() {
  try {
    const payload = await fetchJson("/health");
    const ticketMode = String(payload?.ticket_storage || "").toLowerCase();
    const knowledgeMode = String(payload?.knowledge_storage || "").toLowerCase();
    ticketStorageMode = ticketMode || "unknown";
    knowledgeStorageMode = knowledgeMode || "unknown";
  } catch {
    ticketStorageMode = "unknown";
    knowledgeStorageMode = "unknown";
  }
}

async function loadMetrics() {
  const data = await fetchJson("/api/dashboard/metrics");
  ticketVolumeEl.textContent = formatNumber(data.today_ticket_count);
  resolutionRateEl.textContent = `${formatDecimal(data.resolution_rate, 1)}%`;
  sentimentAlertsEl.textContent = formatNumber(data.sentiment_alert_count);
}

async function loadKnowledgeMetrics() {
  const data = await fetchJson("/api/dashboard/knowledge-metrics");
  knowledgeDocumentsTotalEl.textContent = formatNumber(data.documents_total);
  knowledgeChunksTotalEl.textContent = formatNumber(data.chunks_total);
  knowledgeBacklogCountEl.textContent = formatNumber(data.backlog_count);
  knowledgeFailures24hEl.textContent = formatNumber(data.failure_count_last_24h);
  knowledgeAvgProcessingEl.textContent = formatDuration(data.avg_processing_seconds_last_24h);
  knowledgeAvgCharsEl.textContent = `${formatDecimal(data.avg_chunk_characters, 1)} chars`;
  knowledgeStorageModeEl.textContent = data.knowledge_storage || "unknown";
  knowledgeEmbeddingModelEl.textContent = data.embedding_model || "-";
  knowledgeVectorTableEl.textContent = data.vector_table || "-";
  knowledgeLatestCompletedEl.textContent = formatDateTime(data.latest_completed_at);
  knowledgeAvgChunksPerDocumentEl.textContent = formatDecimal(data.avg_chunks_per_document, 2);
  knowledgeStatusBreakdownEl.textContent = [
    data.ingestions_by_status?.queued || 0,
    data.ingestions_by_status?.processing || 0,
    data.ingestions_by_status?.completed || 0,
    data.ingestions_by_status?.failed || 0,
  ].join(" / ");
  knowledgeDocumentsOfficialEl.textContent = formatNumber(data.documents_by_type?.official || 0);
  knowledgeDocumentsTechnicalEl.textContent = formatNumber(data.documents_by_type?.technical || 0);
  knowledgeChunksOfficialEl.textContent = formatNumber(data.chunks_by_type?.official || 0);
  knowledgeChunksTechnicalEl.textContent = formatNumber(data.chunks_by_type?.technical || 0);
  knowledgeStorageMode = String(data.knowledge_storage || knowledgeStorageMode || "unknown").toLowerCase();
}

function renderStatusPill(status) {
  const normalized = String(status || "unknown").toLowerCase();
  const safeStatus = ["queued", "processing", "completed", "failed"].includes(normalized)
    ? normalized
    : "queued";
  return `<span class="status-pill ${safeStatus}">${escapeHtml(safeStatus)}</span>`;
}

function renderIngestionSource(ingestion) {
  const sourceUrl = sanitizeHttpUrl(ingestion?.source_url);
  const fileName = String(ingestion?.file_name || "").trim();
  const documentId = String(ingestion?.document_id || "").trim();
  if (sourceUrl) {
    return `<a class="ingestion-source-link" href="${escapeHtml(
      sourceUrl
    )}" target="_blank" rel="noopener noreferrer">${escapeHtml(sourceUrl)}</a>`;
  }
  if (fileName) {
    return escapeHtml(fileName);
  }
  if (documentId) {
    return escapeHtml(documentId);
  }
  return "-";
}

function renderKnowledgeIngestions(ingestions) {
  if (!Array.isArray(ingestions) || ingestions.length === 0) {
    knowledgeIngestionsBodyEl.innerHTML = `
      <tr>
        <td colspan="7" class="empty-state">No knowledge ingestions found for the selected filters.</td>
      </tr>
    `;
    return;
  }

  knowledgeIngestionsBodyEl.innerHTML = ingestions
    .map((ingestion) => {
      const title =
        String(ingestion?.title || "").trim()
        || String(ingestion?.file_name || "").trim()
        || String(ingestion?.ingestion_id || "").trim();
      const entryType = String(ingestion?.entry_type || "").trim() || "-";
      const knowledgeType = String(ingestion?.knowledge_type || "").trim() || "-";
      const errorMessage = String(ingestion?.error_message || "").trim();

      return `
        <tr>
          <td>
            <p class="ingestion-title">${escapeHtml(title)}</p>
            <p class="ingestion-meta">${escapeHtml(ingestion.ingestion_id || "-")} · ${escapeHtml(entryType)} · ${escapeHtml(knowledgeType)}</p>
            ${errorMessage ? `<p class="ingestion-error">${escapeHtml(errorMessage)}</p>` : ""}
          </td>
          <td>${renderStatusPill(ingestion.status)}</td>
          <td>${formatNumber(ingestion.chunk_count)}</td>
          <td>${escapeHtml(formatDuration(ingestion.duration_seconds))}</td>
          <td>${escapeHtml(formatDateTime(ingestion.created_at))}</td>
          <td>${escapeHtml(formatDateTime(ingestion.finished_at))}</td>
          <td>
            <div class="ingestion-source">${renderIngestionSource(ingestion)}</div>
          </td>
        </tr>
      `;
    })
    .join("");
}

async function loadKnowledgeIngestions() {
  const query = new URLSearchParams({
    limit: "50",
    status: knowledgeFilters.status,
    knowledge_type: knowledgeFilters.knowledge_type,
  });
  const payload = await fetchJson(`/api/dashboard/knowledge-ingestions?${query.toString()}`);
  renderKnowledgeIngestions(Array.isArray(payload?.ingestions) ? payload.ingestions : []);
}

function normalizeEvent(event) {
  const eventName = String(event?.event || "ticket_updated");
  const ticketId = String(event?.ticket_id || "").trim();
  const ingestionId = String(event?.ingestion_id || "").trim();
  const normalizedTicketId = ticketId && ticketId !== "-" ? ticketId : "";
  const normalizedIngestionId = ingestionId && ingestionId !== "-" ? ingestionId : "";
  const isKnowledge = eventName.startsWith("knowledge_ingestion_") || Boolean(normalizedIngestionId);

  return {
    event: eventName,
    ticketId: normalizedTicketId,
    ingestionId: normalizedIngestionId,
    title: String(event?.title || "").trim(),
    message: String(event?.message || "").trim(),
    status: String(event?.status || "").trim(),
    createdAt: String(event?.created_at || new Date().toISOString()),
    isKnowledge,
  };
}

function appendEvent(event) {
  const normalized = normalizeEvent(event);
  const item = document.createElement("li");
  const isAlert = normalized.event === "sentiment_alert" || normalized.event === "knowledge_ingestion_failed";
  const classNames = ["event-item"];
  if (normalized.isKnowledge) {
    classNames.push("knowledge");
  }
  if (isAlert) {
    classNames.push("alert");
  }
  item.className = classNames.join(" ");

  const identityText = normalized.ingestionId
    ? `Ingestion ${normalized.ingestionId}`
    : `Ticket ${normalized.ticketId || "-"}`;
  const secondaryMessage = normalized.title
    ? escapeHtml(normalized.title)
    : escapeHtml(normalized.message || normalized.status || "Update received");

  item.innerHTML = `
    <div class="event-title">
      <strong>${escapeHtml(normalized.event)}</strong>
      <span class="event-identity">${escapeHtml(identityText)}</span>
    </div>
    <div>${secondaryMessage}</div>
    ${normalized.title && normalized.message ? `<div class="event-meta">${escapeHtml(normalized.message)}</div>` : ""}
    <div class="event-meta">${escapeHtml(formatDateTime(normalized.createdAt))}</div>
  `;
  eventStreamEl.prepend(item);
  while (eventStreamEl.children.length > EVENT_STREAM_LIMIT) {
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

function startKnowledgePolling() {
  if (knowledgePollTimer) {
    clearInterval(knowledgePollTimer);
  }
  knowledgePollTimer = setInterval(() => {
    Promise.all([loadKnowledgeMetrics(), loadKnowledgeIngestions()]).catch((error) => {
      setRealtimeStatus(`Knowledge refresh failed: ${error.message}`);
    });
  }, KNOWLEDGE_POLL_INTERVAL_MS);
}

function stopKnowledgePolling() {
  if (knowledgePollTimer) {
    clearInterval(knowledgePollTimer);
    knowledgePollTimer = null;
  }
}

function isKnowledgeEvent(eventName) {
  return String(eventName || "").startsWith("knowledge_ingestion_");
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
    try {
      if (isKnowledgeEvent(payload?.event)) {
        await Promise.all([loadKnowledgeMetrics(), loadKnowledgeIngestions()]);
      } else {
        await loadMetrics();
      }
    } catch (error) {
      setRealtimeStatus(`Realtime refresh failed: ${error.message}`);
    }
  };

  heartbeatTimer = setInterval(() => {
    if (socket && socket.readyState === WebSocket.OPEN) {
      socket.send("ping");
    }
  }, 10000);
}

function bindKnowledgeFilters() {
  knowledgeStatusFilterEl?.addEventListener("change", () => {
    knowledgeFilters.status = knowledgeStatusFilterEl.value;
    loadKnowledgeIngestions().catch((error) => {
      setRealtimeStatus(`Failed to filter ingestions: ${error.message}`);
    });
  });
  knowledgeTypeFilterEl?.addEventListener("change", () => {
    knowledgeFilters.knowledge_type = knowledgeTypeFilterEl.value;
    loadKnowledgeIngestions().catch((error) => {
      setRealtimeStatus(`Failed to filter ingestions: ${error.message}`);
    });
  });
}

async function handleLogoutClick() {
  if (logoutLoading) {
    return;
  }
  logoutLoading = true;
  renderHeaderUserControls();
  try {
    await fetchJson("/api/v1/auth/logout", { method: "POST" });
    stopKnowledgePolling();
    closeDashboardSocket();
    window.location.assign("/login");
    window.location.reload();
  } finally {
    logoutLoading = false;
    renderHeaderUserControls();
  }
}

async function initializeDashboard() {
  renderHeaderUserControls();
  bindKnowledgeFilters();
  await detectStorageModes();
  await Promise.all([
    loadMetrics(),
    loadKnowledgeMetrics(),
    loadKnowledgeIngestions(),
    loadRecentEvents(),
  ]);
  setRealtimeStatus("Realtime: connecting...");
  startKnowledgePolling();
  setupWebSocket();
}

initializeDashboard().catch((error) => {
  startKnowledgePolling();
  setRealtimeStatus(`Failed to load dashboard: ${error.message}`);
});
