const dashboardStatusEl = document.getElementById("dashboard-status");
const activeScopeLabelEl = document.getElementById("active-scope-label");
const lastRefreshedLabelEl = document.getElementById("last-refreshed-label");
const refreshButtonEl = document.getElementById("refresh-button");
const dashboardTabEls = Array.from(document.querySelectorAll("[data-dashboard-tab]"));
const dashboardPanelEls = Array.from(document.querySelectorAll("[data-dashboard-panel]"));

const ragRangeFilterEl = document.getElementById("rag-range-filter");
const ragSourceFilterEl = document.getElementById("rag-source-filter");
const ragQueryTypeFilterEl = document.getElementById("rag-query-type-filter");
const ragRetrievalFilterEl = document.getElementById("rag-retrieval-filter");
const ragChunkFilterEl = document.getElementById("rag-chunk-filter");
const ragProductFilterEl = document.getElementById("rag-product-filter");
const ragLanguageFilterEl = document.getElementById("rag-language-filter");
const ragExperimentFilterEl = document.getElementById("rag-experiment-filter");

const reportDrawerEl = document.getElementById("report-drawer");
const reportDrawerTitleEl = document.getElementById("report-drawer-title");
const reportDrawerBodyEl = document.getElementById("report-drawer-body");

const ragPageContainers = {
  "experiments": { root: document.getElementById("rag-experiments-page") },
  "diagnosis": { root: document.getElementById("rag-diagnosis-page") },
  "knowledge-supply": { root: document.getElementById("rag-knowledge-supply-page") },
  "production-signals": { root: document.getElementById("rag-production-signals-page") },
  "review": { root: document.getElementById("rag-review-page") },
};

const PAGE_LABELS = {
  experiments: "Experiments",
  diagnosis: "Diagnosis",
  "knowledge-supply": "Knowledge Supply",
  "production-signals": "Production Signals",
  review: "Review Queue",
};

let currentDashboardTab = "experiments";
let loadToken = 0;
const pageCache = {};

const ragFilters = {
  range: "7d",
  source_type: "all",
  product: "all",
  language: "all",
  query_type: "all",
  retrieval_strategy: "all",
  chunk_strategy: "all",
  experiment_id: "all",
  sample_id: "",
  request_id: "",
  eval_run_id: "",
  test_case_id: "",
  baseline_experiment_id: "",
  candidate_experiment_id: "",
  limit: 20,
};

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function normalizeString(value) {
  return String(value ?? "").trim();
}

function normalizeStringList(value) {
  if (typeof value === "string") {
    return value.trim() ? [value.trim()] : [];
  }
  if (!Array.isArray(value)) {
    return [];
  }
  return value.map((item) => normalizeString(item)).filter(Boolean);
}

function humanizeLabel(value) {
  const normalized = normalizeString(value);
  if (!normalized) {
    return "-";
  }
  return normalized
    .replaceAll("_", " ")
    .replace(/\b\w/g, (match) => match.toUpperCase());
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

function formatMetricValue(value, key = "") {
  if (value === null || value === undefined || value === "") {
    return "-";
  }
  if (typeof value === "boolean") {
    return value ? "Yes" : "No";
  }
  if (Array.isArray(value)) {
    return value.length ? `${value.length} items` : "-";
  }
  if (typeof value === "number") {
    const normalizedKey = normalizeString(key).toLowerCase();
    if (
      normalizedKey.includes("rate") ||
      normalizedKey.includes("score") ||
      normalizedKey.startsWith("hit@") ||
      normalizedKey.startsWith("hit_")
    ) {
      return `${formatDecimal(value * 100, 1)}%`;
    }
    if (
      normalizedKey.includes("latency") ||
      normalizedKey.includes("freshness") ||
      normalizedKey.includes("cost")
    ) {
      return formatDecimal(value, 2);
    }
    return formatDecimal(value, 2);
  }
  if (typeof value === "object") {
    return escapeHtml(JSON.stringify(value));
  }
  return escapeHtml(String(value));
}

function buildChipList(values, tone = "neutral") {
  const items = normalizeStringList(values);
  if (!items.length) {
    return `<span class="chip chip-${tone}">none</span>`;
  }
  return items.map((item) => `<span class="chip chip-${tone}">${escapeHtml(item)}</span>`).join("");
}

function buildMetricCards(cards) {
  const entries = Object.entries(cards || {}).filter(([, value]) => value !== null && value !== undefined && value !== "");
  if (!entries.length) {
    return `<div class="empty-state">No metrics available in this scope.</div>`;
  }
  return `
    <div class="metric-grid">
      ${entries
        .map(
          ([key, value]) => `
            <article class="metric-card">
              <span class="metric-label">${escapeHtml(humanizeLabel(key))}</span>
              <strong class="metric-value">${formatMetricValue(value, key)}</strong>
            </article>
          `
        )
        .join("")}
    </div>
  `;
}

function buildDefinitionGrid(items) {
  const normalizedItems = (items || []).filter((item) => item && item.label);
  if (!normalizedItems.length) {
    return `<div class="empty-state">No details available.</div>`;
  }
  return `
    <div class="definition-grid">
      ${normalizedItems
        .map(
          (item) => `
            <div class="definition-item">
              <span class="definition-label">${escapeHtml(item.label)}</span>
              <strong class="definition-value">${formatMetricValue(item.value, item.label)}</strong>
            </div>
          `
        )
        .join("")}
    </div>
  `;
}

function extractColumns(rows) {
  const firstRow = rows.find((item) => item && typeof item === "object");
  if (!firstRow) {
    return [];
  }
  return Object.keys(firstRow).filter((key) => !["trace_payload", "review_context"].includes(key));
}

function buildTable(rows, options = {}) {
  const normalizedRows = Array.isArray(rows) ? rows : [];
  if (!normalizedRows.length) {
    return `<div class="empty-state">${escapeHtml(options.emptyLabel || "No rows available.")}</div>`;
  }
  const columns = options.columns || extractColumns(normalizedRows);
  return `
    <div class="table-scroll">
      <table class="data-table">
        <thead>
          <tr>
            ${columns.map((column) => `<th>${escapeHtml(humanizeLabel(column))}</th>`).join("")}
          </tr>
        </thead>
        <tbody>
          ${normalizedRows
            .map(
              (row) => `
                <tr>
                  ${columns
                    .map((column) => {
                      const value = row[column];
                      if (Array.isArray(value)) {
                        return `<td>${buildChipList(value)}</td>`;
                      }
                      if (typeof value === "object" && value !== null) {
                        return `<td><code class="inline-code">${escapeHtml(JSON.stringify(value))}</code></td>`;
                      }
                      return `<td>${formatMetricValue(value, column)}</td>`;
                    })
                    .join("")}
                </tr>
              `
            )
            .join("")}
        </tbody>
      </table>
    </div>
  `;
}

function buildTableSection(title, rows, options = {}) {
  return `
    <section class="panel-card">
      <div class="panel-header">
        <div>
          <h3>${escapeHtml(title)}</h3>
          ${options.subtitle ? `<p>${escapeHtml(options.subtitle)}</p>` : ""}
        </div>
      </div>
      ${buildTable(rows, options)}
    </section>
  `;
}

function buildDistribution(name, values) {
  const items = Array.isArray(values) ? values : [];
  if (!items.length) {
    return "";
  }
  const normalized = items.slice(0, 12).map((item) => {
    if (item && typeof item === "object") {
      const label = normalizeString(item.label || item.chunk_token_count_bucket || item.segment || item.date || item.metric);
      const value = Number(item.value ?? item.chunk_count ?? item.case_count ?? 0);
      return { label: label || "-", value: Number.isFinite(value) ? value : 0 };
    }
    return { label: normalizeString(item) || "-", value: 0 };
  });
  const maxValue = Math.max(...normalized.map((item) => item.value), 1);
  return `
    <section class="panel-card">
      <div class="panel-header">
        <div>
          <h3>${escapeHtml(humanizeLabel(name))}</h3>
          <p>High-signal breakdown for the current scope.</p>
        </div>
      </div>
      <div class="distribution-list">
        ${normalized
          .map(
            (item) => `
              <div class="distribution-row">
                <span class="distribution-label">${escapeHtml(item.label)}</span>
                <div class="distribution-bar">
                  <span style="width:${Math.max(6, (item.value / maxValue) * 100)}%"></span>
                </div>
                <strong class="distribution-value">${escapeHtml(formatMetricValue(item.value, name))}</strong>
              </div>
            `
          )
          .join("")}
      </div>
    </section>
  `;
}

function buildGroupBlock(group) {
  const charts = Object.entries(group.charts || {})
    .map(([key, value]) => buildDistribution(key, value))
    .join("");
  const tables = Object.entries(group.tables || {})
    .map(([key, value]) => buildTableSection(humanizeLabel(key), value))
    .join("");
  return `
    <section class="panel-card section-stack">
      <div class="panel-header">
        <div>
          <h3>${escapeHtml(group.title || "Section")}</h3>
          <p>${escapeHtml(group.subtitle || "Relevant detail for tuning and diagnosis.")}</p>
        </div>
      </div>
      ${buildMetricCards(group.cards || {})}
      ${charts}
      ${tables}
    </section>
  `;
}

function buildExperimentOption(option) {
  const label = option?.label || option?.experiment_id || option?.eval_run_id || "Experiment";
  const meta = option?.finished_at ? ` · ${formatDateTime(option.finished_at)}` : "";
  return `<option value="${escapeHtml(option.experiment_id || option.eval_run_id || "")}">${escapeHtml(label + meta)}</option>`;
}

function buildSampleAction(item) {
  if (item.sample_source === "live_query") {
    return `
      <button
        type="button"
        class="table-action-button"
        data-open-diagnosis-live="${escapeHtml(item.request_id || "")}"
      >
        Inspect
      </button>
    `;
  }
  return `
    <button
      type="button"
      class="table-action-button"
      data-open-diagnosis-benchmark="${escapeHtml(item.eval_run_id || "")}"
      data-open-test-case="${escapeHtml(item.test_case_id || "")}"
    >
      Inspect
    </button>
  `;
}

function buildSampleList(title, items, tone = "neutral") {
  const rows = Array.isArray(items) ? items : [];
  return `
    <section class="panel-card">
      <div class="panel-header">
        <div>
          <h3>${escapeHtml(title)}</h3>
          <p>Direct entry points into high-value cases.</p>
        </div>
      </div>
      ${
        rows.length
          ? `<div class="sample-list">
              ${rows
                .map(
                  (item) => `
                    <article class="sample-item">
                      <div class="sample-item-header">
                        <span class="chip chip-${tone}">${escapeHtml(item.sample_source || "benchmark")}</span>
                        ${buildChipList(item.root_cause_labels || [], tone)}
                      </div>
                      <h4>${escapeHtml(item.question || item.sample_question || item.user_query || "Untitled sample")}</h4>
                      <div class="sample-meta">
                        <span>${escapeHtml(humanizeLabel(item.query_type || "unknown"))}</span>
                        <span>${escapeHtml(humanizeLabel(item.source_type || "unknown"))}</span>
                        ${
                          item.delta_quality_score !== undefined
                            ? `<span>${escapeHtml(formatMetricValue(item.delta_quality_score, "score"))} delta</span>`
                            : ""
                        }
                        ${
                          item.created_at
                            ? `<span>${escapeHtml(formatDateTime(item.created_at))}</span>`
                            : ""
                        }
                      </div>
                      <div class="sample-item-actions">
                        ${buildSampleAction(item)}
                      </div>
                    </article>
                  `
                )
                .join("")}
            </div>`
          : `<div class="empty-state">No samples available in this scope.</div>`
      }
    </section>
  `;
}

function buildIngestionTaskList(title, rows) {
  const items = Array.isArray(rows) ? rows : [];
  return `
    <section class="panel-card">
      <div class="panel-header">
        <div>
          <h3>${escapeHtml(title)}</h3>
          <p>Open the raw ingestion report for failed or suspicious jobs.</p>
        </div>
      </div>
      ${
        items.length
          ? `<div class="sample-list">
              ${items
                .map(
                  (row) => `
                    <article class="sample-item">
                      <div class="sample-item-header">
                        <span class="chip chip-danger">${escapeHtml(row.failed_stage || row.status || "report")}</span>
                      </div>
                      <h4>${escapeHtml(row.doc_id || row.job_id || "Ingestion job")}</h4>
                      <div class="sample-meta">
                        <span>${escapeHtml(row.source_type || "-")}</span>
                        <span>${escapeHtml(row.error_code || "-")}</span>
                        <span>${escapeHtml(formatDateTime(row.updated_at || row.created_at || ""))}</span>
                      </div>
                      <p>${escapeHtml(row.error_message || row.source_url || "")}</p>
                      <div class="sample-item-actions">
                        <button type="button" class="table-action-button" data-open-report="${escapeHtml(row.job_id || "")}">
                          Open Report
                        </button>
                      </div>
                    </article>
                  `
                )
                .join("")}
            </div>`
          : `<div class="empty-state">No ingestion tasks available in this scope.</div>`
      }
    </section>
  `;
}

function buildComparisonCards(primary, baseline, deltas) {
  if (!primary) {
    return `<div class="empty-state">Select a sample to inspect its trace.</div>`;
  }
  const items = [
    { label: "Faithfulness", primary: primary.faithfulness_score, baseline: baseline?.faithfulness_score, delta: deltas?.faithfulness_score },
    { label: "Groundedness", primary: primary.groundedness_score, baseline: baseline?.groundedness_score, delta: deltas?.groundedness_score },
    { label: "Citation Correctness", primary: primary.citation_correctness_score, baseline: baseline?.citation_correctness_score, delta: deltas?.citation_correctness_score },
    { label: "Hit@5", primary: primary.hit_at_5, baseline: baseline?.hit_at_5, delta: deltas?.hit_at_5 },
  ];
  return `
    <div class="metric-grid">
      ${items
        .map(
          (item) => `
            <article class="metric-card">
              <span class="metric-label">${escapeHtml(item.label)}</span>
              <strong class="metric-value">${formatMetricValue(item.primary, item.label)}</strong>
              ${
                baseline
                  ? `<p class="metric-meta">Baseline ${formatMetricValue(item.baseline, item.label)} · Delta ${formatMetricValue(
                      item.delta,
                      item.label
                    )}</p>`
                  : `<p class="metric-meta">No baseline selected.</p>`
              }
            </article>
          `
        )
        .join("")}
    </div>
  `;
}

function buildTraceOverview(trace) {
  return buildDefinitionGrid([
    { label: "Sample Source", value: trace.sample_source },
    { label: "Experiment Id", value: trace.experiment_id },
    { label: "Request Id", value: trace.request_id },
    { label: "Ticket Id", value: trace.ticket_id },
    { label: "Query Type", value: trace.query_type },
    { label: "Source Type", value: trace.source_type },
    { label: "Product", value: trace.product },
    { label: "Language", value: trace.language },
    { label: "Retrieval Strategy", value: trace.retrieval_strategy },
    { label: "Generation Mode", value: trace.generation_mode },
    { label: "Needs Human", value: trace.needs_human },
    { label: "Handoff Reason", value: trace.handoff_reason },
    { label: "Created At", value: trace.created_at ? formatDateTime(trace.created_at) : "-" },
  ]);
}

function buildContextCards(contexts) {
  const rows = Array.isArray(contexts) ? contexts : [];
  if (!rows.length) {
    return `<div class="empty-state">No selected contexts captured for this sample.</div>`;
  }
  return `
    <div class="context-card-list">
      ${rows
        .map(
          (item) => `
            <article class="context-card">
              <div class="context-card-header">
                <div>
                  <h4>${escapeHtml(item.heading || item.title || item.chunk_id || "Context")}</h4>
                  <p>${escapeHtml(item.source_path || item.source_url || item.doc_id || "-")}</p>
                </div>
                <div class="context-card-meta">
                  <span>${escapeHtml(formatMetricValue(item.chunk_token_count, "chunk_token_count"))}</span>
                  <span>${escapeHtml(item.boundary_reason || item.index_role || "-")}</span>
                </div>
              </div>
              <pre>${escapeHtml(item.text || "")}</pre>
            </article>
          `
        )
        .join("")}
    </div>
  `;
}

function buildCandidateTable(candidates) {
  return buildTableSection("Retrieval Candidates", candidates, {
    columns: [
      "doc_id",
      "chunk_id",
      "heading",
      "rank_before_rerank",
      "rank_after_rerank",
      "retrieval_score",
      "rerank_score",
      "used_in_final_answer",
      "source_url",
    ],
    emptyLabel: "No retrieval candidates stored for this sample.",
  });
}

function renderExperimentsPage(payload) {
  const root = ragPageContainers.experiments.root;
  const sections = payload.sections || {};
  const summary = sections.summary || {};
  const leaderboard = sections.leaderboard?.rows || [];
  const metricRows = sections.metric_matrix?.rows || [];
  const segmentGroups = sections.segment_breakdown?.groups || [];
  const sampleList = sections.sample_list || {};
  const options = summary.available_experiments || [];

  root.innerHTML = `
    <section class="hero-card">
      <div class="hero-copy">
        <p class="eyebrow">Offline Benchmark First</p>
        <h2>${escapeHtml(summary.title || "Experiments")}</h2>
        <p>${escapeHtml(summary.subtitle || "Choose the better experiment before you inspect regressions.")}</p>
      </div>
      <div class="hero-actions">
        <label class="filter-field">
          <span>Baseline</span>
          <select id="baseline-experiment-selector">
            <option value="">Auto</option>
            ${options.map(buildExperimentOption).join("")}
          </select>
        </label>
        <label class="filter-field">
          <span>Candidate</span>
          <select id="candidate-experiment-selector">
            <option value="">Auto</option>
            ${options.map(buildExperimentOption).join("")}
          </select>
        </label>
      </div>
      ${buildMetricCards(summary.cards || {})}
    </section>

    <div class="two-column-grid">
      <section class="panel-card">
        <div class="panel-header">
          <div>
            <h3>Experiment Leaderboard</h3>
            <p>Sorted by faithfulness, groundedness, citation correctness, then hit@5.</p>
          </div>
        </div>
        ${buildTable(leaderboard, {
          columns: [
            "experiment_id",
            "benchmark_version",
            "chunk_strategy",
            "embedding_model",
            "retrieval_strategy",
            "reranker_model",
            "query_rewrite_enabled",
            "faithfulness_score_avg",
            "groundedness_score_avg",
            "citation_correctness_score_avg",
            "hit_at_5",
            "judge_disagreement_rate",
            "p95_latency_ms",
          ],
          emptyLabel: "No benchmark experiments found for this scope.",
        })}
      </section>
      <section class="panel-card">
        <div class="panel-header">
          <div>
            <h3>Candidate vs Baseline</h3>
            <p>Quality metrics dominate. Latency and cost stay secondary.</p>
          </div>
        </div>
        ${buildTable(metricRows, {
          columns: ["metric", "candidate", "baseline", "delta"],
          emptyLabel: "Pick two experiments to compare.",
        })}
      </section>
    </div>

    <div class="two-column-grid">
      ${buildSampleList("Top Regressions", sampleList.top_regressions || [], "danger")}
      ${buildSampleList("Top Wins", sampleList.top_wins || [], "success")}
    </div>

    ${(segmentGroups || []).map(buildGroupBlock).join("")}
  `;

  const baselineSelect = document.getElementById("baseline-experiment-selector");
  const candidateSelect = document.getElementById("candidate-experiment-selector");
  if (baselineSelect) {
    baselineSelect.value = summary.baseline_experiment_id || "";
  }
  if (candidateSelect) {
    candidateSelect.value = summary.candidate_experiment_id || "";
  }
}

function renderDiagnosisPage(payload) {
  const root = ragPageContainers.diagnosis.root;
  const sections = payload.sections || {};
  const sampleList = sections.sample_list || {};
  const traceSection = sections.diagnosis_trace || {};
  const primary = traceSection.primary || null;
  const baseline = traceSection.baseline || null;
  const deltas = traceSection.deltas || null;
  const traceTitle = primary?.question || primary?.user_query || primary?.request_id || "Diagnosis trace";

  root.innerHTML = `
    <div class="diagnosis-grid">
      <aside class="rail-stack">
        ${buildSampleList("Top Regressions", sampleList.top_regressions || [], "danger")}
        ${buildSampleList("Risky Live Queries", sampleList.risky_live_queries || [], "warning")}
        ${buildSampleList("Review Queue", sampleList.review_queue || [], "neutral")}
      </aside>

      <article class="detail-stack">
        <section class="panel-card">
          <div class="panel-header">
            <div>
              <p class="eyebrow">Trace Overview</p>
              <h3>${escapeHtml(traceTitle)}</h3>
              <p>Use this view to decide whether the miss came from retrieval, chunking, context selection, or generation.</p>
            </div>
          </div>
          ${
            primary
              ? `
                ${buildTraceOverview(primary)}
                <section class="callout-block">
                  <h4>Question</h4>
                  <p>${escapeHtml(primary.user_query || "-")}</p>
                  ${
                    primary.rewritten_query
                      ? `<div class="callout-inline"><span>Rewritten Query</span><strong>${escapeHtml(
                          primary.rewritten_query
                        )}</strong></div>`
                      : ""
                  }
                  ${primary.intent ? `<div class="callout-inline"><span>Intent</span><strong>${escapeHtml(primary.intent)}</strong></div>` : ""}
                </section>
                ${buildComparisonCards(primary, baseline, deltas)}
                <section class="callout-block">
                  <h4>Answer</h4>
                  <pre class="answer-block">${escapeHtml(primary.answer || "-")}</pre>
                  <div class="chip-row">
                    ${buildChipList(primary.root_cause_labels || [], "warning")}
                    ${
                      primary.expected_document_ids
                        ? buildChipList(primary.expected_document_ids.map((item) => `doc:${item}`), "neutral")
                        : ""
                    }
                  </div>
                </section>
                ${primary.related_ingestion_ids?.length ? `
                  <div class="button-row">
                    ${primary.related_ingestion_ids
                      .map(
                        (ingestionId) => `
                          <button type="button" class="ghost-button" data-open-report="${escapeHtml(ingestionId)}">
                            Open Ingestion ${escapeHtml(ingestionId)}
                          </button>
                        `
                      )
                      .join("")}
                  </div>
                ` : ""}
              `
              : `<div class="empty-state">Choose a regression, review sample, or risky live query to inspect.</div>`
          }
        </section>
      </article>

      <aside class="evidence-stack">
        <section class="panel-card">
          <div class="panel-header">
            <div>
              <h3>Retrieval Evidence</h3>
              <p>Candidates and selected contexts for the focused sample.</p>
            </div>
          </div>
          ${
            primary
              ? `
                ${buildDefinitionGrid([
                  { label: "Vector Candidates", value: primary.vector_candidates_count },
                  { label: "BM25 Candidates", value: primary.bm25_candidates_count },
                  { label: "Reranked Candidates", value: primary.reranked_candidates_count },
                  { label: "Selected Docs", value: primary.selected_doc_count },
                  { label: "Top1 Similarity", value: primary.top1_similarity_score },
                  { label: "Avg Selected Similarity", value: primary.avg_selected_similarity_score },
                  { label: "Citation Count", value: primary.citation_count },
                  { label: "Citation Coverage", value: primary.citation_coverage_ratio },
                ])}
                ${buildCandidateTable(primary.candidates || [])}
              `
              : `<div class="empty-state">No sample selected.</div>`
          }
        </section>
        <section class="panel-card">
          <div class="panel-header">
            <div>
              <h3>Selected Contexts</h3>
              <p>Chunk-level evidence used to form the final answer.</p>
            </div>
          </div>
          ${buildContextCards(primary?.selected_contexts || [])}
        </section>
      </aside>
    </div>
  `;
}

function renderKnowledgeSupplyPage(payload) {
  const root = ragPageContainers["knowledge-supply"].root;
  const sections = payload.sections || {};
  const summary = sections.summary || {};
  const groups = sections.segment_breakdown?.groups || [];
  const sampleList = sections.sample_list || {};

  root.innerHTML = `
    <section class="hero-card">
      <div class="hero-copy">
        <p class="eyebrow">Supply Before Retrieval</p>
        <h2>${escapeHtml(summary.title || "Knowledge Supply")}</h2>
        <p>${escapeHtml(summary.subtitle || "Inspect ingestion, chunking, and index health before blaming the ranker.")}</p>
      </div>
      ${buildMetricCards(summary.cards || {})}
    </section>
    ${groups.map(buildGroupBlock).join("")}
    <div class="two-column-grid">
      ${buildIngestionTaskList("Failed Tasks", sampleList.failed_tasks || [])}
      ${buildTableSection("Chunking Anomalies", sampleList.chunking_anomalies || [], {
        emptyLabel: "No chunking anomalies in this scope.",
      })}
    </div>
    ${buildTableSection("Metadata Completeness", sampleList.metadata_completeness || [], {
      emptyLabel: "No metadata completeness data available.",
    })}
  `;
}

function renderProductionSignalsPage(payload) {
  const root = ragPageContainers["production-signals"].root;
  const sections = payload.sections || {};
  const summary = sections.summary || {};
  const groups = sections.segment_breakdown?.groups || [];
  const sampleList = sections.sample_list || {};

  root.innerHTML = `
    <section class="hero-card">
      <div class="hero-copy">
        <p class="eyebrow">Live Proxy Signals</p>
        <h2>${escapeHtml(summary.title || "Production Signals")}</h2>
        <p>${escapeHtml(summary.subtitle || "Use online proxy metrics to spot regression, then jump into diagnosis.")}</p>
      </div>
      ${buildMetricCards(summary.cards || {})}
    </section>
    ${groups.map(buildGroupBlock).join("")}
    <div class="two-column-grid">
      ${buildSampleList("Recent Risky Cases", sampleList.risky_cases || [], "danger")}
      ${buildSampleList("Review Queue Snapshot", sampleList.review_queue || [], "warning")}
    </div>
  `;
}

function buildReviewQueue(rows, title) {
  const items = Array.isArray(rows) ? rows : [];
  return `
    <section class="panel-card">
      <div class="panel-header">
        <div>
          <h3>${escapeHtml(title)}</h3>
          <p>Review directly here, or jump to full diagnosis.</p>
        </div>
      </div>
      ${
        items.length
          ? `<div class="review-list">
              ${items
                .map(
                  (row) => `
                    <article class="review-item" data-review-item="${escapeHtml(row.sample_id || "")}">
                      <div class="review-item-header">
                        <div class="review-item-copy">
                          <div class="chip-row">
                            <span class="chip chip-neutral">${escapeHtml(row.sample_source || "sample")}</span>
                            <span class="chip chip-warning">${escapeHtml(row.review_status || "pending")}</span>
                            ${buildChipList(row.sampling_reasons || [], "warning")}
                          </div>
                          <h4>${escapeHtml(row.sample_question || "Review sample")}</h4>
                          <p>${escapeHtml(row.failure_type || row.generation_mode || "-")}</p>
                        </div>
                        <div class="review-item-actions">
                          ${buildSampleAction(row)}
                        </div>
                      </div>
                      ${buildDefinitionGrid([
                        { label: "Risk Score", value: row.risk_score },
                        { label: "Query Type", value: row.query_type },
                        { label: "Source Type", value: row.source_type },
                        { label: "Retrieval Strategy", value: row.retrieval_strategy },
                        { label: "Faithfulness", value: row.faithfulness_score },
                        { label: "Groundedness", value: row.groundedness_score },
                        { label: "Citation Correctness", value: row.citation_correctness_score },
                        { label: "Confidence", value: row.confidence_score },
                        { label: "Citation Count", value: row.citation_count },
                      ])}
                      <div class="review-form-grid">
                        <label class="filter-field">
                          <span>Status</span>
                          <select data-review-status="${escapeHtml(row.sample_id || "")}">
                            <option value="pending" ${row.review_status === "pending" ? "selected" : ""}>Pending</option>
                            <option value="reviewed" ${row.review_status === "reviewed" ? "selected" : ""}>Reviewed</option>
                            <option value="dismissed" ${row.review_status === "dismissed" ? "selected" : ""}>Dismissed</option>
                          </select>
                        </label>
                        <label class="filter-field">
                          <span>Retrieval OK</span>
                          <select data-review-retrieval="${escapeHtml(row.sample_id || "")}">
                            <option value="" ${row.retrieval_ok === null || row.retrieval_ok === undefined ? "selected" : ""}>Unset</option>
                            <option value="true" ${row.retrieval_ok === true ? "selected" : ""}>Yes</option>
                            <option value="false" ${row.retrieval_ok === false ? "selected" : ""}>No</option>
                          </select>
                        </label>
                        <label class="filter-field">
                          <span>Answer OK</span>
                          <select data-review-answer="${escapeHtml(row.sample_id || "")}">
                            <option value="" ${row.answer_ok === null || row.answer_ok === undefined ? "selected" : ""}>Unset</option>
                            <option value="true" ${row.answer_ok === true ? "selected" : ""}>Yes</option>
                            <option value="false" ${row.answer_ok === false ? "selected" : ""}>No</option>
                          </select>
                        </label>
                        <label class="filter-field">
                          <span>Citation OK</span>
                          <select data-review-citation="${escapeHtml(row.sample_id || "")}">
                            <option value="" ${row.citation_ok === null || row.citation_ok === undefined ? "selected" : ""}>Unset</option>
                            <option value="true" ${row.citation_ok === true ? "selected" : ""}>Yes</option>
                            <option value="false" ${row.citation_ok === false ? "selected" : ""}>No</option>
                          </select>
                        </label>
                      </div>
                      <label class="review-note-field">
                        <span>Note</span>
                        <textarea data-review-note="${escapeHtml(row.sample_id || "")}" rows="4">${escapeHtml(row.note || "")}</textarea>
                      </label>
                      <div class="button-row">
                        <button type="button" class="primary-button" data-save-review="${escapeHtml(row.sample_id || "")}">
                          Save Review
                        </button>
                      </div>
                    </article>
                  `
                )
                .join("")}
            </div>`
          : `<div class="empty-state">No review samples available in this scope.</div>`
      }
    </section>
  `;
}

function renderReviewPage(payload) {
  const root = ragPageContainers.review.root;
  const sections = payload.sections || {};
  const summary = sections.summary || {};
  const reviewQueue = sections.review_queue || {};

  root.innerHTML = `
    <section class="hero-card">
      <div class="hero-copy">
        <p class="eyebrow">Human Feedback Loop</p>
        <h2>${escapeHtml(summary.title || "Review Queue")}</h2>
        <p>${escapeHtml(summary.subtitle || "Review high-risk live traffic and disputed benchmark samples directly from this queue.")}</p>
      </div>
      ${buildMetricCards(summary.cards || {})}
    </section>
    ${buildReviewQueue(reviewQueue.pending_rows || [], "Pending First")}
    <div class="two-column-grid">
      ${buildReviewQueue(reviewQueue.benchmark_rows || [], "Benchmark Samples")}
      ${buildReviewQueue(reviewQueue.live_rows || [], "Live Query Samples")}
    </div>
  `;
}

const pageRenderers = {
  experiments: { render: renderExperimentsPage },
  diagnosis: { render: renderDiagnosisPage },
  "knowledge-supply": { render: renderKnowledgeSupplyPage },
  "production-signals": { render: renderProductionSignalsPage },
  review: { render: renderReviewPage },
};

function setStatus(message) {
  dashboardStatusEl.textContent = message;
}

function setLastRefreshed(value) {
  lastRefreshedLabelEl.textContent = `Last refreshed: ${value ? formatDateTime(value) : "-"}`;
}

function updateScopeLabel() {
  const parts = [];
  for (const [key, value] of Object.entries(ragFilters)) {
    if (["sample_id", "request_id", "eval_run_id", "test_case_id", "baseline_experiment_id", "candidate_experiment_id", "limit"].includes(key)) {
      continue;
    }
    if (!value || value === "all") {
      continue;
    }
    parts.push(`${humanizeLabel(key)}=${value}`);
  }
  activeScopeLabelEl.textContent = parts.length ? `Scope: ${parts.join(" · ")}` : "Scope: all data";
}

function syncFiltersToInputs() {
  ragRangeFilterEl.value = ragFilters.range;
  ragSourceFilterEl.value = ragFilters.source_type;
  ragQueryTypeFilterEl.value = ragFilters.query_type;
  ragRetrievalFilterEl.value = ragFilters.retrieval_strategy;
  ragChunkFilterEl.value = ragFilters.chunk_strategy;
  ragProductFilterEl.value = ragFilters.product === "all" ? "" : ragFilters.product;
  ragLanguageFilterEl.value = ragFilters.language === "all" ? "" : ragFilters.language;
  ragExperimentFilterEl.value = ragFilters.experiment_id === "all" ? "" : ragFilters.experiment_id;
}

function readStateFromUrl() {
  const params = new URLSearchParams(window.location.search);
  const page = normalizeString(params.get("page")) || "experiments";
  currentDashboardTab = PAGE_LABELS[page] ? page : "experiments";
  for (const key of Object.keys(ragFilters)) {
    const value = params.get(key);
    if (value === null) {
      continue;
    }
    ragFilters[key] = value;
  }
  if (!["7d", "30d"].includes(ragFilters.range)) {
    ragFilters.range = "7d";
  }
  updateScopeLabel();
  syncFiltersToInputs();
}

function writeStateToUrl() {
  const params = new URLSearchParams();
  params.set("page", currentDashboardTab);
  for (const [key, value] of Object.entries(ragFilters)) {
    if (key === "limit" && Number(value) === 20) {
      continue;
    }
    if (value === null || value === undefined || value === "" || value === "all") {
      continue;
    }
    params.set(key, String(value));
  }
  const nextUrl = `${window.location.pathname}?${params.toString()}`;
  window.history.replaceState({}, "", nextUrl);
}

async function fetchJson(url, options = undefined) {
  const response = await fetch(url, options);
  if (!response.ok) {
    let reason = `HTTP ${response.status}`;
    try {
      const payload = await response.json();
      if (typeof payload?.detail === "string") {
        reason = payload.detail;
      }
    } catch {
      // Keep fallback reason.
    }
    throw new Error(reason);
  }
  return response.json();
}

function buildPageQuery() {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(ragFilters)) {
    if (value === null || value === undefined || value === "" || value === "all") {
      continue;
    }
    params.set(key, String(value));
  }
  if (!params.has("range")) {
    params.set("range", ragFilters.range);
  }
  if (!params.has("limit")) {
    params.set("limit", String(ragFilters.limit));
  }
  return params.toString();
}

function setActiveDashboardTab(tabName) {
  currentDashboardTab = PAGE_LABELS[tabName] ? tabName : "experiments";
  dashboardTabEls.forEach((button) => {
    button.classList.toggle("active", button.dataset.dashboardTab === currentDashboardTab);
  });
  dashboardPanelEls.forEach((panel) => {
    panel.classList.toggle("active", panel.dataset.dashboardPanel === currentDashboardTab);
  });
  writeStateToUrl();
}

function invalidatePageCache(pageName = null) {
  if (!pageName) {
    Object.keys(pageCache).forEach((key) => delete pageCache[key]);
    return;
  }
  delete pageCache[pageName];
}

async function loadPage(pageName, { force = false } = {}) {
  const root = ragPageContainers[pageName]?.root;
  if (!root) {
    return;
  }
  if (!force && pageCache[pageName]) {
    pageRenderers[pageName]?.render(pageCache[pageName]);
    return;
  }
  const token = ++loadToken;
  root.innerHTML = `<div class="empty-state">Loading ${escapeHtml(PAGE_LABELS[pageName])}...</div>`;
  setStatus(`Loading ${PAGE_LABELS[pageName]}...`);
  try {
    const payload = await fetchJson(`/api/dashboard/rag/${pageName}?${buildPageQuery()}`);
    if (token !== loadToken) {
      return;
    }
    pageCache[pageName] = payload;
    pageRenderers[pageName]?.render(payload);
    updateScopeLabel();
    setLastRefreshed(payload.last_refreshed_at);
    setStatus(`${PAGE_LABELS[pageName]} ready.`);
  } catch (error) {
    root.innerHTML = `<div class="empty-state">Failed to load ${escapeHtml(PAGE_LABELS[pageName])}: ${escapeHtml(error.message)}</div>`;
    setStatus(`Failed to load ${PAGE_LABELS[pageName]}: ${error.message}`);
  }
}

async function loadCurrentPage({ force = false } = {}) {
  await loadPage(currentDashboardTab, { force });
}

function openReportDrawer(payload) {
  reportDrawerTitleEl.textContent = payload?.summary?.ingestion_id || "Ingestion Report";
  reportDrawerBodyEl.innerHTML = `
    <div class="definition-grid">
      <div class="definition-item">
        <span class="definition-label">Ingestion Id</span>
        <strong class="definition-value">${escapeHtml(payload?.summary?.ingestion_id || "-")}</strong>
      </div>
      <div class="definition-item">
        <span class="definition-label">Status</span>
        <strong class="definition-value">${escapeHtml(payload?.summary?.status || "-")}</strong>
      </div>
      <div class="definition-item">
        <span class="definition-label">Knowledge Type</span>
        <strong class="definition-value">${escapeHtml(payload?.summary?.knowledge_type || "-")}</strong>
      </div>
      <div class="definition-item">
        <span class="definition-label">Source Type</span>
        <strong class="definition-value">${escapeHtml(payload?.summary?.source_type || "-")}</strong>
      </div>
    </div>
    <section class="panel-card">
      <div class="panel-header">
        <div>
          <h3>Report Payload</h3>
          <p>Structured report captured at ingestion time.</p>
        </div>
      </div>
      <pre class="answer-block">${escapeHtml(JSON.stringify(payload, null, 2))}</pre>
    </section>
  `;
  reportDrawerEl.hidden = false;
}

async function openIngestionReport(ingestionId) {
  const normalizedIngestionId = normalizeString(ingestionId);
  if (!normalizedIngestionId) {
    return;
  }
  setStatus(`Loading ingestion report ${normalizedIngestionId}...`);
  try {
    const payload = await fetchJson(`/api/dashboard/knowledge-ingestions/${encodeURIComponent(normalizedIngestionId)}/report`);
    openReportDrawer(payload);
    setStatus(`Ingestion report ${normalizedIngestionId} ready.`);
  } catch (error) {
    setStatus(`Failed to load ingestion report ${normalizedIngestionId}: ${error.message}`);
  }
}

function closeReportDrawer() {
  reportDrawerEl.hidden = true;
}

function setGlobalFilterState() {
  ragFilters.range = ragRangeFilterEl.value;
  ragFilters.source_type = ragSourceFilterEl.value;
  ragFilters.query_type = ragQueryTypeFilterEl.value;
  ragFilters.retrieval_strategy = ragRetrievalFilterEl.value;
  ragFilters.chunk_strategy = ragChunkFilterEl.value;
  ragFilters.product = normalizeString(ragProductFilterEl.value) || "all";
  ragFilters.language = normalizeString(ragLanguageFilterEl.value) || "all";
  ragFilters.experiment_id = normalizeString(ragExperimentFilterEl.value) || "all";
}

function clearDiagnosisSelectionForLive() {
  ragFilters.request_id = "";
  ragFilters.sample_id = "";
}

function openDiagnosisForBenchmark(evalRunId, testCaseId) {
  ragFilters.request_id = "";
  ragFilters.sample_id = "";
  ragFilters.eval_run_id = normalizeString(evalRunId);
  ragFilters.test_case_id = normalizeString(testCaseId);
  setActiveDashboardTab("diagnosis");
  loadCurrentPage({ force: true }).catch((error) => {
    setStatus(`Failed to open diagnosis: ${error.message}`);
  });
}

function openDiagnosisForLive(requestId) {
  ragFilters.eval_run_id = "";
  ragFilters.test_case_id = "";
  ragFilters.sample_id = "";
  ragFilters.request_id = normalizeString(requestId);
  setActiveDashboardTab("diagnosis");
  loadCurrentPage({ force: true }).catch((error) => {
    setStatus(`Failed to open diagnosis: ${error.message}`);
  });
}

async function saveReviewSample(sampleId) {
  const normalizedSampleId = normalizeString(sampleId);
  if (!normalizedSampleId) {
    return;
  }
  const statusEl = document.querySelector(`[data-review-status="${CSS.escape(normalizedSampleId)}"]`);
  const retrievalEl = document.querySelector(`[data-review-retrieval="${CSS.escape(normalizedSampleId)}"]`);
  const answerEl = document.querySelector(`[data-review-answer="${CSS.escape(normalizedSampleId)}"]`);
  const citationEl = document.querySelector(`[data-review-citation="${CSS.escape(normalizedSampleId)}"]`);
  const noteEl = document.querySelector(`[data-review-note="${CSS.escape(normalizedSampleId)}"]`);
  const payload = {
    review_status: statusEl?.value || "pending",
    retrieval_ok: retrievalEl?.value === "" ? null : retrievalEl?.value === "true",
    answer_ok: answerEl?.value === "" ? null : answerEl?.value === "true",
    citation_ok: citationEl?.value === "" ? null : citationEl?.value === "true",
    note: noteEl?.value || "",
  };
  setStatus(`Saving review for ${normalizedSampleId}...`);
  try {
    await fetchJson(`/api/dashboard/rag/review-samples/${encodeURIComponent(normalizedSampleId)}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    invalidatePageCache("review");
    invalidatePageCache("diagnosis");
    await loadCurrentPage({ force: true });
    setStatus(`Review ${normalizedSampleId} saved.`);
  } catch (error) {
    setStatus(`Failed to save review ${normalizedSampleId}: ${error.message}`);
  }
}

function handleDocumentClick(event) {
  const tabButton = event.target.closest("[data-dashboard-tab]");
  if (tabButton) {
    setActiveDashboardTab(tabButton.dataset.dashboardTab);
    loadCurrentPage({ force: false }).catch((error) => {
      setStatus(`Failed to switch page: ${error.message}`);
    });
    return;
  }
  if (event.target.closest("[data-close-report]")) {
    closeReportDrawer();
    return;
  }
  const benchmarkButton = event.target.closest("[data-open-diagnosis-benchmark]");
  if (benchmarkButton) {
    openDiagnosisForBenchmark(
      benchmarkButton.dataset.openDiagnosisBenchmark,
      benchmarkButton.dataset.openTestCase
    );
    return;
  }
  const liveButton = event.target.closest("[data-open-diagnosis-live]");
  if (liveButton) {
    openDiagnosisForLive(liveButton.dataset.openDiagnosisLive);
    return;
  }
  const reportButton = event.target.closest("[data-open-report]");
  if (reportButton) {
    openIngestionReport(reportButton.dataset.openReport);
    return;
  }
  const saveReviewButton = event.target.closest("[data-save-review]");
  if (saveReviewButton) {
    saveReviewSample(saveReviewButton.dataset.saveReview);
    return;
  }
}

function handleDocumentChange(event) {
  if (event.target.id === "baseline-experiment-selector") {
    ragFilters.baseline_experiment_id = normalizeString(event.target.value);
    invalidatePageCache("experiments");
    loadCurrentPage({ force: true }).catch((error) => {
      setStatus(`Failed to update baseline experiment: ${error.message}`);
    });
    return;
  }
  if (event.target.id === "candidate-experiment-selector") {
    ragFilters.candidate_experiment_id = normalizeString(event.target.value);
    invalidatePageCache("experiments");
    loadCurrentPage({ force: true }).catch((error) => {
      setStatus(`Failed to update candidate experiment: ${error.message}`);
    });
  }
}

function bindFilters() {
  [ragRangeFilterEl, ragSourceFilterEl, ragQueryTypeFilterEl, ragRetrievalFilterEl, ragChunkFilterEl].forEach((el) => {
    el.addEventListener("change", () => {
      setGlobalFilterState();
      invalidatePageCache();
      loadCurrentPage({ force: true }).catch((error) => {
        setStatus(`Failed to apply filters: ${error.message}`);
      });
    });
  });
  [ragProductFilterEl, ragLanguageFilterEl, ragExperimentFilterEl].forEach((el) => {
    el.addEventListener("change", () => {
      setGlobalFilterState();
      invalidatePageCache();
      loadCurrentPage({ force: true }).catch((error) => {
        setStatus(`Failed to apply filters: ${error.message}`);
      });
    });
  });
}

async function initializeDashboard() {
  readStateFromUrl();
  setActiveDashboardTab(currentDashboardTab);
  bindFilters();
  document.addEventListener("click", handleDocumentClick);
  document.addEventListener("change", handleDocumentChange);
  refreshButtonEl.addEventListener("click", () => {
    invalidatePageCache();
    loadCurrentPage({ force: true }).catch((error) => {
      setStatus(`Failed to refresh page: ${error.message}`);
    });
  });
  window.addEventListener("popstate", () => {
    readStateFromUrl();
    setActiveDashboardTab(currentDashboardTab);
    loadCurrentPage({ force: true }).catch((error) => {
      setStatus(`Failed to restore state: ${error.message}`);
    });
  });
  await loadCurrentPage({ force: true });
}

initializeDashboard().catch((error) => {
  setStatus(`Failed to initialize dashboard: ${error.message}`);
});
