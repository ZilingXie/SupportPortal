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
const reportStatusFilterEl = document.getElementById("report-status-filter");
const reportTypeFilterEl = document.getElementById("report-type-filter");
const reportIngestionListEl = document.getElementById("report-ingestion-list");
const ingestionReportDetailEl = document.getElementById("ingestion-report-detail");
const eventStreamEl = document.getElementById("event-stream");
const dashboardTabEls = Array.from(document.querySelectorAll("[data-dashboard-tab]"));
const dashboardPanelEls = Array.from(document.querySelectorAll("[data-dashboard-panel]"));

const DASHBOARD_USER = {
  username: "admin",
  role: "ADMIN",
};

const KNOWLEDGE_POLL_INTERVAL_MS = 10000;
const EVENT_STREAM_LIMIT = 20;
const REPORT_EMPTY_MESSAGE = "Select an ingestion run to view its report.";

let ticketStorageMode = "unknown";
let knowledgeStorageMode = "unknown";
let logoutLoading = false;
let socket = null;
let heartbeatTimer = null;
let reconnectTimer = null;
let knowledgePollTimer = null;
let currentDashboardTab = "overview";
let overviewIngestions = [];
let reportIngestions = [];
let selectedIngestionReportId = "";
let currentReportPayload = null;

const knowledgeFilters = {
  status: "all",
  knowledge_type: "all",
};

const reportFilters = {
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

function normalizeStringList(value) {
  if (typeof value === "string") {
    return value.trim() ? [value.trim()] : [];
  }
  if (!Array.isArray(value)) {
    return [];
  }
  return value.map((item) => String(item || "").trim()).filter(Boolean);
}

function normalizeLinkList(value) {
  if (!Array.isArray(value)) {
    return [];
  }
  return value
    .map((item) => {
      if (typeof item === "string") {
        const url = sanitizeHttpUrl(item);
        return url ? { label: url, url } : null;
      }
      if (!item || typeof item !== "object") {
        return null;
      }
      const url = sanitizeHttpUrl(item.url);
      if (!url) {
        return null;
      }
      return {
        label: String(item.label || url).trim(),
        url,
      };
    })
    .filter(Boolean);
}

function formatDisplayValue(value) {
  if (value === null || value === undefined) {
    return "-";
  }
  if (typeof value === "boolean") {
    return value ? "Yes" : "No";
  }
  if (typeof value === "number") {
    return formatDecimal(value, 2);
  }
  const normalized = String(value).trim();
  return normalized || "-";
}

function humanizeLabel(value) {
  const normalized = String(value || "").trim();
  if (!normalized) {
    return "-";
  }
  return normalized
    .replaceAll("_", " ")
    .replace(/\b\w/g, (match) => match.toUpperCase());
}

function userFacingSourceType(value, fallback = "-") {
  const normalized = String(value || "").trim();
  if (!normalized) {
    return fallback;
  }
  if (normalized === "official_markdown_upload") {
    return "Official Markdown Upload";
  }
  if (normalized === "technical_article_api") {
    return "Technical Article API";
  }
  return humanizeLabel(normalized);
}

function renderTokenList(values, emptyLabel = "-") {
  const items = normalizeStringList(values);
  if (!items.length) {
    return `<p class="report-muted">${escapeHtml(emptyLabel)}</p>`;
  }
  return `
    <div class="report-token-list">
      ${items.map((item) => `<span class="report-token">${escapeHtml(item)}</span>`).join("")}
    </div>
  `;
}

function renderLinkList(values) {
  const links = normalizeLinkList(values);
  if (!links.length) {
    return `<p class="report-muted">-</p>`;
  }
  return `
    <ul class="report-list report-links">
      ${links
        .map(
          (item) =>
            `<li><a href="${escapeHtml(item.url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(
              item.label
            )}</a></li>`
        )
        .join("")}
    </ul>
  `;
}

function renderDefinitionGrid(items) {
  const normalizedItems = items.filter((item) => item && item.label);
  if (!normalizedItems.length) {
    return `<p class="report-muted">-</p>`;
  }
  return `
    <div class="report-grid">
      ${normalizedItems
        .map(
          (item) => `
            <div class="report-grid-item">
              <span class="report-grid-label">${escapeHtml(item.label)}</span>
              <strong class="report-grid-value">${escapeHtml(formatDisplayValue(item.value))}</strong>
            </div>
          `
        )
        .join("")}
    </div>
  `;
}

function renderCountList(record) {
  if (!record || typeof record !== "object") {
    return `<p class="report-muted">-</p>`;
  }
  const entries = Object.entries(record).filter(([, value]) => Number(value || 0) > 0);
  if (!entries.length) {
    return `<p class="report-muted">-</p>`;
  }
  return `
    <ul class="report-list">
      ${entries
        .map(
          ([key, value]) =>
            `<li><strong>${escapeHtml(humanizeLabel(key))}</strong><span>${escapeHtml(
              formatDisplayValue(value)
            )}</span></li>`
        )
        .join("")}
    </ul>
  `;
}

function renderPreviewList(items, emptyLabel = "-") {
  if (!Array.isArray(items) || !items.length) {
    return `<p class="report-muted">${escapeHtml(emptyLabel)}</p>`;
  }
  return `
    <div class="report-preview-list">
      ${items
        .map((item) => {
          const heading = item.h3 || item.h2 || item.section_type || item.heading || "Section";
          const preview = String(item.preview || item.text || "").trim();
          return `
            <article class="report-preview-item">
              <h4>${escapeHtml(humanizeLabel(heading))}</h4>
              <p>${escapeHtml(preview || "-")}</p>
            </article>
          `;
        })
        .join("")}
    </div>
  `;
}

function renderWarningList(warnings, errorMessage) {
  const items = normalizeStringList(warnings);
  const normalizedError = String(errorMessage || "").trim();
  if (normalizedError && !items.includes(normalizedError)) {
    items.push(normalizedError);
  }
  if (!items.length) {
    return `<p class="report-muted">No warnings or errors recorded.</p>`;
  }
  return `
    <ul class="report-list report-warnings">
      ${items.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}
    </ul>
  `;
}

function renderRulesList(values, emptyLabel = "-") {
  const items = normalizeStringList(values);
  if (!items.length) {
    return `<p class="report-muted">${escapeHtml(emptyLabel)}</p>`;
  }
  return `
    <ul class="report-list">
      ${items.map((item) => `<li>${escapeHtml(humanizeLabel(item))}</li>`).join("")}
    </ul>
  `;
}

function warningCountFor(ingestion) {
  return Number(ingestion?.cleaning_report_summary?.warning_count || 0);
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
  overviewIngestions = Array.isArray(ingestions) ? ingestions : [];
  if (!overviewIngestions.length) {
    knowledgeIngestionsBodyEl.innerHTML = `
      <tr>
        <td colspan="7" class="empty-state">No knowledge ingestions found for the selected filters.</td>
      </tr>
    `;
    return;
  }

  knowledgeIngestionsBodyEl.innerHTML = overviewIngestions
    .map((ingestion) => {
      const title =
        String(ingestion?.title || "").trim()
        || String(ingestion?.file_name || "").trim()
        || String(ingestion?.ingestion_id || "").trim();
      const sourceType = userFacingSourceType(ingestion?.source_type, ingestion?.entry_type || "-");
      const knowledgeType = humanizeLabel(ingestion?.knowledge_type || "-");
      const normalizationStatus = humanizeLabel(ingestion?.normalization_status || "-");
      const parserSummary = ingestion?.parser_name
        ? `${humanizeLabel(ingestion.parser_name)}${ingestion?.parser_version ? ` (${ingestion.parser_version})` : ""}`
        : "-";
      const warningCount = warningCountFor(ingestion);
      const dedupeAction = ingestion?.dedupe_action ? humanizeLabel(ingestion.dedupe_action) : "";
      const errorMessage = String(ingestion?.error_message || "").trim();
      const ingestionId = String(ingestion?.ingestion_id || "").trim();

      return `
        <tr class="interactive-row" data-ingestion-id="${escapeHtml(ingestionId)}">
          <td>
            <p class="ingestion-title">${escapeHtml(title)}</p>
            <p class="ingestion-meta">${escapeHtml(ingestionId || "-")} · ${escapeHtml(knowledgeType)} · ${escapeHtml(sourceType)}</p>
            <p class="ingestion-meta">Normalization: ${escapeHtml(normalizationStatus)} · Parser: ${escapeHtml(parserSummary)}</p>
            ${warningCount ? `<p class="ingestion-warning">Warnings: ${escapeHtml(String(warningCount))}</p>` : ""}
            ${dedupeAction ? `<p class="ingestion-meta">Dedupe: ${escapeHtml(dedupeAction)}</p>` : ""}
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

function renderReportListEmpty(message) {
  reportIngestionListEl.innerHTML = `<div class="empty-state report-list-empty">${escapeHtml(message)}</div>`;
}

function renderReportDetailEmpty(message) {
  ingestionReportDetailEl.innerHTML = `<div class="empty-state report-detail-empty">${escapeHtml(message)}</div>`;
}

function renderReportIngestionList(ingestions) {
  reportIngestions = Array.isArray(ingestions) ? ingestions : [];
  if (!reportIngestions.length) {
    renderReportListEmpty("No ingestion reports found for the selected filters.");
    return;
  }
  reportIngestionListEl.innerHTML = reportIngestions
    .map((ingestion) => {
      const ingestionId = String(ingestion?.ingestion_id || "").trim();
      const title =
        String(ingestion?.title || "").trim()
        || String(ingestion?.file_name || "").trim()
        || ingestionId;
      const isActive = ingestionId && ingestionId === selectedIngestionReportId;
      const warningCount = warningCountFor(ingestion);
      const dedupeAction = ingestion?.dedupe_action ? humanizeLabel(ingestion.dedupe_action) : "New Document";
      return `
        <button
          type="button"
          class="report-ingestion-item${isActive ? " active" : ""}"
          data-ingestion-id="${escapeHtml(ingestionId)}"
        >
          <div class="report-ingestion-header">
            <span class="report-ingestion-title">${escapeHtml(title)}</span>
            ${renderStatusPill(ingestion.status)}
          </div>
          <div class="report-ingestion-meta">${escapeHtml(ingestionId)} · ${escapeHtml(
            humanizeLabel(ingestion.knowledge_type || "-")
          )}</div>
          <div class="report-ingestion-meta">${escapeHtml(userFacingSourceType(ingestion.source_type))}</div>
          <div class="report-ingestion-meta">Chunks: ${escapeHtml(formatDisplayValue(ingestion.chunk_count))} · ${escapeHtml(
            dedupeAction
          )}</div>
          <div class="report-ingestion-meta">Warnings: ${escapeHtml(String(warningCount))}</div>
        </button>
      `;
    })
    .join("");
}

function renderIngestionReport(report) {
  const ingestion = report?.ingestion || {};
  const summary = report?.summary || {};
  const cleaningReport = report?.cleaning_report || {};
  const metadata = report?.metadata || {};
  const normalizedSummary = report?.normalized_summary || {};
  const chunkHandoff = report?.chunk_handoff || {};
  const warnings = Array.isArray(report?.warnings) ? report.warnings : [];

  const runSummary = renderDefinitionGrid([
    { label: "Ingestion ID", value: summary.ingestion_id },
    { label: "Status", value: summary.status },
    { label: "Normalization", value: summary.normalization_status },
    { label: "Knowledge Type", value: humanizeLabel(summary.knowledge_type) },
    { label: "Source Type", value: userFacingSourceType(summary.source_type) },
    { label: "Document ID", value: summary.document_id },
    { label: "Chunk Count", value: summary.chunk_count },
    { label: "Duration", value: formatDuration(summary.duration_seconds) },
    { label: "Dedupe Action", value: humanizeLabel(summary.dedupe_action) },
    { label: "Dedupe Target", value: summary.dedupe_target_doc_id },
    { label: "Created", value: formatDateTime(summary.created_at) },
    { label: "Finished", value: formatDateTime(summary.finished_at) },
  ]);

  const parserSection = renderDefinitionGrid([
    { label: "Parser", value: summary.parser_name || cleaningReport.parser_name },
    { label: "Parser Version", value: summary.parser_version || cleaningReport.parser_version },
    { label: "Template Detected", value: cleaningReport.template_detected },
    { label: "Source Hash", value: cleaningReport.source_hash },
    { label: "Processed At", value: formatDateTime(cleaningReport.processed_at) },
  ]);

  const metadataSection = renderDefinitionGrid([
    { label: "Title", value: metadata.title || ingestion.title },
    { label: "URL", value: metadata.url || ingestion.source_url },
    { label: "Language", value: metadata.language },
    { label: "Product", value: metadata.product || metadata.product_area },
    { label: "Module", value: metadata.module },
    { label: "Platform", value: metadata.platform || metadata.platform_sdk },
    { label: "Metadata Source", value: metadata.metadata_source },
    { label: "Metadata Model", value: metadata.metadata_model },
    { label: "Metadata Generated", value: formatDateTime(metadata.metadata_generated_at) },
    { label: "Metadata Version", value: metadata.metadata_version },
  ]);

  const normalizedSection = renderDefinitionGrid([
    { label: "Source Path", value: normalizedSummary.source_path },
    { label: "Source Updated", value: formatDateTime(normalizedSummary.source_updated_at) },
    { label: "Section Count", value: normalizedSummary.section_count },
    { label: "Block Count", value: normalizedSummary.block_count },
    { label: "Language", value: normalizedSummary.language },
    { label: "Product", value: normalizedSummary.product },
    { label: "Module", value: normalizedSummary.module },
  ]);

  const chunkSection = renderDefinitionGrid([
    { label: "Mode", value: humanizeLabel(chunkHandoff.mode) },
    { label: "Content Blocks", value: chunkHandoff.content_block_count },
    { label: "Chunk Count", value: chunkHandoff.chunk_count },
  ]);

  ingestionReportDetailEl.innerHTML = `
    <section class="report-section">
      <div class="report-section-header">
        <div>
          <h3>Run Summary</h3>
          <p class="section-subtitle">${escapeHtml(String(ingestion?.title || summary?.title || "Untitled Ingestion"))}</p>
        </div>
        ${renderStatusPill(summary.status)}
      </div>
      ${runSummary}
    </section>

    <section class="report-section">
      <div class="report-section-header">
        <div>
          <h3>Parser &amp; Cleaning</h3>
          <p class="section-subtitle">Template detection, applied rules, and cleanup notes.</p>
        </div>
      </div>
      ${parserSection}
      <div class="report-subsection">
        <h4>Rules Applied</h4>
        ${renderRulesList(cleaningReport.rules_applied)}
      </div>
      <div class="report-subsection">
        <h4>Removed Noise</h4>
        ${renderRulesList(cleaningReport.removed_noise)}
      </div>
      ${Array.isArray(cleaningReport.missing_sections) && cleaningReport.missing_sections.length
        ? `
          <div class="report-subsection">
            <h4>Missing Sections</h4>
            ${renderRulesList(cleaningReport.missing_sections)}
          </div>
        `
        : ""}
    </section>

    <section class="report-section">
      <div class="report-section-header">
        <div>
          <h3>Metadata</h3>
          <p class="section-subtitle">Rule-first metadata with optional LLM enrichment.</p>
        </div>
      </div>
      ${metadataSection}
      <div class="report-subsection">
        <h4>Tags</h4>
        ${renderTokenList(metadata.tags)}
      </div>
      <div class="report-subsection">
        <h4>Capabilities / Symptoms</h4>
        ${renderTokenList(metadata.capabilities || metadata.symptoms)}
      </div>
      <div class="report-subsection">
        <h4>Reference Links</h4>
        ${renderLinkList(metadata.reference_links)}
      </div>
    </section>

    <section class="report-section">
      <div class="report-section-header">
        <div>
          <h3>Normalized Document Summary</h3>
          <p class="section-subtitle">Structured document snapshot used as the chunking input.</p>
        </div>
      </div>
      ${normalizedSection}
      <div class="report-subsection">
        <h4>Block Counts by Type</h4>
        ${renderCountList(normalizedSummary.block_counts_by_type)}
      </div>
      <div class="report-subsection">
        <h4>Section Preview</h4>
        ${renderPreviewList(normalizedSummary.sections)}
      </div>
    </section>

    <section class="report-section">
      <div class="report-section-header">
        <div>
          <h3>Chunk Handoff</h3>
          <p class="section-subtitle">What was sent into vectorization after normalization.</p>
        </div>
      </div>
      ${chunkSection}
      <div class="report-subsection">
        <h4>Section to Chunk Counts</h4>
        ${renderCountList(chunkHandoff.section_to_chunk_counts)}
      </div>
      <div class="report-subsection">
        <h4>Chunk Preview</h4>
        ${renderPreviewList(chunkHandoff.chunks, "No chunks generated.")}
      </div>
    </section>

    <section class="report-section">
      <div class="report-section-header">
        <div>
          <h3>Warnings / Errors</h3>
          <p class="section-subtitle">Anything the cleaner, parser, or ingestion run flagged.</p>
        </div>
      </div>
      ${renderWarningList(warnings, ingestion.error_message)}
    </section>

    <section class="report-section">
      <details class="raw-json-panel">
        <summary>Raw JSON</summary>
        <pre class="report-json">${escapeHtml(JSON.stringify(report.raw || report, null, 2))}</pre>
      </details>
    </section>
  `;
}

async function loadKnowledgeIngestionReport(ingestionId) {
  const normalizedId = String(ingestionId || "").trim();
  if (!normalizedId) {
    selectedIngestionReportId = "";
    currentReportPayload = null;
    renderReportDetailEmpty(REPORT_EMPTY_MESSAGE);
    return;
  }
  selectedIngestionReportId = normalizedId;
  renderReportIngestionList(reportIngestions);
  renderReportDetailEmpty("Loading ingestion report...");
  const payload = await fetchJson(`/api/dashboard/knowledge-ingestions/${encodeURIComponent(normalizedId)}/report`);
  currentReportPayload = payload;
  selectedIngestionReportId = String(payload?.ingestion?.ingestion_id || normalizedId);
  renderReportIngestionList(reportIngestions);
  renderIngestionReport(payload);
}

async function loadKnowledgeReportList({ refreshSelected = false } = {}) {
  const query = new URLSearchParams({
    limit: "50",
    status: reportFilters.status,
    knowledge_type: reportFilters.knowledge_type,
  });
  const payload = await fetchJson(`/api/dashboard/knowledge-ingestions?${query.toString()}`);
  const ingestions = Array.isArray(payload?.ingestions) ? payload.ingestions : [];
  reportIngestions = ingestions;

  if (!reportIngestions.length) {
    selectedIngestionReportId = "";
    currentReportPayload = null;
    renderReportIngestionList([]);
    renderReportDetailEmpty("No ingestion reports found for the selected filters.");
    return;
  }

  const selectionExists = reportIngestions.some(
    (ingestion) => String(ingestion?.ingestion_id || "").trim() === selectedIngestionReportId
  );
  if (!selectionExists) {
    selectedIngestionReportId = String(reportIngestions[0]?.ingestion_id || "").trim();
  }
  renderReportIngestionList(reportIngestions);
  if (
    selectedIngestionReportId
    && (!currentReportPayload
      || String(currentReportPayload?.ingestion?.ingestion_id || "").trim() !== selectedIngestionReportId
      || refreshSelected)
  ) {
    await loadKnowledgeIngestionReport(selectedIngestionReportId);
  }
}

function setActiveDashboardTab(tabName) {
  currentDashboardTab = tabName === "reports" ? "reports" : "overview";
  dashboardTabEls.forEach((button) => {
    button.classList.toggle("active", button.dataset.dashboardTab === currentDashboardTab);
  });
  dashboardPanels.forEach((panel) => {
    panel.classList.toggle("active", panel.dataset.dashboardPanel === currentDashboardTab);
  });
}

async function openIngestionReport(ingestionId) {
  const normalizedId = String(ingestionId || "").trim();
  if (!normalizedId) {
    return;
  }
  setActiveDashboardTab("reports");
  selectedIngestionReportId = normalizedId;
  const inCurrentList = reportIngestions.some(
    (ingestion) => String(ingestion?.ingestion_id || "").trim() === normalizedId
  );
  if (!inCurrentList && (reportFilters.status !== "all" || reportFilters.knowledge_type !== "all")) {
    reportFilters.status = "all";
    reportFilters.knowledge_type = "all";
    if (reportStatusFilterEl) {
      reportStatusFilterEl.value = "all";
    }
    if (reportTypeFilterEl) {
      reportTypeFilterEl.value = "all";
    }
    await loadKnowledgeReportList({ refreshSelected: false });
  }
  await loadKnowledgeIngestionReport(normalizedId);
}

function bindDashboardTabs() {
  dashboardTabEls.forEach((button) => {
    button.addEventListener("click", () => {
      const tabName = String(button.dataset.dashboardTab || "overview");
      setActiveDashboardTab(tabName);
      if (tabName === "reports" && selectedIngestionReportId) {
        loadKnowledgeIngestionReport(selectedIngestionReportId).catch((error) => {
          setRealtimeStatus(`Failed to load ingestion report: ${error.message}`);
        });
      }
    });
  });
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
    sourceType: String(event?.source_type || "").trim(),
    dedupeAction: String(event?.dedupe_action || "").trim(),
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
  if (normalized.ingestionId) {
    classNames.push("interactive");
    item.dataset.ingestionId = normalized.ingestionId;
  }
  item.className = classNames.join(" ");

  const identityText = normalized.ingestionId
    ? `Ingestion ${normalized.ingestionId}`
    : `Ticket ${normalized.ticketId || "-"}`;
  const secondaryMessage = normalized.title
    ? escapeHtml(normalized.title)
    : escapeHtml(normalized.message || normalized.status || "Update received");
  const extraMeta = [normalized.sourceType ? userFacingSourceType(normalized.sourceType) : "", normalized.dedupeAction ? humanizeLabel(normalized.dedupeAction) : ""]
    .filter(Boolean)
    .join(" · ");

  item.innerHTML = `
    <div class="event-title">
      <strong>${escapeHtml(normalized.event)}</strong>
      <span class="event-identity">${escapeHtml(identityText)}</span>
    </div>
    <div>${secondaryMessage}</div>
    ${normalized.title && normalized.message ? `<div class="event-meta">${escapeHtml(normalized.message)}</div>` : ""}
    ${extraMeta ? `<div class="event-meta">${escapeHtml(extraMeta)}</div>` : ""}
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

async function refreshKnowledgeViews({ refreshReportDetail = false } = {}) {
  await Promise.all([
    loadKnowledgeMetrics(),
    loadKnowledgeIngestions(),
    loadKnowledgeReportList({ refreshSelected: refreshReportDetail }),
  ]);
}

function startKnowledgePolling() {
  if (knowledgePollTimer) {
    clearInterval(knowledgePollTimer);
  }
  knowledgePollTimer = setInterval(() => {
    refreshKnowledgeViews({ refreshReportDetail: currentDashboardTab === "reports" }).catch((error) => {
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
        await refreshKnowledgeViews({
          refreshReportDetail:
            currentDashboardTab === "reports"
            || String(payload?.ingestion_id || "").trim() === selectedIngestionReportId,
        });
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

function bindReportFilters() {
  reportStatusFilterEl?.addEventListener("change", () => {
    reportFilters.status = reportStatusFilterEl.value;
    loadKnowledgeReportList({ refreshSelected: false }).catch((error) => {
      setRealtimeStatus(`Failed to filter ingestion reports: ${error.message}`);
    });
  });
  reportTypeFilterEl?.addEventListener("change", () => {
    reportFilters.knowledge_type = reportTypeFilterEl.value;
    loadKnowledgeReportList({ refreshSelected: false }).catch((error) => {
      setRealtimeStatus(`Failed to filter ingestion reports: ${error.message}`);
    });
  });
}

function bindIngestionNavigation() {
  knowledgeIngestionsBodyEl?.addEventListener("click", (event) => {
    const row = event.target.closest("[data-ingestion-id]");
    if (!row) {
      return;
    }
    openIngestionReport(row.dataset.ingestionId).catch((error) => {
      setRealtimeStatus(`Failed to open ingestion report: ${error.message}`);
    });
  });
  reportIngestionListEl?.addEventListener("click", (event) => {
    const button = event.target.closest("[data-ingestion-id]");
    if (!button) {
      return;
    }
    openIngestionReport(button.dataset.ingestionId).catch((error) => {
      setRealtimeStatus(`Failed to open ingestion report: ${error.message}`);
    });
  });
  eventStreamEl?.addEventListener("click", (event) => {
    const item = event.target.closest("[data-ingestion-id]");
    if (!item) {
      return;
    }
    openIngestionReport(item.dataset.ingestionId).catch((error) => {
      setRealtimeStatus(`Failed to open ingestion report: ${error.message}`);
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
  bindDashboardTabs();
  bindKnowledgeFilters();
  bindReportFilters();
  bindIngestionNavigation();
  setActiveDashboardTab("overview");
  await detectStorageModes();
  await Promise.all([
    loadMetrics(),
    loadKnowledgeMetrics(),
    loadKnowledgeIngestions(),
    loadKnowledgeReportList({ refreshSelected: false }),
    loadRecentEvents(),
  ]);
  setRealtimeStatus("Realtime: connecting...");
  startKnowledgePolling();
  setupWebSocket();
}

initializeDashboard().catch((error) => {
  startKnowledgePolling();
  setRealtimeStatus(`Failed to load dashboard: ${error.message}`);
  renderReportDetailEmpty(REPORT_EMPTY_MESSAGE);
});
