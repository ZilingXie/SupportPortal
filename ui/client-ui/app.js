const appRoot = document.getElementById("app");
const toastRoot = document.getElementById("toast-root");

const AUTH_KEY = "helpdesk_auth_user";
const TICKETS_KEY = "helpdesk_tickets";
const COUNTER_KEY = "helpdesk_ticket_counter";
const MAX_RECENT = 5;

const DEMO_USERS = [{ id: "user-1", name: "Admin", email: "admin", password: "admin" }];
const ENGINEER_ASSISTANCE_WAIT_TEXT = "Estimate waiting time: 3 hours";
const ENGINEER_ASSISTANCE_ESCALATION_MESSAGE =
  "your request has been escalated to an engineer, and he/she will contact you at earlist possible. Estimated waiting time: 3 hours.";

const STATUS_CONFIG = {
  new: { label: "New", className: "status-new" },
  waiting_for_support: { label: "Waiting for Support", className: "status-waiting_for_support" },
  waiting_for_agent: { label: "Waiting for Customer", className: "status-waiting_for_agent" },
  escalated: { label: "Waiting for Engineer", className: "status-escalated" },
  resolved: { label: "Resolved", className: "status-resolved" },
};

const STATUS_FILTER_OPTIONS = [
  { value: "all", label: "All Statuses" },
  ...Object.entries(STATUS_CONFIG).map(([value, config]) => ({
    value,
    label: config.label,
  })),
];

const FEATURES = [
  {
    icon: "dns",
    label: "Server Troubleshooting",
    desc: "Linux, Windows, and service diagnostics",
  },
  {
    icon: "security",
    label: "Security Response",
    desc: "Incident follow-up and escalation guidance",
  },
  {
    icon: "storage",
    label: "Database Operations",
    desc: "MySQL, PostgreSQL, and cache issue support",
  },
  {
    icon: "cloud",
    label: "Cloud Services",
    desc: "AWS, Azure, and GCP request handling",
  },
];

const state = {
  user: getCurrentUser(),
  view: "login",
  activeTicketId: null,
  statusFilter: "all",
  isSending: false,
  loginError: "",
  isSubmittingLogin: false,
  inputDraft: "",
  editingMessageId: null,
  pendingAbortController: null,
  pendingTicketId: null,
  pendingUserMessageId: null,
  pendingAsyncTicketId: null,
  pendingAsyncMessageCreatedAt: null,
};
let clientSocket = null;
let clientReconnectTimer = null;
let clientHeartbeatTimer = null;
let pendingStatusPollTimer = null;
const engineerAssistanceRequestedTicketIds = new Set();

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function sanitizeUrl(value) {
  const raw = String(value || "").trim();
  if (!raw) {
    return null;
  }
  const markdownMatch = raw.match(/\((https?:\/\/[^)\s]+)\)/i);
  const candidate = markdownMatch ? markdownMatch[1] : raw;
  const trimmed = candidate.replace(/[)\],.;]+$/g, "");

  const withProtocol = /^[a-z]+:\/\//i.test(trimmed)
    ? trimmed
    : /^[\w.-]+\.[a-z]{2,}(\/.*)?$/i.test(trimmed)
    ? `https://${trimmed}`
    : trimmed;

  try {
    const parsed = new URL(withProtocol);
    if (parsed.protocol === "http:" || parsed.protocol === "https:") {
      return parsed.toString();
    }
  } catch {
    return null;
  }
  return null;
}

function formatMultilineText(value) {
  return escapeHtml(value).replaceAll("\n", "<br>");
}

function normalizeCitationItem(item) {
  const sourcePathRaw = String(item?.sourcePath ?? item?.source_path ?? "").trim();
  const sourceUrl =
    sanitizeUrl(item?.sourceUrl ?? item?.source_url ?? item?.url) ||
    sanitizeUrl(sourcePathRaw);
  return {
    heading: String(item?.heading ?? item?.title ?? "").trim(),
    sourcePath: sourcePathRaw,
    sourceUrl,
  };
}

function normalizeCitations(payload) {
  if (Array.isArray(payload?.citations) && payload.citations.length > 0) {
    return payload.citations
      .map((item) => normalizeCitationItem(item))
      .filter((item) => item.sourceUrl || item.heading || item.sourcePath);
  }

  if (Array.isArray(payload?.sources) && payload.sources.length > 0) {
    return payload.sources
      .map((source) => normalizeCitationItem({ source_url: source }))
      .filter((item) => item.sourceUrl);
  }

  return [];
}

function renderCitationsHtml(citations) {
  if (!Array.isArray(citations) || citations.length === 0) {
    return "";
  }
  const items = citations
    .map((citation, index) => {
      const heading = citation.heading
        ? escapeHtml(citation.heading)
        : citation.sourcePath
        ? escapeHtml(citation.sourcePath)
        : `Reference ${index + 1}`;
      if (citation.sourceUrl) {
        return `<a class="citation-pill" href="${escapeHtml(citation.sourceUrl)}" target="_blank" rel="noopener noreferrer">${heading}</a>`;
      }
      return `<span class="citation-pill">${heading}</span>`;
    })
    .join("");
  return `
    <div class="citations">
      <div class="citation-title">Source Context</div>
      <div class="citation-list">${items}</div>
    </div>
  `;
}

function renderMessageBody(message) {
  const base = `<div>${formatMultilineText(message.content || "")}</div>`;
  if (message.role !== "assistant") {
    return base;
  }
  return `${base}${renderCitationsHtml(message.citations || [])}`;
}

function toast(message, kind = "") {
  const node = document.createElement("div");
  node.className = `toast ${kind}`.trim();
  node.textContent = message;
  toastRoot.appendChild(node);
  setTimeout(() => {
    node.remove();
  }, 2600);
}

function getCurrentUser() {
  try {
    const raw = localStorage.getItem(AUTH_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

function login(email, password) {
  const normalizedEmail = String(email || "").trim().toLowerCase();
  const normalizedPassword = String(password || "").trim();
  if (normalizedEmail === "admin" && normalizedPassword === "admin") {
    const fallback = DEMO_USERS[0];
    const userData = { id: fallback.id, name: fallback.name, email: fallback.email };
    localStorage.setItem(AUTH_KEY, JSON.stringify(userData));
    return userData;
  }
  const match = DEMO_USERS.find(
    (user) => user.email.toLowerCase() === normalizedEmail && user.password === normalizedPassword
  );
  if (!match) {
    return null;
  }
  const userData = { id: match.id, name: match.name, email: match.email };
  localStorage.setItem(AUTH_KEY, JSON.stringify(userData));
  return userData;
}

function logout() {
  clearPendingRequestState();
  closeClientRealtimeConnection();
  engineerAssistanceRequestedTicketIds.clear();
  localStorage.removeItem(AUTH_KEY);
  state.user = null;
}

function closeClientRealtimeConnection() {
  if (clientReconnectTimer) {
    clearTimeout(clientReconnectTimer);
    clientReconnectTimer = null;
  }
  if (clientHeartbeatTimer) {
    clearInterval(clientHeartbeatTimer);
    clientHeartbeatTimer = null;
  }
  if (clientSocket) {
    clientSocket.onclose = null;
    clientSocket.close();
    clientSocket = null;
  }
}

function stopPendingStatusPolling() {
  if (!pendingStatusPollTimer) {
    return;
  }
  clearInterval(pendingStatusPollTimer);
  pendingStatusPollTimer = null;
}

function clearPendingRequestState() {
  state.isSending = false;
  state.pendingAbortController = null;
  state.pendingTicketId = null;
  state.pendingUserMessageId = null;
  state.pendingAsyncTicketId = null;
  state.pendingAsyncMessageCreatedAt = null;
  stopPendingStatusPolling();
}

function isTicketSending(ticketId) {
  return (
    state.isSending &&
    String(state.pendingTicketId || "").trim() === String(ticketId || "").trim()
  );
}

function ticketHasAssistantReply(ticket) {
  const messages = Array.isArray(ticket?.messages) ? ticket.messages : [];
  if (messages.length === 0) {
    return false;
  }
  const pendingUserId = String(state.pendingUserMessageId || "").trim();
  if (pendingUserId) {
    const index = messages.findIndex((message) => String(message?.id || "").trim() === pendingUserId);
    if (index >= 0) {
      return (
        messages
          .slice(index + 1)
          .filter((message) => String(message?.role || "").toLowerCase() !== "user").length >= 2
      );
    }
  }

  const pendingCreatedAt = String(state.pendingAsyncMessageCreatedAt || "").trim();
  if (pendingCreatedAt) {
    const index = messages.findIndex(
      (message) =>
        String(message?.role || "").toLowerCase() === "user" &&
        String(message?.createdAt || "").trim() === pendingCreatedAt
    );
    if (index >= 0) {
      return (
        messages
          .slice(index + 1)
          .filter((message) => String(message?.role || "").toLowerCase() !== "user").length >= 2
      );
    }

    const trailingMessages = messages.slice(-2);
    return (
      trailingMessages.length === 2 &&
      trailingMessages.every((message) => String(message?.role || "").toLowerCase() !== "user")
    );
  }

  return String(messages[messages.length - 1]?.role || "").toLowerCase() !== "user";
}

function ensurePendingStatusPolling() {
  if (
    pendingStatusPollTimer ||
    !state.user ||
    !state.isSending ||
    !String(state.pendingAsyncTicketId || "").trim()
  ) {
    return;
  }

  pendingStatusPollTimer = setInterval(() => {
    if (!state.user || !state.isSending || !String(state.pendingAsyncTicketId || "").trim()) {
      stopPendingStatusPolling();
      return;
    }

    syncTicketsFromBackend({ silent: true })
      .then(() => {
        const pendingTicket = getTicketById(state.pendingAsyncTicketId);
        if (!pendingTicket || ticketHasAssistantReply(pendingTicket)) {
          clearPendingRequestState();
        }
        render();
      })
      .catch(() => {
        // Keep waiting; websocket or next poll may recover.
      });
  }, 3000);
}

function scheduleClientRealtimeReconnect() {
  if (!state.user || clientReconnectTimer) {
    return;
  }
  clientReconnectTimer = setTimeout(() => {
    clientReconnectTimer = null;
    setupClientRealtimeConnection();
  }, 1500);
}

function setupClientRealtimeConnection() {
  if (!state.user) {
    closeClientRealtimeConnection();
    return;
  }
  if (
    clientSocket &&
    (clientSocket.readyState === WebSocket.OPEN || clientSocket.readyState === WebSocket.CONNECTING)
  ) {
    return;
  }

  const protocol = window.location.protocol === "https:" ? "wss" : "ws";
  clientSocket = new WebSocket(`${protocol}://${window.location.host}/ws/client`);

  clientSocket.onopen = () => {
    if (clientHeartbeatTimer) {
      clearInterval(clientHeartbeatTimer);
    }
    clientHeartbeatTimer = setInterval(() => {
      if (clientSocket && clientSocket.readyState === WebSocket.OPEN) {
        clientSocket.send("ping");
      }
    }, 10000);
  };

  clientSocket.onmessage = async (event) => {
    if (!state.user) {
      return;
    }
    let payload = null;
    try {
      payload = JSON.parse(event.data);
    } catch {
      return;
    }
    const customerId = String(payload?.customer_id || "").trim();
    if (customerId && customerId !== state.user.id) {
      return;
    }
    const eventName = String(payload?.event || "").trim().toLowerCase();
    const eventTicketId = String(payload?.ticket_id || "").trim();
    if (
      state.pendingAsyncTicketId &&
      eventTicketId === state.pendingAsyncTicketId &&
      (eventName === "ticket_ai_response_ready" || eventName === "ticket_ai_generation_stopped")
    ) {
      clearPendingRequestState();
    }
    await syncTicketsFromBackend({ silent: true });
    if (state.user) {
      render();
    }
  };

  clientSocket.onclose = () => {
    if (clientHeartbeatTimer) {
      clearInterval(clientHeartbeatTimer);
      clientHeartbeatTimer = null;
    }
    clientSocket = null;
    scheduleClientRealtimeReconnect();
  };

  clientSocket.onerror = () => {
    // Connection state handled by onclose.
  };
}

function getCounter() {
  const raw = localStorage.getItem(COUNTER_KEY);
  return raw ? Number(raw) : 0;
}

function setCounter(value) {
  localStorage.setItem(COUNTER_KEY, String(value));
}

function toTimestamp(value) {
  const parsed = new Date(String(value || "")).getTime();
  return Number.isFinite(parsed) ? parsed : 0;
}

function pickPreferredTicket(current, candidate) {
  const currentUpdated = toTimestamp(current?.updatedAt || current?.createdAt);
  const candidateUpdated = toTimestamp(candidate?.updatedAt || candidate?.createdAt);
  if (candidateUpdated !== currentUpdated) {
    return candidateUpdated > currentUpdated ? candidate : current;
  }

  const currentMessageCount = Array.isArray(current?.messages) ? current.messages.length : 0;
  const candidateMessageCount = Array.isArray(candidate?.messages) ? candidate.messages.length : 0;
  if (candidateMessageCount !== currentMessageCount) {
    return candidateMessageCount > currentMessageCount ? candidate : current;
  }

  const currentTitle = String(current?.title || "").trim();
  const candidateTitle = String(candidate?.title || "").trim();
  if (
    currentTitle === "New Session" &&
    candidateTitle.length > 0 &&
    candidateTitle !== "New Session"
  ) {
    return candidate;
  }

  return current;
}

function dedupeTickets(tickets) {
  const byId = new Map();
  for (const ticket of Array.isArray(tickets) ? tickets : []) {
    const ticketId = String(ticket?.id || "").trim();
    if (!ticketId) {
      continue;
    }
    const existing = byId.get(ticketId);
    if (!existing) {
      byId.set(ticketId, ticket);
      continue;
    }
    byId.set(ticketId, pickPreferredTicket(existing, ticket));
  }
  return Array.from(byId.values());
}

function getAllTickets() {
  try {
    const raw = localStorage.getItem(TICKETS_KEY);
    const parsed = raw ? JSON.parse(raw) : [];
    return dedupeTickets(parsed);
  } catch {
    return [];
  }
}

function saveAllTickets(tickets) {
  localStorage.setItem(TICKETS_KEY, JSON.stringify(dedupeTickets(tickets)));
}

function mapBackendRoleToClientRole(role) {
  const normalized = String(role || "").toLowerCase();
  if (normalized === "customer") {
    return "user";
  }
  if (normalized === "engineer") {
    return "engineer";
  }
  return "assistant";
}

function mapBackendStatusToClientStatus(ticket) {
  const status = String(ticket?.status || "open").toLowerCase();
  if (status === "resolved") {
    return "resolved";
  }
  if (status === "escalated") {
    return "escalated";
  }
  if (status === "waiting_for_engineer") {
    return "escalated";
  }

  const messages = Array.isArray(ticket?.messages) ? ticket.messages : [];
  const latest = messages.length > 0 ? messages[messages.length - 1] : null;
  const latestRole = String(latest?.role || "").toLowerCase();
  if (latestRole === "customer") {
    return "waiting_for_support";
  }
  return "waiting_for_agent";
}

function normalizeBackendTicket(ticket) {
  const ticketId = String(ticket?.ticket_id || "").trim();
  if (!ticketId) {
    return null;
  }
  const createdAt = String(ticket?.created_at || new Date().toISOString());
  const updatedAt = String(ticket?.updated_at || createdAt);
  const messages = Array.isArray(ticket?.messages) ? ticket.messages : [];

  return {
    id: ticketId,
    title: String(ticket?.subject || "New Session"),
    status: mapBackendStatusToClientStatus(ticket),
    createdAt,
    updatedAt,
    userId: String(ticket?.customer_id || ""),
    messages: messages.map((message, index) => ({
      id:
        String(message?.id || "").trim() ||
        `${ticketId}-m-${String(message?.created_at || "")}-${index}`,
      role: mapBackendRoleToClientRole(message?.role),
      content: String(message?.content || ""),
      createdAt: String(message?.created_at || updatedAt),
      citations: normalizeCitations({
        citations: Array.isArray(message?.citations) ? message.citations : [],
        sources: Array.isArray(message?.sources) ? message.sources : [],
      }),
    })),
  };
}

async function syncTicketsFromBackend(options = {}) {
  const { silent = false } = options;
  if (!state.user?.id) {
    return;
  }
  try {
    const response = await fetch("/api/engineer/tickets?status=all");
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }
    const payload = await response.json();
    const incoming = Array.isArray(payload?.tickets) ? payload.tickets : [];
    const mapped = incoming
      .filter((ticket) => String(ticket?.customer_id || "") === state.user.id)
      .map(normalizeBackendTicket)
      .filter(Boolean);

    const allLocal = getAllTickets();
    const otherUsersLocal = allLocal.filter((ticket) => ticket.userId !== state.user.id);
    saveAllTickets([...otherUsersLocal, ...mapped]);
  } catch (error) {
    if (!silent) {
      toast(`Failed to sync sessions from backend: ${error.message}`, "error");
    }
  }
}

function getTicketsByUser(userId) {
  return getAllTickets()
    .filter((ticket) => ticket.userId === userId)
    .sort((a, b) => new Date(b.updatedAt).getTime() - new Date(a.updatedAt).getTime());
}

function getTicketById(ticketId) {
  const normalizedId = String(ticketId || "").trim();
  if (!normalizedId) {
    return null;
  }
  const matches = getAllTickets().filter((ticket) => String(ticket?.id || "").trim() === normalizedId);
  if (matches.length === 0) {
    return null;
  }
  matches.sort(
    (a, b) => toTimestamp(b?.updatedAt || b?.createdAt) - toTimestamp(a?.updatedAt || a?.createdAt)
  );
  return matches[0];
}

function createUniqueTicketId() {
  const existingIds = new Set(
    getAllTickets()
      .map((ticket) => String(ticket?.id || "").trim())
      .filter(Boolean)
  );
  const numericSeeds = Array.from(existingIds)
    .map((id) => {
      const match = id.match(/^TK-(\d+)$/i);
      return match ? Number(match[1]) : 0;
    })
    .filter((value) => Number.isFinite(value));
  let next = Math.max(getCounter(), ...numericSeeds, 0);

  for (let attempt = 0; attempt < 10000; attempt += 1) {
    next += 1;
    const candidate = `TK-${String(next).padStart(3, "0")}`;
    if (!existingIds.has(candidate)) {
      setCounter(next);
      return candidate;
    }
  }

  // Fallback for extreme edge cases where incremental IDs are exhausted locally.
  let randomId = "";
  do {
    randomId = `T-${crypto.randomUUID().slice(0, 6).toUpperCase()}`;
  } while (existingIds.has(randomId));
  return randomId;
}

function createTicket(userId) {
  const now = new Date().toISOString();
  const ticketId = createUniqueTicketId();
  const ticket = {
    id: ticketId,
    title: "New Session",
    status: "new",
    createdAt: now,
    updatedAt: now,
    userId,
    messages: [],
  };
  const all = getAllTickets();
  all.push(ticket);
  saveAllTickets(all);
  return ticket;
}

function isTicketEmpty(ticket) {
  const messages = Array.isArray(ticket?.messages) ? ticket.messages : [];
  return messages.length === 0;
}

function isReusableDraftTicket(ticket, userId) {
  const normalizedUserId = String(userId || "").trim();
  if (!normalizedUserId) {
    return false;
  }
  return (
    String(ticket?.userId || "").trim() === normalizedUserId &&
    String(ticket?.status || "").trim().toLowerCase() !== "resolved" &&
    String(ticket?.title || "New Session").trim() === "New Session" &&
    isTicketEmpty(ticket)
  );
}

function findReusableDraftTicket(userId) {
  return getAllTickets()
    .filter((ticket) => isReusableDraftTicket(ticket, userId))
    .sort((left, right) => {
      const rightTime = toTimestamp(right?.updatedAt || right?.createdAt);
      const leftTime = toTimestamp(left?.updatedAt || left?.createdAt);
      return rightTime - leftTime;
    })[0] || null;
}

function getOrCreateDraftTicket(userId) {
  return findReusableDraftTicket(userId) || createTicket(userId);
}

function openDraftTicket(userId) {
  const ticket = getOrCreateDraftTicket(userId);
  const targetPath = `/chat/${ticket.id}`;
  const targetHash = `#${targetPath}`;
  const currentTicketId = String(state.activeTicketId || "").trim();

  if (
    String(window.location.hash || "").trim() === targetHash ||
    (state.view === "chat-ticket" && currentTicketId === ticket.id)
  ) {
    return ticket;
  }

  navigate(targetPath);
  return ticket;
}

function updateTicketStatus(ticketId, status) {
  const all = getAllTickets();
  const idx = all.findIndex((ticket) => ticket.id === ticketId);
  if (idx < 0) {
    return;
  }
  all[idx].status = status;
  all[idx].updatedAt = new Date().toISOString();
  saveAllTickets(all);
}

function appendTicketMessage(ticketId, message) {
  const ticket = getTicketById(ticketId);
  if (!ticket) {
    return false;
  }
  saveTicketMessages(ticketId, [...(ticket.messages || []), message]);
  return true;
}

function updateTicketTitle(ticketId, title) {
  const all = getAllTickets();
  const idx = all.findIndex((ticket) => ticket.id === ticketId);
  if (idx < 0) {
    return;
  }
  all[idx].title = title;
  all[idx].updatedAt = new Date().toISOString();
  saveAllTickets(all);
}

function canRequestEngineerAssistance(ticket) {
  return Boolean(
    ticket &&
      String(ticket.status || "").trim().toLowerCase() !== "resolved" &&
      !isTicketEmpty(ticket)
  );
}

function hasRequestedEngineerAssistance(ticketId) {
  const normalizedId = String(ticketId || "").trim();
  if (!normalizedId) {
    return false;
  }
  return engineerAssistanceRequestedTicketIds.has(normalizedId);
}

function clearEngineerAssistanceRequest(ticketId) {
  const normalizedId = String(ticketId || "").trim();
  if (!normalizedId) {
    return;
  }
  engineerAssistanceRequestedTicketIds.delete(normalizedId);
}

function requestEngineerAssistance(ticketId) {
  const normalizedId = String(ticketId || "").trim();
  const ticket = getTicketById(normalizedId);
  if (!normalizedId || !canRequestEngineerAssistance(ticket)) {
    return false;
  }
  if (engineerAssistanceRequestedTicketIds.has(normalizedId)) {
    return false;
  }
  appendTicketMessage(normalizedId, {
    id: crypto.randomUUID(),
    role: "assistant",
    content: ENGINEER_ASSISTANCE_ESCALATION_MESSAGE,
    createdAt: new Date().toISOString(),
  });
  updateTicketStatus(normalizedId, "escalated");
  engineerAssistanceRequestedTicketIds.add(normalizedId);
  return true;
}

function saveTicketMessages(ticketId, messages) {
  const all = getAllTickets();
  const idx = all.findIndex((ticket) => ticket.id === ticketId);
  if (idx < 0) {
    return;
  }
  all[idx].messages = messages;
  all[idx].updatedAt = new Date().toISOString();
  saveAllTickets(all);
}

function formatDate(iso) {
  return new Date(iso).toLocaleDateString("en-US", {
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function statusBadge(status) {
  const config = STATUS_CONFIG[status] || STATUS_CONFIG.new;
  return `<span class="status-badge ${config.className}">${config.label}</span>`;
}

function buildHistoryRowActions(ticket) {
  return `
    <div class="history-row-actions">
      <button class="btn btn-ghost btn-inline" data-action="open-ticket" data-ticket-id="${ticket.id}" type="button">Open</button>
      ${
        ticket.status === "resolved"
          ? `<button class="btn btn-outline btn-inline" data-action="reopen-ticket" data-ticket-id="${ticket.id}" type="button">Reopen</button>`
          : isTicketEmpty(ticket)
          ? ""
          : `<button class="btn btn-outline btn-inline" data-action="resolve-ticket" data-ticket-id="${ticket.id}" type="button">Resolve</button>`
      }
    </div>
  `;
}

function renderHistoryRowMeta(ticket) {
  return `
    <div class="history-row-meta">
      <span><strong>Created</strong> ${escapeHtml(formatDate(ticket.createdAt))}</span>
      <span><strong>Updated</strong> ${escapeHtml(formatDate(ticket.updatedAt))}</span>
    </div>
  `;
}

function renderHistoryRow(ticket, options = {}) {
  const { compact = false, active = false, includeActions = false } = options;
  const classes = ["history-row"];
  if (compact) {
    classes.push("history-row-compact");
  }
  if (active) {
    classes.push("is-active");
  }

  return `
    <article
      class="${classes.join(" ")}"
      role="button"
      tabindex="0"
      data-history-ticket-row="true"
      data-ticket-id="${ticket.id}"
      aria-label="Open session ${escapeHtml(ticket.id)}"
    >
      <div class="history-row-header">
        <div class="history-row-title-group">
          <div class="history-row-headline">
            <p class="history-row-kicker mono">${escapeHtml(ticket.id)}</p>
            <h3 class="history-row-title">${escapeHtml(ticket.title)}</h3>
          </div>
        </div>
        <div class="history-row-badges">${statusBadge(ticket.status)}</div>
      </div>
      <div class="history-row-secondary">
        ${renderHistoryRowMeta(ticket)}
        ${includeActions ? buildHistoryRowActions(ticket) : ""}
      </div>
    </article>
  `;
}

const HISTORY_ROW_INTERACTIVE_SELECTOR = [
  "button",
  "a",
  "input",
  "select",
  "textarea",
  "summary",
  '[role="button"]',
  '[role="link"]',
].join(", ");

function openTicketChat(ticketId) {
  const normalizedId = String(ticketId || "").trim();
  if (!normalizedId) {
    return;
  }
  navigate(`/chat/${normalizedId}`);
}

function getHistoryRowTarget(target) {
  if (!target || typeof target.closest !== "function") {
    return null;
  }
  const row = target.closest("[data-history-ticket-row]");
  if (!row) {
    return null;
  }
  const interactive = target.closest(HISTORY_ROW_INTERACTIVE_SELECTOR);
  if (interactive && interactive !== row) {
    return null;
  }
  return row;
}

function handleHistoryRowClick(event) {
  const row = getHistoryRowTarget(event.target);
  if (!row) {
    return;
  }
  openTicketChat(row.dataset.ticketId);
}

function handleHistoryRowKeydown(event) {
  if (event.key !== "Enter" && event.key !== " " && event.key !== "Spacebar") {
    return;
  }
  const row = getHistoryRowTarget(event.target);
  if (!row) {
    return;
  }
  event.preventDefault();
  openTicketChat(row.dataset.ticketId);
}

function getStatusFilterOption(value) {
  return STATUS_FILTER_OPTIONS.find((option) => option.value === value) || STATUS_FILTER_OPTIONS[0];
}

function renderStatusFilter() {
  const selectedOption = getStatusFilterOption(state.statusFilter);
  return `
    <div class="filter-select" data-filter-select>
      <input id="status-filter" type="hidden" value="${escapeHtml(selectedOption.value)}" />
      <button
        class="filter-select-trigger"
        data-status-filter-trigger
        type="button"
        role="combobox"
        aria-label="Filter sessions by status"
        aria-controls="status-filter-listbox"
        aria-expanded="false"
        aria-haspopup="listbox"
      >
        <span class="filter-select-trigger-label">${escapeHtml(selectedOption.label)}</span>
        <span class="filter-select-trigger-icon" aria-hidden="true">
          <span class="material-symbols-outlined">expand_more</span>
        </span>
      </button>
      <div class="filter-select-panel" data-status-filter-panel hidden>
        <div class="filter-select-options" id="status-filter-listbox" role="listbox" aria-label="Session status filter">
          ${STATUS_FILTER_OPTIONS.map((option, index) => {
            const isSelected = option.value === selectedOption.value;
            return `
              <button
                class="filter-select-option ${isSelected ? "is-selected" : ""}"
                data-status-filter-option
                data-value="${escapeHtml(option.value)}"
                id="status-filter-option-${index}"
                type="button"
                role="option"
                aria-selected="${isSelected ? "true" : "false"}"
              >
                <span class="filter-select-option-copy">${escapeHtml(option.label)}</span>
                <span class="filter-select-check material-symbols-outlined" aria-hidden="true">check</span>
              </button>
            `;
          }).join("")}
        </div>
      </div>
    </div>
  `;
}

function parseRoute() {
  const hash = window.location.hash || "#/chat";
  const path = hash.replace(/^#/, "");
  if (!state.user) {
    state.view = "login";
    return;
  }
  if (path.startsWith("/tickets")) {
    state.view = "tickets";
    state.activeTicketId = null;
    return;
  }
  if (path.startsWith("/chat/")) {
    state.view = "chat-ticket";
    state.activeTicketId = path.split("/")[2] || null;
    return;
  }
  state.view = "chat-home";
  state.activeTicketId = null;
}

function navigate(path) {
  const target = `#${path}`;
  if (window.location.hash === target) {
    parseRoute();
    render();
    return;
  }
  window.location.hash = target;
}

function renderLogin() {
  appRoot.innerHTML = `
    <div class="page-auth">
      <div class="auth-backdrop auth-backdrop-primary" aria-hidden="true"></div>
      <div class="auth-backdrop auth-backdrop-secondary" aria-hidden="true"></div>
      <div class="auth-wrap">
        <section class="auth-intro">
          <div class="brand-head">
            <div class="brand-icon">
              <span class="material-symbols-outlined" aria-hidden="true">support_agent</span>
            </div>
            <div>
              <p class="brand-kicker">Client Workspace</p>
              <h1>Concierge AI</h1>
            </div>
          </div>
          <p class="auth-copy">
            Start a technical support conversation, track active ticket context, and return to prior
            sessions without losing the AI thread.
          </p>
          <div class="auth-highlights">
            <div class="auth-highlight">
              <span class="material-symbols-outlined" aria-hidden="true">bolt</span>
              <div>
                <strong>AI-guided intake</strong>
                <p>Calmer issue reporting with structured follow-up.</p>
              </div>
            </div>
            <div class="auth-highlight">
              <span class="material-symbols-outlined" aria-hidden="true">auto_stories</span>
              <div>
                <strong>Citation-aware replies</strong>
                <p>Responses can carry source context and next-step guidance.</p>
              </div>
            </div>
          </div>
        </section>
        <section class="panel auth-panel">
          <div class="panel-header">
            <h2 class="panel-title">Sign In</h2>
            <p class="panel-desc">Enter your credentials to access Concierge AI.</p>
          </div>
          <div class="panel-body">
            <form id="login-form" class="stack">
              ${
                state.loginError
                  ? `<div class="error-box">${escapeHtml(state.loginError)}</div>`
                  : ""
              }
              <div class="field">
                <label for="username">Username</label>
                <input class="input" id="username" name="username" type="text" placeholder="admin" required />
              </div>
              <div class="field">
                <label for="password">Password</label>
                <input class="input" id="password" name="password" type="password" placeholder="admin" required />
              </div>
              <button class="btn btn-primary w-full" type="submit" ${
                state.isSubmittingLogin ? "disabled" : ""
              }>
                ${state.isSubmittingLogin ? "Signing in..." : "Sign In"}
              </button>
            </form>
          </div>
        </section>
      </div>
      <section class="demo-box">
        <div><strong>Demo Account</strong></div>
        <div>Username: admin / Password: admin</div>
      </section>
    </div>
  `;

  const form = document.getElementById("login-form");
  form?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const username = document.getElementById("username")?.value?.trim() || "";
    const password = document.getElementById("password")?.value || "";
    state.loginError = "";
    const result = login(username, password);
    if (!result) {
      state.isSubmittingLogin = false;
      state.loginError = "Invalid username or password. Please try again.";
      render();
      return;
    }
    state.user = result;
    state.isSubmittingLogin = false;
    await syncTicketsFromBackend({ silent: true });
    navigate("/chat");
  });
}

function renderSidebarNav() {
  return `
    <button class="sidebar-nav-item" data-action="new-session" type="button">
      <span class="material-symbols-outlined" aria-hidden="true">bolt</span>
      <span class="sidebar-nav-label">New Session</span>
    </button>
    <button
      class="sidebar-nav-item ${state.view !== "tickets" ? "active" : ""}"
      data-action="go-chat"
      type="button"
    >
      <span class="material-symbols-outlined" aria-hidden="true">chat</span>
      <span class="sidebar-nav-label">Workspace</span>
    </button>
    <button
      class="sidebar-nav-item ${state.view === "tickets" ? "active" : ""}"
      data-action="go-tickets"
      type="button"
    >
      <span class="material-symbols-outlined" aria-hidden="true">confirmation_number</span>
      <span class="sidebar-nav-label">Session History</span>
    </button>
  `;
}

function renderSidebarContent() {
  const tickets = getTicketsByUser(state.user.id);
  const recent = tickets.slice(0, MAX_RECENT);
  return `
    <div class="sidebar-label">Recent Sessions</div>
    ${
      recent.length === 0
        ? `<p class="session-empty">No sessions yet. Start a conversation to build your history.</p>`
        : recent
            .map(
              (ticket) =>
                renderHistoryRow(ticket, {
                  compact: true,
                  active: state.activeTicketId === ticket.id,
                  includeActions: false,
                })
            )
            .join("")
    }
  `;
}

function renderSidebarFooter() {
  return `
    <button class="btn btn-ghost sidebar-footer-btn" data-action="go-tickets" type="button">View All Sessions</button>
    <div class="user-row">
      <div class="user-meta">
        <span class="user-name">${escapeHtml(state.user.name)}</span>
        <span class="user-email">${escapeHtml(state.user.email)}</span>
      </div>
      <button class="btn btn-ghost btn-icon" data-action="logout" type="button" aria-label="Logout">
        <span class="material-symbols-outlined" aria-hidden="true">logout</span>
      </button>
    </div>
  `;
}

function renderAuthedShell() {
  return `
    <div class="app-shell">
      <aside class="sidebar">
        <div class="sidebar-header">
          <div class="sidebar-brand">
            <div class="sidebar-brand-icon">
              <span class="material-symbols-outlined" aria-hidden="true">support_agent</span>
            </div>
            <div class="sidebar-brand-title">
              <span class="line-1">Concierge AI</span>
              <span class="line-2">Client Workspace</span>
            </div>
          </div>
        </div>
        <nav class="sidebar-nav" aria-label="Client navigation" data-authed-region="sidebar-nav"></nav>
        <div class="sidebar-content" data-authed-region="sidebar-content"></div>
        <div class="sidebar-footer" data-authed-region="sidebar-footer"></div>
      </aside>
      <div class="workspace-shell">
        <div data-authed-region="topbar"></div>
        <div data-authed-region="context"></div>
        <main class="main" data-authed-region="main"></main>
      </div>
    </div>
  `;
}

function ensureAuthedShell() {
  const existingShell = appRoot.querySelector(".app-shell");
  if (existingShell) {
    return existingShell;
  }
  appRoot.innerHTML = renderAuthedShell();
  return appRoot.querySelector(".app-shell");
}

function renderTopbar() {
  const ticketCount = getTicketsByUser(state.user.id).length;
  return `
    <header class="topbar">
      <div class="topbar-copy">
        <h2>Concierge AI</h2>
        <p>Technical Support</p>
      </div>
      <div class="topbar-meta">
        <div class="topbar-pill">
          <span class="topbar-pill-label">Sessions</span>
          <strong>${ticketCount}</strong>
        </div>
        <div class="topbar-user">
          <span class="material-symbols-outlined" aria-hidden="true">account_circle</span>
          <div>
            <p>${escapeHtml(state.user.name)}</p>
            <span>${escapeHtml(state.user.email)}</span>
          </div>
        </div>
      </div>
    </header>
  `;
}

function renderContextBar() {
  if (state.view === "chat-ticket") {
    const ticket = getTicketById(state.activeTicketId);
    if (ticket && ticket.userId === state.user.id) {
      const assistanceRequested =
        String(ticket.status || "").trim().toLowerCase() === "escalated" ||
        hasRequestedEngineerAssistance(ticket.id);
      const canRequestAssistance = canRequestEngineerAssistance(ticket) && !assistanceRequested;
      const assistanceControl = assistanceRequested
        ? `<span class="context-assistance-note">${escapeHtml(
            ENGINEER_ASSISTANCE_WAIT_TEXT
          )}</span>`
        : `
              <button
                class="btn btn-outline btn-inline context-assistance-btn"
                data-action="request-engineer-assistance"
                data-ticket-id="${ticket.id}"
                type="button"
              >Request Engineer Assistance</button>
            `;
      const contextChipLabel = assistanceRequested ? "Escalated" : "AI-SOLVING";
      const contextChipClass = assistanceRequested ? "context-chip is-escalated" : "context-chip";
      const actionButtons =
        ticket.status === "resolved"
          ? `<button class="btn btn-outline" data-action="reopen-ticket" data-ticket-id="${ticket.id}" type="button">Reopen Ticket</button>`
          : isTicketEmpty(ticket)
          ? ""
          : `
              ${assistanceControl}
              <button class="btn btn-outline btn-danger" data-action="resolve-ticket" data-ticket-id="${ticket.id}" type="button">Close Ticket</button>
            `;
      return `
        <section class="context-bar">
          <div class="context-copy">
            <div class="${contextChipClass}">
              <span class="material-symbols-outlined" aria-hidden="true">auto_awesome</span>
              <span>${contextChipLabel}</span>
            </div>
            <div class="context-divider" aria-hidden="true"></div>
            <div class="context-ticket">
              <span class="context-ticket-title">Ticket ${escapeHtml(ticket.id)}: ${escapeHtml(ticket.title)}</span>
              ${statusBadge(ticket.status)}
            </div>
          </div>
          ${actionButtons ? `<div class="context-actions">${actionButtons}</div>` : ""}
        </section>
      `;
    }
  }

  if (state.view === "tickets") {
    return `
      <section class="context-bar context-bar-static">
        <div class="context-copy">
          <div class="context-chip">
            <span class="material-symbols-outlined" aria-hidden="true">history</span>
            <span>SESSION HISTORY</span>
          </div>
          <div class="context-divider" aria-hidden="true"></div>
          <div class="context-ticket">
            <span class="context-ticket-title">Review and reopen previous support conversations.</span>
          </div>
        </div>
        <div class="context-actions">
          <button class="btn btn-primary" data-action="new-session" type="button">Start New Session</button>
        </div>
      </section>
    `;
  }

  return "";
}

function renderChatHome() {
  return `
    <section class="welcome">
      <div class="welcome-inner">
        <div class="bot-mark">
          <span class="material-symbols-outlined" aria-hidden="true">auto_awesome</span>
        </div>
        <p class="welcome-kicker">Concierge AI</p>
        <h1 class="welcome-title">Technical support with a calmer, source-aware workspace.</h1>
        <p class="welcome-desc">
          Start a new session to describe an issue, continue an active ticket, or return to a previous
          conversation without losing its context.
        </p>
        <div class="feature-grid">
          ${FEATURES.map(
            (feature) => `
            <article class="feature-item">
              <div class="feature-icon">
                <span class="material-symbols-outlined" aria-hidden="true">${feature.icon}</span>
              </div>
              <div class="feature-label">${escapeHtml(feature.label)}</div>
              <div class="feature-desc">${escapeHtml(feature.desc)}</div>
            </article>
          `
          ).join("")}
        </div>
        <div class="welcome-actions">
          <button class="btn btn-primary" data-action="new-session" type="button">Start New Session</button>
          <button class="btn btn-outline" data-action="go-tickets" type="button">Open Session History</button>
        </div>
      </div>
    </section>
  `;
}

function renderChatTicket() {
  const ticket = getTicketById(state.activeTicketId);
  if (!ticket || ticket.userId !== state.user.id) {
    return `<div class="empty-state">Session not found.</div>`;
  }
  const sending = isTicketSending(ticket.id);
  const canCompose = !sending && ticket.status !== "resolved";
  const isEditing = Boolean(state.editingMessageId);

  if (isEditing && !ticket.messages.some((message) => message.id === state.editingMessageId)) {
    state.editingMessageId = null;
    if (!sending) {
      state.inputDraft = "";
    }
  }

  return `
    <section class="chat-root">
      <main class="chat-main">
        <div class="message-list">
          ${
            ticket.messages.length === 0
              ? `
                <div class="empty-chat">
                  <div class="bot-mark">
                    <span class="material-symbols-outlined" aria-hidden="true">auto_awesome</span>
                  </div>
                  <h3>Concierge AI</h3>
                  <p>Describe your technical issue and Concierge AI will start with the most likely next step.</p>
                </div>
              `
              : ticket.messages
                  .map(
                    (message) => {
                      const role = String(message.role || "assistant");
                      const tone = role === "user" ? "user" : role === "engineer" ? "engineer" : "assistant";
                      const author =
                        role === "user"
                          ? state.user.name
                          : role === "engineer"
                          ? "Engineer"
                          : "Concierge AI";
                      const metaTime = formatDate(message.createdAt || new Date().toISOString());
                      return `
                <article class="msg-row ${tone === "user" ? "user" : ""}">
                  <div class="msg-column">
                    <div class="message-meta ${tone === "user" ? "message-meta-user" : ""}">
                      ${
                        tone === "user"
                          ? ""
                          : `<span class="avatar ${tone}"><span class="material-symbols-outlined" aria-hidden="true">${
                              tone === "engineer" ? "engineering" : "auto_awesome"
                            }</span></span>`
                      }
                      <span class="message-author">${escapeHtml(author)}</span>
                      <span class="message-time">${escapeHtml(metaTime)}</span>
                    </div>
                    <div class="bubble ${tone}">${renderMessageBody(message)}</div>
                  </div>
                </article>
              `;
                    }
                  )
                  .join("")
          }
          ${
            sending
              ? `
            <div class="thinking-line">
              <span class="thinking-dots"><span></span><span></span><span></span></span>
              <span class="thinking-label">AI is cross-referencing system health logs...</span>
            </div>
          `
              : ""
          }
        </div>
      </main>
      <footer class="chat-input-wrap">
        ${
          sending
            ? `<div class="composer-note">checking the knowledge base... click stop to interrupt.</div>`
            : isEditing
            ? `<div class="composer-note">Editing your last message. Press Enter to resend, Shift+Enter for newline.</div>`
            : ""
        }
        <form id="chat-input-form" class="chat-input-inner">
          <textarea
            id="chat-input"
            class="textarea"
            rows="1"
            placeholder="Type your request or technical issue..."
            ${canCompose ? "" : "disabled"}
          >${escapeHtml(state.inputDraft || "")}</textarea>
          ${
            sending
              ? `
            <button
              class="composer-icon-button composer-stop-btn"
              type="button"
              data-action="stop-generation"
              aria-label="Stop Generation"
              title="Stop Generation"
            >
              <span class="material-symbols-outlined" aria-hidden="true">stop</span>
            </button>
          `
              : `
            <button
              class="composer-icon-button send-btn"
              type="submit"
              aria-label="${isEditing ? "Resend Request" : "Send Request"}"
              title="${isEditing ? "Resend Request" : "Send Request"}"
              ${canCompose ? "" : "disabled"}
            >
              <span class="material-symbols-outlined" aria-hidden="true">arrow_upward</span>
            </button>
          `
          }
        </form>
      </footer>
    </section>
  `;
}

function renderTicketsPage() {
  const all = getTicketsByUser(state.user.id);
  const filtered =
    state.statusFilter === "all"
      ? all
      : all.filter((ticket) => ticket.status === state.statusFilter);

  return `
    <section class="tickets-root">
      <header class="tickets-header">
        <div class="tickets-header-left">
          <button class="btn btn-ghost btn-icon" data-action="go-chat" type="button" aria-label="Back to workspace">
            <span class="material-symbols-outlined" aria-hidden="true">arrow_back</span>
          </button>
          <div>
            <div class="tickets-title">Session History</div>
            <p class="tickets-subtitle">Review active, waiting, and resolved support conversations.</p>
          </div>
        </div>
        <div class="tickets-actions">
          ${renderStatusFilter()}
        </div>
      </header>
      <div class="tickets-body">
        ${
          filtered.length === 0
            ? `<div class="empty-state">No sessions found.</div>`
            : `
          <div class="history-list">
                ${filtered
                  .map((ticket) =>
                    renderHistoryRow(ticket, {
                      compact: false,
                      includeActions: true,
                    })
                  )
                  .join("")}
          </div>
        `
        }
      </div>
    </section>
  `;
}

function syncChatScrollToBottom() {
  if (state.view !== "chat-ticket") {
    return;
  }
  const chatMain = appRoot.querySelector(".chat-main");
  if (!(chatMain instanceof HTMLElement)) {
    return;
  }
  requestAnimationFrame(() => {
    chatMain.scrollTop = chatMain.scrollHeight;
  });
}

function renderMainContent() {
  if (state.view === "tickets") {
    return renderTicketsPage();
  }
  if (state.view === "chat-ticket") {
    return renderChatTicket();
  }
  return renderChatHome();
}

function renderAuthed() {
  const shell = ensureAuthedShell();
  shell.querySelector('[data-authed-region="sidebar-nav"]').innerHTML = renderSidebarNav();
  shell.querySelector('[data-authed-region="sidebar-content"]').innerHTML = renderSidebarContent();
  shell.querySelector('[data-authed-region="sidebar-footer"]').innerHTML = renderSidebarFooter();
  shell.querySelector('[data-authed-region="topbar"]').innerHTML = renderTopbar();
  shell.querySelector('[data-authed-region="context"]').innerHTML = renderContextBar();
  shell.querySelector('[data-authed-region="main"]').innerHTML = renderMainContent();

  bindAuthedEvents();
}

function generateTitle(message) {
  const words = message.trim().split(/\s+/).slice(0, 6).join(" ");
  return words.length > 0 ? words : "New Session";
}

async function syncBackendTicketAction(ticketId, action) {
  try {
    await fetch(`/api/tickets/${encodeURIComponent(ticketId)}/action`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action, engineer_id: "CLIENT-UI" }),
    });
  } catch {
    toast("Action synced locally. Backend sync failed.", "error");
  }
}

async function stopGeneration() {
  if (!state.isSending || !state.pendingAbortController) {
    const pendingTicketId = String(state.pendingTicketId || state.pendingAsyncTicketId || "").trim();
    const pendingCreatedAt = String(state.pendingAsyncMessageCreatedAt || "").trim();
    if (!state.isSending || !pendingTicketId || !pendingCreatedAt) {
      return;
    }

    try {
      const response = await fetch(
        `/api/tickets/${encodeURIComponent(pendingTicketId)}/cancel-pending`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            customer_id: state.user?.id || "",
            message_created_at: pendingCreatedAt,
          }),
        }
      );
      if (!response.ok) {
        throw new Error(`Request failed with status ${response.status}`);
      }

      const activeTicket = getTicketById(pendingTicketId);
      const pendingMessage = activeTicket?.messages?.find(
        (message) => message.id === state.pendingUserMessageId && message.role === "user"
      );
      const userMessages = Array.isArray(activeTicket?.messages)
        ? activeTicket.messages.filter((message) => message.role === "user")
        : [];
      const latestUserContent =
        userMessages.length > 0 ? String(userMessages[userMessages.length - 1]?.content || "") : "";
      state.editingMessageId = state.pendingUserMessageId;
      state.inputDraft = pendingMessage?.content || latestUserContent || "";
      clearPendingRequestState();
      await syncTicketsFromBackend({ silent: true });
      render();
      toast("Generation stopped. Edit your message and resend.");
    } catch (error) {
      toast(`Failed to stop generation: ${error.message}`, "error");
    }
    return;
  }
  state.pendingAbortController.abort();
}

async function handleSendMessage(text, options = {}) {
  const ticketId = state.activeTicketId;
  const ticket = getTicketById(ticketId);
  if (!ticket || ticket.status === "resolved") {
    return;
  }
  if (state.isSending && String(state.pendingTicketId || "").trim() !== String(ticketId || "").trim()) {
    toast("Another session is still processing. Wait or stop it first.", "error");
    return;
  }
  if (isTicketSending(ticketId)) {
    return;
  }
  const editMessageId = options.editMessageId || null;
  const now = new Date().toISOString();
  let userMessageId = editMessageId;
  let messages = [];
  let keepWaitingForAsync = false;

  if (editMessageId) {
    messages = ticket.messages.map((message) => {
      if (message.id === editMessageId && message.role === "user") {
        return {
          ...message,
          content: text,
          createdAt: now,
        };
      }
      return message;
    });
    if (!messages.some((message) => message.id === editMessageId && message.role === "user")) {
      return;
    }
  } else {
    const userMessage = {
      id: crypto.randomUUID(),
      role: "user",
      content: text,
      createdAt: now,
    };
    userMessageId = userMessage.id;
    messages = [...ticket.messages, userMessage];
  }

  saveTicketMessages(ticketId, messages);
  if (ticket.title === "New Session") {
    updateTicketTitle(ticketId, generateTitle(text));
  }
  const hasEscalatedAssistance =
    String(ticket.status || "").trim().toLowerCase() === "escalated" ||
    hasRequestedEngineerAssistance(ticketId);
  updateTicketStatus(ticketId, hasEscalatedAssistance ? "escalated" : "waiting_for_support");
  state.editingMessageId = null;
  state.inputDraft = "";
  state.isSending = true;
  state.pendingTicketId = ticketId;
  state.pendingUserMessageId = userMessageId;
  state.pendingAbortController = new AbortController();
  stopPendingStatusPolling();
  render();

  try {
    const response = await fetch("/api/tickets/query", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      signal: state.pendingAbortController.signal,
      body: JSON.stringify({
        ticket_id: ticketId,
        customer_id: state.user.id,
        message: text,
      }),
    });
    if (!response.ok) {
      throw new Error(`Request failed with status ${response.status}`);
    }
    const payload = await response.json();
    const updated = getTicketById(ticketId);
    const queuedForAi = Boolean(payload?.queued_for_ai);
    if (queuedForAi) {
      keepWaitingForAsync = true;
      state.pendingAsyncTicketId = ticketId;
      state.pendingAsyncMessageCreatedAt = String(payload?.queued_message_created_at || "").trim();
    }
    const allowAssistantReply =
      payload?.ai_replied !== false && String(payload?.answer || "").trim().length > 0;
    const answerMessage = allowAssistantReply
      ? {
          id: crypto.randomUUID(),
          role: "assistant",
          content: payload.answer,
          createdAt: new Date().toISOString(),
          citations: normalizeCitations(payload),
        }
      : null;
    const nextMessages = allowAssistantReply
      ? [...(updated?.messages || messages), answerMessage]
      : [...(updated?.messages || messages)];
    saveTicketMessages(ticketId, nextMessages);
    const nextStatus =
      hasEscalatedAssistance
        ? "escalated"
        : payload?.queued_for_ai
        ? "waiting_for_support"
        : payload?.status === "waiting_for_engineer" || payload?.needs_engineer_input
        ? "escalated"
        : "waiting_for_agent";
    updateTicketStatus(ticketId, nextStatus);
    await syncTicketsFromBackend({ silent: true });
    if (!allowAssistantReply && String(payload?.engineer_mode || "").toLowerCase() === "takeover") {
      toast("This case is in Human Takeover mode. Engineer will reply directly.");
    }
    if (payload.sentiment?.is_alert) {
      toast("Urgent escalation triggered.", "error");
    }
  } catch (error) {
    if (error.name === "AbortError") {
      const updated = getTicketById(ticketId);
      const pendingMessage = updated?.messages?.find(
        (message) => message.id === userMessageId && message.role === "user"
      );
      state.editingMessageId = userMessageId;
      state.inputDraft = pendingMessage?.content || text;
      toast("Generation stopped. Edit your message and resend.");
    } else {
      const updated = getTicketById(ticketId);
      const failMessage = {
        id: crypto.randomUUID(),
        role: "assistant",
        content: `Request failed: ${error.message}`,
        createdAt: new Date().toISOString(),
      };
      saveTicketMessages(ticketId, [...(updated?.messages || messages), failMessage]);
      toast("Failed to fetch assistant response.", "error");
    }
  } finally {
    state.pendingAbortController = null;
    if (keepWaitingForAsync) {
      state.isSending = true;
      ensurePendingStatusPolling();
    } else {
      clearPendingRequestState();
    }
    render();
  }
}

function bindAuthedEvents() {
  appRoot.querySelectorAll("[data-action='new-session']").forEach((element) => {
    element.addEventListener("click", () => {
      openDraftTicket(state.user.id);
    });
  });

  appRoot.querySelectorAll("[data-action='open-ticket']").forEach((element) => {
    element.addEventListener("click", () => {
      const ticketId = element.getAttribute("data-ticket-id");
      if (!ticketId) {
        return;
      }
      openTicketChat(ticketId);
    });
  });

  appRoot.querySelectorAll("[data-history-ticket-row]").forEach((element) => {
    element.addEventListener("click", handleHistoryRowClick);
    element.addEventListener("keydown", handleHistoryRowKeydown);
  });

  appRoot.querySelectorAll("[data-action='resolve-ticket']").forEach((element) => {
    element.addEventListener("click", async () => {
      const ticketId = element.getAttribute("data-ticket-id");
      if (!ticketId) {
        return;
      }
      clearEngineerAssistanceRequest(ticketId);
      updateTicketStatus(ticketId, "resolved");
      render();
      await syncBackendTicketAction(ticketId, "resolved");
      await syncTicketsFromBackend({ silent: true });
      render();
      toast("Session marked as resolved");
    });
  });

  appRoot.querySelectorAll("[data-action='reopen-ticket']").forEach((element) => {
    element.addEventListener("click", async () => {
      const ticketId = element.getAttribute("data-ticket-id");
      if (!ticketId) {
        return;
      }
      clearEngineerAssistanceRequest(ticketId);
      updateTicketStatus(ticketId, "waiting_for_support");
      render();
      await syncBackendTicketAction(ticketId, "processing");
      await syncTicketsFromBackend({ silent: true });
      render();
      toast("Session reopened");
    });
  });

  appRoot.querySelectorAll("[data-action='request-engineer-assistance']").forEach((element) => {
    element.addEventListener("click", () => {
      const ticketId = element.getAttribute("data-ticket-id");
      if (!ticketId) {
        return;
      }
      if (requestEngineerAssistance(ticketId)) {
        render();
      }
    });
  });

  const logoutButton = appRoot.querySelector("[data-action='logout']");
  logoutButton?.addEventListener("click", () => {
    logout();
    navigate("/login");
  });

  appRoot.querySelectorAll("[data-action='go-tickets']").forEach((element) => {
    element.addEventListener("click", () => navigate("/tickets"));
  });

  appRoot.querySelectorAll("[data-action='go-chat']").forEach((element) => {
    element.addEventListener("click", () => navigate("/chat"));
  });
  bindStatusFilter();

  const form = document.getElementById("chat-input-form");
  form?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const message = String(state.inputDraft || "").trim();
    if (!message) {
      return;
    }
    await handleSendMessage(message, {
      editMessageId: state.editingMessageId,
    });
  });

  const input = document.getElementById("chat-input");
  input?.addEventListener("input", () => {
    state.inputDraft = input.value;
  });
  input?.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      form?.requestSubmit();
    }
  });

  const stopButton = appRoot.querySelector("[data-action='stop-generation']");
  stopButton?.addEventListener("click", () => {
    stopGeneration().catch(() => {
      // Stop action errors are already surfaced by toast.
    });
  });
}

function bindStatusFilter() {
  const root = appRoot.querySelector("[data-filter-select]");
  if (!root) {
    return;
  }

  const trigger = root.querySelector("[data-status-filter-trigger]");
  const panel = root.querySelector("[data-status-filter-panel]");
  const hiddenInput = root.querySelector("#status-filter");
  const options = Array.from(root.querySelectorAll("[data-status-filter-option]"));

  if (!trigger || !panel || options.length === 0) {
    return;
  }

  let closeTimer = null;

  const clearCloseTimer = () => {
    if (closeTimer !== null) {
      clearTimeout(closeTimer);
      closeTimer = null;
    }
  };

  const isOpen = () => root.classList.contains("is-open");

  const setActiveDescendant = (option) => {
    if (option?.id) {
      trigger.setAttribute("aria-activedescendant", option.id);
      return;
    }
    trigger.removeAttribute("aria-activedescendant");
  };

  const getSelectedOption = () =>
    options.find((option) => option.getAttribute("data-value") === state.statusFilter) || options[0];

  const openPanel = (focusTarget = "selected") => {
    clearCloseTimer();
    root.classList.add("is-open");
    panel.hidden = false;
    trigger.setAttribute("aria-expanded", "true");

    if (focusTarget === "trigger") {
      setActiveDescendant(getSelectedOption());
      return;
    }

    const target =
      focusTarget === "first"
        ? options[0]
        : focusTarget === "last"
        ? options[options.length - 1]
        : getSelectedOption();

    if (target) {
      setActiveDescendant(target);
      target.focus();
    }
  };

  const closePanel = ({ restoreFocus = false } = {}) => {
    clearCloseTimer();
    root.classList.remove("is-open");
    panel.hidden = true;
    trigger.setAttribute("aria-expanded", "false");
    setActiveDescendant(null);
    if (restoreFocus) {
      trigger.focus();
    }
  };

  const moveFocus = (currentOption, direction) => {
    const currentIndex = options.indexOf(currentOption);
    const nextIndex =
      currentIndex === -1
        ? direction > 0
          ? 0
          : options.length - 1
        : (currentIndex + direction + options.length) % options.length;
    const nextOption = options[nextIndex];
    if (!nextOption) {
      return;
    }
    setActiveDescendant(nextOption);
    nextOption.focus();
  };

  const selectOption = (value) => {
    const nextValue = getStatusFilterOption(value).value;
    hiddenInput.value = nextValue;
    if (nextValue === state.statusFilter) {
      closePanel({ restoreFocus: true });
      return;
    }
    state.statusFilter = nextValue;
    render();
  };

  trigger.addEventListener("click", () => {
    if (isOpen()) {
      closePanel();
      return;
    }
    openPanel("trigger");
  });

  trigger.addEventListener("keydown", (event) => {
    if (event.key === "ArrowDown") {
      event.preventDefault();
      openPanel("selected");
      return;
    }
    if (event.key === "ArrowUp") {
      event.preventDefault();
      openPanel("last");
      return;
    }
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      if (isOpen()) {
        closePanel();
      } else {
        openPanel("selected");
      }
      return;
    }
    if (event.key === "Escape" && isOpen()) {
      event.preventDefault();
      closePanel();
    }
  });

  root.addEventListener("focusin", () => {
    clearCloseTimer();
  });

  root.addEventListener("focusout", (event) => {
    const nextTarget = event.relatedTarget;
    if (nextTarget instanceof Node && root.contains(nextTarget)) {
      return;
    }
    clearCloseTimer();
    closeTimer = setTimeout(() => {
      closePanel();
    }, 120);
  });

  options.forEach((option) => {
    option.addEventListener("focus", () => {
      setActiveDescendant(option);
    });

    option.addEventListener("click", () => {
      selectOption(option.getAttribute("data-value") || "all");
    });

    option.addEventListener("keydown", (event) => {
      if (event.key === "ArrowDown") {
        event.preventDefault();
        moveFocus(option, 1);
        return;
      }
      if (event.key === "ArrowUp") {
        event.preventDefault();
        moveFocus(option, -1);
        return;
      }
      if (event.key === "Home") {
        event.preventDefault();
        moveFocus(options[options.length - 1], 1);
        return;
      }
      if (event.key === "End") {
        event.preventDefault();
        moveFocus(options[0], -1);
        return;
      }
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        selectOption(option.getAttribute("data-value") || "all");
        return;
      }
      if (event.key === "Escape") {
        event.preventDefault();
        closePanel({ restoreFocus: true });
        return;
      }
      if (event.key === "Tab") {
        closePanel();
      }
    });
  });
}

function render() {
  parseRoute();
  if (!state.user) {
    clearPendingRequestState();
    closeClientRealtimeConnection();
    renderLogin();
    return;
  }
  setupClientRealtimeConnection();
  renderAuthed();
  syncChatScrollToBottom();
}

window.addEventListener("hashchange", render);

async function bootstrap() {
  if (state.user) {
    setupClientRealtimeConnection();
    await syncTicketsFromBackend({ silent: true });
  }
  render();
}

bootstrap();
