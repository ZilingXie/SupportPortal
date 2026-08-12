const appRoot = document.getElementById("app");
const toastRoot = document.getElementById("toast-root");
const SharedComposer = globalThis.SupportPortalComposer || {};

const renderMarkdownMessage =
  SharedComposer.renderMarkdownMessage ||
  ((value) =>
    String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll("\n", "<br>"));
const serializeRichComposerHtmlToMarkdown =
  SharedComposer.serializeRichComposerHtmlToMarkdown || ((value) => String(value || ""));
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
const applySharedComposerToolbarStateToButtons =
  SharedComposer.applyComposerToolbarStateToButtons || (() => {});

const PAGE_SIZE = 10;
const SUMMARY_FRESH_MS = 30_000;
const DETAIL_FRESH_MS = 60_000;
const CACHE_HARD_EXPIRY_MS = 5 * 60_000;
const SUMMARY_CACHE_LIMIT = 20;
const DETAIL_CACHE_LIMIT = 20;

const DEFAULT_FILTER_DEFINITIONS = [
  { id: "all", label: "All", children: [] },
  {
    id: "automation",
    label: "Automated",
    children: [
      { id: "fraud_account", label: "Account & Billing / Fraud Account" },
      { id: "detailed_invoice", label: "Account & Billing / Detailed Invoice" },
      { id: "enablement", label: "Backend Operation / Enablement" },
      { id: "quota", label: "Backend Operation / Quota" },
    ],
  },
  {
    id: "backend_operation",
    label: "Backend Operation",
    children: [
      { id: "enablement", label: "Enablement" },
      { id: "quota", label: "Quota" },
      { id: "unregistered", label: "Unregistered" },
    ],
  },
  {
    id: "account_billing",
    label: "Account & Billing",
    children: [
      { id: "account_suspension", label: "Account Suspension" },
      { id: "fraud_account", label: "Fraud Account" },
      { id: "detailed_invoice", label: "Detailed Invoice" },
      { id: "other", label: "Other" },
    ],
  },
  { id: "agora_technical", label: "Tech", children: [] },
  { id: "security_compliance", label: "Security & Compliance", children: [] },
  {
    id: "conversation",
    label: "Conversation",
    children: [
      { id: "resolve", label: "Resolve" },
      { id: "follow_up", label: "Follow-up" },
      { id: "human_review", label: "Human Review" },
    ],
  },
  {
    id: "human_review",
    label: "Human Review",
    children: [
      { id: "uncategorized", label: "Uncategorized" },
      { id: "uncertain", label: "Uncertain" },
      { id: "non_agora", label: "Non-Agora" },
      { id: "other", label: "Other" },
    ],
  },
];

const state = {
  view: "create",
  title: "",
  question: "",
  customerEmail: "",
  source: "manual",
  isSubmitting: false,
  history: [],
  isLoadingHistory: true,
  detailLoading: false,
  currentPage: 1,
  pagination: {
    page: 1,
    pageSize: PAGE_SIZE,
    total: 0,
    totalPages: 1,
    hasMore: false,
  },
  activeItem: null,
  error: "",
  composerToolbarState: buildDefaultComposerToolbarState(),
  statusFilter: "all",
  filterDefinitions: DEFAULT_FILTER_DEFINITIONS,
  filterCounts: {},
  filterCountsVersion: 0,
  caseSearchQuery: "",
  caseSearchError: "",
  isSearchingCase: false,
  replyMessage: "",
  isSubmittingReply: false,
  replyError: "",
  correctionScope: "",
  correctionAction: "",
  isSubmittingCorrection: false,
  correctionError: "",
  routeErrorSummary: null,
  routeCorrectionExpanded: false,
  isSubmittingReview: false,
  reviewError: "",
  rerouteJob: null,
  rerouteConfirmationOpen: false,
  rerouteTargetSnapshot: null,
  rerouteActiveTargetCaseId: "",
  isStartingReroute: false,
  rerouteError: "",
};

let composerRuntime = null;
let isFetchingRouteErrorSummary = false;
let replyPollTimer = null;
let reroutePollTimer = null;
let summaryRequestController = null;
let caseSearchRequestController = null;
let caseSearchGeneration = 0;
let summaryRequestGeneration = 0;
let detailOpenGeneration = 0;
const summaryCache = new Map();
const detailCache = new Map();
const detailInflight = new Map();
const detailRequestControllers = new Set();

const ACTIVE_AI_REPLY_STATUSES = new Set([
  "queued", "preparing", "scheduled", "publishing",
  "persona_queued", "persona_preparing", "persona_scheduled", "persona_publishing",
]);
const ROUTE_TUPLE_OPTIONS = [
  { scope: "ticket_resolution", action: "resolve_ticket", label: "Conversation / Resolve" },
  { scope: "conversation", action: "follow_up", label: "Conversation / Follow-up" },
  { scope: "conversation", action: "human_review_required", label: "Conversation / Human Review" },
  { scope: "agora_technical", action: "rag", label: "Agora / Agora Technical" },
  { scope: "security_compliance", action: "human_review_required", category: "security_compliance", label: "Agora / Security & Compliance" },
  { scope: "account_billing", action: "account_suspension", category: "account_billing", subcategory: "account_suspension", label: "Agora / Account & Billing / Account Suspension" },
  { scope: "account_billing", action: "fraud_account", category: "account_billing", subcategory: "fraud_account", label: "Agora / Account & Billing / Fraud Account" },
  { scope: "account_billing", action: "detailed_invoice", category: "account_billing", subcategory: "detailed_invoice", label: "Agora / Account & Billing / Detailed Invoice" },
  { scope: "account_billing", action: "human_review_required", category: "account_billing", subcategory: "other", label: "Agora / Account & Billing / Other" },
  { scope: "backend_operation", action: "enablement", category: "backend_operation", subcategory: "enablement", label: "Agora / Backend Operation / Enablement" },
  { scope: "backend_operation", action: "quota", category: "backend_operation", subcategory: "quota", label: "Agora / Backend Operation / Quota" },
  { scope: "backend_operation", action: "unregistered", category: "backend_operation", subcategory: "unregistered", label: "Agora / Backend Operation / Unregistered" },
  { scope: "uncategorized", action: "human_review_required", category: "human_review", subcategory: "uncategorized", label: "Human Review / Uncategorized" },
  { scope: "uncertain", action: "human_review_required", category: "human_review", subcategory: "uncertain", label: "Human Review / Uncertain" },
  { scope: "non_agora", action: "human_review_required", category: "human_review", subcategory: "non_agora", label: "Human Review / Non-Agora" },
  { scope: "human_review", action: "human_review_required", category: "human_review", subcategory: "other", label: "Human Review / Other" },
];

const DEFAULT_ROUTE_TUPLE_SELECT_VALUE = "scope|action";

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function statusLabel(status) {
  const labels = {
    automation: "Automation",
    automated: "Automated",
    classified_only: "Classification only",
    needs_more_info: "Needs more info",
    not_automated: "Not automated",
  };
  return labels[status] || "Not automated";
}

function routeClass(route) {
  if (route === "detailed_invoice") return "route-invoice";
  if (route === "account_suspension") return "route-suspension";
  if (route === "fraud_account") return "route-suspension";
  return "route-other";
}

function classificationLabels(item) {
  const primary = String(item?.primary_label || "").trim();
  const secondary = String(item?.secondary_label || "").trim();
  return { primary, secondary };
}

function renderClassificationBadges(item) {
  const { primary, secondary } = classificationLabels(item);
  if (!primary && !secondary) return "";
  return `
    <span class="route-labels" aria-label="Route classification">
      ${primary ? `<span class="route-label route-label--primary">${escapeHtml(primary)}</span>` : ""}
      ${secondary ? `<span class="route-label route-label--secondary">${escapeHtml(secondary)}</span>` : ""}
    </span>
  `;
}

// Build the readable "Route result" string: scope_label / route_family / route.
// Falls back to just `route` for legacy tickets missing the new routing fields,
// and to "manual review" when nothing is present.
function routeResultLabel(item) {
  const { primary, secondary } = classificationLabels(item);
  if (primary || secondary) return [primary, secondary].filter(Boolean).join(" / ");
  const parts = [
    item.category || item.scope_label,
    item.subcategory,
    item.route_family,
    item.subcategory ? null : item.execution_action || item.route,
  ]
    .map((value) => String(value || "").trim())
    .filter((value) => value.length > 0);
  return parts.length ? parts.join(" / ") : "manual review";
}

function internalEmailResponseLinkStatus(item) {
  const payload = item && typeof item.internal_email_payload === "object" ? item.internal_email_payload : null;
  const body = String(payload?.body || "");
  return body.includes("/response?token=") ? "Generated" : "Not generated";
}

function safeSourceLink(source) {
  let link = "";
  if (source && typeof source === "object") {
    link = String(source.Link || source.link || source.url || "");
  } else if (typeof source === "string") {
    link = source;
  }
  link = link.trim();
  if (!link) return "";
  try {
    const parsed = new URL(link);
    if (parsed.protocol === "http:" || parsed.protocol === "https:") {
      return link;
    }
  } catch {}
  return "";
}

function normalizeSource(source) {
  if (safeSourceLink(source)) {
    return "api";
  }
  const normalized = String(source || "").trim().toLowerCase().replaceAll("_", "-");
  if (normalized === "api" || normalized === "http" || normalized === "account-http" || normalized === "/account-http") {
    return "api";
  }
  return "manual";
}

function sourceLabel(source) {
  if (normalizeSource(source) === "api") return "API";
  return "Manual";
}

function sourceClass(source) {
  if (normalizeSource(source) === "api") return "source-api";
  return "source-manual";
}

function zendeskTicketId(link) {
  try {
    const parsed = new URL(link);
    const host = parsed.hostname.toLowerCase();
    if (host === "zendesk.com" || host.endsWith(".zendesk.com")) {
      const m = parsed.pathname.match(/^\/(?:agent|api\/v2)\/tickets\/(\d+)(?:\.json)?$/);
      if (m) return m[1];
    }
  } catch {
    return "";
  }
  return "";
}

function zendeskTicketLabel(link) {
  const ticketId = zendeskTicketId(link);
  return ticketId ? "zen#" + ticketId : "";
}

function accountTicketNumber(item) {
  return zendeskTicketId(safeSourceLink(item?.source))
    || String(item?.ticket_id || item?.client_ticket_id || "").trim();
}

function normalizeCaseNumberQuery(value) {
  const normalized = String(value || "").trim().replace(/^#/, "");
  return /^\d+$/.test(normalized) ? normalized : "";
}

function renderSourceValue(source) {
  const link = safeSourceLink(source);
  if (link) {
    const label = zendeskTicketLabel(link) || "Link";
    return `<a class="source-link" href="${escapeHtml(link)}" target="_blank" rel="noopener noreferrer">${escapeHtml(label)}</a>`;
  }
  return `<span class="source-badge ${sourceClass(source)}">${escapeHtml(sourceLabel(source))}</span>`;
}

function showToast(message) {
  if (!toastRoot) return;
  toastRoot.innerHTML = `<div class="toast">${escapeHtml(message)}</div>`;
  window.setTimeout(() => {
    toastRoot.innerHTML = "";
  }, 3200);
}

function formatMessageTimestamp(value) {
  const date = new Date(String(value || ""));
  if (Number.isNaN(date.getTime())) return "Time unavailable";
  return new Intl.DateTimeFormat(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    timeZoneName: "short",
  }).format(date);
}

function renderAiReplyState(item) {
  const status = String(item?.ai_reply_status || "");
  if (!status || status === "published" || status === "cancelled") return "";
  const scheduled = formatMessageTimestamp(item.ai_reply_scheduled_for);
  if (ACTIVE_AI_REPLY_STATUSES.has(status)) {
    return `
      <div class="ai-reply-state" role="status" aria-live="polite">
        <span class="ai-reply-state__pulse" aria-hidden="true"></span>
        <span><strong>AI reply scheduled</strong><span>${escapeHtml(scheduled)}</span></span>
      </div>
    `;
  }
  const message = status === "manual_attention"
    ? "AI could not prepare a reliable reply. Manual attention is required."
    : item.ai_reply_error || "AI reply preparation failed.";
  return `<div class="ai-reply-state ai-reply-state--attention" role="status" aria-live="polite">${escapeHtml(message)}</div>`;
}

function updateReplyPolling() {
  if (replyPollTimer) {
    window.clearTimeout(replyPollTimer);
    replyPollTimer = null;
  }
  const item = state.activeItem;
  if (state.view !== "detail" || !item || !ACTIVE_AI_REPLY_STATUSES.has(String(item.ai_reply_status || ""))) return;
  replyPollTimer = window.setTimeout(async () => {
    if (document.hidden || state.view !== "detail" || !state.activeItem) {
      updateReplyPolling();
      return;
    }
    const ticketId = state.activeItem.ticket_id || state.activeItem.client_ticket_id || "";
    const detail = ticketId ? await fetchTicketDetail(ticketId, { force: true }) : null;
    if (detail) {
      state.activeItem = detail;
      render();
      return;
    }
    updateReplyPolling();
  }, 12000);
}

function accountCaseIdentifier(item) {
  return String(item?.account_case_id || item?.billing_ticket_id || item?.ticket_id || item?.client_ticket_id || "").trim();
}

function accountCaseAliases(item) {
  return new Set(
    [item?.account_case_id, item?.billing_ticket_id, item?.ticket_id, item?.client_ticket_id]
      .map((value) => String(value || "").trim())
      .filter(Boolean)
  );
}

function touchCacheEntry(cache, key, entry, limit) {
  cache.delete(key);
  cache.set(key, entry);
  while (cache.size > limit) {
    cache.delete(cache.keys().next().value);
  }
}

function currentSummaryKey() {
  return `${state.statusFilter}:${state.currentPage}`;
}

function selectedFilterParts(filterValue = state.statusFilter) {
  const [group, leaf] = String(filterValue || "all").split(":", 2);
  return { group: group || "all", leaf: leaf || "" };
}

function readFilterCount(counts, key) {
  const value = counts?.[key];
  if (value === null || value === undefined || value === "") return null;
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric : null;
}

function buildRouteFilterViewModel(definitions, counts, filterValue) {
  const safeDefinitions = Array.isArray(definitions) && definitions.length
    ? definitions
    : DEFAULT_FILTER_DEFINITIONS;
  const parsed = selectedFilterParts(filterValue);
  const fallbackGroup = safeDefinitions.find((group) => group.id === "all") || safeDefinitions[0];
  const group = safeDefinitions.find((item) => item.id === parsed.group) || fallbackGroup;
  const children = Array.isArray(group?.children) ? group.children : [];
  const leafKey = children.some((child) => child.id === parsed.leaf) ? parsed.leaf : "";
  const groupKey = group?.id || "all";
  const groupCount = readFilterCount(counts, groupKey);
  const options = [
    { value: "", label: `All ${group?.label || "Cases"}`, count: groupCount },
    ...children.map((child) => ({
      value: child.id,
      label: child.label,
      count: readFilterCount(counts, `${groupKey}:${child.id}`),
    })),
  ];
  return {
    groupKey,
    leafKey,
    group,
    groups: safeDefinitions.map((item) => ({
      ...item,
      count: readFilterCount(counts, item.id),
      active: item.id === groupKey,
    })),
    options,
    selectDisabled: children.length === 0,
  };
}

function buildTicketListUrl() {
  const params = new URLSearchParams({
    page: String(state.currentPage),
    page_size: String(PAGE_SIZE),
  });
  if (state.statusFilter === "unreviewed") {
    params.set("review_status", "pending");
  } else if (state.statusFilter === "reviewed") {
    params.set("review_status", "reviewed");
  } else if (state.statusFilter === "route_errors") {
    params.set("route_errors", "true");
  } else {
    const { group, leaf } = selectedFilterParts();
    if (group !== "all") {
      params.set("route_group", group);
      if (leaf) params.set("route_subcategory", leaf);
    }
  }
  return `/api/account/cases?${params.toString()}`;
}

function applyTicketPage(data, countsVersion = 0) {
  state.history = data.cases || data.tickets || data.billing_tickets || [];
  state.pagination = {
    page: Number(data.page || state.currentPage || 1),
    pageSize: Number(data.page_size || PAGE_SIZE),
    total: Number(data.total || 0),
    totalPages: Math.max(1, Number(data.total_pages || 1)),
    hasMore: Boolean(data.has_more),
  };
  state.currentPage = state.pagination.page;
  if (data.filter_counts && countsVersion >= (state.filterCountsVersion || 0)) {
    state.filterCounts = { ...data.filter_counts };
    state.filterCountsVersion = countsVersion;
  }
  if (Array.isArray(data.filter_definitions) && data.filter_definitions.length) {
    state.filterDefinitions = data.filter_definitions;
  }
}

function invalidateSummaryCache() {
  summaryCache.clear();
}

function findDetailCacheEntry(identifier, expectedRevision = "") {
  const normalized = String(identifier || "").trim();
  const now = Date.now();
  for (const [key, entry] of detailCache.entries()) {
    if (!entry.aliases.has(normalized)) continue;
    if (now - entry.cachedAt >= CACHE_HARD_EXPIRY_MS || (expectedRevision && entry.revision !== expectedRevision)) {
      detailCache.delete(key);
      return null;
    }
    touchCacheEntry(detailCache, key, entry, DETAIL_CACHE_LIMIT);
    return entry;
  }
  return null;
}

function cacheDetail(detail) {
  const key = accountCaseIdentifier(detail);
  if (!key) return;
  touchCacheEntry(
    detailCache,
    key,
    {
      data: detail,
      aliases: accountCaseAliases(detail),
      revision: String(detail.detail_revision || ""),
      cachedAt: Date.now(),
    },
    DETAIL_CACHE_LIMIT
  );
}

function invalidateDetailCache(identifier = "") {
  if (!identifier) {
    detailCache.clear();
    return;
  }
  const aliases = accountCaseAliases(
    state.activeItem && accountCaseAliases(state.activeItem).has(String(identifier))
      ? state.activeItem
      : { account_case_id: identifier }
  );
  aliases.add(String(identifier));
  for (const [key, entry] of detailCache.entries()) {
    if ([...aliases].some((alias) => entry.aliases.has(alias))) detailCache.delete(key);
  }
}

function prefetchTicketDetails(items) {
  const requested = items
    .slice(0, PAGE_SIZE)
    .filter((item) => {
      const identifier = accountCaseIdentifier(item);
      return identifier && !findDetailCacheEntry(identifier, String(item.detail_revision || "")) && !detailInflight.has(identifier);
    });
  const caseIds = requested.map(accountCaseIdentifier);
  if (!caseIds.length) return;

  const controller = new AbortController();
  detailRequestControllers.add(controller);
  const batchPromise = fetch("/api/account/cases/batch-details", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ case_ids: caseIds }),
    cache: "no-store",
    signal: controller.signal,
  })
    .then(async (response) => {
      if (!response.ok) throw new Error("Could not preload Account Case details.");
      const payload = await response.json();
      const details = Array.isArray(payload.details) ? payload.details : [];
      details.forEach(cacheDetail);
      return details;
    })
    .catch((error) => {
      if (error?.name !== "AbortError") console.warn("Account detail prefetch failed", error);
      return [];
    })
    .finally(() => {
      detailRequestControllers.delete(controller);
      caseIds.forEach((caseId) => detailInflight.delete(caseId));
    });
  caseIds.forEach((caseId) => {
    detailInflight.set(
      caseId,
      batchPromise.then((details) => details.find((detail) => accountCaseAliases(detail).has(caseId)) || null)
    );
  });
}

async function fetchTickets({ force = false, renderOnUpdate = false } = {}) {
  summaryRequestController?.abort();
  const controller = new AbortController();
  summaryRequestController = controller;
  const generation = ++summaryRequestGeneration;
  const cacheKey = currentSummaryKey();
  let cached = summaryCache.get(cacheKey);
  let age = cached ? Date.now() - cached.cachedAt : Infinity;
  if (cached && age >= CACHE_HARD_EXPIRY_MS) {
    summaryCache.delete(cacheKey);
    cached = null;
    age = Infinity;
  }

  if (!force && cached && age < CACHE_HARD_EXPIRY_MS) {
    touchCacheEntry(summaryCache, cacheKey, cached, SUMMARY_CACHE_LIMIT);
    applyTicketPage(cached.data, cached.countsVersion || 0);
    state.isLoadingHistory = false;
    prefetchTicketDetails(state.history);
    if (renderOnUpdate) render();
    if (age < SUMMARY_FRESH_MS) return;
  } else {
    state.isLoadingHistory = !cached;
    if (!cached) {
      state.history = [];
      state.pagination = {
        page: state.currentPage,
        pageSize: PAGE_SIZE,
        total: 0,
        totalPages: 1,
        hasMore: false,
      };
    }
    if (renderOnUpdate) render();
  }

  try {
    const response = await fetch(buildTicketListUrl(), { cache: "no-store", signal: controller.signal });
    if (!response.ok) throw new Error("Could not load Account Cases.");
    const data = await response.json();
    if (generation !== summaryRequestGeneration || cacheKey !== currentSummaryKey()) return;
    const countsVersion = Date.now();
    touchCacheEntry(summaryCache, cacheKey, { data, cachedAt: countsVersion, countsVersion }, SUMMARY_CACHE_LIMIT);
    applyTicketPage(data, countsVersion);
    state.isLoadingHistory = false;
    prefetchTicketDetails(state.history);
    if (renderOnUpdate) render();
  } catch (error) {
    if (error?.name === "AbortError") return;
    if (!cached) {
      state.history = [];
      state.pagination = {
        page: state.currentPage,
        pageSize: PAGE_SIZE,
        total: 0,
        totalPages: 1,
        hasMore: false,
      };
    }
    state.isLoadingHistory = false;
    if (renderOnUpdate) render();
  }
}

function isActiveRerouteJob(job = state.rerouteJob) {
  return ["queued", "running"].includes(String(job?.status || ""));
}

async function readResponsePayload(response) {
  const raw = await response.text();
  if (!raw.trim()) return {};
  try {
    return JSON.parse(raw);
  } catch (_error) {
    return { detail: raw.trim() };
  }
}

function responseErrorMessage(payload, fallback) {
  const detail = payload?.detail;
  if (typeof detail === "string" && detail.trim()) return detail.trim();
  if (detail && typeof detail.message === "string" && detail.message.trim()) {
    return detail.message.trim();
  }
  if (typeof payload?.message === "string" && payload.message.trim()) {
    return payload.message.trim();
  }
  return fallback;
}

function rerunConflictMessage(payload, fallback) {
  const detail = payload?.detail;
  if (detail?.code !== "account_rerun_readiness_blocked") {
    return responseErrorMessage(payload, fallback);
  }
  const unknownCount = Array.isArray(detail.unknown_case_ids)
    ? detail.unknown_case_ids.length
    : 0;
  const countSummary = unknownCount
    ? ` ${unknownCount} Automation Case${unknownCount === 1 ? " has" : "s have"} an unknown internal-email delivery state.`
    : "";
  return `Preflight blocked.${countSummary} Confirm their delivery state before starting a full rerun.`;
}

function createSingleCaseRerunIdempotencyKey() {
  const cryptoApi = globalThis.crypto;
  if (typeof cryptoApi?.randomUUID === "function") {
    return `account-case-rerun:${cryptoApi.randomUUID()}`;
  }
  if (typeof cryptoApi?.getRandomValues !== "function") {
    throw new Error("Secure request identifiers are unavailable in this browser.");
  }
  const entropy = new Uint32Array(4);
  cryptoApi.getRandomValues(entropy);
  return `account-case-rerun:${Array.from(entropy, (value) => value.toString(16).padStart(8, "0")).join("")}`;
}

async function refreshAfterReroute(targetCaseId = "") {
  invalidateSummaryCache();
  if (targetCaseId) invalidateDetailCache(targetCaseId);
  else invalidateDetailCache();
  await fetchTickets({ force: true });
  if (state.view === "detail" && state.activeItem) {
    const activeCaseId = accountCaseIdentifier(state.activeItem);
    const lookupId = targetCaseId && accountCaseAliases(state.activeItem).has(targetCaseId)
      ? targetCaseId
      : activeCaseId;
    if (lookupId) {
      state.activeItem = (await fetchTicketDetail(lookupId, { force: true })) || state.activeItem;
    }
  }
}

async function fetchRerouteJob(jobId, { refreshCasesOnCompletion = false } = {}) {
  const normalizedJobId = String(jobId || "").trim();
  if (!normalizedJobId) return fetchLatestRerouteJob({ refreshCasesOnCompletion });
  const wasActive = isActiveRerouteJob();
  try {
    const response = await fetch(`/api/account/rerun-jobs/${encodeURIComponent(jobId)}`, {
      cache: "no-store",
    });
    const payload = await readResponsePayload(response);
    if (!response.ok) {
      throw new Error(responseErrorMessage(payload, "Could not load rerun status."));
    }
    if (String(payload.job_id || "") !== normalizedJobId) return;
    state.rerouteJob = payload;
    state.rerouteError = "";
    if (refreshCasesOnCompletion && wasActive && !isActiveRerouteJob(payload)) {
      const targetCaseId = state.rerouteActiveTargetCaseId;
      await refreshAfterReroute(targetCaseId);
      showToast(
        payload.status === "completed"
          ? targetCaseId ? "Account Case rerun complete" : "All Account Cases were reprocessed"
          : "Account Case reprocessing finished with issues"
      );
      state.rerouteActiveTargetCaseId = "";
    }
  } catch (err) {
    state.rerouteError = err instanceof Error ? err.message : "Could not load rerun status.";
  }
}

async function fetchLatestRerouteJob({ refreshCasesOnCompletion = false } = {}) {
  const wasActive = isActiveRerouteJob();
  try {
    const response = await fetch("/api/account/rerun-jobs/latest");
    const payload = await readResponsePayload(response);
    if (!response.ok) {
      throw new Error(responseErrorMessage(payload, "Could not load rerun status."));
    }
    state.rerouteJob = payload;
    state.rerouteError = "";
    if (refreshCasesOnCompletion && wasActive && !isActiveRerouteJob()) {
      await refreshAfterReroute(state.rerouteActiveTargetCaseId);
      showToast(
        state.rerouteJob.status === "completed"
          ? "All Account Cases were reprocessed"
          : "Account Case reprocessing finished with issues"
      );
      state.rerouteActiveTargetCaseId = "";
    }
  } catch (err) {
    state.rerouteError = err instanceof Error ? err.message : "Could not load rerun status.";
  }
}

function updateReroutePolling() {
  if (reroutePollTimer) {
    window.clearTimeout(reroutePollTimer);
    reroutePollTimer = null;
  }
  if (!isActiveRerouteJob()) return;
  reroutePollTimer = window.setTimeout(async () => {
    const jobId = String(state.rerouteJob?.job_id || "").trim();
    if (jobId) await fetchRerouteJob(jobId, { refreshCasesOnCompletion: true });
    else await fetchLatestRerouteJob({ refreshCasesOnCompletion: true });
    render();
  }, 3000);
}

async function startFullReroute() {
  if (state.isStartingReroute || isActiveRerouteJob()) return;
  state.isStartingReroute = true;
  state.rerouteConfirmationOpen = false;
  state.rerouteError = "";
  render();
  try {
    const response = await fetch("/api/account/rerun-jobs", { method: "POST" });
    const payload = await readResponsePayload(response);
    if (!response.ok) {
      if (response.status === 409) {
        await fetchLatestRerouteJob();
        state.rerouteError = rerunConflictMessage(
          payload,
          "Account Case reprocessing could not start because another rerun is active."
        );
        return;
      }
      throw new Error(
        responseErrorMessage(payload, "Could not start Account Case reprocessing.")
      );
    }
    state.rerouteJob = payload;
    state.rerouteActiveTargetCaseId = "";
    showToast("Account Case reprocessing started");
  } catch (err) {
    state.rerouteError = err instanceof Error ? err.message : "Could not start Account Case rerun.";
  } finally {
    state.isStartingReroute = false;
    render();
  }
}

async function resumeRerouteJob() {
  const jobId = String(state.rerouteJob?.job_id || "").trim();
  if (!jobId || state.isStartingReroute || isActiveRerouteJob()) return;
  state.isStartingReroute = true;
  state.rerouteError = "";
  render();
  try {
    const response = await fetch(`/api/account/rerun-jobs/${encodeURIComponent(jobId)}/resume`, {
      method: "POST",
      cache: "no-store",
    });
    const payload = await readResponsePayload(response);
    if (!response.ok) {
      if (response.status === 409) {
        await fetchRerouteJob(jobId);
        throw new Error(responseErrorMessage(payload, "This rerun cannot be resumed."));
      }
      throw new Error(responseErrorMessage(payload, "Could not resume Account Case reprocessing."));
    }
    state.rerouteJob = payload;
    state.rerouteActiveTargetCaseId = "";
    showToast("Account Case rerun resumed");
  } catch (err) {
    state.rerouteError = err instanceof Error ? err.message : "Could not resume Account Case rerun.";
  } finally {
    state.isStartingReroute = false;
    render();
  }
}

async function startSingleCaseRerun() {
  const snapshot = state.rerouteTargetSnapshot;
  const caseId = String(snapshot?.caseId || "").trim();
  if (!caseId || state.isStartingReroute || isActiveRerouteJob()) return;
  state.isStartingReroute = true;
  state.rerouteConfirmationOpen = false;
  state.rerouteError = "";
  render();
  try {
    const response = await fetch(`/api/account/cases/${encodeURIComponent(caseId)}/rerun`, {
      method: "POST",
      cache: "no-store",
      headers: { "Idempotency-Key": snapshot.idempotencyKey },
    });
    const payload = await readResponsePayload(response);
    if (!response.ok) {
      if (response.status === 409) {
        await fetchLatestRerouteJob();
        state.rerouteError = rerunConflictMessage(
          payload,
          "This Account Case could not be rerun because another rerun is active."
        );
        return;
      }
      throw new Error(responseErrorMessage(payload, "Could not rerun this Account Case."));
    }
    state.rerouteJob = payload;
    state.rerouteActiveTargetCaseId = caseId;
    state.rerouteTargetSnapshot = null;
    showToast(`Rerun started for Case #${snapshot.ticketNumber}`);
  } catch (err) {
    state.rerouteError = err instanceof Error ? err.message : "Could not rerun this Account Case.";
    if (state.rerouteTargetSnapshot === snapshot && !isActiveRerouteJob()) {
      state.rerouteConfirmationOpen = true;
    }
  } finally {
    state.isStartingReroute = false;
    render();
  }
}

async function fetchTicketDetail(ticketId, { force = false } = {}) {
  const summary = state.history.find((item) => accountCaseAliases(item).has(String(ticketId))) || null;
  const expectedRevision = String(summary?.detail_revision || "");
  const cached = !force ? findDetailCacheEntry(ticketId, expectedRevision) : null;
  if (cached && Date.now() - cached.cachedAt < DETAIL_FRESH_MS) return cached.data;
  if (!force && detailInflight.has(ticketId)) return detailInflight.get(ticketId);

  const controller = new AbortController();
  detailRequestControllers.add(controller);
  const request = (async () => {
  try {
    const response = await fetch(`/api/account/cases/${encodeURIComponent(ticketId)}`, {
      cache: "no-store",
      signal: controller.signal,
    });
    if (!response.ok) return null;
    const detail = await response.json();
    cacheDetail(detail);
    return detail;
  } catch (error) {
    if (error?.name === "AbortError") return null;
    return null;
  } finally {
    detailRequestControllers.delete(controller);
    detailInflight.delete(ticketId);
  }
  })();
  detailInflight.set(ticketId, request);
  return request;
}

async function searchCaseByNumber(event) {
  event?.preventDefault();
  const ticketNumber = normalizeCaseNumberQuery(state.caseSearchQuery);
  if (!ticketNumber) {
    state.caseSearchError = "Enter an exact numeric Case #.";
    render();
    return;
  }

  caseSearchRequestController?.abort();
  const controller = new AbortController();
  caseSearchRequestController = controller;
  const generation = ++caseSearchGeneration;
  state.isSearchingCase = true;
  state.caseSearchError = "";
  render();
  try {
    const response = await fetch(`/api/account/cases/${encodeURIComponent(ticketNumber)}`, {
      cache: "no-store",
      signal: controller.signal,
    });
    const payload = await readResponsePayload(response);
    if (generation !== caseSearchGeneration) return;
    if (response.status === 404) {
      state.caseSearchError = `Case #${ticketNumber} not found`;
      return;
    }
    if (!response.ok) {
      throw new Error(responseErrorMessage(payload, "Could not search Account Cases."));
    }
    cacheDetail(payload);
    state.activeItem = payload;
    state.view = "detail";
    state.detailLoading = false;
    state.caseSearchQuery = ticketNumber;
    resetCorrectionState(payload);
  } catch (error) {
    if (error?.name === "AbortError" || generation !== caseSearchGeneration) return;
    state.caseSearchError = error instanceof Error
      ? error.message
      : "Could not search Account Cases.";
  } finally {
    if (generation === caseSearchGeneration) {
      state.isSearchingCase = false;
      render();
    }
  }
}

async function fetchRouteErrorSummary() {
  if (isFetchingRouteErrorSummary) return;
  isFetchingRouteErrorSummary = true;
  try {
    const response = await fetch("/api/account/route-errors/summary?limit=100");
    if (!response.ok) return;
    state.routeErrorSummary = await response.json();
  } catch {
    state.routeErrorSummary = null;
  } finally {
    isFetchingRouteErrorSummary = false;
    if (state.statusFilter === "route_errors") {
      render();
    }
  }
}

function resetCorrectionState(item = null) {
  const currentScope = item?.scope_label;
  const selected = ROUTE_TUPLE_OPTIONS.find(
    (option) => option.category && option.category === item?.category && option.subcategory === item?.subcategory
  ) || ROUTE_TUPLE_OPTIONS.find(
    (option) => option.scope === currentScope && option.action === (item?.execution_action || item?.route)
  );
  state.correctionScope = selected ? selected.scope : "";
  state.correctionAction = selected ? selected.action : "";
  state.isSubmittingCorrection = false;
  state.correctionError = "";
  state.routeCorrectionExpanded = false;
  state.isSubmittingReview = false;
  state.reviewError = "";
}

async function openTicket(ticketId) {
  const generation = ++detailOpenGeneration;
  const summary = state.history.find((item) => accountCaseAliases(item).has(String(ticketId))) || null;
  const cached = findDetailCacheEntry(ticketId, String(summary?.detail_revision || ""));
  state.view = "detail";
  state.activeItem = cached?.data || null;
  state.detailLoading = !cached;
  state.replyMessage = "";
  state.replyError = "";
  resetCorrectionState(cached?.data || null);
  render();

  if (cached && Date.now() - cached.cachedAt < DETAIL_FRESH_MS) return;
  const detail = await fetchTicketDetail(ticketId, { force: Boolean(cached) });
  if (generation !== detailOpenGeneration) return;
  if (!detail) {
    state.detailLoading = false;
    showToast("Failed to load Account Case details.");
    render();
    return;
  }
  state.activeItem = detail;
  state.detailLoading = false;
  resetCorrectionState(detail);
  render();
}

function openCreateView() {
  detailOpenGeneration += 1;
  state.activeItem = null;
  state.detailLoading = false;
  state.view = "create";
  state.error = "";
  state.replyMessage = "";
  state.replyError = "";
  resetCorrectionState();
  render();
}

async function submitAccountIntake(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const formData = new FormData(form);
  state.title = String(formData.get("title") || "").trim();
  state.question = String(formData.get("question") || "").trim();
  state.customerEmail = String(formData.get("customerEmail") || "").trim();

  if (!state.title || !state.question) {
    state.error = "Title and question are required.";
    render();
    return;
  }

  state.isSubmitting = true;
  state.error = "";
  render();

  try {
    const response = await fetch("/account", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        title: state.title,
        question: state.question,
        customer_email: state.customerEmail || null,
        source: state.source,
      }),
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(payload.detail || "Account intake failed.");
    }
    showToast(payload.ticket_id ? `Ticket ${payload.ticket_id} created` : "Ticket created");
    invalidateSummaryCache();
    await fetchTickets({ force: true });
    if (payload.ticket_id) {
      await openTicket(payload.ticket_id);
    }
    state.title = "";
    state.question = "";
    state.customerEmail = "";
  } catch (err) {
    state.error = err instanceof Error ? err.message : "Account intake failed.";
  } finally {
    state.isSubmitting = false;
    render();
  }
}

function isAutomationStatus(status) {
  return status === "automation" || status === "automated";
}

function isAutomatedRoute(item) {
  const routeStatus = String(item?.route_status || "").trim();
  if (routeStatus) return routeStatus === "automated";
  return isAutomationStatus(item?.status || item?.automation_status || "not_automated");
}

function displayRouteStatus(item) {
  return isAutomatedRoute(item) ? "automated" : "not_automated";
}

function renderFilterCount(count) {
  return count === null ? "" : `<span class="filter-count" aria-label="${count} cases">${count}</span>`;
}

function renderCaseSearch() {
  return `
    <form class="account-case-search" data-case-search-form role="search" aria-label="Find an exact Account Case number">
      <div class="account-case-search__control">
        <span class="material-symbols-outlined" aria-hidden="true">search</span>
        <input
          type="search"
          inputmode="numeric"
          pattern="#?[0-9]+"
          placeholder="Case #"
          aria-label="Case number"
          value="${escapeHtml(state.caseSearchQuery)}"
          data-case-search-input
          ${state.isSearchingCase ? "disabled" : ""}
        />
        <button type="submit" aria-label="Search case" ${state.isSearchingCase ? "disabled" : ""}>
          <span class="material-symbols-outlined" aria-hidden="true">arrow_forward</span>
        </button>
      </div>
      <div class="account-case-search__status ${state.caseSearchError ? "account-case-search__status--error" : ""}" aria-live="polite">
        ${state.isSearchingCase ? "Searching..." : escapeHtml(state.caseSearchError)}
      </div>
    </form>
  `;
}

function renderFilterControls() {
  const definitions = Array.isArray(state.filterDefinitions) && state.filterDefinitions.length
    ? state.filterDefinitions
    : DEFAULT_FILTER_DEFINITIONS;
  const viewModel = buildRouteFilterViewModel(definitions, state.filterCounts, state.statusFilter);
  return `
    <div class="route-filter" aria-label="Account case route filters">
      <div class="route-filter__groups" role="group" aria-label="Primary route categories">
        ${viewModel.groups
          .map((group) => `
            <button
              class="route-filter__group-button ${group.active ? "route-filter__group-button--active" : ""} ${group.id === "all" ? "route-filter__group-button--all" : ""}"
              type="button"
              data-action="set-route-group"
              data-value="${escapeHtml(group.id)}"
              aria-pressed="${group.active}"
            >${escapeHtml(group.label)}${renderFilterCount(group.count)}</button>
          `)
          .join("")}
      </div>
      <div class="route-filter__subcategory-field">
        <label class="route-filter__label" for="account-route-subcategory">Subcategory</label>
        <select
          class="route-filter__subcategory"
          id="account-route-subcategory"
          data-action="set-route-subcategory"
          data-group="${escapeHtml(viewModel.groupKey)}"
          ${viewModel.selectDisabled ? "disabled" : ""}
        >
          ${viewModel.selectDisabled
            ? '<option value="">No subcategories</option>'
            : viewModel.options
              .map((option) => `
                <option value="${escapeHtml(option.value)}" ${option.value === viewModel.leafKey ? "selected" : ""}>
                  ${escapeHtml(option.label)}${option.count === null ? "" : ` (${option.count})`}
                </option>
              `)
              .join("")}
        </select>
      </div>
    </div>
  `;
}

function selectedFilterLabel() {
  const key = String(state.statusFilter || "all");
  for (const group of state.filterDefinitions || DEFAULT_FILTER_DEFINITIONS) {
    if (group.id === key) return group.label;
    const child = (group.children || []).find((item) => `${group.id}:${item.id}` === key);
    if (child) return `${group.label} / ${child.label}`;
  }
  return "All";
}

function paginationPages(currentPage, totalPages) {
  const pages = [];
  const add = (value) => {
    if (!pages.includes(value)) pages.push(value);
  };
  add(1);
  add(currentPage - 1);
  add(currentPage);
  add(currentPage + 1);
  add(totalPages);
  return pages
    .filter((value) => value >= 1 && value <= totalPages)
    .sort((a, b) => a - b)
    .reduce((items, value, index, source) => {
      if (index > 0 && value - source[index - 1] > 1) {
        items.push("ellipsis");
      }
      items.push(value);
      return items;
    }, []);
}

function renderPaginationControls() {
  const totalPages = Math.max(1, state.pagination.totalPages || 1);
  if (totalPages <= 1) return "";
  const currentPage = Math.min(Math.max(1, state.currentPage || 1), totalPages);
  const pageItems = paginationPages(currentPage, totalPages);
  return `
    <nav class="history-pagination" aria-label="Account case pages">
      <button
        class="pagination-button"
        type="button"
        data-action="set-page"
        data-page="${currentPage - 1}"
        ${currentPage <= 1 ? "disabled" : ""}
        aria-label="Previous page"
      >
        <span class="material-symbols-outlined">chevron_left</span>
      </button>
      ${pageItems
        .map((item) => {
          if (item === "ellipsis") {
            return `<span class="pagination-ellipsis" aria-hidden="true">...</span>`;
          }
          const isActive = item === currentPage;
          return `
            <button
              class="pagination-button ${isActive ? "pagination-button--active" : ""}"
              type="button"
              data-action="set-page"
              data-page="${item}"
              ${isActive ? 'aria-current="page"' : ""}
            >${item}</button>
          `;
        })
        .join("")}
      <button
        class="pagination-button"
        type="button"
        data-action="set-page"
        data-page="${currentPage + 1}"
        ${currentPage >= totalPages ? "disabled" : ""}
        aria-label="Next page"
      >
        <span class="material-symbols-outlined">chevron_right</span>
      </button>
    </nav>
  `;
}

function renderHistorySidebar() {
  if (state.isLoadingHistory && !state.history.length) {
    return `
      ${renderCaseSearch()}
      ${renderFilterControls()}
      <div class="history-loading" role="status" aria-label="Loading Account Cases">
        ${Array.from({ length: 5 }, () => '<span class="loading-line"></span>').join("")}
      </div>
    `;
  }
  if (!state.history.length) {
    return `
      ${renderCaseSearch()}
      ${renderFilterControls()}
      <div class="history-empty">
        <span class="material-symbols-outlined">receipt_long</span>
        <p>No Account Cases yet</p>
      </div>
    `;
  }
  return `
    ${renderCaseSearch()}
    ${renderFilterControls()}
    <div class="history-section-title">${escapeHtml(selectedFilterLabel())} Account Cases (${escapeHtml(state.pagination.total)})</div>
    ${state.history
      .map(
        (item) => {
          const itemId = item.account_case_id || item.ticket_id || item.billing_ticket_id || "";
          const itemTicketId = item.ticket_id || item.client_ticket_id || "";
          const activeTicketId = state.activeItem ? (state.activeItem.ticket_id || state.activeItem.client_ticket_id || "") : "";
          const activeBillingId = state.activeItem ? (state.activeItem.account_case_id || state.activeItem.billing_ticket_id || "") : "";
          const isActive = (activeBillingId && activeBillingId === itemId) || (activeTicketId && activeTicketId === itemTicketId);
          const itemSource = item.source || "";
          const itemStatus = displayRouteStatus(item);
          const ticketNumber = accountTicketNumber(item);
          return `
    <button class="history-item ${isActive ? "history-item--active" : ""}" type="button" data-action="open-ticket" data-id="${escapeHtml(itemId)}">
      <div class="history-item-header">
        <div class="history-item-identity">
          <span class="history-ticket-number">#${escapeHtml(ticketNumber)}</span>
          <strong>${escapeHtml(item.title || "")}</strong>
        </div>
        ${renderSourceValue(itemSource)}
      </div>
      ${renderClassificationBadges(item)}
      <div class="history-item-meta">
        <span class="status-badge status-badge--${escapeHtml(itemStatus)}">${escapeHtml(statusLabel(itemStatus))}</span>
        <span class="history-time">${escapeHtml((item.updated_at || item.created_at || "").slice(0, 16).replace("T", " "))}</span>
      </div>
    </button>
  `;
        }
      )
      .join("")}
    ${renderPaginationControls()}
  `;
}

function renderCreateForm() {
  return `
    <div class="panel form-stack">
      <div class="form-header">
        <h3>Create a ticket</h3>
        <p class="form-desc">Submit an account case for routing and automated processing.</p>
      </div>
      <form data-account-form>
        <label class="field">
          <span class="field-label">Title</span>
          <input class="input" name="title" value="${escapeHtml(state.title)}" placeholder="Detailed invoice request" autocomplete="off" />
        </label>
        <label class="field">
          <span class="field-label">Customer email</span>
          <input class="input" name="customerEmail" value="${escapeHtml(state.customerEmail)}" placeholder="customer@example.com" autocomplete="off" />
        </label>
        <label class="field">
          <span class="field-label">Question</span>
          <textarea class="textarea" name="question" placeholder="Issue date: 6 May 2026&#10;Transaction ID: 1104245232004173824&#10;Amount: USD 705.97">${escapeHtml(state.question)}</textarea>
        </label>
        <div class="actions">
          <button class="primary-button" type="submit" ${state.isSubmitting ? "disabled" : ""}>
            <span class="material-symbols-outlined">send</span>
            ${state.isSubmitting ? "Creating..." : "Create ticket"}
          </button>
        </div>
      </form>
      ${
        state.error
          ? `<div class="error-banner"><span class="material-symbols-outlined">error</span>${escapeHtml(state.error)}</div>`
          : ""
      }
    </div>
  `;
}

function renderMessageThread() {
  const item = state.activeItem;
  if (!item) return "";
  const messages = Array.isArray(item.messages) ? item.messages : [];
  if (!messages.length) return "";

  return `
    <div class="message-thread">
      <div class="detail-section-title">Conversation</div>
      ${messages
        .map((msg) => {
          const role = String(msg.role || "").toLowerCase();
          const content = String(msg.content || "");
          const isCustomer = role === "customer" || role === "user";
          const bubbleClass = isCustomer ? "msg-bubble--customer" : "msg-bubble--assistant";
          const label = isCustomer ? "Customer" : "AI";
          const timestamp = formatMessageTimestamp(msg.created_at);
          return `
        <div class="msg-row ${isCustomer ? "msg-row--customer" : "msg-row--assistant"}">
          <div class="msg-bubble ${bubbleClass}">
            <div class="msg-header"><span class="msg-label">${escapeHtml(label)}</span><time datetime="${escapeHtml(msg.created_at || "")}">${escapeHtml(timestamp)}</time></div>
            <div class="msg-content">${renderMarkdownMessage(content)}</div>
          </div>
        </div>
      `;
        })
        .join("")}
      ${renderAiReplyState(item)}
    </div>
  `;
}

function renderReplyComposer() {
  const item = state.activeItem;
  if (!item) return "";
  return `
    <div class="reply-composer">
      <div class="detail-section-title">Add customer message</div>
      <textarea
        class="reply-textarea"
        placeholder="Add the customer's latest message..."
        data-reply-input
        ${state.isSubmittingReply ? "disabled" : ""}
      >${escapeHtml(state.replyMessage)}</textarea>
      <div class="reply-actions">
        <button
          class="primary-button primary-button--small"
          type="button"
          data-action="submit-reply"
          ${state.isSubmittingReply ? "disabled" : ""}
        >
          <span class="material-symbols-outlined">send</span>
          ${state.isSubmittingReply ? "Adding..." : "Add message"}
        </button>
      </div>
      ${
        state.replyError
          ? `<div class="error-banner"><span class="material-symbols-outlined">error</span>${escapeHtml(state.replyError)}</div>`
          : ""
      }
    </div>
  `;
}

function routeTupleSelectValue() {
  if (!state.correctionScope || !state.correctionAction) return DEFAULT_ROUTE_TUPLE_SELECT_VALUE;
  return `${state.correctionScope}|${state.correctionAction}`;
}

function renderRouteCorrectionPanel() {
  const item = state.activeItem;
  if (!item) return "";
  const currentCorrection = item.route_correction || {};
  const selectedValue = routeTupleSelectValue();
  const hasCorrection = Boolean(item.route_corrected || currentCorrection.corrected_execution_action);
  const originalAction =
    currentCorrection.original_execution_action || item.execution_action || item.route || "";
  const correctedAction = currentCorrection.corrected_execution_action || "";
  const showChangeRecord = Boolean(originalAction && correctedAction && originalAction !== correctedAction);
  return `
    <div class="route-correction detail-section" ${state.routeCorrectionExpanded ? "" : "hidden"}>
      <div class="detail-section-title">Route correction</div>
      ${
        hasCorrection
          ? `<div class="route-correction-current">
              <span class="meta-label">Current correction</span>
              <span class="meta-value">${escapeHtml(
                [
                  currentCorrection.corrected_scope_label || item.scope_label,
                  currentCorrection.corrected_route_family || item.route_family,
                  currentCorrection.corrected_execution_action || item.execution_action || item.route,
                ]
                  .filter(Boolean)
                  .join(" / ")
              )}</span>
            </div>`
          : ""
      }
      ${
        showChangeRecord
          ? `<div class="route-change-record">route changed from ${escapeHtml(originalAction)} to ${escapeHtml(correctedAction)}</div>`
          : ""
      }
      <label class="field">
        <span class="field-label">Correct route tuple</span>
        <select class="input" data-correction-select ${state.isSubmittingCorrection ? "disabled" : ""}>
          <option value="${DEFAULT_ROUTE_TUPLE_SELECT_VALUE}" ${selectedValue === DEFAULT_ROUTE_TUPLE_SELECT_VALUE ? "selected" : ""}>Select scope / action</option>
          ${ROUTE_TUPLE_OPTIONS.map((option) => {
            const value = `${option.scope}|${option.action}`;
            return `<option value="${escapeHtml(value)}" ${selectedValue === value ? "selected" : ""}>${escapeHtml(option.label)}</option>`;
          }).join("")}
        </select>
      </label>
      <div class="reply-actions">
        <button
          class="primary-button primary-button--small"
          type="button"
          data-action="submit-route-correction"
          ${state.isSubmittingCorrection ? "disabled" : ""}
        >
          <span class="material-symbols-outlined">rule_settings</span>
          ${state.isSubmittingCorrection ? "Saving..." : "Save correction"}
        </button>
      </div>
      ${
        state.correctionError
          ? `<div class="error-banner"><span class="material-symbols-outlined">error</span>${escapeHtml(state.correctionError)}</div>`
          : ""
      }
    </div>
  `;
}

function renderRouteErrorSummaryPanel() {
  if (state.statusFilter !== "route_errors") return "";
  const summary = state.routeErrorSummary;
  if (!summary) {
    return `
      <div class="route-summary detail-section">
        <div class="detail-section-title">Route error summary</div>
        <p class="result-copy">Loading route error summary...</p>
      </div>
    `;
  }
  const transitions = summary.transitions || summary.top_transitions || summary.predicted_to_corrected || [];
  return `
    <div class="route-summary detail-section">
      <div class="detail-section-title">Route error summary</div>
      <div class="route-summary-grid">
        <div><span class="meta-label">Total</span><strong>${escapeHtml(summary.total_error_cases ?? summary.total ?? 0)}</strong></div>
        <div><span class="meta-label">Corrected</span><strong>${escapeHtml(summary.corrected_count ?? 0)}</strong></div>
        <div><span class="meta-label">Low confidence</span><strong>${escapeHtml(summary.low_confidence_count ?? 0)}</strong></div>
      </div>
      ${
        transitions.length
          ? `<div class="route-transition-list">${transitions
              .slice(0, 6)
              .map((entry) => {
                if (entry.transition) {
                  return `<div class="route-transition"><span>${escapeHtml(entry.transition)}</span><strong>${escapeHtml(entry.count ?? "")}</strong></div>`;
                }
                const from = entry.predicted || entry.original || entry.from || entry.original_execution_action || "Unknown";
                const to = entry.corrected || entry.to || entry.corrected_execution_action || "Uncorrected";
                const count = entry.count ?? entry.total ?? "";
                return `<div class="route-transition"><span>${escapeHtml(from)} -> ${escapeHtml(to)}</span><strong>${escapeHtml(count)}</strong></div>`;
              })
              .join("")}</div>`
          : ""
      }
    </div>
  `;
}

const ACCOUNT_PERSONA_PRESENTATION = Object.freeze({
  "sid-precise": { style: "Precise", styleKey: "precise" },
  "sid-bright": { style: "Bright", styleKey: "bright" },
  "default-support": { style: "Warm", styleKey: "warm" },
});

function renderPersonaAssignment(item) {
  if (!isAutomatedRoute(item)) return "";
  const assignment = item?.persona_assignment;
  if (!assignment || typeof assignment !== "object") {
    return `<div class="meta-row persona-assignment"><span class="meta-label">Persona</span><span class="meta-value persona-assignment__value">Not assigned yet</span></div>`;
  }
  const personaKey = String(assignment.persona_key || "").trim();
  const presentation = Object.hasOwn(ACCOUNT_PERSONA_PRESENTATION, personaKey)
    ? ACCOUNT_PERSONA_PRESENTATION[personaKey]
    : null;
  const displayName = String(assignment.display_name || personaKey || "Unknown Persona").trim();
  const version = Number(assignment.version);
  const versionLabel = Number.isInteger(version) && version > 0 ? `v${version}` : "Version unavailable";
  return `<div class="meta-row persona-assignment"><span class="meta-label">Persona</span><span class="meta-value persona-assignment__value"><strong>${escapeHtml(displayName)}</strong><span class="persona-version-badge">${escapeHtml(versionLabel)}</span>${presentation ? `<span class="persona-style-badge persona-style-badge--${presentation.styleKey}">${presentation.style}</span>` : ""}</span></div>`;
}

function renderDetailView() {
  const item = state.activeItem;
  if (!item && state.detailLoading) {
    return `
      <div class="panel detail-stack detail-loading" role="status" aria-label="Loading Account Case details">
        <span class="loading-line loading-line--title"></span>
        <span class="loading-line"></span>
        <span class="loading-line"></span>
        <span class="loading-line loading-line--wide"></span>
        <span class="loading-line loading-line--wide"></span>
      </div>
    `;
  }
  if (!item) return "";

  let missingFields = [];
  if (Array.isArray(item.missing_fields)) missingFields = item.missing_fields;
  else if (typeof item.missing_fields === "string") {
    try { missingFields = JSON.parse(item.missing_fields || "[]"); } catch {}
  }

  let collectedFields = {};
  if (typeof item.collected_fields === "object" && item.collected_fields !== null) {
    collectedFields = item.collected_fields;
  }

  const itemSource = item.source || "";
  const itemStatus = displayRouteStatus(item);
  const ticketId = item.ticket_id || item.client_ticket_id || "";
  const ticketNumber = accountTicketNumber(item);
  const accountCaseId = item.account_case_id || item.billing_ticket_id || "";
  const hasDifferentInternalTicketId = Boolean(ticketId && ticketNumber && String(ticketId) !== ticketNumber);
  const routeClassification =
    typeof item.route_classification === "object" && item.route_classification !== null
      ? item.route_classification
      : {};

  return `
    <div class="panel detail-stack">
      <div class="detail-header">
        <div class="detail-title">
          <span class="detail-ticket-number">Ticket #${escapeHtml(ticketNumber)}</span>
          <h3>${escapeHtml(item.title || "")}</h3>
        </div>
        <div class="detail-header__actions">
          ${renderClassificationBadges(item)}
          <button
            class="danger-button detail-rerun-button"
            type="button"
            data-action="open-single-rerun-confirmation"
            ${state.isStartingReroute || isActiveRerouteJob() ? "disabled" : ""}
          >
            <span class="material-symbols-outlined" aria-hidden="true">restart_alt</span>
            Rerun this case
          </button>
        </div>
      </div>
      <div class="meta-grid">
        <div class="meta-row">
          <span class="meta-label">Account Case ID</span>
          <span class="meta-value">${escapeHtml(accountCaseId)}</span>
        </div>
        <div class="meta-row">
          <span class="meta-label">Ticket #</span>
          <span class="meta-value">#${escapeHtml(ticketNumber)}</span>
        </div>
        ${hasDifferentInternalTicketId ? `<div class="meta-row"><span class="meta-label">Internal Ticket ID</span><span class="meta-value">${escapeHtml(ticketId)}</span></div>` : ""}

        <div class="meta-row">
          <span class="meta-label">Source</span>
          <span class="meta-value">${renderSourceValue(itemSource)}</span>
        </div>
        <div class="meta-row">
          <span class="meta-label">Status</span>
          <span class="meta-value status-badge status-badge--${escapeHtml(itemStatus)}">${escapeHtml(statusLabel(itemStatus))}</span>
        </div>
        ${renderPersonaAssignment(item)}
        ${
          routeClassification.automation_mode
            ? `<div class="meta-row"><span class="meta-label">Automation mode</span><span class="meta-value">${escapeHtml(String(routeClassification.automation_mode).replaceAll("_", " "))}</span></div>`
            : ""
        }
        <div class="meta-row meta-row--route-result">
          <span class="meta-label">Route result</span>
          <div class="meta-row--route-result-value">
            <span class="meta-value">${escapeHtml(routeResultLabel(item))}</span>
            <button
              class="filter-chip correct-route-toggle"
              type="button"
              data-action="toggle-route-correction"
              aria-expanded="${state.routeCorrectionExpanded ? "true" : "false"}"
              ${state.isSubmittingReview ? "disabled" : ""}
            >
              correct route
            </button>
            ${
              item.route_review_status === "reviewed"
                ? `<button
                    class="filter-chip unreview-toggle"
                    type="button"
                    data-action="unreview-route"
                    ${state.isSubmittingReview ? "disabled" : ""}
                  >unreview</button>`
                : `<button
                    class="filter-chip pass-route-toggle"
                    type="button"
                    data-action="pass-route"
                    ${state.isSubmittingReview ? "disabled" : ""}
                  >pass</button>`
            }
          </div>
        </div>
        <div class="meta-row">
          <span class="meta-label">Internal email</span>
          <span class="meta-value">${escapeHtml(item.internal_email_send_status || "not_applicable")}</span>
        </div>
        <div class="meta-row">
          <span class="meta-label">Response link</span>
          <span class="meta-value">${escapeHtml(internalEmailResponseLinkStatus(item))}</span>
        </div>
        ${
          item.internal_email_send_reason
            ? `<div class="meta-row"><span class="meta-label">Email reason</span><span class="meta-value">${escapeHtml(item.internal_email_send_reason)}</span></div>`
            : ""
        }
        ${
          item.route_reason_code || item.route_reason
            ? `<div class="meta-row"><span class="meta-label">Route reason</span><span class="meta-value">${escapeHtml(item.route_reason_code || item.route_reason)}</span></div>`
            : ""
        }
        ${
          item.created_at
            ? `<div class="meta-row"><span class="meta-label">Created</span><span class="meta-value">${escapeHtml(item.created_at.slice(0, 16).replace("T", " "))}</span></div>`
            : ""
        }
      </div>
      ${
        routeClassification.superseded_automation_response
          ? `<div class="detail-section warning"><div class="detail-section-title">Prior automation response superseded</div></div>`
          : ""
      }
      ${renderRouteCorrectionPanel()}
      ${
        missingFields.length
          ? `<div class="detail-section warning"><div class="detail-section-title">Missing fields</div><ul class="missing-list">${missingFields
              .map((field) => `<li>${escapeHtml(field)}</li>`)
              .join("")}</ul></div>`
          : ""
      }
      ${
        Object.keys(collectedFields).length
          ? `<div class="detail-section"><div class="detail-section-title">Collected fields</div><ul class="collected-list">${Object.entries(collectedFields)
              .map(([k, v]) => `<li><strong>${escapeHtml(k)}</strong>: ${escapeHtml(String(v))}</li>`)
              .join("")}</ul></div>`
          : ""
      }
      ${renderMessageThread()}
      ${renderReplyComposer()}
    </div>
  `;
}

async function submitRouteCorrection() {
  const item = state.activeItem;
  if (!item) return;
  if (!state.correctionScope || !state.correctionAction) {
    state.correctionError = "Select a route tuple.";
    render();
    return;
  }

  state.isSubmittingCorrection = true;
  state.correctionError = "";
  render();

  try {
    const billingTicketId = item.account_case_id || item.billing_ticket_id || item.ticket_id || "";
    const selectedOption = ROUTE_TUPLE_OPTIONS.find(
      (option) => option.scope === state.correctionScope && option.action === state.correctionAction
    );
    const correctionPayload = selectedOption?.category
      ? { category: selectedOption.category, subcategory: selectedOption.subcategory, corrector: "operator" }
      : {
          scope_label: state.correctionScope,
          execution_action: state.correctionAction,
          corrector: "operator",
        };
    const response = await fetch(`/api/account/cases/${billingTicketId}/route-correction`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(correctionPayload),
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(payload.detail || "Route correction failed.");
    }
    state.activeItem = payload;
    cacheDetail(payload);
    invalidateSummaryCache();
    resetCorrectionState(payload);
    showToast("Route correction saved");
    await fetchTickets({ force: true });
    if (state.statusFilter === "route_errors") {
      await fetchRouteErrorSummary();
    }
  } catch (err) {
    state.correctionError = err instanceof Error ? err.message : "Route correction failed.";
  } finally {
    state.isSubmittingCorrection = false;
    render();
  }
}

async function submitRouteReview(reviewStatus) {
  const item = state.activeItem;
  if (!item) return;

  state.isSubmittingReview = true;
  state.reviewError = "";
  render();

  try {
    const billingTicketId = item.account_case_id || item.billing_ticket_id || item.ticket_id || "";
    const response = await fetch(`/api/account/cases/${billingTicketId}/route-review`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        review_status: reviewStatus,
        reviewer: "operator",
      }),
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(payload.detail || "Route review failed.");
    }
    state.activeItem = payload;
    cacheDetail(payload);
    invalidateSummaryCache();
    showToast(reviewStatus === "reviewed" ? "Route marked as reviewed" : "Route moved back to unreviewed");
    await fetchTickets({ force: true });
  } catch (err) {
    state.reviewError = err instanceof Error ? err.message : "Route review failed.";
  } finally {
    state.isSubmittingReview = false;
    render();
  }
}

async function submitReply() {
  const item = state.activeItem;
  if (!item) return;

  const message = state.replyMessage.trim();
  if (!message) {
    state.replyError = "Reply cannot be empty.";
    render();
    return;
  }

  state.isSubmittingReply = true;
  state.replyError = "";
  render();

  try {
    const billingTicketId = item.account_case_id || item.billing_ticket_id || item.ticket_id || "";
    const response = await fetch(`/api/account/cases/${billingTicketId}/reply`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message }),
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(payload.detail || "Message failed.");
    }
    state.activeItem = payload;
    cacheDetail(payload);
    invalidateSummaryCache();

    state.replyMessage = "";
    showToast("Customer message added");
    await fetchTickets({ force: true });
  } catch (err) {
    state.replyError = err instanceof Error ? err.message : "Reply failed.";
  } finally {
    state.isSubmittingReply = false;
    render();
  }
}

function renderRerouteStatus() {
  const job = state.rerouteJob;
  if ((!job || job.status === "not_started") && !state.rerouteError) return "";
  if (state.rerouteError) {
    return `<div class="reroute-status reroute-status--error" role="alert">${escapeHtml(state.rerouteError)}</div>`;
  }
  const total = Number(job.total || 0);
  const processed = Number(job.processed || 0);
  const percent = total > 0 ? Math.min(100, Math.round((processed / total) * 100)) : 0;
  if (isActiveRerouteJob(job)) {
    return `
      <div class="reroute-status" role="status" aria-live="polite">
        <div class="reroute-status__line">
          <span><strong>Running</strong>${job.status === "queued" ? " · Queued" : job.phase ? ` · ${escapeHtml(job.phase)}` : ""}</span>
          <strong>${processed}${total ? ` of ${total}` : ""}</strong>
        </div>
        <div class="reroute-progress" aria-label="Reroute progress" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${percent}" role="progressbar">
          <span style="width: ${percent}%"></span>
        </div>
      </div>
    `;
  }
  const failed = Number(job.failed || 0);
  const recovered = Number(job.recovered || 0);
  const statusLabel = String(job.status || "") === "failed"
    && String(job.stop_reason || "") === "preflight_failed"
    ? "Preflight failed"
    : String(job.status || "") === "failed" || String(job.status || "") === "completed_with_errors"
      ? "Stopped at Case"
      : String(job.status || "") === "completed"
        ? "Completed"
        : "Rerun status unavailable";
  const failedCase = String(job.failed_case_id || "").trim();
  const failedStage = String(job.failed_stage || "").trim();
  const remaining = Number(job.remaining || 0);
  const stopDetails = failedCase
    ? `Stopped at Case ${failedCase}${failedStage ? ` during ${failedStage}` : ""}`
    : failedStage ? `Stopped during ${failedStage}` : "";
  const resumeButton = Boolean(
    !isActiveRerouteJob(job)
    && (String(job.status || "") === "failed" || String(job.status || "") === "completed_with_errors")
    && (remaining > 0 || failedCase)
  )
    ? `<button class="primary-button primary-button--small" type="button" data-action="resume-reroute" ${state.isStartingReroute ? "disabled" : ""}>Resume rerun</button>`
    : "";
  return `
      <div class="reroute-status ${failed || job.status === "failed" || job.status === "completed_with_errors" ? "reroute-status--error" : "reroute-status--done"}" role="status" aria-live="polite">
      <strong>${escapeHtml(statusLabel)}</strong>
      ${stopDetails ? `<span>${escapeHtml(stopDetails)}</span>` : ""}
      <span>${Number(job.succeeded || 0)} succeeded${failed ? `, ${failed} failed` : ""}; ${remaining} unprocessed${recovered ? `, ${recovered} recovered` : ""}; ${Number(job.changed || 0)} changed; ${Number(job.emails_sent || 0)} emails sent; ${Number(job.replies_scheduled || 0)} replies scheduled; ${Number(job.replies_deleted || 0)} old replies deleted; ${Number(job.reply_jobs_deleted || 0)} old reply jobs deleted; ${Number(job.persona_assignments_deleted || 0)} Persona assignments reset</span>
      ${resumeButton}
    </div>
  `;
}

function renderRerouteConfirmation() {
  if (!state.rerouteConfirmationOpen) return "";
  const snapshot = state.rerouteTargetSnapshot;
  const singleCase = Boolean(snapshot?.caseId);
  return `
    <div class="reroute-modal-backdrop" data-action="close-reroute-confirmation">
      <section class="reroute-modal" role="dialog" aria-modal="true" aria-labelledby="reroute-dialog-title" data-reroute-dialog>
        <div class="reroute-modal__heading">
          <span class="material-symbols-outlined" aria-hidden="true">${singleCase ? "warning" : "sync"}</span>
          <div>
            <h2 id="reroute-dialog-title">${singleCase ? `Rerun Case #${escapeHtml(snapshot.ticketNumber)}?` : "Rerun all Account Cases?"}</h2>
            <p>${singleCase
              ? "This completely resets the active case conversation before running the latest routing workflow."
              : "This starts each case again with the latest Router, Field Extractors, Automation handlers, and Persona."}</p>
          </div>
        </div>
        ${singleCase
          ? `<ul>
              <li>All non-customer messages will be permanently deleted.</li>
              <li>Engineer, manual, and internal messages are included and cannot be recovered.</li>
              <li>The current route review and correction will be reset.</li>
              <li>Automation may send a new internal email.</li>
              <li>A new Account-only reply will be scheduled with the standard 6-10 minute delay.</li>
              <li>Independent audit records will be retained.</li>
            </ul>`
          : `<ul>
              <li>A read-only preflight checks the database contract, managed Prompts, and Account Luna model before the Case loop begins.</li>
              <li>The first Case error stops the rerun immediately; remaining Cases stay unprocessed and can be resumed later.</li>
              <li>Previously sent internal emails will be sent again as a new rerun execution.</li>
              <li>Existing Account-only AI replies and reply jobs will be deleted before each case starts again.</li>
              <li>Account & Billing classification extractors also run again; they never send email or customer replies.</li>
            </ul>`}
        <p>The pinned Persona assignment will be cleared. Only if the rerun produces a new Automation customer reply will runtime select again from Personas that are enabled and have a published version; the same Persona may be selected again.</p>
        <div class="reroute-modal__actions">
          <button class="ghost-button" type="button" data-action="close-reroute-confirmation">Cancel</button>
          <button class="${singleCase ? "danger-button" : "primary-button"}" type="button" data-action="${singleCase ? "confirm-single-rerun" : "confirm-reroute"}" ${state.isStartingReroute ? "disabled" : ""}>
            ${singleCase ? "Rerun this case" : "Rerun all cases"}
          </button>
        </div>
      </section>
    </div>
  `;
}

function render() {
  appRoot.innerHTML = `
    <main class="account-shell">
      <aside class="side-panel">
        <div class="brand">
          <div class="brand-mark"><span class="material-symbols-outlined">support_agent</span></div>
          <div>
            <div class="eyebrow">Account intake</div>
            <h1>Support Portal</h1>
          </div>
        </div>
        <div class="side-actions">
          <button class="primary-button primary-button--small" type="button" data-action="new-ticket">
            <span class="material-symbols-outlined">add</span>
            New Account Case
          </button>
          <button
            class="reroute-button"
            type="button"
            data-action="open-reroute-confirmation"
            ${state.isStartingReroute || isActiveRerouteJob() ? "disabled" : ""}
          >
            <span class="material-symbols-outlined">sync</span>
            Rerun
          </button>
        </div>
        ${renderRerouteStatus()}
        <div class="history-stack" id="history-list">
          ${renderHistorySidebar()}
        </div>
      </aside>
      <section class="workbench">
        <div class="workbench-header">
          <div>
            <span class="pill"><span class="material-symbols-outlined">route</span>HTTP or manual</span>
            <h2>${state.view === "create" ? "Create and route an Account Case" : "Account Case detail"}</h2>
          </div>
          ${state.view === "detail" ? `<button class="ghost-button" type="button" data-action="back-to-create">Back to create</button>` : ""}
        </div>
        <div class="intake-grid">
          ${renderRouteErrorSummaryPanel()}
          ${state.view === "create" ? renderCreateForm() : ""}
          ${state.view === "detail" ? renderDetailView() : ""}
        </div>
      </section>
    </main>
    ${renderRerouteConfirmation()}
  `;
  bind();
  updateReplyPolling();
  updateReroutePolling();
}

document.addEventListener("visibilitychange", () => {
  if (!document.hidden) {
    updateReplyPolling();
    void fetchTickets({ force: true, renderOnUpdate: true });
    if (state.view === "detail" && state.activeItem) {
      const ticketId = accountCaseIdentifier(state.activeItem);
      const generation = detailOpenGeneration;
      if (ticketId) {
        void fetchTicketDetail(ticketId, { force: true }).then((detail) => {
          if (
            detail
            && state.view === "detail"
            && generation === detailOpenGeneration
            && accountCaseIdentifier(state.activeItem) === ticketId
          ) {
            state.activeItem = detail;
            render();
          }
        });
      }
    }
    if (isActiveRerouteJob()) {
      const jobId = String(state.rerouteJob?.job_id || "").trim();
      const refresh = jobId
        ? fetchRerouteJob(jobId, { refreshCasesOnCompletion: true })
        : fetchLatestRerouteJob({ refreshCasesOnCompletion: true });
      void refresh.then(render);
    }
  }
});

window.addEventListener("pagehide", () => {
  summaryRequestController?.abort();
  caseSearchRequestController?.abort();
  detailRequestControllers.forEach((controller) => controller.abort());
  summaryCache.clear();
  detailCache.clear();
  detailInflight.clear();
});

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && state.rerouteConfirmationOpen) {
    state.rerouteConfirmationOpen = false;
    state.rerouteTargetSnapshot = null;
    render();
  }
});

function bind() {
  const caseSearchForm = document.querySelector("[data-case-search-form]");
  if (caseSearchForm) caseSearchForm.addEventListener("submit", searchCaseByNumber);
  const caseSearchInput = document.querySelector("[data-case-search-input]");
  if (caseSearchInput) {
    caseSearchInput.addEventListener("input", (event) => {
      state.caseSearchQuery = event.target.value;
      state.caseSearchError = "";
    });
  }
  const form = document.querySelector("[data-account-form]");
  if (form) {
    form.addEventListener("submit", submitAccountIntake);
  }
  const historyList = document.getElementById("history-list");
  if (historyList) {
    historyList.addEventListener("click", (event) => {
      const button = event.target.closest("[data-action='open-ticket']");
      if (button) {
        const id = button.dataset.id;
        if (id) openTicket(id);
        return;
      }
      const groupButton = event.target.closest("[data-action='set-route-group']");
      if (groupButton) {
        state.statusFilter = groupButton.dataset.value || "all";
        state.currentPage = 1;
        if (state.statusFilter === "route_errors") {
          state.routeErrorSummary = null;
          void fetchRouteErrorSummary();
        }
        void fetchTickets({ renderOnUpdate: true });
        return;
      }
      const pageBtn = event.target.closest("[data-action='set-page']");
      if (pageBtn && !pageBtn.disabled) {
        const targetPage = Number(pageBtn.dataset.page || "1");
        const totalPages = Math.max(1, state.pagination.totalPages || 1);
        state.currentPage = Math.min(Math.max(1, targetPage), totalPages);
        void fetchTickets({ renderOnUpdate: true });
        return;
      }
    });
    historyList.addEventListener("change", (event) => {
      const subcategorySelect = event.target.closest("[data-action='set-route-subcategory']");
      if (!subcategorySelect || subcategorySelect.disabled) return;
      const group = subcategorySelect.dataset.group || "all";
      const subcategory = subcategorySelect.value || "";
      state.statusFilter = subcategory ? `${group}:${subcategory}` : group;
      state.currentPage = 1;
      void fetchTickets({ renderOnUpdate: true });
    });
  }
  document.querySelectorAll("[data-action='new-ticket']").forEach((el) => {
    el.addEventListener("click", openCreateView);
  });
  document.querySelectorAll("[data-action='back-to-create']").forEach((el) => {
    el.addEventListener("click", openCreateView);
  });
  document.querySelectorAll("[data-action='open-reroute-confirmation']").forEach((el) => {
    el.addEventListener("click", () => {
      state.rerouteTargetSnapshot = null;
      state.rerouteConfirmationOpen = true;
      render();
      document.querySelector("[data-reroute-dialog] [data-action='confirm-reroute']")?.focus();
    });
  });
  document.querySelectorAll("[data-action='open-single-rerun-confirmation']").forEach((el) => {
    el.addEventListener("click", () => {
      if (!state.activeItem || state.isStartingReroute || isActiveRerouteJob()) return;
      state.rerouteTargetSnapshot = {
        caseId: accountCaseIdentifier(state.activeItem),
        ticketNumber: accountTicketNumber(state.activeItem),
        idempotencyKey: createSingleCaseRerunIdempotencyKey(),
      };
      state.rerouteConfirmationOpen = true;
      render();
      document.querySelector("[data-reroute-dialog] [data-action='confirm-single-rerun']")?.focus();
    });
  });
  document.querySelectorAll("[data-action='close-reroute-confirmation']").forEach((el) => {
    el.addEventListener("click", (event) => {
      if (
        event.currentTarget.classList.contains("reroute-modal-backdrop")
        && event.target.closest("[data-reroute-dialog]")
      ) return;
      state.rerouteConfirmationOpen = false;
      state.rerouteTargetSnapshot = null;
      render();
    });
  });
  document.querySelectorAll("[data-action='confirm-reroute']").forEach((el) => {
    el.addEventListener("click", () => void startFullReroute());
  });
  document.querySelectorAll("[data-action='resume-reroute']").forEach((el) => {
    el.addEventListener("click", () => void resumeRerouteJob());
  });
  document.querySelectorAll("[data-action='confirm-single-rerun']").forEach((el) => {
    el.addEventListener("click", () => void startSingleCaseRerun());
  });
  document.querySelectorAll("[data-action='submit-reply']").forEach((el) => {
    el.addEventListener("click", submitReply);
  });
  document.querySelectorAll("[data-action='submit-route-correction']").forEach((el) => {
    el.addEventListener("click", submitRouteCorrection);
  });
  document.querySelectorAll("[data-action='toggle-route-correction']").forEach((el) => {
    el.addEventListener("click", () => {
      state.routeCorrectionExpanded = !state.routeCorrectionExpanded;
      render();
    });
  });
  document.querySelectorAll("[data-action='pass-route']").forEach((el) => {
    el.addEventListener("click", () => void submitRouteReview("reviewed"));
  });
  document.querySelectorAll("[data-action='unreview-route']").forEach((el) => {
    el.addEventListener("click", () => void submitRouteReview("pending"));
  });
  const replyInput = document.querySelector("[data-reply-input]");
  if (replyInput) {
    replyInput.addEventListener("input", (event) => {
      state.replyMessage = event.target.value;
    });
  }
  const correctionSelect = document.querySelector("[data-correction-select]");
  if (correctionSelect) {
    correctionSelect.addEventListener("change", (event) => {
      const [scope, action] = String(event.target.value || "").split("|");
      const selected = ROUTE_TUPLE_OPTIONS.find((option) => option.scope === scope && option.action === action);
      state.correctionScope = selected ? selected.scope : "";
      state.correctionAction = selected ? selected.action : "";
    });
  }
  applySharedComposerToolbarStateToButtons(document, state.composerToolbarState);
}

renderSharedComposerFormattingToolbarButtons(state.composerToolbarState);
serializeRichComposerHtmlToMarkdown("");
void composerRuntime;
(async () => {
  await Promise.all([fetchTickets(), fetchLatestRerouteJob()]);
  render();
})();
