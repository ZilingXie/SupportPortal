const AUTH_KEY = "engineer_portal_auth";
const LOGIN_USER = "eng";
const LOGIN_PASS = "eng";
const ENGINEER_ID = "eng";

const loginScreenEl = document.getElementById("login-screen");
const boardScreenEl = document.getElementById("board-screen");
const loginFormEl = document.getElementById("login-form");
const loginErrorEl = document.getElementById("login-error");
const statusFilterEl = document.getElementById("status-filter");
const logoutBtnEl = document.getElementById("logout-btn");
const wsStatusEl = document.getElementById("ws-status");
const ticketCountEl = document.getElementById("ticket-count");
const ticketTableBodyEl = document.getElementById("ticket-table-body");
const detailModalEl = document.getElementById("detail-modal");
const detailTitleEl = document.getElementById("detail-title");
const detailBodyEl = document.getElementById("detail-body");
const detailCloseBtnEl = document.getElementById("detail-close-btn");

let tickets = [];
let selectedTicketId = null;
let selectedTicket = null;
let selectedTicketSummary = "";
let selectedTicketSummaryMeta = "";
let socket = null;
let heartbeatTimer = null;
let reconnectTimer = null;
let storageMode = "unknown";
const ticketSummaryCache = new Map();

const PRIORITY_RANK = {
  urgent: 4,
  high: 3,
  normal: 2,
  low: 1,
};

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function formatMultiline(value) {
  return escapeHtml(value).replaceAll("\n", "<br>");
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

function buildMessageReferences(message) {
  const role = String(message?.role || "").toLowerCase();
  if (role !== "assistant") {
    return "";
  }

  const citations = Array.isArray(message?.citations) ? message.citations : [];
  const sources = Array.isArray(message?.sources) ? message.sources : [];
  const seen = new Set();

  const citationItems = citations
    .map((citation, index) => {
      if (!citation || typeof citation !== "object") {
        return "";
      }

      const heading = String(
        citation.heading || citation.source_path || citation.chunk_id || `Citation ${index + 1}`
      ).trim();
      const sourcePath = String(citation.source_path || "").trim();
      const chunkId = String(citation.chunk_id || "").trim();
      const sourceUrl = sanitizeHttpUrl(citation.source_url);
      const identity = sourceUrl || sourcePath || chunkId || heading;
      if (!identity || seen.has(identity)) {
        return "";
      }
      seen.add(identity);

      const metaParts = [];
      if (sourcePath) {
        metaParts.push(sourcePath);
      }
      if (chunkId) {
        metaParts.push(`#${chunkId}`);
      }
      const meta = metaParts.length
        ? `<span class="reference-meta">${escapeHtml(metaParts.join(" · "))}</span>`
        : "";

      if (sourceUrl) {
        return `<li><a class="reference-link" href="${escapeHtml(
          sourceUrl
        )}" target="_blank" rel="noopener noreferrer">${escapeHtml(heading)}</a>${meta}</li>`;
      }

      return `<li><span class="reference-text">${escapeHtml(heading)}</span>${meta}</li>`;
    })
    .filter(Boolean);

  const sourceItems = sources
    .map((source, index) => {
      const sourceText = String(source || "").trim();
      if (!sourceText) {
        return "";
      }
      const sourceUrl = sanitizeHttpUrl(sourceText);
      const identity = sourceUrl || sourceText;
      if (!identity || seen.has(identity)) {
        return "";
      }
      seen.add(identity);

      if (sourceUrl) {
        let linkLabel = `Source ${index + 1}`;
        try {
          const parsed = new URL(sourceUrl);
          linkLabel = parsed.hostname || linkLabel;
        } catch {
          // Keep fallback label.
        }
        return `<li><a class="reference-link" href="${escapeHtml(
          sourceUrl
        )}" target="_blank" rel="noopener noreferrer">${escapeHtml(linkLabel)}</a></li>`;
      }

      return `<li><span class="reference-text">${escapeHtml(sourceText)}</span></li>`;
    })
    .filter(Boolean);

  const items = [...citationItems, ...sourceItems];
  if (items.length === 0) {
    return "";
  }

  return `
    <section class="message-references">
      <h4>References</h4>
      <ul>
        ${items.join("")}
      </ul>
    </section>
  `;
}

function formatDateTime(value) {
  if (!value) {
    return "-";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return "-";
  }
  return date.toLocaleString("zh-CN", {
    hour12: false,
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function priorityLabel(value) {
  const normalized = String(value || "normal").toLowerCase();
  if (normalized === "urgent") {
    return "Urgent";
  }
  if (normalized === "high") {
    return "High";
  }
  if (normalized === "low") {
    return "Low";
  }
  return "Normal";
}

function modeLabel(value) {
  return value === "takeover" ? "Human Takeover" : "AI Managing";
}

function statusLabel(value) {
  if (value === "waiting_for_engineer") {
    return "Waiting Engineer";
  }
  if (value === "resolved") {
    return "Resolved";
  }
  return "Open";
}

function statusClass(value) {
  if (value === "resolved") {
    return "status-resolved";
  }
  if (value === "waiting_for_engineer") {
    return "status-waiting";
  }
  return "status-open";
}

function roleLabel(role) {
  if (role === "customer") {
    return "Customer";
  }
  if (role === "assistant") {
    return "AI";
  }
  if (role === "engineer") {
    return "Engineer";
  }
  return "System";
}

function roleClass(role) {
  if (role === "customer") {
    return "msg-customer";
  }
  if (role === "assistant") {
    return "msg-assistant";
  }
  if (role === "engineer") {
    return "msg-engineer";
  }
  return "msg-system";
}

function setRealtimeStatus(text) {
  const suffix = storageMode === "unknown" ? "" : ` | Storage: ${storageMode}`;
  wsStatusEl.textContent = `${text}${suffix}`;
}

function summaryCacheKey(ticket) {
  const messageCount = Array.isArray(ticket?.messages) ? ticket.messages.length : 0;
  return [
    String(ticket?.updated_at || ""),
    String(ticket?.status || ""),
    String(ticket?.engineer_mode || ""),
    String(messageCount),
  ].join("|");
}

function buildLocalTicketSummary(ticket) {
  const subject = String(ticket?.subject || "").trim() || "General support request";
  const status = String(ticket?.status || "open").toLowerCase();
  const mode = String(ticket?.engineer_mode || "managed").toLowerCase();
  const priority = String(ticket?.priority || "normal");
  const messages = Array.isArray(ticket?.messages) ? ticket.messages : [];

  let latestCustomer = "";
  let latestAssistant = "";
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const item = messages[index];
    const role = String(item?.role || "").toLowerCase();
    const content = String(item?.content || "").trim();
    if (!content) {
      continue;
    }
    if (!latestCustomer && role === "customer") {
      latestCustomer = content;
    }
    if (!latestAssistant && role === "assistant") {
      latestAssistant = content;
    }
    if (latestCustomer && latestAssistant) {
      break;
    }
  }

  const lines = [
    `Subject: ${subject}`,
    `Status: ${statusLabel(status)}, Mode: ${modeLabel(mode)}, Priority: ${priorityLabel(priority)}`,
  ];
  if (latestCustomer) {
    lines.push(`Latest customer request: ${latestCustomer.slice(0, 140)}`);
  }
  if (latestAssistant) {
    lines.push(`Latest system response: ${latestAssistant.slice(0, 140)}`);
  }
  return lines.join("\n");
}

function isAuthenticated() {
  return localStorage.getItem(AUTH_KEY) === "1";
}

function setAuthenticated(value) {
  if (value) {
    localStorage.setItem(AUTH_KEY, "1");
  } else {
    localStorage.removeItem(AUTH_KEY);
  }
}

function toggleScreens() {
  const authed = isAuthenticated();
  loginScreenEl.classList.toggle("hidden", authed);
  boardScreenEl.classList.toggle("hidden", !authed);
}

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

function sortTicketsByPriority(items) {
  return [...items].sort((a, b) => {
    const rankA = PRIORITY_RANK[String(a.priority || "normal").toLowerCase()] || PRIORITY_RANK.normal;
    const rankB = PRIORITY_RANK[String(b.priority || "normal").toLowerCase()] || PRIORITY_RANK.normal;
    if (rankA !== rankB) {
      return rankB - rankA;
    }
    const updatedA = new Date(a.updated_at || a.created_at || 0).getTime();
    const updatedB = new Date(b.updated_at || b.created_at || 0).getTime();
    return updatedB - updatedA;
  });
}

function renderTickets() {
  const rows = sortTicketsByPriority(tickets);
  ticketCountEl.textContent = `${rows.length} tickets`;

  if (rows.length === 0) {
    ticketTableBodyEl.innerHTML = '<tr><td colspan="8" class="empty-row">No tickets.</td></tr>';
    return;
  }

  ticketTableBodyEl.innerHTML = rows
    .map((ticket) => {
      const ticketId = String(ticket.ticket_id || "-");
      const priority = String(ticket.priority || "normal").toLowerCase();
      const status = String(ticket.status || "open").toLowerCase();
      const mode = String(ticket.engineer_mode || "managed").toLowerCase();
      const subject = String(ticket.subject || "(No subject)");
      const requester = String(ticket.requester || ticket.customer_id || "Unknown");
      const pendingQuestion = String(ticket.pending_engineer_question || "").trim();
      const showPendingQuestion = Boolean(
        pendingQuestion && status !== "waiting_for_engineer"
      );

      const isSelected = selectedTicketId === ticketId;

      return `
        <tr class="${isSelected ? "row-selected" : ""}">
          <td class="ticket-id">
            <button class="ticket-link action-btn" data-action="view-detail" data-ticket-id="${escapeHtml(
              ticketId
            )}">${escapeHtml(ticketId)}</button>
          </td>
          <td>
            <span class="priority-badge priority-${escapeHtml(priority)}">${escapeHtml(
              priorityLabel(priority)
            )}</span>
          </td>
          <td><span class="mode-text">${escapeHtml(modeLabel(mode))}</span></td>
          <td>
            <span class="status-badge ${statusClass(status)}">${escapeHtml(statusLabel(status))}</span>
          </td>
          <td>
            <div class="subject-cell">${escapeHtml(subject)}</div>
            ${
              showPendingQuestion
                ? `<div class="pending-note">AI question: ${escapeHtml(pendingQuestion)}</div>`
                : ""
            }
          </td>
          <td>${escapeHtml(requester)}</td>
          <td>${escapeHtml(formatDateTime(ticket.created_at))}</td>
          <td>${escapeHtml(formatDateTime(ticket.updated_at))}</td>
        </tr>
      `;
    })
    .join("");
}

function closeTicketDetail() {
  selectedTicketId = null;
  selectedTicket = null;
  selectedTicketSummary = "";
  selectedTicketSummaryMeta = "";
  detailModalEl.classList.add("hidden");
  detailTitleEl.textContent = "Ticket Detail";
  detailBodyEl.innerHTML = "";
  renderTickets();
}

function renderTicketDetail() {
  if (!selectedTicketId || !selectedTicket) {
    detailModalEl.classList.add("hidden");
    detailTitleEl.textContent = "Ticket Detail";
    detailBodyEl.innerHTML = "";
    return;
  }

  const ticket = selectedTicket;
  const ticketId = String(ticket.ticket_id || selectedTicketId || "-");
  const status = String(ticket.status || "open").toLowerCase();
  const mode = String(ticket.engineer_mode || "managed").toLowerCase();
  const pendingQuestion = String(ticket.pending_engineer_question || "").trim();
  const showPendingQuestion = Boolean(
    pendingQuestion && status !== "waiting_for_engineer"
  );
  const messages = Array.isArray(ticket.messages) ? ticket.messages : [];

  const messageItems =
    messages.length === 0
      ? '<p>No messages on this ticket.</p>'
      : `<div class="message-list">${messages
          .map((message) => {
            const role = String(message.role || "system").toLowerCase();
            const createdAt = formatDateTime(message.created_at);
            return `
              <article class="message-item ${roleClass(role)}">
                <header>
                  <span class="message-role">${escapeHtml(roleLabel(role))}</span>
                  <span class="message-time">${escapeHtml(createdAt)}</span>
                </header>
                <div class="message-content">${formatMultiline(String(message.content || ""))}</div>
                ${buildMessageReferences(message)}
              </article>
            `;
          })
          .join("")}</div>`;

  detailTitleEl.textContent = `Ticket ${ticketId}`;
  detailBodyEl.innerHTML = `
    <div class="detail-grid">
      <div class="detail-item"><span>ID</span><strong>${escapeHtml(ticketId)}</strong></div>
      <div class="detail-item"><span>Priority</span><strong>${escapeHtml(priorityLabel(ticket.priority))}</strong></div>
      <div class="detail-item detail-item-status">
        <span>Status</span>
        <div class="detail-status-row">
          <select id="detail-status-select" class="detail-inline-select">
            <option value="open" ${status === "open" ? "selected" : ""}>${escapeHtml(statusLabel("open"))}</option>
            <option value="waiting_for_engineer" ${status === "waiting_for_engineer" ? "selected" : ""}>${escapeHtml(
              statusLabel("waiting_for_engineer")
            )}</option>
            <option value="resolved" ${status === "resolved" ? "selected" : ""}>${escapeHtml(
              statusLabel("resolved")
            )}</option>
          </select>
        </div>
      </div>
      <div class="detail-item detail-item-mode">
        <span>Mode</span>
        <select id="detail-mode-select" class="detail-inline-select">
          <option value="managed" ${mode === "managed" ? "selected" : ""}>AI Managing</option>
          <option value="takeover" ${mode === "takeover" ? "selected" : ""}>Human Takeover</option>
        </select>
      </div>
      <div class="detail-item"><span>Requester</span><strong>${escapeHtml(
        String(ticket.requester || ticket.customer_id || "Unknown")
      )}</strong></div>
      <div class="detail-item"><span>Create</span><strong>${escapeHtml(
        formatDateTime(ticket.created_at)
      )}</strong></div>
      <div class="detail-item"><span>Update</span><strong>${escapeHtml(
        formatDateTime(ticket.updated_at)
      )}</strong></div>
    </div>

    <section class="detail-block">
      <h3>Subject</h3>
      <p>${escapeHtml(String(ticket.subject || "(No subject)"))}</p>
      ${
        showPendingQuestion
          ? `<p class="pending-note"><strong>AI question:</strong> ${escapeHtml(pendingQuestion)}</p>`
          : ""
      }
    </section>

    <section class="detail-block">
      <h3>Summary</h3>
      <p class="summary-text">${formatMultiline(selectedTicketSummary || "Generating summary...")}</p>
      ${
        selectedTicketSummaryMeta
          ? `<p class="summary-meta">${escapeHtml(selectedTicketSummaryMeta)}</p>`
          : ""
      }
    </section>

    <section class="detail-block">
      <h3>Conversation</h3>
      ${messageItems}
    </section>
  `;

  detailModalEl.classList.remove("hidden");
}

async function refreshSelectedSummary(options = {}) {
  const { silent = true } = options;
  if (!selectedTicketId || !selectedTicket) {
    return;
  }

  const cacheKey = summaryCacheKey(selectedTicket);
  const cached = ticketSummaryCache.get(selectedTicketId);
  if (cached && cached.cacheKey === cacheKey) {
    selectedTicketSummary = cached.summary;
    selectedTicketSummaryMeta = cached.meta;
    renderTicketDetail();
    return;
  }

  try {
    const payload = await fetchJson(
      `/api/engineer/tickets/${encodeURIComponent(selectedTicketId)}/summary`
    );
    const summary = String(payload?.summary || "").trim();
    if (!summary) {
      return;
    }
    const model = String(payload?.model || "fallback").trim();
    const meta = model === "fallback" ? "AI Summary (fallback)" : `AI Summary (${model})`;
    selectedTicketSummary = summary;
    selectedTicketSummaryMeta = meta;
    ticketSummaryCache.set(selectedTicketId, {
      cacheKey,
      summary,
      meta,
    });
    renderTicketDetail();
  } catch (error) {
    if (!silent) {
      window.alert(`Summary generation failed: ${error.message}`);
    }
  }
}

async function refreshSelectedTicket(options = {}) {
  const { silent = false } = options;
  if (!selectedTicketId) {
    selectedTicket = null;
    selectedTicketSummary = "";
    selectedTicketSummaryMeta = "";
    renderTicketDetail();
    return;
  }

  try {
    const payload = await fetchJson(`/api/engineer/tickets/${encodeURIComponent(selectedTicketId)}`);
    selectedTicket = payload.ticket || null;
    if (selectedTicket) {
      selectedTicketSummary = buildLocalTicketSummary(selectedTicket);
      selectedTicketSummaryMeta = "AI Summary (loading...)";
    } else {
      selectedTicketSummary = "";
      selectedTicketSummaryMeta = "";
    }
    renderTicketDetail();
    await refreshSelectedSummary({ silent: true });
  } catch (error) {
    if (String(error.message || "").toLowerCase().includes("not found")) {
      closeTicketDetail();
      return;
    }
    if (!silent) {
      window.alert(`Failed to load ticket detail: ${error.message}`);
    }
  }
}

async function openTicketDetail(ticketId) {
  selectedTicketId = ticketId;
  renderTickets();
  await refreshSelectedTicket();
}

async function loadTickets(options = {}) {
  const { refreshDetail = true } = options;
  const statusFilter = statusFilterEl.value || "open";
  const params = new URLSearchParams({ status: statusFilter });
  const payload = await fetchJson(`/api/engineer/tickets?${params.toString()}`);
  tickets = Array.isArray(payload.tickets) ? payload.tickets : [];
  renderTickets();

  if (refreshDetail && selectedTicketId) {
    await refreshSelectedTicket({ silent: true });
  }
}

function showBoardError(message) {
  ticketTableBodyEl.innerHTML = `<tr><td colspan="8" class="empty-row">${escapeHtml(message)}</td></tr>`;
  ticketCountEl.textContent = "0 tickets";
}

async function updateTicketMode(ticketId, mode) {
  await fetchJson(`/api/engineer/tickets/${encodeURIComponent(ticketId)}/mode`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ mode, engineer_id: ENGINEER_ID }),
  });
}

async function updateTicketStatus(ticketId, action) {
  await fetchJson(`/api/tickets/${encodeURIComponent(ticketId)}/action`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ action, engineer_id: ENGINEER_ID }),
  });
}

async function submitManagedResponse(ticketId) {
  const solution = window.prompt("请输入给 AI 的处理建议，AI 会总结后回复客户：");
  if (solution === null) {
    return;
  }
  const cleaned = solution.trim();
  if (!cleaned) {
    window.alert("内容不能为空。");
    return;
  }
  await fetchJson(`/api/engineer/tickets/${encodeURIComponent(ticketId)}/managed-response`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ solution: cleaned, engineer_id: ENGINEER_ID }),
  });
}

async function handleTableClick(event) {
  const button = event.target.closest("button.action-btn");
  if (!button) {
    return;
  }

  const action = button.dataset.action;
  const ticketId = button.dataset.ticketId;
  if (!action || !ticketId) {
    return;
  }

  button.disabled = true;
  try {
    if (action === "view-detail") {
      await openTicketDetail(ticketId);
      return;
    }
    if (action === "managed-response") {
      await submitManagedResponse(ticketId);
    } else {
      await updateTicketStatus(ticketId, action);
    }
    await loadTickets({ refreshDetail: false });
    if (selectedTicketId === ticketId) {
      await refreshSelectedTicket({ silent: true });
    }
  } catch (error) {
    window.alert(`Action failed: ${error.message}`);
    await loadTickets({ refreshDetail: false });
    if (selectedTicketId === ticketId) {
      await refreshSelectedTicket({ silent: true });
    }
  } finally {
    button.disabled = false;
  }
}

async function handleDetailClick(event) {
  const button = event.target.closest("button[data-detail-action]");
  if (!button || !selectedTicketId) {
    return;
  }

  const action = button.dataset.detailAction;
  if (!action) {
    return;
  }

  if (action !== "managed-response") {
    return;
  }

  button.disabled = true;
  try {
    await submitManagedResponse(selectedTicketId);

    await loadTickets({ refreshDetail: false });
    await refreshSelectedTicket({ silent: true });
  } catch (error) {
    window.alert(`Operation failed: ${error.message}`);
    await loadTickets({ refreshDetail: false });
    await refreshSelectedTicket({ silent: true });
  } finally {
    button.disabled = false;
  }
}

function statusValueToAction(status) {
  if (status === "resolved") {
    return "resolved";
  }
  if (status === "waiting_for_engineer") {
    return "handoff";
  }
  return "reopen";
}

async function handleDetailChange(event) {
  const statusSelect = event.target.closest("#detail-status-select");
  if (statusSelect && selectedTicketId) {
    const nextStatus = String(statusSelect.value || "open");
    const currentStatus = String(selectedTicket?.status || "open");
    if (nextStatus === currentStatus) {
      return;
    }
    const action = statusValueToAction(nextStatus);

    statusSelect.disabled = true;
    try {
      await updateTicketStatus(selectedTicketId, action);
      await loadTickets({ refreshDetail: false });
      await refreshSelectedTicket({ silent: true });
    } catch (error) {
      window.alert(`Status update failed: ${error.message}`);
      await refreshSelectedTicket({ silent: true });
    } finally {
      statusSelect.disabled = false;
    }
    return;
  }

  const modeSelect = event.target.closest("#detail-mode-select");
  if (!modeSelect || !selectedTicketId) {
    return;
  }

  const mode = String(modeSelect.value || "managed");
  modeSelect.disabled = true;
  try {
    await updateTicketMode(selectedTicketId, mode);
    await loadTickets({ refreshDetail: false });
    await refreshSelectedTicket({ silent: true });
  } catch (error) {
    window.alert(`Mode update failed: ${error.message}`);
    await refreshSelectedTicket({ silent: true });
  } finally {
    modeSelect.disabled = false;
  }
}

function closeSocket() {
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

function setupWebSocket() {
  if (!isAuthenticated()) {
    return;
  }

  closeSocket();

  const protocol = window.location.protocol === "https:" ? "wss" : "ws";
  socket = new WebSocket(`${protocol}://${window.location.host}/ws/engineer`);

  socket.onopen = () => {
    setRealtimeStatus("Realtime: connected");
  };

  socket.onmessage = async () => {
    try {
      await loadTickets({ refreshDetail: true });
    } catch (error) {
      showBoardError(`Failed to refresh tickets: ${error.message}`);
    }
  };

  socket.onerror = () => {
    setRealtimeStatus("Realtime: error");
  };

  socket.onclose = () => {
    setRealtimeStatus("Realtime: disconnected (reconnecting...)");
    if (heartbeatTimer) {
      clearInterval(heartbeatTimer);
      heartbeatTimer = null;
    }
    if (isAuthenticated()) {
      reconnectTimer = setTimeout(() => {
        setupWebSocket();
      }, 1500);
    }
  };

  heartbeatTimer = setInterval(() => {
    if (socket && socket.readyState === WebSocket.OPEN) {
      socket.send("ping");
    }
  }, 10000);
}

async function enterBoard() {
  toggleScreens();
  await detectStorageMode();
  setRealtimeStatus("Realtime: connecting...");
  try {
    await loadTickets({ refreshDetail: true });
  } catch (error) {
    showBoardError(`Failed to load tickets: ${error.message}`);
  }
  setupWebSocket();
}

function resetLoginForm() {
  loginFormEl.reset();
  loginErrorEl.textContent = "";
}

async function handleLoginSubmit(event) {
  event.preventDefault();
  const formData = new FormData(loginFormEl);
  const username = String(formData.get("username") || "").trim();
  const password = String(formData.get("password") || "").trim();

  if (username !== LOGIN_USER || password !== LOGIN_PASS) {
    loginErrorEl.textContent = "Invalid credentials. Use eng / eng.";
    return;
  }

  setAuthenticated(true);
  resetLoginForm();
  await enterBoard();
}

function handleLogout() {
  setAuthenticated(false);
  storageMode = "unknown";
  closeSocket();
  tickets = [];
  closeTicketDetail();
  toggleScreens();
  resetLoginForm();
}

loginFormEl.addEventListener("submit", (event) => {
  handleLoginSubmit(event).catch((error) => {
    loginErrorEl.textContent = `Login failed: ${error.message}`;
  });
});

logoutBtnEl.addEventListener("click", handleLogout);
statusFilterEl.addEventListener("change", () => {
  loadTickets({ refreshDetail: true }).catch((error) => {
    showBoardError(`Failed to load tickets: ${error.message}`);
  });
});
ticketTableBodyEl.addEventListener("click", (event) => {
  handleTableClick(event).catch((error) => {
    showBoardError(`Operation failed: ${error.message}`);
  });
});
detailBodyEl.addEventListener("click", (event) => {
  handleDetailClick(event).catch((error) => {
    window.alert(`Operation failed: ${error.message}`);
  });
});
detailBodyEl.addEventListener("change", (event) => {
  handleDetailChange(event).catch((error) => {
    window.alert(`Operation failed: ${error.message}`);
  });
});
detailCloseBtnEl.addEventListener("click", closeTicketDetail);
detailModalEl.addEventListener("click", (event) => {
  if (event.target === detailModalEl) {
    closeTicketDetail();
  }
});

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && !detailModalEl.classList.contains("hidden")) {
    closeTicketDetail();
  }
});

if (isAuthenticated()) {
  enterBoard();
} else {
  toggleScreens();
  setRealtimeStatus("Realtime: signed out");
}
