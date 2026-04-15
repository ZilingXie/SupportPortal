const appRoot = document.getElementById("app");
const toastRoot = document.getElementById("toast-root");

const AUTH_KEY = "helpdesk_auth_user";
const CLIENT_ASSISTANT_NAME = "Sid";
const TICKETS_KEY = "helpdesk_tickets";
const COUNTER_KEY = "helpdesk_ticket_counter";
const MAX_RECENT = 5;
const DEFAULT_CLIENT_ACK_FALLBACK_TIMEOUT_MS = 5000;
const STATUS_FOLLOWUP_MARKERS = [
  "any update",
  "status update",
  "status?",
  "follow up",
  "follow-up",
  "eta",
  "when will",
  "有进展",
  "有更新",
  "跟进",
  "进度",
  "状态",
  "最新情况",
];
const LEGACY_REASSURANCE_MESSAGES = new Set([
  "收到，我先帮你看一下。",
  "收到，我继续帮你跟进。",
  "收到，我来查看。",
  "Got it, let me check this for you.",
  "Got it, I'm checking the latest status.",
  "I got your message and I am checking it now.",
]);
const CJK_RE = /[\u4e00-\u9fff]/;

const DEMO_USERS = [
  {
    id: "user-1",
    username: "Zac",
    name: "Zac",
    email: "zac@example.com",
    password: "Zac",
  },
];
const ENGINEER_ASSISTANCE_WAIT_TEXT = "Estimate waiting time: 3 hours";

const STATUS_CONFIG = {
  open: { label: "Open", className: "status-open", surfaceClass: "status-surface-open" },
  communicating: {
    label: "Communicating",
    className: "status-communicating",
    surfaceClass: "status-surface-communicating",
  },
  investigating: {
    label: "Investigating",
    className: "status-investigating",
    surfaceClass: "status-surface-investigating",
  },
  escalated: {
    label: "Waiting for Engineer",
    className: "status-escalated",
    surfaceClass: "status-surface-escalated",
  },
  resolved: { label: "Resolved", className: "status-resolved", surfaceClass: "status-surface-resolved" },
};

const STATUS_FILTER_OPTIONS = [
  { value: "all", label: "All Statuses" },
  ...Object.entries(STATUS_CONFIG).map(([value, config]) => ({
    value,
    label: config.label,
  })),
];

const PRODUCT_OPTIONS = [
  { value: "audio_video_calling", label: "Audio/Video Calling" },
  { value: "cloud_recording", label: "Cloud Recording" },
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
  pendingClientAck: null,
  stagedClientAck: null,
  pendingAckAbortController: null,
  pendingAckTicketId: null,
  pendingAckFlowId: null,
  pendingAckFallbackTimerId: null,
};
let clientSocket = null;
let clientReconnectTimer = null;
let clientHeartbeatTimer = null;
let pendingStatusPollTimer = null;
const CHAT_NEAR_BOTTOM_THRESHOLD_PX = 96;
let pendingChatScrollRequest = null;
let scheduledChatScrollPlan = null;
let scheduledChatScrollJobId = 0;
let lastRenderedChatMessageSignature = {
  ticketId: "",
  signature: "",
};
const chatUnreadStateByTicket = {};

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

function renderInlineMarkdown(value) {
  const text = String(value ?? "");
  const parts = [];
  const inlineCodePattern = /`([^`\n]+)`/g;
  let lastIndex = 0;
  let match = inlineCodePattern.exec(text);
  while (match) {
    parts.push(escapeHtml(text.slice(lastIndex, match.index)));
    parts.push(`<code>${escapeHtml(match[1])}</code>`);
    lastIndex = match.index + match[0].length;
    match = inlineCodePattern.exec(text);
  }
  parts.push(escapeHtml(text.slice(lastIndex)));
  return parts.join("");
}

function isOrderedListLine(line) {
  return /^\s*\d+\.\s+/.test(String(line || ""));
}

function isUnorderedListLine(line) {
  return /^\s*[-*]\s+/.test(String(line || ""));
}

function renderMarkdownMessage(value) {
  const lines = String(value ?? "").replace(/\r\n?/g, "\n").split("\n");
  const html = [];
  let index = 0;

  while (index < lines.length) {
    const currentLine = lines[index];
    const trimmedLine = currentLine.trim();
    if (!trimmedLine) {
      index += 1;
      continue;
    }

    const fenceMatch = trimmedLine.match(/^```([A-Za-z0-9_+-]*)\s*$/);
    if (fenceMatch) {
      const language = String(fenceMatch[1] || "").trim().toLowerCase();
      const codeLines = [];
      index += 1;
      while (index < lines.length && !lines[index].trim().match(/^```/)) {
        codeLines.push(lines[index]);
        index += 1;
      }
      if (index < lines.length) {
        index += 1;
      }
      const languageAttr = language ? ` class="language-${escapeHtml(language)}"` : "";
      html.push(`<pre><code${languageAttr}>${escapeHtml(codeLines.join("\n"))}</code></pre>`);
      continue;
    }

    if (isOrderedListLine(trimmedLine)) {
      const items = [];
      while (index < lines.length && isOrderedListLine(lines[index])) {
        items.push(lines[index].replace(/^\s*\d+\.\s+/, ""));
        index += 1;
      }
      html.push(
        `<ol>${items
          .map((item) => `<li>${renderInlineMarkdown(item.trim())}</li>`)
          .join("")}</ol>`
      );
      continue;
    }

    if (isUnorderedListLine(trimmedLine)) {
      const items = [];
      while (index < lines.length && isUnorderedListLine(lines[index])) {
        items.push(lines[index].replace(/^\s*[-*]\s+/, ""));
        index += 1;
      }
      html.push(
        `<ul>${items
          .map((item) => `<li>${renderInlineMarkdown(item.trim())}</li>`)
          .join("")}</ul>`
      );
      continue;
    }

    const paragraphLines = [];
    while (index < lines.length) {
      const line = lines[index];
      const trimmed = line.trim();
      if (!trimmed || trimmed.match(/^```/) || isOrderedListLine(trimmed) || isUnorderedListLine(trimmed)) {
        break;
      }
      paragraphLines.push(trimmed);
      index += 1;
    }
    html.push(`<p>${paragraphLines.map((line) => renderInlineMarkdown(line)).join("<br>")}</p>`);
  }

  return html.join("");
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
        return `<li class="citation-item"><a class="citation-link" href="${escapeHtml(citation.sourceUrl)}" target="_blank" rel="noopener noreferrer">${heading}</a></li>`;
      }
      return `<li class="citation-item"><span class="citation-link is-static">${heading}</span></li>`;
    })
    .join("");
  return `
    <div class="citations">
      <div class="citation-title">References</div>
      <ul class="citation-list">${items}</ul>
    </div>
  `;
}

function renderMessageBody(message) {
  const normalizedCitations = normalizeCitations({
    citations: Array.isArray(message?.citations) ? message.citations : [],
    sources: Array.isArray(message?.sources) ? message.sources : [],
  });
  const base =
    message.role === "assistant"
      ? `<div class="message-markdown">${renderMarkdownMessage(message.content || "")}</div>`
      : `<div>${formatMultilineText(message.content || "")}</div>`;
  if (message.role !== "assistant") {
    return base;
  }
  return `${base}${renderCitationsHtml(normalizedCitations)}`;
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
    if (!raw) {
      return null;
    }
    const parsed = JSON.parse(raw);
    const matchedUser = DEMO_USERS.find(
      (user) =>
        user.id === parsed?.id &&
        user.name === parsed?.name &&
        user.email === parsed?.email
    );
    if (!matchedUser) {
      localStorage.removeItem(AUTH_KEY);
      return null;
    }
    return { id: matchedUser.id, name: matchedUser.name, email: matchedUser.email };
  } catch {
    localStorage.removeItem(AUTH_KEY);
    return null;
  }
}

function login(username, password) {
  const normalizedUsername = String(username || "").trim().toLowerCase();
  const normalizedPassword = String(password || "").trim();
  const match = DEMO_USERS.find(
    (user) =>
      user.username.toLowerCase() === normalizedUsername && user.password === normalizedPassword
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
  resetChatScrollState();
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

function createAbortController() {
  if (typeof AbortController === "function") {
    return new AbortController();
  }
  return {
    signal: undefined,
    abort() {},
  };
}

function containsCjk(text) {
  return CJK_RE.test(String(text || ""));
}

function isStatusFollowup(message) {
  const normalized = String(message || "")
    .replace(/\s+/g, " ")
    .trim()
    .toLowerCase();
  if (!normalized) {
    return false;
  }
  return STATUS_FOLLOWUP_MARKERS.some((marker) => normalized.includes(marker));
}

function buildStaticClientAck(message) {
  if (containsCjk(message)) {
    return isStatusFollowup(message) ? "收到，我继续帮你跟进。" : "收到，我先帮你看一下。";
  }
  return isStatusFollowup(message)
    ? "Got it, I'm checking the latest status."
    : "Got it, let me check this for you.";
}

function buildNewSessionWelcomeText(userName) {
  const greetingName = String(userName || "").trim() || "there";
  return `Hi ${greetingName}, I'm ${CLIENT_ASSISTANT_NAME}, Agora's intelligent support assistant. I'm here to help you with Agora-related technical issues. Before we begin, please select the product you need support with.`;
}

function renderNewSessionWelcomeBubble() {
  const welcomeText = buildNewSessionWelcomeText(state.user?.name);
  return `
    <div class="empty-chat-welcome">
      <article class="msg-row">
        <div class="msg-column">
          <div class="message-meta">
            <span class="avatar assistant"><span class="material-symbols-outlined" aria-hidden="true">auto_awesome</span></span>
            <span class="message-author">${CLIENT_ASSISTANT_NAME}</span>
          </div>
          <div class="bubble assistant"><div>${escapeHtml(welcomeText)}</div></div>
        </div>
      </article>
    </div>
  `;
}

function getTransientClientAck(ticketId) {
  const pending = state.pendingClientAck;
  if (!pending) {
    return null;
  }
  return String(pending.ticketId || "").trim() === String(ticketId || "").trim() ? pending : null;
}

function setTransientClientAck(ticketId, content, options = {}) {
  const normalizedTicketId = String(ticketId || "").trim();
  const normalizedContent = String(content || "").trim();
  if (!normalizedTicketId || !normalizedContent) {
    return;
  }
  const existing = getTransientClientAck(normalizedTicketId);
  state.pendingClientAck = {
    id: existing?.id || `client-ack-${crypto.randomUUID()}`,
    ticketId: normalizedTicketId,
    role: "assistant",
    content: normalizedContent,
    createdAt: existing?.createdAt || new Date().toISOString(),
    citations: [],
    transient: true,
    source: options.source || existing?.source || "fallback",
  };
}

function clearTransientClientAck(ticketId = null) {
  if (!state.pendingClientAck) {
    return;
  }
  if (ticketId && String(state.pendingClientAck.ticketId || "").trim() !== String(ticketId || "").trim()) {
    return;
  }
  state.pendingClientAck = null;
}

function getStagedClientAck(ticketId) {
  const pending = state.stagedClientAck;
  if (!pending) {
    return null;
  }
  return String(pending.ticketId || "").trim() === String(ticketId || "").trim() ? pending : null;
}

function setStagedClientAck(ticketId, content, options = {}) {
  const normalizedTicketId = String(ticketId || "").trim();
  const normalizedContent = String(content || "").trim();
  if (!normalizedTicketId || !normalizedContent) {
    return;
  }
  const existing = getStagedClientAck(normalizedTicketId);
  state.stagedClientAck = {
    id: existing?.id || `staged-client-ack-${crypto.randomUUID()}`,
    ticketId: normalizedTicketId,
    content: normalizedContent,
    source: options.source || existing?.source || "client_model",
  };
}

function clearStagedClientAck(ticketId = null) {
  if (!state.stagedClientAck) {
    return;
  }
  if (ticketId && String(state.stagedClientAck.ticketId || "").trim() !== String(ticketId || "").trim()) {
    return;
  }
  state.stagedClientAck = null;
}

function clearPendingAckFallbackTimer() {
  if (state.pendingAckFallbackTimerId) {
    clearTimeout(state.pendingAckFallbackTimerId);
    state.pendingAckFallbackTimerId = null;
  }
}

function clearPendingAckState(options = {}) {
  const normalizedTicketId = String(options?.ticketId || "").trim();
  const pendingTicketId = String(state.pendingAckTicketId || "").trim();
  const ticketMatches = !normalizedTicketId || !pendingTicketId || pendingTicketId === normalizedTicketId;

  clearStagedClientAck(normalizedTicketId || null);
  if (options.clearVisible !== false) {
    clearTransientClientAck(normalizedTicketId || null);
  }
  if (!ticketMatches) {
    return;
  }

  clearPendingAckFallbackTimer();
  if (
    options.abortTransport !== false &&
    state.pendingAckAbortController &&
    typeof state.pendingAckAbortController.abort === "function"
  ) {
    state.pendingAckAbortController.abort();
  }
  state.pendingAckAbortController = null;
  state.pendingAckTicketId = null;
  state.pendingAckFlowId = null;
}

function isLegacyReassuranceMessage(message) {
  return (
    String(message?.role || "").toLowerCase() === "assistant" &&
    LEGACY_REASSURANCE_MESSAGES.has(String(message?.content || "").trim())
  );
}

function shouldHideLegacyReassuranceMessage(messages, index) {
  const message = messages[index];
  if (!isLegacyReassuranceMessage(message)) {
    return false;
  }
  for (let nextIndex = index + 1; nextIndex < messages.length; nextIndex += 1) {
    const nextRole = String(messages[nextIndex]?.role || "").toLowerCase();
    if (nextRole === "user" || nextRole === "customer") {
      return false;
    }
    if (nextRole) {
      return true;
    }
  }
  return false;
}

function getRenderableMessages(ticket) {
  const durable = Array.isArray(ticket?.messages)
    ? ticket.messages.filter((message, index, messages) => !shouldHideLegacyReassuranceMessage(messages, index))
    : [];
  const transientAck = getTransientClientAck(ticket?.id);
  if (transientAck && !ticketHasAssistantReply(ticket)) {
    durable.push(transientAck);
  }
  return durable;
}

function stopPendingStatusPolling() {
  if (!pendingStatusPollTimer) {
    return;
  }
  clearInterval(pendingStatusPollTimer);
  pendingStatusPollTimer = null;
}

function hasDurableAssistantReplyAfterMessage(ticket, messageId = null) {
  const messages = Array.isArray(ticket?.messages) ? ticket.messages : [];
  if (messages.length === 0) {
    return false;
  }
  const hasReplyAfterIndex = (index) =>
    index >= 0 &&
    messages
      .slice(index + 1)
      .some((message) => String(message?.role || "").toLowerCase() !== "user");
  const normalizedMessageId = String(messageId || "").trim();
  if (normalizedMessageId) {
    const index = messages.findIndex(
      (message) => String(message?.id || "").trim() === normalizedMessageId
    );
    if (index >= 0) {
      return hasReplyAfterIndex(index);
    }
  }
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    if (String(messages[index]?.role || "").toLowerCase() === "user") {
      return hasReplyAfterIndex(index);
    }
  }
  return false;
}

function getPendingReplyAnchorIndex(ticket) {
  const messages = Array.isArray(ticket?.messages) ? ticket.messages : [];
  const pendingUserId = String(state.pendingUserMessageId || "").trim();
  if (pendingUserId) {
    const index = messages.findIndex((message) => String(message?.id || "").trim() === pendingUserId);
    if (index >= 0) {
      return index;
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
      return index;
    }
  }

  for (let index = messages.length - 1; index >= 0; index -= 1) {
    if (String(messages[index]?.role || "").toLowerCase() === "user") {
      return index;
    }
  }
  return -1;
}

function clearPendingRequestState() {
  state.isSending = false;
  state.pendingAbortController = null;
  state.pendingTicketId = null;
  state.pendingUserMessageId = null;
  state.pendingAsyncTicketId = null;
  state.pendingAsyncMessageCreatedAt = null;
  clearPendingAckState();
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
  const anchorIndex = getPendingReplyAnchorIndex(ticket);
  if (anchorIndex < 0) {
    return false;
  }
  return messages
    .slice(anchorIndex + 1)
    .some((message) => String(message?.role || "").toLowerCase() !== "user");
}

function isTicketAwaitingDurableReply(ticket) {
  return isTicketSending(ticket?.id) && !ticketHasAssistantReply(ticket);
}

function reconcilePendingAsyncStateAfterSync(options = {}) {
  const currentPendingTicketId = String(state.pendingAsyncTicketId || "").trim();
  const normalizedTicketId = String(options?.ticketId || currentPendingTicketId || "").trim();
  if (!normalizedTicketId) {
    return false;
  }
  if (currentPendingTicketId && currentPendingTicketId !== normalizedTicketId) {
    return false;
  }

  const eventName = String(options?.eventName || "").trim().toLowerCase();
  if (eventName === "ticket_ai_generation_stopped") {
    clearPendingRequestState();
    return true;
  }

  const pendingTicket = getTicketById(normalizedTicketId);
  if (!pendingTicket || ticketHasAssistantReply(pendingTicket)) {
    clearPendingRequestState();
    return true;
  }
  return false;
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
        reconcilePendingAsyncStateAfterSync({ ticketId: state.pendingAsyncTicketId });
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
      await syncTicketsFromBackend({ silent: true });
      reconcilePendingAsyncStateAfterSync({ ticketId: eventTicketId, eventName });
      if (state.user) {
        render();
      }
      return;
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

function normalizeTicketProduct(value) {
  const normalized = String(value || "").trim().toLowerCase();
  return PRODUCT_OPTIONS.some((option) => option.value === normalized) ? normalized : null;
}

function getProductOption(value) {
  const normalized = normalizeTicketProduct(value);
  return PRODUCT_OPTIONS.find((option) => option.value === normalized) || null;
}

function getProductLabel(value) {
  return getProductOption(value)?.label || "";
}

function pickPreferredTicket(current, candidate) {
  const currentUpdated = toTimestamp(current?.updatedAt || current?.createdAt);
  const candidateUpdated = toTimestamp(candidate?.updatedAt || candidate?.createdAt);
  const currentProduct = normalizeTicketProduct(current?.product);
  const candidateProduct = normalizeTicketProduct(candidate?.product);

  const mergeProduct = (preferred, fallback) => {
    if (normalizeTicketProduct(preferred?.product) || !normalizeTicketProduct(fallback?.product)) {
      return preferred;
    }
    return {
      ...preferred,
      product: normalizeTicketProduct(fallback.product),
    };
  };

  if (candidateUpdated !== currentUpdated) {
    return candidateUpdated > currentUpdated
      ? mergeProduct(candidate, current)
      : mergeProduct(current, candidate);
  }

  const currentMessageCount = Array.isArray(current?.messages) ? current.messages.length : 0;
  const candidateMessageCount = Array.isArray(candidate?.messages) ? candidate.messages.length : 0;
  if (candidateMessageCount !== currentMessageCount) {
    return candidateMessageCount > currentMessageCount
      ? mergeProduct(candidate, current)
      : mergeProduct(current, candidate);
  }

  const currentTitle = String(current?.title || "").trim();
  const candidateTitle = String(candidate?.title || "").trim();
  if (
    currentTitle === "New Session" &&
    candidateTitle.length > 0 &&
    candidateTitle !== "New Session"
  ) {
    return mergeProduct(candidate, current);
  }

  if (!currentProduct && candidateProduct) {
    return mergeProduct(candidate, current);
  }

  return mergeProduct(current, candidate);
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
  if (status === "open") {
    const messages = Array.isArray(ticket?.messages) ? ticket.messages : [];
    return messages.length > 0 ? "communicating" : "open";
  }
  if (status === "communicating") {
    return "communicating";
  }
  if (status === "resolved") {
    return "resolved";
  }
  if (status === "escalated") {
    return "escalated";
  }
  if (status === "investigating" || status === "waiting_for_engineer") {
    return "investigating";
  }
  return "open";
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
    product: normalizeTicketProduct(ticket?.product),
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

function getPendingLocalUserMessageForSync(localTicket) {
  const pendingTicketId = String(state.pendingAsyncTicketId || state.pendingTicketId || "").trim();
  const localTicketId = String(localTicket?.id || "").trim();
  const pendingUserId = String(state.pendingUserMessageId || "").trim();
  if (!pendingTicketId || !localTicketId || pendingTicketId !== localTicketId || !pendingUserId) {
    return null;
  }
  return (
    (Array.isArray(localTicket?.messages) ? localTicket.messages : []).find(
      (message) =>
        String(message?.id || "").trim() === pendingUserId &&
        String(message?.role || "").toLowerCase() === "user"
    ) || null
  );
}

function remoteTicketHasPersistedPendingCustomerTurn(remoteTicket) {
  const pendingCreatedAt = String(state.pendingAsyncMessageCreatedAt || "").trim();
  if (!pendingCreatedAt) {
    return false;
  }
  return (Array.isArray(remoteTicket?.messages) ? remoteTicket.messages : []).some(
    (message) =>
      String(message?.role || "").toLowerCase() === "user" &&
      String(message?.createdAt || "").trim() === pendingCreatedAt
  );
}

function mergePendingLocalUserMessageIntoRemoteTicket(remoteTicket, localTicket) {
  const pendingLocalMessage = getPendingLocalUserMessageForSync(localTicket);
  if (!pendingLocalMessage || remoteTicketHasPersistedPendingCustomerTurn(remoteTicket)) {
    return remoteTicket;
  }
  const remoteMessages = Array.isArray(remoteTicket?.messages) ? remoteTicket.messages : [];
  if (
    remoteMessages.some(
      (message) => String(message?.id || "").trim() === String(pendingLocalMessage?.id || "").trim()
    )
  ) {
    return remoteTicket;
  }
  return {
    ...remoteTicket,
    messages: [...remoteMessages, pendingLocalMessage],
  };
}

async function syncTicketsFromBackend(options = {}) {
  const { silent = false } = options;
  if (!state.user?.id) {
    return;
  }
  try {
    const response = await fetch(
      `/api/tickets?customer_id=${encodeURIComponent(state.user.id)}&status=all`
    );
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }
    const payload = await response.json();
    const incoming = Array.isArray(payload?.tickets) ? payload.tickets : [];
    const mapped = incoming
      .map(normalizeBackendTicket)
      .filter(Boolean);

    const allLocal = getAllTickets();
    const localTicketsById = new Map(
      allLocal
        .filter((ticket) => String(ticket?.userId || "").trim() === String(state.user.id || "").trim())
        .map((ticket) => [String(ticket.id || "").trim(), ticket])
    );
    const mergedMapped = mapped.map((remoteTicket) =>
      mergePendingLocalUserMessageIntoRemoteTicket(
        remoteTicket,
        localTicketsById.get(String(remoteTicket?.id || "").trim()) || null
      )
    );
    const otherUsersLocal = allLocal.filter((ticket) => ticket.userId !== state.user.id);
    const preservedDrafts = allLocal.filter(
      (ticket) =>
        isReusableDraftTicket(ticket, state.user.id) &&
        !mergedMapped.some((remoteTicket) => remoteTicket.id === ticket.id)
    );
    saveAllTickets([...otherUsersLocal, ...mergedMapped, ...preservedDrafts]);
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
    status: "open",
    createdAt: now,
    updatedAt: now,
    userId,
    product: null,
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

function updateTicketProduct(ticketId, product) {
  const all = getAllTickets();
  const idx = all.findIndex((ticket) => ticket.id === ticketId);
  if (idx < 0) {
    return false;
  }
  if (!isTicketEmpty(all[idx])) {
    return false;
  }
  all[idx].product = normalizeTicketProduct(product);
  all[idx].updatedAt = new Date().toISOString();
  saveAllTickets(all);
  return true;
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
      !["resolved", "escalated", "investigating"].includes(
        String(ticket.status || "").trim().toLowerCase()
      ) &&
      !isTicketEmpty(ticket)
  );
}

async function requestEngineerAssistance(ticketId) {
  const normalizedId = String(ticketId || "").trim();
  const ticket = getTicketById(normalizedId);
  if (!normalizedId || !canRequestEngineerAssistance(ticket)) {
    return false;
  }
  try {
    const response = await fetch(
      `/api/tickets/${encodeURIComponent(normalizedId)}/request-engineer-assistance`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({}),
      }
    );
    if (!response.ok) {
      throw new Error(`Request failed with status ${response.status}`);
    }
    const payload = await response.json();
    updateTicketStatus(
      normalizedId,
      mapBackendStatusToClientStatus({ status: payload?.status, messages: ticket.messages })
    );
    return true;
  } catch (error) {
    toast(`Failed to request engineer assistance: ${error.message}`, "error");
    return false;
  }
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
  const config = STATUS_CONFIG[status] || STATUS_CONFIG.open;
  return `<span class="status-badge ${config.className}">${config.label}</span>`;
}

function historySurfaceClass(status) {
  const config = STATUS_CONFIG[String(status || "").trim().toLowerCase()] || STATUS_CONFIG.open;
  return config.surfaceClass;
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
  const productLabel = getProductLabel(ticket?.product);
  return `
    <div class="history-row-meta">
      <span><strong>Created</strong> ${escapeHtml(formatDate(ticket.createdAt))}</span>
      <span><strong>Updated</strong> ${escapeHtml(formatDate(ticket.updatedAt))}</span>
      ${productLabel ? `<span><strong>Product</strong> ${escapeHtml(productLabel)}</span>` : ""}
    </div>
  `;
}

function renderHistoryRow(ticket, options = {}) {
  const { compact = false, active = false, includeActions = false } = options;
  const classes = ["history-row"];
  if (compact) {
    classes.push("history-row-compact");
  } else {
    classes.push(historySurfaceClass(ticket.status));
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

function renderTicketProductSelector(ticket) {
  const selectedOption = getProductOption(ticket?.product);
  const triggerLabel = selectedOption?.label || "Select Product";
  return `
    <div class="filter-select product-select" data-product-select data-ticket-id="${escapeHtml(ticket.id)}">
      <input id="ticket-product-${escapeHtml(ticket.id)}" type="hidden" value="${escapeHtml(selectedOption?.value || "")}" />
      <button
        class="filter-select-trigger"
        data-product-select-trigger
        type="button"
        role="combobox"
        aria-label="Select Product"
        aria-controls="ticket-product-listbox"
        aria-expanded="false"
        aria-haspopup="listbox"
      >
        <span class="filter-select-trigger-label">${escapeHtml(triggerLabel)}</span>
        <span class="filter-select-trigger-icon" aria-hidden="true">
          <span class="material-symbols-outlined">expand_more</span>
        </span>
      </button>
      <div class="filter-select-panel" data-product-select-panel hidden>
        <div class="filter-select-options" id="ticket-product-listbox" role="listbox" aria-label="Product selector">
          ${PRODUCT_OPTIONS.map((option, index) => {
            const isSelected = option.value === selectedOption?.value;
            return `
              <button
                class="filter-select-option ${isSelected ? "is-selected" : ""}"
                data-product-select-option
                data-value="${escapeHtml(option.value)}"
                id="ticket-product-option-${index}"
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
              <h1>${CLIENT_ASSISTANT_NAME}</h1>
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
            <p class="panel-desc">Enter your credentials to access ${CLIENT_ASSISTANT_NAME}.</p>
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
                <input class="input" id="username" name="username" type="text" placeholder="Zac" required />
              </div>
              <div class="field">
                <label for="password">Password</label>
                <input class="input" id="password" name="password" type="password" placeholder="Zac" required />
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
        <div>Username: Zac / Password: Zac</div>
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
              <span class="line-1">${CLIENT_ASSISTANT_NAME}</span>
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
        <h2>${CLIENT_ASSISTANT_NAME}</h2>
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
      const productLabel = getProductLabel(ticket.product);
      const assistanceRequested = String(ticket.status || "").trim().toLowerCase() === "escalated";
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
              ${productLabel ? `<span class="context-product-pill">${escapeHtml(productLabel)}</span>` : ""}
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
        <p class="welcome-kicker">${CLIENT_ASSISTANT_NAME}</p>
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

function isTextComposerElement(element) {
  return Boolean(
    element &&
      typeof element === "object" &&
      typeof element.focus === "function" &&
      typeof element.value === "string"
  );
}

function captureComposerPreservationState(element) {
  if (!isTextComposerElement(element) || document.activeElement !== element || element.disabled) {
    return null;
  }
  return {
    selectionStart:
      typeof element.selectionStart === "number" ? element.selectionStart : element.value.length,
    selectionEnd:
      typeof element.selectionEnd === "number" ? element.selectionEnd : element.value.length,
    selectionDirection:
      typeof element.selectionDirection === "string" ? element.selectionDirection : "none",
    scrollTop: typeof element.scrollTop === "number" ? element.scrollTop : 0,
  };
}

function restoreComposerPreservationState(element, snapshot) {
  if (!isTextComposerElement(element) || !snapshot || element.disabled) {
    return;
  }
  try {
    element.focus({ preventScroll: true });
  } catch {
    element.focus();
  }
  if (typeof element.setSelectionRange === "function") {
    element.setSelectionRange(
      snapshot.selectionStart,
      snapshot.selectionEnd,
      snapshot.selectionDirection || "none"
    );
  }
  if (typeof snapshot.scrollTop === "number" && typeof element.scrollTop === "number") {
    element.scrollTop = snapshot.scrollTop;
  }
}

function getActiveChatComposerElement() {
  const input = document.getElementById("chat-input");
  return isTextComposerElement(input) ? input : null;
}

function buildChatTicketViewState(ticket) {
  if (!ticket || ticket.userId !== state.user.id) {
    return null;
  }
  const renderableMessages = getRenderableMessages(ticket);
  const sending = isTicketAwaitingDurableReply(ticket);
  const requiresProductSelection = isTicketEmpty(ticket) && !normalizeTicketProduct(ticket.product);
  const canCompose = !sending && ticket.status !== "resolved" && !requiresProductSelection;
  const isEditing = Boolean(state.editingMessageId);

  if (isEditing && !renderableMessages.some((message) => message.id === state.editingMessageId)) {
    state.editingMessageId = null;
    if (!sending) {
      state.inputDraft = "";
    }
  }

  return {
    ticket,
    renderableMessages,
    sending,
    requiresProductSelection,
    canCompose,
    isEditing: Boolean(state.editingMessageId),
  };
}

function renderChatMessagesHtml(viewState) {
  return `
    ${
      viewState.renderableMessages.length === 0
        ? `
          <div class="empty-chat">
            ${renderNewSessionWelcomeBubble()}
            ${renderTicketProductSelector(viewState.ticket)}
          </div>
        `
        : viewState.renderableMessages
            .map((message) => {
              const role = String(message.role || "assistant");
              const tone = role === "user" ? "user" : role === "engineer" ? "engineer" : "assistant";
              const author =
                role === "user"
                  ? state.user.name
                  : role === "engineer"
                  ? "Engineer"
                  : CLIENT_ASSISTANT_NAME;
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
            })
            .join("")
    }
    ${
      viewState.sending
        ? `
      <div class="thinking-line">
        <span class="thinking-dots"><span></span><span></span><span></span></span>
        <span class="thinking-label">AI is cross-referencing system health logs...</span>
      </div>
    `
        : ""
    }
  `;
}

function renderChatComposerNoteHtml(viewState) {
  if (viewState.sending) {
    return `<div class="composer-note">checking the knowledge base... click stop to interrupt.</div>`;
  }
  if (viewState.isEditing) {
    return `<div class="composer-note">Editing your last message. Press Enter to resend, Shift+Enter for newline.</div>`;
  }
  if (viewState.requiresProductSelection) {
    return `<div class="composer-note">Select Product before sending the first message in this session.</div>`;
  }
  return "";
}

function renderChatComposerActionHtml(viewState) {
  if (viewState.sending) {
    return `
      <button
        class="composer-icon-button composer-stop-btn"
        type="button"
        data-action="stop-generation"
        aria-label="Stop Generation"
        title="Stop Generation"
      >
        <span class="material-symbols-outlined" aria-hidden="true">stop</span>
      </button>
    `;
  }

  return `
    <button
      class="composer-icon-button send-btn"
      type="submit"
      aria-label="${viewState.isEditing ? "Resend Request" : "Send Request"}"
      title="${viewState.isEditing ? "Resend Request" : "Send Request"}"
      ${viewState.canCompose ? "" : "disabled"}
    >
      <span class="material-symbols-outlined" aria-hidden="true">arrow_upward</span>
    </button>
  `;
}

function renderChatUnreadIndicatorHtml(ticketId) {
  if (!getChatUnreadState(ticketId)) {
    return "";
  }
  return `
    <div class="chat-new-messages">
      <button class="new-messages-btn" type="button" data-action="jump-chat-latest">
        New messages
      </button>
    </div>
  `;
}

function renderChatTicketFromState(viewState) {
  return `
    <section class="chat-root" data-chat-ticket-id="${escapeHtml(viewState.ticket.id)}">
      <main class="chat-main">
        <div class="message-list" data-chat-section="messages">
          ${renderChatMessagesHtml(viewState)}
        </div>
      </main>
      ${renderChatUnreadIndicatorHtml(viewState.ticket.id)}
      <footer class="chat-input-wrap">
        <div data-chat-section="composer-note">${renderChatComposerNoteHtml(viewState)}</div>
        <form id="chat-input-form" class="chat-input-inner" data-chat-section="composer-form">
          <textarea
            id="chat-input"
            class="textarea"
            rows="1"
            placeholder="Type your request or technical issue..."
            ${viewState.canCompose ? "" : "disabled"}
          >${escapeHtml(state.inputDraft || "")}</textarea>
          <div data-chat-section="composer-action">
            ${renderChatComposerActionHtml(viewState)}
          </div>
        </form>
      </footer>
    </section>
  `;
}

function shouldPreserveActiveChatComposerOnRender(viewState) {
  if (!viewState?.canCompose) {
    return false;
  }
  return Boolean(captureComposerPreservationState(getActiveChatComposerElement()));
}

function patchChatTicketWhilePreservingComposer(mainRegion, viewState) {
  if (!mainRegion || typeof mainRegion.querySelector !== "function") {
    return false;
  }
  const chatRoot = mainRegion.querySelector(".chat-root");
  if (!chatRoot || typeof chatRoot.querySelector !== "function") {
    return false;
  }
  const chatTicketId = String(chatRoot.dataset?.chatTicketId || "").trim();
  if (chatTicketId && chatTicketId !== String(viewState.ticket.id || "").trim()) {
    return false;
  }
  const messagesRegion = chatRoot.querySelector('[data-chat-section="messages"]');
  const noteRegion = chatRoot.querySelector('[data-chat-section="composer-note"]');
  const actionRegion = chatRoot.querySelector('[data-chat-section="composer-action"]');
  if (!messagesRegion || !noteRegion || !actionRegion) {
    return false;
  }

  const composer = getActiveChatComposerElement();
  const snapshot = captureComposerPreservationState(composer);
  messagesRegion.innerHTML = renderChatMessagesHtml(viewState);
  noteRegion.innerHTML = renderChatComposerNoteHtml(viewState);
  actionRegion.innerHTML = renderChatComposerActionHtml(viewState);
  if (composer) {
    composer.disabled = !viewState.canCompose;
    composer.placeholder = "Type your request or technical issue...";
  }
  restoreComposerPreservationState(composer, snapshot);
  return true;
}

function renderChatTicket() {
  const ticket = getTicketById(state.activeTicketId);
  const viewState = buildChatTicketViewState(ticket);
  if (!viewState) {
    return `<div class="empty-state">Session not found.</div>`;
  }
  return renderChatTicketFromState(viewState);
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

function getActiveChatTicket() {
  if (state.view !== "chat-ticket") {
    return null;
  }
  const ticket = getTicketById(state.activeTicketId);
  if (!ticket || ticket.userId !== state.user?.id) {
    return null;
  }
  return ticket;
}

function prefersReducedMotion() {
  try {
    return Boolean(window.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches);
  } catch {
    return false;
  }
}

function scrollElementToTop(element, top, behavior = "auto") {
  if (!element || typeof top !== "number") {
    return;
  }
  const resolvedBehavior = behavior === "smooth" && !prefersReducedMotion() ? "smooth" : "auto";
  if (typeof element.scrollTo === "function") {
    if (resolvedBehavior === "smooth") {
      element.scrollTo({ top, behavior: "smooth" });
    } else {
      element.scrollTo({ top });
    }
    if (typeof element.scrollTop === "number" && resolvedBehavior !== "smooth") {
      element.scrollTop = top;
    }
    return;
  }
  if (typeof element.scrollTop === "number") {
    element.scrollTop = top;
  }
}

function distanceFromBottom(element) {
  if (!element || typeof element.scrollHeight !== "number") {
    return Number.POSITIVE_INFINITY;
  }
  const scrollTop = typeof element.scrollTop === "number" ? element.scrollTop : 0;
  const clientHeight = typeof element.clientHeight === "number" ? element.clientHeight : 0;
  return element.scrollHeight - (scrollTop + clientHeight);
}

function isElementNearBottom(element, threshold = CHAT_NEAR_BOTTOM_THRESHOLD_PX) {
  return distanceFromBottom(element) <= threshold;
}

function normalizeRenderableMessageRole(message) {
  const normalized = String(message?.role || "").trim().toLowerCase();
  if (normalized === "customer") {
    return "user";
  }
  return normalized || "assistant";
}

function getChatMessageSignatureToken(message) {
  const role = normalizeRenderableMessageRole(message);
  const id = String(message?.id || "").trim();
  const createdAt = String(message?.createdAt || message?.created_at || "").trim();
  const content = String(message?.content || "").trim().slice(0, 160);
  const citationCount = Array.isArray(message?.citations) ? message.citations.length : 0;
  return `${role}:${id || createdAt || content}:${citationCount}`;
}

function getRenderablePendingReplyAnchorIndex(ticket, renderableMessages) {
  const messages = Array.isArray(renderableMessages) ? renderableMessages : [];
  const pendingUserId = String(state.pendingUserMessageId || "").trim();
  if (pendingUserId) {
    const index = messages.findIndex(
      (message) =>
        normalizeRenderableMessageRole(message) === "user" &&
        String(message?.id || "").trim() === pendingUserId
    );
    if (index >= 0) {
      return index;
    }
  }

  const pendingCreatedAt = String(state.pendingAsyncMessageCreatedAt || "").trim();
  if (pendingCreatedAt) {
    const index = messages.findIndex(
      (message) =>
        normalizeRenderableMessageRole(message) === "user" &&
        String(message?.createdAt || message?.created_at || "").trim() === pendingCreatedAt
    );
    if (index >= 0) {
      return index;
    }
  }

  for (let index = messages.length - 1; index >= 0; index -= 1) {
    if (normalizeRenderableMessageRole(messages[index]) === "user") {
      return index;
    }
  }
  return -1;
}

function buildRenderableChatMessageSignature(ticket, renderableMessages = getRenderableMessages(ticket)) {
  const messages = Array.isArray(renderableMessages) ? renderableMessages : [];
  if (messages.length === 0) {
    return "";
  }
  const anchorIndex = getRenderablePendingReplyAnchorIndex(ticket, messages);
  if (anchorIndex < 0 || anchorIndex >= messages.length - 1) {
    return messages.map((message) => getChatMessageSignatureToken(message)).join("|");
  }
  const leadingTokens = messages
    .slice(0, anchorIndex + 1)
    .map((message) => getChatMessageSignatureToken(message));
  const trailingRoles = messages
    .slice(anchorIndex + 1)
    .map((message) => normalizeRenderableMessageRole(message))
    .join(",");
  leadingTokens.push(`reply-slot:${messages.length - anchorIndex - 1}:${trailingRoles}`);
  return leadingTokens.join("|");
}

function getChatUnreadState(ticketId) {
  const normalizedTicketId = String(ticketId || "").trim();
  return normalizedTicketId ? Boolean(chatUnreadStateByTicket[normalizedTicketId]) : false;
}

function setChatUnreadState(ticketId, visible) {
  const normalizedTicketId = String(ticketId || "").trim();
  if (!normalizedTicketId) {
    return;
  }
  if (visible) {
    chatUnreadStateByTicket[normalizedTicketId] = true;
    return;
  }
  delete chatUnreadStateByTicket[normalizedTicketId];
}

function clearRequestedChatScroll() {
  pendingChatScrollRequest = null;
}

function resetChatScrollState() {
  clearRequestedChatScroll();
  scheduledChatScrollPlan = null;
  scheduledChatScrollJobId += 1;
}

function requestChatScrollToBottom(ticketId, options = {}) {
  const normalizedId = String(ticketId || "").trim();
  if (!normalizedId) {
    return;
  }
  pendingChatScrollRequest = {
    ticketId: normalizedId,
    behavior: String(options?.behavior || "").trim().toLowerCase() === "smooth" ? "smooth" : "auto",
  };
  setChatUnreadState(normalizedId, false);
}

function captureChatScrollSnapshot() {
  const ticket = getActiveChatTicket();
  const ticketId = String(ticket?.id || "").trim();
  if (!ticketId) {
    return null;
  }
  const chatMain = appRoot.querySelector(".chat-main");
  if (scheduledChatScrollPlan?.ticketId === ticketId) {
    if (scheduledChatScrollPlan.type === "bottom") {
      return {
        ticketId,
        scrollTop: chatMain && typeof chatMain.scrollHeight === "number" ? chatMain.scrollHeight : null,
        nearBottom: true,
        preserveBottom: true,
        behavior: scheduledChatScrollPlan.behavior || "auto",
      };
    }
    return {
      ticketId,
      scrollTop:
        typeof scheduledChatScrollPlan.scrollTop === "number"
          ? scheduledChatScrollPlan.scrollTop
          : null,
      nearBottom: false,
    };
  }
  return {
    ticketId,
    scrollTop: chatMain && typeof chatMain.scrollTop === "number" ? chatMain.scrollTop : null,
    nearBottom: isElementNearBottom(chatMain),
  };
}

function runOnNextFrame(callback) {
  if (typeof requestAnimationFrame === "function") {
    requestAnimationFrame(callback);
    return;
  }
  setTimeout(callback, 0);
}

function syncChatScrollPosition(previousSnapshot = null) {
  const ticket = getActiveChatTicket();
  if (!ticket) {
    resetChatScrollState();
    lastRenderedChatMessageSignature = {
      ticketId: "",
      signature: "",
    };
    return;
  }

  const ticketId = String(ticket.id || "").trim();
  const unreadVisibleBeforeSync = getChatUnreadState(ticketId);
  const renderableMessages = getRenderableMessages(ticket);
  const currentSignature = buildRenderableChatMessageSignature(ticket, renderableMessages);
  const previousSignature =
    lastRenderedChatMessageSignature.ticketId === ticketId ? lastRenderedChatMessageSignature.signature : "";
  const hasExplicitBottomRequest = pendingChatScrollRequest?.ticketId === ticketId;
  const shouldRestoreScroll =
    !hasExplicitBottomRequest &&
    previousSnapshot?.ticketId === ticketId &&
    typeof previousSnapshot?.scrollTop === "number";
  const hasNewVisibleMessages = Boolean(previousSignature) && currentSignature !== previousSignature;

  let nextPlan = null;
  if (hasExplicitBottomRequest) {
    nextPlan = {
      ticketId,
      type: "bottom",
      behavior: pendingChatScrollRequest.behavior || "auto",
    };
    clearRequestedChatScroll();
    setChatUnreadState(ticketId, false);
  } else if (hasNewVisibleMessages) {
    if (previousSnapshot?.preserveBottom) {
      nextPlan = {
        ticketId,
        type: "restore",
        scrollTop: previousSnapshot?.scrollTop ?? 0,
        behavior: previousSnapshot.behavior || "auto",
      };
      setChatUnreadState(ticketId, false);
    } else if (previousSnapshot?.nearBottom) {
      nextPlan = {
        ticketId,
        type: "bottom",
        behavior: "smooth",
      };
      setChatUnreadState(ticketId, false);
    } else if (shouldRestoreScroll) {
      nextPlan = {
        ticketId,
        type: "restore",
        scrollTop: previousSnapshot?.scrollTop ?? 0,
      };
      setChatUnreadState(ticketId, true);
    } else {
      setChatUnreadState(ticketId, true);
    }
  } else if (previousSnapshot?.preserveBottom) {
    nextPlan = {
      ticketId,
      type: "restore",
      scrollTop: previousSnapshot.scrollTop ?? 0,
      behavior: previousSnapshot.behavior || "auto",
    };
  } else if (shouldRestoreScroll) {
    nextPlan = { ticketId, type: "restore", scrollTop: previousSnapshot?.scrollTop ?? 0 };
  }

  lastRenderedChatMessageSignature = {
    ticketId,
    signature: currentSignature,
  };
  const unreadVisibleAfterSync = getChatUnreadState(ticketId);
  if (unreadVisibleBeforeSync !== unreadVisibleAfterSync) {
    const shell = appRoot.querySelector(".app-shell");
    const mainRegion = shell?.querySelector?.('[data-authed-region="main"]') || null;
    if (mainRegion) {
      renderMainRegion(mainRegion);
      bindAuthedEvents();
    }
  }

  if (!nextPlan) {
    scheduledChatScrollPlan = null;
    scheduledChatScrollJobId += 1;
    return;
  }

  scheduledChatScrollPlan = nextPlan;
  scheduledChatScrollJobId += 1;
  const jobId = scheduledChatScrollJobId;

  runOnNextFrame(() => {
    if (jobId !== scheduledChatScrollJobId) {
      return;
    }
    const chatMain = appRoot.querySelector(".chat-main");
    if (!chatMain) {
      return;
    }

    if (nextPlan.type === "bottom") {
      if (typeof chatMain.scrollHeight !== "number") {
        return;
      }
      scrollElementToTop(chatMain, chatMain.scrollHeight, nextPlan.behavior || "auto");
      scheduledChatScrollPlan = null;
      return;
    }

    scrollElementToTop(chatMain, nextPlan.scrollTop, nextPlan.behavior || "auto");
    scheduledChatScrollPlan = null;
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

function renderMainRegion(mainRegion) {
  if (!mainRegion) {
    return;
  }
  if (state.view !== "chat-ticket") {
    mainRegion.innerHTML = renderMainContent();
    return;
  }
  const ticket = getTicketById(state.activeTicketId);
  const viewState = buildChatTicketViewState(ticket);
  if (!viewState) {
    mainRegion.innerHTML = renderChatTicket();
    return;
  }
  if (shouldPreserveActiveChatComposerOnRender(viewState) && patchChatTicketWhilePreservingComposer(mainRegion, viewState)) {
    return;
  }
  mainRegion.innerHTML = renderChatTicketFromState(viewState);
}

function renderAuthed() {
  const shell = ensureAuthedShell();
  shell.querySelector('[data-authed-region="sidebar-nav"]').innerHTML = renderSidebarNav();
  shell.querySelector('[data-authed-region="sidebar-content"]').innerHTML = renderSidebarContent();
  shell.querySelector('[data-authed-region="sidebar-footer"]').innerHTML = renderSidebarFooter();
  shell.querySelector('[data-authed-region="topbar"]').innerHTML = renderTopbar();
  shell.querySelector('[data-authed-region="context"]').innerHTML = renderContextBar();
  renderMainRegion(shell.querySelector('[data-authed-region="main"]'));

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

async function requestProxyTextAck(ticketId, message, controller) {
  try {
    const response = await fetch("/api/client/ack", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      signal: controller?.signal,
      body: JSON.stringify({
        ticket_id: ticketId,
        customer_id: state.user?.id || "",
        message,
      }),
    });
    if (!response.ok) {
      return { ackText: "", source: "client_model" };
    }
    const payload = await response.json();
    const ackText = String(payload?.ack_text || "").trim();
    return {
      ackText,
      source: String(payload?.source || "client_model").trim() || "client_model",
    };
  } catch (error) {
    return { ackText: "", source: "client_model" };
  }
}

async function startClientAck(ticketId, message) {
  const normalizedTicketId = String(ticketId || "").trim();
  if (!normalizedTicketId) {
    return;
  }
  const fallbackText = buildStaticClientAck(message);
  clearPendingAckState();

  const controller = createAbortController();
  const flowId = crypto.randomUUID();
  state.pendingAckAbortController = controller;
  state.pendingAckTicketId = normalizedTicketId;
  state.pendingAckFlowId = flowId;
  const fallbackTimerId = setTimeout(() => {
    if (
      state.pendingAckFlowId !== flowId ||
      String(state.pendingAckTicketId || "").trim() !== normalizedTicketId
    ) {
      return;
    }
    clearPendingAckFallbackTimer();
    const latestTicket = getTicketById(normalizedTicketId);
    if (latestTicket && ticketHasAssistantReply(latestTicket)) {
      clearPendingAckState({ ticketId: normalizedTicketId });
      return;
    }
    const stagedAck = getStagedClientAck(normalizedTicketId);
    if (stagedAck) {
      setTransientClientAck(normalizedTicketId, stagedAck.content, { source: stagedAck.source });
      clearStagedClientAck(normalizedTicketId);
    } else {
      setTransientClientAck(normalizedTicketId, fallbackText, { source: "fallback" });
    }
    if (!state.pendingAckAbortController) {
      state.pendingAckTicketId = null;
      state.pendingAckFlowId = null;
    }
    render();
  }, DEFAULT_CLIENT_ACK_FALLBACK_TIMEOUT_MS);
  state.pendingAckFallbackTimerId = fallbackTimerId;

  const result = await requestProxyTextAck(normalizedTicketId, message, controller);

  if (
    state.pendingAckFlowId !== flowId ||
    String(state.pendingAckTicketId || "").trim() !== normalizedTicketId
  ) {
    return;
  }

  state.pendingAckAbortController = null;
  const ackText = String(result?.ackText || "").trim();
  if (!ackText) {
    if (!state.pendingAckFallbackTimerId) {
      state.pendingAckTicketId = null;
      state.pendingAckFlowId = null;
    }
    return;
  }
  const latestTicket = getTicketById(normalizedTicketId);
  if (latestTicket && ticketHasAssistantReply(latestTicket)) {
    clearPendingAckState({ ticketId: normalizedTicketId, abortTransport: false });
    return;
  }

  const ackSource = String(result?.source || "client_model").trim() || "client_model";
  if (state.pendingAckFallbackTimerId) {
    setStagedClientAck(normalizedTicketId, ackText, { source: ackSource });
    return;
  }
  setTransientClientAck(normalizedTicketId, ackText, {
    source: ackSource,
  });
  state.pendingAckTicketId = null;
  state.pendingAckFlowId = null;
  render();
}

async function handleSendMessage(text, options = {}) {
  const ticketId = state.activeTicketId;
  const ticket = getTicketById(ticketId);
  if (!ticket || ticket.status === "resolved") {
    return;
  }
  const normalizedProduct = normalizeTicketProduct(ticket.product);
  if (isTicketEmpty(ticket) && !normalizedProduct) {
    toast("Select a product before sending your first message.", "error");
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
    String(ticket.status || "").trim().toLowerCase() === "escalated";
  updateTicketStatus(ticketId, hasEscalatedAssistance ? "escalated" : "communicating");
  state.editingMessageId = null;
  state.inputDraft = "";
  state.isSending = true;
  state.pendingTicketId = ticketId;
  state.pendingUserMessageId = userMessageId;
  state.pendingAbortController = createAbortController();
  stopPendingStatusPolling();
  requestChatScrollToBottom(ticketId, { behavior: "smooth" });
  render();
  startClientAck(ticketId, text).catch(() => {
    // Keep the static fallback ack when client-side model generation fails.
  });

  try {
    const response = await fetch("/api/tickets/query", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      signal: state.pendingAbortController.signal,
      body: JSON.stringify({
        ticket_id: ticketId,
        customer_id: state.user.id,
        product: normalizedProduct,
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
        : payload?.status === "investigating" ||
          payload?.status === "waiting_for_engineer" ||
          payload?.needs_engineer_input
        ? "investigating"
        : payload?.status === "escalated"
        ? "escalated"
        : payload?.status === "resolved"
        ? "resolved"
        : "communicating";
    updateTicketStatus(ticketId, nextStatus);
    await syncTicketsFromBackend({ silent: true });
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
      updateTicketStatus(ticketId, "communicating");
      render();
      await syncBackendTicketAction(ticketId, "processing");
      await syncTicketsFromBackend({ silent: true });
      render();
      toast("Session reopened");
    });
  });

  appRoot.querySelectorAll("[data-action='request-engineer-assistance']").forEach((element) => {
    element.addEventListener("click", async () => {
      const ticketId = element.getAttribute("data-ticket-id");
      if (!ticketId) {
        return;
      }
      if (await requestEngineerAssistance(ticketId)) {
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

  appRoot.querySelectorAll("[data-action='jump-chat-latest']").forEach((element) => {
    element.addEventListener("click", () => {
      const ticket = getActiveChatTicket();
      const ticketId = String(ticket?.id || "").trim();
      if (!ticketId) {
        return;
      }
      requestChatScrollToBottom(ticketId, { behavior: "smooth" });
      render();
    });
  });
  bindTicketProductSelect();
  bindStatusFilter();

  const form = document.getElementById("chat-input-form");
  if (form && !form.__clientComposerSubmitBound) {
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const message = String(state.inputDraft || "").trim();
      if (!message) {
        return;
      }
      await handleSendMessage(message, {
        editMessageId: state.editingMessageId,
      });
    });
    form.__clientComposerSubmitBound = true;
  }

  const input = document.getElementById("chat-input");
  if (input && !input.__clientComposerInputBound) {
    input.addEventListener("input", () => {
      state.inputDraft = input.value;
    });
    input.addEventListener("keydown", (event) => {
      if (event.key === "Enter" && !event.shiftKey) {
        event.preventDefault();
        form?.requestSubmit();
      }
    });
    input.__clientComposerInputBound = true;
  }

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

function bindTicketProductSelect() {
  appRoot.querySelectorAll("[data-product-select]").forEach((root) => {
    const ticketId = String(root.getAttribute("data-ticket-id") || "").trim();
    if (!ticketId) {
      return;
    }

    const trigger = root.querySelector("[data-product-select-trigger]");
    const panel = root.querySelector("[data-product-select-panel]");
    const hiddenInput = root.querySelector("input[type='hidden']");
    const options = Array.from(root.querySelectorAll("[data-product-select-option]"));
    const chatRoot = typeof root.closest === "function" ? root.closest(".chat-root") : null;

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

    const selectedValue = () => normalizeTicketProduct(getTicketById(ticketId)?.product);

    const setActiveDescendant = (option) => {
      if (option?.id) {
        trigger.setAttribute("aria-activedescendant", option.id);
        return;
      }
      trigger.removeAttribute("aria-activedescendant");
    };

    const getSelectedOption = () =>
      options.find((option) => option.getAttribute("data-value") === selectedValue()) || options[0];

    const openPanel = (focusTarget = "selected") => {
      clearCloseTimer();
      root.classList.add("is-open");
      chatRoot?.classList?.add("has-open-product-select");
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
      chatRoot?.classList?.remove("has-open-product-select");
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
      const nextValue = normalizeTicketProduct(value);
      if (!updateTicketProduct(ticketId, nextValue)) {
        closePanel({ restoreFocus: true });
        return;
      }
      if (hiddenInput) {
        hiddenInput.value = nextValue || "";
      }
      closePanel({ restoreFocus: true });
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
        selectOption(option.getAttribute("data-value") || "");
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
          selectOption(option.getAttribute("data-value") || "");
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
  });
}

function render() {
  const previousView = state.view;
  const previousTicketId = String(state.activeTicketId || "").trim();
  const previousChatScroll = captureChatScrollSnapshot();

  parseRoute();
  if (!state.user) {
    clearPendingRequestState();
    closeClientRealtimeConnection();
    resetChatScrollState();
    renderLogin();
    return;
  }

  const nextTicketId =
    state.view === "chat-ticket" ? String(state.activeTicketId || "").trim() : "";
  if (previousView === "chat-ticket" && previousTicketId && previousTicketId !== nextTicketId) {
    clearPendingAckState({ ticketId: previousTicketId });
  }
  if (!nextTicketId) {
    resetChatScrollState();
  } else if (previousView !== "chat-ticket" || previousTicketId !== nextTicketId) {
    requestChatScrollToBottom(nextTicketId);
  }

  setupClientRealtimeConnection();
  renderAuthed();
  syncChatScrollPosition(previousChatScroll);
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
