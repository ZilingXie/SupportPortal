const appRoot = document.getElementById("app");
const toastRoot = document.getElementById("toast-root");

const AUTH_KEY = "helpdesk_auth_user";
const CLIENT_ASSISTANT_NAME = "Sid";
const TICKETS_KEY = "helpdesk_tickets";
const SUPERSEDED_TURNS_KEY = "helpdesk_superseded_turns";
const COUNTER_KEY = "helpdesk_ticket_counter";
const MAX_RECENT = 5;
const LEGACY_REASSURANCE_MESSAGES = new Set([
  ["收到，", "我先帮你看一下。"].join(""),
  ["收到，", "我继续帮你跟进。"].join(""),
  ["收到，", "我来查看。"].join(""),
  ["Got it,", " let me check this for you."].join(""),
  ["Got it,", " I'm checking the latest status."].join(""),
  ["I got your message", " and I am checking it now."].join(""),
]);

const DEMO_USERS = [
  {
    id: "user-1",
    username: "Zac",
    name: "Zac",
    email: "zac@example.com",
    password: "Zac",
  },
];
const REPLY_COUNTDOWN_BASELINE_MINUTES = 20;
const REPLY_COUNTDOWN_MINUTES_BY_STATUS = {
  communicating: REPLY_COUNTDOWN_BASELINE_MINUTES,
  investigating: REPLY_COUNTDOWN_BASELINE_MINUTES,
  escalated: REPLY_COUNTDOWN_BASELINE_MINUTES,
};
const REPLY_COUNTDOWN_REFRESH_INTERVAL_MS = 60 * 1000;

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
const CLIENT_ROUTE_BRAND = "Support Portal";
const CLIENT_ROUTE_SUBLABEL = "Client Workspace";
const CLIENT2_ROUTE_MARKER = "client2-route-shell";
const DEFAULT_DRAFT_TICKET_TITLE = "New ticket";
const LEGACY_DEFAULT_DRAFT_TICKET_TITLE = "New Session";
const DELIVERED_LABEL_DELAY_MS = 5000;
const CUSTOMER_MESSAGE_MARKDOWN_FORMAT = "markdown";
const PLAINTEXT_MESSAGE_FORMAT = "plaintext";
const AGORA_STATUS_PAGE_URL = "https://status.agora.io/";
const SERVICE_EVENTS_ENDPOINT = "/api/client/service-events";
const SERVICE_EVENTS_REFRESH_INTERVAL_MS = 5 * 60 * 1000;
const SharedComposer = globalThis.SupportPortalComposer || {};
let renderMarkdownMessage =
  SharedComposer.renderMarkdownMessage ||
  ((value) =>
    String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll("\n", "<br>"));
let buildDefaultComposerToolbarState =
  SharedComposer.buildDefaultComposerToolbarState ||
  (() => ({
    bold: false,
    italic: false,
    list: false,
    codeBlock: false,
  }));
let serializeRichComposerHtmlToMarkdown =
  SharedComposer.serializeRichComposerHtmlToMarkdown || ((value) => String(value || ""));
const renderSharedComposerFormattingToolbarButtons =
  SharedComposer.renderComposerFormattingToolbarButtons || (() => "");
const applySharedComposerToolbarStateToButtons =
  SharedComposer.applyComposerToolbarStateToButtons || (() => {});
let clientComposerRuntime = null;

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
  newTicketPreviewTicketId: null,
  statusFilter: "all",
  isSending: false,
  loginError: "",
  isSubmittingLogin: false,
  inputDraft: "",
  inputDraftRichHtml: "",
  mobileSidebarOpen: false,
  editingMessageId: null,
  composerToolbarState: buildDefaultComposerToolbarState(),
  pendingAbortController: null,
  pendingTicketId: null,
  pendingUserMessageId: null,
  pendingPersistedUserMessageCreatedAt: null,
  pendingAsyncTicketId: null,
  pendingAsyncMessageCreatedAt: null,
  pendingByTicket: {},
  attachmentsByTicket: {},
  supersededTurnsByTicket: loadSupersededTurnsByTicket(),
  serviceEvents: buildDefaultServiceEventsState(),
};
let clientSocket = null;
let clientReconnectTimer = null;
let clientHeartbeatTimer = null;
let pendingStatusPollTimer = null;
let deliveredStatusRefreshTimer = null;
let deliveredStatusRefreshDueAt = 0;
let replyCountdownRefreshTimer = null;
let replyCountdownRefreshTicketId = "";
let pendingTicketsPageScrollReset = false;
let pendingChatScrollRequest = null;
let scheduledChatScrollPlan = null;
let scheduledChatScrollJobId = 0;
let lastRenderedChatMessageSignature = {
  ticketId: "",
  signature: "",
};

function isDefaultDraftTitle(value) {
  const normalized = String(value || "").trim();
  return (
    !normalized ||
    normalized === DEFAULT_DRAFT_TICKET_TITLE ||
    normalized === LEGACY_DEFAULT_DRAFT_TICKET_TITLE
  );
}

function getDefaultDraftTitle() {
  return DEFAULT_DRAFT_TICKET_TITLE;
}

function buildDefaultServiceEventsState() {
  return {
    loadState: "idle",
    items: [],
    statusPageUrl: AGORA_STATUS_PAGE_URL,
    fetchedAt: "",
    lastRequestedAtMs: 0,
  };
}

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

function normalizeAttachmentRecord(value) {
  if (!value || typeof value !== "object") {
    return null;
  }
  const assetId = String(value.assetId || value.asset_id || "").trim();
  const originalFilename = String(
    value.originalFilename || value.original_filename || value.fileName || value.file_name || "attachment"
  ).trim();
  if (!assetId && !originalFilename) {
    return null;
  }
  return {
    localId: String(value.localId || value.local_id || assetId || crypto.randomUUID()).trim(),
    assetId,
    originalFilename: originalFilename || "attachment",
    sizeBytes: Number(value.sizeBytes ?? value.size_bytes ?? 0) || 0,
    contentType: String(value.contentType || value.content_type || "").trim(),
    status: String(value.status || "uploaded").trim().toLowerCase(),
    error: String(value.error || "").trim(),
    agentReadEnabled: value.agentReadEnabled === true || value.agent_read_enabled === true,
  };
}

function normalizeMessageAttachments(message) {
  return (Array.isArray(message?.attachments) ? message.attachments : [])
    .map((attachment) => normalizeAttachmentRecord(attachment))
    .filter(Boolean);
}

function getComposerAttachments(ticketId = state.activeTicketId) {
  const normalizedTicketId = normalizeTicketKey(ticketId);
  if (!normalizedTicketId) {
    return [];
  }
  return (Array.isArray(state.attachmentsByTicket?.[normalizedTicketId])
    ? state.attachmentsByTicket[normalizedTicketId]
    : []
  )
    .map((attachment) => normalizeAttachmentRecord(attachment))
    .filter(Boolean);
}

function setComposerAttachments(ticketId, attachments) {
  const normalizedTicketId = normalizeTicketKey(ticketId);
  if (!normalizedTicketId) {
    return;
  }
  const normalizedAttachments = (Array.isArray(attachments) ? attachments : [])
    .map((attachment) => normalizeAttachmentRecord(attachment))
    .filter(Boolean);
  state.attachmentsByTicket = { ...(state.attachmentsByTicket || {}) };
  if (normalizedAttachments.length > 0) {
    state.attachmentsByTicket[normalizedTicketId] = normalizedAttachments;
  } else {
    delete state.attachmentsByTicket[normalizedTicketId];
  }
}

function clearUploadedComposerAttachments(ticketId) {
  const remaining = getComposerAttachments(ticketId).filter((attachment) => attachment.status !== "uploaded");
  setComposerAttachments(ticketId, remaining);
}

function uploadedComposerAssetIds(ticketId = state.activeTicketId) {
  return getComposerAttachments(ticketId)
    .filter((attachment) => attachment.status === "uploaded" && attachment.assetId)
    .map((attachment) => attachment.assetId);
}

function formatAttachmentSize(sizeBytes) {
  const bytes = Number(sizeBytes || 0);
  if (!Number.isFinite(bytes) || bytes <= 0) {
    return "";
  }
  if (bytes < 1024) {
    return `${bytes} B`;
  }
  if (bytes < 1024 * 1024) {
    return `${(bytes / 1024).toFixed(bytes < 10 * 1024 ? 1 : 0)} KB`;
  }
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function renderAttachmentStatusText(status, error = "") {
  if (status === "uploading") {
    return "Uploading";
  }
  if (status === "failed") {
    return error || "Failed";
  }
  return "Attached";
}

function renderComposerAttachmentsHtml(ticketId = state.activeTicketId) {
  const attachments = getComposerAttachments(ticketId);
  if (attachments.length === 0) {
    return "";
  }
  return `
    <div class="attachment-chip-list" data-chat-section="composer-attachments">
      ${attachments
        .map((attachment) => {
          const isUploading = attachment.status === "uploading";
          const isFailed = attachment.status === "failed";
          const statusText = renderAttachmentStatusText(attachment.status, attachment.error);
          const chipClass = ["attachment-chip", isUploading ? "is-uploading" : "", isFailed ? "is-failed" : ""]
            .filter(Boolean)
            .join(" ");
          return `
            <span class="${chipClass}" data-attachment-local-id="${escapeHtml(attachment.localId)}">
              <span class="material-symbols-outlined" aria-hidden="true">${isFailed ? "error" : "description"}</span>
              <span class="attachment-chip-main">
                <span class="attachment-chip-name">${escapeHtml(attachment.originalFilename)}</span>
                <span class="attachment-chip-meta">${escapeHtml(
                  [formatAttachmentSize(attachment.sizeBytes), statusText].filter(Boolean).join(" · ")
                )}</span>
              </span>
              <button type="button" class="attachment-chip-remove" data-attachment-remove-id="${escapeHtml(
                attachment.localId
              )}" aria-label="Remove attachment" title="Remove attachment">
                <span class="material-symbols-outlined" aria-hidden="true">close</span>
              </button>
            </span>
          `;
        })
        .join("")}
    </div>
  `;
}

function renderMessageAttachmentsHtml(message) {
  const attachments = normalizeMessageAttachments(message);
  if (attachments.length === 0) {
    return "";
  }
  return `
    <div class="message-attachment-list">
      ${attachments
        .map((attachment) => `
          <button
            type="button"
            class="message-attachment"
            data-asset-download-id="${escapeHtml(attachment.assetId)}"
            title="Download attachment"
          >
            <span class="material-symbols-outlined" aria-hidden="true">description</span>
            <span class="message-attachment-main">
              <span class="message-attachment-name">${escapeHtml(attachment.originalFilename)}</span>
              <span class="message-attachment-meta">${escapeHtml(formatAttachmentSize(attachment.sizeBytes))}</span>
            </span>
            <span class="material-symbols-outlined" aria-hidden="true">download</span>
          </button>
        `)
        .join("")}
    </div>
  `;
}

function removeComposerAttachment(ticketId, localId) {
  const normalizedLocalId = String(localId || "").trim();
  if (!normalizedLocalId) {
    return false;
  }
  const nextAttachments = getComposerAttachments(ticketId).filter(
    (attachment) => attachment.localId !== normalizedLocalId
  );
  setComposerAttachments(ticketId, nextAttachments);
  return true;
}

function isAllowedLogAttachmentFile(file) {
  const name = String(file?.name || "").toLowerCase();
  return [".log", ".err", ".txt"].some((extension) => name.endsWith(extension));
}

function openAttachmentFilePicker() {
  const ticket = getTicketById(state.activeTicketId);
  if (!ticket || ticket.status === "resolved") {
    return false;
  }
  const input = document.createElement("input");
  input.type = "file";
  input.accept = ".log,.err,.txt,text/plain";
  input.multiple = true;
  input.style.display = "none";
  input.addEventListener("change", () => {
    const files = Array.from(input.files || []);
    uploadSelectedLogAttachments(files, ticket.id).finally(() => {
      input.remove();
    });
  });
  document.body?.appendChild(input);
  input.click();
  return true;
}

async function uploadSelectedLogAttachments(files, ticketId = state.activeTicketId) {
  const normalizedTicketId = normalizeTicketKey(ticketId);
  if (!normalizedTicketId || !Array.isArray(files) || files.length === 0) {
    return;
  }
  for (const file of files) {
    if (!isAllowedLogAttachmentFile(file)) {
      toast("Only .log, .err, and .txt files can be attached.", "error");
      continue;
    }
    const localId = crypto.randomUUID();
    const initialAttachment = {
      localId,
      assetId: "",
      originalFilename: file.name || "attachment.log",
      sizeBytes: Number(file.size || 0),
      contentType: file.type || "text/plain",
      status: "uploading",
    };
    setComposerAttachments(normalizedTicketId, [...getComposerAttachments(normalizedTicketId), initialAttachment]);
    render();
    try {
      const intentResponse = await fetch("/api/assets/upload-intents", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          ticket_id: normalizedTicketId,
          customer_id: state.user?.id || "",
          file_name: file.name || "attachment.log",
          content_type: file.type || "text/plain",
          size_bytes: Number(file.size || 0),
        }),
      });
      if (!intentResponse.ok) {
        throw new Error(`Upload intent failed with status ${intentResponse.status}`);
      }
      const intent = await intentResponse.json();
      const formData = new FormData();
      Object.entries(intent?.upload?.fields || {}).forEach(([key, value]) => {
        formData.append(key, value);
      });
      formData.append("file", file);
      const uploadResponse = await fetch(intent?.upload?.url, {
        method: "POST",
        body: formData,
      });
      if (!uploadResponse.ok) {
        throw new Error(`S3 upload failed with status ${uploadResponse.status}`);
      }
      const assetId = String(intent?.asset?.asset_id || "").trim();
      const completeResponse = await fetch(`/api/assets/${encodeURIComponent(assetId)}/complete`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ customer_id: state.user?.id || "" }),
      });
      if (!completeResponse.ok) {
        throw new Error(`Upload completion failed with status ${completeResponse.status}`);
      }
      const completed = await completeResponse.json();
      const completedAsset = completed?.asset || intent?.asset || {};
      setComposerAttachments(
        normalizedTicketId,
        getComposerAttachments(normalizedTicketId).map((attachment) =>
          attachment.localId === localId
            ? {
                ...attachment,
                assetId: String(completedAsset.asset_id || assetId).trim(),
                originalFilename: String(
                  completedAsset.original_filename || completedAsset.file_name || attachment.originalFilename
                ).trim(),
                sizeBytes: Number(completedAsset.size_bytes || attachment.sizeBytes || 0),
                contentType: String(completedAsset.content_type || attachment.contentType || "").trim(),
                status: "uploaded",
              }
            : attachment
        )
      );
    } catch (error) {
      setComposerAttachments(
        normalizedTicketId,
        getComposerAttachments(normalizedTicketId).map((attachment) =>
          attachment.localId === localId
            ? { ...attachment, status: "failed", error: error?.message || "Upload failed" }
            : attachment
        )
      );
      toast(`Attachment upload failed: ${error.message}`, "error");
    }
    render();
  }
}

async function downloadAsset(assetId) {
  const normalizedAssetId = String(assetId || "").trim();
  if (!normalizedAssetId) {
    return;
  }
  try {
    const params = new URLSearchParams();
    if (state.user?.id) {
      params.set("customer_id", state.user.id);
    }
    const suffix = params.toString() ? `?${params.toString()}` : "";
    const response = await fetch(`/api/assets/${encodeURIComponent(normalizedAssetId)}/download-url${suffix}`);
    if (!response.ok) {
      throw new Error(`Download URL failed with status ${response.status}`);
    }
    const payload = await response.json();
    const downloadUrl = sanitizeUrl(payload?.download_url);
    if (!downloadUrl) {
      throw new Error("Download URL was empty");
    }
    window.open(downloadUrl, "_blank", "noopener,noreferrer");
  } catch (error) {
    toast(`Attachment download failed: ${error.message}`, "error");
  }
}

function normalizeMessageContentFormat(value) {
  const normalized = String(value || "").trim().toLowerCase();
  if (normalized === CUSTOMER_MESSAGE_MARKDOWN_FORMAT) {
    return CUSTOMER_MESSAGE_MARKDOWN_FORMAT;
  }
  if (normalized === PLAINTEXT_MESSAGE_FORMAT) {
    return PLAINTEXT_MESSAGE_FORMAT;
  }
  return "";
}

function shouldRenderMarkdownForMessage(message) {
  const normalizedRole = normalizeRenderableMessageRole(message);
  if (normalizedRole === "assistant") {
    return true;
  }
  if (normalizedRole !== "user") {
    return false;
  }
  return (
    normalizeMessageContentFormat(message?.contentFormat || message?.content_format) ===
    CUSTOMER_MESSAGE_MARKDOWN_FORMAT
  );
}

function normalizeSingleLineText(value) {
  return String(value || "")
    .replace(/\s+/g, " ")
    .trim();
}

function normalizeServiceEventItem(item, index) {
  const normalizedTitle = normalizeSingleLineText(item?.title);
  return {
    title: normalizedTitle || `Service Event ${index + 1}`,
    summary: normalizeSingleLineText(item?.summary),
    link: sanitizeUrl(item?.link),
    statusLabel: normalizeSingleLineText(item?.status_label || item?.statusLabel),
    postedAtLabel: normalizeSingleLineText(item?.posted_at_label || item?.postedAtLabel),
  };
}

function normalizeServiceEventsPayload(payload, requestedAtMs) {
  const items = Array.isArray(payload?.items)
    ? payload.items.map((item, index) => normalizeServiceEventItem(item, index)).filter(Boolean)
    : [];
  return {
    loadState: "ready",
    items,
    statusPageUrl: sanitizeUrl(payload?.status_page_url || payload?.statusPageUrl) || AGORA_STATUS_PAGE_URL,
    fetchedAt: normalizeSingleLineText(payload?.fetched_at || payload?.fetchedAt),
    lastRequestedAtMs: requestedAtMs,
  };
}

function shouldRefreshWorkspaceServiceEvents() {
  const current = state.serviceEvents || buildDefaultServiceEventsState();
  if (current.loadState === "loading") {
    return false;
  }
  if (current.loadState === "idle") {
    return true;
  }
  const requestedAtMs = Number(current.lastRequestedAtMs || 0);
  if (!requestedAtMs) {
    return current.loadState !== "ready";
  }
  return Date.now() - requestedAtMs >= SERVICE_EVENTS_REFRESH_INTERVAL_MS;
}

async function fetchWorkspaceServiceEvents(requestedAtMs) {
  let nextState = null;
  try {
    const response = await fetch(SERVICE_EVENTS_ENDPOINT);
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }
    const payload = await response.json();
    nextState = normalizeServiceEventsPayload(payload, requestedAtMs);
  } catch (_error) {
    nextState = {
      ...buildDefaultServiceEventsState(),
      loadState: "error",
      lastRequestedAtMs: requestedAtMs,
    };
  }

  if (Number(state.serviceEvents?.lastRequestedAtMs || 0) !== requestedAtMs) {
    return;
  }
  state.serviceEvents = nextState;
  if (state.user && state.view === "chat-home") {
    render();
  }
}

function ensureWorkspaceServiceEventsLoaded() {
  if (!state.user || !shouldRefreshWorkspaceServiceEvents()) {
    return;
  }
  const current = state.serviceEvents || buildDefaultServiceEventsState();
  const requestedAtMs = Date.now();
  state.serviceEvents = {
    ...current,
    loadState: "loading",
    statusPageUrl: sanitizeUrl(current.statusPageUrl) || AGORA_STATUS_PAGE_URL,
    lastRequestedAtMs: requestedAtMs,
  };
  void fetchWorkspaceServiceEvents(requestedAtMs);
}

function formatMultilineText(value) {
  return escapeHtml(value).replaceAll("\n", "<br>");
}

function buildMarkdownInlineTextNode(value) {
  return {
    type: "text",
    value: String(value ?? ""),
  };
}

function collectMarkdownInlinePlainText(nodes = []) {
  return nodes
    .map((node) => {
      if (!node) {
        return "";
      }
      if (node.type === "text" || node.type === "code") {
        return String(node.value ?? "");
      }
      return collectMarkdownInlinePlainText(node.children || []);
    })
    .join("");
}

function canOpenMarkdownInlineDelimiter(value, index, delimiter) {
  const previousChar = index > 0 ? String(value[index - 1] || "") : "";
  const nextChar = String(value[index + delimiter.length] || "");
  if (!nextChar || /\s/.test(nextChar)) {
    return false;
  }
  if (delimiter === "_" && /[A-Za-z0-9]/.test(previousChar)) {
    return false;
  }
  return true;
}

function parseMarkdownInlineCodeToken(value, startIndex) {
  let index = startIndex + 1;
  let codeText = "";
  while (index < value.length) {
    const currentChar = value[index];
    if (currentChar === "\n") {
      return null;
    }
    if (currentChar === "\\") {
      if (index + 1 < value.length) {
        codeText += value[index + 1];
        index += 2;
        continue;
      }
      return null;
    }
    if (currentChar === "`") {
      return {
        value: codeText,
        nextIndex: index + 1,
      };
    }
    codeText += currentChar;
    index += 1;
  }
  return null;
}

function parseMarkdownLinkUrlToken(value, startIndex) {
  if (String(value[startIndex] || "") !== "(") {
    return null;
  }
  let index = startIndex + 1;
  let url = "";
  while (index < value.length) {
    const currentChar = value[index];
    if (currentChar === "\n") {
      return null;
    }
    if (currentChar === "\\") {
      if (index + 1 < value.length) {
        url += value[index + 1];
        index += 2;
        continue;
      }
      return null;
    }
    if (currentChar === ")") {
      return {
        url,
        nextIndex: index + 1,
      };
    }
    url += currentChar;
    index += 1;
  }
  return null;
}

function parseMarkdownInlineSequence(value, startIndex = 0, stopToken = "") {
  const nodes = [];
  let textBuffer = "";
  let index = startIndex;

  const flushTextBuffer = () => {
    if (!textBuffer) {
      return;
    }
    nodes.push(buildMarkdownInlineTextNode(textBuffer));
    textBuffer = "";
  };

  while (index < value.length) {
    if (stopToken && value.startsWith(stopToken, index)) {
      flushTextBuffer();
      return {
        nodes,
        nextIndex: index + stopToken.length,
        closed: true,
      };
    }

    const currentChar = value[index];

    if (currentChar === "\\") {
      if (index + 1 < value.length) {
        textBuffer += value[index + 1];
        index += 2;
        continue;
      }
      textBuffer += currentChar;
      index += 1;
      continue;
    }

    if (currentChar === "`") {
      const codeToken = parseMarkdownInlineCodeToken(value, index);
      if (codeToken) {
        flushTextBuffer();
        nodes.push({
          type: "code",
          value: codeToken.value,
        });
        index = codeToken.nextIndex;
        continue;
      }
    }

    if (value.startsWith("**", index) && canOpenMarkdownInlineDelimiter(value, index, "**")) {
      const strongToken = parseMarkdownInlineSequence(value, index + 2, "**");
      if (strongToken.closed && collectMarkdownInlinePlainText(strongToken.nodes).trim()) {
        flushTextBuffer();
        nodes.push({
          type: "strong",
          children: strongToken.nodes,
        });
        index = strongToken.nextIndex;
        continue;
      }
    }

    if ((currentChar === "_" || currentChar === "*") && canOpenMarkdownInlineDelimiter(value, index, currentChar)) {
      const emphasisToken = parseMarkdownInlineSequence(value, index + 1, currentChar);
      if (emphasisToken.closed && collectMarkdownInlinePlainText(emphasisToken.nodes).trim()) {
        flushTextBuffer();
        nodes.push({
          type: "em",
          children: emphasisToken.nodes,
        });
        index = emphasisToken.nextIndex;
        continue;
      }
    }

    if (currentChar === "[") {
      const labelToken = parseMarkdownInlineSequence(value, index + 1, "]");
      if (labelToken.closed) {
        const urlToken = parseMarkdownLinkUrlToken(value, labelToken.nextIndex);
        if (urlToken) {
          const rawToken = value.slice(index, urlToken.nextIndex);
          const href = sanitizeUrl(urlToken.url);
          flushTextBuffer();
          if (href && collectMarkdownInlinePlainText(labelToken.nodes).trim()) {
            nodes.push({
              type: "link",
              href,
              children: labelToken.nodes,
            });
          } else {
            nodes.push(buildMarkdownInlineTextNode(rawToken));
          }
          index = urlToken.nextIndex;
          continue;
        }
      }
    }

    textBuffer += currentChar;
    index += 1;
  }

  flushTextBuffer();
  return {
    nodes,
    nextIndex: index,
    closed: false,
  };
}

function renderMarkdownInlineNodes(nodes = []) {
  return nodes
    .map((node) => {
      if (!node) {
        return "";
      }
      switch (node.type) {
        case "text":
          return escapeHtml(node.value);
        case "strong":
          return `<strong>${renderMarkdownInlineNodes(node.children || [])}</strong>`;
        case "em":
          return `<em>${renderMarkdownInlineNodes(node.children || [])}</em>`;
        case "link":
          return `<a href="${escapeHtml(node.href || "")}" target="_blank" rel="noopener noreferrer">${renderMarkdownInlineNodes(
            node.children || []
          )}</a>`;
        case "code":
          return `<code>${escapeHtml(node.value)}</code>`;
        default:
          return "";
      }
    })
    .join("");
}

function renderInlineMarkdown(value) {
  const text = String(value ?? "");
  if (!text) {
    return "";
  }
  return renderMarkdownInlineNodes(parseMarkdownInlineSequence(text).nodes);
}

function isOrderedListLine(line) {
  return /^\s*\d+\.\s+/.test(String(line || ""));
}

function isUnorderedListLine(line) {
  return /^\s*[-*]\s+/.test(String(line || ""));
}

function legacyRenderMarkdownMessage(value) {
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
    shouldRenderMarkdownForMessage(message)
      ? `<div class="message-markdown">${renderMarkdownMessage(message.content || "")}</div>`
      : `<div>${formatMultilineText(message.content || "")}</div>`;
  const withAttachments = `${base}${renderMessageAttachmentsHtml(message)}`;
  if (normalizeRenderableMessageRole(message) !== "assistant") {
    return withAttachments;
  }
  return `${withAttachments}${renderCitationsHtml(normalizedCitations)}`;
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
  state.mobileSidebarOpen = false;
  localStorage.removeItem(AUTH_KEY);
  state.user = null;
  state.serviceEvents = buildDefaultServiceEventsState();
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

function normalizeTicketKey(value) {
  return String(value || "").trim();
}

function normalizeSupersededTurnRecord(value) {
  if (!value || typeof value !== "object") {
    return null;
  }
  const messageId = String(value.messageId || value.message_id || "").trim();
  const createdAt = String(value.createdAt || value.created_at || "").trim();
  if (!messageId && !createdAt) {
    return null;
  }
  return {
    messageId,
    createdAt,
  };
}

function loadSupersededTurnsByTicket() {
  try {
    const raw = localStorage.getItem(SUPERSEDED_TURNS_KEY);
    const parsed = raw ? JSON.parse(raw) : {};
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
      return {};
    }
    const normalized = {};
    for (const [ticketId, turns] of Object.entries(parsed)) {
      const normalizedTicketId = normalizeTicketKey(ticketId);
      if (!normalizedTicketId || !Array.isArray(turns)) {
        continue;
      }
      const normalizedTurns = turns.map(normalizeSupersededTurnRecord).filter(Boolean);
      if (normalizedTurns.length > 0) {
        normalized[normalizedTicketId] = normalizedTurns;
      }
    }
    return normalized;
  } catch {
    return {};
  }
}

function saveSupersededTurnsByTicket() {
  localStorage.setItem(SUPERSEDED_TURNS_KEY, JSON.stringify(state.supersededTurnsByTicket || {}));
}

function getSupersededTurnsForTicket(ticketId) {
  return Array.isArray(state.supersededTurnsByTicket?.[normalizeTicketKey(ticketId)])
    ? state.supersededTurnsByTicket[normalizeTicketKey(ticketId)]
    : [];
}

function setSupersededTurnsForTicket(ticketId, turns) {
  const normalizedTicketId = normalizeTicketKey(ticketId);
  if (!normalizedTicketId) {
    return;
  }
  const normalizedTurns = (Array.isArray(turns) ? turns : [])
    .map(normalizeSupersededTurnRecord)
    .filter(Boolean);
  if (normalizedTurns.length > 0) {
    state.supersededTurnsByTicket[normalizedTicketId] = normalizedTurns;
  } else {
    delete state.supersededTurnsByTicket[normalizedTicketId];
  }
  saveSupersededTurnsByTicket();
}

function rememberSupersededTurn(ticketId, turn) {
  const normalizedTicketId = normalizeTicketKey(ticketId);
  const normalizedTurn = normalizeSupersededTurnRecord(turn);
  if (!normalizedTicketId || !normalizedTurn) {
    return;
  }
  const existing = getSupersededTurnsForTicket(normalizedTicketId);
  const duplicate = existing.some(
    (entry) =>
      (normalizedTurn.messageId && entry.messageId === normalizedTurn.messageId) ||
      (normalizedTurn.createdAt && entry.createdAt === normalizedTurn.createdAt)
  );
  if (duplicate) {
    return;
  }
  setSupersededTurnsForTicket(normalizedTicketId, [...existing, normalizedTurn]);
}

function isSupersededTurn(ticketId, message) {
  const messageId = String(message?.id || "").trim();
  const createdAt = String(message?.createdAt || message?.created_at || "").trim();
  return getSupersededTurnsForTicket(ticketId).some(
    (entry) =>
      (messageId && entry.messageId === messageId) || (createdAt && entry.createdAt === createdAt)
  );
}

function normalizePendingSession(value) {
  if (!value || typeof value !== "object") {
    return null;
  }
  const phase = String(value.phase || "").trim().toLowerCase();
  if (phase !== "submitting" && phase !== "queued") {
    return null;
  }
  return {
    phase,
    userMessageId: String(value.userMessageId || "").trim(),
    persistedMessageCreatedAt: String(value.persistedMessageCreatedAt || "").trim(),
    queuedMessageCreatedAt: String(value.queuedMessageCreatedAt || "").trim(),
    waitingForDurableReply: phase === "queued" ? value.waitingForDurableReply !== false : false,
    abortController: value.abortController || null,
  };
}

function getLegacyPendingSession(ticketId) {
  const normalizedTicketId = normalizeTicketKey(ticketId);
  const legacyTicketId = normalizeTicketKey(state.pendingTicketId || state.pendingAsyncTicketId);
  if (!normalizedTicketId || !legacyTicketId || normalizedTicketId !== legacyTicketId) {
    return null;
  }
  const phase = state.pendingAsyncTicketId ? "queued" : state.isSending ? "submitting" : "";
  if (!phase) {
    return null;
  }
  return normalizePendingSession({
    phase,
    userMessageId: state.pendingUserMessageId,
    persistedMessageCreatedAt: state.pendingPersistedUserMessageCreatedAt,
    queuedMessageCreatedAt: state.pendingAsyncMessageCreatedAt,
    waitingForDurableReply: Boolean(state.pendingAsyncTicketId),
    abortController: state.pendingAbortController,
  });
}

function getPendingSession(ticketId) {
  const normalizedTicketId = normalizeTicketKey(ticketId);
  if (!normalizedTicketId) {
    return null;
  }
  return (
    normalizePendingSession(state.pendingByTicket?.[normalizedTicketId]) ||
    getLegacyPendingSession(normalizedTicketId)
  );
}

function getPendingSessionEntries() {
  const entries = Object.entries(state.pendingByTicket || {})
    .map(([ticketId, session]) => [normalizeTicketKey(ticketId), normalizePendingSession(session)])
    .filter((entry) => entry[0] && entry[1]);
  if (entries.length > 0) {
    return entries;
  }
  const legacyTicketId = normalizeTicketKey(state.pendingTicketId || state.pendingAsyncTicketId);
  const legacySession = getLegacyPendingSession(legacyTicketId);
  return legacyTicketId && legacySession ? [[legacyTicketId, legacySession]] : [];
}

function getRepresentativePendingEntry() {
  const entries = getPendingSessionEntries();
  if (entries.length === 0) {
    return null;
  }
  const activeTicketId = normalizeTicketKey(state.activeTicketId);
  const activeEntry = activeTicketId
    ? entries.find(([ticketId]) => ticketId === activeTicketId) || null
    : null;
  if (activeEntry) {
    return {
      ticketId: activeEntry[0],
      session: activeEntry[1],
    };
  }
  return {
    ticketId: entries[0][0],
    session: entries[0][1],
  };
}

function syncLegacyPendingState() {
  const entries = getPendingSessionEntries();
  state.isSending = entries.length > 0;
  const representative = getRepresentativePendingEntry();
  state.pendingAbortController = representative?.session?.abortController || null;
  state.pendingTicketId = representative?.ticketId || null;
  state.pendingUserMessageId = representative?.session?.userMessageId || null;
  state.pendingPersistedUserMessageCreatedAt =
    representative?.session?.persistedMessageCreatedAt || null;
  state.pendingAsyncTicketId =
    representative?.session?.phase === "queued" ? representative.ticketId : null;
  state.pendingAsyncMessageCreatedAt =
    representative?.session?.phase === "queued"
      ? representative.session.queuedMessageCreatedAt || representative.session.persistedMessageCreatedAt
      : null;
  if (!entries.some(([, session]) => session?.phase === "queued")) {
    stopPendingStatusPolling();
  }
}

function setPendingSession(ticketId, session) {
  const normalizedTicketId = normalizeTicketKey(ticketId);
  const normalizedSession = normalizePendingSession(session);
  if (!normalizedTicketId || !normalizedSession) {
    return;
  }
  state.pendingByTicket = {
    ...(state.pendingByTicket || {}),
    [normalizedTicketId]: normalizedSession,
  };
  syncLegacyPendingState();
}

function getQueuedPendingTicketIds() {
  return getPendingSessionEntries()
    .filter(([, session]) => session?.phase === "queued")
    .map(([ticketId]) => ticketId);
}

function abortAllPendingSubmissions() {
  for (const [, session] of getPendingSessionEntries()) {
    if (session?.phase === "submitting" && session.abortController?.abort) {
      session.abortController.abort();
    }
  }
}

function buildNewSessionHintText() {
  return "Describe your issue. Sid will identify the product if needed.";
}

function renderNewSessionHintCard() {
  const hintText = buildNewSessionHintText();
  return `
    <div class="empty-chat-hint" aria-live="polite">
      <p class="empty-chat-hint-eyebrow">${escapeHtml(getDefaultDraftTitle())}</p>
      <p class="empty-chat-hint-copy">${escapeHtml(hintText)}</p>
    </div>
  `;
}

function isLegacyReassuranceMessage(message) {
  const messageContent = String(message?.content || "").trim();
  return (
    String(message?.role || "").toLowerCase() === "assistant" &&
    LEGACY_REASSURANCE_MESSAGES.has(messageContent)
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

function shouldHideSupersededAssistantTurn(ticketId, messages, index) {
  const role = normalizeRenderableMessageRole(messages[index]);
  if (role === "user") {
    return false;
  }
  for (let currentIndex = index - 1; currentIndex >= 0; currentIndex -= 1) {
    if (normalizeRenderableMessageRole(messages[currentIndex]) !== "user") {
      continue;
    }
    return isSupersededTurn(ticketId, messages[currentIndex]);
  }
  return false;
}

function getRenderableMessages(ticket) {
  const ticketId = normalizeTicketKey(ticket?.id);
  return Array.isArray(ticket?.messages)
    ? ticket.messages.filter(
        (message, index, messages) =>
          !shouldHideLegacyReassuranceMessage(messages, index) &&
          !shouldHideSupersededAssistantTurn(ticketId, messages, index)
      )
    : [];
}

function stopPendingStatusPolling() {
  if (!pendingStatusPollTimer) {
    return;
  }
  clearInterval(pendingStatusPollTimer);
  pendingStatusPollTimer = null;
}

function hasDurableAssistantReplyAfterMessage(ticket, messageId = null) {
  const messages = getRenderableMessages(ticket);
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

function getPendingReplyAnchorIndex(ticket, renderableMessages = null) {
  const messages = Array.isArray(renderableMessages)
    ? renderableMessages
    : Array.isArray(ticket?.messages)
    ? ticket.messages
    : [];
  const pendingSession = getPendingSession(ticket?.id);
  const pendingUserId = String(pendingSession?.userMessageId || "").trim();
  if (pendingUserId) {
    const index = messages.findIndex((message) => String(message?.id || "").trim() === pendingUserId);
    if (index >= 0) {
      return index;
    }
  }

  const pendingCreatedAt = String(
    pendingSession?.persistedMessageCreatedAt || pendingSession?.queuedMessageCreatedAt || ""
  ).trim();
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

function clearPendingRequestState(ticketId = null) {
  const normalizedTicketId = normalizeTicketKey(ticketId);
  if (!normalizedTicketId) {
    state.pendingByTicket = {};
    state.pendingAbortController = null;
    state.pendingTicketId = null;
    state.pendingUserMessageId = null;
    state.pendingPersistedUserMessageCreatedAt = null;
    state.pendingAsyncTicketId = null;
    state.pendingAsyncMessageCreatedAt = null;
    state.isSending = false;
    syncLegacyPendingState();
    return;
  }
  if (state.pendingByTicket && Object.prototype.hasOwnProperty.call(state.pendingByTicket, normalizedTicketId)) {
    const nextPendingByTicket = { ...(state.pendingByTicket || {}) };
    delete nextPendingByTicket[normalizedTicketId];
    state.pendingByTicket = nextPendingByTicket;
  } else if (
    normalizeTicketKey(state.pendingTicketId || state.pendingAsyncTicketId) === normalizedTicketId
  ) {
    state.pendingTicketId = null;
    state.pendingUserMessageId = null;
    state.pendingPersistedUserMessageCreatedAt = null;
    state.pendingAsyncTicketId = null;
    state.pendingAsyncMessageCreatedAt = null;
    state.pendingAbortController = null;
    state.isSending = false;
  }
  syncLegacyPendingState();
}

function isTicketSending(ticketId) {
  return Boolean(getPendingSession(ticketId));
}

function isTicketSubmitting(ticketId) {
  return getPendingSession(ticketId)?.phase === "submitting";
}

function ticketHasAssistantReply(ticket) {
  const messages = getRenderableMessages(ticket);
  if (messages.length === 0) {
    return false;
  }
  const anchorIndex = getPendingReplyAnchorIndex(ticket, messages);
  if (anchorIndex < 0) {
    return false;
  }
  return messages
    .slice(anchorIndex + 1)
    .some((message) => String(message?.role || "").toLowerCase() !== "user");
}

function isTicketAwaitingDurableReply(ticket) {
  const pendingSession = getPendingSession(ticket?.id);
  return Boolean(pendingSession) && pendingSession.phase === "queued" && !ticketHasAssistantReply(ticket);
}

function pendingSessionMatchesCreatedAt(session, messageCreatedAt) {
  const normalizedCreatedAt = String(messageCreatedAt || "").trim();
  if (!session || !normalizedCreatedAt) {
    return false;
  }
  return [session.persistedMessageCreatedAt, session.queuedMessageCreatedAt].some(
    (value) => String(value || "").trim() === normalizedCreatedAt
  );
}

function reconcilePendingAsyncStateAfterSync(options = {}) {
  const normalizedTicketId = normalizeTicketKey(options?.ticketId);
  if (!normalizedTicketId) {
    return false;
  }
  const pendingSession = getPendingSession(normalizedTicketId);
  if (!pendingSession || pendingSession.phase !== "queued") {
    return false;
  }

  const eventName = String(options?.eventName || "").trim().toLowerCase();
  const eventMessageCreatedAt = String(options?.messageCreatedAt || "").trim();
  if (eventName === "ticket_ai_generation_stopped") {
    if (!eventMessageCreatedAt || pendingSessionMatchesCreatedAt(pendingSession, eventMessageCreatedAt)) {
      clearPendingRequestState(normalizedTicketId);
      return true;
    }
    return false;
  }

  const pendingTicket = getTicketById(normalizedTicketId);
  if (!pendingTicket || ticketHasAssistantReply(pendingTicket)) {
    clearPendingRequestState(normalizedTicketId);
    return true;
  }
  return false;
}

function ensurePendingStatusPolling() {
  if (pendingStatusPollTimer || !state.user || getQueuedPendingTicketIds().length === 0) {
    return;
  }

  pendingStatusPollTimer = setInterval(() => {
    const queuedTicketIds = getQueuedPendingTicketIds();
    if (!state.user || queuedTicketIds.length === 0) {
      stopPendingStatusPolling();
      return;
    }

    syncTicketsFromBackend({ silent: true })
      .then(() => {
        let changed = false;
        for (const ticketId of queuedTicketIds) {
          changed =
            reconcilePendingAsyncStateAfterSync({ ticketId }) || changed;
        }
        if (changed) {
          render();
        }
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
    const eventTicketId = normalizeTicketKey(payload?.ticket_id);
    const eventMessageCreatedAt = String(payload?.message_created_at || "").trim();
    if (
      getPendingSession(eventTicketId)?.phase === "queued" &&
      (eventName === "ticket_ai_response_ready" || eventName === "ticket_ai_generation_stopped")
    ) {
      await syncTicketsFromBackend({ silent: true });
      reconcilePendingAsyncStateAfterSync({
        ticketId: eventTicketId,
        eventName,
        messageCreatedAt: eventMessageCreatedAt,
      });
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

function clearDeliveredStatusRefreshTimer() {
  if (deliveredStatusRefreshTimer) {
    clearTimeout(deliveredStatusRefreshTimer);
  }
  deliveredStatusRefreshTimer = null;
  deliveredStatusRefreshDueAt = 0;
}

function clearReplyCountdownRefreshTimer() {
  if (replyCountdownRefreshTimer) {
    clearInterval(replyCountdownRefreshTimer);
  }
  replyCountdownRefreshTimer = null;
  replyCountdownRefreshTicketId = "";
}

function shouldShowDeliveredLabel(message) {
  if (String(message?.role || "").toLowerCase() !== "user") {
    return false;
  }
  const createdAt = toTimestamp(message?.createdAt);
  if (!createdAt) {
    return true;
  }
  return Date.now() - createdAt >= DELIVERED_LABEL_DELAY_MS;
}

function scheduleDeliveredStatusRefresh(message) {
  if (String(message?.role || "").toLowerCase() !== "user") {
    return;
  }
  const createdAt = toTimestamp(message?.createdAt);
  if (!createdAt) {
    return;
  }
  const dueAt = createdAt + DELIVERED_LABEL_DELAY_MS;
  const delay = dueAt - Date.now();
  if (delay <= 0) {
    return;
  }
  if (deliveredStatusRefreshTimer && deliveredStatusRefreshDueAt <= dueAt) {
    return;
  }
  clearDeliveredStatusRefreshTimer();
  deliveredStatusRefreshDueAt = dueAt;
  deliveredStatusRefreshTimer = setTimeout(() => {
    deliveredStatusRefreshTimer = null;
    deliveredStatusRefreshDueAt = 0;
    render();
  }, delay);
}

function getLatestRenderableMessage(ticket) {
  const messages = getRenderableMessages(ticket);
  return messages.length > 0 ? messages[messages.length - 1] : null;
}

function formatReplyCountdownText(remainingMinutes) {
  const normalizedMinutes = Math.max(0, Number(remainingMinutes) || 0);
  if (normalizedMinutes < 60) {
    return `Next update in ${String(normalizedMinutes).padStart(2, "0")}:00`;
  }
  const hours = Math.floor(normalizedMinutes / 60);
  const minutes = normalizedMinutes % 60;
  return `Next update in ${hours}h ${String(minutes).padStart(2, "0")}m`;
}

function getReplyCountdownState(ticket) {
  const normalizedStatus = String(ticket?.status || "").trim().toLowerCase();
  const durationMinutes = REPLY_COUNTDOWN_MINUTES_BY_STATUS[normalizedStatus];
  if (!durationMinutes) {
    return null;
  }
  const latestMessage = getLatestRenderableMessage(ticket);
  if (!latestMessage || normalizeRenderableMessageRole(latestMessage) !== "user") {
    return null;
  }
  const anchorTimestamp = toTimestamp(
    latestMessage?.createdAt || latestMessage?.created_at || ticket?.updatedAt
  );
  if (!anchorTimestamp) {
    return null;
  }
  const remainingMs = Math.max(0, anchorTimestamp + durationMinutes * 60 * 1000 - Date.now());
  const remainingMinutes = remainingMs > 0 ? Math.ceil(remainingMs / (60 * 1000)) : 0;
  return {
    status: normalizedStatus,
    text: formatReplyCountdownText(remainingMinutes),
  };
}

function syncReplyCountdownRefresh(ticket, countdownState) {
  const ticketId = String(ticket?.id || "").trim();
  const activeTicketId = String(state.activeTicketId || "").trim();
  if (!countdownState || state.view !== "chat-ticket" || !ticketId || ticketId !== activeTicketId) {
    clearReplyCountdownRefreshTimer();
    return;
  }
  if (replyCountdownRefreshTimer && replyCountdownRefreshTicketId === ticketId) {
    return;
  }
  clearReplyCountdownRefreshTimer();
  replyCountdownRefreshTicketId = ticketId;
  replyCountdownRefreshTimer = setInterval(() => {
    const currentActiveTicketId = String(state.activeTicketId || "").trim();
    if (state.view !== "chat-ticket" || !replyCountdownRefreshTicketId || currentActiveTicketId !== replyCountdownRefreshTicketId) {
      clearReplyCountdownRefreshTimer();
      return;
    }
    const activeTicket = getTicketById(replyCountdownRefreshTicketId);
    if (!getReplyCountdownState(activeTicket)) {
      clearReplyCountdownRefreshTimer();
      return;
    }
    render();
  }, REPLY_COUNTDOWN_REFRESH_INTERVAL_MS);
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

function isNewTicketPreviewTicket(ticket) {
  const ticketId = String(ticket?.id || "").trim();
  if (!ticketId) {
    return false;
  }
  return isTicketEmpty(ticket);
}

function clearStaleNewTicketPreviewTicketId(ticket) {
  const ticketId = String(ticket?.id || "").trim();
  if (!ticketId || isTicketEmpty(ticket)) {
    return;
  }
  if (String(state.newTicketPreviewTicketId || "").trim() === ticketId) {
    state.newTicketPreviewTicketId = null;
  }
}

function usesNewTicketShellTicket(ticket) {
  const ticketId = String(ticket?.id || "").trim();
  return ticketId.length > 0;
}

function getActiveNewTicketShellTicket() {
  if (state.view !== "chat-ticket") {
    return null;
  }
  const ticket = getTicketById(state.activeTicketId);
  return usesNewTicketShellTicket(ticket) ? ticket : null;
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
  if (isDefaultDraftTitle(currentTitle) && candidateTitle.length > 0 && !isDefaultDraftTitle(candidateTitle)) {
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
    title: String(ticket?.subject || getDefaultDraftTitle()),
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
      contentFormat: normalizeMessageContentFormat(
        message?.contentFormat || message?.content_format
      ),
      citations: normalizeCitations({
        citations: Array.isArray(message?.citations) ? message.citations : [],
        sources: Array.isArray(message?.sources) ? message.sources : [],
      }),
      attachments: normalizeMessageAttachments(message),
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
  const pendingCreatedAt = String(state.pendingPersistedUserMessageCreatedAt || "").trim();
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

function shouldPreservePendingLocalTicketDuringSync(localTicket, remoteTicketIds = new Set()) {
  const localTicketId = normalizeTicketKey(localTicket?.id);
  if (!localTicketId || remoteTicketIds.has(localTicketId)) {
    return false;
  }
  if (String(localTicket?.userId || "").trim() !== String(state.user?.id || "").trim()) {
    return false;
  }
  if (!getPendingSession(localTicketId)) {
    return false;
  }
  return Array.isArray(localTicket?.messages) && localTicket.messages.length > 0;
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
    const remoteTicketIds = new Set(
      mapped.map((ticket) => normalizeTicketKey(ticket?.id)).filter(Boolean)
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
    const preservedPendingLocals = allLocal.filter((ticket) =>
      shouldPreservePendingLocalTicketDuringSync(ticket, remoteTicketIds)
    );
    saveAllTickets([...otherUsersLocal, ...mergedMapped, ...preservedPendingLocals, ...preservedDrafts]);
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
    title: getDefaultDraftTitle(),
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
    isDefaultDraftTitle(ticket?.title) &&
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
  state.newTicketPreviewTicketId = ticket.id;
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
  const ticket = getTicketById(normalizedId);
  if (!ticket) {
    return;
  }
  if (isNewTicketPreviewTicket(ticket)) {
    state.newTicketPreviewTicketId = normalizedId;
  } else if (String(state.newTicketPreviewTicketId || "").trim() === normalizedId) {
    state.newTicketPreviewTicketId = null;
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
    <div class="filter-select tickets-status-filter" data-filter-select>
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
  state.mobileSidebarOpen = false;
  const target = `#${path}`;
  if (window.location.hash === target) {
    parseRoute();
    render();
    return;
  }
  window.location.hash = target;
}

function requestTicketsPageScrollReset() {
  pendingTicketsPageScrollReset = true;
}

function resetTicketsPageScrollTop() {
  const shell = typeof ensureAuthedShell === "function" ? ensureAuthedShell() : null;
  const mainScrollContainer =
    shell?.querySelector?.('[data-authed-region="main"]') ||
    appRoot?.querySelector?.(".clienttest-main") ||
    null;
  const ticketsScrollContainer =
    mainScrollContainer?.querySelector?.(".tickets-root") ||
    appRoot?.querySelector?.(".tickets-root") ||
    null;

  scrollElementToTop(mainScrollContainer, 0, "auto");
  scrollElementToTop(ticketsScrollContainer, 0, "auto");

  if (typeof window.scrollTo === "function") {
    try {
      window.scrollTo({ top: 0, behavior: "auto" });
    } catch {
      try {
        window.scrollTo(0, 0);
      } catch {
        // Best-effort only.
      }
    }
  }
  if (typeof document?.documentElement?.scrollTop === "number") {
    document.documentElement.scrollTop = 0;
  }
  if (typeof document?.body?.scrollTop === "number") {
    document.body.scrollTop = 0;
  }
}

function consumeTicketsPageScrollReset() {
  if (!pendingTicketsPageScrollReset || state.view !== "tickets") {
    return;
  }
  pendingTicketsPageScrollReset = false;
  resetTicketsPageScrollTop();
}

function navigateToTicketsTop() {
  requestTicketsPageScrollReset();
  navigate("/tickets");
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
      <span class="sidebar-nav-label">New Ticket</span>
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
      <span class="sidebar-nav-label">My Tickets</span>
    </button>
  `;
}

function renderSidebarContent() {
  const tickets = getTicketsByUser(state.user.id);
  const recent = tickets.slice(0, MAX_RECENT);
  return `
    <div class="sidebar-label">Recent Tickets</div>
    ${
      recent.length === 0
        ? `<p class="session-empty">No tickets yet. Start a new ticket to begin your support history.</p>`
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
    <button class="btn btn-ghost sidebar-footer-btn" data-action="go-tickets" type="button">View My Tickets</button>
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
    <div class="app-shell clienttest-shell ${CLIENT2_ROUTE_MARKER}" data-client-route="client2">
      <button
        class="sidebar-overlay ${state.mobileSidebarOpen ? "is-visible" : ""}"
        data-action="close-sidebar"
        type="button"
        aria-label="Close navigation"
      ></button>
      <aside class="sidebar clienttest-sidebar ${state.mobileSidebarOpen ? "is-open" : ""}">
        <div class="sidebar-header">
          <div class="sidebar-brand">
            <div class="sidebar-brand-icon">
              <span class="material-symbols-outlined" aria-hidden="true">support_agent</span>
            </div>
            <div class="sidebar-brand-title">
              <span class="line-1">${CLIENT_ROUTE_BRAND}</span>
              <span class="line-2">${CLIENT_ROUTE_SUBLABEL}</span>
            </div>
          </div>
        </div>
        <nav class="sidebar-nav" aria-label="Client navigation" data-authed-region="sidebar-nav"></nav>
        <div class="sidebar-content" data-authed-region="sidebar-content"></div>
        <div class="sidebar-footer" data-authed-region="sidebar-footer"></div>
      </aside>
      <div class="workspace-shell clienttest-workspace">
        <div data-authed-region="topbar"></div>
        <div data-authed-region="context"></div>
        <main class="main clienttest-main" data-authed-region="main"></main>
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
    <header class="topbar clienttest-topbar">
      <div class="clienttest-topbar-leading">
        <button class="btn btn-ghost btn-round mobile-rail-toggle" data-action="toggle-sidebar" type="button" aria-label="Open navigation">
          <span class="material-symbols-outlined" aria-hidden="true">menu</span>
        </button>
        <div class="topbar-copy">
          <h2>${CLIENT_ROUTE_BRAND}</h2>
          <p>${CLIENT_ROUTE_SUBLABEL}</p>
        </div>
      </div>
      <div class="topbar-meta">
        <div class="topbar-pill">
          <span class="topbar-pill-label">Tickets</span>
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

function renderTicketHeaderActions(ticket) {
  if (!ticket) {
    return "";
  }
  const normalizedStatus = String(ticket.status || "").trim().toLowerCase();
  const assistanceControl =
    normalizedStatus === "escalated"
      ? ""
      : `
          <button
            class="btn btn-outline btn-inline context-assistance-btn"
            data-action="request-engineer-assistance"
            data-ticket-id="${ticket.id}"
            type="button"
          >Request Engineer</button>
        `;

  if (normalizedStatus === "resolved") {
    return `<button class="btn btn-outline" data-action="reopen-ticket" data-ticket-id="${ticket.id}" type="button">Reopen Ticket</button>`;
  }

  if (isTicketEmpty(ticket)) {
    return "";
  }

  return `
    ${assistanceControl}
    <button class="btn btn-outline btn-danger" data-action="resolve-ticket" data-ticket-id="${ticket.id}" type="button">Resolve</button>
  `;
}

function summarizePreviewText(value, maxLength = 180) {
  const normalized = String(value || "").replace(/\s+/g, " ").trim();
  if (!normalized) {
    return "";
  }
  if (normalized.length <= maxLength) {
    return normalized;
  }
  return `${normalized.slice(0, maxLength - 1).trimEnd()}…`;
}

function getLatestSupportReply(ticket) {
  const messages = Array.isArray(ticket?.messages) ? ticket.messages : [];
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const role = String(messages[index]?.role || "").trim().toLowerCase();
    if (role && role !== "user" && role !== "customer") {
      return messages[index];
    }
  }
  return null;
}

function buildTicketSummary(ticket) {
  const latestReply = getLatestSupportReply(ticket);
  if (latestReply?.content) {
    return summarizePreviewText(latestReply.content, 220);
  }
  const statusLabel = (STATUS_CONFIG[String(ticket?.status || "").trim().toLowerCase()] || STATUS_CONFIG.open).label;
  return `Sid is tracking this ticket. Current status: ${statusLabel}.`;
}

function buildRelatedKnowledgeItems(ticket) {
  const latestReply = getLatestSupportReply(ticket);
  const citations = normalizeCitations({
    citations: Array.isArray(latestReply?.citations) ? latestReply.citations : [],
    sources: Array.isArray(latestReply?.sources) ? latestReply.sources : [],
  });
  if (citations.length > 0) {
    return citations.slice(0, 3).map((citation, index) => ({
      title: citation.heading || citation.sourcePath || `Reference ${index + 1}`,
      meta: "Source-linked",
      href: citation.sourceUrl || "",
    }));
  }

  if (normalizeTicketProduct(ticket?.product) === "cloud_recording") {
    return [
      { title: "Recording upload delays", meta: "Operational note", href: "" },
      { title: "Storage region checklist", meta: "Setup guide", href: "" },
      { title: "Playback validation flow", meta: "QA reference", href: "" },
    ];
  }

  return [
    { title: "Troubleshooting call setup", meta: "Quick reference", href: "" },
    { title: "Token renewal checkpoints", meta: "SDK checklist", href: "" },
    { title: "Device and network triage", meta: "Runbook", href: "" },
  ];
}

function renderTicketInformationPanel(ticket) {
  const productLabel = getProductLabel(ticket?.product) || "General Support";
  return `
    <section class="ticket-side-card">
      <p class="ticket-side-kicker">Ticket Information</p>
      <div class="ticket-side-stack">
        <div class="ticket-side-row">
          <span class="ticket-side-row-label">Status</span>
          <div class="ticket-side-row-value">${statusBadge(ticket.status)}</div>
        </div>
        <div class="ticket-side-row">
          <span class="ticket-side-row-label">Ticket ID</span>
          <div class="ticket-side-row-value mono">${escapeHtml(ticket.id)}</div>
        </div>
        <div class="ticket-side-row">
          <span class="ticket-side-row-label">Product</span>
          <div class="ticket-side-row-value">${escapeHtml(productLabel)}</div>
        </div>
        <div class="ticket-side-row">
          <span class="ticket-side-row-label">Created</span>
          <div class="ticket-side-row-value">${escapeHtml(formatDate(ticket.createdAt))}</div>
        </div>
        <div class="ticket-side-row">
          <span class="ticket-side-row-label">Last Activity</span>
          <div class="ticket-side-row-value">${escapeHtml(formatDate(ticket.updatedAt))}</div>
        </div>
        <div class="ticket-side-row">
          <span class="ticket-side-row-label">Customer</span>
          <div class="ticket-side-row-value">${escapeHtml(state.user.name)}</div>
        </div>
      </div>
    </section>
  `;
}

function renderTicketSummaryPanel(ticket) {
  return `
    <section class="ticket-side-card ticket-summary-card">
      <p class="ticket-side-kicker">AI Summary</p>
      <div class="ticket-summary-body">
        <p>${escapeHtml(buildTicketSummary(ticket))}</p>
      </div>
    </section>
  `;
}

function renderRelatedKnowledgePanel(ticket) {
  const items = buildRelatedKnowledgeItems(ticket);
  return `
    <section class="ticket-side-card">
      <p class="ticket-side-kicker">Related Knowledge</p>
      <div class="ticket-knowledge-list">
        ${items
          .map((item) => {
            const body = `
              <span class="ticket-knowledge-title">${escapeHtml(item.title)}</span>
              <span class="ticket-knowledge-meta">${escapeHtml(item.meta)}</span>
            `;
            if (item.href) {
              return `<a class="ticket-knowledge-item ticket-knowledge-link" href="${escapeHtml(item.href)}" target="_blank" rel="noopener noreferrer">${body}</a>`;
            }
            return `<div class="ticket-knowledge-item">${body}</div>`;
          })
          .join("")}
      </div>
    </section>
  `;
}

function formatTicketDetailDateTime(value) {
  return new Date(value).toLocaleString("en-US", {
    month: "short",
    day: "2-digit",
    year: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

function buildNewTicketPageTitle(ticket) {
  if (isTicketEmpty(ticket)) {
    return getDefaultDraftTitle();
  }
  return String(ticket?.title || "New Ticket").trim() || "New Ticket";
}

function renderNewTicketStatusPill(ticket) {
  if (isTicketEmpty(ticket)) {
    return `<span class="new-ticket-status-pill draft">Draft</span>`;
  }
  return statusBadge(ticket.status);
}

function buildNewTicketAssignedAgent(ticket) {
  if (isTicketEmpty(ticket)) {
    return "Unassigned";
  }
  if (String(ticket?.status || "").trim().toLowerCase() === "investigating") {
    return "Engineer reviewing";
  }
  return "Support Team";
}

function buildNewTicketSummary(ticket) {
  if (isTicketEmpty(ticket)) {
    return "AI summary appears after you submit the first message and Sid begins structuring the case.";
  }
  return buildTicketSummary(ticket);
}

function buildNewTicketKnowledgeItems(ticket) {
  const messages = Array.isArray(ticket?.messages) ? ticket.messages : [];
  const items = [];
  const seenUrls = new Set();

  for (const message of messages) {
    const role = String(message?.role || "").trim().toLowerCase();
    if (role !== "assistant" && role !== "agent") {
      continue;
    }
    const citations = normalizeCitations({
      citations: Array.isArray(message?.citations) ? message.citations : [],
    });
    const sources = normalizeCitations({
      sources: Array.isArray(message?.sources) ? message.sources : [],
    });

    for (const citation of [...citations, ...sources]) {
      const href = sanitizeUrl(citation?.sourceUrl || "");
      if (!href || seenUrls.has(href)) {
        continue;
      }
      seenUrls.add(href);
      items.push({
        title: citation?.heading || citation?.sourcePath || `Reference ${items.length + 1}`,
        href,
      });
    }
  }
  return items;
}

function buildNewTicketAttachmentItems(ticket) {
  const messages = Array.isArray(ticket?.messages) ? ticket.messages : [];
  const items = [];
  const seenAssetIds = new Set();

  const addAttachment = (attachment) => {
    if (!attachment.assetId || seenAssetIds.has(attachment.assetId)) {
      return;
    }
    if (attachment.status && attachment.status !== "uploaded" && attachment.status !== "attached") {
      return;
    }
    seenAssetIds.add(attachment.assetId);
    items.push(attachment);
  };

  for (const message of messages) {
    const attachments = normalizeMessageAttachments(message);
    for (const attachment of attachments) {
      addAttachment(attachment);
    }
  }
  for (const attachment of getComposerAttachments(ticket?.id)) {
    addAttachment(attachment);
  }
  return items;
}

function isNewTicketPostSendState(viewState) {
  return Boolean(viewState?.usesNewTicketShell && !isTicketEmpty(viewState?.ticket));
}

function renderNewTicketInformationPanel(ticket, options = {}) {
  const isDraft = isTicketEmpty(ticket);
  const normalizedTicketId = String(ticket?.id || "").trim();
  const classes = ["new-ticket-info-card"];
  if (options.fixed !== false) {
    classes.push("new-ticket-fixed-info-card");
  }
  if (options.variant) {
    classes.push(`new-ticket-${options.variant}-info-card`);
  }
  return `
    <section class="${classes.join(" ")}">
      <div class="new-ticket-info-card-header">
        <p class="new-ticket-info-kicker">Ticket Information</p>
      </div>
      <div class="new-ticket-info-body">
        <div class="new-ticket-info-row">
          <span class="new-ticket-info-label">Status</span>
          <div class="new-ticket-info-value">${renderNewTicketStatusPill(ticket)}</div>
        </div>
        <div class="new-ticket-info-row">
          <span class="new-ticket-info-label">Ticket ID</span>
          <div class="new-ticket-info-value mono">${escapeHtml(normalizedTicketId || "Pending")}</div>
        </div>
        <div class="new-ticket-info-row">
          <span class="new-ticket-info-label">Created Date</span>
          <div class="new-ticket-info-value">${escapeHtml(isDraft ? "Now" : formatTicketDetailDateTime(ticket.createdAt))}</div>
        </div>
        <div class="new-ticket-info-row">
          <span class="new-ticket-info-label">Last Activity</span>
          <div class="new-ticket-info-value">${escapeHtml(isDraft ? "Not submitted" : formatTicketDetailDateTime(ticket.updatedAt))}</div>
        </div>
        <div class="new-ticket-info-row">
          <span class="new-ticket-info-label">Assigned Agent</span>
          <div class="new-ticket-info-value">${escapeHtml(buildNewTicketAssignedAgent(ticket))}</div>
        </div>
      </div>
    </section>
  `;
}

function renderNewTicketKnowledgePanel(ticket, options = {}) {
  const items = buildNewTicketKnowledgeItems(ticket);
  const classes = ["new-ticket-info-card"];
  if (options.fixed !== false) {
    classes.push("new-ticket-fixed-knowledge-card");
  }
  if (options.variant) {
    classes.push(`new-ticket-${options.variant}-knowledge-card`);
  }
  return `
    <section class="${classes.join(" ")}">
      <div class="new-ticket-info-card-header">
        <p class="new-ticket-info-kicker">Knowledge Base Articles</p>
      </div>
      <div class="new-ticket-info-body new-ticket-knowledge-list">
        ${
          items.length > 0
            ? `
                <div class="new-ticket-correspondence-sources new-ticket-knowledge-sources">
                  ${items
                    .map((item, index) => {
                      const label = escapeHtml(item.title || `Reference ${index + 1}`);
                      if (item.href) {
                        return `<a class="new-ticket-correspondence-source-chip" href="${escapeHtml(item.href)}" target="_blank" rel="noopener noreferrer">${label}</a>`;
                      }
                      return `<span class="new-ticket-correspondence-source-chip is-static">${label}</span>`;
                    })
                    .join("")}
                </div>
              `
            : `<p class="new-ticket-knowledge-placeholder new-ticket-info-value">All reference links provided by agent will show up here.</p>`
        }
      </div>
    </section>
  `;
}

function renderNewTicketAttachmentsPanel(ticket, options = {}) {
  const items = buildNewTicketAttachmentItems(ticket);
  const classes = ["new-ticket-info-card", "new-ticket-attachments-card"];
  if (options.fixed !== false) {
    classes.push("new-ticket-fixed-attachments-card");
  }
  if (options.variant) {
    classes.push(`new-ticket-${options.variant}-attachments-card`);
  }
  return `
    <section class="${classes.join(" ")}">
      <div class="new-ticket-info-card-header">
        <p class="new-ticket-info-kicker">Attachments</p>
      </div>
      <div class="new-ticket-info-body new-ticket-attachments-list">
        ${
          items.length > 0
            ? items
                .map(
                  (attachment) => `
                    <button
                      type="button"
                      class="new-ticket-attachment-item"
                      data-asset-download-id="${escapeHtml(attachment.assetId)}"
                      title="Download attachment"
                    >
                      <span class="material-symbols-outlined" aria-hidden="true">description</span>
                      <span class="new-ticket-attachment-main">
                        <span class="new-ticket-attachment-name">${escapeHtml(attachment.originalFilename)}</span>
                        <span class="new-ticket-attachment-meta">${escapeHtml(
                          [formatAttachmentSize(attachment.sizeBytes), "Uploaded"].filter(Boolean).join(" · ")
                        )}</span>
                      </span>
                      <span class="material-symbols-outlined new-ticket-attachment-download" aria-hidden="true">download</span>
                    </button>
                  `
                )
                .join("")
            : `<p class="new-ticket-attachments-placeholder new-ticket-info-value">Successfully uploaded attachments will show up here.</p>`
        }
      </div>
    </section>
  `;
}

function getNewTicketMessagePresenter(message) {
  const role = String(message?.role || "assistant").trim().toLowerCase();
  if (role === "user" || role === "customer") {
    return {
      tone: "customer",
      icon: "person",
      name: String(message?.authorName || state.user?.name || "Customer").trim() || "Customer",
      subtitle: String(message?.authorEmail || state.user?.email || "Customer message").trim() || "Customer message",
    };
  }
  if (role === "engineer") {
    return {
      tone: "engineer",
      icon: "support_agent",
      name: String(message?.authorName || "Support Engineer").trim() || "Support Engineer",
      subtitle: String(message?.authorEmail || "Human response").trim() || "Human response",
    };
  }
  return {
    tone: "assistant",
    icon: "smart_toy",
    name: `${CLIENT_ASSISTANT_NAME} (AI)`,
    subtitle: "Automated response",
  };
}

function renderNewTicketMessageContent(message) {
  if (shouldRenderMarkdownForMessage(message)) {
    return `<div class="message-markdown">${renderMarkdownMessage(message.content || "")}</div>`;
  }
  return `<div>${formatMultilineText(message.content || "")}</div>`;
}

function renderNewTicketCorrespondenceSourceChips(citations) {
  if (!Array.isArray(citations) || citations.length === 0) {
    return "";
  }
  return `
    <div class="new-ticket-correspondence-sources">
      ${citations
        .map((citation, index) => {
          const label = escapeHtml(citation.heading || citation.sourcePath || `Reference ${index + 1}`);
          if (citation.sourceUrl) {
            return `<a class="new-ticket-correspondence-source-chip" href="${escapeHtml(citation.sourceUrl)}" target="_blank" rel="noopener noreferrer">${label}</a>`;
          }
          return `<span class="new-ticket-correspondence-source-chip is-static">${label}</span>`;
        })
        .join("")}
    </div>
  `;
}

function renderNewTicketMessageCard(message) {
  const presenter = getNewTicketMessagePresenter(message);
  const citations = normalizeCitations({
    citations: Array.isArray(message?.citations) ? message.citations : [],
    sources: Array.isArray(message?.sources) ? message.sources : [],
  });
  return `
    <article class="new-ticket-thread-card ${presenter.tone}">
      <div class="new-ticket-thread-card-head">
        <div class="new-ticket-thread-identity">
          <span class="new-ticket-thread-avatar ${presenter.tone}">
            <span class="material-symbols-outlined" aria-hidden="true">${presenter.icon}</span>
          </span>
          <div class="new-ticket-thread-copy">
            <p class="new-ticket-thread-author">${escapeHtml(presenter.name)}</p>
            <p class="new-ticket-thread-subtitle">${escapeHtml(presenter.subtitle)}</p>
          </div>
        </div>
        <div class="new-ticket-thread-time">${escapeHtml(formatTicketDetailDateTime(message.createdAt || new Date().toISOString()))}</div>
      </div>
      <div class="new-ticket-thread-card-body">
        <div class="new-ticket-thread-card-copy">${renderNewTicketMessageContent(message)}</div>
        ${
          citations.length > 0
            ? `
              <div class="new-ticket-thread-card-tags">
                ${citations
                  .slice(0, 3)
                  .map(
                    (citation, index) =>
                      `<span class="new-ticket-thread-tag">${escapeHtml(
                        citation.heading || citation.sourcePath || `Reference ${index + 1}`
                      )}</span>`
                  )
                  .join("")}
              </div>
            `
            : ""
        }
      </div>
    </article>
  `;
}

function renderNewTicketCorrespondenceMessageCard(message) {
  const presenter = getNewTicketMessagePresenter(message);
  const citations = normalizeCitations({
    citations: Array.isArray(message?.citations) ? message.citations : [],
    sources: Array.isArray(message?.sources) ? message.sources : [],
  });
  const showDelivered = presenter.tone === "customer" && shouldShowDeliveredLabel(message);
  if (presenter.tone === "customer" && !showDelivered) {
    scheduleDeliveredStatusRefresh(message);
  }
  const correspondenceMetaHtml = `
    <div class="new-ticket-correspondence-meta">
      <p class="new-ticket-correspondence-time">${escapeHtml(
        formatTicketDetailDateTime(message.createdAt || new Date().toISOString())
      )}</p>
      ${
        showDelivered
          ? '<p class="new-ticket-correspondence-delivered">✅ Delivered</p>'
          : ""
      }
    </div>
  `;
  return `
    <article class="new-ticket-correspondence-card ${presenter.tone}">
      <div class="new-ticket-correspondence-card-head">
        <div class="new-ticket-correspondence-identity">
          <span class="new-ticket-correspondence-avatar ${presenter.tone}">
            <span class="material-symbols-outlined" aria-hidden="true">${presenter.icon}</span>
          </span>
          <div class="new-ticket-correspondence-copy">
            <p class="new-ticket-correspondence-author">${escapeHtml(presenter.name)}</p>
            <p class="new-ticket-correspondence-subtitle">${escapeHtml(presenter.subtitle)}</p>
          </div>
        </div>
        ${correspondenceMetaHtml}
      </div>
      <div class="new-ticket-correspondence-card-body">
        <div class="new-ticket-correspondence-body-copy">${renderNewTicketMessageContent(message)}</div>
        ${renderNewTicketCorrespondenceSourceChips(citations)}
      </div>
    </article>
  `;
}

function renderNewTicketThreadHtml(viewState) {
  if (viewState.renderableMessages.length === 0) {
    return `
      <div class="new-ticket-thread-empty">
        <p class="new-ticket-thread-empty-kicker">Draft Workspace</p>
        <h2>Your conversation thread will appear here after the first submission.</h2>
        <p>Use the intake composer below to describe the issue and share the impact or reproduction details.</p>
      </div>
    `;
  }

  return `${viewState.renderableMessages.map((message) => renderNewTicketMessageCard(message)).join("")}`;
}

function renderNewTicketPostSendThreadHtml(viewState) {
  return `${viewState.renderableMessages.map((message) => renderNewTicketCorrespondenceMessageCard(message)).join("")}`;
}

function renderComposerFormattingToolbarButtons({ canCompose = true } = {}) {
  const toolbarState = state.composerToolbarState || buildDefaultComposerToolbarState();
  return renderSharedComposerFormattingToolbarButtons({
    canCompose,
    toolbarState,
  });
}

function renderNewTicketComposerToolbar({ canCompose = true, includeSummary = true } = {}) {
  const formattingButtons = renderComposerFormattingToolbarButtons({ canCompose });
  if (!includeSummary) {
    return formattingButtons;
  }
  return (
    formattingButtons +
    `
      <button class="new-ticket-summary-toolbar-btn" type="button" aria-label="AI Summary" title="AI Summary">
        <span class="material-symbols-outlined" aria-hidden="true">auto_awesome</span>
        <span>AI Summary</span>
      </button>
    `
  );
}

function renderNewTicketComposerNoteHtml(viewState) {
  if (viewState.isEditing) {
    return `<div class="composer-note">Editing your draft message. Press Enter to resend, Shift+Enter for newline.</div>`;
  }
  return "";
}

function getChatComposerPlaceholder(viewState) {
  if (viewState?.usesNewTicketShell) {
    return isTicketEmpty(viewState.ticket)
      ? "Type your request or technical issue..."
      : "Add more context or follow-up details...";
  }
  return "Type your request or technical issue...";
}

function renderNewTicketDraftComposerActionHtml(viewState) {
  const buttonLabel = isTicketEmpty(viewState.ticket)
    ? viewState.isEditing
      ? "Update Draft"
      : "Submit Ticket"
    : viewState.isEditing
    ? "Resend Message"
    : "Send Message";

  return `
    <button
      class="new-ticket-inline-send-btn"
      type="submit"
      aria-label="${escapeHtml(buttonLabel)}"
      title="${escapeHtml(buttonLabel)}"
      ${viewState.canSubmit ? "" : "disabled"}
    >
      <span class="material-symbols-outlined" aria-hidden="true">arrow_upward</span>
    </button>
  `;
}

function renderNewTicketComposerActionHtml(viewState) {
  return renderNewTicketDraftComposerActionHtml(viewState);
}

function buildNewTicketPostSendMetaHtml(ticket) {
  const productLabel = getProductLabel(ticket?.product);
  const countdownState = getReplyCountdownState(ticket);
  syncReplyCountdownRefresh(ticket, countdownState);
  const items = [
    statusBadge(ticket.status),
    productLabel ? `<span class="new-ticket-postsend-meta-pill">${escapeHtml(productLabel)}</span>` : "",
    `<span class="new-ticket-postsend-meta-item">Updated ${escapeHtml(formatDate(ticket.updatedAt))}</span>`,
    countdownState
      ? `<span class="new-ticket-postsend-meta-item new-ticket-postsend-countdown status-${escapeHtml(
          countdownState.status
        )}">${escapeHtml(countdownState.text)}</span>`
      : "",
  ].filter(Boolean);
  return items.join("");
}

function renderNewTicketComposerPanel(viewState, composerClass) {
  return `
    <footer class="${composerClass}">
      <div class="new-ticket-composer-toolbar">
        ${renderNewTicketComposerToolbar({ canCompose: viewState.canCompose, includeSummary: true })}
      </div>
      <div data-chat-section="composer-note">${renderNewTicketComposerNoteHtml(viewState)}</div>
      <form id="chat-input-form" class="chat-input-inner new-ticket-composer-form" data-chat-section="composer-form">
        <div class="new-ticket-composer-input-shell">
          <div
            id="chat-input"
            class="textarea new-ticket-textarea composer-rich-input"
            contenteditable="${viewState.canCompose ? "true" : "false"}"
            role="textbox"
            aria-multiline="true"
            spellcheck="true"
            data-chat-composer-rich="true"
            data-placeholder="${escapeHtml(getChatComposerPlaceholder(viewState))}"
          >${ensureComposerDraftRichHtml()}</div>
          <div class="new-ticket-inline-action" data-chat-section="composer-action">
            ${renderNewTicketComposerActionHtml(viewState)}
          </div>
        </div>
      </form>
      <div data-chat-section="composer-attachments-region">${renderComposerAttachmentsHtml(viewState.ticket?.id)}</div>
    </footer>
  `;
}

function renderNewTicketTailComposer(viewState, { postsend = false } = {}) {
  const tailRowClass = postsend
    ? "clienttest-route-footer-band new-ticket-tail-row new-ticket-postsend-tail-row"
    : "clienttest-route-footer-band new-ticket-tail-row";
  const composerClass = postsend
    ? "new-ticket-composer-panel new-ticket-fixed-composer-panel new-ticket-postsend-composer new-ticket-tail-composer"
    : "new-ticket-composer-panel new-ticket-fixed-composer-panel new-ticket-tail-composer";

  return `
    <div class="${tailRowClass}">
      ${renderNewTicketComposerPanel(viewState, composerClass)}
    </div>
  `;
}

function buildNewTicketThreadFooterComposerClass(extraClassName = "") {
  return [
    "new-ticket-composer-panel",
    "new-ticket-fixed-composer-panel",
    "new-ticket-thread-footer-composer",
    extraClassName,
  ]
    .filter(Boolean)
    .join(" ");
}

function renderNewTicketPostSendInlineComposer(viewState) {
  return renderNewTicketComposerPanel(
    viewState,
    buildNewTicketThreadFooterComposerClass(
      "new-ticket-postsend-composer new-ticket-postsend-inline-composer"
    )
  );
}

function renderNewTicketDraftInlineComposer(viewState) {
  return renderNewTicketComposerPanel(
    viewState,
    buildNewTicketThreadFooterComposerClass(
      "new-ticket-postsend-composer new-ticket-postsend-inline-composer new-ticket-draft-inline-composer"
    )
  );
}

function renderNewTicketDraftTicketFromState(viewState) {
  clearReplyCountdownRefreshTimer();
  const ticket = viewState.ticket;
  return `
    <section class="chat-root clienttest-new-ticket-shell" data-chat-ticket-id="${escapeHtml(ticket.id)}">
      <div class="new-ticket-layout clienttest-route-page new-ticket-draft-inline-route">
        <header class="new-ticket-hero">
          <h1 class="new-ticket-page-title">${escapeHtml(buildNewTicketPageTitle(ticket))}</h1>
        </header>
        <div class="new-ticket-body-layout">
          <div class="new-ticket-main-column">
            <section class="new-ticket-thread-panel new-ticket-fixed-thread-panel">
              <main class="chat-main new-ticket-thread-scroll">
                <div class="message-list new-ticket-thread-list" data-chat-section="messages">
                  ${renderNewTicketThreadHtml(viewState)}
                </div>
              </main>
            </section>
            ${renderNewTicketDraftInlineComposer(viewState)}
          </div>
          <aside class="new-ticket-sidebar">
            ${renderNewTicketInformationPanel(ticket)}
            ${renderNewTicketKnowledgePanel(ticket)}
            ${renderNewTicketAttachmentsPanel(ticket)}
          </aside>
        </div>
      </div>
    </section>
  `;
}

function renderNewTicketPostSendTicketFromState(viewState) {
  const ticket = viewState.ticket;
  const actionButtons = renderTicketHeaderActions(ticket);
  const isTailComposerRoute = viewState.showVisibleFooterBand;
  const showInlineComposer = viewState.canCompose && !isTailComposerRoute;
  const showTailComposer = viewState.canCompose && isTailComposerRoute;
  return `
    <section class="chat-root clienttest-new-ticket-shell" data-chat-ticket-id="${escapeHtml(ticket.id)}">
      <div class="new-ticket-postsend-shell">
        <div class="new-ticket-postsend-page ${buildClient2RoutePageClass({ visibleFooterBand: isTailComposerRoute, tailComposerRoute: isTailComposerRoute })}">
          <header class="new-ticket-postsend-header">
            <div class="new-ticket-postsend-breadcrumb">My Tickets / Ticket #${escapeHtml(ticket.id)}</div>
            <div class="new-ticket-postsend-header-row">
              <div class="new-ticket-postsend-heading">
                <h1 class="new-ticket-page-title">${escapeHtml(buildNewTicketPageTitle(ticket))}</h1>
                <div class="new-ticket-postsend-meta">${buildNewTicketPostSendMetaHtml(ticket)}</div>
              </div>
              ${actionButtons ? `<div class="new-ticket-postsend-actions">${actionButtons}</div>` : ""}
            </div>
          </header>
          <div class="new-ticket-postsend-layout">
            <div class="new-ticket-postsend-main">
              <section class="new-ticket-postsend-thread">
                <main class="chat-main new-ticket-postsend-thread-scroll">
                  <div class="message-list new-ticket-postsend-message-list" data-chat-section="messages">
                    ${renderNewTicketPostSendThreadHtml(viewState)}
                  </div>
                </main>
              </section>
              ${showInlineComposer ? renderNewTicketPostSendInlineComposer(viewState) : ""}
            </div>
            <aside class="new-ticket-postsend-sidebar">
              ${renderNewTicketInformationPanel(ticket, { fixed: false, variant: "postsend" })}
              ${renderNewTicketKnowledgePanel(ticket, { variant: "postsend" })}
              ${renderNewTicketAttachmentsPanel(ticket, { variant: "postsend" })}
            </aside>
          </div>
          ${showTailComposer ? renderNewTicketTailComposer(viewState, { postsend: true }) : ""}
        </div>
      </div>
    </section>
  `;
}

function renderNewTicketTicketFromState(viewState) {
  if (isNewTicketPostSendState(viewState)) {
    return renderNewTicketPostSendTicketFromState(viewState);
  }
  return renderNewTicketDraftTicketFromState(viewState);
}

function renderContextBar() {
  if (state.view === "chat-ticket") {
    return `
      <section class="context-bar context-bar-static clienttest-preview-bar">
        <div class="context-copy">
          <div class="context-chip">
            <span class="material-symbols-outlined" aria-hidden="true">support_agent</span>
            <span>Support Ticket</span>
          </div>
          <div class="context-divider" aria-hidden="true"></div>
          <div class="context-ticket">
            <span class="context-ticket-title">Continue the same ticket with the redesigned correspondence surface and current client runtime.</span>
          </div>
        </div>
        <div class="context-actions">
          <button class="btn btn-outline" data-action="go-tickets" type="button">My Tickets</button>
        </div>
      </section>
    `;
  }
  return "";
}

function renderChatHome() {
  const tickets = getTicketsByUser(state.user.id);
  const activeTickets = tickets
    .filter((ticket) => String(ticket.status || "").trim().toLowerCase() !== "resolved")
    .slice(0, 4);
  const serviceEventsState =
    state.serviceEvents && typeof state.serviceEvents === "object"
      ? state.serviceEvents
      : buildDefaultServiceEventsState();
  const statusPageUrl = sanitizeUrl(serviceEventsState.statusPageUrl) || AGORA_STATUS_PAGE_URL;
  const serviceEventsBody =
    serviceEventsState.loadState === "loading" || serviceEventsState.loadState === "idle"
      ? `<p class="session-empty clienttest-empty-card">Loading latest Agora service events...</p>`
      : Array.isArray(serviceEventsState.items) && serviceEventsState.items.length > 0
      ? `
          <div class="clienttest-service-events-list">
            ${serviceEventsState.items
              .map(
                (item) => `
                  <article class="clienttest-service-event-card">
                    <div class="clienttest-service-event-meta">
                      ${item.statusLabel ? `<span class="clienttest-service-event-status">${escapeHtml(item.statusLabel)}</span>` : ""}
                      ${item.postedAtLabel ? `<span class="clienttest-service-event-time">${escapeHtml(item.postedAtLabel)}</span>` : ""}
                    </div>
                    <h4 class="clienttest-service-event-title">${escapeHtml(item.title)}</h4>
                    ${
                      item.summary
                        ? `<p class="clienttest-service-event-summary">${escapeHtml(item.summary)}</p>`
                        : ""
                    }
                    ${
                      item.link
                        ? `<a class="clienttest-service-event-link" href="${escapeHtml(item.link)}" target="_blank" rel="noopener noreferrer">View incident</a>`
                        : ""
                    }
                  </article>
                `
              )
              .join("")}
          </div>
        `
      : `<p class="session-empty clienttest-empty-card">Service events are temporarily unavailable. Open Agora Status Page -> for the latest updates.</p>`;

  return `
    <section class="welcome clienttest-home ${buildClient2RoutePageClass({ visibleFooterBand: true })}">
      <div class="clienttest-home-shell">
        <header class="clienttest-home-intro">
          <div class="clienttest-home-intro-head">
            <div class="bot-mark">
              <span class="material-symbols-outlined" aria-hidden="true">auto_awesome</span>
            </div>
            <div class="clienttest-home-intro-copy">
              <p class="welcome-kicker">${CLIENT_ROUTE_BRAND}</p>
              <h1 class="welcome-title">Manage your support tickets in one place</h1>
            </div>
          </div>
          <p class="welcome-desc">
            Track open tickets, return to recent conversations, and keep your support work moving.
          </p>
          <div class="welcome-actions clienttest-home-intro-actions">
            <button class="btn btn-primary" data-action="new-session" type="button">Start New Ticket</button>
            <button class="btn btn-outline" data-action="go-tickets" type="button">Open My Tickets</button>
          </div>
        </header>
        <div class="clienttest-home-content-grid">
          <article class="clienttest-home-panel clienttest-home-panel-active-tickets">
            <div class="clienttest-home-panel-header">
              <div>
                <p class="clienttest-home-panel-kicker">Active Tickets</p>
                <h3>Continue what needs attention</h3>
              </div>
            </div>
            <div class="clienttest-home-panel-body">
              ${
                activeTickets.length === 0
                  ? `<p class="session-empty clienttest-empty-card">No active tickets yet. Start a new one to open the redesigned detail view.</p>`
                  : activeTickets
                      .map((ticket) =>
                        renderHistoryRow(ticket, {
                          compact: false,
                          includeActions: false,
                        })
                    )
                      .join("")
              }
            </div>
            <div class="clienttest-home-panel-footer">
              <button class="clienttest-home-panel-footer-btn" data-action="go-tickets-top" type="button">view all tickets-></button>
            </div>
          </article>
          <article class="clienttest-home-panel">
            <div class="clienttest-home-panel-header">
              <div>
                <p class="clienttest-home-panel-kicker">Service Events</p>
                <h3>Latest Agora platform events</h3>
              </div>
              <a class="clienttest-home-panel-link" href="${escapeHtml(statusPageUrl)}" target="_blank" rel="noopener noreferrer">Open Agora Status Page -></a>
            </div>
            <div class="clienttest-home-panel-body">
              ${serviceEventsBody}
            </div>
          </article>
        </div>
      </div>
      ${renderClient2RouteFooterBand()}
    </section>
  `;
}

function legacyBuildDefaultComposerToolbarState() {
  return {
    bold: false,
    italic: false,
    list: false,
    codeBlock: false,
  };
}

function normalizeComposerToolbarActionStateKey(action) {
  return String(action || "").trim() === "code-block" ? "codeBlock" : String(action || "").trim();
}

function stripComposerZeroWidthSpaces(value) {
  return String(value || "").replace(/\u200B/g, "");
}

function decodeRichComposerHtmlEntities(value) {
  return String(value || "")
    .replace(/&nbsp;/gi, " ")
    .replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'")
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&amp;/g, "&");
}

function parseRichComposerHtmlAttributes(value) {
  const attrs = {};
  String(value || "").replace(/([A-Za-z0-9:_-]+)(?:\s*=\s*"([^"]*)")?/g, (_match, name, rawValue) => {
    attrs[String(name || "").toLowerCase()] = rawValue ?? "";
    return "";
  });
  return attrs;
}

function parseRichComposerHtmlFragment(value) {
  const root = { type: "root", children: [] };
  const stack = [root];
  const tokens = String(value || "").match(/<\/?[^>]+>|[^<]+/g) || [];

  tokens.forEach((token) => {
    const current = stack[stack.length - 1];
    if (!token) {
      return;
    }
    if (token.startsWith("</")) {
      const closingTag = token.slice(2, -1).trim().toLowerCase();
      while (stack.length > 1) {
        const candidate = stack.pop();
        if (candidate?.tag === closingTag) {
          break;
        }
      }
      return;
    }
    if (token.startsWith("<")) {
      const raw = token.slice(1, -1).trim();
      const selfClosing = raw.endsWith("/");
      const normalizedRaw = selfClosing ? raw.slice(0, -1).trim() : raw;
      const nameMatch = normalizedRaw.match(/^([A-Za-z0-9:_-]+)/);
      if (!nameMatch) {
        return;
      }
      const tag = nameMatch[1].toLowerCase();
      const node = {
        type: "element",
        tag,
        attrs: parseRichComposerHtmlAttributes(normalizedRaw.slice(nameMatch[1].length)),
        children: [],
      };
      current.children.push(node);
      if (!selfClosing && tag !== "br") {
        stack.push(node);
      }
      return;
    }
    current.children.push({
      type: "text",
      value: decodeRichComposerHtmlEntities(token),
    });
  });

  return root;
}

function isRichComposerBlockTag(tagName) {
  return ["p", "div", "ul", "ol", "pre"].includes(String(tagName || "").toLowerCase());
}

function isRichComposerWhitespaceTextNode(node) {
  return (
    node?.type === "text" && stripComposerZeroWidthSpaces(String(node.value || "")).trim().length === 0
  );
}

function isRichComposerBreakNode(node) {
  return node?.type === "element" && String(node.tag || "").toLowerCase() === "br";
}

function isRichComposerEmptyBlockWrapperNode(node) {
  if (
    node?.type !== "element" ||
    !["p", "div"].includes(String(node.tag || "").toLowerCase())
  ) {
    return false;
  }
  return (node.children || []).every(
    (child) => isRichComposerWhitespaceTextNode(child) || isRichComposerBreakNode(child)
  );
}

function isRichComposerEmptyListNode(node) {
  if (
    node?.type !== "element" ||
    !["ul", "ol"].includes(String(node.tag || "").toLowerCase())
  ) {
    return false;
  }
  const items = (node.children || []).filter(
    (child) => child?.type === "element" && String(child.tag || "").toLowerCase() === "li"
  );
  if (items.length === 0) {
    return true;
  }
  return items.every((item) =>
    (item.children || []).every(
      (child) => isRichComposerWhitespaceTextNode(child) || isRichComposerBreakNode(child)
    )
  );
}

function normalizeRichComposerParsedNodes(nodes = []) {
  const normalized = [];

  nodes.forEach((node) => {
    if (!node) {
      return;
    }
    if (node.type === "text") {
      if (!String(node.value || "")) {
        return;
      }
      normalized.push({
        ...node,
        value: String(node.value || ""),
      });
      return;
    }
    if (node.type !== "element") {
      return;
    }

    const normalizedChildren = normalizeRichComposerParsedNodes(node.children || []);
    const normalizedNode = {
      ...node,
      children: normalizedChildren,
    };
    const normalizedTag = String(normalizedNode.tag || "").toLowerCase();

    if (isRichComposerEmptyBlockWrapperNode(normalizedNode)) {
      return;
    }

    if (["ul", "ol"].includes(normalizedTag)) {
      const items = normalizedChildren.filter(
        (child) => child?.type === "element" && String(child.tag || "").toLowerCase() === "li"
      );
      const normalizedList = {
        ...normalizedNode,
        children: items,
      };
      if (isRichComposerEmptyListNode(normalizedList)) {
        return;
      }
      normalized.push(normalizedList);
      return;
    }

    if (["p", "div"].includes(normalizedTag)) {
      const hasOnlyBlocksOrWhitespace =
        normalizedChildren.length > 0 &&
        normalizedChildren.every(
          (child) =>
            isRichComposerWhitespaceTextNode(child) ||
            (child?.type === "element" && isRichComposerBlockTag(child.tag))
        );
      if (hasOnlyBlocksOrWhitespace) {
        normalized.push(
          ...normalizedChildren.filter(
            (child) => child?.type === "element" && isRichComposerBlockTag(child.tag)
          )
        );
        return;
      }
    }

    normalized.push(normalizedNode);
  });

  return normalized;
}

function renderRichComposerHtmlAttributes(attrs = {}) {
  return Object.entries(attrs)
    .filter(([name]) => String(name || "").trim())
    .map(([name, rawValue]) => {
      const normalizedName = String(name || "").trim().toLowerCase();
      const value = rawValue ?? "";
      return ` ${normalizedName}="${escapeHtml(String(value))}"`;
    })
    .join("");
}

function renderRichComposerHtmlNode(node) {
  if (!node) {
    return "";
  }
  if (node.type === "text") {
    return escapeHtml(String(node.value || ""));
  }
  if (node.type !== "element") {
    return "";
  }
  const tag = String(node.tag || "").toLowerCase();
  if (tag === "br") {
    return "<br>";
  }
  return `<${tag}${renderRichComposerHtmlAttributes(node.attrs || {})}>${renderRichComposerHtmlNodes(
    node.children || []
  )}</${tag}>`;
}

function renderRichComposerHtmlNodes(nodes = []) {
  return nodes.map((node) => renderRichComposerHtmlNode(node)).join("");
}

function isRichComposerCaretMarkerNode(node) {
  return (
    node?.type === "element" &&
    String(node.tag || "").toLowerCase() === "span" &&
    String(node.attrs?.["data-composer-caret-marker"] || "").toLowerCase() === "true"
  );
}

function isRichComposerEmptyLineMarkerNode(node) {
  return (
    node?.type === "element" &&
    String(node.tag || "").toLowerCase() === "span" &&
    String(node.attrs?.["data-composer-empty-line"] || "").toLowerCase() === "true"
  );
}

function buildRichComposerCaretMarkerNode() {
  return {
    type: "element",
    tag: "span",
    attrs: { "data-composer-caret-marker": "true" },
    children: [],
  };
}

function cloneRichComposerParsedNode(node) {
  if (!node) {
    return null;
  }
  if (node.type === "text") {
    return {
      type: "text",
      value: String(node.value || ""),
    };
  }
  if (node.type !== "element") {
    return null;
  }
  return {
    type: "element",
    tag: String(node.tag || "").toLowerCase(),
    attrs: { ...(node.attrs || {}) },
    children: cloneRichComposerParsedNodes(node.children || []),
  };
}

function cloneRichComposerParsedNodes(nodes = []) {
  return (Array.isArray(nodes) ? nodes : []).map((node) => cloneRichComposerParsedNode(node)).filter(Boolean);
}

function cloneRichComposerElementNodeWithChildren(node, children = []) {
  return {
    type: "element",
    tag: String(node?.tag || "").toLowerCase(),
    attrs: { ...(node?.attrs || {}) },
    children: cloneRichComposerParsedNodes(children),
  };
}

function hasRichComposerCaretMarkerInParsedNodes(nodes = []) {
  return (Array.isArray(nodes) ? nodes : []).some((node) => {
    if (!node) {
      return false;
    }
    if (isRichComposerCaretMarkerNode(node)) {
      return true;
    }
    return node.type === "element" && hasRichComposerCaretMarkerInParsedNodes(node.children || []);
  });
}

function isRichComposerParsedNodeStructurallyEmpty(node, { ignoreCaretMarker = true } = {}) {
  if (!node) {
    return true;
  }
  if (node.type === "text") {
    return stripComposerZeroWidthSpaces(String(node.value || "")).trim().length === 0;
  }
  if (node.type !== "element") {
    return true;
  }
  if (isRichComposerBreakNode(node) || isRichComposerEmptyLineMarkerNode(node)) {
    return true;
  }
  if (ignoreCaretMarker && isRichComposerCaretMarkerNode(node)) {
    return true;
  }
  return (node.children || []).every((child) =>
    isRichComposerParsedNodeStructurallyEmpty(child, { ignoreCaretMarker })
  );
}

function areRichComposerParsedNodesStructurallyEmpty(nodes = [], options = {}) {
  return (Array.isArray(nodes) ? nodes : []).every((node) =>
    isRichComposerParsedNodeStructurallyEmpty(node, options)
  );
}

function unwrapRichComposerSingleBlockChildren(nodes = []) {
  const normalizedNodes = cloneRichComposerParsedNodes(nodes);
  const meaningfulNodes = normalizedNodes.filter((node) => {
    if (!node) {
      return false;
    }
    if (node.type === "text") {
      return String(node.value || "").length > 0;
    }
    return true;
  });
  if (meaningfulNodes.length !== 1) {
    return normalizedNodes;
  }
  const candidate = meaningfulNodes[0];
  if (
    candidate?.type !== "element" ||
    !["p", "div"].includes(String(candidate.tag || "").toLowerCase())
  ) {
    return normalizedNodes;
  }
  const hasNestedBlocks = (candidate.children || []).some(
    (child) => child?.type === "element" && isRichComposerBlockTag(child.tag)
  );
  if (hasNestedBlocks) {
    return normalizedNodes;
  }
  return cloneRichComposerParsedNodes(candidate.children || []);
}

function buildRichComposerListItemChildrenWithFallback(children = [], { includeCaretMarker = false } = {}) {
  const normalizedChildren = cloneRichComposerParsedNodes(children);
  if (!areRichComposerParsedNodesStructurallyEmpty(normalizedChildren, { ignoreCaretMarker: true })) {
    return normalizedChildren;
  }
  const fallbackChildren = [];
  if (includeCaretMarker) {
    fallbackChildren.push(buildRichComposerCaretMarkerNode());
  }
  fallbackChildren.push({
    type: "element",
    tag: "br",
    attrs: {},
    children: [],
  });
  return fallbackChildren;
}

function buildRichComposerPlainTextCarrierNode(children = [], { includeCaretMarker = false } = {}) {
  const normalizedChildren = cloneRichComposerParsedNodes(children);
  if (!areRichComposerParsedNodesStructurallyEmpty(normalizedChildren, { ignoreCaretMarker: true })) {
    return {
      type: "element",
      tag: "div",
      attrs: {},
      children: normalizedChildren,
    };
  }
  const emptyLineChildren = cloneRichComposerParsedNodes(buildRichComposerEmptyLineBlockNode().children || []);
  return {
    type: "element",
    tag: "div",
    attrs: {},
    children: includeCaretMarker
      ? [buildRichComposerCaretMarkerNode(), ...emptyLineChildren]
      : emptyLineChildren,
  };
}

function splitRichComposerParsedNodesAtCaretMarker(nodes = []) {
  const beforeNodes = [];
  const afterNodes = [];
  let foundMarker = false;

  (Array.isArray(nodes) ? nodes : []).forEach((node) => {
    if (!node) {
      return;
    }
    if (foundMarker) {
      afterNodes.push(cloneRichComposerParsedNode(node));
      return;
    }
    if (isRichComposerCaretMarkerNode(node)) {
      foundMarker = true;
      afterNodes.push(buildRichComposerCaretMarkerNode());
      return;
    }
    if (node.type === "element" && !isRichComposerBreakNode(node)) {
      const splitChildren = splitRichComposerParsedNodesAtCaretMarker(node.children || []);
      if (splitChildren.foundMarker) {
        foundMarker = true;
        if (!areRichComposerParsedNodesStructurallyEmpty(splitChildren.beforeNodes, { ignoreCaretMarker: false })) {
          beforeNodes.push(cloneRichComposerElementNodeWithChildren(node, splitChildren.beforeNodes));
        }
        if (splitChildren.afterNodes.length > 0 && isRichComposerCaretMarkerNode(splitChildren.afterNodes[0])) {
          afterNodes.push(buildRichComposerCaretMarkerNode());
          const trailingChildren = splitChildren.afterNodes.slice(1);
          if (!areRichComposerParsedNodesStructurallyEmpty(trailingChildren, { ignoreCaretMarker: false })) {
            afterNodes.push(cloneRichComposerElementNodeWithChildren(node, trailingChildren));
          }
        } else if (
          !areRichComposerParsedNodesStructurallyEmpty(splitChildren.afterNodes, { ignoreCaretMarker: false })
        ) {
          afterNodes.push(cloneRichComposerElementNodeWithChildren(node, splitChildren.afterNodes));
        }
        return;
      }
    }
    beforeNodes.push(cloneRichComposerParsedNode(node));
  });

  return {
    beforeNodes,
    afterNodes,
    foundMarker,
  };
}

function wrapRichComposerBlockHtmlInList(blockHtml) {
  const parsed = parseRichComposerHtmlFragment(String(blockHtml || ""));
  const blockChildren = unwrapRichComposerSingleBlockChildren(parsed.children || []);
  const listItemChildren = buildRichComposerListItemChildrenWithFallback(blockChildren, {
    includeCaretMarker: hasRichComposerCaretMarkerInParsedNodes(blockChildren),
  });
  return renderRichComposerHtmlNodes([
    {
      type: "element",
      tag: "ul",
      attrs: {},
      children: [
        {
          type: "element",
          tag: "li",
          attrs: {},
          children: listItemChildren,
        },
      ],
    },
  ]);
}

function splitRichComposerListItemHtmlAtCaret(listItemHtml) {
  const parsed = parseRichComposerHtmlFragment(String(listItemHtml || ""));
  const listItemNode =
    (parsed.children || []).find(
      (node) => node?.type === "element" && String(node.tag || "").toLowerCase() === "li"
    ) || null;
  if (!listItemNode) {
    return String(listItemHtml || "");
  }
  const splitChildren = splitRichComposerParsedNodesAtCaretMarker(listItemNode.children || []);
  if (!splitChildren.foundMarker) {
    return String(listItemHtml || "");
  }
  const beforeChildren = buildRichComposerListItemChildrenWithFallback(splitChildren.beforeNodes);
  const afterChildren = buildRichComposerListItemChildrenWithFallback(splitChildren.afterNodes, {
    includeCaretMarker: true,
  });
  return renderRichComposerHtmlNodes([
    {
      type: "element",
      tag: "li",
      attrs: { ...(listItemNode.attrs || {}) },
      children: beforeChildren,
    },
    {
      type: "element",
      tag: "li",
      attrs: { ...(listItemNode.attrs || {}) },
      children: afterChildren,
    },
  ]);
}

function exitRichComposerCurrentListItemHtml(listHtml) {
  const parsed = parseRichComposerHtmlFragment(String(listHtml || ""));
  const listNode =
    (parsed.children || []).find(
      (node) =>
        node?.type === "element" && ["ul", "ol"].includes(String(node.tag || "").toLowerCase())
    ) || null;
  if (!listNode) {
    return String(listHtml || "");
  }
  const listItems = (listNode.children || []).filter(
    (node) => node?.type === "element" && String(node.tag || "").toLowerCase() === "li"
  );
  const exitIndex = listItems.findIndex((item) => hasRichComposerCaretMarkerInParsedNodes(item.children || []));
  if (exitIndex < 0) {
    return String(listHtml || "");
  }
  const beforeItems = cloneRichComposerParsedNodes(listItems.slice(0, exitIndex));
  const afterItems = cloneRichComposerParsedNodes(listItems.slice(exitIndex + 1));
  const exitItem = listItems[exitIndex];
  const exitBlock = buildRichComposerPlainTextCarrierNode(exitItem.children || [], {
    includeCaretMarker: hasRichComposerCaretMarkerInParsedNodes(exitItem.children || []),
  });
  const renderedNodes = [];
  if (beforeItems.length > 0) {
    renderedNodes.push({
      type: "element",
      tag: String(listNode.tag || "").toLowerCase(),
      attrs: { ...(listNode.attrs || {}) },
      children: beforeItems,
    });
  }
  renderedNodes.push(exitBlock);
  if (afterItems.length > 0) {
    renderedNodes.push({
      type: "element",
      tag: String(listNode.tag || "").toLowerCase(),
      attrs: { ...(listNode.attrs || {}) },
      children: afterItems,
    });
  }
  return renderRichComposerHtmlNodes(renderedNodes);
}

function normalizeRichComposerHtmlString(value) {
  const normalized = String(value || "")
    .replace(/<(\/?)b>/gi, "<$1strong>")
    .replace(/<(\/?)i>/gi, "<$1em>")
    .replace(/<span[^>]*data-composer-caret-marker="true"[^>]*><\/span>/gi, "");
  const parsed = parseRichComposerHtmlFragment(normalized);
  return renderRichComposerHtmlNodes(normalizeRichComposerParsedNodes(parsed.children || []));
}

function escapeMarkdownLiteralText(value) {
  return String(value || "")
    .replace(/\\/g, "\\\\")
    .replace(/`/g, "\\`")
    .replace(/\*/g, "\\*")
    .replace(/_/g, "\\_")
    .replace(/\[/g, "\\[")
    .replace(/\]/g, "\\]");
}

function escapeMarkdownParagraphLineStarts(value) {
  return String(value || "")
    .split("\n")
    .map((line) =>
      line
        .replace(/^(-\s+)/, "\\$1")
        .replace(/^(\d+\.\s+)/, "\\$1")
        .replace(/^(```)/, "\\$1")
    )
    .join("\n");
}

function serializeRichComposerPlainTextNodes(nodes = []) {
  return nodes
    .map((node) => {
      if (!node) {
        return "";
      }
      if (node.type === "text") {
        return stripComposerZeroWidthSpaces(node.value);
      }
      if (node.type === "element" && node.tag === "br") {
        return "\n";
      }
      return serializeRichComposerPlainTextNodes(node.children || []);
    })
    .join("");
}

function wrapSerializedInlineMarkdown(marker, inner) {
  if (!marker || !inner) {
    return inner || "";
  }
  const leadingWhitespaceMatch = inner.match(/^\s+/);
  const trailingWhitespaceMatch = inner.match(/\s+$/);
  const leadingWhitespace = leadingWhitespaceMatch ? leadingWhitespaceMatch[0] : "";
  const trailingWhitespace = trailingWhitespaceMatch ? trailingWhitespaceMatch[0] : "";
  const core = inner.slice(leadingWhitespace.length, inner.length - trailingWhitespace.length);
  if (!core) {
    return inner;
  }
  return `${leadingWhitespace}${marker}${core}${marker}${trailingWhitespace}`;
}

function serializeRichComposerInlineNodes(nodes = []) {
  return nodes
    .map((node) => {
      if (!node) {
        return "";
      }
      if (node.type === "text") {
        return escapeMarkdownLiteralText(stripComposerZeroWidthSpaces(node.value));
      }
      if (node.type !== "element") {
        return "";
      }
      switch (node.tag) {
        case "br":
          return "\n";
        case "strong": {
          const inner = serializeRichComposerInlineNodes(node.children || []);
          return wrapSerializedInlineMarkdown("**", inner);
        }
        case "em": {
          const inner = serializeRichComposerInlineNodes(node.children || []);
          return wrapSerializedInlineMarkdown("*", inner);
        }
        case "a": {
          const href = sanitizeUrl(node.attrs?.href);
          const label = serializeRichComposerInlineNodes(node.children || []);
          return href && label ? `[${label}](${href})` : label;
        }
        case "code": {
          const codeText = serializeRichComposerPlainTextNodes(node.children || []);
          return codeText ? `\`${codeText}\`` : "";
        }
        default:
          return serializeRichComposerInlineNodes(node.children || []);
      }
    })
    .join("");
}

function serializeRichComposerBlockNode(node) {
  if (!node) {
    return "";
  }
  if (node.type === "text") {
    return escapeMarkdownParagraphLineStarts(
      escapeMarkdownLiteralText(stripComposerZeroWidthSpaces(node.value))
    ).trim();
  }
  if (node.type !== "element") {
    return "";
  }

  switch (node.tag) {
    case "ul":
      return node.children
        .filter((child) => child?.type === "element" && child.tag === "li")
        .map((child) => serializeRichComposerInlineNodes(child.children || []).trim())
        .filter(Boolean)
        .map((item) => `- ${item}`)
        .join("\n")
        .trim();
    case "ol":
      return node.children
        .filter((child) => child?.type === "element" && child.tag === "li")
        .map((child) => serializeRichComposerInlineNodes(child.children || []).trim())
        .filter(Boolean)
        .map((item, index) => `${index + 1}. ${item}`)
        .join("\n")
        .trim();
    case "pre": {
      const codeNode =
        (node.children || []).find((child) => child?.type === "element" && child.tag === "code") ||
        node;
      const className = String(codeNode.attrs?.class || "");
      const languageMatch = className.match(/language-([A-Za-z0-9_+-]+)/i);
      const language = languageMatch ? String(languageMatch[1] || "").trim().toLowerCase() : "";
      const codeText = stripComposerZeroWidthSpaces(
        serializeRichComposerPlainTextNodes(codeNode.children || [])
      ).replace(/\n$/, "");
      if (!codeText) {
        return "";
      }
      return `\`\`\`${language}\n${codeText}\n\`\`\``.trim();
    }
    case "p":
    case "div": {
      const hasNestedBlocks = (node.children || []).some(
        (child) => child?.type === "element" && isRichComposerBlockTag(child.tag)
      );
      if (hasNestedBlocks) {
        return serializeRichComposerRootNodes(node.children || []);
      }
      return escapeMarkdownParagraphLineStarts(
        serializeRichComposerInlineNodes(node.children || [])
      ).trim();
    }
    default:
      return escapeMarkdownParagraphLineStarts(
        serializeRichComposerInlineNodes(node.children || [])
      ).trim();
  }
}

function serializeRichComposerRootNodes(nodes = []) {
  const parts = [];
  let inlineBuffer = [];

  const flushInlineBuffer = () => {
    if (inlineBuffer.length === 0) {
      return;
    }
    const serialized = escapeMarkdownParagraphLineStarts(
      serializeRichComposerInlineNodes(inlineBuffer)
    )
      .replace(/\n{3,}/g, "\n\n")
      .trim();
    if (serialized) {
      parts.push(serialized);
    }
    inlineBuffer = [];
  };

  nodes.forEach((node) => {
    if (node?.type === "element" && isRichComposerBlockTag(node.tag)) {
      flushInlineBuffer();
      const block = serializeRichComposerBlockNode(node);
      if (block) {
        parts.push(block);
      }
      return;
    }
    inlineBuffer.push(node);
  });

  flushInlineBuffer();
  return parts.join("\n\n").replace(/\n{3,}/g, "\n\n").trim();
}

function legacySerializeRichComposerHtmlToMarkdown(value) {
  const normalizedHtml = normalizeRichComposerHtmlString(value);
  if (!normalizedHtml) {
    return "";
  }
  const parsed = parseRichComposerHtmlFragment(normalizedHtml);
  return serializeRichComposerRootNodes(parsed.children || []);
}

function buildRichComposerHtmlFromMarkdown(value) {
  const markdown = String(value || "").trim();
  if (!markdown) {
    return "";
  }
  return ensureRichComposerEditableLinesAroundCodeBlocksHtml(
    String(renderMarkdownMessage(markdown) || "")
      .replace(/ target="_blank"/g, "")
      .replace(/ rel="noopener noreferrer"/g, "")
  );
}

function unwrapRichComposerListHtml(value) {
  const normalizedHtml = normalizeRichComposerHtmlString(value);
  if (!normalizedHtml) {
    return "";
  }
  const parsed = parseRichComposerHtmlFragment(normalizedHtml);
  const unwrappedNodes = [];
  normalizeRichComposerParsedNodes(parsed.children || []).forEach((node) => {
    const normalizedTag = String(node?.tag || "").toLowerCase();
    if (node?.type === "element" && ["ul", "ol"].includes(normalizedTag)) {
      const items = (node.children || []).filter(
        (child) => child?.type === "element" && String(child.tag || "").toLowerCase() === "li"
      );
      items.forEach((item, index) => {
        if (index > 0) {
          unwrappedNodes.push({
            type: "element",
            tag: "br",
            attrs: {},
            children: [],
          });
        }
        unwrappedNodes.push(...(item.children || []));
      });
      return;
    }
    unwrappedNodes.push(node);
  });
  return normalizeRichComposerHtmlString(renderRichComposerHtmlNodes(unwrappedNodes));
}

function unwrapRichComposerInlineTagNodes(nodes = [], tagName) {
  const normalizedTagName = String(tagName || "").trim().toLowerCase();
  const unwrappedNodes = [];
  (Array.isArray(nodes) ? nodes : []).forEach((node) => {
    if (!node) {
      return;
    }
    if (node.type !== "element") {
      unwrappedNodes.push(node);
      return;
    }
    const normalizedNode = {
      ...node,
      children: unwrapRichComposerInlineTagNodes(node.children || [], normalizedTagName),
    };
    if (String(normalizedNode.tag || "").toLowerCase() === normalizedTagName) {
      unwrappedNodes.push(...(normalizedNode.children || []));
      return;
    }
    unwrappedNodes.push(normalizedNode);
  });
  return unwrappedNodes;
}

function unwrapRichComposerInlineTagHtml(value, tagName) {
  const normalizedHtml = normalizeRichComposerHtmlString(value);
  const normalizedTagName = String(tagName || "").trim().toLowerCase();
  if (!normalizedHtml || !normalizedTagName) {
    return normalizedHtml;
  }
  const parsed = parseRichComposerHtmlFragment(normalizedHtml);
  const normalizedNodes = normalizeRichComposerParsedNodes(parsed.children || []);
  const unwrappedNodes = unwrapRichComposerInlineTagNodes(normalizedNodes, normalizedTagName);
  return normalizeRichComposerHtmlString(renderRichComposerHtmlNodes(unwrappedNodes));
}

function buildRichComposerPlainTextBreakNodes(value) {
  const nodes = [];
  String(value || "")
    .split("\n")
    .forEach((line, index) => {
      if (index > 0) {
        nodes.push({
          type: "element",
          tag: "br",
          attrs: {},
          children: [],
        });
      }
      if (line) {
        nodes.push({
          type: "text",
          value: line,
        });
      }
    });
  return nodes;
}

function isRichComposerEmptyLineInlineMarkerNode(node) {
  return (
    node?.type === "element" &&
    String(node.tag || "").toLowerCase() === "span" &&
    String(node.attrs?.["data-composer-empty-line"] || "").toLowerCase() === "true"
  );
}

function isRichComposerEmptyLineBlockNode(node) {
  if (
    node?.type !== "element" ||
    !["p", "div"].includes(String(node.tag || "").toLowerCase())
  ) {
    return false;
  }
  const children = node.children || [];
  return (
    children.length > 0 &&
    children.every(
      (child) =>
        isRichComposerWhitespaceTextNode(child) || isRichComposerEmptyLineInlineMarkerNode(child)
    )
  );
}

function buildRichComposerEmptyLineBlockNode() {
  return {
    type: "element",
    tag: "div",
    attrs: {},
    children: [
      {
        type: "element",
        tag: "span",
        attrs: {
          "data-composer-empty-line": "true",
        },
        children: [
          {
            type: "text",
            value: "\u200B",
          },
        ],
      },
    ],
  };
}

function isRichComposerPlainTextCarrierNode(node) {
  if (!node) {
    return false;
  }
  if (node.type === "text") {
    return stripComposerZeroWidthSpaces(String(node.value || "")).trim().length > 0;
  }
  if (node.type !== "element") {
    return false;
  }
  if (isRichComposerEmptyLineBlockNode(node)) {
    return true;
  }
  const normalizedTag = String(node.tag || "").toLowerCase();
  if (["pre", "ul", "ol"].includes(normalizedTag)) {
    return false;
  }
  if (["p", "div"].includes(normalizedTag)) {
    return !(node.children || []).some(
      (child) => child?.type === "element" && isRichComposerBlockTag(child.tag)
    );
  }
  return stripComposerZeroWidthSpaces(serializeRichComposerPlainTextNodes([node])).trim().length > 0;
}

function ensureRichComposerEditableLinesAroundCodeBlocksHtml(value) {
  const normalizedHtml = normalizeRichComposerHtmlString(value);
  if (!normalizedHtml) {
    return "";
  }
  const parsed = parseRichComposerHtmlFragment(normalizedHtml);
  const normalizedNodes = normalizeRichComposerParsedNodes(parsed.children || []);
  const ensuredNodes = [];

  normalizedNodes.forEach((node, index) => {
    const normalizedTag = String(node?.tag || "").toLowerCase();
    if (node?.type === "element" && normalizedTag === "pre") {
      const previousNode = ensuredNodes[ensuredNodes.length - 1] || null;
      if (!isRichComposerPlainTextCarrierNode(previousNode)) {
        ensuredNodes.push(buildRichComposerEmptyLineBlockNode());
      }
      ensuredNodes.push(node);
      const nextNode = normalizedNodes[index + 1] || null;
      if (!isRichComposerPlainTextCarrierNode(nextNode)) {
        ensuredNodes.push(buildRichComposerEmptyLineBlockNode());
      }
      return;
    }
    ensuredNodes.push(node);
  });

  return normalizeRichComposerHtmlString(renderRichComposerHtmlNodes(ensuredNodes));
}

function unwrapRichComposerCodeBlockHtml(value) {
  const normalizedHtml = normalizeRichComposerHtmlString(value);
  if (!normalizedHtml) {
    return "";
  }
  const parsed = parseRichComposerHtmlFragment(normalizedHtml);
  const unwrappedNodes = [];
  normalizeRichComposerParsedNodes(parsed.children || []).forEach((node) => {
    const normalizedTag = String(node?.tag || "").toLowerCase();
    if (node?.type === "element" && normalizedTag === "pre") {
      const codeNode =
        (node.children || []).find(
          (child) => child?.type === "element" && String(child.tag || "").toLowerCase() === "code"
        ) || node;
      const codeText = stripComposerZeroWidthSpaces(
        serializeRichComposerPlainTextNodes(codeNode.children || [])
      ).replace(/\n$/, "");
      unwrappedNodes.push(...buildRichComposerPlainTextBreakNodes(codeText));
      return;
    }
    unwrappedNodes.push(node);
  });
  return normalizeRichComposerHtmlString(renderRichComposerHtmlNodes(unwrappedNodes));
}

function setComposerDraftFromMarkdown(value) {
  state.inputDraft = String(value || "");
  state.inputDraftRichHtml = buildRichComposerHtmlFromMarkdown(state.inputDraft);
  state.composerToolbarState = buildDefaultComposerToolbarState();
}

function setComposerDraftFromRichHtml(value) {
  const normalizedHtml = ensureRichComposerEditableLinesAroundCodeBlocksHtml(value);
  state.inputDraftRichHtml = normalizedHtml;
  state.inputDraft = serializeRichComposerHtmlToMarkdown(normalizedHtml);
}

function ensureComposerDraftRichHtml() {
  if (!state.inputDraftRichHtml && state.inputDraft) {
    state.inputDraftRichHtml = buildRichComposerHtmlFromMarkdown(state.inputDraft);
  }
  return state.inputDraftRichHtml || "";
}

function isTextComposerElement(element) {
  return Boolean(
    element &&
      typeof element === "object" &&
      typeof element.focus === "function" &&
      typeof element.value === "string"
  );
}

function isRichTextComposerElement(element) {
  return Boolean(
    element &&
      typeof element === "object" &&
      typeof element.focus === "function" &&
      typeof element.innerHTML === "string" &&
      typeof element.getAttribute === "function"
  );
}

function isComposerElementDisabled(element) {
  if (!element) {
    return true;
  }
  if (typeof element.disabled === "boolean") {
    return element.disabled;
  }
  return String(element.getAttribute?.("contenteditable") || "").toLowerCase() === "false";
}

function getComposerSelectionObject() {
  if (window?.getSelection) {
    return window.getSelection();
  }
  if (document?.getSelection) {
    return document.getSelection();
  }
  return null;
}

function getComposerNodePath(root, node) {
  const path = [];
  let current = node;
  while (current && current !== root) {
    const parent = current.parentNode;
    if (!parent) {
      return null;
    }
    const index = Array.from(parent.childNodes || []).indexOf(current);
    if (index < 0) {
      return null;
    }
    path.unshift(index);
    current = parent;
  }
  return current === root ? path : null;
}

function resolveComposerNodePath(root, path = []) {
  let current = root;
  for (const segment of path) {
    if (!current?.childNodes || !current.childNodes[segment]) {
      return null;
    }
    current = current.childNodes[segment];
  }
  return current;
}

function clampComposerNodeOffset(node, offset) {
  const normalizedOffset = Number.isFinite(offset) ? Number(offset) : 0;
  if (!node) {
    return 0;
  }
  if (node.nodeType === 3) {
    return Math.max(0, Math.min(String(node.textContent || "").length, normalizedOffset));
  }
  return Math.max(0, Math.min(node.childNodes?.length || 0, normalizedOffset));
}

function captureRichComposerSelectionBookmark(element) {
  const selection = getComposerSelectionObject();
  if (!isRichTextComposerElement(element) || !selection || selection.rangeCount === 0) {
    return null;
  }
  const range = selection.getRangeAt(0);
  if (!element.contains(range.startContainer) || !element.contains(range.endContainer)) {
    return null;
  }
  return {
    startPath: getComposerNodePath(element, range.startContainer),
    startOffset: range.startOffset,
    endPath: getComposerNodePath(element, range.endContainer),
    endOffset: range.endOffset,
  };
}

function restoreRichComposerSelectionBookmark(element, bookmark) {
  if (!isRichTextComposerElement(element) || !bookmark || !document?.createRange) {
    return false;
  }
  const selection = getComposerSelectionObject();
  if (!selection) {
    return false;
  }
  const startNode = resolveComposerNodePath(element, bookmark.startPath);
  const endNode = resolveComposerNodePath(element, bookmark.endPath);
  if (!startNode || !endNode) {
    return false;
  }
  const range = document.createRange();
  range.setStart(startNode, clampComposerNodeOffset(startNode, bookmark.startOffset));
  range.setEnd(endNode, clampComposerNodeOffset(endNode, bookmark.endOffset));
  selection.removeAllRanges();
  selection.addRange(range);
  return true;
}

function captureComposerPreservationState(element) {
  if (!element || isComposerElementDisabled(element)) {
    return null;
  }
  if (isTextComposerElement(element)) {
    if (document.activeElement !== element) {
      return null;
    }
    return {
      kind: "text",
      selectionStart:
        typeof element.selectionStart === "number" ? element.selectionStart : element.value.length,
      selectionEnd:
        typeof element.selectionEnd === "number" ? element.selectionEnd : element.value.length,
      selectionDirection:
        typeof element.selectionDirection === "string" ? element.selectionDirection : "none",
      scrollTop: typeof element.scrollTop === "number" ? element.scrollTop : 0,
    };
  }
  if (!isRichTextComposerElement(element)) {
    return null;
  }
  const activeInside =
    document.activeElement === element ||
    (typeof element.contains === "function" && element.contains(document.activeElement));
  if (!activeInside) {
    return null;
  }
  return {
    kind: "rich",
    selectionBookmark: captureRichComposerSelectionBookmark(element),
    scrollTop: typeof element.scrollTop === "number" ? element.scrollTop : 0,
  };
}

function restoreComposerPreservationState(element, snapshot) {
  if (!element || !snapshot || isComposerElementDisabled(element)) {
    return;
  }
  try {
    element.focus({ preventScroll: true });
  } catch {
    element.focus();
  }
  if (snapshot.kind === "text" && isTextComposerElement(element)) {
    if (typeof element.setSelectionRange === "function") {
      element.setSelectionRange(
        snapshot.selectionStart,
        snapshot.selectionEnd,
        snapshot.selectionDirection || "none"
      );
    }
  }
  if (snapshot.kind === "rich" && isRichTextComposerElement(element)) {
    restoreRichComposerSelectionBookmark(element, snapshot.selectionBookmark);
  }
  if (typeof snapshot.scrollTop === "number" && typeof element.scrollTop === "number") {
    element.scrollTop = snapshot.scrollTop;
  }
}

function getActiveChatComposerElement() {
  const input = document.getElementById("chat-input");
  return isTextComposerElement(input) || isRichTextComposerElement(input) ? input : null;
}

function findNearestComposerAncestor(node, tagName, root) {
  let current = node;
  const normalizedTagName = String(tagName || "").toLowerCase();
  while (current && current !== root) {
    if (current.nodeType === 1 && String(current.tagName || "").toLowerCase() === normalizedTagName) {
      return current;
    }
    current = current.parentNode;
  }
  return null;
}

function setComposerSelectionRange(range) {
  const selection = getComposerSelectionObject();
  if (!selection || !range) {
    return false;
  }
  selection.removeAllRanges();
  selection.addRange(range);
  return true;
}

function selectComposerNodeContents(node) {
  if (!node || !document?.createRange) {
    return false;
  }
  const range = document.createRange();
  range.selectNodeContents(node);
  return setComposerSelectionRange(range);
}

function selectComposerNodes(nodes = []) {
  const normalizedNodes = (Array.isArray(nodes) ? nodes : []).filter(Boolean);
  if (normalizedNodes.length === 0 || !document?.createRange) {
    return false;
  }
  if (normalizedNodes.length === 1) {
    return selectComposerNodeContents(normalizedNodes[0]);
  }
  const range = document.createRange();
  range.setStartBefore(normalizedNodes[0]);
  range.setEndAfter(normalizedNodes[normalizedNodes.length - 1]);
  return setComposerSelectionRange(range);
}

function placeComposerCaretAfterNode(node) {
  if (!node || !document?.createRange) {
    return false;
  }
  const range = document.createRange();
  range.setStartAfter(node);
  range.collapse(true);
  return setComposerSelectionRange(range);
}

function placeComposerCaretInsideNode(node, offset = 0) {
  if (!node || !document?.createRange) {
    return false;
  }
  const range = document.createRange();
  range.setStart(node, clampComposerNodeOffset(node, offset));
  range.collapse(true);
  return setComposerSelectionRange(range);
}

function placeComposerCaretAtEnd(node) {
  if (!node) {
    return false;
  }
  let current = node;
  while (current?.nodeType === 1 && current.childNodes?.length) {
    current = current.lastChild;
  }
  if (!current) {
    return false;
  }
  if (current.nodeType === 3) {
    return placeComposerCaretInsideNode(current, String(current.textContent || "").length);
  }
  if (current.nodeType === 1 && String(current.tagName || "").toLowerCase() === "br") {
    return placeComposerCaretAfterNode(current);
  }
  return placeComposerCaretInsideNode(current, current.childNodes?.length || 0);
}

function placeComposerCaretAtStart(node) {
  if (!node) {
    return false;
  }
  let current = node;
  while (current?.nodeType === 1 && current.childNodes?.length) {
    current = current.firstChild;
  }
  if (!current) {
    return false;
  }
  if (current.nodeType === 3) {
    return placeComposerCaretInsideNode(current, 0);
  }
  if (current.nodeType === 1 && String(current.tagName || "").toLowerCase() === "br") {
    return placeComposerCaretAfterNode(current);
  }
  return placeComposerCaretInsideNode(current, 0);
}

function getComposerSelectionRange(element) {
  const selection = getComposerSelectionObject();
  if (!isRichTextComposerElement(element) || !selection || selection.rangeCount === 0) {
    return null;
  }
  const range = selection.getRangeAt(0);
  if (!element.contains(range.startContainer) || !element.contains(range.endContainer)) {
    return null;
  }
  return range;
}

function getComposerRangeContextNode(range, root) {
  if (!range) {
    return null;
  }
  if (!range.collapsed) {
    return range.commonAncestorContainer || range.startContainer;
  }
  const startContainer = range.startContainer || null;
  if (!startContainer || startContainer.nodeType !== 1) {
    return startContainer;
  }
  const childNodes = Array.from(startContainer.childNodes || []);
  if (childNodes.length === 0) {
    return startContainer;
  }
  const previousChild =
    range.startOffset > 0 && range.startOffset - 1 < childNodes.length
      ? childNodes[range.startOffset - 1]
      : null;
  const nextChild =
    range.startOffset >= 0 && range.startOffset < childNodes.length
      ? childNodes[range.startOffset]
      : null;
  const candidate = previousChild || nextChild || startContainer;
  if (!root || candidate === root) {
    return candidate;
  }
  return typeof root.contains === "function" && root.contains(candidate) ? candidate : startContainer;
}

function getComposerCollapsedRangeAdjacentNode(range) {
  if (!range?.collapsed) {
    return null;
  }
  const startContainer = range.startContainer || null;
  if (!startContainer || startContainer.nodeType !== 1) {
    return null;
  }
  const childNodes = Array.from(startContainer.childNodes || []);
  if (childNodes.length === 0) {
    return null;
  }
  if (range.startOffset > 0 && range.startOffset - 1 < childNodes.length) {
    return {
      node: childNodes[range.startOffset - 1],
      affinity: "after",
    };
  }
  if (range.startOffset >= 0 && range.startOffset < childNodes.length) {
    return {
      node: childNodes[range.startOffset],
      affinity: "before",
    };
  }
  return null;
}

function getComposerCollapsedListContext(range, root) {
  if (!range?.collapsed) {
    return {
      listNode: null,
      listItem: null,
    };
  }
  const startContainer = range.startContainer || null;
  const directListItem =
    (startContainer?.nodeType === 1 && String(startContainer.tagName || "").toLowerCase() === "li"
      ? startContainer
      : null) || findNearestComposerAncestor(startContainer, "li", root);
  const directListNode =
    (startContainer?.nodeType === 1 &&
    ["ul", "ol"].includes(String(startContainer.tagName || "").toLowerCase())
      ? startContainer
      : null) ||
    findNearestComposerAncestor(startContainer, "ul", root) ||
    findNearestComposerAncestor(startContainer, "ol", root);
  if (directListItem) {
    return {
      listNode:
        directListNode ||
        findNearestComposerAncestor(directListItem, "ul", root) ||
        findNearestComposerAncestor(directListItem, "ol", root),
      listItem: directListItem,
    };
  }
  const adjacent = getComposerCollapsedRangeAdjacentNode(range);
  if (!adjacent?.node) {
    return {
      listNode: directListNode || null,
      listItem: null,
    };
  }
  const adjacentNode = adjacent.node;
  const adjacentListNode =
    (adjacentNode.nodeType === 1 &&
    ["ul", "ol"].includes(String(adjacentNode.tagName || "").toLowerCase())
      ? adjacentNode
      : null) ||
    findNearestComposerAncestor(adjacentNode, "ul", root) ||
    findNearestComposerAncestor(adjacentNode, "ol", root);
  if (!adjacentListNode) {
    return {
      listNode: directListNode || null,
      listItem: null,
    };
  }
  const adjacentListItem =
    (adjacentNode.nodeType === 1 && String(adjacentNode.tagName || "").toLowerCase() === "li"
      ? adjacentNode
      : null) || findNearestComposerAncestor(adjacentNode, "li", root);
  if (adjacentListItem) {
    return {
      listNode: adjacentListNode,
      listItem: adjacentListItem,
    };
  }
  const listItems = Array.from(adjacentListNode.childNodes || []).filter(
    (child) => child?.nodeType === 1 && String(child.tagName || "").toLowerCase() === "li"
  );
  if (listItems.length === 0) {
    return {
      listNode: adjacentListNode,
      listItem: null,
    };
  }
  return {
    listNode: adjacentListNode,
    listItem: adjacent.affinity === "after" ? listItems[listItems.length - 1] : listItems[0],
  };
}

function getComposerRangeSelectedSingleNode(range, root) {
  if (
    !range ||
    range.collapsed ||
    range.startContainer !== range.endContainer ||
    range.startContainer?.nodeType !== 1
  ) {
    return null;
  }
  if (range.endOffset - range.startOffset !== 1) {
    return null;
  }
  const candidate = range.startContainer.childNodes?.[range.startOffset] || null;
  if (!candidate || !root) {
    return null;
  }
  if (candidate === root) {
    return candidate;
  }
  return typeof root.contains === "function" && root.contains(candidate) ? candidate : null;
}

function doesComposerRangeCoverNodeContents(range, node) {
  if (!range || !node || !document?.createRange || typeof range.compareBoundaryPoints !== "function") {
    return false;
  }
  const nodeRange = document.createRange();
  nodeRange.selectNodeContents(node);
  const startToStart = typeof Range === "function" ? Range.START_TO_START : 0;
  const endToEnd = typeof Range === "function" ? Range.END_TO_END : 2;
  return (
    range.compareBoundaryPoints(startToStart, nodeRange) === 0 &&
    range.compareBoundaryPoints(endToEnd, nodeRange) === 0
  );
}

function findComposerFullySelectedInlineFormatNode(range, tagName, root) {
  const normalizedTagName = String(tagName || "").trim().toLowerCase();
  const selectedNode = getComposerRangeSelectedSingleNode(range, root);
  if (
    selectedNode?.nodeType === 1 &&
    String(selectedNode.tagName || "").toLowerCase() === normalizedTagName
  ) {
    return selectedNode;
  }
  const startAncestor = findNearestComposerAncestor(range.startContainer, normalizedTagName, root);
  const endAncestor = findNearestComposerAncestor(range.endContainer, normalizedTagName, root);
  if (
    startAncestor &&
    startAncestor === endAncestor &&
    doesComposerRangeCoverNodeContents(range, startAncestor)
  ) {
    return startAncestor;
  }
  return null;
}

function findComposerFullySelectedCodeBlockNode(range, root) {
  const selectedNode = getComposerRangeSelectedSingleNode(range, root);
  if (selectedNode?.nodeType === 1) {
    const selectedTagName = String(selectedNode.tagName || "").toLowerCase();
    if (selectedTagName === "pre") {
      return selectedNode;
    }
    if (selectedTagName === "code") {
      return findNearestComposerAncestor(selectedNode, "pre", root);
    }
  }
  const selectedCodeNode = findComposerFullySelectedInlineFormatNode(range, "code", root);
  return selectedCodeNode ? findNearestComposerAncestor(selectedCodeNode, "pre", root) : null;
}

function deriveRichComposerToolbarState(context = {}) {
  return {
    bold: Boolean(context.bold),
    italic: Boolean(context.italic),
    list: Boolean(context.list),
    codeBlock: Boolean(context.codeBlock),
  };
}

function getRichComposerSelectionContext(element) {
  const range = getComposerSelectionRange(element);
  if (!range) {
    return buildDefaultComposerToolbarState();
  }
  const contextNode = getComposerRangeContextNode(range, element);
  const collapsedListContext = range.collapsed
    ? getComposerCollapsedListContext(range, element)
    : null;
  return deriveRichComposerToolbarState({
    bold: Boolean(findNearestComposerAncestor(contextNode, "strong", element)),
    italic: Boolean(findNearestComposerAncestor(contextNode, "em", element)),
    list: Boolean(
      collapsedListContext?.listItem || findNearestComposerAncestor(contextNode, "li", element)
    ),
    codeBlock: Boolean(findNearestComposerAncestor(contextNode, "code", element)),
  });
}

function applyComposerToolbarStateToDom() {
  const toolbarState = state.composerToolbarState || buildDefaultComposerToolbarState();
  applySharedComposerToolbarStateToButtons(appRoot, toolbarState);
}

function syncComposerToolbarStateFromElement(element = getActiveChatComposerElement()) {
  if (!isRichTextComposerElement(element) || isComposerElementDisabled(element)) {
    state.composerToolbarState = buildDefaultComposerToolbarState();
    applyComposerToolbarStateToDom();
    return state.composerToolbarState;
  }
  state.composerToolbarState = getRichComposerSelectionContext(element);
  applyComposerToolbarStateToDom();
  return state.composerToolbarState;
}

function syncComposerDraftStateFromElement(element = getActiveChatComposerElement(), options = {}) {
  if (isRichTextComposerElement(element)) {
    const normalizedHtml = normalizeRichComposerHtmlString(element.innerHTML);
    const selectionBookmark =
      options?.selectionBookmark ||
      (normalizedHtml !== element.innerHTML ? captureRichComposerSelectionBookmark(element) : null);
    if (normalizedHtml !== element.innerHTML) {
      element.innerHTML = normalizedHtml;
    }
    state.inputDraftRichHtml = normalizedHtml;
    state.inputDraft = serializeRichComposerHtmlToMarkdown(normalizedHtml);
    refreshNewTicketInlineComposerAction();
    syncComposerToolbarStateFromElement(element);
    if (selectionBookmark) {
      restoreRichComposerSelectionBookmark(element, selectionBookmark);
    }
    return state.inputDraft;
  }
  if (isTextComposerElement(element)) {
    state.inputDraft = element.value;
    refreshNewTicketInlineComposerAction();
    return state.inputDraft;
  }
  return state.inputDraft;
}

function getClientComposerRuntime() {
  if (clientComposerRuntime || !SharedComposer.createRichComposerRuntime) {
    return clientComposerRuntime;
  }
  clientComposerRuntime = SharedComposer.createRichComposerRuntime({
    getToolbarRoot: () => appRoot,
    onAttach: openAttachmentFilePicker,
    syncState: syncComposerDraftStateFromElement,
  });
  return clientComposerRuntime;
}

function applyComposerInlineFormat(tagName, element) {
  const range = getComposerSelectionRange(element);
  if (!range) {
    return false;
  }
  if (range.collapsed) {
    const existing = findNearestComposerAncestor(range.startContainer, tagName, element);
    if (existing) {
      placeComposerCaretAfterNode(existing);
      syncComposerToolbarStateFromElement(element);
      return true;
    }
    const wrapper = document.createElement(tagName);
    const marker = document.createTextNode("\u200B");
    wrapper.appendChild(marker);
    range.insertNode(wrapper);
    placeComposerCaretInsideNode(marker, 1);
    syncComposerDraftStateFromElement(element);
    return true;
  }
  const toggleTarget = findComposerFullySelectedInlineFormatNode(range, tagName, element);
  if (toggleTarget) {
    const insertedNodes = replaceComposerNodeWithHtml(
      toggleTarget,
      unwrapRichComposerInlineTagHtml(toggleTarget.outerHTML || "", tagName)
    );
    const lastInsertedNode = insertedNodes[insertedNodes.length - 1] || null;
    if (!placeComposerCaretAtEnd(lastInsertedNode) && !selectComposerNodes(insertedNodes)) {
      placeComposerCaretInsideNode(element, element.childNodes?.length || 0);
    }
    const selectionBookmark = captureRichComposerSelectionBookmark(element);
    syncComposerDraftStateFromElement(element, { selectionBookmark });
    return true;
  }
  const wrapper = document.createElement(tagName);
  wrapper.appendChild(range.extractContents());
  range.insertNode(wrapper);
  placeComposerCaretAtEnd(wrapper);
  const selectionBookmark = captureRichComposerSelectionBookmark(element);
  syncComposerDraftStateFromElement(element, { selectionBookmark });
  return true;
}

function isRichComposerDomNodeStructurallyEmpty(node) {
  if (!node) {
    return true;
  }
  if (node.nodeType === 3) {
    return stripComposerZeroWidthSpaces(String(node.textContent || "")).trim().length === 0;
  }
  if (node.nodeType !== 1) {
    return true;
  }
  if (String(node.tagName || "").toLowerCase() === "br") {
    return true;
  }
  return Array.from(node.childNodes || []).every((child) => isRichComposerDomNodeStructurallyEmpty(child));
}

function findNearestEmptyComposerBlockAncestor(node, root) {
  let current = node;
  while (current && current !== root) {
    if (
      current.nodeType === 1 &&
      ["p", "div"].includes(String(current.tagName || "").toLowerCase()) &&
      isRichComposerDomNodeStructurallyEmpty(current)
    ) {
      return current;
    }
    current = current.parentNode;
  }
  return null;
}

function buildComposerEmptyLineBlock() {
  if (!document?.createElement || !document?.createTextNode) {
    return null;
  }
  const line = document.createElement("div");
  const marker = document.createElement("span");
  marker.setAttribute("data-composer-empty-line", "true");
  marker.appendChild(document.createTextNode("\u200B"));
  line.appendChild(marker);
  return line;
}

function isComposerEmptyLineInlineMarker(node) {
  return (
    node?.nodeType === 1 &&
    String(node.tagName || "").toLowerCase() === "span" &&
    String(node.getAttribute?.("data-composer-empty-line") || "").toLowerCase() === "true"
  );
}

function isComposerEmptyLineBlock(node) {
  if (
    node?.nodeType !== 1 ||
    !["p", "div"].includes(String(node.tagName || "").toLowerCase())
  ) {
    return false;
  }
  const children = Array.from(node.childNodes || []);
  return (
    children.length > 0 &&
    children.every(
      (child) => child?.nodeType === 3 || isComposerEmptyLineInlineMarker(child)
    ) &&
    stripComposerZeroWidthSpaces(String(node.textContent || "")).trim().length === 0
  );
}

function isComposerPlainTextCarrierNode(node) {
  if (!node) {
    return false;
  }
  if (node.nodeType === 3) {
    return stripComposerZeroWidthSpaces(String(node.textContent || "")).trim().length > 0;
  }
  if (node.nodeType !== 1) {
    return false;
  }
  if (isComposerEmptyLineBlock(node)) {
    return true;
  }
  const normalizedTag = String(node.tagName || "").toLowerCase();
  if (["pre", "ul", "ol"].includes(normalizedTag)) {
    return false;
  }
  if (["p", "div"].includes(normalizedTag)) {
    return !Array.from(node.childNodes || []).some(
      (child) => child?.nodeType === 1 && isRichComposerBlockTag(child.tagName)
    );
  }
  return stripComposerZeroWidthSpaces(String(node.textContent || "")).trim().length > 0;
}

function ensureComposerAdjacentTextLine(codeBlock, root, position = "after") {
  if (!codeBlock?.parentNode || !root) {
    return null;
  }
  const sibling = position === "before" ? codeBlock.previousSibling : codeBlock.nextSibling;
  if (isComposerPlainTextCarrierNode(sibling)) {
    return sibling;
  }
  const spacerLine = buildComposerEmptyLineBlock();
  if (!spacerLine) {
    return null;
  }
  codeBlock.parentNode.insertBefore(
    spacerLine,
    position === "before" ? codeBlock : codeBlock.nextSibling || null
  );
  return spacerLine;
}

function ensureComposerCaretInAdjacentTextLine(codeBlock, root, position = "after") {
  const line = ensureComposerAdjacentTextLine(codeBlock, root, position);
  if (!line) {
    return false;
  }
  if (position === "before") {
    return placeComposerCaretAtEnd(line);
  }
  if (isComposerEmptyLineBlock(line)) {
    const marker = line.querySelector?.('[data-composer-empty-line="true"]');
    const markerText = marker?.firstChild || null;
    if (markerText?.nodeType === 3) {
      return placeComposerCaretInsideNode(markerText, 1);
    }
  }
  return placeComposerCaretAtStart(line);
}

function removeComposerAdjacentCodeBlockSpacerLine(node) {
  if (isComposerEmptyLineBlock(node)) {
    node.remove();
  }
}

function replaceComposerNodeWithHtml(node, html) {
  if (!node?.parentNode || !document?.createElement || !document?.createDocumentFragment) {
    return [];
  }
  const container = document.createElement("div");
  container.innerHTML = String(html || "");
  const insertedNodes = Array.from(container.childNodes || []);
  const fragment = document.createDocumentFragment();
  insertedNodes.forEach((child) => fragment.appendChild(child));
  node.parentNode.insertBefore(fragment, node);
  node.parentNode.removeChild(node);
  return insertedNodes;
}

function replaceComposerElementContentsWithHtml(element, html) {
  if (!element || !document?.createElement || !document?.createDocumentFragment) {
    return [];
  }
  const container = document.createElement("div");
  container.innerHTML = String(html || "");
  const insertedNodes = Array.from(container.childNodes || []);
  const fragment = document.createDocumentFragment();
  insertedNodes.forEach((child) => fragment.appendChild(child));
  element.innerHTML = "";
  element.appendChild(fragment);
  return Array.from(element.childNodes || []);
}

function createComposerCaretMarkerElement() {
  if (!document?.createElement) {
    return null;
  }
  const marker = document.createElement("span");
  marker.setAttribute("data-composer-caret-marker", "true");
  return marker;
}

function isComposerCaretMarkerElement(node) {
  return (
    node?.nodeType === 1 &&
    String(node.tagName || "").toLowerCase() === "span" &&
    String(node.getAttribute?.("data-composer-caret-marker") || "").toLowerCase() === "true"
  );
}

function findComposerCaretMarkerInNode(node) {
  if (!node) {
    return null;
  }
  if (isComposerCaretMarkerElement(node)) {
    return node;
  }
  if (node.nodeType === 1 && typeof node.querySelector === "function") {
    return node.querySelector('[data-composer-caret-marker="true"]');
  }
  return null;
}

function findComposerCaretMarkerInNodes(nodes = []) {
  for (const node of Array.isArray(nodes) ? nodes : []) {
    const marker = findComposerCaretMarkerInNode(node);
    if (marker) {
      return marker;
    }
  }
  return null;
}

function restoreComposerCaretFromMarker(marker) {
  if (!marker) {
    return false;
  }
  const nextSibling = marker.nextSibling || null;
  const previousSibling = marker.previousSibling || null;
  let restored = false;
  if (nextSibling?.nodeType === 3) {
    restored = placeComposerCaretInsideNode(nextSibling, 0);
  } else if (nextSibling) {
    restored = placeComposerCaretAtStart(nextSibling);
  } else if (previousSibling?.nodeType === 3) {
    restored = placeComposerCaretInsideNode(previousSibling, String(previousSibling.textContent || "").length);
  } else if (previousSibling) {
    restored = placeComposerCaretAtEnd(previousSibling);
  } else if (marker.parentNode) {
    restored = placeComposerCaretInsideNode(marker.parentNode, 0);
  }
  marker.remove();
  return restored;
}

function findNearestComposerListConvertibleBlock(node, root) {
  let current = node;
  while (current && current !== root) {
    if (
      current.nodeType === 1 &&
      ["p", "div"].includes(String(current.tagName || "").toLowerCase()) &&
      !Array.from(current.childNodes || []).some(
        (child) => child?.nodeType === 1 && isRichComposerBlockTag(child.tagName)
      )
    ) {
      return current;
    }
    current = current.parentNode;
  }
  if (
    root?.nodeType === 1 &&
    !Array.from(root.childNodes || []).some(
      (child) => child?.nodeType === 1 && isRichComposerBlockTag(child.tagName)
    )
  ) {
    return root;
  }
  return null;
}

function applyComposerListFormat(element) {
  const range = getComposerSelectionRange(element);
  if (!range) {
    return false;
  }
  const contextNode = getComposerRangeContextNode(range, element);
  const collapsedListContext = range.collapsed
    ? getComposerCollapsedListContext(range, element)
    : null;
  const currentListItem = collapsedListContext?.listItem || null;
  const existingList =
    collapsedListContext?.listNode ||
    findNearestComposerAncestor(contextNode, "ul", element) ||
    findNearestComposerAncestor(contextNode, "ol", element);
  if (existingList && currentListItem) {
    const marker = createComposerCaretMarkerElement();
    if (!marker) {
      return false;
    }
    range.deleteContents();
    range.insertNode(marker);
    const exitHtml = exitRichComposerCurrentListItemHtml(existingList.outerHTML || "");
    const insertedNodes = replaceComposerNodeWithHtml(existingList, exitHtml);
    const restoredMarker =
      findComposerCaretMarkerInNodes(insertedNodes) || findComposerCaretMarkerInNode(element);
    if (!restoreComposerCaretFromMarker(restoredMarker)) {
      const exitBlock =
        insertedNodes.find(
          (node) =>
            node?.nodeType === 1 && ["p", "div"].includes(String(node.tagName || "").toLowerCase())
        ) || null;
      if (exitBlock) {
        placeComposerCaretAtEnd(exitBlock);
      }
    }
    const selectionBookmark = captureRichComposerSelectionBookmark(element);
    syncComposerDraftStateFromElement(element, { selectionBookmark });
    return true;
  }
  if (existingList) {
    const insertedNodes = replaceComposerNodeWithHtml(
      existingList,
      unwrapRichComposerListHtml(existingList.outerHTML || "")
    );
    if (!selectComposerNodes(insertedNodes)) {
      placeComposerCaretInsideNode(element, element.childNodes?.length || 0);
    }
    syncComposerDraftStateFromElement(element);
    return true;
  }
  const list = document.createElement("ul");
  if (range.collapsed) {
    const marker = createComposerCaretMarkerElement();
    if (!marker) {
      return false;
    }
    range.deleteContents();
    range.insertNode(marker);
    const convertibleBlock =
      findNearestComposerListConvertibleBlock(marker, element) ||
      findNearestEmptyComposerBlockAncestor(marker, element) ||
      element;
    const targetHtml =
      convertibleBlock === element
        ? element.innerHTML
        : String(convertibleBlock.outerHTML || "");
    const wrappedHtml = wrapRichComposerBlockHtmlInList(targetHtml);
    const insertedNodes =
      convertibleBlock === element
        ? replaceComposerElementContentsWithHtml(element, wrappedHtml)
        : replaceComposerNodeWithHtml(convertibleBlock, wrappedHtml);
    const restoredMarker =
      findComposerCaretMarkerInNodes(insertedNodes) || findComposerCaretMarkerInNode(element);
    if (!restoreComposerCaretFromMarker(restoredMarker)) {
      const firstItem = element.querySelector?.("li") || null;
      if (firstItem) {
        placeComposerCaretAtEnd(firstItem);
      }
    }
    syncComposerDraftStateFromElement(element);
    return true;
  }
  const selectedText = String(range.toString() || "");
  if (selectedText.includes("\n")) {
    selectedText.split("\n").forEach((line) => {
      const item = document.createElement("li");
      item.textContent = line;
      list.appendChild(item);
    });
  } else {
    const item = document.createElement("li");
    item.appendChild(range.extractContents());
    list.appendChild(item);
  }
  range.insertNode(list);
  const firstItem = list.querySelector?.("li") || list.firstChild;
  if (firstItem) {
    selectComposerNodeContents(firstItem);
  }
  syncComposerDraftStateFromElement(element);
  return true;
}

function handleRichComposerListDeletion(event, element) {
  const runtime = getClientComposerRuntime();
  if (runtime) {
    return runtime.handleListDeletion(event, element);
  }
  const key = String(event?.key || "");
  if (!["Backspace", "Delete"].includes(key)) {
    return false;
  }
  const range = getComposerSelectionRange(element);
  if (!range || !range.collapsed) {
    return false;
  }
  const listItem = findNearestComposerAncestor(range.startContainer, "li", element);
  if (!listItem || !isRichComposerDomNodeStructurallyEmpty(listItem)) {
    return false;
  }

  event.preventDefault();
  const list = listItem.parentNode;
  const previousItem = listItem.previousElementSibling;
  const nextItem = listItem.nextElementSibling;
  listItem.remove();

  if (!list || !list.querySelector?.("li")) {
    list?.remove();
    syncComposerDraftStateFromElement(element);
    placeComposerCaretInsideNode(element, element.childNodes?.length || 0);
    return true;
  }

  syncComposerDraftStateFromElement(element);
  if (previousItem) {
    placeComposerCaretAtEnd(previousItem);
    return true;
  }
  if (nextItem) {
    selectComposerNodeContents(nextItem);
    return true;
  }
  placeComposerCaretInsideNode(element, element.childNodes?.length || 0);
  return true;
}

function applyComposerCodeBlockFormat(element) {
  const range = getComposerSelectionRange(element);
  if (!range) {
    return false;
  }
  const fullySelectedCodeBlock = findComposerFullySelectedCodeBlockNode(range, element);
  const existingCodeBlock =
    findNearestComposerAncestor(range.startContainer, "pre", element) || fullySelectedCodeBlock;
  if (range.collapsed && existingCodeBlock) {
    removeComposerAdjacentCodeBlockSpacerLine(existingCodeBlock.previousSibling);
    removeComposerAdjacentCodeBlockSpacerLine(existingCodeBlock.nextSibling);
    const insertedNodes = replaceComposerNodeWithHtml(
      existingCodeBlock,
      unwrapRichComposerCodeBlockHtml(existingCodeBlock.outerHTML || "")
    );
    if (!selectComposerNodes(insertedNodes)) {
      placeComposerCaretInsideNode(element, element.childNodes?.length || 0);
    }
    syncComposerDraftStateFromElement(element);
    return true;
  }
  if (fullySelectedCodeBlock) {
    removeComposerAdjacentCodeBlockSpacerLine(fullySelectedCodeBlock.previousSibling);
    removeComposerAdjacentCodeBlockSpacerLine(fullySelectedCodeBlock.nextSibling);
    const insertedNodes = replaceComposerNodeWithHtml(
      fullySelectedCodeBlock,
      unwrapRichComposerCodeBlockHtml(existingCodeBlock.outerHTML || "")
    );
    if (!selectComposerNodes(insertedNodes)) {
      placeComposerCaretInsideNode(element, element.childNodes?.length || 0);
    }
    syncComposerDraftStateFromElement(element);
    return true;
  }
  const pre = document.createElement("pre");
  const code = document.createElement("code");
  if (range.collapsed) {
    const marker = document.createTextNode("\u200B");
    code.appendChild(marker);
    pre.appendChild(code);
    const emptyBlockAncestor = findNearestEmptyComposerBlockAncestor(range.startContainer, element);
    if (emptyBlockAncestor?.parentNode) {
      const beforeLine = buildComposerEmptyLineBlock();
      const afterLine = buildComposerEmptyLineBlock();
      const fragment = document.createDocumentFragment();
      if (beforeLine) {
        fragment.appendChild(beforeLine);
      }
      fragment.appendChild(pre);
      if (afterLine) {
        fragment.appendChild(afterLine);
      }
      emptyBlockAncestor.parentNode.insertBefore(fragment, emptyBlockAncestor);
      emptyBlockAncestor.remove();
    } else {
      range.insertNode(pre);
      ensureComposerAdjacentTextLine(pre, element, "before");
      ensureComposerAdjacentTextLine(pre, element, "after");
    }
    placeComposerCaretInsideNode(marker, 1);
  } else {
    code.textContent = String(range.toString() || "");
    pre.appendChild(code);
    range.deleteContents();
    range.insertNode(pre);
    ensureComposerAdjacentTextLine(pre, element, "before");
    ensureComposerAdjacentTextLine(pre, element, "after");
    selectComposerNodeContents(code);
  }
  syncComposerDraftStateFromElement(element);
  return true;
}

function handleComposerToolbarAction(action, element = getActiveChatComposerElement()) {
  const runtime = getClientComposerRuntime();
  if (runtime) {
    return runtime.handleToolbarAction(action, element);
  }
  const normalizedAction = String(action || "").trim();
  if (!normalizedAction) {
    return false;
  }
  if (normalizedAction === "attach") {
    openAttachmentFilePicker();
    return false;
  }
  if (!isRichTextComposerElement(element) || isComposerElementDisabled(element)) {
    return false;
  }
  switch (normalizedAction) {
    case "bold":
      return applyComposerInlineFormat("strong", element);
    case "italic":
      return applyComposerInlineFormat("em", element);
    case "list":
      return applyComposerListFormat(element);
    case "code-block":
      return applyComposerCodeBlockFormat(element);
    default:
      return false;
  }
}

function insertComposerLineBreak(element) {
  const range = getComposerSelectionRange(element);
  if (!range) {
    return false;
  }
  range.deleteContents();
  const lineBreak = document.createElement("br");
  range.insertNode(lineBreak);
  placeComposerCaretAfterNode(lineBreak);
  syncComposerDraftStateFromElement(element);
  return true;
}

function insertComposerPlainText(element, text, { preserveNewlines = true } = {}) {
  const runtime = getClientComposerRuntime();
  if (runtime) {
    return runtime.insertPlainText(element, text, { preserveNewlines });
  }
  const range = getComposerSelectionRange(element);
  if (!range || !document?.createTextNode) {
    return false;
  }
  range.deleteContents();
  const fragment = document.createDocumentFragment();
  const parts = String(text || "").split("\n");
  parts.forEach((part, index) => {
    fragment.appendChild(document.createTextNode(part));
    if (preserveNewlines && index < parts.length - 1) {
      fragment.appendChild(document.createElement("br"));
    }
  });
  const lastNode = fragment.lastChild;
  range.insertNode(fragment);
  if (lastNode) {
    placeComposerCaretAfterNode(lastNode);
  }
  syncComposerDraftStateFromElement(element);
  return true;
}

function handleRichComposerShiftEnter(element) {
  const runtime = getClientComposerRuntime();
  if (runtime) {
    return runtime.handleShiftEnter(element);
  }
  const range = getComposerSelectionRange(element);
  if (!range) {
    return false;
  }
  const listItem = findNearestComposerAncestor(range.startContainer, "li", element);
  if (listItem) {
    const marker = createComposerCaretMarkerElement();
    if (!marker) {
      return false;
    }
    range.deleteContents();
    range.insertNode(marker);
    const splitHtml = splitRichComposerListItemHtmlAtCaret(listItem.outerHTML || "");
    const insertedNodes = replaceComposerNodeWithHtml(listItem, splitHtml);
    const restoredMarker =
      findComposerCaretMarkerInNodes(insertedNodes) || findComposerCaretMarkerInNode(element);
    if (!restoreComposerCaretFromMarker(restoredMarker)) {
      const nextItem = insertedNodes[1] || insertedNodes[0] || null;
      if (nextItem) {
        placeComposerCaretAtStart(nextItem);
      }
    }
    syncComposerDraftStateFromElement(element);
    return true;
  }
  if (findNearestComposerAncestor(range.startContainer, "code", element)) {
    return insertComposerPlainText(element, "\n", { preserveNewlines: true });
  }
  return insertComposerLineBreak(element);
}

function buildChatTicketViewState(ticket) {
  if (!ticket || ticket.userId !== state.user.id) {
    return null;
  }
  clearStaleNewTicketPreviewTicketId(ticket);
  const renderableMessages = getRenderableMessages(ticket);
  const sending = isTicketAwaitingDurableReply(ticket);
  const usesNewTicketShell = usesNewTicketShellTicket(ticket);
  const hasComposerText = String(state.inputDraft || "").trim().length > 0;
  const requiresProductSelection = false;
  const canCompose = ticket.status !== "resolved";
  const canSubmit = canCompose && hasComposerText;
  const isEditing = Boolean(state.editingMessageId);
  const showVisibleFooterBand = isNewTicketPreviewTicket(ticket);

  if (isEditing && !renderableMessages.some((message) => message.id === state.editingMessageId)) {
    state.editingMessageId = null;
    if (!sending) {
      setComposerDraftFromMarkdown("");
    }
  }

  return {
    ticket,
    renderableMessages,
    sending,
    requiresProductSelection,
    canCompose,
    canSubmit,
    usesNewTicketShell,
    showVisibleFooterBand,
    isEditing: Boolean(state.editingMessageId),
  };
}

function renderChatMessagesHtml(viewState) {
  return `
    ${
      viewState.renderableMessages.length === 0
        ? `
          <div class="empty-chat">
            ${renderNewSessionHintCard()}
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
  `;
}

function renderChatComposerNoteHtml(viewState) {
  if (viewState?.usesNewTicketShell) {
    return renderNewTicketComposerNoteHtml(viewState);
  }
  if (viewState.isEditing) {
    return `<div class="composer-note">Editing your last message. Press Enter to resend, Shift+Enter for newline.</div>`;
  }
  return "";
}

function renderChatComposerActionHtml(viewState) {
  if (viewState?.usesNewTicketShell) {
    return renderNewTicketComposerActionHtml(viewState);
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

function renderChatTicketFromState(viewState) {
  if (viewState?.usesNewTicketShell) {
    return renderNewTicketTicketFromState(viewState);
  }
  clearReplyCountdownRefreshTimer();
  const ticket = viewState.ticket;
  const productLabel = getProductLabel(ticket.product);
  const actionButtons = renderTicketHeaderActions(ticket);
  return `
    <section class="chat-root ticket-detail-layout clienttest-route-page" data-chat-ticket-id="${escapeHtml(ticket.id)}">
      <div class="ticket-detail-main">
        <header class="ticket-detail-hero">
          <div class="ticket-detail-breadcrumb mono">My Tickets / ${escapeHtml(ticket.id)}</div>
          <div class="ticket-detail-header-row">
            <div class="ticket-detail-header-copy">
              <p class="ticket-detail-kicker">${CLIENT_ASSISTANT_NAME}</p>
              <h1 class="ticket-detail-title">${escapeHtml(ticket.title)}</h1>
              <div class="ticket-detail-meta">
                ${statusBadge(ticket.status)}
                ${productLabel ? `<span class="context-product-pill">${escapeHtml(productLabel)}</span>` : ""}
                <span class="ticket-detail-meta-item">Updated ${escapeHtml(formatDate(ticket.updatedAt))}</span>
              </div>
            </div>
            ${actionButtons ? `<div class="ticket-detail-header-actions">${actionButtons}</div>` : ""}
          </div>
        </header>
        <div class="ticket-detail-thread">
          <main class="chat-main">
            <div class="message-list" data-chat-section="messages">
              ${renderChatMessagesHtml(viewState)}
            </div>
          </main>
        </div>
        ${
          viewState.canCompose
            ? `
        <footer class="chat-input-wrap ticket-detail-composer">
          <div class="ticket-detail-composer-header">
            <div>
              <p class="ticket-detail-composer-label">Message Support</p>
              <p class="ticket-detail-composer-desc">
                Continue the same ticket with Sid handling the assistant turn and the existing client
                runtime behind the screen.
              </p>
            </div>
            <span class="ticket-detail-composer-state">Support thread</span>
          </div>
          <div class="ticket-detail-toolbar">
            <span class="ticket-toolbar-chip"><span class="material-symbols-outlined" aria-hidden="true">bolt</span>Source-aware</span>
            <span class="ticket-toolbar-chip"><span class="material-symbols-outlined" aria-hidden="true">history</span>Thread context</span>
            <span class="ticket-toolbar-chip"><span class="material-symbols-outlined" aria-hidden="true">forum</span>Customer chat</span>
          </div>
          <div class="new-ticket-composer-toolbar ticket-detail-composer-format-toolbar">
            ${renderNewTicketComposerToolbar({ canCompose: viewState.canCompose, includeSummary: false })}
          </div>
          <div data-chat-section="composer-note">${renderChatComposerNoteHtml(viewState)}</div>
          <form id="chat-input-form" class="chat-input-inner ticket-detail-composer-form" data-chat-section="composer-form">
            <div
              id="chat-input"
              class="textarea composer-rich-input"
              contenteditable="${viewState.canCompose ? "true" : "false"}"
              role="textbox"
              aria-multiline="true"
              spellcheck="true"
              data-chat-composer-rich="true"
              data-placeholder="Type your request or technical issue..."
            >${ensureComposerDraftRichHtml()}</div>
            <div data-chat-section="composer-action">
              ${renderChatComposerActionHtml(viewState)}
            </div>
          </form>
          <div data-chat-section="composer-attachments-region">${renderComposerAttachmentsHtml(ticket.id)}</div>
        </footer>`
            : ""
        }
      </div>
      <aside class="ticket-detail-sidebar">
        ${renderTicketInformationPanel(ticket)}
        ${renderTicketSummaryPanel(ticket)}
        ${renderRelatedKnowledgePanel(ticket)}
      </aside>
    </section>
  `;
}

function shouldPreserveActiveChatComposerOnRender(viewState) {
  if (viewState?.usesNewTicketShell) {
    return false;
  }
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
  const toolbarRegion = chatRoot.querySelector(".ticket-detail-composer-format-toolbar");
  const noteRegion = chatRoot.querySelector('[data-chat-section="composer-note"]');
  const actionRegion = chatRoot.querySelector('[data-chat-section="composer-action"]');
  const attachmentsRegion = chatRoot.querySelector('[data-chat-section="composer-attachments-region"]');
  if (!messagesRegion || !toolbarRegion || !noteRegion || !actionRegion) {
    return false;
  }

  const composer = getActiveChatComposerElement();
  const snapshot = captureComposerPreservationState(composer);
  messagesRegion.innerHTML = viewState.usesNewTicketShell
    ? isNewTicketPostSendState(viewState)
      ? renderNewTicketPostSendThreadHtml(viewState)
      : renderNewTicketThreadHtml(viewState)
    : renderChatMessagesHtml(viewState);
  toolbarRegion.innerHTML = renderNewTicketComposerToolbar({
    canCompose: viewState.canCompose,
    includeSummary: false,
  });
  noteRegion.innerHTML = renderChatComposerNoteHtml(viewState);
  actionRegion.innerHTML = renderChatComposerActionHtml(viewState);
  if (attachmentsRegion) {
    attachmentsRegion.innerHTML = renderComposerAttachmentsHtml(viewState.ticket?.id);
  }
  if (composer) {
    if (isRichTextComposerElement(composer)) {
      composer.setAttribute("contenteditable", viewState.canCompose ? "true" : "false");
      composer.setAttribute("data-placeholder", getChatComposerPlaceholder(viewState));
    } else {
      composer.disabled = !viewState.canCompose;
      composer.placeholder = getChatComposerPlaceholder(viewState);
    }
  }
  restoreComposerPreservationState(composer, snapshot);
  applyComposerToolbarStateToDom();
  return true;
}

function refreshNewTicketInlineComposerAction() {
  const ticket = getActiveChatTicket();
  const viewState = buildChatTicketViewState(ticket);
  if (!viewState?.usesNewTicketShell || viewState.sending) {
    return;
  }

  const actionRegion = appRoot.querySelector('[data-chat-section="composer-action"]');
  if (!actionRegion) {
    return;
  }

  actionRegion.innerHTML = renderNewTicketComposerActionHtml(viewState);
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
    <section class="tickets-root clienttest-tickets ${buildClient2RoutePageClass({ visibleFooterBand: true })}">
      <header class="tickets-header">
        <div class="tickets-header-left">
          <button class="btn btn-ghost btn-icon" data-action="go-chat" type="button" aria-label="Back to workspace">
            <span class="material-symbols-outlined" aria-hidden="true">arrow_back</span>
          </button>
          <div>
            <div class="tickets-title">My Tickets</div>
            <p class="tickets-subtitle">Review active, waiting, and resolved tickets through the card-based ticket history view.</p>
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
      ${renderClient2RouteFooterBand()}
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

function buildClient2RoutePageClass({ visibleFooterBand = false, tailComposerRoute = false } = {}) {
  const classNames = ["clienttest-route-page"];
  if (visibleFooterBand) {
    classNames.push("clienttest-route-page-footer-band");
  }
  if (tailComposerRoute) {
    classNames.push("new-ticket-tail-route");
  }
  return classNames.join(" ");
}

function renderClient2RouteFooterBand({ communicatingShell = false } = {}) {
  return `
    <div class="clienttest-route-footer-band" aria-hidden="true">
      ${
        communicatingShell
          ? '<div class="clienttest-route-footer-shell clienttest-route-footer-shell-communicating"></div>'
          : ""
      }
    </div>
  `;
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
  } else if (hasNewVisibleMessages) {
    nextPlan = {
      ticketId,
      type: "bottom",
      behavior: "smooth",
    };
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
  const activeNewTicketShell = getActiveNewTicketShellTicket();
  const workspace = shell.querySelector(".clienttest-workspace");
  const topbarRegion = shell.querySelector('[data-authed-region="topbar"]');
  const contextRegion = shell.querySelector('[data-authed-region="context"]');
  const mainRegion = shell.querySelector('[data-authed-region="main"]');

  shell.querySelector('[data-authed-region="sidebar-nav"]').innerHTML = renderSidebarNav();
  shell.querySelector('[data-authed-region="sidebar-content"]').innerHTML = renderSidebarContent();
  shell.querySelector('[data-authed-region="sidebar-footer"]').innerHTML = renderSidebarFooter();
  if (topbarRegion) {
    topbarRegion.innerHTML = activeNewTicketShell ? "" : renderTopbar();
  }
  if (contextRegion) {
    contextRegion.innerHTML = activeNewTicketShell ? "" : renderContextBar();
  }
  if (workspace?.classList) {
    workspace.classList.toggle("clienttest-workspace-new-ticket", Boolean(activeNewTicketShell));
  }
  if (mainRegion?.classList) {
    mainRegion.classList.toggle("clienttest-main-new-ticket", Boolean(activeNewTicketShell));
  }
  renderMainRegion(mainRegion);

  bindAuthedEvents();
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

function markPendingTurnSuperseded(ticketId, pendingSession) {
  if (!pendingSession) {
    return;
  }
  rememberSupersededTurn(ticketId, {
    messageId: pendingSession.userMessageId,
    createdAt:
      pendingSession.persistedMessageCreatedAt || pendingSession.queuedMessageCreatedAt || "",
  });
}

function cancelPendingTurnSilently(ticketId, pendingSession) {
  if (!pendingSession?.queuedMessageCreatedAt) {
    return;
  }
  fetch(`/api/tickets/${encodeURIComponent(ticketId)}/cancel-pending`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      customer_id: state.user?.id || "",
      message_created_at: pendingSession.queuedMessageCreatedAt,
    }),
  }).catch(() => {
    // Best effort only; local supersede state already hides the old turn.
  });
}

async function stopGeneration(ticketId = state.activeTicketId) {
  const normalizedTicketId = normalizeTicketKey(ticketId);
  const pendingSession = getPendingSession(normalizedTicketId);
  if (!pendingSession) {
    return;
  }

  if (pendingSession.phase !== "queued" || !pendingSession.queuedMessageCreatedAt) {
    if (pendingSession.phase === "submitting" && pendingSession.abortController?.abort) {
      pendingSession.abortController.abort();
      clearPendingRequestState(normalizedTicketId);
      render();
    }
    return;
  }

  try {
    const response = await fetch(
      `/api/tickets/${encodeURIComponent(normalizedTicketId)}/cancel-pending`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          customer_id: state.user?.id || "",
          message_created_at: pendingSession.queuedMessageCreatedAt,
        }),
      }
    );
    if (!response.ok) {
      throw new Error(`Request failed with status ${response.status}`);
    }

    const activeTicket = getTicketById(normalizedTicketId);
    const pendingMessage = activeTicket?.messages?.find(
      (message) => message.id === pendingSession.userMessageId && message.role === "user"
    );
    const userMessages = Array.isArray(activeTicket?.messages)
      ? activeTicket.messages.filter((message) => message.role === "user")
      : [];
    const latestUserContent =
      userMessages.length > 0 ? String(userMessages[userMessages.length - 1]?.content || "") : "";
    state.editingMessageId = pendingSession.userMessageId;
    setComposerDraftFromMarkdown(pendingMessage?.content || latestUserContent || "");
    clearPendingRequestState(normalizedTicketId);
    await syncTicketsFromBackend({ silent: true });
    render();
    toast("Generation stopped. Edit your message and resend.");
  } catch (error) {
    toast(`Failed to stop generation: ${error.message}`, "error");
  }
}

async function handleSendMessage(text, options = {}) {
  const ticketId = state.activeTicketId;
  const ticket = getTicketById(ticketId);
  if (!ticket || ticket.status === "resolved") {
    return;
  }
  const normalizedProduct = normalizeTicketProduct(ticket.product);
  const existingPendingSession = getPendingSession(ticketId);
  if (existingPendingSession?.phase === "submitting") {
    return;
  }

  if (existingPendingSession?.phase === "queued") {
    markPendingTurnSuperseded(ticketId, existingPendingSession);
    clearPendingRequestState(ticketId);
    cancelPendingTurnSilently(ticketId, existingPendingSession);
  }

  const editMessageId = options.editMessageId || null;
  const now = new Date().toISOString();
  const messageAttachments = getComposerAttachments(ticketId).filter((attachment) => attachment.status === "uploaded");
  const messageAssetIds = messageAttachments.map((attachment) => attachment.assetId).filter(Boolean);
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
          contentFormat: CUSTOMER_MESSAGE_MARKDOWN_FORMAT,
          attachments: messageAttachments,
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
      contentFormat: CUSTOMER_MESSAGE_MARKDOWN_FORMAT,
      attachments: messageAttachments,
    };
    userMessageId = userMessage.id;
    messages = [...ticket.messages, userMessage];
  }

  saveTicketMessages(ticketId, messages);
  const hasEscalatedAssistance =
    String(ticket.status || "").trim().toLowerCase() === "escalated";
  updateTicketStatus(ticketId, hasEscalatedAssistance ? "escalated" : "communicating");
  state.editingMessageId = null;
  setComposerDraftFromMarkdown("");
  const abortController = createAbortController();
  setPendingSession(ticketId, {
    phase: "submitting",
    userMessageId,
    abortController,
  });
  requestChatScrollToBottom(ticketId, { behavior: "smooth" });
  render();

  try {
    const requestBody = {
      ticket_id: ticketId,
      customer_id: state.user.id,
      requester: state.user.name,
      message: text,
      content_format: CUSTOMER_MESSAGE_MARKDOWN_FORMAT,
    };
    if (normalizedProduct) {
      requestBody.product = normalizedProduct;
    }
    if (messageAssetIds.length > 0) {
      requestBody.asset_ids = messageAssetIds;
    }
    const response = await fetch("/api/tickets/query", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      signal: abortController.signal,
      body: JSON.stringify(requestBody),
    });
    if (!response.ok) {
      throw new Error(`Request failed with status ${response.status}`);
    }
    const payload = await response.json();
    if (messageAssetIds.length > 0) {
      clearUploadedComposerAttachments(ticketId);
    }
    const updated = getTicketById(ticketId);
    const queuedForAi = Boolean(payload?.queued_for_ai);
    const persistedMessageCreatedAt = String(
      payload?.message_created_at || payload?.queued_message_created_at || ""
    ).trim();
    const queuedMessageCreatedAt = String(payload?.queued_message_created_at || "").trim();
    if (queuedForAi) {
      keepWaitingForAsync = true;
      setPendingSession(ticketId, {
        phase: "queued",
        userMessageId,
        persistedMessageCreatedAt,
        queuedMessageCreatedAt: queuedMessageCreatedAt || persistedMessageCreatedAt,
        waitingForDurableReply: true,
      });
    } else {
      setPendingSession(ticketId, {
        phase: "submitting",
        userMessageId,
        persistedMessageCreatedAt,
      });
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
      setComposerDraftFromMarkdown(pendingMessage?.content || text);
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
    if (keepWaitingForAsync) {
      ensurePendingStatusPolling();
    } else {
      clearPendingRequestState(ticketId);
    }
    render();
  }
}

function bindAuthedEvents() {
  appRoot.querySelectorAll("[data-action='toggle-sidebar']").forEach((element) => {
    element.addEventListener("click", () => {
      state.mobileSidebarOpen = !state.mobileSidebarOpen;
      render();
    });
  });

  appRoot.querySelectorAll("[data-action='close-sidebar']").forEach((element) => {
    element.addEventListener("click", () => {
      if (!state.mobileSidebarOpen) {
        return;
      }
      state.mobileSidebarOpen = false;
      render();
    });
  });

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

  appRoot.querySelectorAll("[data-action='go-tickets-top']").forEach((element) => {
    element.addEventListener("click", () => navigateToTicketsTop());
  });

  appRoot.querySelectorAll("[data-action='go-chat']").forEach((element) => {
    element.addEventListener("click", () => navigate("/chat"));
  });
  bindTicketProductSelect();
  bindStatusFilter();

  appRoot.querySelectorAll("[data-composer-markdown-action]").forEach((element) => {
    if (!element.__clientComposerToolbarBound) {
      element.addEventListener("mousedown", (event) => {
        event.preventDefault();
      });
      element.addEventListener("click", () => {
        handleComposerToolbarAction(element.getAttribute("data-composer-markdown-action"));
      });
      element.__clientComposerToolbarBound = true;
    }
  });

  appRoot.querySelectorAll("[data-attachment-remove-id]").forEach((element) => {
    if (!element.__clientAttachmentRemoveBound) {
      element.addEventListener("click", () => {
        removeComposerAttachment(state.activeTicketId, element.getAttribute("data-attachment-remove-id"));
        render();
      });
      element.__clientAttachmentRemoveBound = true;
    }
  });

  appRoot.querySelectorAll("[data-asset-download-id]").forEach((element) => {
    if (!element.__clientAssetDownloadBound) {
      element.addEventListener("click", () => {
        downloadAsset(element.getAttribute("data-asset-download-id"));
      });
      element.__clientAssetDownloadBound = true;
    }
  });

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
    if (isRichTextComposerElement(input)) {
      input.addEventListener("input", () => {
        syncComposerDraftStateFromElement(input);
      });
      input.addEventListener("focus", () => {
        syncComposerToolbarStateFromElement(input);
      });
      input.addEventListener("mouseup", () => {
        syncComposerToolbarStateFromElement(input);
      });
      input.addEventListener("keyup", () => {
        syncComposerToolbarStateFromElement(input);
      });
      input.addEventListener("paste", (event) => {
        const text = event.clipboardData?.getData("text/plain");
        if (typeof text !== "string") {
          return;
        }
        event.preventDefault();
        insertComposerPlainText(input, text, { preserveNewlines: true });
      });
      input.addEventListener("keydown", (event) => {
        if (handleRichComposerListDeletion(event, input)) {
          return;
        }
        if (event.key === "Enter" && !event.shiftKey) {
          event.preventDefault();
          form?.requestSubmit();
          return;
        }
        if (event.key === "Enter" && event.shiftKey) {
          event.preventDefault();
          handleRichComposerShiftEnter(input);
        }
      });
      syncComposerDraftStateFromElement(input);
    } else {
      input.addEventListener("input", () => {
        state.inputDraft = input.value;
        refreshNewTicketInlineComposerAction();
      });
      input.addEventListener("keydown", (event) => {
        if (event.key === "Enter" && !event.shiftKey) {
          event.preventDefault();
          form?.requestSubmit();
        }
      });
    }
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

  clearDeliveredStatusRefreshTimer();
  syncLegacyPendingState();
  parseRoute();
  if (!state.user) {
    clearPendingRequestState();
    closeClientRealtimeConnection();
    clearReplyCountdownRefreshTimer();
    resetChatScrollState();
    renderLogin();
    return;
  }
  if (state.view !== "chat-ticket") {
    clearReplyCountdownRefreshTimer();
  }
  if (state.view === "chat-home") {
    ensureWorkspaceServiceEventsLoaded();
  }

  const nextTicketId =
    state.view === "chat-ticket" ? String(state.activeTicketId || "").trim() : "";
  if (!nextTicketId) {
    resetChatScrollState();
  } else if (previousView !== "chat-ticket" || previousTicketId !== nextTicketId) {
    requestChatScrollToBottom(nextTicketId);
  }

  setupClientRealtimeConnection();
  renderAuthed();
  consumeTicketsPageScrollReset();
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
