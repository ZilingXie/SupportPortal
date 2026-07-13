const WORKSPACE_AUTH_KEY = "supportportal_workspace_selected_engineer";
const WORKSPACE_SHIFT_KEY = "supportportal_workspace_daily_shift";
const WORKSPACE_ACTIVE_KEY = "supportportal_workspace_active";
const WORKSPACE_BREAK_AFTER_CASE_KEY = "supportportal_workspace_break_after_case";
const WORKSPACE_CASE_SLA_STARTED_AT_KEY = "supportportal_workspace_case_sla_started_at";
const UTC8_OFFSET_MS = 8 * 60 * 60 * 1000;
const WORKSPACE_CASE_SLA_MS = 3 * 60 * 60 * 1000;
const AGORA_STATUS_PAGE_URL = "https://status.agora.io/";
const SERVICE_EVENTS_ENDPOINT = "/api/client/service-events";
const SERVICE_EVENTS_REFRESH_INTERVAL_MS = 5 * 60 * 1000;
const DEMO_ENGINEERS = [
  { id: "Jack", name: "Jack", role: "Tier One Engineer", initials: "J" },
  { id: "Maya", name: "Maya", role: "Tier One Engineer", initials: "M" },
  { id: "Leo", name: "Leo", role: "Tier One Engineer", initials: "L" },
];
const WEEKLY_KNOWN_ISSUES = [
  {
    title: "RTC black screen reports in Chromium 124",
    severity: "High",
    owner: "RTC Client",
    surface: "Video rendering",
    brief: "Several Web SDK escalations mention remote video rendering a black frame after tab restore.",
  },
  {
    title: "Webhook replay latency for billing exports",
    severity: "Medium",
    owner: "Billing Ops",
    surface: "Replay pipeline",
    brief: "Zendesk billing replay export jobs can lag behind the customer reply stream during peak ingest windows.",
  },
  {
    title: "iOS screen share permission prompt confusion",
    severity: "Low",
    owner: "Mobile SDK",
    surface: "Screen share UX",
    brief: "Customers often miss the iOS broadcast picker confirmation and report screen share as stuck.",
  },
];
const DEFAULT_SHIFT = {
  start: "00:00",
  end: "23:59",
};
const ENGINEER_DISPLAY_NAME = "engineer";
const ENGINEER_AI_DISPLAY_NAME = "Sid";
const PUBLIC_ASSISTANT_DISPLAY_NAME = "Sid";
const CASE_BUDDY_CURRENT_ISSUE_FALLBACK = "The current issue summary is still being clarified.";
const CASE_BUDDY_ACTION_FALLBACK = "Review the current evidence and decide the next technical check.";
const SharedComposer = globalThis.SupportPortalComposer || {};
const renderMarkdownMessage =
  SharedComposer.renderMarkdownMessage ||
  ((value) => String(value ?? "").replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll("\n", "<br>"));
const buildDefaultComposerToolbarState =
  SharedComposer.buildDefaultComposerToolbarState ||
  (() => ({
    bold: false,
    italic: false,
    list: false,
    codeBlock: false,
  }));
const renderSharedComposerFormattingToolbarButtons =
  SharedComposer.renderComposerFormattingToolbarButtons || (() => "");
const serializeRichComposerHtmlToMarkdown =
  SharedComposer.serializeRichComposerHtmlToMarkdown || ((value) => String(value || ""));
const buildRichComposerHtmlFromMarkdown =
  SharedComposer.buildRichComposerHtmlFromMarkdown || ((value) => String(value || ""));
const normalizeRichComposerHtmlString =
  SharedComposer.normalizeRichComposerHtmlString || ((value) => String(value || ""));
const captureComposerPreservationState =
  SharedComposer.captureComposerPreservationState || (() => null);
const restoreComposerPreservationState =
  SharedComposer.restoreComposerPreservationState || (() => {});
const restoreSharedRichComposerSelectionBookmark =
  SharedComposer.restoreRichComposerSelectionBookmark || (() => false);
const isRichTextComposerElement =
  SharedComposer.isRichTextComposerElement ||
  ((element) =>
    Boolean(
      element &&
        typeof element === "object" &&
        typeof element.focus === "function" &&
        typeof element.innerHTML === "string" &&
        typeof element.getAttribute === "function"
    ));
const isComposerElementDisabled =
  SharedComposer.isComposerElementDisabled ||
  ((element) =>
    !element || String(element.getAttribute?.("contenteditable") || "").toLowerCase() === "false");
const getRichComposerSelectionContext =
  SharedComposer.getRichComposerSelectionContext || buildDefaultComposerToolbarState;
const applySharedComposerToolbarStateToButtons =
  SharedComposer.applyComposerToolbarStateToButtons || (() => {});
const placeSharedComposerCaretAtEnd = SharedComposer.placeComposerCaretAtEnd || (() => false);

const loginScreenEl = document.getElementById("login-screen");
const engineerScreenEl = document.getElementById("engineer-screen");
const workspaceRootEl = document.getElementById("workspace-root");
const loginFormEl = document.getElementById("login-form");
const loginErrorEl = document.getElementById("login-error");
const filterControlsEl = document.getElementById("filter-controls");
const headerUserControlsEl = document.getElementById("header-user-controls");
const wsStatusEl = document.getElementById("ws-status");
const workspaceRegionEl = document.getElementById("workspace-region");
const workspaceTitleEl = document.getElementById("workspace-title");
const workspaceSubtitleEl = document.getElementById("workspace-subtitle");
const railNavEl = document.getElementById("rail-nav");
const workspaceAssignmentSidebarEl = document.getElementById("workspace-assignment-sidebar");

let tickets = [];
let boardLoading = false;
let selectedTicketId = null;
let selectedTicket = null;
let detailLoading = false;
let tellAiDraft = "";
let tellAiDraftRichHtml = "";
let investigationReviseMode = false;
// Multi-agent workspace is a per-ticket, per-session explicit mode. Clicking
// the Investigating badge runs Plan / Execute / Review and shows the result in
// the right-side insight panel only.
let multiAgentWorkspaceTicketId = null;
let multiAgentRunLoadingTicketId = null;
let multiAgentRunError = "";
let tellAiSubmitting = false;
let hitlFeedbackLoading = false;
let hitlFeedbackRequestSeq = 0;
let investigationComposerToolbarState = buildDefaultComposerToolbarState();
let engineerComposerRuntime = null;
let localInvestigationThreadState = null;
let localInvestigationMessageSequence = 0;
const detailRefreshState = {
  ticketId: null,
  requestSeq: 0,
  mutationEpoch: 0,
  inFlightController: null,
};
const ticketLoadState = {
  inFlightPromise: null,
  queuedOptions: null,
};
let socket = null;
let heartbeatTimer = null;
let reconnectTimer = null;
let storageMode = "unknown";
let changeEngineerLoading = false;
const routeState = {
  view: "pool",
  ticketId: null,
};
let selectedEngineerId = "";
let selectedEngineerCandidate = DEMO_ENGINEERS[0].id;
let workspaceShift = DEFAULT_SHIFT;
let workspaceActive = false;
let workspaceBreakAfterCase = false;
let readyTransitionActive = false;
let workspaceSlaCountdownTimer = null;
let workspaceRealtimeStatusText = "Realtime: connecting...";
let workspaceServiceEventsState = buildDefaultWorkspaceServiceEventsState();

const ENGINEER_POOL_STATUSES = ["investigating", "escalated", "communicating", "resolved"];
const POOL_STATUS_RANK = {
  investigating: 4,
  escalated: 3,
  communicating: 2,
  resolved: 1,
  open: 0,
};
const DEFAULT_FETCH_TIMEOUT_MS = 25000;
const INVESTIGATION_APPROVE_FETCH_TIMEOUT_MS = DEFAULT_FETCH_TIMEOUT_MS;
const INVESTIGATION_AI_TURN_FETCH_TIMEOUT_MS = 100000;
const INVESTIGATION_TIMEOUT_RECOVERY_WINDOW_MS = 15000;
const INVESTIGATION_TIMEOUT_RECOVERY_POLL_MS = 1500;
const TICKET_POOL_VIEW_STORAGE_KEY = "engineer_ticket_pool_view_mode";
const DETAIL_THREAD_PANE = "thread";
const DETAIL_TIMELINE_PANE = "timeline";

const FILTER_KEYS = [];
const FILTER_BLUR_DELAY_MS = 140;
const filterValues = {};
const filterComboboxState = {};
let ticketPoolViewMode = "list";
let selectedPoolStatus = "investigating";
const filterComboboxConfig = {};
const detailPaneStateByKey = {};
const detailPendingScrollRequestByKey = {};
let detailScheduledScrollPlans = null;
let detailScheduledScrollJobId = 0;

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function readStorage(key, fallback) {
  try {
    const raw = localStorage.getItem(key);
    if (raw === null || raw === undefined || raw === "") {
      return fallback;
    }
    try {
      return JSON.parse(raw);
    } catch {
      return raw;
    }
  } catch {
    return fallback;
  }
}

function writeStorage(key, value) {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch {
    // Local demo state is best-effort; real case handling stays server-backed.
  }
}

function removeStorage(key) {
  try {
    localStorage.removeItem(key);
  } catch {
    // Ignore storage failures in private browsing or locked-down contexts.
  }
}

function normalizeSingleLineText(value) {
  return String(value || "")
    .replace(/\s+/g, " ")
    .trim();
}

function buildDefaultWorkspaceServiceEventsState() {
  return {
    loadState: "idle",
    items: [],
    statusPageUrl: AGORA_STATUS_PAGE_URL,
    fetchedAt: "",
    lastRequestedAtMs: 0,
  };
}

function normalizeWorkspaceServiceEventItem(item, index) {
  const title = normalizeSingleLineText(item?.title);
  return {
    title: title || `Service Event ${index + 1}`,
    summary: normalizeSingleLineText(item?.summary),
    link: sanitizeHttpUrl(item?.link),
    statusLabel: normalizeSingleLineText(item?.status_label || item?.statusLabel),
    postedAtLabel: normalizeSingleLineText(item?.posted_at_label || item?.postedAtLabel),
  };
}

function normalizeWorkspaceServiceEventsPayload(payload, requestedAtMs) {
  const items = Array.isArray(payload?.items)
    ? payload.items.map((item, index) => normalizeWorkspaceServiceEventItem(item, index)).filter(Boolean)
    : [];
  return {
    loadState: "ready",
    items,
    statusPageUrl: sanitizeHttpUrl(payload?.status_page_url || payload?.statusPageUrl) || AGORA_STATUS_PAGE_URL,
    fetchedAt: normalizeSingleLineText(payload?.fetched_at || payload?.fetchedAt),
    lastRequestedAtMs: requestedAtMs,
  };
}

function shouldRefreshWorkspaceServiceEvents() {
  const current = workspaceServiceEventsState || buildDefaultWorkspaceServiceEventsState();
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
  let nextState;
  try {
    const response = await fetch(SERVICE_EVENTS_ENDPOINT);
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }
    const payload = await response.json();
    nextState = normalizeWorkspaceServiceEventsPayload(payload, requestedAtMs);
  } catch {
    nextState = {
      ...buildDefaultWorkspaceServiceEventsState(),
      loadState: "error",
      lastRequestedAtMs: requestedAtMs,
    };
  }

  if (Number(workspaceServiceEventsState?.lastRequestedAtMs || 0) !== requestedAtMs) {
    return;
  }
  workspaceServiceEventsState = nextState;
  if (routeState.view === "pool" && getSelectedEngineer() && workspaceRegionEl) {
    workspaceRegionEl.innerHTML = renderWelcomeViewHtml();
  }
}

function ensureWorkspaceServiceEventsLoaded() {
  if (!getSelectedEngineer() || !shouldRefreshWorkspaceServiceEvents()) {
    return;
  }
  const current = workspaceServiceEventsState || buildDefaultWorkspaceServiceEventsState();
  const requestedAtMs = Date.now();
  workspaceServiceEventsState = {
    ...current,
    loadState: "loading",
    statusPageUrl: sanitizeHttpUrl(current.statusPageUrl) || AGORA_STATUS_PAGE_URL,
    lastRequestedAtMs: requestedAtMs,
  };
  void fetchWorkspaceServiceEvents(requestedAtMs);
}

function normalizeEngineerId(value) {
  const raw = String(value || "").trim();
  const engineer = DEMO_ENGINEERS.find((candidate) => candidate.id === raw);
  return engineer ? engineer.id : "";
}

function refreshWorkspaceSessionState() {
  const storedEngineerId = normalizeEngineerId(readStorage(WORKSPACE_AUTH_KEY, ""));
  selectedEngineerId = storedEngineerId;
  selectedEngineerCandidate = storedEngineerId || normalizeEngineerId(selectedEngineerCandidate) || DEMO_ENGINEERS[0].id;
  workspaceShift = normalizeShift(readStorage(WORKSPACE_SHIFT_KEY, DEFAULT_SHIFT));
  workspaceActive = Boolean(readStorage(WORKSPACE_ACTIVE_KEY, false));
  workspaceBreakAfterCase = Boolean(readStorage(WORKSPACE_BREAK_AFTER_CASE_KEY, false));
}

function getSelectedEngineerId() {
  refreshWorkspaceSessionState();
  return selectedEngineerId;
}

function currentEngineerId() {
  return getSelectedEngineerId() || DEMO_ENGINEERS[0].id;
}

function getSelectedEngineer() {
  const engineerId = getSelectedEngineerId();
  return DEMO_ENGINEERS.find((engineer) => engineer.id === engineerId) || null;
}

function getCandidateEngineer() {
  const candidateId = normalizeEngineerId(selectedEngineerCandidate) || getSelectedEngineerId() || DEMO_ENGINEERS[0].id;
  return DEMO_ENGINEERS.find((engineer) => engineer.id === candidateId) || DEMO_ENGINEERS[0];
}

function normalizeShift(value) {
  const candidate = value && typeof value === "object" ? value : {};
  const start = normalizeShiftTime(candidate.start, DEFAULT_SHIFT.start);
  const end = normalizeShiftTime(candidate.end, DEFAULT_SHIFT.end);
  return { start, end };
}

function normalizeShiftTime(value, fallback) {
  const text = String(value || "").trim();
  return /^\d{2}:\d{2}$/.test(text) ? text : fallback;
}

function utc8Now() {
  return new Date(Date.now() + UTC8_OFFSET_MS);
}

function formatUtc8Time(date = utc8Now()) {
  const year = date.getUTCFullYear();
  const month = String(date.getUTCMonth() + 1).padStart(2, "0");
  const day = String(date.getUTCDate()).padStart(2, "0");
  const hour = String(date.getUTCHours()).padStart(2, "0");
  const minute = String(date.getUTCMinutes()).padStart(2, "0");
  return `${year}-${month}-${day} ${hour}:${minute} UTC+8`;
}

function minutesFromShiftTime(value) {
  const match = String(value || "").match(/^(\d{2}):(\d{2})$/);
  if (!match) {
    return 0;
  }
  return Number(match[1]) * 60 + Number(match[2]);
}

function isInShift(now = utc8Now()) {
  refreshWorkspaceSessionState();
  const current = now.getUTCHours() * 60 + now.getUTCMinutes();
  const start = minutesFromShiftTime(workspaceShift.start);
  const end = minutesFromShiftTime(workspaceShift.end);
  if (start === end) {
    return false;
  }
  if (start < end) {
    return current >= start && current < end;
  }
  return current >= start || current < end;
}

function nextShiftInfo() {
  refreshWorkspaceSessionState();
  const now = utc8Now();
  const current = now.getUTCHours() * 60 + now.getUTCMinutes();
  const start = minutesFromShiftTime(workspaceShift.start);
  const end = minutesFromShiftTime(workspaceShift.end);
  if (start === end) {
    return "Shift is not configured";
  }
  if (isInShift(now)) {
    const remaining = start < end ? end - current : current < end ? end - current : 24 * 60 - current + end;
    return `On shift now, ${Math.max(1, remaining)} min left`;
  }
  const untilStart = current < start ? start - current : 24 * 60 - current + start;
  return `Next shift starts in ${Math.max(1, untilStart)} min`;
}

function saveWorkspaceActive(value) {
  workspaceActive = Boolean(value);
  writeStorage(WORKSPACE_ACTIVE_KEY, workspaceActive);
}

function saveWorkspaceBreakAfterCase(value) {
  workspaceBreakAfterCase = Boolean(value);
  writeStorage(WORKSPACE_BREAK_AFTER_CASE_KEY, workspaceBreakAfterCase);
}

function toggleWorkspaceBreakAfterCase() {
  saveWorkspaceBreakAfterCase(!workspaceBreakAfterCase);
  renderWorkspaceChrome();
  renderTicketDetail();
}

function readWorkspaceCaseSlaStarts() {
  const value = readStorage(WORKSPACE_CASE_SLA_STARTED_AT_KEY, {});
  return value && typeof value === "object" && !Array.isArray(value) ? value : {};
}

function writeWorkspaceCaseSlaStarts(value) {
  writeStorage(WORKSPACE_CASE_SLA_STARTED_AT_KEY, value && typeof value === "object" ? value : {});
}

function parseEpochMs(value) {
  const numeric = Number(value);
  if (Number.isFinite(numeric) && numeric > 0) {
    return numeric;
  }
  const parsed = Date.parse(String(value || ""));
  return Number.isFinite(parsed) && parsed > 0 ? parsed : 0;
}

function rememberWorkspaceCaseSlaStart(ticketId, startedAt = Date.now()) {
  const normalizedTicketId = normalizeDetailTicketId(ticketId);
  if (!normalizedTicketId) {
    return 0;
  }
  const starts = readWorkspaceCaseSlaStarts();
  const existing = parseEpochMs(starts[normalizedTicketId]);
  if (existing) {
    return existing;
  }
  starts[normalizedTicketId] = startedAt;
  writeWorkspaceCaseSlaStarts(starts);
  return startedAt;
}

function workspaceCaseAssignedAt(ticket = selectedTicket) {
  const explicitAssignedAt = parseEpochMs(
    ticket?.assignedAt ||
      ticket?.assigned_at ||
      ticket?.assignment_started_at ||
      ticket?.engineer_assigned_at
  );
  if (explicitAssignedAt) {
    return explicitAssignedAt;
  }
  const ticketId = engineerCaseRouteId(ticket) || selectedTicketId;
  return rememberWorkspaceCaseSlaStart(ticketId);
}

function workspaceTicketSlaState(ticket = selectedTicket) {
  const assignedAt = workspaceCaseAssignedAt(ticket);
  if (!assignedAt) {
    return {
      label: "3h SLA from assign",
      className: "is-muted",
      remainingMs: WORKSPACE_CASE_SLA_MS,
      overdue: false,
    };
  }
  const remainingMs = WORKSPACE_CASE_SLA_MS - (Date.now() - assignedAt);
  if (remainingMs <= 0) {
    return { label: "SLA overdue", className: "is-danger", remainingMs, overdue: true };
  }
  if (remainingMs <= 30 * 60 * 1000) {
    return { label: `${formatDuration(remainingMs)} left`, className: "is-warning", remainingMs, overdue: false };
  }
  return { label: `${formatDuration(remainingMs)} left`, className: "is-success", remainingMs, overdue: false };
}

function formatDuration(ms) {
  const totalMinutes = Math.max(0, Math.ceil(ms / 60000));
  const hours = Math.floor(totalMinutes / 60);
  const minutes = totalMinutes % 60;
  return `${hours}h ${String(minutes).padStart(2, "0")}m`;
}

function formatCountdown(ms) {
  const totalSeconds = Math.max(0, Math.ceil(ms / 1000));
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  return `${String(hours).padStart(2, "0")}:${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
}

function getWorkspaceSlaCountdownLabel(sla) {
  if (!selectedTicketId) {
    return "3h SLA from assign";
  }
  if (sla?.overdue) {
    return "SLA overdue";
  }
  return formatCountdown(sla?.remainingMs ?? WORKSPACE_CASE_SLA_MS);
}

function nextLocalInvestigationMessageId(prefix = "message") {
  localInvestigationMessageSequence += 1;
  return `local-${prefix}-${localInvestigationMessageSequence}`;
}

function normalizeDetailTicketId(value) {
  return String(value || "").trim();
}

function getLocalInvestigationThreadState(ticketId) {
  const normalizedTicketId = normalizeDetailTicketId(ticketId);
  if (!normalizedTicketId || !localInvestigationThreadState) {
    return null;
  }
  return normalizeDetailTicketId(localInvestigationThreadState.ticketId) === normalizedTicketId
    ? localInvestigationThreadState
    : null;
}

function clearLocalInvestigationThreadState(ticketId = null) {
  if (!localInvestigationThreadState) {
    return;
  }
  if (ticketId && normalizeDetailTicketId(localInvestigationThreadState.ticketId) !== normalizeDetailTicketId(ticketId)) {
    return;
  }
  localInvestigationThreadState = null;
}

function createAbortController() {
  if (typeof AbortController === "function") {
    return new AbortController();
  }
  return {
    signal: {
      aborted: false,
    },
    abort() {
      this.signal.aborted = true;
    },
  };
}

function abortInFlightDetailRefresh() {
  if (
    !detailRefreshState.inFlightController ||
    typeof detailRefreshState.inFlightController.abort !== "function"
  ) {
    detailRefreshState.inFlightController = null;
    return;
  }
  try {
    detailRefreshState.inFlightController.abort();
  } catch {
    // Ignore abort errors from already-settled requests.
  }
  detailRefreshState.inFlightController = null;
}

function resetDetailRefreshState(ticketId = null) {
  abortInFlightDetailRefresh();
  detailRefreshState.ticketId = normalizeDetailTicketId(ticketId) || null;
  detailRefreshState.requestSeq = 0;
  detailRefreshState.mutationEpoch = 0;
}

function ensureDetailRefreshStateTicket(ticketId) {
  const normalizedTicketId = normalizeDetailTicketId(ticketId) || null;
  if (detailRefreshState.ticketId === normalizedTicketId) {
    return;
  }
  resetDetailRefreshState(normalizedTicketId);
}

function bumpDetailMutationEpoch(ticketId = selectedTicketId) {
  ensureDetailRefreshStateTicket(ticketId);
  detailRefreshState.mutationEpoch += 1;
  abortInFlightDetailRefresh();
}

function detailTicketUpdatedAtMs(ticket) {
  if (!ticket || typeof ticket !== "object") {
    return NaN;
  }
  return normalizeIsoTimestamp(ticket.updated_at || ticket.closed_at || ticket.created_at);
}

function shouldDiscardStaleDetailPayload(currentTicket, nextTicket) {
  if (!currentTicket || typeof currentTicket !== "object" || !nextTicket || typeof nextTicket !== "object") {
    return false;
  }

  const currentUpdatedAt = detailTicketUpdatedAtMs(currentTicket);
  const nextUpdatedAt = detailTicketUpdatedAtMs(nextTicket);
  if (Number.isFinite(currentUpdatedAt) && Number.isFinite(nextUpdatedAt) && nextUpdatedAt < currentUpdatedAt) {
    return true;
  }

  const currentStatus = normalizeStatusValue(currentTicket.status || "open");
  const nextStatus = normalizeStatusValue(nextTicket.status || "open");
  if (
    currentStatus === "resolved" &&
    nextStatus !== "resolved" &&
    (!Number.isFinite(currentUpdatedAt) || !Number.isFinite(nextUpdatedAt) || nextUpdatedAt <= currentUpdatedAt)
  ) {
    return true;
  }

  const currentActiveInvestigation = getActiveInvestigation(currentTicket);
  const nextActiveInvestigation = getActiveInvestigation(nextTicket);
  const currentClosedInvestigation = getLatestClosedInvestigation(currentTicket);
  if (
    !currentActiveInvestigation &&
    currentClosedInvestigation &&
    nextActiveInvestigation &&
    (!Number.isFinite(currentUpdatedAt) || !Number.isFinite(nextUpdatedAt) || nextUpdatedAt <= currentUpdatedAt)
  ) {
    return true;
  }

  return false;
}

function mergeInvestigationMessagesWithLocalState(ticketId, durableMessages) {
  const baseMessages = Array.isArray(durableMessages) ? durableMessages : [];
  const localState = getLocalInvestigationThreadState(ticketId);
  if (!localState || !Array.isArray(localState.messages) || localState.messages.length === 0) {
    return baseMessages;
  }
  return [...baseMessages, ...localState.messages];
}

function hasPendingLocalInvestigationReply(ticketId) {
  const localState = getLocalInvestigationThreadState(ticketId);
  return Boolean(localState?.pendingAi);
}

function hasPendingLocalInvestigationApproval(ticketId) {
  const localState = getLocalInvestigationThreadState(ticketId);
  return Boolean(localState?.pendingApproval);
}

function normalizeLocalInvestigationPendingAction(value) {
  const normalized = String(value || "").trim().toLowerCase();
  return normalized === "investigation_revise" || normalized === "investigation_message" ? normalized : "";
}

function normalizeIsoTimestamp(value) {
  const parsed = Date.parse(String(value || "").trim());
  return Number.isFinite(parsed) ? parsed : NaN;
}

function isInvestigationTimeoutErrorMessage(message) {
  return String(message || "").trim().toLowerCase().includes("request timed out after");
}

function reconcileDurableInvestigationState(ticketId, ticket) {
  const normalizedTicketId = normalizeDetailTicketId(ticketId);
  const localState = getLocalInvestigationThreadState(normalizedTicketId);
  if (!localState?.pendingAi || !ticket || typeof ticket !== "object") {
    return false;
  }

  const pendingAction = normalizeLocalInvestigationPendingAction(localState.pendingAction);
  const pendingNote = String(localState.pendingNote || "").trim();
  const submittedAt = normalizeIsoTimestamp(localState.submittedAt);
  const lastEngineerAction =
    ticket.last_engineer_action && typeof ticket.last_engineer_action === "object"
      ? ticket.last_engineer_action
      : null;
  if (!pendingAction || !pendingNote || !lastEngineerAction) {
    return false;
  }

  const durableAction = normalizeLocalInvestigationPendingAction(lastEngineerAction.action);
  const durableNote = String(lastEngineerAction.note || "").trim();
  const durableCreatedAt = normalizeIsoTimestamp(lastEngineerAction.created_at);
  const hasMatchingDurableAction =
    durableAction === pendingAction &&
    durableNote === pendingNote &&
    Number.isFinite(submittedAt) &&
    Number.isFinite(durableCreatedAt) &&
    durableCreatedAt >= submittedAt;

  if (!hasMatchingDurableAction) {
    return false;
  }

  clearLocalInvestigationThreadState(normalizedTicketId);
  return true;
}

function delay(ms) {
  const timeoutMs = Number.isFinite(Number(ms)) ? Math.max(0, Number(ms)) : 0;
  return new Promise((resolve) => {
    setTimeout(resolve, timeoutMs);
  });
}

async function recoverTimedOutInvestigationSend(ticketId) {
  const normalizedTicketId = normalizeDetailTicketId(ticketId);
  if (!normalizedTicketId || !getLocalInvestigationThreadState(normalizedTicketId)) {
    return false;
  }

  const attemptCount = Math.max(
    1,
    Math.ceil(INVESTIGATION_TIMEOUT_RECOVERY_WINDOW_MS / INVESTIGATION_TIMEOUT_RECOVERY_POLL_MS)
  );

  for (let attempt = 0; attempt < attemptCount; attempt += 1) {
    if (!getLocalInvestigationThreadState(normalizedTicketId)) {
      return true;
    }
    if (routeState.view !== "detail" || normalizeDetailTicketId(selectedTicketId) !== normalizedTicketId) {
      return true;
    }

    try {
      await loadTickets({ refreshDetail: false });
    } catch {
      // Keep polling the selected ticket detail until the recovery window expires.
    }

    try {
      await refreshSelectedTicket({ silent: true, showLoading: false });
    } catch {
      // Keep polling the selected ticket detail until the recovery window expires.
    }

    if (!getLocalInvestigationThreadState(normalizedTicketId)) {
      return true;
    }
    if (attempt < attemptCount - 1) {
      await delay(INVESTIGATION_TIMEOUT_RECOVERY_POLL_MS);
    }
  }

  return !getLocalInvestigationThreadState(normalizedTicketId);
}

function startLocalInvestigationPendingApproval(ticketId) {
  const normalizedTicketId = normalizeDetailTicketId(ticketId);
  if (!normalizedTicketId) {
    return;
  }
  const existingState = getLocalInvestigationThreadState(normalizedTicketId);
  localInvestigationThreadState = {
    ticketId: normalizedTicketId,
    pendingAi: existingState?.pendingAi === true,
    pendingApproval: true,
    pendingAction: existingState?.pendingAction,
    pendingNote: existingState?.pendingNote,
    submittedAt: existingState?.submittedAt,
    messages: Array.isArray(existingState?.messages) ? existingState.messages : [],
  };
}

function clearLocalInvestigationPendingApproval(ticketId) {
  const normalizedTicketId = normalizeDetailTicketId(ticketId);
  const existingState = getLocalInvestigationThreadState(normalizedTicketId);
  if (!existingState?.pendingApproval) {
    return;
  }
  const preservedMessages = Array.isArray(existingState.messages) ? existingState.messages : [];
  if (!preservedMessages.length && existingState.pendingAi !== true) {
    clearLocalInvestigationThreadState(normalizedTicketId);
    return;
  }
  localInvestigationThreadState = {
    ...existingState,
    pendingApproval: false,
    messages: preservedMessages,
  };
}

function startLocalInvestigationOptimisticSend(ticketId, engineerMessage, pendingAction = "investigation_message") {
  const normalizedTicketId = normalizeDetailTicketId(ticketId);
  const cleaned = String(engineerMessage || "").trim();
  if (!normalizedTicketId || !cleaned) {
    return;
  }
  const existingMessages = Array.isArray(getLocalInvestigationThreadState(normalizedTicketId)?.messages)
    ? getLocalInvestigationThreadState(normalizedTicketId).messages.filter((message) => message?.is_pending_ai !== true)
    : [];
  const createdAt = new Date().toISOString();
  localInvestigationThreadState = {
    ticketId: normalizedTicketId,
    pendingAi: true,
    pendingApproval: false,
    pendingAction: normalizeLocalInvestigationPendingAction(pendingAction) || "investigation_message",
    pendingNote: cleaned,
    submittedAt: createdAt,
    messages: [
      ...existingMessages,
      {
        id: nextLocalInvestigationMessageId("engineer"),
        role: "engineer",
        content: cleaned,
        created_at: createdAt,
        is_optimistic_local: true,
      },
      {
        id: nextLocalInvestigationMessageId("engineer-ai"),
        role: "engineer_ai",
        content: `${ENGINEER_AI_DISPLAY_NAME} is reviewing your update...`,
        created_at: createdAt,
        is_pending_ai: true,
      },
    ],
  };
}

function failLocalInvestigationOptimisticSend(ticketId, errorMessage) {
  const normalizedTicketId = normalizeDetailTicketId(ticketId);
  const localState = getLocalInvestigationThreadState(normalizedTicketId);
  if (!localState) {
    return;
  }
  const cleanedError = String(errorMessage || "").trim() || "Unknown error";
  localInvestigationThreadState = {
    ticketId: normalizedTicketId,
    pendingAi: false,
    pendingApproval: false,
    messages: [
      ...localState.messages.filter((message) => message?.is_pending_ai !== true),
      {
        id: nextLocalInvestigationMessageId("system"),
        role: "system",
        content: `${ENGINEER_AI_DISPLAY_NAME} update failed: ${cleanedError}`,
        created_at: new Date().toISOString(),
        is_local_error: true,
      },
    ],
  };
}

function applyInvestigationResponseToSelectedTicket(ticketId, payload) {
  const normalizedTicketId = normalizeDetailTicketId(ticketId);
  if (!selectedTicket || normalizeDetailTicketId(selectedTicket.ticket_id || selectedTicketId) !== normalizedTicketId) {
    clearLocalInvestigationThreadState(normalizedTicketId);
    return;
  }

  if (payload && typeof payload === "object") {
    bumpDetailMutationEpoch(normalizedTicketId);
    const nextClosedInvestigation =
      payload.closed_investigation && typeof payload.closed_investigation === "object"
        ? payload.closed_investigation
        : null;
    const existingHistory = Array.isArray(selectedTicket.investigation_history)
      ? selectedTicket.investigation_history
      : [];
    const nextHistory = nextClosedInvestigation
      ? [
          nextClosedInvestigation,
          ...existingHistory.filter(
            (item) => String(item?.id || "").trim() !== String(nextClosedInvestigation.id || "").trim()
          ),
        ]
      : existingHistory;
    const nextEngineerAgentState =
      payload.engineer_agent_state === undefined
        ? selectedTicket.engineer_agent_state
        : payload.engineer_agent_state && typeof payload.engineer_agent_state === "object"
        ? payload.engineer_agent_state
        : null;
    // Merge active_guardrail_final from response into agent state if present
    if (
      payload.active_guardrail_final &&
      typeof payload.active_guardrail_final === "object" &&
      nextEngineerAgentState &&
      typeof nextEngineerAgentState === "object"
    ) {
      nextEngineerAgentState.active_guardrail_final = payload.active_guardrail_final;
    }
    selectedTicket = {
      ...selectedTicket,
      status: payload.status ?? selectedTicket.status,
      updated_at: payload.updated_at ?? selectedTicket.updated_at,
      active_investigation:
        payload.active_investigation === undefined
          ? selectedTicket.active_investigation
          : payload.active_investigation,
      engineer_agent_state: nextEngineerAgentState,
      investigation_history: nextHistory,
    };

    const ticketIndex = tickets.findIndex(
      (ticket) => normalizeDetailTicketId(ticket?.ticket_id) === normalizedTicketId
    );
    if (ticketIndex >= 0) {
      tickets[ticketIndex] = {
        ...tickets[ticketIndex],
        status: selectedTicket.status,
        updated_at: selectedTicket.updated_at,
        active_investigation: selectedTicket.active_investigation,
        engineer_agent_state: nextEngineerAgentState,
        investigation_history: nextHistory,
      };
    }
  }

  clearLocalInvestigationThreadState(normalizedTicketId);
}

function applySuccessfulInvestigationSendResponse(ticketId, payload) {
  applyInvestigationResponseToSelectedTicket(ticketId, payload);
  tellAiSubmitting = false;
  renderTicketDetail();
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

function ChangeEngineerButton({ loading = false } = {}) {
  return `
    <button
      id="logout-btn"
      class="logout-icon-btn"
      type="button"
      title="Change engineer"
      aria-label="Change engineer"
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

function getHeaderUserControlsEl() {
  return document.getElementById("header-user-controls") || headerUserControlsEl;
}

function getWsStatusEl() {
  return document.getElementById("ws-status") || wsStatusEl;
}

function workspaceRealtimeStatusLabel() {
  const suffix = storageMode === "unknown" ? "" : ` | Storage: ${storageMode}`;
  return `${workspaceRealtimeStatusText}${suffix}`;
}

function renderHeaderUserControls() {
  const controlsEl = getHeaderUserControlsEl();
  if (!controlsEl) {
    return;
  }
  const engineer = getSelectedEngineer() || getCandidateEngineer();
  controlsEl.innerHTML = [
    UserProfileChip({ username: engineer.name, role: "ENGINEER" }),
    ChangeEngineerButton({ loading: changeEngineerLoading }),
  ].join("");
  const logoutBtn = document.getElementById("logout-btn");
  logoutBtn?.addEventListener("click", () => {
    handleChangeEngineerClick();
  });
}

function renderWorkspaceAssignmentSidebarHtml() {
  refreshWorkspaceSessionState();
  const engineer = getSelectedEngineer() || getCandidateEngineer();
  const inShift = isInShift();
  const activeTicketId = selectedTicketId || engineerCaseRouteId(selectedTicket);
  const activeTicketTitle = selectedTicket
    ? String(selectedTicket.title || selectedTicket.subject || "Current engineer case")
    : activeTicketId
    ? "Loading assigned case"
    : "No active Engineer Ticket";
  const investigatingCount = tickets.filter((ticket) => normalizeStatusValue(ticket?.status || "open") === "investigating").length;
  const sla = workspaceTicketSlaState(selectedTicket);
  const assignable = Boolean(inShift && !activeTicketId);
  const assignmentSummary = activeTicketId
    ? `${activeTicketId} is locked until this case is completed.`
    : "Ready opens the first real investigating case.";

  return `
    <div class="sidebar-inner">
      <div class="rail-brand">
        <div class="rail-brand-icon">
          <span class="material-symbols-outlined" aria-hidden="true">bolt</span>
        </div>
        <div class="rail-brand-copy">
          <span class="rail-brand-title">Concierge AI</span>
          <span class="rail-brand-subtitle">Assignment Command</span>
        </div>
      </div>

      <div class="rail-compact-stack" aria-hidden="true">
        <span class="engineer-avatar mono rail-compact-avatar">${escapeHtml(engineer.initials)}</span>
        <span class="rail-compact-status ${inShift ? "is-success" : "is-muted"}">
          <span class="material-symbols-outlined" aria-hidden="true">schedule</span>
        </span>
        <span class="rail-compact-status ${activeTicketId ? "is-warning" : "is-success"}">
          <span class="material-symbols-outlined" aria-hidden="true">${activeTicketId ? "confirmation_number" : "task_alt"}</span>
        </span>
      </div>

      <div class="workspace-sidebar-scroll">
      <section class="engineer-context-card panel-card">
        <div class="sidebar-profile">
          <span class="engineer-avatar mono" aria-hidden="true">${escapeHtml(engineer.initials)}</span>
          <div>
            <p class="eyebrow">Engineer context</p>
            <h2>${escapeHtml(engineer.name)}</h2>
            <p>${escapeHtml(engineer.role)}</p>
          </div>
        </div>
        <div class="status-pills">
          <span class="status-pill ${inShift ? "is-success" : "is-muted"}">${inShift ? "In shift" : "Out of shift"}</span>
          <span class="status-pill ${assignable ? "is-success" : "is-warning"}">${assignable ? "Ready for next" : "Not assignable"}</span>
        </div>
      </section>

      <section class="context-panel panel-card">
        <div class="panel-head">
          <p class="eyebrow">UTC+8 daily shift</p>
          <h3>${escapeHtml(workspaceShift.start)}-${escapeHtml(workspaceShift.end)}</h3>
          <p>${escapeHtml(formatUtc8Time())}</p>
        </div>
        <div class="shift-form shift-form-readonly" aria-label="UTC+8 daily shift">
          <span class="field">
            <span class="field-label">Start</span>
            <strong>${escapeHtml(workspaceShift.start)}</strong>
          </span>
          <span class="field">
            <span class="field-label">End</span>
            <strong>${escapeHtml(workspaceShift.end)}</strong>
          </span>
        </div>
      </section>

      <section class="context-panel panel-card">
        <div class="panel-head">
          <p class="eyebrow">Assignment</p>
          <h3>${activeTicketId ? "One active ticket" : "No active Engineer Ticket"}</h3>
          <p>${escapeHtml(assignmentSummary)}</p>
        </div>
        <div class="sidebar-metrics">
          <span><strong>${escapeHtml(String(investigatingCount))}</strong> investigating</span>
          <span><strong>3h</strong> SLA</span>
          <span><strong class="current-ticket-sla ${escapeHtml(sla.className)}" data-sla-countdown>${escapeHtml(
            getWorkspaceSlaCountdownLabel(sla)
          )}</strong></span>
        </div>
      </section>

      <section class="context-panel panel-card">
        <div class="panel-head">
          <p class="eyebrow">Current case</p>
          <h3>${escapeHtml(activeTicketId || "Waiting")}</h3>
          <p>${escapeHtml(activeTicketTitle)}</p>
        </div>
      </section>
      </div>

      <div class="rail-footer workspace-sidebar-footer">
        <div id="header-user-controls" class="header-user-controls rail-user-controls"></div>
      </div>
    </div>
  `;
}

function renderWorkspaceAssignmentSidebar() {
  if (!workspaceAssignmentSidebarEl) {
    return;
  }
  workspaceAssignmentSidebarEl.classList.remove("hidden");
  workspaceAssignmentSidebarEl.innerHTML = renderWorkspaceAssignmentSidebarHtml();
  renderHeaderUserControls();
}

function parseRoute() {
  const hash = String(window.location.hash || "").trim();
  const path = hash.replace(/^#/, "") || "/tickets";

  if (path === "/" || path === "/tickets") {
    if (hash) {
      const homeUrl = `${window.location.pathname || "/workspace/"}${window.location.search || ""}`;
      if (typeof window.history?.replaceState === "function") {
        window.history.replaceState(null, "", homeUrl);
      } else {
        window.location.hash = "";
      }
    }
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
  const target = path === "/" || path === "/tickets" ? "" : `#${path}`;
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

function setWorkspaceShellMode(mode) {
  const isDetailMode = mode === "detail";
  const isPreparingMode = mode === "preparing";
  const hidesSidebar = isDetailMode || isPreparingMode;
  engineerScreenEl?.classList.toggle("workspace-detail-mode", isDetailMode);
  engineerScreenEl?.classList.toggle("workspace-preparing-mode", isPreparingMode);
  engineerScreenEl?.classList.toggle("workspace-home-mode", !hidesSidebar);
  if (workspaceAssignmentSidebarEl) {
    workspaceAssignmentSidebarEl.classList.toggle("hidden", hidesSidebar);
    if (hidesSidebar) {
      workspaceAssignmentSidebarEl.innerHTML = "";
    }
  }
}

function showWorkspaceShell(mode = "home") {
  loginScreenEl?.classList.add("hidden");
  engineerScreenEl?.classList.remove("hidden");
  setWorkspaceShellMode(mode);
}

function renderWorkspaceChrome() {
  const engineerVisibleTickets = tickets.filter((ticket) => isEngineerVisibleStatus(ticket?.status || "open"));
  if (routeState.view === "detail" && routeState.ticketId) {
    setWorkspaceShellMode("detail");
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
    setWorkspaceShellMode("home");
    if (workspaceTitleEl) {
      workspaceTitleEl.textContent = "Engineer Command Center";
    }
    if (workspaceSubtitleEl) {
      workspaceSubtitleEl.textContent = `${engineerVisibleTickets.length} engineer-visible tickets across active support workflows.`;
    }
    filterControlsEl?.classList.remove("hidden");
    renderFilterControls();
  }

  if (routeState.view !== "detail") {
    renderWorkspaceAssignmentSidebar();
  }
}

function formatMultiline(value) {
  return escapeHtml(value).replaceAll("\n", "<br>");
}

function normalizeAttachmentRecord(value) {
  if (!value || typeof value !== "object") {
    return null;
  }
  const assetId = String(value.asset_id || value.assetId || "").trim();
  const originalFilename = String(
    value.original_filename || value.originalFilename || value.file_name || value.fileName || "attachment"
  ).trim();
  if (!assetId && !originalFilename) {
    return null;
  }
  return {
    assetId,
    originalFilename: originalFilename || "attachment",
    sizeBytes: Number(value.size_bytes ?? value.sizeBytes ?? 0) || 0,
  };
}

function normalizeMessageAttachments(message) {
  return (Array.isArray(message?.attachments) ? message.attachments : [])
    .map((attachment) => normalizeAttachmentRecord(attachment))
    .filter(Boolean);
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

function renderMessageAttachments(message) {
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

async function downloadAsset(assetId) {
  const normalizedAssetId = String(assetId || "").trim();
  if (!normalizedAssetId) {
    return;
  }
  const payload = await fetchJson(`/api/assets/${encodeURIComponent(normalizedAssetId)}/download-url`);
  const downloadUrl = sanitizeHttpUrl(payload?.download_url);
  if (!downloadUrl) {
    throw new Error("Download URL was empty");
  }
  window.open(downloadUrl, "_blank", "noopener,noreferrer");
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
  if (role !== "assistant" && role !== "engineer_ai") {
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
    return ENGINEER_AI_DISPLAY_NAME;
  }
  if (role === "assistant") {
    return PUBLIC_ASSISTANT_DISPLAY_NAME;
  }
  if (role === "engineer") {
    return ENGINEER_DISPLAY_NAME;
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

function dedupeTextItems(items) {
  const seen = new Set();
  return (Array.isArray(items) ? items : [])
    .map((item) => String(item || "").trim())
    .filter(Boolean)
    .filter((item) => {
      const key = item.toLowerCase();
      if (seen.has(key)) {
        return false;
      }
      seen.add(key);
      return true;
    });
}

function withFallbackItems(items, fallback) {
  const normalized = dedupeTextItems(items);
  if (normalized.length) {
    return normalized;
  }
  return fallback ? [fallback] : [];
}

function normalizeCaseBuddyFactText(value) {
  return String(value || "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, " ")
    .trim();
}

function escapeRegExp(value) {
  return String(value || "").replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function extractStructuredCurrentIssueFact(value) {
  const normalized = normalizeCaseBuddyFactText(value);
  if (!normalized) {
    return null;
  }

  const matchers = [
    {
      kind: "channel",
      pattern: /^(?:customer reported\s+)?channel(?: name)?(?: is)?\s+(.+)$/,
    },
    {
      kind: "uid",
      pattern: /^(?:problematic\s+)?uid(?: is)?\s+(.+)$/,
    },
    {
      kind: "timestamp",
      pattern: /^(?:issue\s+time(?:stamp)?|timestamp)(?: is)?(?: around)?\s+(.+)$/,
    },
    {
      kind: "symptom",
      pattern: /^(?:issue\s+symptom|symptom)(?: is)?\s+(.+)$/,
    },
  ];

  for (const matcher of matchers) {
    const match = normalized.match(matcher.pattern);
    const payload = match?.[1]?.trim();
    if (payload) {
      return { kind: matcher.kind, payload };
    }
  }

  return null;
}

function isCurrentIssueFactCoveredBySummary(issueUnderstanding, fact) {
  const normalizedIssue = normalizeCaseBuddyFactText(issueUnderstanding);
  if (!normalizedIssue) {
    return false;
  }

  const structuredFact = extractStructuredCurrentIssueFact(fact);
  if (!structuredFact) {
    return false;
  }

  const payloadPattern = escapeRegExp(structuredFact.payload);
  switch (structuredFact.kind) {
    case "channel":
      return (
        new RegExp(`\\bchannel\\s+${payloadPattern}\\b`).test(normalizedIssue) ||
        new RegExp(`\\b${payloadPattern}\\b`).test(normalizedIssue)
      );
    case "uid":
      return (
        new RegExp(`\\buid\\s+${payloadPattern}\\b`).test(normalizedIssue) ||
        new RegExp(`\\buser\\s+id\\s+${payloadPattern}\\b`).test(normalizedIssue)
      );
    case "timestamp":
    case "symptom":
      return normalizedIssue.includes(structuredFact.payload);
    default:
      return false;
  }
}

function isCandidateAnswerLikeCurrentIssueFact(fact) {
  const normalizedFact = normalizeCaseBuddyFactText(fact);
  if (!normalizedFact) {
    return false;
  }
  const normalizedPublicAssistantName = normalizeCaseBuddyFactText(PUBLIC_ASSISTANT_DISPLAY_NAME);
  return (
    normalizedFact.startsWith(`${normalizedPublicAssistantName} candidate answer`) ||
    normalizedFact.startsWith("candidate answer") ||
    normalizedFact.startsWith("the current candidate answer") ||
    normalizedFact.startsWith("client ai candidate answer")
  );
}

function cleanCaseBuddyCurrentIssueFacts(facts) {
  return (Array.isArray(facts) ? facts : [])
    .map((fact) => String(fact || "").trim())
    .filter(Boolean)
    .filter((fact) => !isCandidateAnswerLikeCurrentIssueFact(fact));
}

function buildCaseBuddyCurrentIssueItems(agentState) {
  const issueUnderstanding = String(agentState?.issue_understanding || "").trim();
  const knownFacts = cleanCaseBuddyCurrentIssueFacts(agentState?.known_facts);
  if (issueUnderstanding) {
    const filteredKnownFacts = knownFacts.filter(
      (fact) => !isCurrentIssueFactCoveredBySummary(issueUnderstanding, fact)
    );
    return {
      summary: issueUnderstanding,
      items: filteredKnownFacts,
    };
  }

  if (knownFacts.length) {
    const [summary, ...items] = knownFacts;
    return {
      summary,
      items,
    };
  }

  const [summary = "", ...items] = withFallbackItems([], CASE_BUDDY_CURRENT_ISSUE_FALLBACK);
  return {
    summary,
    items,
  };
}

function findOpeningCaseBuddyMessageIndex(messages) {
  const items = Array.isArray(messages) ? messages : [];
  for (let index = 0; index < items.length; index += 1) {
    const message = items[index];
    if (String(message?.role || "").trim().toLowerCase() !== "engineer_ai") {
      continue;
    }
    if (message?.is_pending_ai === true) {
      continue;
    }
    return index;
  }
  return -1;
}

function buildCaseBuddyOpeningRequestSections(ticket, rawMessage = "") {
  const agentState = getEngineerAgentState(ticket);
  if (agentState) {
    const currentIssue = buildCaseBuddyCurrentIssueItems(agentState);
    return [
      {
        title: "Current issue",
        summary: currentIssue.summary,
        items: currentIssue.items,
      },
      {
        title: "Action needed",
        items: withFallbackItems(
          [
            agentState.next_request_for_engineer,
            ...(Array.isArray(agentState.missing_information) ? agentState.missing_information : []),
          ],
          CASE_BUDDY_ACTION_FALLBACK
        ),
      },
    ];
  }

  const parsedRequest = parseEngineerRequest(rawMessage);
  const [summary = "", ...items] = withFallbackItems(
    [parsedRequest.issue || String(rawMessage || "").trim()],
    CASE_BUDDY_CURRENT_ISSUE_FALLBACK
  );
  return [
    {
      title: "Current issue",
      summary,
      items,
    },
    {
      title: "Action needed",
      items: withFallbackItems([parsedRequest.action], CASE_BUDDY_ACTION_FALLBACK),
    },
  ];
}

function renderCaseBuddyRequestSectionsHtml(sections) {
  const items = Array.isArray(sections) ? sections : [];
  if (!items.length) {
    return "";
  }
  return `
    <div class="case-buddy-request-sections">
      ${items
        .map(
          (section) => {
            const summary = String(section?.summary || "").trim();
            const sectionItems = withFallbackItems(section?.items, "");
            return `
            <section class="case-buddy-request-section">
              <p class="case-buddy-request-title">${String(section?.title || "")}</p>
              ${summary ? `<p class="case-buddy-request-summary">${escapeHtml(summary)}</p>` : ""}
              ${
                sectionItems.length
                  ? `<ul class="case-buddy-request-list">
                ${sectionItems.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}
              </ul>`
                  : ""
              }
            </section>
          `;
          }
        )
        .join("")}
    </div>
  `;
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

function getEngineerAgentState(ticket) {
  if (!ticket || typeof ticket !== "object") {
    return null;
  }
  return ticket.engineer_agent_state && typeof ticket.engineer_agent_state === "object"
    ? ticket.engineer_agent_state
    : null;
}

function getReplyReadiness(ticket) {
  const agentState = getEngineerAgentState(ticket);
  return agentState?.reply_readiness && typeof agentState.reply_readiness === "object"
    ? agentState.reply_readiness
    : null;
}

function getEngineerHitlFeedbackRecords(ticket) {
  return Array.isArray(ticket?.engineer_hitl_feedback) ? ticket.engineer_hitl_feedback : [];
}

function getLatestEngineerHitlFeedback(ticket) {
  return getEngineerHitlFeedbackRecords(ticket)[0] || null;
}

function formatHitlFeedbackList(value, fallbackText = "None") {
  if (!Array.isArray(value) || !value.length) {
    return escapeHtml(fallbackText);
  }
  return value
    .map((item) => {
      if (!item || typeof item !== "object") {
        return escapeHtml(String(item || ""));
      }
      const text =
        item.value ||
        item.claim ||
        item.source_id ||
        item.source ||
        item.id ||
        Object.values(item).find((entry) => String(entry || "").trim());
      return escapeHtml(text || "");
    })
    .filter(Boolean)
    .join("<br>");
}

function renderHitlFeedbackReadOnlyFieldHtml(label, value, fallbackText = "Not captured") {
  const text = String(value || "").trim() || fallbackText;
  return `
    <div class="detail-hitl-feedback-field">
      <span>${escapeHtml(label)}</span>
      <strong>${escapeHtml(text)}</strong>
    </div>
  `;
}

function renderHitlFeedbackPanelHtml(ticket) {
  const latestFeedback = getLatestEngineerHitlFeedback(ticket);
  const feedbackCount = getEngineerHitlFeedbackRecords(ticket).length;
  const isResolved = normalizeStatusValue(ticket?.status) === "resolved";
  const statusLabel = hitlFeedbackLoading
    ? "Loading review"
    : latestFeedback
    ? "Auto-reviewed after closure"
    : isResolved
    ? "Review pending"
    : "Pending closure";
  const bodyHtml = hitlFeedbackLoading
    ? `<div class="detail-hitl-feedback-empty" role="status">Loading feedback history...</div>`
    : latestFeedback
    ? `
      <div class="detail-hitl-feedback-latest" aria-label="Latest AI learning review">
        <span class="mono">${escapeHtml(latestFeedback.feedback_id || "-")}</span>
        <strong>${escapeHtml(latestFeedback.feedback_type || "feedback")}</strong>
        <small>${escapeHtml(latestFeedback.created_by || "engineer_ai_auto_review")} · ${escapeHtml(
          latestFeedback.created_at || ""
        )}</small>
      </div>
      <div class="detail-hitl-feedback-grid">
        ${renderHitlFeedbackReadOnlyFieldHtml("Diagnosis", latestFeedback.diagnosis_correctness)}
        ${renderHitlFeedbackReadOnlyFieldHtml("Root cause", latestFeedback.root_cause_correctness)}
        ${renderHitlFeedbackReadOnlyFieldHtml("Evidence", latestFeedback.evidence_quality)}
        ${renderHitlFeedbackReadOnlyFieldHtml("Citations", latestFeedback.citation_quality)}
        ${renderHitlFeedbackReadOnlyFieldHtml("Customer reply", latestFeedback.customer_reply_quality)}
        ${renderHitlFeedbackReadOnlyFieldHtml("Memory candidate", latestFeedback.memory_candidate)}
        ${renderHitlFeedbackReadOnlyFieldHtml("Memory safety", latestFeedback.memory_safety)}
      </div>
      <div class="detail-hitl-feedback-review">
        <div>
          <span>Reviewed root cause</span>
          <p>${escapeHtml(latestFeedback.corrected_root_cause || "Not captured")}</p>
        </div>
        <div>
          <span>Reviewed solution</span>
          <p>${escapeHtml(latestFeedback.corrected_solution || "Not captured")}</p>
        </div>
        <div>
          <span>Reviewed customer reply</span>
          <p>${escapeHtml(latestFeedback.corrected_customer_reply || "Not captured")}</p>
        </div>
        <div>
          <span>Evidence refs</span>
          <p>${formatHitlFeedbackList(latestFeedback.evidence_refs)}</p>
        </div>
        <div>
          <span>Missing information</span>
          <p>${formatHitlFeedbackList(latestFeedback.missing_information)}</p>
        </div>
        <div>
          <span>Incorrect claims</span>
          <p>${formatHitlFeedbackList(latestFeedback.incorrect_claims)}</p>
        </div>
        <div>
          <span>Memory notes</span>
          <p>${escapeHtml(latestFeedback.memory_notes || "Not captured")}</p>
        </div>
      </div>
    `
    : isResolved
    ? `<div class="detail-hitl-feedback-empty">Review pending. The AI learning review has not been recorded for this closed engineer case yet.</div>`
    : `<div class="detail-hitl-feedback-empty">Learning review will run after this engineer case closes.</div>`;
  return `
    <section class="panel-card detail-hitl-feedback">
      <div class="panel-card-head">
        <div>
          <p class="panel-card-kicker">HITL Feedback</p>
          <h3 class="panel-card-title">Feedback for AI Learning</h3>
        </div>
        <span class="detail-hitl-feedback-count">${escapeHtml(statusLabel)}</span>
      </div>
      <p class="detail-hitl-feedback-note">
        Read-only AI review generated after closure. It is an audit candidate for eval and memory review, not a direct memory write.
      </p>
      ${bodyHtml}
    </section>
  `;
}

function hasValidatedInvestigationApproval(ticket, activeInvestigation) {
  const draftCustomerReply = String(activeInvestigation?.draft_customer_reply || "").trim();
  return Boolean(
    activeInvestigation &&
      draftCustomerReply &&
      getReplyReadiness(ticket)?.ready_for_customer_reply === true
  );
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

function findLatestEngineerAiMessageIndex(messages) {
  const items = Array.isArray(messages) ? messages : [];
  for (let index = items.length - 1; index >= 0; index -= 1) {
    if (String(items[index]?.role || "").trim().toLowerCase() === "engineer_ai") {
      return index;
    }
  }
  return -1;
}

function getInvestigationApprovalUiState(ticket, activeInvestigation, investigationMessages, options = {}) {
  if (!activeInvestigation) {
    return { showApprovalBlock: false, decisionIndex: -1, awaitingFinalApproval: false };
  }

  const investigationState = String(activeInvestigation.state || "").trim().toLowerCase();
  const awaitingFinalApproval = investigationState === "awaiting_final_approval";
  const agentState = getEngineerAgentState(ticket);
  const activeGuardrailFinal =
    agentState?.active_guardrail_final && typeof agentState.active_guardrail_final === "object"
      ? agentState.active_guardrail_final
      : null;
  const guardrailBlocked = String(activeGuardrailFinal?.decision || "").trim() === "blocked";

  const fallbackEngineerAiIndex = findLatestEngineerAiMessageIndex(investigationMessages);
  const suppressApprovalBlock = options?.suppressApprovalBlock === true;

  // In awaiting_final_approval state, show guardrail final review instead of approve block
  const showApprovalBlock =
    !suppressApprovalBlock &&
    !awaitingFinalApproval &&
    !guardrailBlocked &&
    hasValidatedInvestigationApproval(ticket, activeInvestigation);

  return {
    showApprovalBlock,
    decisionIndex: showApprovalBlock ? fallbackEngineerAiIndex : -1,
    awaitingFinalApproval,
    activeGuardrailFinal,
  };
}

function renderReplyReadinessReviewHtml(ticket, activeInvestigation) {
  if (!activeInvestigation) {
    return "";
  }

  const replyReadiness = getReplyReadiness(ticket) || {};
  const conclusionSummary = String(replyReadiness.conclusion_summary || "").trim();
  const proofSummary = String(replyReadiness.proof_summary || "").trim();
  const solutionSummary = String(replyReadiness.solution_or_next_step || "").trim();
  const checks = [
    {
      label: "Conclusion",
      passed: replyReadiness.has_conclusion === true,
    },
    {
      label: "Proof",
      passed: replyReadiness.has_proof === true,
    },
    {
      label: "Next step",
      passed: replyReadiness.has_solution_or_next_step === true,
    },
  ];

  return `
    <section class="panel-card detail-readiness-review">
      <div class="panel-card-head">
        <div>
          <p class="panel-card-kicker">Internal Review</p>
          <h3 class="panel-card-title">Readiness Review</h3>
        </div>
        <span class="detail-readiness-pill ${
          replyReadiness.ready_for_customer_reply === true ? "is-ready" : "is-blocked"
        }">
          ${replyReadiness.ready_for_customer_reply === true ? "Validated" : "Needs Follow-up"}
        </span>
      </div>
      <div class="detail-readiness-body">
        <div class="detail-readiness-checks" aria-label="Reply readiness checks">
          ${checks
            .map(
              (item) => `
                <div class="detail-readiness-check ${item.passed ? "is-passed" : "is-missing"}">
                  <span class="detail-readiness-check-dot" aria-hidden="true"></span>
                  <span>${escapeHtml(item.label)}</span>
                </div>
              `
            )
            .join("")}
        </div>
        <div class="detail-readiness-fields">
          <div class="detail-readiness-field">
            <p class="detail-readiness-field-label">Conclusion</p>
            <p class="detail-readiness-field-value">${formatMultiline(
              conclusionSummary || "Conclusion not extracted yet."
            )}</p>
          </div>
          <div class="detail-readiness-field">
            <p class="detail-readiness-field-label">Proof</p>
            <p class="detail-readiness-field-value">${formatMultiline(proofSummary || "Proof still missing.")}</p>
          </div>
          <div class="detail-readiness-field">
            <p class="detail-readiness-field-label">Next step</p>
            <p class="detail-readiness-field-value">${formatMultiline(
              solutionSummary || "No actionable next step captured yet."
            )}</p>
          </div>
        </div>
      </div>
    </section>
  `;
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
  workspaceRealtimeStatusText = String(text || "");
  const statusEl = getWsStatusEl();
  if (statusEl) {
    statusEl.textContent = workspaceRealtimeStatusLabel();
  }
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

function isAuthenticated() {
  refreshWorkspaceSessionState();
  return Boolean(selectedEngineerId && workspaceActive);
}

function toggleScreens() {
  const authed = isAuthenticated();
  loginScreenEl?.classList.toggle("hidden", authed);
  engineerScreenEl?.classList.toggle("hidden", !authed);
  if (authed) {
    setWorkspaceShellMode(routeState.view === "detail" && routeState.ticketId ? "detail" : "home");
  }
}

function renderEngineerOption(engineer, selected) {
  return `
    <button
      class="engineer-option ${selected ? "is-selected" : ""}"
      type="button"
      role="radio"
      aria-checked="${selected ? "true" : "false"}"
      data-engineer-id="${escapeHtml(engineer.id)}"
    >
      <span class="engineer-avatar" aria-hidden="true">${escapeHtml(engineer.initials)}</span>
      <span>
        <strong>${escapeHtml(engineer.name)}</strong>
        <span>${escapeHtml(engineer.role)}</span>
      </span>
      <span class="select-mark material-symbols-outlined" aria-hidden="true">${
        selected ? "radio_button_checked" : "radio_button_unchecked"
      }</span>
    </button>
  `;
}

function renderWeeklyKnownIssuesHtml() {
  return `
    <div class="workspace-known-issue-list">
      ${WEEKLY_KNOWN_ISSUES.map(
        (issue) => `
          <article class="workspace-known-issue-item">
            <div class="workspace-known-issue-meta">
              <span class="status-pill ${issue.severity === "High" ? "is-warning" : "is-muted"}">${escapeHtml(issue.severity)}</span>
              <span>${escapeHtml(issue.surface)}</span>
            </div>
            <h4>${escapeHtml(issue.title)}</h4>
            <p>${escapeHtml(issue.brief)}</p>
            <div class="workspace-known-issue-owner">
              <span class="material-symbols-outlined" aria-hidden="true">groups</span>
              <span>${escapeHtml(issue.owner)}</span>
            </div>
          </article>
        `
      ).join("")}
    </div>
  `;
}

function renderWorkspaceServiceStatusHtml() {
  const state = workspaceServiceEventsState || buildDefaultWorkspaceServiceEventsState();
  const statusPageUrl = sanitizeHttpUrl(state.statusPageUrl) || AGORA_STATUS_PAGE_URL;
  const isLoading = state.loadState === "loading" || state.loadState === "idle";
  const hasItems = Array.isArray(state.items) && state.items.length > 0;

  if (isLoading) {
    return `<p class="workspace-service-empty">Loading latest Agora service events...</p>`;
  }

  if (!hasItems) {
    return `
      <div class="workspace-service-empty">
        <p>Service events are temporarily unavailable.</p>
        <a href="${escapeHtml(statusPageUrl)}" target="_blank" rel="noopener noreferrer">Open Agora Status Page</a>
      </div>
    `;
  }

  return `
    <div class="workspace-service-event-list">
      ${state.items
        .map(
          (item) => `
            <article class="workspace-service-event-item">
              <div class="workspace-service-event-meta">
                ${item.statusLabel ? `<span class="workspace-service-status">${escapeHtml(item.statusLabel)}</span>` : ""}
                ${item.postedAtLabel ? `<span>${escapeHtml(item.postedAtLabel)}</span>` : ""}
              </div>
              <h4>${escapeHtml(item.title)}</h4>
              ${item.summary ? `<p>${escapeHtml(item.summary)}</p>` : ""}
              ${
                item.link
                  ? `<a href="${escapeHtml(item.link)}" target="_blank" rel="noopener noreferrer">View incident</a>`
                  : ""
              }
            </article>
          `
        )
        .join("")}
    </div>
  `;
}

function renderLogin() {
  if (!workspaceRootEl) {
    return;
  }
  refreshWorkspaceSessionState();
  const selected = getCandidateEngineer();
  workspaceRootEl.innerHTML = `
    <section class="workspace-entry-view">
      <aside class="workspace-entry-intro">
        <div class="workspace-brand-lockup">
          <span class="workspace-brand-icon material-symbols-outlined" aria-hidden="true">assignment_ind</span>
          <div>
            <p class="workspace-eyebrow">SupportPortal Workspace</p>
            <strong>Engineer readiness</strong>
          </div>
        </div>
        <div>
          <h1>Start with the assignment flow. Work the real case.</h1>
          <p class="workspace-intro-copy">
            Choose a demo engineer, confirm UTC+8 readiness, then open the next real investigating engineer case.
          </p>
        </div>
        <ul class="workspace-policy-list" aria-label="Workspace policy">
          <li><span class="material-symbols-outlined" aria-hidden="true">schedule</span><span>Outside shift: ready is disabled until the UTC+8 shift opens.</span></li>
          <li><span class="material-symbols-outlined" aria-hidden="true">troubleshoot</span><span>Ready opens only real investigating cases.</span></li>
          <li><span class="material-symbols-outlined" aria-hidden="true">fact_check</span><span>Case detail, Engineer AI, approve/revise, and final approve use the real engineer APIs.</span></li>
        </ul>
      </aside>
      <section class="workspace-selector-panel">
        <div class="workspace-panel-head">
          <p class="workspace-eyebrow">Choose a demo engineer</p>
          <h2>Engineer login</h2>
          <p>Selection is stored locally for this workspace validation entry.</p>
        </div>
        <div id="engineer-selector" class="engineer-selector-grid" role="radiogroup" aria-label="Choose a demo engineer">
          ${DEMO_ENGINEERS.map((engineer) => renderEngineerOption(engineer, engineer.id === selected.id)).join("")}
        </div>
        <button class="btn btn-primary workspace-entry-cta" type="button" data-action="enter-welcome">
          View readiness overview
          <span class="material-symbols-outlined" aria-hidden="true">arrow_forward</span>
        </button>
      </section>
    </section>
  `;
}

function renderWelcomeViewHtml() {
  const engineer = getSelectedEngineer();
  if (!engineer) {
    return "";
  }
  const inShift = isInShift();
  return `
    <section class="workspace-welcome-view">
      <header class="workspace-welcome-hero workspace-home-hero">
        <div class="workspace-welcome-top">
          <div class="workspace-brand-lockup">
            <span class="workspace-brand-icon material-symbols-outlined" aria-hidden="true">bolt</span>
            <div>
              <p class="workspace-eyebrow">Real case workspace</p>
              <strong>Shift readiness</strong>
            </div>
          </div>
          <button
            class="btn btn-primary workspace-ready-btn"
            type="button"
            data-action="ready-to-roll"
            ${inShift ? "" : "disabled"}
          >
            I'm ready to roll
            <span class="material-symbols-outlined" aria-hidden="true">arrow_forward</span>
          </button>
        </div>
        <div class="workspace-home-intro">
          <p class="workspace-eyebrow">Engineer workspace</p>
          <h1>Welcome back, ${escapeHtml(engineer.name)}</h1>
          <p>Review today&rsquo;s operating context, adjust your UTC+8 shift, then open the next real investigating case when you are ready.</p>
          <div class="workspace-status-strip">
            <span class="status-pill ${inShift ? "is-success" : "is-muted"}">${inShift ? "In shift" : "Out of shift"}</span>
            <span class="status-pill is-muted">${escapeHtml(workspaceShift.start)}-${escapeHtml(workspaceShift.end)} UTC+8</span>
            <span class="status-pill is-muted">${escapeHtml(formatUtc8Time())}</span>
          </div>
        </div>
      </header>
      <section class="workspace-home-layout">
        <article class="workspace-info-panel workspace-shift-readiness-panel">
          <div class="workspace-panel-heading">
            <p class="ticket-kicker">Real queue gate</p>
            <h2>${escapeHtml(nextShiftInfo())}</h2>
            <p>Ready requests the backend investigating queue and opens the first eligible engineer case.</p>
          </div>
          <div class="workspace-shift-readiness-body">
            <form class="workspace-shift-form" data-workspace-shift-form>
              <label class="field">
                <span class="field-label">Start</span>
                <input name="start" type="time" value="${escapeHtml(workspaceShift.start)}" required />
              </label>
              <label class="field">
                <span class="field-label">End</span>
                <input name="end" type="time" value="${escapeHtml(workspaceShift.end)}" required />
              </label>
              <button class="btn btn-ghost" type="submit">Save shift</button>
            </form>
          </div>
          ${!inShift ? `<p class="workspace-shift-note">Ready is disabled outside the saved UTC+8 shift.</p>` : ""}
        </article>
        <section class="workspace-home-status-grid" aria-label="Known issues and service status">
          <article class="workspace-info-panel workspace-known-issues-panel">
            <div class="workspace-panel-heading">
              <p class="ticket-kicker">This week</p>
              <h2>Known issues</h2>
              <p>Demo context for common patterns engineers may see this week.</p>
            </div>
            ${renderWeeklyKnownIssuesHtml()}
          </article>
          <article class="workspace-info-panel workspace-service-status-panel">
            <div class="workspace-panel-heading workspace-service-heading">
              <div>
                <p class="ticket-kicker">Service Status</p>
                <h2>Latest Agora platform events</h2>
              </div>
              <a href="${escapeHtml(
                sanitizeHttpUrl(workspaceServiceEventsState?.statusPageUrl) || AGORA_STATUS_PAGE_URL
              )}" target="_blank" rel="noopener noreferrer">Open Agora Status Page</a>
            </div>
            ${renderWorkspaceServiceStatusHtml()}
          </article>
        </section>
      </section>
    </section>
  `;
}

function renderWelcome() {
  if (!workspaceRootEl) {
    return;
  }
  const html = renderWelcomeViewHtml();
  if (!html) {
    renderLogin();
    return;
  }
  workspaceRootEl.innerHTML = html;
}

function renderWorkspacePreparingLoadingHtml(message = "Preparing the real case workspace.") {
  return `
    <section class="workspace-ready-loading-view" aria-label="Preparing your workspace">
      <div class="workspace-ready-loading-card">
        <div class="ready-loading-spinner" aria-label="Preparing your workspace">
          <span class="material-symbols-outlined" aria-hidden="true">bolt</span>
        </div>
        <h1>Preparing your workspace</h1>
        <p>${escapeHtml(message)}</p>
        <div class="ready-loading-bar">
          <span class="ready-loading-bar-fill"></span>
        </div>
      </div>
    </section>
  `;
}

function renderReadyLoading() {
  const engineer = getSelectedEngineer();
  if (engineer) {
    showWorkspaceShell("preparing");
    filterControlsEl?.classList.add("hidden");
    if (filterControlsEl) {
      filterControlsEl.innerHTML = "";
    }
  }
  const targetEl = engineer ? workspaceRegionEl : workspaceRootEl;
  if (!targetEl) {
    return;
  }
  targetEl.innerHTML = renderWorkspacePreparingLoadingHtml(
    `Checking real investigating cases for ${engineer ? engineer.name : "this engineer"}.`
  );
}

function renderNoInvestigatingCase(message = "No investigating cases available") {
  const engineer = getSelectedEngineer();
  const targetEl = engineer ? workspaceRegionEl : workspaceRootEl;
  if (!targetEl) {
    return;
  }
  saveWorkspaceActive(false);
  if (engineer) {
    showWorkspaceShell("home");
    renderWorkspaceAssignmentSidebar();
  } else {
    toggleScreens();
  }
  targetEl.innerHTML = `
    <section class="workspace-empty-queue-view">
      <div class="workspace-empty-queue-panel">
        <span class="material-symbols-outlined" aria-hidden="true">hourglass_empty</span>
        <p class="workspace-eyebrow">Real queue check</p>
        <h1>No investigating cases available</h1>
        <p>${escapeHtml(message)}</p>
        <div class="workspace-ready-actions">
          <button class="btn btn-secondary" type="button" data-action="back-to-welcome">Back to readiness</button>
          <button class="btn btn-primary" type="button" data-action="ready-to-roll" ${isInShift() ? "" : "disabled"}>
            Try again
          </button>
        </div>
      </div>
    </section>
  `;
}

function renderReadinessInsteadOfPool() {
  saveWorkspaceActive(false);
  readyTransitionActive = false;
  boardLoading = false;
  stopWorkspaceSlaCountdown();
  closeSocket();
  tickets = [];
  selectedPoolStatus = "investigating";
  closeAllHeaderFilterComboboxes({ render: false });
  resetDetailWorkspaceState();
  if (filterControlsEl) {
    filterControlsEl.innerHTML = "";
  }
  if (workspaceRegionEl) {
    workspaceRegionEl.innerHTML = "";
  }
  if (getSelectedEngineer()) {
    showWorkspaceShell("home");
    filterControlsEl?.classList.add("hidden");
    renderWorkspaceAssignmentSidebar();
    if (workspaceRegionEl) {
      workspaceRegionEl.innerHTML = renderWelcomeViewHtml();
    }
    ensureWorkspaceServiceEventsLoaded();
  } else {
    toggleScreens();
    renderLogin();
  }
  setRealtimeStatus("Realtime: signed out");
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

  renderReadinessInsteadOfPool();
}

function engineerCaseRouteId(ticket) {
  return String(ticket?.engineer_case_id || ticket?.ticket_id || ticket?.id || "").trim();
}

function findNextInvestigatingCase(payloadOrTickets, engineerId = currentEngineerId()) {
  const items = Array.isArray(payloadOrTickets)
    ? payloadOrTickets
    : Array.isArray(payloadOrTickets?.tickets)
    ? payloadOrTickets.tickets
    : [];
  const investigatingCases = items.filter(
    (ticket) => normalizeStatusValue(ticket?.status || "open") === "investigating" && engineerCaseRouteId(ticket)
  );
  return (
    investigatingCases.find(
      (ticket) => String(ticket?.assigned_engineer_id || "").trim() === engineerId
    ) ||
    investigatingCases.find((ticket) => !String(ticket?.assigned_engineer_id || "").trim()) ||
    null
  );
}

async function claimEngineerCase(ticketId, engineerId) {
  return fetchJson(`/api/engineer/tickets/${encodeURIComponent(ticketId)}/claim`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ engineer_id: engineerId }),
  });
}

async function readyToRoll() {
  const engineer = getSelectedEngineer();
  if (!engineer) {
    renderLogin();
    return;
  }
  if (!isInShift()) {
    saveWorkspaceActive(false);
    renderWelcome();
    return;
  }

  readyTransitionActive = true;
  renderReadyLoading();
  try {
    const payload = await fetchJson("/api/engineer/tickets?status=investigating");
    if (!readyTransitionActive) {
      return;
    }
    const nextTicket = findNextInvestigatingCase(payload, engineer.id);
    tickets = Array.isArray(payload?.tickets) ? payload.tickets : [];
    if (!nextTicket) {
      readyTransitionActive = false;
      window.location.hash = "";
      renderNoInvestigatingCase("No investigating cases available for automatic assignment right now.");
      return;
    }

    const ticketId = engineerCaseRouteId(nextTicket);
    const claim = await claimEngineerCase(ticketId, engineer.id);
    tickets = tickets.map((ticket) =>
      engineerCaseRouteId(ticket) === ticketId
        ? { ...ticket, assigned_engineer_id: claim.assigned_engineer_id || engineer.id }
        : ticket
    );
    selectedPoolStatus = "investigating";
    rememberWorkspaceCaseSlaStart(ticketId);
    saveWorkspaceActive(true);
    readyTransitionActive = false;
    window.location.hash = `#/tickets/${encodeURIComponent(ticketId)}`;
    await enterBoard({ continuousLoading: true });
  } catch (error) {
    readyTransitionActive = false;
    renderNoInvestigatingCase(`No investigating cases available. Queue check failed: ${error.message}`);
  }
}

async function syncRouteToWorkspace(options = {}) {
  const { silent = true, showLoading = true, continuousLoading = false } = options;
  parseRoute();

  if (routeState.view === "detail" && routeState.ticketId) {
    const nextTicketId = routeState.ticketId;
    const routeChanged = selectedTicketId !== nextTicketId;

    if (routeChanged) {
      resetDetailWorkspaceState();
      selectedTicketId = nextTicketId;
      detailLoading = true;
    }

    if (!continuousLoading) {
      renderTicketDetail();
    }
    if (routeChanged || !selectedTicket) {
      await refreshSelectedTicket({ silent, showLoading: showLoading && !continuousLoading });
    }
    return;
  }

  renderReadinessInsteadOfPool();
}

async function fetchJson(url, options = undefined) {
  const requestOptions = options ? { ...options } : {};
  const timeoutMsCandidate = Number(requestOptions.timeoutMs);
  const timeoutMs =
    Number.isFinite(timeoutMsCandidate) && timeoutMsCandidate > 0
      ? timeoutMsCandidate
      : DEFAULT_FETCH_TIMEOUT_MS;
  delete requestOptions.timeoutMs;

  const timeoutController = createAbortController();
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
    } else if (typeof externalSignal.addEventListener === "function") {
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
    if (externalSignal && typeof externalSignal.removeEventListener === "function") {
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
  const subject = String(ticket.title || ticket.subject || "(No subject)");
  const requester = String(ticket.requester || ticket.customer_id || "Unknown");
  const clientTicketId = String(ticket?.client_ticket_ref?.ticket_id || ticket?.client_ticket_id || "").trim();
  const clientTicketSubject = String(ticket?.client_ticket_ref?.subject || "").trim();
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
    clientTicketId,
    clientTicketSubject,
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
                  ${
                    item.clientTicketId
                      ? `<span><strong>Client Ticket</strong> ${escapeHtml(item.clientTicketId)}${
                          item.clientTicketSubject
                            ? ` · ${escapeHtml(item.clientTicketSubject)}`
                            : ""
                        }</span>`
                      : ""
                  }
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
                ${
                  item.clientTicketId
                    ? `<span><strong>Client Ticket</strong> ${escapeHtml(item.clientTicketId)}${
                        item.clientTicketSubject ? ` · ${escapeHtml(item.clientTicketSubject)}` : ""
                      }</span>`
                    : ""
                }
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
  stopWorkspaceSlaCountdown();
  clearStatusComboboxBlurTimer();
  clearLocalInvestigationThreadState();
  resetDetailRefreshState();
  clearDetailPaneScrollRequest(selectedTicketId, DETAIL_THREAD_PANE);
  clearDetailPaneScrollRequest(selectedTicketId, DETAIL_TIMELINE_PANE);
  detailScheduledScrollPlans = null;
  detailScheduledScrollJobId += 1;
  selectedTicketId = null;
  selectedTicket = null;
  detailLoading = false;
  tellAiDraft = "";
  tellAiDraftRichHtml = "";
  investigationReviseMode = false;
  multiAgentWorkspaceTicketId = null;
  multiAgentRunLoadingTicketId = null;
  multiAgentRunError = "";
  tellAiSubmitting = false;
  hitlFeedbackLoading = false;
  hitlFeedbackRequestSeq += 1;
  investigationComposerToolbarState = buildDefaultComposerToolbarState();
}

function isMultiAgentWorkspaceActiveForTicket(ticketId) {
  const normalized = normalizeDetailTicketId(ticketId);
  if (!normalized) {
    return false;
  }
  return normalizeDetailTicketId(multiAgentWorkspaceTicketId) === normalized;
}

function setMultiAgentWorkspaceActiveForTicket(ticketId, active) {
  const normalized = normalizeDetailTicketId(ticketId);
  if (!normalized) {
    return false;
  }
  if (!active) {
    if (isMultiAgentWorkspaceActiveForTicket(normalized)) {
      multiAgentWorkspaceTicketId = null;
    }
    if (normalizeDetailTicketId(multiAgentRunLoadingTicketId) === normalized) {
      multiAgentRunLoadingTicketId = null;
    }
    multiAgentRunError = "";
    return false;
  }
  multiAgentWorkspaceTicketId = normalized;
  multiAgentRunError = "";
  return true;
}

function toggleMultiAgentWorkspaceForTicket(ticketId) {
  const normalized = normalizeDetailTicketId(ticketId);
  if (!normalized) {
    return false;
  }
  if (isMultiAgentWorkspaceActiveForTicket(normalized)) {
    setMultiAgentWorkspaceActiveForTicket(normalized, false);
    return false;
  }
  return setMultiAgentWorkspaceActiveForTicket(normalized, true);
}

function setInvestigationComposerDraftFromMarkdown(value) {
  tellAiDraft = String(value || "");
  tellAiDraftRichHtml = buildRichComposerHtmlFromMarkdown(tellAiDraft);
  investigationComposerToolbarState = buildDefaultComposerToolbarState();
}

function setInvestigationComposerDraftFromRichHtml(value) {
  const normalizedHtml = normalizeRichComposerHtmlString(value);
  tellAiDraftRichHtml = normalizedHtml;
  tellAiDraft = serializeRichComposerHtmlToMarkdown(normalizedHtml);
}

function ensureInvestigationComposerDraftRichHtml() {
  if (!tellAiDraftRichHtml && tellAiDraft) {
    tellAiDraftRichHtml = buildRichComposerHtmlFromMarkdown(tellAiDraft);
  }
  return tellAiDraftRichHtml || "";
}

function syncInvestigationComposerToolbarStateFromElement(element = getActiveInvestigationComposerElement()) {
  if (!isRichTextComposerElement(element) || isComposerElementDisabled(element)) {
    investigationComposerToolbarState = buildDefaultComposerToolbarState();
    applySharedComposerToolbarStateToButtons(workspaceRegionEl, investigationComposerToolbarState);
    return investigationComposerToolbarState;
  }
  investigationComposerToolbarState = getRichComposerSelectionContext(element);
  applySharedComposerToolbarStateToButtons(workspaceRegionEl, investigationComposerToolbarState);
  return investigationComposerToolbarState;
}

function syncInvestigationComposerDraftStateFromElement(element = getActiveInvestigationComposerElement(), options = {}) {
  if (isRichTextComposerElement(element)) {
    const normalizedHtml = normalizeRichComposerHtmlString(element.innerHTML);
    if (normalizedHtml !== element.innerHTML) {
      element.innerHTML = normalizedHtml;
    }
    tellAiDraftRichHtml = normalizedHtml;
    tellAiDraft = serializeRichComposerHtmlToMarkdown(normalizedHtml);
    syncInvestigationComposerToolbarStateFromElement(element);
    if (options?.selectionBookmark) {
      restoreSharedRichComposerSelectionBookmark(element, options.selectionBookmark);
    }
    return tellAiDraft;
  }
  if (isTextComposerElement(element)) {
    tellAiDraft = String(element.value || "");
    return tellAiDraft;
  }
  return tellAiDraft;
}

function getEngineerComposerRuntime() {
  if (engineerComposerRuntime || !SharedComposer.createRichComposerRuntime) {
    return engineerComposerRuntime;
  }
  engineerComposerRuntime = SharedComposer.createRichComposerRuntime({
    getToolbarRoot: () => workspaceRegionEl,
    onAttach: () => window.alert("Attachments are not available yet."),
    syncState: syncInvestigationComposerDraftStateFromElement,
  });
  return engineerComposerRuntime;
}

function renderInvestigationDecisionHtml({
  draftCustomerReply,
  controlsDisabled,
}) {
  return `
    ${renderInvestigationDraftPreviewHtml({ draftCustomerReply })}
    <div class="detail-investigation-inline-actions">
      <button
        type="button"
        class="btn btn-primary"
        data-detail-action="approve-investigation"
        ${controlsDisabled ? "disabled" : ""}
      >Approve for Guardrail</button>
    </div>
  `;
}

function renderInvestigationDraftPreviewHtml({ draftCustomerReply }) {
  return `
    <div class="detail-investigation-draft">
      <p class="detail-investigation-draft-label">Draft Customer Reply</p>
      <div class="detail-investigation-draft-body">${formatMultiline(
        draftCustomerReply || "Draft reply is not ready yet."
      )}</div>
    </div>
  `;
}

function renderInvestigationClosingStateHtml() {
  return `
    <section
      class="detail-investigation-closing-state"
      role="status"
      aria-live="polite"
      aria-busy="true"
    >
      <span class="loading-spinner loading-spinner-sm" aria-hidden="true"></span>
      <div class="detail-investigation-closing-copy">
        <strong>Running Guardrail Review</strong>
        <p>Running final guardrail review before sending to customer...</p>
      </div>
    </section>
  `;
}

function renderFinalApprovalPendingHtml() {
  return `
    <section
      class="detail-investigation-closing-state"
      role="status"
      aria-live="polite"
      aria-busy="true"
    >
      <span class="loading-spinner loading-spinner-sm" aria-hidden="true"></span>
      <div class="detail-investigation-closing-copy">
        <strong>Final Approving</strong>
        <p>Sending final approved reply and closing this engineer ticket...</p>
      </div>
    </section>
  `;
}

function renderGuardrailFinalReviewHtml({ guardrailPacket, draftCustomerReply, controlsDisabled }) {
  if (!guardrailPacket || typeof guardrailPacket !== "object") {
    return "";
  }
  const decision = String(guardrailPacket.decision || "").trim();
  const checks = guardrailPacket.checks && typeof guardrailPacket.checks === "object" ? guardrailPacket.checks : {};
  const blockers = Array.isArray(guardrailPacket.blockers) ? guardrailPacket.blockers : [];
  const guardrailId = String(guardrailPacket.guardrail_id || "unknown");
  const approved = decision === "approved_for_final_engineer_review";
  const finalReply = String(guardrailPacket.customer_reply || draftCustomerReply || "").trim();

  const checkItems = Object.keys(checks).length
    ? Object.entries(checks)
        .map(
          ([name, detail]) => {
            const label = String(name || "").replace(/_/g, " ");
            const passed = detail && typeof detail === "object" && detail.passed === true;
            const checkDetail = detail && typeof detail === "object" ? String(detail.detail || "") : "";
            return `
              <div class="detail-readiness-check ${passed ? "is-passed" : "is-missing"}">
                <span class="detail-readiness-check-dot" aria-hidden="true"></span>
                <span>${escapeHtml(label)}${checkDetail ? ": " + escapeHtml(checkDetail) : ""}</span>
              </div>
            `;
          }
        )
        .join("")
    : '<div class="detail-readiness-check is-missing"><span class="detail-readiness-check-dot" aria-hidden="true"></span><span>No checks recorded</span></div>';

  const blockersHtml = blockers.length
    ? blockers
        .map(
          (blocker) => `
            <li class="detail-guardrail-blocker-item">${escapeHtml(String(blocker || ""))}</li>
          `
        )
        .join("")
    : "";

  return `
    <section class="panel-card detail-guardrail-final-review">
      <div class="panel-card-head">
        <div>
          <p class="panel-card-kicker">Guardrail Final Review</p>
          <h3 class="panel-card-title">Final Customer Reply</h3>
        </div>
        <span class="detail-readiness-pill ${approved ? "is-ready" : "is-blocked"}">
          ${approved ? "Approved" : "Blocked"}
        </span>
      </div>
      <div class="detail-guardrail-body">
        <div class="detail-guardrail-meta">
          <span class="mono">${escapeHtml(guardrailId)}</span>
        </div>
        <div class="detail-readiness-checks" aria-label="Guardrail checks">
          ${checkItems}
        </div>
        ${
          blockersHtml
            ? `
              <div class="detail-guardrail-blockers">
                <p class="detail-guardrail-blockers-label">Blockers</p>
                <ul class="detail-guardrail-blockers-list">${blockersHtml}</ul>
              </div>
            `
            : ""
        }
        <div class="detail-investigation-draft">
          <p class="detail-investigation-draft-label">Final Customer Reply</p>
          <div class="detail-investigation-draft-body">${formatMultiline(
            finalReply || "Reply is not ready yet."
          )}</div>
        </div>
        ${
          approved
            ? `
              <div class="detail-investigation-inline-actions">
                <button
                  type="button"
                  class="btn btn-primary"
                  data-detail-action="final-approve-investigation"
                  ${controlsDisabled ? "disabled" : ""}
                >Final Approve &amp; Send</button>
                <button
                  type="button"
                  class="btn btn-ghost"
                  data-detail-action="revise-investigation"
                  ${controlsDisabled ? "disabled" : ""}
                >Ask AI to Revise</button>
              </div>
            `
            : `
              <div class="detail-investigation-inline-actions">
                <button
                  type="button"
                  class="btn btn-ghost"
                  data-detail-action="revise-investigation"
                  ${controlsDisabled ? "disabled" : ""}
                >Ask AI to Revise</button>
              </div>
            `
        }
      </div>
    </section>
  `;
}

function renderInvestigationComposerHtml({ draft, controlsDisabled, reviseMode, approvalMode = false }) {
  const revisionMode = reviseMode || approvalMode;
  const placeholder = revisionMode
    ? `If the draft needs changes, tell ${ENGINEER_AI_DISPLAY_NAME} what to revise before replying to the customer...`
    : `Share the next technical detail for ${ENGINEER_AI_DISPLAY_NAME}. Include your conclusion, proof, and solution or next step when you have them...`;
  const submitLabel = revisionMode ? "Send Revision Note" : "Send Update";
  const canCompose = !controlsDisabled;

  return `
    <div class="detail-investigation-composer">
      <div class="detail-investigation-composer-shell new-ticket-composer-panel">
        <div class="new-ticket-composer-toolbar">
          ${renderSharedComposerFormattingToolbarButtons({
            canCompose,
            toolbarState: investigationComposerToolbarState,
          })}
        </div>
        <form class="chat-input-inner new-ticket-composer-form detail-investigation-composer-form">
          <div class="new-ticket-composer-input-shell">
            <div
              id="detail-investigation-input"
              class="textarea new-ticket-textarea detail-textarea composer-rich-input"
              contenteditable="${canCompose ? "true" : "false"}"
              role="textbox"
              aria-multiline="true"
              spellcheck="true"
              data-chat-composer-rich="true"
              data-placeholder="${escapeHtml(placeholder)}"
            >${ensureInvestigationComposerDraftRichHtml() || buildRichComposerHtmlFromMarkdown(draft)}</div>
            <div class="new-ticket-inline-action" data-detail-section="investigation-composer-action">
              <button
                type="button"
                class="composer-icon-button send-btn"
                data-detail-action="send-tell-ai"
                aria-label="${escapeHtml(submitLabel)}"
                title="${escapeHtml(submitLabel)}"
                ${canCompose ? "" : "disabled"}
              >
                <span class="material-symbols-outlined" aria-hidden="true">arrow_upward</span>
              </button>
            </div>
          </div>
        </form>
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

  const compactThread = Boolean(options.compactThread);
  const inlineDecisionIndex = Number.isInteger(options.inlineDecisionIndex)
    ? options.inlineDecisionIndex
    : -1;
  const showInlineConfirmation = Boolean(options.showInlineConfirmation);
  const draftCustomerReply = String(options.draftCustomerReply || "").trim();
  const controlsDisabled = Boolean(options.controlsDisabled);
  const structuredCaseBuddyMessageIndex = Number.isInteger(options.structuredCaseBuddyMessageIndex)
    ? options.structuredCaseBuddyMessageIndex
    : -1;
  const structuredCaseBuddySections = Array.isArray(options.structuredCaseBuddySections)
    ? options.structuredCaseBuddySections
    : [];

  return `
    <div class="message-list${compactThread ? " message-list-compact-thread" : ""}">
      ${messages
        .map((message, index) => {
          const role = String(message.role || "system").toLowerCase();
          const createdAt = formatDateTime(message.created_at);
          const isPendingAi = compactThread && message?.is_pending_ai === true;
          const isLocalError = compactThread && message?.is_local_error === true;
          const sentimentLabel =
            role === "customer" ? normalizeMessageSentimentLabel(message.sentiment_label) : "";
          const shouldRenderDecision =
            showInlineConfirmation && inlineDecisionIndex === index && role === "engineer_ai";
          const shouldRenderStructuredCaseBuddyRequest =
            compactThread &&
            role === "engineer_ai" &&
            structuredCaseBuddyMessageIndex === index &&
            structuredCaseBuddySections.length > 0;
          return `
            <article class="message-item ${roleClass(role)}${isPendingAi ? " message-item-pending-ai" : ""}${isLocalError ? " message-item-local-error" : ""}">
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
              <div class="message-content${isPendingAi ? " message-content-pending-ai" : ""}${shouldRenderStructuredCaseBuddyRequest ? " message-content-structured" : ""}">
                ${
                  isPendingAi
                    ? `
                      <span class="detail-thinking-dots" aria-hidden="true"><span></span><span></span><span></span></span>
                      <span class="detail-thinking-label">${escapeHtml(
                        String(message.content || `${ENGINEER_AI_DISPLAY_NAME} is reviewing your update...`)
                      )}</span>
                    `
                    : shouldRenderStructuredCaseBuddyRequest
                    ? renderCaseBuddyRequestSectionsHtml(structuredCaseBuddySections)
                    : compactThread
                    ? `<div class="message-markdown">${renderMarkdownMessage(String(message.content || ""))}</div>`
                    : formatMultiline(String(message.content || ""))
                }
              </div>
              ${renderMessageAttachments(message)}
              ${
                shouldRenderDecision
                  ? renderInvestigationDecisionHtml({
                      draftCustomerReply,
                      controlsDisabled,
                    })
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

function isTextComposerElement(element) {
  return Boolean(
    element &&
      typeof element === "object" &&
      typeof element.focus === "function" &&
      typeof element.value === "string"
  );
}

function legacyCaptureComposerPreservationState(element) {
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

function legacyRestoreComposerPreservationState(element, snapshot) {
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

function getActiveInvestigationComposerElement() {
  const input = document.getElementById("detail-investigation-input");
  return isTextComposerElement(input) || isRichTextComposerElement(input) ? input : null;
}

function prefersReducedMotion() {
  try {
    return Boolean(window.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches);
  } catch {
    return false;
  }
}

function runOnNextFrame(callback) {
  if (typeof requestAnimationFrame === "function") {
    requestAnimationFrame(callback);
    return;
  }
  setTimeout(callback, 0);
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

function getScrollDistanceFromBottom(element) {
  if (!element || typeof element.scrollHeight !== "number") {
    return Number.POSITIVE_INFINITY;
  }
  const scrollTop = typeof element.scrollTop === "number" ? element.scrollTop : 0;
  const clientHeight = typeof element.clientHeight === "number" ? element.clientHeight : 0;
  return element.scrollHeight - (scrollTop + clientHeight);
}

function buildMessageSignature(messages) {
  if (!Array.isArray(messages) || messages.length === 0) {
    return "";
  }
  return messages
    .map((message) => {
      const role = String(message?.role || "").trim().toLowerCase() || "system";
      const id = String(message?.id || "").trim();
      const createdAt = String(message?.created_at || message?.createdAt || "").trim();
      const content = String(message?.content || "").trim().slice(0, 160);
      return `${role}:${id || createdAt || content}`;
    })
    .join("|");
}

function getDetailPaneKey(ticketId, pane) {
  const normalizedTicketId = normalizeDetailTicketId(ticketId);
  const normalizedPane = String(pane || "").trim().toLowerCase();
  return normalizedTicketId && normalizedPane ? `${normalizedTicketId}:${normalizedPane}` : "";
}

function getDetailPaneState(ticketId, pane) {
  const key = getDetailPaneKey(ticketId, pane);
  if (!key) {
    return {
      signature: "",
    };
  }
  return (
    detailPaneStateByKey[key] || {
      signature: "",
    }
  );
}

function setDetailPaneState(ticketId, pane, patch) {
  const key = getDetailPaneKey(ticketId, pane);
  if (!key) {
    return;
  }
  detailPaneStateByKey[key] = {
    ...getDetailPaneState(ticketId, pane),
    ...(patch && typeof patch === "object" ? patch : {}),
  };
}

function requestDetailPaneScrollToBottom(ticketId, pane, options = {}) {
  const key = getDetailPaneKey(ticketId, pane);
  if (!key) {
    return;
  }
  detailPendingScrollRequestByKey[key] = {
    behavior: String(options?.behavior || "").trim().toLowerCase() === "smooth" ? "smooth" : "auto",
  };
}

function clearDetailPaneScrollRequest(ticketId, pane) {
  const key = getDetailPaneKey(ticketId, pane);
  if (!key) {
    return;
  }
  delete detailPendingScrollRequestByKey[key];
}

function getDetailPaneScrollElement(pane) {
  if (!workspaceRegionEl || typeof workspaceRegionEl.querySelector !== "function") {
    return null;
  }
  if (pane === DETAIL_THREAD_PANE) {
    return workspaceRegionEl.querySelector(".detail-conversation-thread-body");
  }
  if (pane === DETAIL_TIMELINE_PANE) {
    return workspaceRegionEl.querySelector(".detail-timeline-panel .message-list");
  }
  return null;
}

function buildDetailPaneElementSnapshot(element, scheduledPlan = null) {
  if (scheduledPlan?.type === "bottom") {
    return {
      scrollTop: typeof element?.scrollHeight === "number" ? element.scrollHeight : null,
      nearBottom: true,
      preserveBottom: true,
      behavior: scheduledPlan.behavior || "auto",
    };
  }
  if (scheduledPlan?.type === "restore") {
    return {
      scrollTop: typeof scheduledPlan.scrollTop === "number" ? scheduledPlan.scrollTop : null,
      behavior: scheduledPlan.behavior || "auto",
    };
  }
  return {
    scrollTop: typeof element?.scrollTop === "number" ? element.scrollTop : null,
  };
}

function captureDetailPaneScrollSnapshot() {
  const ticketId = normalizeDetailTicketId(selectedTicketId);
  if (routeState.view !== "detail" || !ticketId) {
    return null;
  }
  const scheduledPlans = detailScheduledScrollPlans?.ticketId === ticketId ? detailScheduledScrollPlans : null;
  const threadElement = getDetailPaneScrollElement(DETAIL_THREAD_PANE);
  const timelineElement = getDetailPaneScrollElement(DETAIL_TIMELINE_PANE);
  return {
    ticketId,
    thread: buildDetailPaneElementSnapshot(threadElement, scheduledPlans?.thread || null),
    timeline: buildDetailPaneElementSnapshot(timelineElement, scheduledPlans?.timeline || null),
  };
}

function syncDetailPaneScrollPosition(previousSnapshot, viewState) {
  if (!viewState?.ticketId) {
    detailScheduledScrollPlans = null;
    detailScheduledScrollJobId += 1;
    return;
  }

  const ticketId = normalizeDetailTicketId(viewState.ticketId);
  const paneDefinitions = [
    {
      pane: DETAIL_THREAD_PANE,
      signature: buildMessageSignature(viewState.investigationMessages),
      previous: previousSnapshot?.thread || null,
    },
    {
      pane: DETAIL_TIMELINE_PANE,
      signature: buildMessageSignature(viewState.messages),
      previous: previousSnapshot?.timeline || null,
    },
  ];

  const nextPlans = {
    ticketId,
    thread: null,
    timeline: null,
  };

  paneDefinitions.forEach(({ pane, signature, previous }) => {
    const state = getDetailPaneState(ticketId, pane);
    const request = detailPendingScrollRequestByKey[getDetailPaneKey(ticketId, pane)] || null;
    const hasNewMessages = Boolean(state.signature) && signature !== state.signature;
    const shouldRestore =
      previousSnapshot?.ticketId === ticketId && typeof previous?.scrollTop === "number";

    if (request) {
      nextPlans[pane] = {
        type: "bottom",
        behavior: request.behavior || "auto",
      };
      clearDetailPaneScrollRequest(ticketId, pane);
    } else if (hasNewMessages) {
      nextPlans[pane] = {
        type: "bottom",
        behavior: "smooth",
      };
    } else if (previous?.preserveBottom) {
      nextPlans[pane] = {
        type: "bottom",
        behavior: previous.behavior || "auto",
      };
    } else if (shouldRestore) {
      nextPlans[pane] = {
        type: "restore",
        scrollTop: previous.scrollTop,
      };
    }

    setDetailPaneState(ticketId, pane, {
      signature,
    });
  });

  if (!nextPlans.thread && !nextPlans.timeline) {
    detailScheduledScrollPlans = null;
    detailScheduledScrollJobId += 1;
    return;
  }

  detailScheduledScrollPlans = nextPlans;
  detailScheduledScrollJobId += 1;
  const jobId = detailScheduledScrollJobId;
  runOnNextFrame(() => {
    if (jobId !== detailScheduledScrollJobId || !detailScheduledScrollPlans || detailScheduledScrollPlans.ticketId !== ticketId) {
      return;
    }
    [DETAIL_THREAD_PANE, DETAIL_TIMELINE_PANE].forEach((pane) => {
      const plan = detailScheduledScrollPlans[pane];
      const element = getDetailPaneScrollElement(pane);
      if (!plan || !element) {
        return;
      }
      if (plan.type === "bottom") {
        if (typeof element.scrollHeight !== "number") {
          return;
        }
        scrollElementToTop(element, element.scrollHeight, plan.behavior || "auto");
        return;
      }
      scrollElementToTop(element, plan.scrollTop, plan.behavior || "auto");
    });
    detailScheduledScrollPlans = null;
  });
}

function buildTicketDetailViewState() {
  const ticket = selectedTicket;
  const ticketId = String(ticket.ticket_id || selectedTicketId || "-");
  const clientTicketId = String(ticket?.client_ticket_ref?.ticket_id || ticket?.client_ticket_id || "").trim();
  const clientTicketSubject = String(ticket?.client_ticket_ref?.subject || "").trim();
  const status = normalizeStatusValue(ticket.status || "open");
  const requester = String(ticket.requester || ticket.customer_id || "Unknown");
  const activeInvestigation = getActiveInvestigation(ticket);
  const displayInvestigation = getDisplayInvestigation(ticket);
  const investigationState = String(displayInvestigation?.state || "active").toLowerCase();
  const investigationPreview = latestInvestigationUpdate(ticket);
  const durableInvestigationMessages = Array.isArray(displayInvestigation?.messages)
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
  const investigationMessages = mergeInvestigationMessagesWithLocalState(ticketId, durableInvestigationMessages);
  const pendingLocalReply = hasPendingLocalInvestigationReply(ticketId);
  const pendingLocalApproval = hasPendingLocalInvestigationApproval(ticketId);
  const approvalUiState = getInvestigationApprovalUiState(ticket, activeInvestigation, investigationMessages, {
    suppressApprovalBlock: pendingLocalReply || pendingLocalApproval,
  });
  const showApproveInFlightState = pendingLocalApproval && !approvalUiState.awaitingFinalApproval;
  const showFinalApprovalInFlight = pendingLocalApproval && approvalUiState.awaitingFinalApproval;
  const draftCustomerReply = String(displayInvestigation?.draft_customer_reply || "").trim();
  const guardrailPacket = approvalUiState.activeGuardrailFinal;
  const guardrailFinalBlocked = String(guardrailPacket?.decision || "").trim() === "blocked";
  const showGuardrailFinalReview =
    (approvalUiState.awaitingFinalApproval || guardrailFinalBlocked) && guardrailPacket && !showFinalApprovalInFlight;
  const guardrailFinalReviewHtml = showGuardrailFinalReview
    ? renderGuardrailFinalReviewHtml({
        guardrailPacket,
        draftCustomerReply,
        controlsDisabled: tellAiSubmitting,
      })
    : "";
  const openingCaseBuddyMessageIndex = findOpeningCaseBuddyMessageIndex(investigationMessages);
  const structuredCaseBuddySections =
    openingCaseBuddyMessageIndex >= 0
      ? buildCaseBuddyOpeningRequestSections(ticket, investigationMessages[openingCaseBuddyMessageIndex]?.content)
      : [];

  const engineerAgentState = getEngineerAgentState(ticket) || {};
  const activePlan =
    engineerAgentState.active_plan && typeof engineerAgentState.active_plan === "object"
      ? engineerAgentState.active_plan
      : {};
  const activeExecution =
    engineerAgentState.active_execution && typeof engineerAgentState.active_execution === "object"
      ? engineerAgentState.active_execution
      : {};
  const activeReview =
    engineerAgentState.active_review && typeof engineerAgentState.active_review === "object"
      ? engineerAgentState.active_review
      : {};
  const hasMultiAgentState = Boolean(activePlan.plan_id || activeExecution.execution_id || activeReview.review_id);
  const isMultiAgentWorkspace =
    isMultiAgentWorkspaceActiveForTicket(ticketId) && status === "investigating";
  const isMultiAgentRunLoading = normalizeDetailTicketId(multiAgentRunLoadingTicketId) === ticketId;
  const multiAgentWorkspacePanelHtml = isMultiAgentWorkspace
    ? renderMultiAgentWorkspacePanelHtml({
        activePlan,
        activeExecution,
        activeReview,
        hasMultiAgentState,
        isLoading: isMultiAgentRunLoading,
        errorMessage: isMultiAgentRunLoading ? "" : multiAgentRunError,
      })
    : "";

  return {
    ticket,
    ticketId,
    clientTicketId,
    clientTicketSubject,
    status,
    requester,
    activeInvestigation,
    displayInvestigation,
    investigationState,
    investigationMessages,
    approvalUiState,
    openingCaseBuddyMessageIndex,
    structuredCaseBuddySections,
    replyReadinessReviewHtml: showApproveInFlightState || showFinalApprovalInFlight ? "" : renderReplyReadinessReviewHtml(ticket, activeInvestigation),
    showInlineConfirmation: approvalUiState.showApprovalBlock,
    showApproveInFlightState,
    showFinalApprovalInFlight,
    showGuardrailFinalReview,
    guardrailFinalBlocked,
    guardrailFinalReviewHtml,
    showInvestigationComposer:
      Boolean(activeInvestigation) &&
      !showApproveInFlightState &&
      !showFinalApprovalInFlight &&
      (!showGuardrailFinalReview || investigationReviseMode || guardrailFinalBlocked),
    showInvestigationDraftPreview:
      Boolean(activeInvestigation) &&
      Boolean(draftCustomerReply) &&
      !approvalUiState.showApprovalBlock &&
      !showApproveInFlightState &&
      !showGuardrailFinalReview,
    hitlFeedbackPanelHtml: isMultiAgentWorkspace ? renderHitlFeedbackPanelHtml(ticket) : "",
    draftCustomerReply,
    controlsDisabled: tellAiSubmitting,
    messages: Array.isArray(ticket.messages) ? ticket.messages : [],
    isMultiAgentWorkspace,
    hasMultiAgentState,
    activePlan,
    activeExecution,
    activeReview,
    isMultiAgentRunLoading,
    multiAgentWorkspacePanelHtml,
  };
}

function renderTicketDetailHeaderHtml(viewState) {
  return `
    <header class="workspace-header">
      <div class="workspace-header-line workspace-header-line-primary">
        <button
          class="workspace-ticket-id workspace-ticket-id-button"
          type="button"
          data-detail-action="back-to-workspace-home"
          aria-label="Return to workspace home"
          title="Return to workspace home"
        >${escapeHtml(viewState.ticketId)}</button>
        <h2 class="workspace-ticket-title">${escapeHtml(
          String(viewState.ticket.title || viewState.ticket.subject || "(No subject)")
        )}</h2>
        ${renderTicketDetailStatusBadgeHtml(viewState)}
        ${renderWorkspaceCaseControlsHtml(viewState)}
      </div>
      <div class="workspace-header-line workspace-header-line-secondary">
        ${
          viewState.clientTicketId
            ? `<span class="workspace-header-meta-primary">Client Ticket ${escapeHtml(viewState.clientTicketId)}${
                viewState.clientTicketSubject ? ` · ${escapeHtml(viewState.clientTicketSubject)}` : ""
              }</span>`
            : ""
        }
        <span>Requester ${escapeHtml(viewState.requester)}</span>
        <span>Created ${escapeHtml(formatDateTime(viewState.ticket.created_at))}</span>
        <span>Updated ${escapeHtml(formatDateTime(viewState.ticket.updated_at))}</span>
      </div>
    </header>
  `;
}

function renderWorkspaceCaseControlsHtml(viewState) {
  const sla = workspaceTicketSlaState(viewState?.ticket);
  return `
    <div class="workspace-case-controls">
      <span class="current-ticket-sla ${escapeHtml(sla.className)}" data-sla-countdown>${escapeHtml(
        getWorkspaceSlaCountdownLabel(sla)
      )}</span>
      <button
        class="btn btn-ghost break-after-case-btn ${workspaceBreakAfterCase ? "is-active" : ""}"
        type="button"
        data-action="toggle-break-after-case"
        data-detail-action="toggle-break-after-case"
        aria-pressed="${workspaceBreakAfterCase ? "true" : "false"}"
      >
        <span class="material-symbols-outlined" aria-hidden="true">free_cancellation</span>
        ${workspaceBreakAfterCase ? "Break queued after this case" : "Break after this case"}
      </button>
    </div>
  `;
}

function renderTicketDetailStatusBadgeHtml(viewState) {
  const label = escapeHtml(statusLabel(viewState.status));
  const classes = `status-badge status-badge-compact ${statusClass(viewState.status)}`;
  if (viewState.status !== "investigating") {
    return `<span class="${classes}">${label}</span>`;
  }
  const isActive = Boolean(viewState.isMultiAgentWorkspace);
  const isLoading = Boolean(viewState.isMultiAgentRunLoading);
  return `
    <button
      type="button"
      class="${classes} status-badge-investigating-toggle${isActive ? " is-active" : ""}"
      data-detail-action="toggle-multi-agent-workspace"
      data-multi-agent-toggle="${escapeHtml(viewState.ticketId)}"
      aria-pressed="${isActive ? "true" : "false"}"
      aria-label="Run multi-agent investigation"
      title="Run multi-agent investigation"
      ${isLoading ? "disabled" : ""}
    >${isLoading ? "Investigating..." : label}</button>
  `;
}

function renderTicketDetailConversationStaticHtml(viewState) {
  return `
    <div class="panel-card-head">
      <div>
        <p class="panel-card-kicker">Engineer Ticket</p>
        <h3 class="panel-card-title">Engineer Ticket Thread</h3>
      </div>
    </div>
  `;
}

function renderTicketDetailConversationBodyHtml(viewState) {
  return `
    ${
      viewState.investigationMessages.length
        ? renderConversationHtml(viewState.investigationMessages, {
            compactThread: true,
            inlineDecisionIndex: viewState.showInlineConfirmation
              ? viewState.approvalUiState.decisionIndex
              : -1,
            showInlineConfirmation:
              viewState.showInlineConfirmation && viewState.approvalUiState.decisionIndex >= 0,
            draftCustomerReply: viewState.draftCustomerReply,
            controlsDisabled: viewState.controlsDisabled,
            structuredCaseBuddyMessageIndex: viewState.openingCaseBuddyMessageIndex,
            structuredCaseBuddySections: viewState.structuredCaseBuddySections,
          })
        : '<div class="empty-state">No open engineer ticket yet.</div>'
    }
    ${
      viewState.showInvestigationDraftPreview
        ? renderInvestigationDraftPreviewHtml({ draftCustomerReply: viewState.draftCustomerReply })
        : ""
    }
  `;
}

function renderTicketDetailComposerShellHtml(viewState) {
  if (viewState.showApproveInFlightState) {
    return renderInvestigationClosingStateHtml();
  }
  if (viewState.showFinalApprovalInFlight) {
    return renderFinalApprovalPendingHtml();
  }
  if (viewState.showGuardrailFinalReview) {
    if (!viewState.showInvestigationComposer) {
      return viewState.guardrailFinalReviewHtml;
    }
    return `
      ${viewState.guardrailFinalReviewHtml}
      ${renderInvestigationComposerHtml({
        draft: tellAiDraft,
        controlsDisabled: viewState.controlsDisabled,
        reviseMode: true,
        approvalMode: true,
      })}
    `;
  }
  if (!viewState.showInvestigationComposer) {
    return "";
  }
  return renderInvestigationComposerHtml({
    draft: tellAiDraft,
    controlsDisabled: viewState.controlsDisabled,
    reviseMode: investigationReviseMode,
    approvalMode: viewState.approvalUiState.showApprovalBlock,
  });
}

function renderTicketDetailInsightPanelHtml(viewState) {
  return `
    <section class="panel-card detail-timeline-panel">
      <div class="panel-card-head">
        <div>
          <p class="panel-card-kicker">Customer Timeline</p>
          <h3 class="panel-card-title">Customer Timeline</h3>
        </div>
      </div>
      <div class="detail-timeline-body">
        ${renderConversationHtml(viewState.messages)}
      </div>
    </section>
    ${viewState.multiAgentWorkspacePanelHtml}
    ${viewState.replyReadinessReviewHtml}
    ${viewState.hitlFeedbackPanelHtml}
  `;
}

function renderMultiAgentWorkspacePanelHtml(viewState) {
  const {
    activePlan = {},
    activeExecution = {},
    activeReview = {},
    hasMultiAgentState = false,
    isLoading = false,
    errorMessage = "",
  } = viewState || {};
  // This panel only renders when the multi-agent workspace is toggled on for
  // the current ticket, so the active-mode status line always applies here.
  const activeStatusHtml =
    '<p class="detail-multi-agent-status">Multi-agent mode is active for this ticket.</p>';
  if (isLoading) {
    return `
      <section class="panel-card detail-multi-agent-panel">
        <div class="panel-card-head">
          <div>
            <p class="panel-card-kicker">Multi-Agent Run</p>
            <h3 class="panel-card-title">Multi-Agent Run</h3>
          </div>
        </div>
        <div class="detail-multi-agent-body">
          ${activeStatusHtml}
          <div class="empty-state detail-multi-agent-loading" role="status" aria-live="polite" aria-busy="true">
            <span class="loading-spinner loading-spinner-sm" aria-hidden="true"></span>
            Running Plan / Execute / Review...
          </div>
        </div>
      </section>
    `;
  }
  if (String(errorMessage || "").trim()) {
    return `
      <section class="panel-card detail-multi-agent-panel">
        <div class="panel-card-head">
          <div>
            <p class="panel-card-kicker">Multi-Agent Run</p>
            <h3 class="panel-card-title">Multi-Agent Run</h3>
          </div>
        </div>
        <div class="detail-multi-agent-body">
          ${activeStatusHtml}
          <div class="empty-state detail-multi-agent-error" role="alert">
            Multi-agent run failed: ${escapeHtml(String(errorMessage).trim())}
          </div>
        </div>
      </section>
    `;
  }
  if (!hasMultiAgentState) {
    return `
      <section class="panel-card detail-multi-agent-panel">
        <div class="panel-card-head">
          <div>
            <p class="panel-card-kicker">Multi-Agent Run</p>
            <h3 class="panel-card-title">Multi-Agent Run</h3>
          </div>
        </div>
        <div class="detail-multi-agent-body">
          ${activeStatusHtml}
          <div class="empty-state">No multi-agent run captured for this ticket yet.</div>
        </div>
      </section>
    `;
  }

  return `
    <section class="panel-card detail-multi-agent-panel">
      <div class="panel-card-head">
        <div>
          <p class="panel-card-kicker">Multi-Agent Run</p>
          <h3 class="panel-card-title">Multi-Agent Run</h3>
        </div>
      </div>
      <div class="detail-multi-agent-body">
        ${activeStatusHtml}
        ${renderMultiAgentPlanStageHtml(activePlan)}
        ${renderMultiAgentExecuteStageHtml(activeExecution)}
        ${renderMultiAgentReviewStageHtml(activeReview)}
      </div>
    </section>
  `;
}

function renderMultiAgentFieldHtml(label, value, { fallback = "—" } = {}) {
  const text = String(value ?? "").trim();
  return `
    <div class="detail-readiness-field">
      <p class="detail-readiness-field-label">${escapeHtml(label)}</p>
      <p class="detail-readiness-field-value">${formatMultiline(text || fallback)}</p>
    </div>
  `;
}

function renderMultiAgentListFieldHtml(label, items) {
  const list = Array.isArray(items) ? items.filter((item) => String(item ?? "").trim()) : [];
  return `
    <div class="detail-readiness-field">
      <p class="detail-readiness-field-label">${escapeHtml(label)}</p>
      ${
        list.length
          ? `<ul class="detail-readiness-list">${list
              .map((item) => `<li>${escapeHtml(String(item))}</li>`)
              .join("")}</ul>`
          : `<p class="detail-readiness-field-value">—</p>`
      }
    </div>
  `;
}

function renderMultiAgentDecisionPillHtml(value) {
  const normalized = String(value || "").trim().toLowerCase();
  const tone = normalized === "ready_for_engineer" ? "is-ready" : "is-blocked";
  const label = String(value || "Pending").trim() || "Pending";
  return `<span class="detail-readiness-pill ${tone} detail-agent-decision-pill">${escapeHtml(label)}</span>`;
}

function renderMultiAgentPlanStageHtml(activePlan) {
  const planId = String(activePlan.plan_id || "").trim();
  const planVersion = String(activePlan.plan_version || "").trim();
  const planAgentVersion = String(activePlan.plan_agent_version || "").trim();
  const memoryContext =
    activePlan.memory_context && typeof activePlan.memory_context === "object" ? activePlan.memory_context : {};
  const skillContext =
    activePlan.skill_context && typeof activePlan.skill_context === "object" ? activePlan.skill_context : {};
  const hypotheses = Array.isArray(activePlan.hypotheses) ? activePlan.hypotheses : [];
  const tasks = Array.isArray(activePlan.tasks) ? activePlan.tasks : [];
  const dependencies = Array.isArray(activePlan.dependencies) ? activePlan.dependencies : [];
  const schedulerHints =
    activePlan.scheduler_hints && typeof activePlan.scheduler_hints === "object" ? activePlan.scheduler_hints : {};

  return `
    <section class="detail-agent-stage" aria-label="Plan Agent output">
      <div class="panel-card-head">
        <div>
          <p class="panel-card-kicker">Plan Agent</p>
          <h3 class="panel-card-title">Plan</h3>
        </div>
        <span class="detail-readiness-pill detail-agent-decision-pill">${escapeHtml(
          String(memoryContext.mode || "fallback") + " / " + String(skillContext.mode || "fallback")
        )}</span>
      </div>
      <div class="detail-readiness-fields">
        ${renderMultiAgentFieldHtml("Plan ID", planId)}
        ${renderMultiAgentFieldHtml("Plan version", planVersion)}
        ${renderMultiAgentFieldHtml("Plan agent version", planAgentVersion)}
        ${renderMultiAgentFieldHtml("Objective", activePlan.objective)}
        ${renderMultiAgentListFieldHtml("Hypotheses", hypotheses.map((item) => (item && typeof item === "object" ? item.summary || item.title || JSON.stringify(item) : String(item))))}
        ${renderMultiAgentPlanTasksHtml(tasks)}
        ${renderMultiAgentListFieldHtml(
          "Dependencies",
          dependencies.map((item) => (item && typeof item === "object" ? item.description || item.dependency_id || JSON.stringify(item) : String(item)))
        )}
        ${renderMultiAgentFieldHtml("Scheduler hints", schedulerHints.note || schedulerHints.summary || JSON.stringify(schedulerHints))}
      </div>
    </section>
  `;
}

function renderMultiAgentPlanTasksHtml(tasks) {
  const list = tasks.filter((task) => task && typeof task === "object");
  if (!list.length) {
    return renderMultiAgentFieldHtml("Tasks", "", { fallback: "No planned tasks." });
  }
  return `
    <div class="detail-readiness-field">
      <p class="detail-readiness-field-label">Tasks</p>
      <ul class="detail-agent-task-list">
        ${list
          .map((task) => {
            const taskId = escapeHtml(String(task.task_id || ""));
            const skill = escapeHtml(String(task.skill || ""));
            const title = escapeHtml(String(task.title || task.description || ""));
            const dependsOn = Array.isArray(task.depends_on) ? task.depends_on.filter(Boolean) : [];
            const canParallelize = task.can_parallelize === true;
            const hints = [
              dependsOn.length ? `depends on ${dependsOn.join(", ")}` : "",
              canParallelize ? "parallelizable" : "serial",
            ]
              .filter(Boolean)
              .join(" · ");
            return `
              <li>
                <span class="detail-agent-task-id">${taskId}</span>
                <span class="detail-agent-task-skill">${skill}</span>
                <span class="detail-agent-task-title">${title}</span>
                ${hints ? `<span class="detail-agent-task-hint">${escapeHtml(hints)}</span>` : ""}
              </li>
            `;
          })
          .join("")}
      </ul>
    </div>
  `;
}

function renderMultiAgentExecuteStageHtml(activeExecution) {
  const executionId = String(activeExecution.execution_id || "").trim();
  const executionVersion = String(activeExecution.execution_version || "").trim();
  const executeAgentVersion = String(activeExecution.execute_agent_version || "").trim();
  const status = String(activeExecution.status || "").trim();
  const scheduler =
    activeExecution.scheduler && typeof activeExecution.scheduler === "object" ? activeExecution.scheduler : {};
  const executionOrder = Array.isArray(scheduler.execution_order) ? scheduler.execution_order : [];
  const taskResults = Array.isArray(activeExecution.task_results) ? activeExecution.task_results : [];

  return `
    <section class="detail-agent-stage" aria-label="Execute Agent output">
      <div class="panel-card-head">
        <div>
          <p class="panel-card-kicker">Execute Agent</p>
          <h3 class="panel-card-title">Execution</h3>
        </div>
        ${renderMultiAgentDecisionPillHtml(status || "pending")}
      </div>
      <div class="detail-readiness-fields">
        ${renderMultiAgentFieldHtml("Execution ID", executionId)}
        ${renderMultiAgentFieldHtml("Execution version", executionVersion)}
        ${renderMultiAgentFieldHtml("Execute agent version", executeAgentVersion)}
        ${renderMultiAgentFieldHtml("Status", status)}
        ${renderMultiAgentFieldHtml("Scheduler mode", scheduler.mode)}
        ${renderMultiAgentExecutionOrderHtml(executionOrder)}
        ${renderMultiAgentTaskResultsHtml(taskResults)}
      </div>
    </section>
  `;
}

function renderMultiAgentExecutionOrderHtml(executionOrder) {
  const stages = executionOrder.filter((stage) => stage && typeof stage === "object");
  if (!stages.length) {
    return renderMultiAgentFieldHtml("Execution order", "", { fallback: "No execution order captured." });
  }
  const text = stages
    .map((stage, index) => {
      const taskIds = Array.isArray(stage.task_ids) ? stage.task_ids.filter(Boolean) : [];
      return `Stage ${index + 1}: ${taskIds.join(", ") || "—"}`;
    })
    .join("\n");
  return `
    <div class="detail-readiness-field">
      <p class="detail-readiness-field-label">Execution order</p>
      <p class="detail-readiness-field-value">${formatMultiline(text)}</p>
    </div>
  `;
}

function renderMultiAgentTaskResultsHtml(taskResults) {
  const list = taskResults.filter((result) => result && typeof result === "object");
  if (!list.length) {
    return renderMultiAgentFieldHtml("Task results", "", { fallback: "No task results captured." });
  }
  return `
    <div class="detail-readiness-field">
      <p class="detail-readiness-field-label">Task results</p>
      <ul class="detail-agent-task-list">
        ${list
          .map((result) => {
            const taskId = escapeHtml(String(result.task_id || ""));
            const skill = escapeHtml(String(result.skill || ""));
            const taskStatus = escapeHtml(String(result.status || ""));
            const summary = escapeHtml(String(result.summary || ""));
            const missing = Array.isArray(result.missing_information)
              ? result.missing_information.filter(Boolean)
              : [];
            return `
              <li>
                <span class="detail-agent-task-id">${taskId}</span>
                <span class="detail-agent-task-skill">${skill}</span>
                <span class="detail-agent-task-title">${taskStatus}</span>
                <span class="detail-agent-task-summary">${summary}</span>
                ${
                  missing.length
                    ? `<span class="detail-agent-task-hint">missing: ${escapeHtml(missing.join("; "))}</span>`
                    : ""
                }
              </li>
            `;
          })
          .join("")}
      </ul>
    </div>
  `;
}

function renderMultiAgentReviewStageHtml(activeReview) {
  const reviewId = String(activeReview.review_id || "").trim();
  const reviewVersion = String(activeReview.review_version || "").trim();
  const reviewAgentVersion = String(activeReview.review_agent_version || "").trim();
  const reviewDecision = String(activeReview.review_decision || "").trim();
  const replanCount = activeReview.replan_count;
  const evidenceGaps = Array.isArray(activeReview.evidence_gaps) ? activeReview.evidence_gaps : [];
  const missingInformation = Array.isArray(activeReview.missing_information)
    ? activeReview.missing_information
    : [];

  return `
    <section class="detail-agent-stage" aria-label="Review Agent output">
      <div class="panel-card-head">
        <div>
          <p class="panel-card-kicker">Review Agent</p>
          <h3 class="panel-card-title">Review</h3>
        </div>
        ${renderMultiAgentDecisionPillHtml(reviewDecision || "pending")}
      </div>
      <div class="detail-readiness-fields">
        ${renderMultiAgentFieldHtml("Review ID", reviewId)}
        ${renderMultiAgentFieldHtml("Review version", reviewVersion)}
        ${renderMultiAgentFieldHtml("Review agent version", reviewAgentVersion)}
        ${renderMultiAgentFieldHtml("Review decision", reviewDecision)}
        ${renderMultiAgentFieldHtml("Replan count", replanCount)}
        ${renderMultiAgentFieldHtml("Problem statement", activeReview.problem_statement)}
        ${renderMultiAgentFieldHtml("Decision rationale", activeReview.decision_rationale)}
        ${renderMultiAgentFieldHtml("Recommended action", activeReview.recommended_action)}
        ${renderMultiAgentListFieldHtml("Evidence gaps", evidenceGaps)}
        ${renderMultiAgentListFieldHtml("Missing information", missingInformation)}
      </div>
    </section>
  `;
}

function renderTicketDetailViewFromState(viewState) {
  return `
    <section class="ticket-workspace" data-detail-ticket-id="${escapeHtml(viewState.ticketId)}">
      <div data-detail-section="header">${renderTicketDetailHeaderHtml(viewState)}</div>
      <div class="workspace-layout">
        <section
          class="panel-card conversation-panel conversation-panel-compact-thread"
          data-detail-section="investigation-panel"
        >
          <div class="detail-conversation-static" data-detail-section="investigation-static">
            ${renderTicketDetailConversationStaticHtml(viewState)}
          </div>
          <div class="detail-conversation-thread-body" data-detail-section="investigation-thread-body">
            ${renderTicketDetailConversationBodyHtml(viewState)}
          </div>
          <div data-detail-section="investigation-composer-shell">
            ${renderTicketDetailComposerShellHtml(viewState)}
          </div>
        </section>

        <aside class="insight-panel" data-detail-section="insight">
          ${renderTicketDetailInsightPanelHtml(viewState)}
        </aside>
      </div>
    </section>
  `;
}

function getWorkspaceSlaCountdownNodes() {
  const nodes = [];
  if (workspaceRegionEl && typeof workspaceRegionEl.querySelectorAll === "function") {
    nodes.push(...Array.from(workspaceRegionEl.querySelectorAll("[data-sla-countdown]")));
  }
  if (workspaceAssignmentSidebarEl && typeof workspaceAssignmentSidebarEl.querySelectorAll === "function") {
    nodes.push(...Array.from(workspaceAssignmentSidebarEl.querySelectorAll("[data-sla-countdown]")));
  }
  return nodes;
}

function updateWorkspaceSlaCountdown() {
  const sla = workspaceTicketSlaState(selectedTicket);
  const label = getWorkspaceSlaCountdownLabel(sla);
  getWorkspaceSlaCountdownNodes().forEach((node) => {
    node.textContent = label;
    node.className = `current-ticket-sla ${sla.className}`;
  });
  return sla;
}

function stopWorkspaceSlaCountdown() {
  if (workspaceSlaCountdownTimer) {
    clearInterval(workspaceSlaCountdownTimer);
    workspaceSlaCountdownTimer = null;
  }
}

function startWorkspaceSlaCountdown() {
  stopWorkspaceSlaCountdown();
  if (!selectedTicketId) {
    return;
  }
  const initialSla = updateWorkspaceSlaCountdown();
  if (initialSla.overdue) {
    return;
  }
  workspaceSlaCountdownTimer = setInterval(() => {
    if (!selectedTicketId) {
      stopWorkspaceSlaCountdown();
      return;
    }
    const sla = updateWorkspaceSlaCountdown();
    if (sla.overdue) {
      stopWorkspaceSlaCountdown();
    }
  }, 1000);
}

function shouldPreserveInvestigationComposerOnRender(viewState) {
  if (!viewState?.showInvestigationComposer || viewState.controlsDisabled) {
    return false;
  }
  return Boolean(captureComposerPreservationState(getActiveInvestigationComposerElement()));
}

function patchTicketDetailWhilePreservingComposer(viewState) {
  if (!workspaceRegionEl || typeof workspaceRegionEl.querySelector !== "function") {
    return false;
  }
  const workspace = workspaceRegionEl.querySelector(".ticket-workspace");
  if (!workspace || typeof workspace.querySelector !== "function") {
    return false;
  }
  const workspaceTicketId = normalizeDetailTicketId(workspace.dataset?.detailTicketId || "");
  if (workspaceTicketId && workspaceTicketId !== normalizeDetailTicketId(viewState.ticketId)) {
    return false;
  }
  const headerRegion = workspace.querySelector('[data-detail-section="header"]');
  const staticRegion = workspace.querySelector('[data-detail-section="investigation-static"]');
  const threadBodyRegion = workspace.querySelector('[data-detail-section="investigation-thread-body"]');
  const insightRegion = workspace.querySelector('[data-detail-section="insight"]');
  if (!headerRegion || !staticRegion || !threadBodyRegion || !insightRegion) {
    return false;
  }
  const composer = getActiveInvestigationComposerElement();
  const snapshot = captureComposerPreservationState(composer);
  headerRegion.innerHTML = renderTicketDetailHeaderHtml(viewState);
  staticRegion.innerHTML = renderTicketDetailConversationStaticHtml(viewState);
  threadBodyRegion.innerHTML = renderTicketDetailConversationBodyHtml(viewState);
  insightRegion.innerHTML = renderTicketDetailInsightPanelHtml(viewState);
  restoreComposerPreservationState(composer, snapshot);
  return true;
}

function renderTicketDetailView() {
  if (!selectedTicketId) {
    return '<div class="empty-state">Select a ticket to open the active workspace.</div>';
  }

  if (detailLoading) {
    return renderWorkspacePreparingLoadingHtml();
  }

  if (!selectedTicket) {
    return '<div class="empty-state">Ticket detail is unavailable.</div>';
  }

  return renderTicketDetailViewFromState(buildTicketDetailViewState());
}

function focusInvestigationComposerInput(retries = 8) {
  const input = document.getElementById("detail-investigation-input");
  if (!isTextComposerElement(input) && !isRichTextComposerElement(input)) {
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
  if (isRichTextComposerElement(input)) {
    placeSharedComposerCaretAtEnd(input);
    syncInvestigationComposerToolbarStateFromElement(input);
    return;
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
    stopWorkspaceSlaCountdown();
    detailScheduledScrollPlans = null;
    detailScheduledScrollJobId += 1;
    return;
  }
  const previousPaneSnapshot = captureDetailPaneScrollSnapshot();
  renderWorkspaceChrome();
  if (!selectedTicketId || detailLoading || !selectedTicket) {
    workspaceRegionEl.innerHTML = renderTicketDetailView();
    startWorkspaceSlaCountdown();
    return;
  }
  const viewState = buildTicketDetailViewState();
  if (shouldPreserveInvestigationComposerOnRender(viewState) && patchTicketDetailWhilePreservingComposer(viewState)) {
    syncDetailPaneScrollPosition(previousPaneSnapshot, viewState);
    startWorkspaceSlaCountdown();
    return;
  }
  workspaceRegionEl.innerHTML = renderTicketDetailViewFromState(viewState);
  syncDetailPaneScrollPosition(previousPaneSnapshot, viewState);
  startWorkspaceSlaCountdown();
}

function redirectOpenTicketToPool() {
  setSelectedPoolStatus("investigating", { render: false });
  resetDetailWorkspaceState();
  routeState.view = "pool";
  routeState.ticketId = null;
  window.location.hash = "";
  showWorkspaceFeedback(
    "Client Workspace Only",
    "This ticket is only visible in the client workspace."
  );
}

async function refreshSelectedTicket(options = {}) {
  const { silent = false, showLoading = false } = options;
  if (!selectedTicketId) {
    selectedTicket = null;
    detailLoading = false;
    resetDetailRefreshState();
    renderTicketDetail();
    return;
  }

  const requestedTicketId = selectedTicketId;
  ensureDetailRefreshStateTicket(requestedTicketId);
  abortInFlightDetailRefresh();
  const controller = createAbortController();
  detailRefreshState.inFlightController = controller;
  detailRefreshState.requestSeq += 1;
  const requestSeq = detailRefreshState.requestSeq;
  const requestMutationEpoch = detailRefreshState.mutationEpoch;
  if (showLoading) {
    detailLoading = true;
    renderTicketDetail();
  }

  try {
    const payload = await fetchJson(`/api/engineer/tickets/${encodeURIComponent(requestedTicketId)}?include_context=false`, {
      signal: controller.signal,
    });
    if (selectedTicketId !== requestedTicketId) {
      return;
    }
    if (
      detailRefreshState.inFlightController !== controller ||
      detailRefreshState.requestSeq !== requestSeq ||
      detailRefreshState.mutationEpoch !== requestMutationEpoch
    ) {
      return;
    }
    const nextTicket = payload.ticket || null;
    if (nextTicket && !isEngineerVisibleStatus(nextTicket.status || "open")) {
      detailRefreshState.inFlightController = null;
      redirectOpenTicketToPool();
      return;
    }
    if (shouldDiscardStaleDetailPayload(selectedTicket, nextTicket)) {
      detailRefreshState.inFlightController = null;
      detailLoading = false;
      renderTicketDetail();
      return;
    }
    selectedTicket = nextTicket;
    reconcileDurableInvestigationState(requestedTicketId, selectedTicket);
    const refreshedInvestigation = getActiveInvestigation(selectedTicket);
    const refreshedMessages = Array.isArray(refreshedInvestigation?.messages) ? refreshedInvestigation.messages : [];
    if (!getInvestigationApprovalUiState(selectedTicket, refreshedInvestigation, refreshedMessages).showApprovalBlock) {
      investigationReviseMode = false;
    }
    if (!selectedTicket) {
      investigationReviseMode = false;
    }
    detailRefreshState.inFlightController = null;
    detailLoading = false;
    renderTicketDetail();
    refreshSelectedTicketHitlFeedback(requestedTicketId).catch(() => {});
  } catch (error) {
    if (selectedTicketId !== requestedTicketId) {
      return;
    }
    const superseded =
      controller.signal.aborted ||
      detailRefreshState.inFlightController !== controller ||
      detailRefreshState.requestSeq !== requestSeq ||
      detailRefreshState.mutationEpoch !== requestMutationEpoch;
    if (superseded) {
      return;
    }
    detailRefreshState.inFlightController = null;
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

async function refreshSelectedTicketHitlFeedback(ticketId) {
  const normalizedTicketId = normalizeDetailTicketId(ticketId);
  if (!normalizedTicketId) {
    return;
  }
  const requestSeq = ++hitlFeedbackRequestSeq;
  hitlFeedbackLoading = true;
  renderTicketDetail();
  try {
    const payload = await fetchJson(`/api/engineer/tickets/${encodeURIComponent(normalizedTicketId)}/feedback`);
    if (requestSeq !== hitlFeedbackRequestSeq || normalizeDetailTicketId(selectedTicketId) !== normalizedTicketId) {
      return;
    }
    if (selectedTicket && normalizeDetailTicketId(selectedTicket.ticket_id || selectedTicketId) === normalizedTicketId) {
      selectedTicket = {
        ...selectedTicket,
        engineer_hitl_feedback: Array.isArray(payload.feedback) ? payload.feedback : [],
      };
    }
  } catch {
    // Feedback history should not block opening or refreshing the engineer workspace.
  } finally {
    if (requestSeq === hitlFeedbackRequestSeq && normalizeDetailTicketId(selectedTicketId) === normalizedTicketId) {
      hitlFeedbackLoading = false;
      renderTicketDetail();
    }
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

function normalizeTicketLoadOptions(options = {}) {
  const normalizedOptions = options && typeof options === "object" ? options : {};
  return {
    refreshDetail: normalizedOptions.refreshDetail !== false,
    showLoading: Object.prototype.hasOwnProperty.call(normalizedOptions, "showLoading")
      ? Boolean(normalizedOptions.showLoading)
      : tickets.length === 0,
  };
}

function mergeQueuedTicketLoadOptions(options) {
  if (!ticketLoadState.queuedOptions) {
    ticketLoadState.queuedOptions = {
      refreshDetail: Boolean(options.refreshDetail),
      showLoading: false,
    };
    return;
  }
  ticketLoadState.queuedOptions.refreshDetail =
    ticketLoadState.queuedOptions.refreshDetail || Boolean(options.refreshDetail);
}

async function performTicketLoad(options = {}) {
  const { refreshDetail, showLoading } = normalizeTicketLoadOptions(options);
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

async function loadTickets(options = {}) {
  const normalizedOptions = normalizeTicketLoadOptions(options);
  if (ticketLoadState.inFlightPromise) {
    mergeQueuedTicketLoadOptions(normalizedOptions);
    return ticketLoadState.inFlightPromise;
  }

  const activePromise = (async () => {
    let nextOptions = normalizedOptions;
    let pendingError = null;
    while (nextOptions) {
      try {
        await performTicketLoad(nextOptions);
        pendingError = null;
      } catch (error) {
        pendingError = error;
      }
      if (ticketLoadState.queuedOptions) {
        nextOptions = ticketLoadState.queuedOptions;
        ticketLoadState.queuedOptions = null;
        continue;
      }
      if (pendingError) {
        throw pendingError;
      }
      nextOptions = null;
    }
  })();

  ticketLoadState.inFlightPromise = activePromise;
  try {
    return await activePromise;
  } finally {
    if (ticketLoadState.inFlightPromise === activePromise) {
      ticketLoadState.inFlightPromise = null;
      ticketLoadState.queuedOptions = null;
    }
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
    body: JSON.stringify({ action, engineer_id: currentEngineerId() }),
  });
}

async function runMultiAgentInvestigation(ticketId) {
  return await fetchJson(`/api/engineer/tickets/${encodeURIComponent(ticketId)}/multi-agent/run`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ engineer_id: currentEngineerId() }),
    timeoutMs: INVESTIGATION_AI_TURN_FETCH_TIMEOUT_MS,
  });
}

async function submitInvestigationMessage(ticketId, messageText) {
  const cleaned = String(messageText || "").trim();
  if (!cleaned) {
    window.alert(`Please enter the next technical detail for ${ENGINEER_AI_DISPLAY_NAME}.`);
    return;
  }
  // multi_agent_enabled is true only when the multi-agent workspace is toggled
  // on for this ticket. The default guardrail-only workspace sends false so the
  // backend keeps the existing Plan/Execute/Review state untouched.
  const multiAgentEnabled = isMultiAgentWorkspaceActiveForTicket(ticketId);
  return await fetchJson(`/api/engineer/tickets/${encodeURIComponent(ticketId)}/investigation/messages`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      message: cleaned,
      engineer_id: currentEngineerId(),
      multi_agent_enabled: multiAgentEnabled,
    }),
    timeoutMs: INVESTIGATION_AI_TURN_FETCH_TIMEOUT_MS,
  });
}

async function submitInvestigationConfirmation(ticketId, decision, note = "", options = {}) {
  const timeoutMsCandidate = Number(options.timeoutMs);
  const timeoutMs =
    Number.isFinite(timeoutMsCandidate) && timeoutMsCandidate > 0
      ? timeoutMsCandidate
      : INVESTIGATION_APPROVE_FETCH_TIMEOUT_MS;
  return await fetchJson(`/api/engineer/tickets/${encodeURIComponent(ticketId)}/investigation/confirmation`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      decision: String(decision || "approve").toLowerCase(),
      note: String(note || "").trim() || undefined,
      engineer_id: currentEngineerId(),
    }),
    timeoutMs,
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

  if (action === "back-to-workspace-home") {
    closeTicketDetail();
    return;
  }

  if (action === "toggle-break-after-case") {
    toggleWorkspaceBreakAfterCase();
    return;
  }

  if (action === "toggle-multi-agent-workspace") {
    if (normalizeStatusValue(selectedTicket?.status || "open") !== "investigating") {
      return;
    }
    const requestTicketId = normalizeDetailTicketId(button.dataset.multiAgentToggle || selectedTicketId);
    if (!requestTicketId) {
      return;
    }
    setMultiAgentWorkspaceActiveForTicket(requestTicketId, true);
    multiAgentRunLoadingTicketId = requestTicketId;
    multiAgentRunError = "";
    button.disabled = true;
    renderTicketDetail();
    try {
      const payload = await runMultiAgentInvestigation(requestTicketId);
      const nextEngineerAgentState =
        payload?.engineer_agent_state && typeof payload.engineer_agent_state === "object"
          ? payload.engineer_agent_state
          : selectedTicket?.engineer_agent_state;
      if (selectedTicket && normalizeDetailTicketId(selectedTicket.ticket_id || selectedTicketId) === requestTicketId) {
        selectedTicket = {
          ...selectedTicket,
          status: payload?.status ?? selectedTicket.status,
          updated_at: payload?.updated_at ?? selectedTicket.updated_at,
          engineer_agent_state: nextEngineerAgentState,
        };
      }
      const ticketIndex = tickets.findIndex(
        (ticket) => normalizeDetailTicketId(ticket?.ticket_id) === requestTicketId
      );
      if (ticketIndex >= 0) {
        tickets[ticketIndex] = {
          ...tickets[ticketIndex],
          status: payload?.status ?? tickets[ticketIndex].status,
          updated_at: payload?.updated_at ?? tickets[ticketIndex].updated_at,
          engineer_agent_state: nextEngineerAgentState,
        };
      }
    } catch (error) {
      multiAgentRunError = error.message || "Unknown error";
    } finally {
      if (normalizeDetailTicketId(multiAgentRunLoadingTicketId) === requestTicketId) {
        multiAgentRunLoadingTicketId = null;
      }
      button.disabled = false;
      renderTicketDetail();
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
      setInvestigationComposerDraftFromMarkdown("");
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
      window.alert(`Start an investigation before sending a note to ${ENGINEER_AI_DISPLAY_NAME}.`);
      return;
    }
    const cleaned = tellAiDraft.trim();
    if (!cleaned) {
      window.alert(`Please enter the next message for ${ENGINEER_AI_DISPLAY_NAME}.`);
      return;
    }
    const requestTicketId = selectedTicketId;
    const activeInvestigation = getActiveInvestigation(selectedTicket);
    const approvalUiState = getInvestigationApprovalUiState(
      selectedTicket,
      activeInvestigation,
      Array.isArray(activeInvestigation?.messages) ? activeInvestigation.messages : []
    );
    const guardrailDecision = String(approvalUiState.activeGuardrailFinal?.decision || "").trim();
    const isRevisionFlow = Boolean(
      investigationReviseMode ||
        approvalUiState.showApprovalBlock ||
        approvalUiState.awaitingFinalApproval ||
        guardrailDecision === "blocked"
    );
    button.disabled = true;
    startLocalInvestigationOptimisticSend(
      requestTicketId,
      cleaned,
      isRevisionFlow ? "investigation_revise" : "investigation_message"
    );
    requestDetailPaneScrollToBottom(requestTicketId, DETAIL_THREAD_PANE, { behavior: "smooth" });
    setInvestigationComposerDraftFromMarkdown("");
    tellAiSubmitting = true;
    renderTicketDetail();
    try {
      let responsePayload = null;
      if (activeInvestigation) {
        if (isRevisionFlow) {
          responsePayload = await submitInvestigationConfirmation(requestTicketId, "revise", cleaned, {
            timeoutMs: INVESTIGATION_AI_TURN_FETCH_TIMEOUT_MS,
          });
          investigationReviseMode = false;
        } else {
          responsePayload = await submitInvestigationMessage(requestTicketId, cleaned);
        }
      }
      applySuccessfulInvestigationSendResponse(requestTicketId, responsePayload);
      try {
        await loadTickets({ refreshDetail: false });
        await refreshSelectedTicket({ silent: true });
      } catch {
        // Keep the immediate optimistic replacement even if the background refresh fails.
      }
    } catch (error) {
      let recovered = false;
      if (isInvestigationTimeoutErrorMessage(error?.message)) {
        recovered = await recoverTimedOutInvestigationSend(requestTicketId);
      }
      if (!recovered) {
        failLocalInvestigationOptimisticSend(requestTicketId, error.message);
        setInvestigationComposerDraftFromMarkdown(cleaned);
      }
    } finally {
      tellAiSubmitting = false;
      renderTicketDetail();
      button.disabled = false;
    }
    return;
  }

  if (action === "approve-investigation") {
    const activeInvestigation = getActiveInvestigation(selectedTicket);
    if (!activeInvestigation) {
      return;
    }
    const approvalUiState = getInvestigationApprovalUiState(
      selectedTicket,
      activeInvestigation,
      Array.isArray(activeInvestigation?.messages) ? activeInvestigation.messages : []
    );
    if (!approvalUiState.showApprovalBlock) {
      return;
    }
    button.disabled = true;
    tellAiSubmitting = true;
    startLocalInvestigationPendingApproval(selectedTicketId);
    renderTicketDetail();
    try {
      const responsePayload = await submitInvestigationConfirmation(selectedTicketId, "approve");
      investigationReviseMode = false;
      setInvestigationComposerDraftFromMarkdown("");
      applyInvestigationResponseToSelectedTicket(selectedTicketId, responsePayload);
      // Keep in investigating status; guardrail final review will be shown after refresh
      renderTicketDetail();
      await loadTickets({ refreshDetail: false });
      await refreshSelectedTicket({ silent: true });
    } catch (error) {
      clearLocalInvestigationPendingApproval(selectedTicketId);
      window.alert(`Approve for guardrail failed: ${error.message}`);
      await refreshSelectedTicket({ silent: true });
    } finally {
      tellAiSubmitting = false;
      clearLocalInvestigationPendingApproval(selectedTicketId);
      renderTicketDetail();
      button.disabled = false;
    }
    return;
  }

  if (action === "final-approve-investigation") {
    const activeInvestigation = getActiveInvestigation(selectedTicket);
    if (!activeInvestigation) {
      return;
    }
    const approvalUiState = getInvestigationApprovalUiState(
      selectedTicket,
      activeInvestigation,
      Array.isArray(activeInvestigation?.messages) ? activeInvestigation.messages : []
    );
    if (!approvalUiState.awaitingFinalApproval || !approvalUiState.activeGuardrailFinal) {
      return;
    }
    if (approvalUiState.activeGuardrailFinal.decision !== "approved_for_final_engineer_review") {
      window.alert("Guardrail final review did not approve the customer reply.");
      return;
    }
    button.disabled = true;
    tellAiSubmitting = true;
    startLocalInvestigationPendingApproval(selectedTicketId);
    renderTicketDetail();
    try {
      const responsePayload = await submitInvestigationConfirmation(selectedTicketId, "final_approve");
      investigationReviseMode = false;
      setInvestigationComposerDraftFromMarkdown("");
      applyInvestigationResponseToSelectedTicket(selectedTicketId, responsePayload);
      setSelectedPoolStatus("resolved", { render: false });
      renderTicketDetail();
      await loadTickets({ refreshDetail: false });
      await refreshSelectedTicket({ silent: true });
    } catch (error) {
      clearLocalInvestigationPendingApproval(selectedTicketId);
      window.alert(`Final approve failed: ${error.message}`);
      await refreshSelectedTicket({ silent: true });
    } finally {
      tellAiSubmitting = false;
      clearLocalInvestigationPendingApproval(selectedTicketId);
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
    setInvestigationComposerDraftFromMarkdown("");
    renderTicketDetail();
    focusInvestigationComposerInput();
    return;
  }
}

function handleDetailInput(event) {
  const tellAiInput = event.target.closest("#detail-investigation-input");
  if (tellAiInput) {
    if (isRichTextComposerElement(tellAiInput)) {
      syncInvestigationComposerDraftStateFromElement(tellAiInput);
      return;
    }
    tellAiDraft = String(tellAiInput.value || "");
  }
}

function handleDetailFocusIn(event) {
  const tellAiInput = event.target.closest("#detail-investigation-input");
  if (tellAiInput && isRichTextComposerElement(tellAiInput)) {
    syncInvestigationComposerToolbarStateFromElement(tellAiInput);
  }
}

function handleDetailFocusOut() {}

function handleDetailPaste(event) {
  const tellAiInput = event.target.closest?.("#detail-investigation-input");
  if (!tellAiInput || !isRichTextComposerElement(tellAiInput)) {
    return;
  }
  const text = event.clipboardData?.getData("text/plain");
  if (typeof text !== "string") {
    return;
  }
  event.preventDefault();
  getEngineerComposerRuntime()?.insertPlainText(tellAiInput, text, {
    preserveNewlines: true,
  });
}

function handleDetailSelectionChange(event) {
  const tellAiInput = event.target.closest?.("#detail-investigation-input");
  if (tellAiInput && isRichTextComposerElement(tellAiInput)) {
    syncInvestigationComposerToolbarStateFromElement(tellAiInput);
  }
}

function handleDetailKeydown(event) {
  const tellAiInput = event.target.closest("#detail-investigation-input");
  if (!tellAiInput) {
    return;
  }
  if (isRichTextComposerElement(tellAiInput)) {
    const runtime = getEngineerComposerRuntime();
    if (runtime?.handleListDeletion(event, tellAiInput)) {
      return;
    }
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      workspaceRegionEl
        ?.querySelector('button[data-detail-action="send-tell-ai"]')
        ?.click();
      return;
    }
    if (event.key === "Enter" && event.shiftKey) {
      event.preventDefault();
      runtime?.handleShiftEnter(tellAiInput);
    }
    return;
  }
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    workspaceRegionEl?.querySelector('button[data-detail-action="send-tell-ai"]')?.click();
  }
}

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

function shouldRefreshSelectedTicketForRealtimePayload(payload) {
  if (!payload || typeof payload !== "object") {
    return false;
  }
  const selectedIds = new Set(
    [
      selectedTicketId,
      routeState.ticketId,
      selectedTicket?.ticket_id,
      selectedTicket?.client_ticket_id,
      selectedTicket?.client_ticket_ref?.ticket_id,
    ]
      .map((value) => String(value || "").trim())
      .filter(Boolean)
  );
  if (!selectedIds.size) {
    return false;
  }
  const payloadIds = [
    payload.ticket_id,
    payload.client_ticket_id,
    payload.engineer_case_id,
  ]
    .map((value) => String(value || "").trim())
    .filter(Boolean);
  return payloadIds.some((value) => selectedIds.has(value));
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

  socket.onmessage = async (event) => {
    let payload = null;
    try {
      payload = JSON.parse(String(event?.data || ""));
    } catch {
      payload = null;
    }
    const refreshSelectedDetail = shouldRefreshSelectedTicketForRealtimePayload(payload);
    try {
      await loadTickets({ refreshDetail: false });
      if (refreshSelectedDetail) {
        await refreshSelectedTicket({ silent: true });
      }
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

async function enterBoard(options = {}) {
  const { continuousLoading = false } = options;
  parseRoute();
  if (routeState.view !== "detail" || !routeState.ticketId) {
    renderReadinessInsteadOfPool();
    return;
  }
  toggleScreens();
  boardLoading = routeState.view === "pool" && tickets.length === 0;
  if (!continuousLoading) {
    renderWorkspace();
  }
  await detectStorageMode();
  setRealtimeStatus("Realtime: connecting...");
  try {
    await loadTickets({ refreshDetail: false });
    await syncRouteToWorkspace({ silent: true, showLoading: true, continuousLoading });
  } catch (error) {
    showBoardError(`Failed to load tickets: ${error.message}`);
  }
  setupWebSocket();
}

function resetLoginForm() {
  loginFormEl?.reset?.();
  if (loginErrorEl) {
    loginErrorEl.textContent = "";
  }
}

async function handleLoginSubmit(event) {
  event?.preventDefault?.();
  enterWelcome();
}

function resetWorkspaceBoardState() {
  storageMode = "unknown";
  boardLoading = false;
  closeSocket();
  tickets = [];
  selectedPoolStatus = "investigating";
  closeAllHeaderFilterComboboxes({ render: false });
  resetDetailWorkspaceState();
  window.location.hash = "";
  parseRoute();
  renderFilterControls();
  renderWorkspace();
}

function enterWelcome() {
  const engineer = getCandidateEngineer();
  writeStorage(WORKSPACE_AUTH_KEY, engineer.id);
  selectedEngineerId = engineer.id;
  selectedEngineerCandidate = engineer.id;
  saveWorkspaceActive(false);
  resetWorkspaceBoardState();
  renderReadinessInsteadOfPool();
  setRealtimeStatus("Realtime: signed out");
}

function signOut() {
  saveWorkspaceActive(false);
  removeStorage(WORKSPACE_AUTH_KEY);
  selectedEngineerId = "";
  selectedEngineerCandidate = DEMO_ENGINEERS[0].id;
  resetWorkspaceBoardState();
  toggleScreens();
  renderLogin();
  setRealtimeStatus("Realtime: signed out");
}

function handleWorkspaceEntryClick(event) {
  const target = event?.target;
  const engineerButton = target?.closest?.("[data-engineer-id]");
  if (engineerButton) {
    selectedEngineerCandidate = normalizeEngineerId(engineerButton.dataset.engineerId) || DEMO_ENGINEERS[0].id;
    renderLogin();
    return;
  }

  const actionButton = target?.closest?.("[data-action]");
  if (!actionButton) {
    return;
  }
  const action = String(actionButton.dataset.action || "");
  if (action === "enter-welcome") {
    enterWelcome();
  }
  if (action === "ready-to-roll") {
    readyToRoll().catch((error) => {
      renderNoInvestigatingCase(`No investigating cases available. Queue check failed: ${error.message}`);
    });
  }
  if (action === "sign-out") {
    signOut();
  }
  if (action === "back-to-welcome") {
    renderReadinessInsteadOfPool();
  }
}

function handleWorkspaceShiftSubmit(event) {
  event?.preventDefault?.();
  const formData = new FormData(event?.target);
  const nextShift = normalizeShift({
    start: formData.get("start"),
    end: formData.get("end"),
  });
  workspaceShift = nextShift;
  writeStorage(WORKSPACE_SHIFT_KEY, nextShift);
  saveWorkspaceActive(false);
  renderWorkspaceAssignmentSidebar();
  if (workspaceRegionEl) {
    workspaceRegionEl.innerHTML = renderWelcomeViewHtml();
  }
}

hydrateTicketPoolViewMode();

function handleChangeEngineerClick() {
  if (changeEngineerLoading) {
    return;
  }
  changeEngineerLoading = true;
  renderHeaderUserControls();
  try {
    signOut();
  } finally {
    changeEngineerLoading = false;
    renderHeaderUserControls();
  }
}

loginFormEl?.addEventListener("submit", (event) => {
  handleLoginSubmit(event).catch((error) => {
    if (loginErrorEl) {
      loginErrorEl.textContent = `Login failed: ${error.message}`;
    }
  });
});

workspaceRootEl?.addEventListener("click", handleWorkspaceEntryClick);

filterControlsEl?.addEventListener("click", handleFilterControlsClick);
filterControlsEl?.addEventListener("input", handleFilterControlsInput);
filterControlsEl?.addEventListener("focusin", handleFilterControlsFocusIn);
filterControlsEl?.addEventListener("focusout", handleFilterControlsFocusOut);
filterControlsEl?.addEventListener("keydown", handleFilterControlsKeydown);
workspaceRegionEl?.addEventListener("click", (event) => {
  if (event.target.closest('[data-action="ready-to-roll"], [data-action="sign-out"], [data-action="back-to-welcome"]')) {
    handleWorkspaceEntryClick(event);
    return;
  }

  const assetDownloadButton = event.target.closest("[data-asset-download-id]");
  if (assetDownloadButton) {
    downloadAsset(assetDownloadButton.getAttribute("data-asset-download-id")).catch((error) => {
      window.alert(`Attachment download failed: ${error.message}`);
    });
    return;
  }

  const composerToolbarButton = event.target.closest("[data-composer-markdown-action]");
  if (composerToolbarButton) {
    getEngineerComposerRuntime()?.handleToolbarAction(
      composerToolbarButton.getAttribute("data-composer-markdown-action"),
      getActiveInvestigationComposerElement()
    );
    return;
  }

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
workspaceRegionEl?.addEventListener("submit", (event) => {
  if (event.target?.matches?.("[data-workspace-shift-form]")) {
    handleWorkspaceShiftSubmit(event);
  }
});
workspaceRegionEl?.addEventListener("mousedown", (event) => {
  if (event.target.closest("[data-composer-markdown-action]")) {
    event.preventDefault();
  }
});
workspaceRegionEl?.addEventListener("input", handleDetailInput);
workspaceRegionEl?.addEventListener("focusin", handleDetailFocusIn);
workspaceRegionEl?.addEventListener("focusout", handleDetailFocusOut);
workspaceRegionEl?.addEventListener("paste", handleDetailPaste);
workspaceRegionEl?.addEventListener("mouseup", handleDetailSelectionChange);
workspaceRegionEl?.addEventListener("keyup", handleDetailSelectionChange);
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
refreshWorkspaceSessionState();

if (isAuthenticated()) {
  enterBoard().catch((error) => {
    renderNoInvestigatingCase(`No investigating cases available. Workspace initialization failed: ${error.message}`);
  });
} else {
  parseRoute();
  if (getSelectedEngineer() && routeState.view === "pool") {
    renderReadinessInsteadOfPool();
  } else if (getSelectedEngineer()) {
    toggleScreens();
    renderWelcome();
  } else {
    toggleScreens();
    renderLogin();
  }
  setRealtimeStatus("Realtime: signed out");
}
