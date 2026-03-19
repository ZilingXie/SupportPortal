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
const ragRangeFilterEl = document.getElementById("rag-range-filter");
const ragSourceFilterEl = document.getElementById("rag-source-filter");
const ragStatusFilterEl = document.getElementById("rag-status-filter");
const ragQueryTypeFilterEl = document.getElementById("rag-query-type-filter");
const ragRetrievalFilterEl = document.getElementById("rag-retrieval-filter");
const ragChunkFilterEl = document.getElementById("rag-chunk-filter");
const ragPageContainers = {
  overview: {
    cards: document.getElementById("rag-overview-cards"),
    charts: document.getElementById("rag-overview-charts"),
    tables: document.getElementById("rag-overview-tables"),
  },
  ingestion: {
    cards: document.getElementById("rag-ingestion-cards"),
    charts: document.getElementById("rag-ingestion-charts"),
    tables: document.getElementById("rag-ingestion-tables"),
  },
  chunking: {
    cards: document.getElementById("rag-chunking-cards"),
    charts: document.getElementById("rag-chunking-charts"),
    tables: document.getElementById("rag-chunking-tables"),
  },
  "embedding-index": {
    cards: document.getElementById("rag-embedding-index-cards"),
    charts: document.getElementById("rag-embedding-index-charts"),
    tables: document.getElementById("rag-embedding-index-tables"),
  },
  retrieval: {
    cards: document.getElementById("rag-retrieval-cards"),
    charts: document.getElementById("rag-retrieval-charts"),
    tables: document.getElementById("rag-retrieval-tables"),
  },
  generation: {
    cards: document.getElementById("rag-generation-cards"),
    charts: document.getElementById("rag-generation-charts"),
    tables: document.getElementById("rag-generation-tables"),
  },
  handoff: {
    cards: document.getElementById("rag-handoff-cards"),
    charts: document.getElementById("rag-handoff-charts"),
    tables: document.getElementById("rag-handoff-tables"),
  },
  "performance-cost": {
    cards: document.getElementById("rag-performance-cost-cards"),
    charts: document.getElementById("rag-performance-cost-charts"),
    tables: document.getElementById("rag-performance-cost-tables"),
  },
  failures: {
    cards: document.getElementById("rag-failures-cards"),
    charts: document.getElementById("rag-failures-charts"),
    tables: document.getElementById("rag-failures-tables"),
  },
  experiments: {
    cards: document.getElementById("rag-experiments-cards"),
    charts: document.getElementById("rag-experiments-charts"),
    tables: document.getElementById("rag-experiments-tables"),
  },
};

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
let ragPageCache = {};
let ragChartInstances = {};

const knowledgeFilters = {
  status: "all",
  knowledge_type: "all",
};

const reportFilters = {
  status: "all",
  knowledge_type: "all",
};

const ragFilters = {
  range: "7d",
  source_type: "all",
  status: "all",
  query_type: "all",
  retrieval_strategy: "all",
  chunk_strategy: "all",
  limit: 20,
};

const EVAL_FIELDS = new Set([
  "retrieval_hit_at_1",
  "retrieval_hit_at_3",
  "retrieval_hit_at_5",
  "retrieval_recall_at_5",
  "mrr",
  "ndcg_at_5",
  "document_relevance_score_avg",
  "faithfulness_score_avg",
  "groundedness_score_avg",
  "response_relevance_score_avg",
  "response_completeness_score_avg",
  "citation_correctness_score_avg",
  "hallucination_rate",
  "false_positive_handoff_rate",
  "false_negative_handoff_rate",
]);

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

function optionalBooleanValue(value) {
  if (value === true) {
    return "true";
  }
  if (value === false) {
    return "false";
  }
  return "";
}

function parseOptionalBoolean(value) {
  if (value === "true") {
    return true;
  }
  if (value === "false") {
    return false;
  }
  return null;
}

function renderReviewDetailRow(row, columnCount) {
  const sampleId = escapeHtml(String(row.sample_id || ""));
  const reviewContext = row.review_context ? JSON.stringify(row.review_context, null, 2) : "{}";
  const reviewStatus = String(row.review_status || "pending");
  return `
    <tr class="rag-table-detail" id="review-detail-${sampleId}" hidden>
      <td colspan="${columnCount}">
        <div class="rag-review-detail">
          <div class="rag-review-context">
            <h4>Review Context</h4>
            <pre>${escapeHtml(reviewContext)}</pre>
          </div>
          <div class="rag-review-form">
            <label>
              <span>Status</span>
              <select data-review-status="${sampleId}">
                <option value="pending" ${reviewStatus === "pending" ? "selected" : ""}>Pending</option>
                <option value="reviewed" ${reviewStatus === "reviewed" ? "selected" : ""}>Reviewed</option>
                <option value="dismissed" ${reviewStatus === "dismissed" ? "selected" : ""}>Dismissed</option>
              </select>
            </label>
            <label>
              <span>Retrieval OK</span>
              <select data-review-retrieval="${sampleId}">
                <option value="" ${optionalBooleanValue(row.retrieval_ok) === "" ? "selected" : ""}>Unset</option>
                <option value="true" ${optionalBooleanValue(row.retrieval_ok) === "true" ? "selected" : ""}>Yes</option>
                <option value="false" ${optionalBooleanValue(row.retrieval_ok) === "false" ? "selected" : ""}>No</option>
              </select>
            </label>
            <label>
              <span>Answer OK</span>
              <select data-review-answer="${sampleId}">
                <option value="" ${optionalBooleanValue(row.answer_ok) === "" ? "selected" : ""}>Unset</option>
                <option value="true" ${optionalBooleanValue(row.answer_ok) === "true" ? "selected" : ""}>Yes</option>
                <option value="false" ${optionalBooleanValue(row.answer_ok) === "false" ? "selected" : ""}>No</option>
              </select>
            </label>
            <label>
              <span>Citation OK</span>
              <select data-review-citation="${sampleId}">
                <option value="" ${optionalBooleanValue(row.citation_ok) === "" ? "selected" : ""}>Unset</option>
                <option value="true" ${optionalBooleanValue(row.citation_ok) === "true" ? "selected" : ""}>Yes</option>
                <option value="false" ${optionalBooleanValue(row.citation_ok) === "false" ? "selected" : ""}>No</option>
              </select>
            </label>
            <label class="rag-review-note">
              <span>Note</span>
              <textarea data-review-note="${sampleId}" rows="4">${escapeHtml(String(row.note || ""))}</textarea>
            </label>
            <div class="rag-review-actions">
              <button class="rag-table-row-action" type="button" data-submit-review-sample="${sampleId}">Save Review</button>
            </div>
          </div>
        </div>
      </td>
    </tr>
  `;
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

function formatMetricValue(key, value, hasEvalData = true) {
  if (value === null || value === undefined) {
    if (EVAL_FIELDS.has(key) && !hasEvalData) {
      return "No evaluation data yet";
    }
    return "-";
  }
  if (typeof value === "boolean") {
    return value ? "Yes" : "No";
  }
  if (Array.isArray(value)) {
    return `${value.length} items`;
  }
  if (typeof value === "number") {
    if (key.endsWith("_rate") || key.includes("_score")) {
      return `${formatDecimal(value * 100, 1)}%`;
    }
    if (key.includes("_latency_ms") || key.includes("_freshness_minutes") || key.includes("_size")) {
      return formatDecimal(value, 1);
    }
    return formatDecimal(value, 2);
  }
  return String(value);
}

function buildRagQuery() {
  const query = new URLSearchParams({
    range: ragFilters.range,
    source_type: ragFilters.source_type,
    status: ragFilters.status,
    query_type: ragFilters.query_type,
    retrieval_strategy: ragFilters.retrieval_strategy,
    chunk_strategy: ragFilters.chunk_strategy,
    limit: String(ragFilters.limit),
  });
  return query.toString();
}

function chartInstanceKey(pageName, chartKey) {
  return `${pageName}:${chartKey}`;
}

function destroyPageCharts(pageName) {
  Object.keys(ragChartInstances).forEach((key) => {
    if (!key.startsWith(`${pageName}:`)) {
      return;
    }
    ragChartInstances[key]?.destroy?.();
    delete ragChartInstances[key];
  });
}

function renderRagCards(pageName, cards = {}, hasEvalData = false) {
  const container = ragPageContainers[pageName]?.cards;
  if (!container) {
    return;
  }
  const entries = Object.entries(cards || {});
  if (!entries.length) {
    container.innerHTML = `<div class="rag-empty">No card data available.</div>`;
    return;
  }
  container.innerHTML = entries
    .map(([key, value]) => {
      if (Array.isArray(value)) {
        return `
          <article class="rag-panel-card rag-kpi-card">
            <span class="metric-label">${escapeHtml(humanizeLabel(key))}</span>
            <div class="rag-token-list">
              ${value
                .slice(0, 8)
                .map((item) => `<span class="rag-token">${escapeHtml(String(item.label || item.value || item))}</span>`)
                .join("")}
            </div>
          </article>
        `;
      }
      return `
        <article class="rag-panel-card rag-kpi-card">
          <span class="metric-label">${escapeHtml(humanizeLabel(key))}</span>
          <strong class="metric-value">${escapeHtml(formatMetricValue(key, value, hasEvalData))}</strong>
        </article>
      `;
    })
    .join("");
}

function guessChartConfig(chartKey, values) {
  const firstItem = Array.isArray(values) ? values[0] : null;
  if (!firstItem || typeof firstItem !== "object") {
    return null;
  }
  if ("date" in firstItem && "value" in firstItem) {
    return {
      type: "line",
      labels: values.map((item) => item.date),
      data: values.map((item) => item.value),
      datasetLabel: humanizeLabel(chartKey),
    };
  }
  if ("label" in firstItem && "value" in firstItem) {
    return {
      type: "bar",
      labels: values.map((item) => item.label),
      data: values.map((item) => item.value),
      datasetLabel: humanizeLabel(chartKey),
    };
  }
  if ("chunk_token_count_bucket" in firstItem && "chunk_count" in firstItem) {
    return {
      type: "bar",
      labels: values.map((item) => item.chunk_token_count_bucket),
      data: values.map((item) => item.chunk_count),
      datasetLabel: humanizeLabel(chartKey),
    };
  }
  if ("doc_token_count" in firstItem && "chunk_count_per_doc" in firstItem) {
    return {
      type: "scatter",
      labels: values.map((item) => item.title || item.doc_id || ""),
      data: values.map((item) => ({ x: item.doc_token_count, y: item.chunk_count_per_doc })),
      datasetLabel: humanizeLabel(chartKey),
    };
  }
  return null;
}

function renderRagCharts(pageName, charts = {}, hasEvalData = false) {
  const container = ragPageContainers[pageName]?.charts;
  if (!container) {
    return;
  }
  destroyPageCharts(pageName);
  const entries = Object.entries(charts || {});
  if (!entries.length) {
    container.innerHTML = `<div class="rag-empty">No chart data available.</div>`;
    return;
  }
  container.innerHTML = entries
    .map(
      ([key]) => `
        <article class="rag-panel-card rag-chart-card">
          <div>
            <h3 class="rag-section-title">${escapeHtml(humanizeLabel(key))}</h3>
            <p class="rag-subtitle">${hasEvalData || !EVAL_FIELDS.has(key) ? "Live and aggregated RAG metric view." : "No evaluation data yet."}</p>
          </div>
          <canvas id="chart-${escapeHtml(pageName)}-${escapeHtml(key)}"></canvas>
        </article>
      `
    )
    .join("");
  entries.forEach(([key, value]) => {
    const canvas = document.getElementById(`chart-${pageName}-${key}`);
    const chartConfig = guessChartConfig(key, value);
    if (!canvas || !chartConfig || !window.Chart) {
      if (canvas && (!chartConfig || !Array.isArray(value) || !value.length)) {
        canvas.parentElement.insertAdjacentHTML("beforeend", `<div class="rag-empty">${EVAL_FIELDS.has(key) && !hasEvalData ? "No evaluation data yet" : "No chart data available."}</div>`);
        canvas.remove();
      }
      return;
    }
    const chart = new window.Chart(canvas.getContext("2d"), {
      type: chartConfig.type,
      data: {
        labels: chartConfig.labels,
        datasets: [
          {
            label: chartConfig.datasetLabel,
            data: chartConfig.data,
            borderColor: "#145da0",
            backgroundColor: chartConfig.type === "line" ? "rgba(20, 93, 160, 0.18)" : "rgba(20, 93, 160, 0.72)",
            tension: 0.28,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        scales: chartConfig.type === "scatter"
          ? {
              x: { title: { display: true, text: "Document Tokens" } },
              y: { title: { display: true, text: "Chunks Per Document" } },
            }
          : {},
        plugins: {
          legend: {
            display: chartConfig.type === "line" || chartConfig.type === "scatter",
          },
        },
      },
    });
    ragChartInstances[chartInstanceKey(pageName, key)] = chart;
  });
}

function renderCellValue(pageName, tableKey, columnKey, value, row) {
  if (Array.isArray(value)) {
    if (!value.length) {
      return "-";
    }
    return `<div class="rag-token-list">${value
      .slice(0, 4)
      .map((item) => `<span class="rag-token">${escapeHtml(String(item))}</span>`)
      .join("")}</div>`;
  }
  if (tableKey === "failed_tasks" && columnKey === "job_id" && value) {
    return `<button class="rag-table-row-action" data-open-ingestion="${escapeHtml(String(value))}">${escapeHtml(String(value))}</button>`;
  }
  if (tableKey === "retrieval_replay" && columnKey === "request_id" && Array.isArray(row.candidates) && row.candidates.length) {
    return `
      <button class="rag-table-row-action" data-toggle-detail="${escapeHtml(String(value))}">
        ${escapeHtml(String(value))}
      </button>
    `;
  }
  if (tableKey === "review_queue" && columnKey === "sample_id" && value) {
    return `
      <button class="rag-table-row-action" data-toggle-review-detail="${escapeHtml(String(value))}">
        ${escapeHtml(String(value))}
      </button>
    `;
  }
  if (tableKey === "review_queue" && columnKey === "review_action" && row.sample_id) {
    return `
      <button class="rag-table-row-action" data-toggle-review-detail="${escapeHtml(String(row.sample_id))}">
        ${escapeHtml(String(value))}
      </button>
    `;
  }
  if (typeof value === "boolean") {
    return value ? "Yes" : "No";
  }
  if (value && typeof value === "object") {
    return escapeHtml(JSON.stringify(value));
  }
  if (value === null || value === undefined || value === "") {
    return "-";
  }
  return escapeHtml(String(value));
}

function renderRagTables(pageName, tables = {}, hasEvalData = false) {
  const container = ragPageContainers[pageName]?.tables;
  if (!container) {
    return;
  }
  const entries = Object.entries(tables || {});
  if (!entries.length) {
    container.innerHTML = `<div class="rag-empty">No table data available.</div>`;
    return;
  }
  container.innerHTML = entries
    .map(([tableKey, rows]) => {
      const tableRows = Array.isArray(rows) ? rows : [];
      if (!tableRows.length) {
        return `
          <article class="rag-panel-card rag-table-card">
            <div>
              <h3 class="rag-section-title">${escapeHtml(humanizeLabel(tableKey))}</h3>
              <p class="rag-subtitle">${EVAL_FIELDS.has(tableKey) && !hasEvalData ? "No evaluation data yet." : "No rows available."}</p>
            </div>
            <div class="rag-empty">${EVAL_FIELDS.has(tableKey) && !hasEvalData ? "No evaluation data yet" : "No rows available."}</div>
          </article>
        `;
      }
      const columns = Object.keys(tableRows[0] || {}).filter((column) => !(tableKey === "review_queue" && column === "review_context"));
      return `
        <article class="rag-panel-card rag-table-card" data-rag-table="${escapeHtml(tableKey)}">
          <div>
            <h3 class="rag-section-title">${escapeHtml(humanizeLabel(tableKey))}</h3>
            <p class="rag-subtitle">Structured rows from the latest dashboard aggregation.</p>
          </div>
          <div class="rag-table-scroll">
            <table class="rag-generic-table">
              <thead>
                <tr>
                  ${columns.map((column) => `<th>${escapeHtml(humanizeLabel(column))}</th>`).join("")}
                </tr>
              </thead>
              <tbody>
                ${tableRows
                  .map((row) => {
                    const detailHtml =
                      tableKey === "retrieval_replay" && Array.isArray(row.candidates) && row.candidates.length
                        ? `
                          <tr class="rag-table-detail" id="detail-${escapeHtml(String(row.request_id))}" hidden>
                            <td colspan="${columns.length}">
                              <pre>${escapeHtml(JSON.stringify(row.candidates, null, 2))}</pre>
                            </td>
                          </tr>
                        `
                        : tableKey === "review_queue"
                          ? renderReviewDetailRow(row, columns.length)
                        : "";
                    return `
                      <tr>
                        ${columns
                          .map((column) => `<td>${renderCellValue(pageName, tableKey, column, row[column], row)}</td>`)
                          .join("")}
                      </tr>
                      ${detailHtml}
                    `;
                  })
                  .join("")}
              </tbody>
            </table>
          </div>
        </article>
      `;
    })
    .join("");
}

async function loadRagPage(pageName, { force = false } = {}) {
  if (!ragPageContainers[pageName]) {
    return;
  }
  if (!force && ragPageCache[pageName] && currentDashboardTab !== pageName) {
    return;
  }
  const payload = await fetchJson(`/api/dashboard/rag/${encodeURIComponent(pageName)}?${buildRagQuery()}`);
  ragPageCache[pageName] = payload;
  renderRagCards(pageName, payload.cards, payload.has_eval_data);
  renderRagCharts(pageName, payload.charts, payload.has_eval_data);
  renderRagTables(pageName, payload.tables, payload.has_eval_data);
}

async function loadAllRagPages({ force = false } = {}) {
  const pages = Object.keys(ragPageContainers);
  await Promise.all(pages.map((pageName) => loadRagPage(pageName, { force })));
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
  currentDashboardTab = String(tabName || "overview");
  dashboardTabEls.forEach((button) => {
    button.classList.toggle("active", button.dataset.dashboardTab === currentDashboardTab);
  });
  dashboardPanelEls.forEach((panel) => {
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
      } else if (ragPageContainers[tabName]) {
        loadRagPage(tabName, { force: true }).catch((error) => {
          setRealtimeStatus(`Failed to load ${tabName} dashboard data: ${error.message}`);
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
    loadAllRagPages({ force: true }),
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
        await Promise.all([loadMetrics(), loadAllRagPages({ force: true })]);
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

function bindRagFilters() {
  const bindSelect = (element, key) => {
    element?.addEventListener("change", () => {
      ragFilters[key] = element.value;
      loadAllRagPages({ force: true }).catch((error) => {
        setRealtimeStatus(`Failed to refresh RAG dashboard filters: ${error.message}`);
      });
    });
  };
  bindSelect(ragRangeFilterEl, "range");
  bindSelect(ragSourceFilterEl, "source_type");
  bindSelect(ragStatusFilterEl, "status");
  bindSelect(ragQueryTypeFilterEl, "query_type");
  bindSelect(ragRetrievalFilterEl, "retrieval_strategy");
  bindSelect(ragChunkFilterEl, "chunk_strategy");
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
  Object.values(ragPageContainers).forEach((containerSet) => {
    containerSet?.tables?.addEventListener("click", (event) => {
      const ingestionTrigger = event.target.closest("[data-open-ingestion]");
      if (ingestionTrigger) {
        openIngestionReport(ingestionTrigger.dataset.openIngestion).catch((error) => {
          setRealtimeStatus(`Failed to open ingestion report: ${error.message}`);
        });
        return;
      }
      const reviewTrigger = event.target.closest("[data-toggle-review-detail]");
      if (reviewTrigger) {
        const detailId = reviewTrigger.dataset.toggleReviewDetail;
        const detailRow = document.getElementById(`review-detail-${detailId}`);
        if (detailRow) {
          detailRow.hidden = !detailRow.hidden;
        }
        return;
      }
      const reviewSubmitTrigger = event.target.closest("[data-submit-review-sample]");
      if (reviewSubmitTrigger) {
        const sampleId = reviewSubmitTrigger.dataset.submitReviewSample;
        const detailRow = document.getElementById(`review-detail-${sampleId}`);
        if (!detailRow) {
          return;
        }
        const reviewStatus = detailRow.querySelector(`[data-review-status="${sampleId}"]`)?.value || "pending";
        const retrievalOk = parseOptionalBoolean(detailRow.querySelector(`[data-review-retrieval="${sampleId}"]`)?.value || "");
        const answerOk = parseOptionalBoolean(detailRow.querySelector(`[data-review-answer="${sampleId}"]`)?.value || "");
        const citationOk = parseOptionalBoolean(detailRow.querySelector(`[data-review-citation="${sampleId}"]`)?.value || "");
        const note = detailRow.querySelector(`[data-review-note="${sampleId}"]`)?.value || "";
        fetchJson(`/api/dashboard/rag/review-samples/${encodeURIComponent(sampleId)}`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            review_status: reviewStatus,
            retrieval_ok: retrievalOk,
            answer_ok: answerOk,
            citation_ok: citationOk,
            note,
          }),
        }).then(async () => {
          setRealtimeStatus(`Review sample ${sampleId} updated.`);
          await Promise.all([
            loadRagPage("failures", { force: true }),
            loadRagPage("overview", { force: true }),
          ]);
        }).catch((error) => {
          setRealtimeStatus(`Failed to update review sample: ${error.message}`);
        });
        return;
      }
      const detailTrigger = event.target.closest("[data-toggle-detail]");
      if (!detailTrigger) {
        return;
      }
      const detailId = detailTrigger.dataset.toggleDetail;
      const detailRow = document.getElementById(`detail-${detailId}`);
      if (!detailRow) {
        return;
      }
      detailRow.hidden = !detailRow.hidden;
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
  bindRagFilters();
  bindIngestionNavigation();
  setActiveDashboardTab("overview");
  await detectStorageModes();
  await Promise.all([
    loadMetrics(),
    loadKnowledgeMetrics(),
    loadKnowledgeIngestions(),
    loadKnowledgeReportList({ refreshSelected: false }),
    loadRecentEvents(),
    loadAllRagPages({ force: true }),
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
