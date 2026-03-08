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
let detailLoading = false;
let showTellAiComposer = false;
let tellAiDraft = "";
let takeoverReplyDraft = "";
let tellAiSubmitting = false;
let takeoverSubmitting = false;
let modeSwitching = false;
let statusComboboxOpen = false;
let modeComboboxOpen = false;
let statusComboboxQuery = "";
let modeComboboxQuery = "";
let statusComboboxBlurTimer = null;
let modeComboboxBlurTimer = null;
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

function parseEngineerRequest(rawValue) {
  const raw = String(rawValue || "").trim();
  if (!raw) {
    return { issue: "", action: "", formatted: "" };
  }

  const lines = raw
    .split(/\r?\n/g)
    .map((line) => line.trim())
    .filter(Boolean);
  const issueParts = [];
  const actionParts = [];
  let currentSection = null;

  for (const line of lines) {
    const lowered = line.toLowerCase();
    if (lowered.startsWith("engineer request")) {
      currentSection = null;
      continue;
    }
    if (lowered.startsWith("issue:")) {
      currentSection = "issue";
      const issueLine = line.split(":", 2)[1]?.trim() || "";
      if (issueLine) {
        issueParts.push(issueLine);
      }
      continue;
    }
    if (lowered.startsWith("action needed:")) {
      currentSection = "action";
      const actionLine = line.split(":", 2)[1]?.trim() || "";
      if (actionLine) {
        actionParts.push(actionLine);
      }
      continue;
    }
    if (currentSection === "issue") {
      issueParts.push(line);
    } else if (currentSection === "action") {
      actionParts.push(line);
    }
  }

  const issue = issueParts.join(" ").trim();
  const action = actionParts.join(" ").trim();
  if (!issue && !action) {
    return { issue: "", action: "", formatted: raw };
  }
  return {
    issue,
    action,
    formatted: `Engineer Request:\nIssue: ${issue || "N/A"}\nAction Needed: ${action || "N/A"}`,
  };
}

function engineerRequestStatusLabel(status) {
  const normalized = String(status || "").trim().toLowerCase();
  if (normalized === "engineer replied") {
    return "Engineer Replied";
  }
  if (normalized === "engineer takeover") {
    return "Engineer Takeover";
  }
  if (normalized === "received answer") {
    return "Received Answer";
  }
  return "Unknown";
}

function engineerRequestStatusClass(status) {
  const normalized = String(status || "").trim().toLowerCase();
  if (normalized === "engineer replied") {
    return "record-status-replied";
  }
  if (normalized === "engineer takeover") {
    return "record-status-takeover";
  }
  if (normalized === "received answer") {
    return "record-status-answer";
  }
  return "";
}

function clearStatusComboboxBlurTimer() {
  if (statusComboboxBlurTimer) {
    clearTimeout(statusComboboxBlurTimer);
    statusComboboxBlurTimer = null;
  }
}

function clearModeComboboxBlurTimer() {
  if (modeComboboxBlurTimer) {
    clearTimeout(modeComboboxBlurTimer);
    modeComboboxBlurTimer = null;
  }
}

function detailStatusOptions() {
  return [
    { value: "open", label: statusLabel("open") },
    { value: "waiting_for_engineer", label: statusLabel("waiting_for_engineer") },
    { value: "resolved", label: statusLabel("resolved") },
  ];
}

function detailModeOptions() {
  return [
    { value: "managed", label: "AI Managing" },
    { value: "takeover", label: "Human Takeover" },
  ];
}

function filterComboboxOptions(options, query) {
  const keyword = String(query || "").trim().toLowerCase();
  if (!keyword) {
    return options;
  }
  return options.filter((option) => String(option.label || "").toLowerCase().includes(keyword));
}

function buildDetailComboboxHtml({
  kind,
  selectedValue,
  options,
  isOpen,
  query,
  disabled = false,
  placeholder = "",
}) {
  const filteredOptions = filterComboboxOptions(options, query);
  const selectedOption = options.find((option) => option.value === selectedValue) || options[0];
  const displayValue = isOpen ? String(query || "") : String(selectedOption?.label || "");
  const panelId = `detail-${kind}-options`;
  const inputId = `detail-${kind}-input`;

  return `
    <div
      class="detail-combobox ${isOpen ? "is-open" : ""} ${disabled ? "is-disabled" : ""}"
      data-combobox-root="${escapeHtml(kind)}"
    >
      <div class="detail-combobox-control">
        <input
          id="${escapeHtml(inputId)}"
          class="detail-combobox-input"
          type="text"
          autocomplete="off"
          role="combobox"
          aria-expanded="${isOpen ? "true" : "false"}"
          aria-controls="${escapeHtml(panelId)}"
          value="${escapeHtml(displayValue)}"
          placeholder="${escapeHtml(placeholder)}"
          ${disabled ? "disabled" : ""}
        />
        <button
          type="button"
          class="detail-combobox-toggle"
          data-detail-action="toggle-${escapeHtml(kind)}-combobox"
          ${disabled ? "disabled" : ""}
          aria-label="Toggle ${escapeHtml(kind)} options"
        >
          <span class="detail-combobox-caret" aria-hidden="true"></span>
        </button>
      </div>
      <div
        id="${escapeHtml(panelId)}"
        class="detail-combobox-panel ${isOpen ? "" : "hidden"}"
        role="listbox"
      >
        ${
          filteredOptions.length === 0
            ? '<p class="detail-combobox-empty">No matching options.</p>'
            : filteredOptions
                .map((option) => {
                  const isSelected = option.value === selectedValue;
                  return `
                    <button
                      type="button"
                      class="detail-combobox-option ${isSelected ? "is-selected" : ""}"
                      data-detail-action="select-${escapeHtml(kind)}-option"
                      data-value="${escapeHtml(option.value)}"
                      role="option"
                      aria-selected="${isSelected ? "true" : "false"}"
                    >
                      ${escapeHtml(option.label)}
                    </button>
                  `;
                })
                .join("")
        }
      </div>
    </div>
  `;
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

function applyLocalTicketPatch(ticketId, patch) {
  const normalizedId = String(ticketId || "").trim();
  if (!normalizedId || !patch || typeof patch !== "object") {
    return;
  }

  tickets = tickets.map((ticket) => {
    if (String(ticket.ticket_id || "") !== normalizedId) {
      return ticket;
    }
    return { ...ticket, ...patch };
  });

  if (
    selectedTicket &&
    String(selectedTicket.ticket_id || selectedTicketId || "").trim() === normalizedId
  ) {
    selectedTicket = { ...selectedTicket, ...patch };
  }
}

function refreshSelectedSummaryPreview(ticketId) {
  const normalizedId = String(ticketId || "").trim();
  if (
    !normalizedId ||
    !selectedTicket ||
    String(selectedTicket.ticket_id || selectedTicketId || "").trim() !== normalizedId
  ) {
    return;
  }

  selectedTicketSummary = buildLocalTicketSummary(selectedTicket);
  selectedTicketSummaryMeta = "AI Summary (loading...)";
  ticketSummaryCache.delete(normalizedId);
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
      const parsedEngineerRequest = parseEngineerRequest(pendingQuestion);
      const showPendingQuestion = Boolean(pendingQuestion);
      const previewSource = parsedEngineerRequest.issue
        ? `Issue: ${parsedEngineerRequest.issue}`
        : parsedEngineerRequest.formatted || pendingQuestion;
      const pendingPreview =
        previewSource.length > 140 ? `${previewSource.slice(0, 140)}...` : previewSource;

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
                ? `<div class="pending-note pending-note-preview">${escapeHtml(pendingPreview)}</div>`
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
  clearStatusComboboxBlurTimer();
  clearModeComboboxBlurTimer();
  selectedTicketId = null;
  selectedTicket = null;
  selectedTicketSummary = "";
  selectedTicketSummaryMeta = "";
  detailLoading = false;
  showTellAiComposer = false;
  tellAiDraft = "";
  takeoverReplyDraft = "";
  tellAiSubmitting = false;
  takeoverSubmitting = false;
  modeSwitching = false;
  statusComboboxOpen = false;
  modeComboboxOpen = false;
  statusComboboxQuery = "";
  modeComboboxQuery = "";
  detailModalEl.classList.add("hidden");
  detailTitleEl.textContent = "Ticket Detail";
  detailBodyEl.innerHTML = "";
  renderTickets();
}

function renderTicketDetail() {
  if (!selectedTicketId) {
    detailModalEl.classList.add("hidden");
    detailTitleEl.textContent = "Ticket Detail";
    detailBodyEl.innerHTML = "";
    return;
  }

  if (detailLoading) {
    detailTitleEl.textContent = `Ticket ${selectedTicketId}`;
    detailBodyEl.innerHTML = `
      <section class="detail-loading" role="status" aria-live="polite" aria-busy="true">
        <span class="loading-spinner" aria-hidden="true"></span>
        <p>Loading ticket detail...</p>
      </section>
    `;
    detailModalEl.classList.remove("hidden");
    return;
  }

  if (!selectedTicket) {
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
  const parsedEngineerRequest = parseEngineerRequest(pendingQuestion);
  const showPendingQuestion = Boolean(pendingQuestion);
  const pendingDetailText = parsedEngineerRequest.formatted || pendingQuestion;
  const pendingDetailBodyText =
    parsedEngineerRequest.issue || parsedEngineerRequest.action
      ? `Issue: ${parsedEngineerRequest.issue || "N/A"}\nAction Needed: ${
          parsedEngineerRequest.action || "N/A"
        }`
      : pendingDetailText;
  const isTakeoverMode = mode === "takeover";
  const controlsDisabled = tellAiSubmitting || takeoverSubmitting || modeSwitching;
  const takeoverComposerDisabled = takeoverSubmitting;
  const statusComboboxDisabled = tellAiSubmitting || takeoverSubmitting || modeSwitching;
  const modeComboboxDisabled = modeSwitching || tellAiSubmitting || takeoverSubmitting;
  const takeoverButtonLabel = modeSwitching
    ? `<span class="btn-spinner-inline loading-spinner loading-spinner-sm" aria-hidden="true"></span>Switching...`
    : "Takeover";
  const modeSwitchNotice = modeSwitching
    ? `<p class="mode-switch-notice" role="status" aria-live="polite"><span class="loading-spinner loading-spinner-sm" aria-hidden="true"></span>Applying mode change...</p>`
    : "";
  const statusComboboxHtml = buildDetailComboboxHtml({
    kind: "status",
    selectedValue: status,
    options: detailStatusOptions(),
    isOpen: statusComboboxOpen,
    query: statusComboboxQuery,
    disabled: statusComboboxDisabled,
    placeholder: "Search status",
  });
  const modeComboboxHtml = buildDetailComboboxHtml({
    kind: "mode",
    selectedValue: mode,
    options: detailModeOptions(),
    isOpen: modeComboboxOpen,
    query: modeComboboxQuery,
    disabled: modeComboboxDisabled,
    placeholder: "Search mode",
  });
  const messages = Array.isArray(ticket.messages) ? ticket.messages : [];
  const engineerRequestRecords = Array.isArray(ticket.engineer_request_records)
    ? ticket.engineer_request_records
    : [];
  const engineerRequestRecordsHtml =
    engineerRequestRecords.length === 0
      ? '<p class="request-record-empty">No completed engineer request records yet.</p>'
      : `<div class="request-record-list">${engineerRequestRecords
          .map((record) => {
            const statusText = engineerRequestStatusLabel(record.status);
            const statusClass = engineerRequestStatusClass(record.status);
            const detailText = String(record.detail || "").trim();
            const engineerText = String(record.engineer_id || "").trim();
            const createdAt = formatDateTime(record.created_at);
            return `
              <article class="request-record-item">
                <header>
                  <span class="request-record-status ${statusClass}">${escapeHtml(statusText)}</span>
                  <span class="request-record-time">${escapeHtml(createdAt)}</span>
                </header>
                ${
                  detailText
                    ? `<p class="request-record-detail">${formatMultiline(detailText)}</p>`
                    : ""
                }
                ${
                  engineerText
                    ? `<p class="request-record-meta">Engineer: ${escapeHtml(engineerText)}</p>`
                    : ""
                }
              </article>
            `;
          })
          .join("")}</div>`;

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
          ${statusComboboxHtml}
        </div>
      </div>
      <div class="detail-item detail-item-mode">
        <span>Mode</span>
        ${modeComboboxHtml}
        ${modeSwitchNotice}
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
    </section>

    ${
      showPendingQuestion
        ? `
    <section class="detail-block detail-block-engineer-request">
      <h3>Engineer Request</h3>
      <p class="engineer-request-body">${formatMultiline(pendingDetailBodyText)}</p>
      <div class="engineer-request-actions">
        <button
          type="button"
          class="btn btn-outline"
          data-detail-action="toggle-tell-ai"
          ${isTakeoverMode ? "disabled" : ""}
          ${controlsDisabled ? "disabled" : ""}
        >
          Tell AI
        </button>
        <button
          type="button"
          class="btn btn-outline"
          data-detail-action="takeover-mode"
          ${isTakeoverMode ? "disabled" : ""}
          ${controlsDisabled ? "disabled" : ""}
        >
          ${takeoverButtonLabel}
        </button>
      </div>
      ${
        modeSwitching
          ? `<p class="mode-switch-hint" role="status" aria-live="polite">Takeover mode is being enabled. Please wait...</p>`
          : ""
      }
      ${
        showTellAiComposer && !isTakeoverMode
          ? `
      <div class="tell-ai-composer">
        <textarea
          id="detail-tell-ai-input"
          class="detail-textarea"
          rows="4"
          placeholder="Tell AI what to say or what guidance to provide..."
          ${controlsDisabled ? "disabled" : ""}
        >${escapeHtml(tellAiDraft)}</textarea>
        <div class="detail-inline-actions">
          <button
            type="button"
            class="btn btn-primary"
            data-detail-action="send-tell-ai"
            ${controlsDisabled ? "disabled" : ""}
          >${tellAiSubmitting ? "Sending..." : "Send to AI"}</button>
          <button
            type="button"
            class="btn btn-ghost"
            data-detail-action="cancel-tell-ai"
            ${controlsDisabled ? "disabled" : ""}
          >Cancel</button>
        </div>
      </div>
      `
          : ""
      }
    </section>
    `
        : ""
    }

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
      <h3>Engineer Request Records</h3>
      ${engineerRequestRecordsHtml}
    </section>

    <section class="detail-block">
      <h3>Conversation</h3>
      ${messageItems}
      <div class="takeover-composer">
        <h4>Direct Reply to Customer</h4>
        <textarea
          id="detail-takeover-input"
          class="detail-textarea"
          rows="4"
          placeholder="Type your direct reply to the customer..."
          ${takeoverComposerDisabled ? "disabled" : ""}
        >${escapeHtml(takeoverReplyDraft)}</textarea>
        <div class="detail-inline-actions">
          <button
            type="button"
            class="btn btn-primary"
            data-detail-action="send-takeover-reply"
            ${takeoverComposerDisabled ? "disabled" : ""}
          >${
            takeoverSubmitting
              ? "Sending..."
              : "Send to Customer"
          }</button>
        </div>
      </div>
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
  const { silent = false, showLoading = false } = options;
  if (!selectedTicketId) {
    selectedTicket = null;
    selectedTicketSummary = "";
    selectedTicketSummaryMeta = "";
    detailLoading = false;
    renderTicketDetail();
    return;
  }

  if (showLoading) {
    detailLoading = true;
    renderTicketDetail();
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
    detailLoading = false;
    renderTicketDetail();
    refreshSelectedSummary({ silent: true }).catch(() => {
      // Keep local summary if async summary fails.
    });
  } catch (error) {
    detailLoading = false;
    if (String(error.message || "").toLowerCase().includes("not found")) {
      closeTicketDetail();
      return;
    }
    if (!silent) {
      window.alert(`Failed to load ticket detail: ${error.message}`);
    }
    renderTicketDetail();
  }
}

async function openTicketDetail(ticketId) {
  if (selectedTicketId !== ticketId) {
    clearStatusComboboxBlurTimer();
    clearModeComboboxBlurTimer();
    showTellAiComposer = false;
    tellAiDraft = "";
    takeoverReplyDraft = "";
    tellAiSubmitting = false;
    takeoverSubmitting = false;
    modeSwitching = false;
    statusComboboxOpen = false;
    modeComboboxOpen = false;
    statusComboboxQuery = "";
    modeComboboxQuery = "";
  }
  selectedTicketId = ticketId;
  renderTickets();
  await refreshSelectedTicket({ showLoading: true });
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
  return fetchJson(`/api/engineer/tickets/${encodeURIComponent(ticketId)}/mode`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ mode, engineer_id: ENGINEER_ID }),
  });
}

async function switchTicketModeOptimistic(ticketId, nextMode) {
  const normalizedId = String(ticketId || "").trim();
  const targetMode = String(nextMode || "managed").toLowerCase() === "takeover" ? "takeover" : "managed";
  if (!normalizedId) {
    return;
  }

  const previousTickets = tickets.map((ticket) => ({ ...ticket }));
  const previousSelectedTicket = selectedTicket ? { ...selectedTicket } : null;
  const previousSummary = selectedTicketSummary;
  const previousSummaryMeta = selectedTicketSummaryMeta;
  const previousShowTellAiComposer = showTellAiComposer;
  const previousTellAiDraft = tellAiDraft;
  const localPatch = {
    engineer_mode: targetMode,
    updated_at: new Date().toISOString(),
  };

  if (targetMode === "takeover") {
    showTellAiComposer = false;
    tellAiDraft = "";
    const currentStatus = String(selectedTicket?.status || "").toLowerCase();
    if (currentStatus === "waiting_for_engineer") {
      localPatch.status = "open";
      localPatch.pending_engineer_question = null;
    }
  }

  modeSwitching = true;
  applyLocalTicketPatch(normalizedId, localPatch);
  refreshSelectedSummaryPreview(normalizedId);
  renderTickets();
  renderTicketDetail();

  try {
    const payload = await updateTicketMode(normalizedId, targetMode);
    const serverMode = String(payload?.engineer_mode || targetMode).toLowerCase();
    const serverPatch = {
      engineer_mode: serverMode,
      status: String(payload?.status || localPatch.status || selectedTicket?.status || "open").toLowerCase(),
      updated_at: String(payload?.updated_at || new Date().toISOString()),
    };
    if (serverMode === "takeover") {
      serverPatch.pending_engineer_question = null;
    }
    applyLocalTicketPatch(normalizedId, serverPatch);
    refreshSelectedSummaryPreview(normalizedId);
    renderTickets();
    renderTicketDetail();

    loadTickets({ refreshDetail: false }).catch(() => {
      // websocket or next poll will re-sync.
    });
    refreshSelectedSummary({ silent: true }).catch(() => {
      // Keep local summary if async summary fails.
    });
  } catch (error) {
    tickets = previousTickets;
    selectedTicket = previousSelectedTicket;
    selectedTicketSummary = previousSummary;
    selectedTicketSummaryMeta = previousSummaryMeta;
    showTellAiComposer = previousShowTellAiComposer;
    tellAiDraft = previousTellAiDraft;
    renderTickets();
    renderTicketDetail();
    throw error;
  } finally {
    modeSwitching = false;
    renderTicketDetail();
  }
}

async function updateTicketStatus(ticketId, action) {
  await fetchJson(`/api/tickets/${encodeURIComponent(ticketId)}/action`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ action, engineer_id: ENGINEER_ID }),
  });
}

async function submitManagedResponse(ticketId, solutionText = null) {
  let cleaned = "";
  if (typeof solutionText === "string") {
    cleaned = solutionText.trim();
  } else {
    const solution = window.prompt("请输入给 AI 的处理建议，AI 会总结后回复客户：");
    if (solution === null) {
      return;
    }
    cleaned = solution.trim();
  }

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

async function submitTakeoverReply(ticketId, messageText) {
  const cleaned = String(messageText || "").trim();
  if (!cleaned) {
    window.alert("回复内容不能为空。");
    return;
  }
  await fetchJson(`/api/engineer/tickets/${encodeURIComponent(ticketId)}/takeover-reply`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message: cleaned, engineer_id: ENGINEER_ID }),
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

  if (action === "toggle-status-combobox") {
    if (tellAiSubmitting || takeoverSubmitting || modeSwitching) {
      return;
    }
    clearStatusComboboxBlurTimer();
    statusComboboxOpen = !statusComboboxOpen;
    modeComboboxOpen = false;
    modeComboboxQuery = "";
    if (!statusComboboxOpen) {
      statusComboboxQuery = "";
    }
    renderTicketDetail();
    if (statusComboboxOpen) {
      setTimeout(() => {
        const input = document.getElementById("detail-status-input");
        input?.focus();
      }, 0);
    }
    return;
  }

  if (action === "toggle-mode-combobox") {
    if (tellAiSubmitting || takeoverSubmitting || modeSwitching) {
      return;
    }
    clearModeComboboxBlurTimer();
    modeComboboxOpen = !modeComboboxOpen;
    statusComboboxOpen = false;
    statusComboboxQuery = "";
    if (!modeComboboxOpen) {
      modeComboboxQuery = "";
    }
    renderTicketDetail();
    if (modeComboboxOpen) {
      setTimeout(() => {
        const input = document.getElementById("detail-mode-input");
        input?.focus();
      }, 0);
    }
    return;
  }

  if (action === "select-status-option") {
    const nextStatus = String(button.dataset.value || "").trim().toLowerCase();
    if (!nextStatus) {
      return;
    }
    statusComboboxOpen = false;
    statusComboboxQuery = "";
    renderTicketDetail();

    const currentStatus = String(selectedTicket?.status || "open");
    if (nextStatus === currentStatus) {
      return;
    }

    const statusAction = statusValueToAction(nextStatus);
    try {
      await updateTicketStatus(selectedTicketId, statusAction);
      await loadTickets({ refreshDetail: false });
      await refreshSelectedTicket({ silent: true });
    } catch (error) {
      window.alert(`Status update failed: ${error.message}`);
      await refreshSelectedTicket({ silent: true });
    }
    return;
  }

  if (action === "select-mode-option") {
    const nextMode = String(button.dataset.value || "managed");
    modeComboboxOpen = false;
    modeComboboxQuery = "";
    renderTicketDetail();

    if (modeSwitching || nextMode === String(selectedTicket?.engineer_mode || "managed")) {
      return;
    }
    try {
      await switchTicketModeOptimistic(selectedTicketId, nextMode);
    } catch (error) {
      window.alert(`Mode update failed: ${error.message}`);
    }
    return;
  }

  if (action === "toggle-tell-ai") {
    if (modeSwitching) {
      return;
    }
    if (String(selectedTicket?.engineer_mode || "managed") === "takeover") {
      window.alert("Ticket is in Human Takeover mode. Switch back to AI Managing first.");
      return;
    }
    showTellAiComposer = !showTellAiComposer;
    if (!showTellAiComposer) {
      tellAiDraft = "";
    }
    renderTicketDetail();
    if (showTellAiComposer) {
      setTimeout(() => {
        const input = document.getElementById("detail-tell-ai-input");
        input?.focus();
      }, 0);
    }
    return;
  }

  if (action === "cancel-tell-ai") {
    if (modeSwitching) {
      return;
    }
    showTellAiComposer = false;
    tellAiDraft = "";
    renderTicketDetail();
    return;
  }

  if (action === "takeover-mode") {
    if (modeSwitching) {
      return;
    }
    try {
      await switchTicketModeOptimistic(selectedTicketId, "takeover");
    } catch (error) {
      window.alert(`Takeover failed: ${error.message}`);
    }
    return;
  }

  if (action === "send-tell-ai") {
    if (modeSwitching) {
      window.alert("Mode is switching. Please wait.");
      return;
    }
    if (String(selectedTicket?.engineer_mode || "managed") === "takeover") {
      window.alert("Ticket is in Human Takeover mode. Switch back to AI Managing first.");
      return;
    }
    const cleaned = tellAiDraft.trim();
    if (!cleaned) {
      window.alert("Please input guidance for AI.");
      return;
    }
    button.disabled = true;
    tellAiSubmitting = true;
    renderTicketDetail();
    try {
      await submitManagedResponse(selectedTicketId, cleaned);
      showTellAiComposer = false;
      tellAiDraft = "";
      await loadTickets({ refreshDetail: false });
      await refreshSelectedTicket({ silent: true });
    } catch (error) {
      window.alert(`Tell AI failed: ${error.message}`);
      await refreshSelectedTicket({ silent: true });
    } finally {
      tellAiSubmitting = false;
      renderTicketDetail();
      button.disabled = false;
    }
    return;
  }

  if (action === "send-takeover-reply") {
    const cleaned = takeoverReplyDraft.trim();
    if (!cleaned) {
      window.alert("Please input your reply to customer.");
      return;
    }
    button.disabled = true;
    takeoverSubmitting = true;
    renderTicketDetail();
    try {
      await submitTakeoverReply(selectedTicketId, cleaned);
      takeoverReplyDraft = "";
      await loadTickets({ refreshDetail: false });
      await refreshSelectedTicket({ silent: true });
    } catch (error) {
      window.alert(`Send reply failed: ${error.message}`);
      await refreshSelectedTicket({ silent: true });
    } finally {
      takeoverSubmitting = false;
      renderTicketDetail();
      button.disabled = false;
    }
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

function handleDetailInput(event) {
  const statusInput = event.target.closest("#detail-status-input");
  if (statusInput) {
    clearStatusComboboxBlurTimer();
    statusComboboxOpen = true;
    modeComboboxOpen = false;
    modeComboboxQuery = "";
    statusComboboxQuery = String(statusInput.value || "");
    renderTicketDetail();
    setTimeout(() => {
      const input = document.getElementById("detail-status-input");
      if (!input) {
        return;
      }
      input.focus();
      const end = statusComboboxQuery.length;
      input.setSelectionRange(end, end);
    }, 0);
    return;
  }

  const modeInput = event.target.closest("#detail-mode-input");
  if (modeInput) {
    clearModeComboboxBlurTimer();
    modeComboboxOpen = true;
    statusComboboxOpen = false;
    statusComboboxQuery = "";
    modeComboboxQuery = String(modeInput.value || "");
    renderTicketDetail();
    setTimeout(() => {
      const input = document.getElementById("detail-mode-input");
      if (!input) {
        return;
      }
      input.focus();
      const end = modeComboboxQuery.length;
      input.setSelectionRange(end, end);
    }, 0);
    return;
  }

  const tellAiInput = event.target.closest("#detail-tell-ai-input");
  if (tellAiInput) {
    tellAiDraft = String(tellAiInput.value || "");
    return;
  }

  const takeoverInput = event.target.closest("#detail-takeover-input");
  if (takeoverInput) {
    takeoverReplyDraft = String(takeoverInput.value || "");
  }
}

function closeStatusComboboxWithDelay() {
  clearStatusComboboxBlurTimer();
  statusComboboxBlurTimer = setTimeout(() => {
    statusComboboxBlurTimer = null;
    const root = detailBodyEl.querySelector('[data-combobox-root="status"]');
    const active = document.activeElement;
    if (root && active && root.contains(active)) {
      return;
    }
    if (!statusComboboxOpen && !statusComboboxQuery) {
      return;
    }
    statusComboboxOpen = false;
    statusComboboxQuery = "";
    renderTicketDetail();
  }, 140);
}

function closeModeComboboxWithDelay() {
  clearModeComboboxBlurTimer();
  modeComboboxBlurTimer = setTimeout(() => {
    modeComboboxBlurTimer = null;
    const root = detailBodyEl.querySelector('[data-combobox-root="mode"]');
    const active = document.activeElement;
    if (root && active && root.contains(active)) {
      return;
    }
    if (!modeComboboxOpen && !modeComboboxQuery) {
      return;
    }
    modeComboboxOpen = false;
    modeComboboxQuery = "";
    renderTicketDetail();
  }, 140);
}

function handleDetailFocusIn(event) {
  if (event.target.closest('[data-combobox-root="status"]')) {
    clearStatusComboboxBlurTimer();
  }
  if (event.target.closest('[data-combobox-root="mode"]')) {
    clearModeComboboxBlurTimer();
  }

  if (event.target.closest("#detail-status-input")) {
    if (!statusComboboxOpen) {
      statusComboboxOpen = true;
      statusComboboxQuery = "";
      modeComboboxOpen = false;
      modeComboboxQuery = "";
      renderTicketDetail();
      setTimeout(() => {
        const input = document.getElementById("detail-status-input");
        input?.focus();
      }, 0);
    }
    return;
  }

  if (event.target.closest("#detail-mode-input")) {
    if (!modeComboboxOpen) {
      modeComboboxOpen = true;
      modeComboboxQuery = "";
      statusComboboxOpen = false;
      statusComboboxQuery = "";
      renderTicketDetail();
      setTimeout(() => {
        const input = document.getElementById("detail-mode-input");
        input?.focus();
      }, 0);
    }
  }
}

function handleDetailFocusOut(event) {
  if (event.target.closest('[data-combobox-root="status"]')) {
    closeStatusComboboxWithDelay();
  }
  if (event.target.closest('[data-combobox-root="mode"]')) {
    closeModeComboboxWithDelay();
  }
}

function handleDetailKeydown(event) {
  if (event.key !== "Escape") {
    return;
  }
  if (!statusComboboxOpen && !modeComboboxOpen) {
    return;
  }
  event.preventDefault();
  event.stopPropagation();
  statusComboboxOpen = false;
  modeComboboxOpen = false;
  statusComboboxQuery = "";
  modeComboboxQuery = "";
  clearStatusComboboxBlurTimer();
  clearModeComboboxBlurTimer();
  renderTicketDetail();
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
  if (modeSwitching || mode === String(selectedTicket?.engineer_mode || "managed")) {
    return;
  }
  try {
    await switchTicketModeOptimistic(selectedTicketId, mode);
  } catch (error) {
    window.alert(`Mode update failed: ${error.message}`);
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
detailBodyEl.addEventListener("input", handleDetailInput);
detailBodyEl.addEventListener("focusin", handleDetailFocusIn);
detailBodyEl.addEventListener("focusout", handleDetailFocusOut);
detailBodyEl.addEventListener("keydown", handleDetailKeydown);
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
