const AUTH_KEY = "engineer_portal_auth";
const LOGIN_USER = "eng";
const LOGIN_PASS = "eng";
const ENGINEER_ID = "eng";

const loginScreenEl = document.getElementById("login-screen");
const engineerScreenEl = document.getElementById("engineer-screen");
const loginFormEl = document.getElementById("login-form");
const loginErrorEl = document.getElementById("login-error");
const filterControlsEl = document.getElementById("filter-controls");
const headerUserControlsEl = document.getElementById("header-user-controls");
const wsStatusEl = document.getElementById("ws-status");
const workspaceRegionEl = document.getElementById("workspace-region");
const workspaceTitleEl = document.getElementById("workspace-title");
const workspaceSubtitleEl = document.getElementById("workspace-subtitle");
const railNavEl = document.getElementById("rail-nav");

let tickets = [];
let boardLoading = false;
let selectedTicketId = null;
let selectedTicket = null;
let selectedTicketSummary = "";
let selectedTicketNextAction = "";
let detailLoading = false;
let tellAiDraft = "";
let investigationReviseMode = false;
let tellAiSubmitting = false;
let socket = null;
let heartbeatTimer = null;
let reconnectTimer = null;
let storageMode = "unknown";
let logoutLoading = false;
const ticketSummaryCache = new Map();
const routeState = {
  view: "pool",
  ticketId: null,
};

const ENGINEER_POOL_STATUSES = ["investigating", "escalated", "communicating", "resolved"];
const POOL_STATUS_RANK = {
  investigating: 4,
  escalated: 3,
  communicating: 2,
  resolved: 1,
  open: 0,
};
const DEFAULT_FETCH_TIMEOUT_MS = 25000;
const TELL_AI_FETCH_TIMEOUT_MS = 70000;
const TICKET_POOL_VIEW_STORAGE_KEY = "engineer_ticket_pool_view_mode";

const FILTER_KEYS = [];
const FILTER_BLUR_DELAY_MS = 140;
const filterValues = {};
const filterComboboxState = {};
let ticketPoolViewMode = "list";
let selectedPoolStatus = "investigating";
const filterComboboxConfig = {};

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function userInitial(username) {
  const value = String(username || "").trim();
  if (!value) {
    return "U";
  }
  return value[0].toUpperCase();
}

function UserProfileChip({ username, role }) {
  const normalizedRole = String(role || "ENGINEER").trim().toUpperCase();
  const roleLabel =
    normalizedRole === "ADMIN"
      ? "ADMIN"
      : normalizedRole === "OPERATOR"
      ? "OPERATOR"
      : "ENGINEER";
  const roleClass =
    roleLabel === "ADMIN"
      ? "user-role-admin"
      : roleLabel === "OPERATOR"
      ? "user-role-operator"
      : "user-role-engineer";
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
    UserProfileChip({ username: LOGIN_USER, role: "ENGINEER" }),
    LogoutButton({ loading: logoutLoading }),
  ].join("");
  const logoutBtn = document.getElementById("logout-btn");
  logoutBtn?.addEventListener("click", () => {
    handleLogoutClick().catch((error) => {
      window.alert(`Logout failed: ${error.message}`);
    });
  });
}

function parseRoute() {
  const hash = String(window.location.hash || "#/tickets").trim();
  const path = hash.replace(/^#/, "") || "/tickets";

  if (path === "/" || path === "/tickets") {
    routeState.view = "pool";
    routeState.ticketId = null;
    return routeState;
  }

  if (path.startsWith("/tickets/")) {
    const ticketId = decodeURIComponent(path.split("/")[2] || "").trim();
    if (ticketId) {
      routeState.view = "detail";
      routeState.ticketId = ticketId;
      return routeState;
    }
  }

  routeState.view = "pool";
  routeState.ticketId = null;
  return routeState;
}

function navigate(path) {
  const target = `#${path}`;
  if (window.location.hash === target) {
    void syncRouteToWorkspace({ silent: true, showLoading: false });
    return false;
  }
  window.location.hash = target;
  return true;
}

function normalizeEngineerPoolStatus(value) {
  const normalized = String(value || "").trim().toLowerCase();
  return ENGINEER_POOL_STATUSES.includes(normalized) ? normalized : "investigating";
}

function isEngineerVisibleStatus(value) {
  return ENGINEER_POOL_STATUSES.includes(normalizeStatusValue(value));
}

function setSelectedPoolStatus(value, { render = true } = {}) {
  selectedPoolStatus = normalizeEngineerPoolStatus(value);
  if (render && routeState.view === "pool") {
    renderTickets();
  } else if (render) {
    renderWorkspaceChrome();
  }
  return selectedPoolStatus;
}

function renderRailNav() {
  if (!railNavEl) {
    return;
  }

  railNavEl.innerHTML = `
    ${ENGINEER_POOL_STATUSES.map((status) => {
      const isActive = selectedPoolStatus === status;
      const icon =
        status === "investigating"
          ? "troubleshoot"
          : status === "escalated"
          ? "notification_important"
          : status === "communicating"
          ? "forum"
          : "task_alt";
      return `
      <button
        class="rail-nav-item ${isActive ? "is-active" : ""}"
        type="button"
        data-nav-status="${escapeHtml(status)}"
        aria-pressed="${isActive ? "true" : "false"}"
      >
        <span class="material-symbols-outlined" aria-hidden="true">${icon}</span>
        <span class="rail-nav-label">${escapeHtml(statusLabel(status))}</span>
      </button>
    `;
    }).join("")}
  `;
}

function renderWorkspaceChrome() {
  const engineerVisibleTickets = tickets.filter((ticket) => isEngineerVisibleStatus(ticket?.status || "open"));
  if (routeState.view === "detail" && routeState.ticketId) {
    const detailStatus = selectedTicket
      ? statusLabel(normalizeStatusValue(selectedTicket.status || "open"))
      : "Loading ticket context...";
    if (workspaceTitleEl) {
      workspaceTitleEl.textContent = "Active Ticket Workspace";
    }
    if (workspaceSubtitleEl) {
      workspaceSubtitleEl.textContent = detailStatus;
    }
    filterControlsEl?.classList.add("hidden");
    if (filterControlsEl) {
      filterControlsEl.innerHTML = "";
    }
  } else {
    if (workspaceTitleEl) {
      workspaceTitleEl.textContent = "Engineer Command Center";
    }
    if (workspaceSubtitleEl) {
      workspaceSubtitleEl.textContent = `${engineerVisibleTickets.length} engineer-visible tickets across active support workflows.`;
    }
    filterControlsEl?.classList.remove("hidden");
    renderFilterControls();
  }

  renderHeaderUserControls();
  renderRailNav();
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

function normalizeStatusValue(value) {
  const normalized = String(value || "open").toLowerCase();
  if (normalized === "waiting_for_engineer") {
    return "investigating";
  }
  if (normalized === "escalated") {
    return "escalated";
  }
  if (normalized === "communicating") {
    return "communicating";
  }
  if (normalized === "resolved") {
    return "resolved";
  }
  return normalized === "investigating" ? "investigating" : "open";
}

function statusLabel(value) {
  const normalized = normalizeStatusValue(value);
  if (normalized === "communicating") {
    return "Communicating";
  }
  if (normalized === "escalated") {
    return "Escalated";
  }
  if (normalized === "investigating") {
    return "Investigating";
  }
  if (normalized === "resolved") {
    return "Resolved";
  }
  return "Open";
}

function statusClass(value) {
  const normalized = normalizeStatusValue(value);
  if (normalized === "resolved") {
    return "status-resolved";
  }
  if (normalized === "investigating") {
    return "status-investigating";
  }
  if (normalized === "escalated") {
    return "status-escalated";
  }
  if (normalized === "communicating") {
    return "status-communicating";
  }
  return "status-open";
}

function statusSurfaceClass(value) {
  const normalized = normalizeStatusValue(value);
  if (normalized === "resolved") {
    return "status-surface-resolved";
  }
  if (normalized === "investigating") {
    return "status-surface-investigating";
  }
  if (normalized === "escalated") {
    return "status-surface-escalated";
  }
  if (normalized === "communicating") {
    return "status-surface-communicating";
  }
  return "status-surface-open";
}

function roleLabel(role) {
  if (role === "customer") {
    return "Customer";
  }
  if (role === "engineer_ai") {
    return "Engineer AI";
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
  if (role === "engineer_ai") {
    return "msg-assistant";
  }
  if (role === "assistant") {
    return "msg-assistant";
  }
  if (role === "engineer") {
    return "msg-engineer";
  }
  return "msg-system";
}

function normalizeMessageSentimentLabel(value) {
  const normalized = String(value || "").trim().toLowerCase();
  if (normalized === "good" || normalized === "bad" || normalized === "neutral") {
    return normalized;
  }
  return "";
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

function getActiveInvestigation(ticket) {
  if (!ticket || typeof ticket !== "object") {
    return null;
  }
  return ticket.active_investigation && typeof ticket.active_investigation === "object"
    ? ticket.active_investigation
    : null;
}

function getLatestClosedInvestigation(ticket) {
  if (!ticket || typeof ticket !== "object") {
    return null;
  }
  const history = Array.isArray(ticket.investigation_history) ? ticket.investigation_history : [];
  return history.find((item) => item && typeof item === "object") || null;
}

function getDisplayInvestigation(ticket) {
  return getActiveInvestigation(ticket) || getLatestClosedInvestigation(ticket);
}

function latestInvestigationUpdate(ticket) {
  const activeInvestigation = getActiveInvestigation(ticket);
  if (activeInvestigation) {
    const messages = Array.isArray(activeInvestigation.messages) ? activeInvestigation.messages : [];
    for (let index = messages.length - 1; index >= 0; index -= 1) {
      const content = String(messages[index]?.content || "").trim();
      if (content) {
        return content;
      }
    }
  }
  return "";
}

function investigationStateLabel(value) {
  const normalized = String(value || "active").toLowerCase();
  if (normalized === "awaiting_confirmation") {
    return "Awaiting Engineer Approval";
  }
  if (normalized === "closed") {
    return "Closed";
  }
  return "Open Engineer Ticket";
}

function engineerRequestStatusLabel(status) {
  const normalized = String(status || "").trim().toLowerCase();
  if (normalized === "engineer replied") {
    return "Engineer Replied";
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
  if (normalized === "received answer") {
    return "record-status-answer";
  }
  return "";
}

function clearStatusComboboxBlurTimer() {
  const state = filterComboboxState.status;
  if (state?.blurTimer) {
    clearTimeout(state.blurTimer);
    state.blurTimer = null;
  }
}

function detailStatusOptions() {
  return [
    { value: "open", label: statusLabel("open") },
    { value: "communicating", label: statusLabel("communicating") },
    { value: "escalated", label: statusLabel("escalated") },
    { value: "investigating", label: statusLabel("investigating") },
    { value: "resolved", label: statusLabel("resolved") },
  ];
}

function filterComboboxOptions(options, query) {
  const keyword = String(query || "").trim().toLowerCase();
  if (!keyword) {
    return options;
  }
  return options.filter((option) => String(option.label || "").toLowerCase().includes(keyword));
}

function headerFilterOptions(key) {
  return [];
}

function normalizeFilterValue(key, value) {
  const normalized = String(value || "all").toLowerCase();
  const options = headerFilterOptions(key);
  if (options.some((option) => option.value === normalized)) {
    return normalized;
  }
  return "all";
}

function selectedHeaderFilterOption(key) {
  const options = headerFilterOptions(key);
  const selected = options.find((option) => option.value === filterValues[key]);
  return selected || options[0] || { value: "all", label: "All" };
}

function normalizeTicketPoolViewMode(value) {
  return String(value || "").toLowerCase() === "grid" ? "grid" : "list";
}

function hydrateTicketPoolViewMode() {
  try {
    ticketPoolViewMode = normalizeTicketPoolViewMode(localStorage.getItem(TICKET_POOL_VIEW_STORAGE_KEY));
  } catch (_error) {
    ticketPoolViewMode = "list";
  }
  return ticketPoolViewMode;
}

function applyTicketPoolViewMode(value, { render = true } = {}) {
  const normalized = normalizeTicketPoolViewMode(value);
  ticketPoolViewMode = normalized;
  try {
    localStorage.setItem(TICKET_POOL_VIEW_STORAGE_KEY, normalized);
  } catch (_error) {
    // Ignore storage errors and keep the in-memory mode.
  }
  if (render && routeState.view === "pool") {
    renderTickets();
  }
  return ticketPoolViewMode;
}

function poolViewToggleIcon(mode) {
  if (mode === "grid") {
    return `
      <svg class="pool-view-toggle-icon" viewBox="0 0 20 20" fill="none" aria-hidden="true">
        <rect x="3" y="3" width="5" height="5" rx="1.3" stroke="currentColor" stroke-width="1.4"></rect>
        <rect x="12" y="3" width="5" height="5" rx="1.3" stroke="currentColor" stroke-width="1.4"></rect>
        <rect x="3" y="12" width="5" height="5" rx="1.3" stroke="currentColor" stroke-width="1.4"></rect>
        <rect x="12" y="12" width="5" height="5" rx="1.3" stroke="currentColor" stroke-width="1.4"></rect>
      </svg>
    `;
  }
  return `
    <svg class="pool-view-toggle-icon" viewBox="0 0 20 20" fill="none" aria-hidden="true">
      <path d="M4 5H16" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"></path>
      <path d="M4 10H16" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"></path>
      <path d="M4 15H16" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"></path>
    </svg>
  `;
}

function buildPoolViewToggleHtml() {
  const viewMode = normalizeTicketPoolViewMode(ticketPoolViewMode);
  const options = [
    { value: "list", label: "List view" },
    { value: "grid", label: "Grid view" },
  ];

  return `
    <div class="pool-view-toggle" role="group" aria-label="Ticket pool layout">
      ${options
        .map((option) => {
          const isSelected = option.value === viewMode;
          return `
            <button
              type="button"
              class="pool-view-toggle-btn ${isSelected ? "is-active" : ""}"
              data-pool-view-option="${escapeHtml(option.value)}"
              aria-label="${escapeHtml(option.label)}"
              title="${escapeHtml(option.label)}"
              aria-pressed="${isSelected ? "true" : "false"}"
            >
              ${poolViewToggleIcon(option.value)}
              <span class="sr-only">${escapeHtml(option.label)}</span>
            </button>
          `;
        })
        .join("")}
    </div>
  `;
}

function buildHeaderFilterComboboxHtml(key) {
  const config = filterComboboxConfig[key];
  if (!config) {
    return "";
  }

  const state = filterComboboxState[key];
  const options = headerFilterOptions(key);
  const selected = selectedHeaderFilterOption(key);
  const searchable = config.searchable !== false;
  const query = String(state?.query || "");
  const filteredOptions = searchable ? filterComboboxOptions(options, query) : options;
  const isOpen = Boolean(state?.open);
  const disabled = Boolean(config.disabled);
  const displayValue = searchable && isOpen ? query : selected.label;
  const panelId = `ticket-filter-options-${key}`;
  const inputId = `ticket-filter-input-${key}`;
  const classes = [
    "filter-combobox",
    isOpen ? "is-open" : "",
    disabled ? "is-disabled" : "",
  ]
    .filter(Boolean)
    .join(" ");

  return `
    <div class="${escapeHtml(classes)}" data-filter-root="${escapeHtml(key)}">
      <div class="filter-combobox-control">
        <input
          id="${escapeHtml(inputId)}"
          class="filter-combobox-input"
          data-filter-input="${escapeHtml(key)}"
          type="text"
          autocomplete="off"
          role="combobox"
          aria-expanded="${isOpen ? "true" : "false"}"
          aria-controls="${escapeHtml(panelId)}"
          value="${escapeHtml(displayValue)}"
          aria-label="${escapeHtml(config.label)}"
          ${searchable ? "" : "readonly"}
          ${disabled ? "disabled" : ""}
        />
        <button
          type="button"
          class="filter-combobox-toggle"
          data-filter-action="toggle"
          data-filter-key="${escapeHtml(key)}"
          aria-label="Toggle ${escapeHtml(config.label)} options"
          ${disabled ? "disabled" : ""}
        >
          <span class="filter-combobox-caret" aria-hidden="true"></span>
        </button>
      </div>
      <div
        id="${escapeHtml(panelId)}"
        class="filter-combobox-panel ${isOpen ? "" : "hidden"}"
        role="listbox"
      >
        ${
          filteredOptions.length === 0
            ? '<p class="filter-combobox-empty">No matching options.</p>'
            : filteredOptions
                .map((option) => {
                  const isSelected = option.value === selected.value;
                  return `
                    <button
                      type="button"
                      class="filter-combobox-option ${isSelected ? "is-selected" : ""}"
                      data-filter-action="select"
                      data-filter-key="${escapeHtml(key)}"
                      data-value="${escapeHtml(option.value)}"
                      role="option"
                      aria-selected="${isSelected ? "true" : "false"}"
                      ${disabled ? "disabled" : ""}
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

function renderFilterControls() {
  if (!filterControlsEl) {
    return;
  }
  filterControlsEl.innerHTML = `
    <div class="filter-control-group">
      ${FILTER_KEYS.map((key) => buildHeaderFilterComboboxHtml(key)).join("")}
    </div>
    ${buildPoolViewToggleHtml()}
  `;
}

function clearFilterBlurTimer(key) {
  const state = filterComboboxState[key];
  if (!state?.blurTimer) {
    return;
  }
  clearTimeout(state.blurTimer);
  state.blurTimer = null;
}

function clearAllFilterBlurTimers() {
  FILTER_KEYS.forEach((key) => {
    clearFilterBlurTimer(key);
  });
}

function isHeaderFilterOpen() {
  return FILTER_KEYS.some((key) => Boolean(filterComboboxState[key]?.open));
}

function closeFilterCombobox(key, { clearQuery = true } = {}) {
  const state = filterComboboxState[key];
  if (!state) {
    return false;
  }
  const changed = state.open || (clearQuery && state.query);
  state.open = false;
  if (clearQuery) {
    state.query = "";
  }
  return Boolean(changed);
}

function closeAllHeaderFilterComboboxes({ render = true } = {}) {
  clearAllFilterBlurTimers();
  const changed = FILTER_KEYS.some((key) => closeFilterCombobox(key, { clearQuery: true }));
  if (changed && render) {
    renderFilterControls();
  }
}

function openFilterCombobox(key) {
  const state = filterComboboxState[key];
  const config = filterComboboxConfig[key];
  if (!state || !config || config.disabled) {
    return false;
  }

  clearFilterBlurTimer(key);
  let changed = false;
  FILTER_KEYS.forEach((otherKey) => {
    if (otherKey === key) {
      if (!filterComboboxState[otherKey].open) {
        filterComboboxState[otherKey].open = true;
        changed = true;
      }
      return;
    }
    if (closeFilterCombobox(otherKey, { clearQuery: true })) {
      changed = true;
    }
  });
  return changed;
}

function focusHeaderFilterInput(key) {
  setTimeout(() => {
    const input = document.getElementById(`ticket-filter-input-${key}`);
    if (!input) {
      return;
    }
    input.focus();
    const config = filterComboboxConfig[key];
    if (config?.searchable === false) {
      return;
    }
    const query = String(filterComboboxState[key]?.query || "");
    const end = query.length;
    input.setSelectionRange(end, end);
  }, 0);
}

function closeFilterComboboxWithDelay(key) {
  const state = filterComboboxState[key];
  if (!state) {
    return;
  }
  clearFilterBlurTimer(key);
  state.blurTimer = setTimeout(() => {
    state.blurTimer = null;
    const root = filterControlsEl?.querySelector(`[data-filter-root="${key}"]`);
    const active = document.activeElement;
    if (root && active && root.contains(active)) {
      return;
    }
    if (closeFilterCombobox(key, { clearQuery: true })) {
      renderFilterControls();
    }
  }, FILTER_BLUR_DELAY_MS);
}

function applyHeaderFilterValue(key, value) {
  const normalized = normalizeFilterValue(key, value);
  if (filterValues[key] === normalized) {
    return false;
  }

  filterValues[key] = normalized;
  const config = filterComboboxConfig[key];
  if (typeof config?.onValueChange === "function") {
    config.onValueChange(normalized, { ...filterValues });
  }
  if (config?.autoSubmit) {
    const form = filterControlsEl?.closest("form");
    if (form && typeof form.requestSubmit === "function") {
      form.requestSubmit();
    }
  }
  return true;
}

function applyTicketFilters(items) {
  return items.filter((ticket) => {
    const status = normalizeStatusValue(ticket?.status || "open");

    if (!isEngineerVisibleStatus(status)) {
      return false;
    }
    if (status !== selectedPoolStatus) {
      return false;
    }
    return true;
  });
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
  const investigationUpdatedAt = String(
    ticket?.active_investigation?.updated_at || ticket?.active_investigation?.opened_at || ""
  );
  const agentUpdatedAt = String(ticket?.engineer_agent_state?.last_refreshed_at || "");
  const handoffUpdatedAt = String(ticket?.engineer_handoff_packet?.updated_at || "");
  return [
    String(ticket?.updated_at || ""),
    String(ticket?.status || ""),
    String(messageCount),
    investigationUpdatedAt,
    agentUpdatedAt,
    handoffUpdatedAt,
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

  selectedTicketSummary = "Generating AI summary for this ticket...";
  selectedTicketNextAction = "Determining next action needed...";
  ticketSummaryCache.delete(normalizedId);
}

function buildLocalSummaryFallback(ticket) {
  const status = statusLabel(normalizeStatusValue(ticket?.status || "open"));
  const messages = Array.isArray(ticket?.messages) ? ticket.messages : [];
  const activeInvestigation = getActiveInvestigation(ticket);
  const agentState =
    ticket?.engineer_agent_state && typeof ticket.engineer_agent_state === "object"
      ? ticket.engineer_agent_state
      : null;

  if (agentState) {
    const missingInformation = Array.isArray(agentState.missing_information)
      ? agentState.missing_information
          .map((item) => String(item || "").trim())
          .filter(Boolean)
      : [];
    const summaryLines = [
      `Current understanding: ${String(agentState.issue_understanding || "Not available yet.").trim() || "Not available yet."}`,
      `Current knowledge: ${String(agentState.knowledge_summary || "Not available yet.").trim() || "Not available yet."}`,
      `Why client AI could not solve it: ${String(agentState.why_not_solved || "Not available yet.").trim() || "Not available yet."}`,
      `Goal: ${String(agentState.goal || "Not available yet.").trim() || "Not available yet."}`,
    ];
    if (missingInformation.length) {
      summaryLines.push(`Still missing: ${missingInformation.join("; ")}`);
    }
    return {
      summary: summaryLines.join(" "),
      nextAction:
        String(agentState.next_request_for_engineer || "").trim() ||
        "Continue the engineer ticket and collect the next missing detail.",
    };
  }

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

  const summaryLines = [`Ticket is currently ${status}.`];
  if (latestCustomer) {
    summaryLines.push(`Latest customer request: ${latestCustomer.slice(0, 220)}`);
  }
  if (latestAssistant) {
    summaryLines.push(`Latest AI response: ${latestAssistant.slice(0, 220)}`);
  }
  if (activeInvestigation) {
    summaryLines.push(
      `Engineer ticket is ${investigationStateLabel(activeInvestigation.state).toLowerCase()}.`
    );
    const latestInternal = latestInvestigationUpdate(ticket);
    if (latestInternal) {
      summaryLines.push(`Latest engineer ticket update: ${latestInternal.slice(0, 220)}`);
    }
  }

  const nextAction =
    activeInvestigation
      ? "Continue the engineer ticket, confirm the next missing detail, or approve the prepared customer reply."
      : latestCustomer || latestAssistant
      ? "Review the latest messages and either continue communicating, start an investigation, or resolve the ticket."
      : "Collect initial issue details from the customer and define the first troubleshooting step.";

  return {
    summary: summaryLines.join(" "),
    nextAction,
  };
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
  loginScreenEl?.classList.toggle("hidden", authed);
  engineerScreenEl?.classList.toggle("hidden", !authed);
}

function renderWorkspace() {
  parseRoute();

  if (routeState.view === "detail" && routeState.ticketId) {
    if (!selectedTicketId) {
      selectedTicketId = routeState.ticketId;
      detailLoading = true;
    }
    if (selectedTicketId === routeState.ticketId && !selectedTicket) {
      detailLoading = true;
    }
    renderTicketDetail();
    return;
  }

  renderTickets();
}

async function syncRouteToWorkspace(options = {}) {
  const { silent = true, showLoading = true } = options;
  parseRoute();

  if (routeState.view === "detail" && routeState.ticketId) {
    const nextTicketId = routeState.ticketId;
    const routeChanged = selectedTicketId !== nextTicketId;

    if (routeChanged) {
      resetDetailWorkspaceState();
      selectedTicketId = nextTicketId;
      detailLoading = true;
    }

    renderTicketDetail();
    if (routeChanged || !selectedTicket) {
      await refreshSelectedTicket({ silent, showLoading });
    }
    return;
  }

  if (selectedTicketId || selectedTicket || detailLoading) {
    resetDetailWorkspaceState();
  }
  renderTickets();
}

async function fetchJson(url, options = undefined) {
  const requestOptions = options ? { ...options } : {};
  const timeoutMsCandidate = Number(requestOptions.timeoutMs);
  const timeoutMs =
    Number.isFinite(timeoutMsCandidate) && timeoutMsCandidate > 0
      ? timeoutMsCandidate
      : DEFAULT_FETCH_TIMEOUT_MS;
  delete requestOptions.timeoutMs;

  const timeoutController = new AbortController();
  const timeoutId = setTimeout(() => {
    timeoutController.abort();
  }, timeoutMs);

  const externalSignal = requestOptions.signal;
  const abortFromExternal = () => {
    timeoutController.abort();
  };
  if (externalSignal) {
    if (externalSignal.aborted) {
      timeoutController.abort();
    } else {
      externalSignal.addEventListener("abort", abortFromExternal, { once: true });
    }
  }

  requestOptions.signal = timeoutController.signal;
  let response;
  try {
    response = await fetch(url, requestOptions);
  } catch (error) {
    if (error?.name === "AbortError") {
      throw new Error(`Request timed out after ${Math.ceil(timeoutMs / 1000)}s`);
    }
    throw error;
  } finally {
    clearTimeout(timeoutId);
    if (externalSignal) {
      externalSignal.removeEventListener("abort", abortFromExternal);
    }
  }

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

function sortTicketsByRecency(items) {
  return [...items].sort((a, b) => {
    const statusRankA = POOL_STATUS_RANK[normalizeStatusValue(a.status || "open")] || 0;
    const statusRankB = POOL_STATUS_RANK[normalizeStatusValue(b.status || "open")] || 0;
    if (statusRankA !== statusRankB) {
      return statusRankB - statusRankA;
    }
    const updatedA = new Date(a.updated_at || a.created_at || 0).getTime();
    const updatedB = new Date(b.updated_at || b.created_at || 0).getTime();
    return updatedB - updatedA;
  });
}

function describeTicketPoolTicket(ticket) {
  const ticketId = String(ticket.ticket_id || "-");
  const status = normalizeStatusValue(ticket.status || "open");
  const subject = String(ticket.subject || "(No subject)");
  const requester = String(ticket.requester || ticket.customer_id || "Unknown");
  const investigationPreview = latestInvestigationUpdate(ticket);
  const pendingQuestion = String(investigationPreview || "").trim();
  const parsedEngineerRequest = parseEngineerRequest(pendingQuestion);
  const previewSource = parsedEngineerRequest.issue ? `Issue: ${parsedEngineerRequest.issue}` : pendingQuestion;
  const pendingPreview = previewSource.length > 180 ? `${previewSource.slice(0, 180)}...` : previewSource;
  return {
    ticketId,
    status,
    subject,
    requester,
    pendingQuestion,
    pendingPreview,
    surfaceClass: statusSurfaceClass(status),
  };
}

function renderTicketPoolList(rows) {
  return `
    <section class="ticket-pool-list" role="list">
      ${rows
        .map((ticket) => {
          const item = describeTicketPoolTicket(ticket);
          return `
            <article
              class="ticket-row ${item.surfaceClass}"
              role="button"
              tabindex="0"
              data-ticket-row="true"
              data-ticket-id="${escapeHtml(item.ticketId)}"
              aria-label="Open ticket ${escapeHtml(item.ticketId)} detail"
            >
              <div class="ticket-row-header">
                <div class="ticket-row-title-group">
                  <div class="ticket-row-headline">
                    <p class="ticket-row-kicker mono">${escapeHtml(item.ticketId)}</p>
                    <h3 class="ticket-row-title">${escapeHtml(item.subject)}</h3>
                  </div>
                </div>
                <div class="ticket-row-badges">
                  <span class="status-badge ${statusClass(item.status)}">${escapeHtml(statusLabel(item.status))}</span>
                </div>
              </div>

              <div class="ticket-row-secondary">
                <div class="ticket-row-meta">
                  <span><strong>Requester</strong> ${escapeHtml(item.requester)}</span>
                  <span><strong>Updated</strong> ${escapeHtml(formatDateTime(ticket.updated_at))}</span>
                  <span><strong>Created</strong> ${escapeHtml(formatDateTime(ticket.created_at))}</span>
                  ${
                    item.pendingQuestion
                      ? `
                    <span class="ticket-row-request">
                      <strong>Investigation Update</strong>
                      ${escapeHtml(item.pendingPreview)}
                    </span>
                  `
                      : ""
                  }
                </div>
              </div>
            </article>
          `;
        })
        .join("")}
    </section>
  `;
}

function renderTicketPoolGrid(rows) {
  return `
    <section class="ticket-pool-grid" role="list">
      ${rows
        .map((ticket) => {
          const item = describeTicketPoolTicket(ticket);
          return `
            <article
              class="ticket-pool-card ${item.surfaceClass}"
              role="button"
              tabindex="0"
              data-ticket-row="true"
              data-ticket-id="${escapeHtml(item.ticketId)}"
              aria-label="Open ticket ${escapeHtml(item.ticketId)} detail"
            >
              <div class="ticket-pool-card-top">
                <p class="ticket-row-kicker mono">${escapeHtml(item.ticketId)}</p>
                <div class="ticket-pool-card-badges">
                  <span class="status-badge ${statusClass(item.status)}">${escapeHtml(statusLabel(item.status))}</span>
                </div>
              </div>
              <h3 class="ticket-pool-card-title">${escapeHtml(item.subject)}</h3>
              <div class="ticket-pool-card-meta">
                <span><strong>Requester</strong> ${escapeHtml(item.requester)}</span>
                <span><strong>Updated</strong> ${escapeHtml(formatDateTime(ticket.updated_at))}</span>
                <span><strong>Created</strong> ${escapeHtml(formatDateTime(ticket.created_at))}</span>
              </div>
              ${
                item.pendingQuestion
                  ? `
                <div class="ticket-pool-card-preview">
                  <span class="ticket-pool-card-preview-label">Investigation Update</span>
                  <p>${escapeHtml(item.pendingPreview)}</p>
                </div>
              `
                  : ""
              }
            </article>
          `;
        })
        .join("")}
    </section>
  `;
}

function renderTicketPoolView() {
  const engineerVisibleTickets = tickets.filter((ticket) =>
    isEngineerVisibleStatus(ticket?.status || "open")
  );
  const rows = sortTicketsByRecency(applyTicketFilters(engineerVisibleTickets));
  const viewMode = normalizeTicketPoolViewMode(ticketPoolViewMode);
  const communicatingCount = engineerVisibleTickets.filter(
    (ticket) => normalizeStatusValue(ticket.status) === "communicating"
  ).length;
  const escalatedCount = engineerVisibleTickets.filter(
    (ticket) => normalizeStatusValue(ticket.status) === "escalated"
  ).length;
  const investigatingCount = engineerVisibleTickets.filter(
    (ticket) => normalizeStatusValue(ticket.status) === "investigating"
  ).length;
  const resolvedCount = engineerVisibleTickets.filter(
    (ticket) => normalizeStatusValue(ticket.status) === "resolved"
  ).length;
  const showLoadingState = boardLoading && engineerVisibleTickets.length === 0;
  const emptyStateLabel = statusLabel(selectedPoolStatus).toLowerCase();

  return `
    <section class="ticket-pool-page" data-pool-view-mode="${escapeHtml(viewMode)}">
      <section class="pool-metrics" aria-label="Engineer queue metrics">
        <article class="metric-card">
          <span class="metric-label">Investigating</span>
          <strong>${investigatingCount}</strong>
          <p>Tickets with an open engineer ticket awaiting AI and engineer handling.</p>
        </article>
        <article class="metric-card">
          <span class="metric-label">Escalated</span>
          <strong>${escalatedCount}</strong>
          <p>Tickets where the customer has requested engineer assistance.</p>
        </article>
        <article class="metric-card">
          <span class="metric-label">Communicating</span>
          <strong>${communicatingCount}</strong>
          <p>Tickets currently progressing in the customer-facing AI flow.</p>
        </article>
        <article class="metric-card">
          <span class="metric-label">Resolved</span>
          <strong>${resolvedCount}</strong>
          <p>Tickets already closed on the engineer side.</p>
        </article>
      </section>

      ${
        showLoadingState
          ? `
      <section class="empty-state pool-loading-state" role="status" aria-live="polite" aria-busy="true">
        <span class="loading-spinner pool-loading-spinner" aria-hidden="true"></span>
        <strong>Loading tickets...</strong>
        <p>Fetching the latest engineer queue snapshot.</p>
      </section>
    `
          : rows.length === 0
          ? `<div class="empty-state">No ${escapeHtml(emptyStateLabel)} tickets right now.</div>`
          : viewMode === "grid"
          ? renderTicketPoolGrid(rows)
          : renderTicketPoolList(rows)
      }
    </section>
  `;
}

function resetDetailWorkspaceState() {
  clearStatusComboboxBlurTimer();
  selectedTicketId = null;
  selectedTicket = null;
  selectedTicketSummary = "";
  selectedTicketNextAction = "";
  detailLoading = false;
  tellAiDraft = "";
  investigationReviseMode = false;
  tellAiSubmitting = false;
}

function renderInvestigationDecisionHtml({ draftCustomerReply, controlsDisabled }) {
  return `
    <div class="detail-investigation-draft">
      <p class="detail-investigation-draft-label">Draft Customer Reply</p>
      <div class="detail-investigation-draft-body">${formatMultiline(
        draftCustomerReply || "Draft reply is not ready yet."
      )}</div>
    </div>
    <div class="detail-investigation-inline-actions">
      <button
        type="button"
        class="btn btn-primary"
        data-detail-action="approve-investigation"
        ${controlsDisabled ? "disabled" : ""}
      >Approve Reply</button>
      <button
        type="button"
        class="btn btn-ghost"
        data-detail-action="revise-investigation"
        ${controlsDisabled ? "disabled" : ""}
      >Ask AI to Revise</button>
    </div>
  `;
}

function renderInvestigationComposerHtml({ draft, controlsDisabled, reviseMode }) {
  const placeholder = reviseMode
    ? "Tell Engineer AI what to revise before replying to the customer..."
    : "Share the next technical detail for Engineer AI...";
  const submitLabel = reviseMode ? "Send Revision Note" : "Send Update";

  return `
    <div class="detail-investigation-composer">
      <textarea
        id="detail-investigation-input"
        class="detail-textarea"
        placeholder="${escapeHtml(placeholder)}"
        ${controlsDisabled ? "disabled" : ""}
      >${escapeHtml(draft)}</textarea>
      <div class="detail-investigation-composer-actions">
        <button
          type="button"
          class="btn btn-primary"
          data-detail-action="send-tell-ai"
          ${controlsDisabled ? "disabled" : ""}
        >${escapeHtml(submitLabel)}</button>
      </div>
    </div>
  `;
}

function renderTicketStateActionsHtml({ ticketStatus, controlsDisabled, hasActiveInvestigation }) {
  const status = normalizeStatusValue(ticketStatus);

  if (status === "resolved") {
    return `
      <div class="detail-ticket-actions">
        <button
          type="button"
          class="btn btn-outline"
          data-detail-action="reopen-ticket"
          ${controlsDisabled ? "disabled" : ""}
        >Reopen to Communicating</button>
      </div>
    `;
  }

  const primaryAction =
    status === "investigating"
      ? `
        <button
          type="button"
          class="btn btn-outline"
          data-detail-action="resume-communicating"
          ${controlsDisabled ? "disabled" : ""}
        >Back to Communicating</button>
      `
      : !hasActiveInvestigation
      ? `
        <button
          type="button"
          class="btn btn-outline"
          data-detail-action="start-investigation"
          ${controlsDisabled ? "disabled" : ""}
        >Start Investigation</button>
      `
      : "";

  return `
    <div class="detail-ticket-actions">
      ${primaryAction}
      <button
        type="button"
        class="btn btn-ghost"
        data-detail-action="resolve-ticket"
        ${controlsDisabled ? "disabled" : ""}
      >Resolve Ticket</button>
    </div>
  `;
}

function renderConversationHtml(messages, options = {}) {
  if (!messages.length) {
    return '<div class="empty-state">No messages on this ticket yet.</div>';
  }

  const inlineDecisionIndex = Number.isInteger(options.inlineDecisionIndex)
    ? options.inlineDecisionIndex
    : -1;
  const showInlineConfirmation = Boolean(options.showInlineConfirmation);
  const draftCustomerReply = String(options.draftCustomerReply || "").trim();
  const controlsDisabled = Boolean(options.controlsDisabled);

  return `
    <div class="message-list">
      ${messages
        .map((message, index) => {
          const role = String(message.role || "system").toLowerCase();
          const createdAt = formatDateTime(message.created_at);
          const sentimentLabel =
            role === "customer" ? normalizeMessageSentimentLabel(message.sentiment_label) : "";
          const shouldRenderDecision =
            showInlineConfirmation && inlineDecisionIndex === index && role === "engineer_ai";
          return `
            <article class="message-item ${roleClass(role)}">
              <header>
                <div class="message-header-primary">
                  <span class="message-role">${escapeHtml(roleLabel(role))}</span>
                  ${
                    sentimentLabel
                      ? `<span class="message-sentiment-pill sentiment-${escapeHtml(sentimentLabel)}">${escapeHtml(sentimentLabel)}</span>`
                      : ""
                  }
                </div>
                <span class="message-time">${escapeHtml(createdAt)}</span>
              </header>
              <div class="message-content">${formatMultiline(String(message.content || ""))}</div>
              ${
                shouldRenderDecision
                  ? renderInvestigationDecisionHtml({ draftCustomerReply, controlsDisabled })
                  : ""
              }
              ${buildMessageReferences(message)}
            </article>
          `;
        })
        .join("")}
    </div>
  `;
}

function renderInvestigationHistoryHtml(historyItems) {
  if (!historyItems.length) {
    return '<p class="request-record-empty">No prior engineer ticket cycles yet.</p>';
  }

  return `
    <div class="request-record-list">
      ${historyItems
        .map((item) => {
          const stateText = investigationStateLabel(item?.state);
          const reason = String(item?.trigger_reason || "").trim();
          const source = String(item?.trigger_source || "").trim();
          const updatedAt = formatDateTime(item?.updated_at || item?.closed_at || item?.opened_at);
          const draft = String(item?.draft_customer_reply || "").trim();
          return `
            <article class="request-record-item">
              <header>
                <span class="request-record-status ${String(item?.state || "").toLowerCase() === "closed" ? "record-status-answer" : "record-status-replied"}">${escapeHtml(stateText)}</span>
                <span class="request-record-time">${escapeHtml(updatedAt)}</span>
              </header>
              ${
                reason || source
                  ? `<p class="request-record-detail">${escapeHtml(
                      [reason, source].filter(Boolean).join(" · ")
                    )}</p>`
                  : ""
              }
              ${
                draft
                  ? `<p class="request-record-meta">Last draft: ${escapeHtml(draft.slice(0, 220))}</p>`
                  : ""
              }
            </article>
          `;
        })
        .join("")}
    </div>
  `;
}

function renderTicketDetailView() {
  if (!selectedTicketId) {
    return '<div class="empty-state">Select a ticket to open the active workspace.</div>';
  }

  if (detailLoading) {
    return `
      <section class="ticket-workspace">
        <section class="detail-loading" role="status" aria-live="polite" aria-busy="true">
          <span class="loading-spinner" aria-hidden="true"></span>
          <p>Loading ticket workspace...</p>
        </section>
      </section>
    `;
  }

  if (!selectedTicket) {
    return '<div class="empty-state">Ticket detail is unavailable.</div>';
  }

  const ticket = selectedTicket;
  const ticketId = String(ticket.ticket_id || selectedTicketId || "-");
  const status = normalizeStatusValue(ticket.status || "open");
  const requester = String(ticket.requester || ticket.customer_id || "Unknown");
  const activeInvestigation = getActiveInvestigation(ticket);
  const displayInvestigation = getDisplayInvestigation(ticket);
  const investigationState = String(displayInvestigation?.state || "active").toLowerCase();
  const investigationPreview = latestInvestigationUpdate(ticket);
  const investigationMessages = Array.isArray(displayInvestigation?.messages)
    ? displayInvestigation.messages
    : investigationPreview
    ? [
        {
          role: "system",
          content: investigationPreview,
          created_at: ticket.updated_at || ticket.created_at || new Date().toISOString(),
        },
      ]
    : [];
  const showInlineConfirmation =
    Boolean(activeInvestigation) &&
    investigationState === "awaiting_confirmation" &&
    !investigationReviseMode;
  const showInvestigationComposer =
    Boolean(activeInvestigation) &&
    (investigationState === "active" || investigationReviseMode);
  const draftCustomerReply = String(displayInvestigation?.draft_customer_reply || "").trim();
  const controlsDisabled = tellAiSubmitting;
  const messages = Array.isArray(ticket.messages) ? ticket.messages : [];

  return `
    <section class="ticket-workspace">
      <header class="workspace-header">
        <div class="workspace-header-top">
          <div class="workspace-header-toolbar-start">
            <button class="btn btn-ghost" type="button" data-detail-action="back-to-pool">Back to Pool</button>
            <div class="workspace-header-badges">
              <span class="status-badge ${statusClass(status)}">${escapeHtml(statusLabel(status))}</span>
            </div>
          </div>
          <div class="workspace-header-toolbar-end">
            <span class="workspace-eyebrow">Ticket #${escapeHtml(ticketId)}</span>
            <div class="workspace-header-actions">
              <button class="btn btn-outline" type="button" data-detail-action="refresh-ticket">Sync Ticket</button>
            </div>
          </div>
        </div>

        <div class="workspace-header-main">
          <div class="workspace-header-copy">
            <h2 class="workspace-ticket-title">${escapeHtml(
              String(ticket.subject || "(No subject)")
            )}</h2>
            <div class="workspace-header-meta">
              <span>Requester ${escapeHtml(requester)}</span>
              <span>Created ${escapeHtml(formatDateTime(ticket.created_at))}</span>
              <span>Updated ${escapeHtml(formatDateTime(ticket.updated_at))}</span>
            </div>
          </div>
        </div>
      </header>

      <div class="workspace-layout">
        <section class="panel-card conversation-panel">
          <div class="panel-card-head">
            <div>
              <p class="panel-card-kicker">Engineer Ticket</p>
              <h3 class="panel-card-title">Engineer Ticket Thread</h3>
              ${
                displayInvestigation
                  ? `<p class="mode-switch-hint">State: ${escapeHtml(
                      investigationStateLabel(displayInvestigation.state)
                    )}</p>`
                  : ""
              }
            </div>
          </div>
          ${
            investigationMessages.length
              ? renderConversationHtml(investigationMessages, {
                  inlineDecisionIndex: showInlineConfirmation ? investigationMessages.length - 1 : -1,
                  showInlineConfirmation,
                  draftCustomerReply,
                  controlsDisabled,
                })
              : '<div class="empty-state">No open engineer ticket yet.</div>'
          }
          ${
            showInvestigationComposer
              ? renderInvestigationComposerHtml({
                  draft: tellAiDraft,
                  controlsDisabled,
                  reviseMode: investigationReviseMode,
                })
              : ""
          }
        </section>

        <aside class="insight-panel">
          <section class="panel-card">
            <div class="panel-card-head">
              <div>
                <p class="panel-card-kicker">Customer Timeline</p>
                <h3 class="panel-card-title">Customer Timeline</h3>
              </div>
            </div>
            ${renderConversationHtml(messages)}
          </section>

          <section class="panel-card">
            <div class="panel-card-head">
              <div>
                <p class="panel-card-kicker">AI Summary</p>
                <h3 class="panel-card-title">Next Action Needed</h3>
              </div>
            </div>
            <p class="summary-text">${formatMultiline(selectedTicketSummary || "Generating summary...")}</p>
            <p class="summary-next-title">Next Action Needed</p>
            <p class="summary-text">${formatMultiline(
              selectedTicketNextAction || "Analyzing next action needed..."
            )}</p>
            ${renderTicketStateActionsHtml({
              ticketStatus: status,
              controlsDisabled,
              hasActiveInvestigation: Boolean(activeInvestigation),
            })}
          </section>
        </aside>
      </div>
    </section>
  `;
}

function focusInvestigationComposerInput(retries = 8) {
  const input = document.getElementById("detail-investigation-input");
  if (!(input instanceof HTMLTextAreaElement)) {
    if (retries > 0) {
      setTimeout(() => focusInvestigationComposerInput(retries - 1), 90);
    }
    return;
  }

  input.scrollIntoView({ behavior: "smooth", block: "center", inline: "nearest" });
  try {
    input.focus({ preventScroll: true });
  } catch {
    input.focus();
  }
  const end = input.value.length;
  input.setSelectionRange(end, end);
}

function renderTickets() {
  if (!workspaceRegionEl || routeState.view !== "pool") {
    return;
  }
  renderWorkspaceChrome();
  workspaceRegionEl.innerHTML = renderTicketPoolView();
}

function showWorkspaceFeedback(title, message, { error = false } = {}) {
  renderWorkspaceChrome();
  if (!workspaceRegionEl) {
    return;
  }
  workspaceRegionEl.innerHTML = `
    <section class="workspace-feedback ${error ? "workspace-feedback-error" : ""}" role="${
      error ? "alert" : "status"
    }">
      <strong>${escapeHtml(title)}</strong>
      <p>${escapeHtml(message)}</p>
    </section>
  `;
}

function closeTicketDetail() {
  resetDetailWorkspaceState();
  const hashChanged = navigate("/tickets");
  if (!hashChanged) {
    renderWorkspace();
  }
}

function renderTicketDetail() {
  if (!workspaceRegionEl || routeState.view !== "detail") {
    return;
  }
  renderWorkspaceChrome();
  workspaceRegionEl.innerHTML = renderTicketDetailView();
}

function redirectOpenTicketToPool() {
  setSelectedPoolStatus("investigating", { render: false });
  resetDetailWorkspaceState();
  routeState.view = "pool";
  routeState.ticketId = null;
  window.location.hash = "#/tickets";
  showWorkspaceFeedback(
    "Client Workspace Only",
    "This ticket is only visible in the client workspace."
  );
}

async function refreshSelectedSummary(options = {}) {
  const { silent = true } = options;
  if (!selectedTicketId || !selectedTicket) {
    return;
  }

  const requestedTicketId = selectedTicketId;
  const cacheKey = summaryCacheKey(selectedTicket);
  const cached = ticketSummaryCache.get(requestedTicketId);
  if (cached && cached.cacheKey === cacheKey) {
    selectedTicketSummary = cached.summary;
    selectedTicketNextAction = cached.nextAction;
    renderTicketDetail();
    return;
  }

  try {
    const payload = await fetchJson(
      `/api/engineer/tickets/${encodeURIComponent(requestedTicketId)}/summary`
    );
    if (selectedTicketId !== requestedTicketId) {
      return;
    }
    const summary = String(payload?.summary || "").trim();
    const nextAction = String(payload?.next_action_needed || "").trim();
    if (!summary || !nextAction) {
      const fallback = buildLocalSummaryFallback(selectedTicket);
      selectedTicketSummary = fallback.summary;
      selectedTicketNextAction = fallback.nextAction;
      renderTicketDetail();
      return;
    }
    selectedTicketSummary = summary;
    selectedTicketNextAction = nextAction;
    ticketSummaryCache.set(requestedTicketId, {
      cacheKey,
      summary,
      nextAction,
    });
    renderTicketDetail();
  } catch (error) {
    if (selectedTicketId !== requestedTicketId) {
      return;
    }
    const fallback = buildLocalSummaryFallback(selectedTicket);
    selectedTicketSummary = fallback.summary;
    selectedTicketNextAction = fallback.nextAction;
    renderTicketDetail();
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
    selectedTicketNextAction = "";
    detailLoading = false;
    renderTicketDetail();
    return;
  }

  const requestedTicketId = selectedTicketId;
  if (showLoading) {
    detailLoading = true;
    renderTicketDetail();
  }

  try {
    const payload = await fetchJson(`/api/engineer/tickets/${encodeURIComponent(requestedTicketId)}`);
    if (selectedTicketId !== requestedTicketId) {
      return;
    }
    const nextTicket = payload.ticket || null;
    if (nextTicket && !isEngineerVisibleStatus(nextTicket.status || "open")) {
      redirectOpenTicketToPool();
      return;
    }
    selectedTicket = nextTicket;
    const refreshedInvestigation = getActiveInvestigation(selectedTicket);
    if (String(refreshedInvestigation?.state || "").toLowerCase() !== "awaiting_confirmation") {
      investigationReviseMode = false;
    }
    if (selectedTicket) {
      selectedTicketSummary = "Generating AI summary for this ticket...";
      selectedTicketNextAction = "Determining next action needed...";
    } else {
      selectedTicketSummary = "";
      selectedTicketNextAction = "";
      investigationReviseMode = false;
    }
    detailLoading = false;
    renderTicketDetail();
    refreshSelectedSummary({ silent: true }).catch(() => {
      // Keep local summary if async summary fails.
    });
  } catch (error) {
    if (selectedTicketId !== requestedTicketId) {
      return;
    }
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
  const normalizedId = String(ticketId || "").trim();
  if (!normalizedId) {
    return;
  }
  if (selectedTicketId !== normalizedId) {
    resetDetailWorkspaceState();
    selectedTicketId = normalizedId;
    detailLoading = true;
  }
  routeState.view = "detail";
  routeState.ticketId = normalizedId;
  renderTicketDetail();
  const hashChanged = navigate(`/tickets/${normalizedId}`);
  if (!hashChanged) {
    await refreshSelectedTicket({ showLoading: true, silent: true });
  }
}

async function loadTickets(options = {}) {
  const { refreshDetail = true, showLoading = tickets.length === 0 } = options;
  const shouldShowPoolLoading = showLoading && routeState.view === "pool" && tickets.length === 0;
  if (shouldShowPoolLoading) {
    boardLoading = true;
    renderTickets();
  }
  const params = new URLSearchParams({ status: "all" });
  try {
    const payload = await fetchJson(`/api/engineer/tickets?${params.toString()}`);
    tickets = Array.isArray(payload.tickets) ? payload.tickets : [];
    boardLoading = false;
    parseRoute();
    if (routeState.view === "pool") {
      renderTickets();
    } else {
      renderWorkspaceChrome();
    }

    if (refreshDetail && selectedTicketId) {
      await refreshSelectedTicket({ silent: true });
    }
  } catch (error) {
    boardLoading = false;
    throw error;
  }
}

function showBoardError(message) {
  boardLoading = false;
  showWorkspaceFeedback("Workspace Unavailable", message, { error: true });
}

async function updateTicketStatus(ticketId, action) {
  await fetchJson(`/api/tickets/${encodeURIComponent(ticketId)}/action`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ action, engineer_id: ENGINEER_ID }),
  });
}

async function submitInvestigationMessage(ticketId, messageText) {
  const cleaned = String(messageText || "").trim();
  if (!cleaned) {
    window.alert("Please enter the next technical detail for Engineer AI.");
    return;
  }
  await fetchJson(`/api/engineer/tickets/${encodeURIComponent(ticketId)}/investigation/messages`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message: cleaned, engineer_id: ENGINEER_ID }),
    timeoutMs: TELL_AI_FETCH_TIMEOUT_MS,
  });
}

async function submitInvestigationConfirmation(ticketId, decision, note = "") {
  await fetchJson(`/api/engineer/tickets/${encodeURIComponent(ticketId)}/investigation/confirmation`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      decision: String(decision || "approve").toLowerCase(),
      note: String(note || "").trim() || undefined,
      engineer_id: ENGINEER_ID,
    }),
    timeoutMs: TELL_AI_FETCH_TIMEOUT_MS,
  });
}

async function handleTableClick(event) {
  const row = getTicketRowTarget(event.target);
  if (row) {
    const ticketId = String(row.dataset.ticketId || "").trim();
    if (!ticketId) {
      return;
    }
    await openTicketDetail(ticketId);
    return;
  }

  const button = event.target.closest("button.action-btn");
  if (!button || !button.dataset) {
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
    await updateTicketStatus(ticketId, action);
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

const ROW_INTERACTIVE_SELECTOR = [
  "button",
  "a",
  "input",
  "select",
  "textarea",
  "summary",
  '[role="button"]',
  '[role="link"]',
].join(", ");

function getTicketRowTarget(target) {
  if (!target || typeof target.closest !== "function") {
    return null;
  }
  const row = target.closest("[data-ticket-row]");
  if (!row) {
    return null;
  }
  const interactive = target.closest(ROW_INTERACTIVE_SELECTOR);
  if (interactive && interactive !== row) {
    return null;
  }
  return row;
}

function handleTableKeydown(event) {
  if (event.key !== "Enter" && event.key !== " " && event.key !== "Spacebar") {
    return;
  }
  const row = getTicketRowTarget(event.target);
  if (!row) {
    return;
  }
  const ticketId = String(row.dataset.ticketId || "").trim();
  if (!ticketId) {
    return;
  }
  event.preventDefault();
  const openResult = openTicketDetail(ticketId);
  if (openResult && typeof openResult.catch === "function") {
    openResult.catch((error) => {
      showBoardError(`Operation failed: ${error.message}`);
    });
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

  if (action === "back-to-pool") {
    closeTicketDetail();
    return;
  }

  if (action === "refresh-ticket") {
    button.disabled = true;
    try {
      await loadTickets({ refreshDetail: false });
      await refreshSelectedTicket({ silent: true, showLoading: true });
    } catch (error) {
      window.alert(`Sync failed: ${error.message}`);
    } finally {
      button.disabled = false;
    }
    return;
  }

  if (action === "start-investigation" || action === "resume-communicating" || action === "resolve-ticket" || action === "reopen-ticket") {
    const actionMap = {
      "start-investigation": "investigate",
      "resume-communicating": "processing",
      "resolve-ticket": "resolved",
      "reopen-ticket": "reopen",
    };
    const nextPoolStatusMap = {
      "start-investigation": "investigating",
      "resume-communicating": "communicating",
      "resolve-ticket": "resolved",
      "reopen-ticket": "communicating",
    };
    button.disabled = true;
    try {
      await updateTicketStatus(selectedTicketId, actionMap[action]);
      setSelectedPoolStatus(nextPoolStatusMap[action], { render: false });
      investigationReviseMode = false;
      tellAiDraft = "";
      await loadTickets({ refreshDetail: false });
      await refreshSelectedTicket({ silent: true });
    } catch (error) {
      window.alert(`Ticket action failed: ${error.message}`);
      await refreshSelectedTicket({ silent: true });
    } finally {
      button.disabled = false;
    }
    return;
  }

  if (action === "send-tell-ai") {
    if (!getActiveInvestigation(selectedTicket)) {
      window.alert("Start an investigation before sending a note to Engineer AI.");
      return;
    }
    const cleaned = tellAiDraft.trim();
    if (!cleaned) {
      window.alert("Please enter the next message for Engineer AI.");
      return;
    }
    button.disabled = true;
    tellAiSubmitting = true;
    renderTicketDetail();
    try {
      if (getActiveInvestigation(selectedTicket)) {
        if (investigationReviseMode) {
          await submitInvestigationConfirmation(selectedTicketId, "revise", cleaned);
          investigationReviseMode = false;
        } else {
          await submitInvestigationMessage(selectedTicketId, cleaned);
        }
      }
      tellAiDraft = "";
      await loadTickets({ refreshDetail: false });
      await refreshSelectedTicket({ silent: true });
    } catch (error) {
      window.alert(`Engineer AI update failed: ${error.message}`);
      await refreshSelectedTicket({ silent: true });
    } finally {
      tellAiSubmitting = false;
      renderTicketDetail();
      button.disabled = false;
    }
    return;
  }

  if (action === "approve-investigation") {
    if (!getActiveInvestigation(selectedTicket)) {
      return;
    }
    button.disabled = true;
    tellAiSubmitting = true;
    renderTicketDetail();
    try {
      await submitInvestigationConfirmation(selectedTicketId, "approve");
      investigationReviseMode = false;
      tellAiDraft = "";
      await loadTickets({ refreshDetail: false });
      await refreshSelectedTicket({ silent: true });
    } catch (error) {
      window.alert(`Approve reply failed: ${error.message}`);
      await refreshSelectedTicket({ silent: true });
    } finally {
      tellAiSubmitting = false;
      renderTicketDetail();
      button.disabled = false;
    }
    return;
  }

  if (action === "revise-investigation") {
    if (!getActiveInvestigation(selectedTicket)) {
      return;
    }
    investigationReviseMode = true;
    tellAiDraft = "";
    renderTicketDetail();
    focusInvestigationComposerInput();
    return;
  }
}

function handleDetailInput(event) {
  const tellAiInput = event.target.closest("#detail-investigation-input");
  if (tellAiInput) {
    tellAiDraft = String(tellAiInput.value || "");
  }
}

function handleDetailFocusIn() {}

function handleDetailFocusOut() {}

function handleDetailKeydown() {}

async function handleDetailChange() {}

function handleFilterControlsClick(event) {
  const viewToggleButton = event.target.closest("button[data-pool-view-option]");
  if (viewToggleButton) {
    applyTicketPoolViewMode(viewToggleButton.dataset.poolViewOption);
    return;
  }

  const actionButton = event.target.closest("[data-filter-action]");
  if (actionButton) {
    const action = actionButton.dataset.filterAction;
    const key = String(actionButton.dataset.filterKey || "").trim().toLowerCase();
    if (!FILTER_KEYS.includes(key)) {
      return;
    }
    const config = filterComboboxConfig[key];
    if (!config || config.disabled) {
      return;
    }

    if (action === "toggle") {
      const isOpen = Boolean(filterComboboxState[key]?.open);
      if (isOpen) {
        closeFilterCombobox(key, { clearQuery: true });
        renderFilterControls();
      } else if (openFilterCombobox(key)) {
        renderFilterControls();
        focusHeaderFilterInput(key);
      }
      return;
    }

    if (action === "select") {
      const nextValue = String(actionButton.dataset.value || "all").toLowerCase();
      const normalizedValue = normalizeFilterValue(key, nextValue);
      if (config.strictSelection && !headerFilterOptions(key).some((option) => option.value === normalizedValue)) {
        return;
      }
      applyHeaderFilterValue(key, normalizedValue);
      closeAllHeaderFilterComboboxes({ render: false });
      renderFilterControls();
    }
    return;
  }

  const input = event.target.closest("[data-filter-input]");
  if (!input) {
    return;
  }
  const key = String(input.dataset.filterInput || "").trim().toLowerCase();
  if (!FILTER_KEYS.includes(key)) {
    return;
  }
  if (openFilterCombobox(key)) {
    renderFilterControls();
    focusHeaderFilterInput(key);
  }
}

function handleFilterControlsInput(event) {
  const input = event.target.closest("[data-filter-input]");
  if (!input) {
    return;
  }
  const key = String(input.dataset.filterInput || "").trim().toLowerCase();
  if (!FILTER_KEYS.includes(key)) {
    return;
  }

  const config = filterComboboxConfig[key];
  if (!config || config.disabled || config.searchable === false) {
    return;
  }

  filterComboboxState[key].query = String(input.value || "");
  openFilterCombobox(key);
  renderFilterControls();
  focusHeaderFilterInput(key);
}

function handleFilterControlsFocusIn(event) {
  const root = event.target.closest("[data-filter-root]");
  if (root) {
    const key = String(root.dataset.filterRoot || "").trim().toLowerCase();
    if (FILTER_KEYS.includes(key)) {
      clearFilterBlurTimer(key);
    }
  }

  const input = event.target.closest("[data-filter-input]");
  if (!input) {
    return;
  }
  const key = String(input.dataset.filterInput || "").trim().toLowerCase();
  if (!FILTER_KEYS.includes(key)) {
    return;
  }
  if (openFilterCombobox(key)) {
    renderFilterControls();
    focusHeaderFilterInput(key);
  }
}

function handleFilterControlsFocusOut(event) {
  const root = event.target.closest("[data-filter-root]");
  if (!root) {
    return;
  }
  const key = String(root.dataset.filterRoot || "").trim().toLowerCase();
  if (!FILTER_KEYS.includes(key)) {
    return;
  }
  closeFilterComboboxWithDelay(key);
}

function handleFilterControlsKeydown(event) {
  const input = event.target.closest("[data-filter-input]");
  if (!input) {
    return;
  }
  const key = String(input.dataset.filterInput || "").trim().toLowerCase();
  if (!FILTER_KEYS.includes(key)) {
    return;
  }

  if (event.key === "Escape") {
    event.preventDefault();
    event.stopPropagation();
    closeAllHeaderFilterComboboxes({ render: true });
    return;
  }

  if (event.key === "ArrowDown") {
    if (openFilterCombobox(key)) {
      event.preventDefault();
      renderFilterControls();
      focusHeaderFilterInput(key);
    }
    return;
  }

  if (event.key !== "Enter" || !filterComboboxState[key]?.open) {
    return;
  }

  event.preventDefault();
  const config = filterComboboxConfig[key];
  const options = headerFilterOptions(key);
  const filteredOptions =
    config?.searchable === false ? options : filterComboboxOptions(options, filterComboboxState[key].query);
  if (filteredOptions.length === 0) {
    return;
  }

  const nextValue = normalizeFilterValue(key, filteredOptions[0].value);
  applyHeaderFilterValue(key, nextValue);
  closeAllHeaderFilterComboboxes({ render: false });
  renderFilterControls();
}

function handleDocumentPointerDown(event) {
  if (!filterControlsEl || filterControlsEl.contains(event.target) || !isHeaderFilterOpen()) {
    return;
  }
  closeAllHeaderFilterComboboxes({ render: true });
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
  parseRoute();
  boardLoading = routeState.view === "pool" && tickets.length === 0;
  renderWorkspace();
  await detectStorageMode();
  setRealtimeStatus("Realtime: connecting...");
  try {
    await loadTickets({ refreshDetail: false });
    await syncRouteToWorkspace({ silent: true, showLoading: true });
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

function handleLocalLogout() {
  setAuthenticated(false);
  storageMode = "unknown";
  boardLoading = false;
  closeSocket();
  tickets = [];
  selectedPoolStatus = "investigating";
  closeAllHeaderFilterComboboxes({ render: false });
  resetDetailWorkspaceState();
  window.location.hash = "#/tickets";
  parseRoute();
  renderFilterControls();
  renderWorkspace();
  toggleScreens();
  resetLoginForm();
}

hydrateTicketPoolViewMode();

async function handleLogoutClick() {
  if (logoutLoading) {
    return;
  }
  logoutLoading = true;
  renderHeaderUserControls();
  try {
    await fetchJson("/api/v1/auth/logout", { method: "POST" });
    handleLocalLogout();
    window.location.assign("/login");
    window.location.reload();
  } finally {
    logoutLoading = false;
    renderHeaderUserControls();
  }
}

loginFormEl.addEventListener("submit", (event) => {
  handleLoginSubmit(event).catch((error) => {
    loginErrorEl.textContent = `Login failed: ${error.message}`;
  });
});

filterControlsEl?.addEventListener("click", handleFilterControlsClick);
filterControlsEl?.addEventListener("input", handleFilterControlsInput);
filterControlsEl?.addEventListener("focusin", handleFilterControlsFocusIn);
filterControlsEl?.addEventListener("focusout", handleFilterControlsFocusOut);
filterControlsEl?.addEventListener("keydown", handleFilterControlsKeydown);
workspaceRegionEl?.addEventListener("click", (event) => {
  if (event.target.closest("button[data-detail-action]")) {
    handleDetailClick(event).catch((error) => {
      window.alert(`Operation failed: ${error.message}`);
    });
    return;
  }

  handleTableClick(event).catch((error) => {
    showBoardError(`Operation failed: ${error.message}`);
  });
});
workspaceRegionEl?.addEventListener("input", handleDetailInput);
workspaceRegionEl?.addEventListener("focusin", handleDetailFocusIn);
workspaceRegionEl?.addEventListener("focusout", handleDetailFocusOut);
workspaceRegionEl?.addEventListener("keydown", (event) => {
  handleTableKeydown(event);
  handleDetailKeydown(event);
});
workspaceRegionEl?.addEventListener("change", (event) => {
  handleDetailChange(event).catch((error) => {
    window.alert(`Operation failed: ${error.message}`);
  });
});
railNavEl?.addEventListener("click", (event) => {
  const button = event.target.closest("[data-nav-action]");
  const statusButton = event.target.closest("[data-nav-status]");
  if (statusButton) {
    setSelectedPoolStatus(String(statusButton.dataset.navStatus || "investigating"), { render: false });
    navigate("/tickets");
    return;
  }
  if (!button) {
    return;
  }
  const action = String(button.dataset.navAction || "").trim();
  if (action === "go-detail" && selectedTicketId) {
    navigate(`/tickets/${encodeURIComponent(selectedTicketId)}`);
  }
});
window.addEventListener("hashchange", () => {
  if (!isAuthenticated()) {
    parseRoute();
    return;
  }
  syncRouteToWorkspace({ silent: true, showLoading: true }).catch((error) => {
    showBoardError(`Route update failed: ${error.message}`);
  });
});
document.addEventListener("pointerdown", handleDocumentPointerDown);

renderHeaderUserControls();
renderFilterControls();

if (isAuthenticated()) {
  enterBoard().catch((error) => {
    showBoardError(`Failed to initialize engineer workspace: ${error.message}`);
  });
} else {
  toggleScreens();
  parseRoute();
  setRealtimeStatus("Realtime: signed out");
}
