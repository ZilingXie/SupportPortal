const dashboardStatusEl = document.getElementById("dashboard-status");
const activeScopeLabelEl = document.getElementById("active-scope-label");
const lastRefreshedLabelEl = document.getElementById("last-refreshed-label");
const currentBenchmarkRunSelectorEl = document.getElementById("current-benchmark-run-selector");
const currentBenchmarkRunMetaEl = document.getElementById("current-benchmark-run-meta");
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

const caseDetailModalEl = document.getElementById("case-detail-modal");
const caseDetailDialogEl = document.getElementById("case-detail-dialog");
const caseDetailTitleEl = document.getElementById("case-detail-title");
const caseDetailBodyEl = document.getElementById("case-detail-body");
const caseDetailDiagnosisButtonEl = document.querySelector("[data-open-full-diagnosis]");
const reportDrawerEl = document.getElementById("report-drawer");
const reportDrawerTitleEl = document.getElementById("report-drawer-title");
const reportDrawerBodyEl = document.getElementById("report-drawer-body");

const ragPageContainers = {
  "scorecard": { root: document.getElementById("rag-scorecard-page") },
  "routing": { root: document.getElementById("rag-routing-page") },
  "retrieval": { root: document.getElementById("rag-retrieval-page") },
  "generation": { root: document.getElementById("rag-generation-page") },
  "data-supply": { root: document.getElementById("rag-data-supply-page") },
  "diagnosis": { root: document.getElementById("rag-diagnosis-page") },
  "review": { root: document.getElementById("rag-review-page") },
};

const PAGE_LABELS = {
  scorecard: "Scorecard",
  routing: "Routing",
  retrieval: "Retrieval",
  generation: "Generation",
  "data-supply": "Data Supply",
  diagnosis: "Diagnosis",
  review: "Review Queue",
};
const DASHBOARD_PAGE_NAMES = Object.keys(PAGE_LABELS);
const ROUTING_SUMMARY_PERCENT_KEYS = new Set([
  "route_family_accuracy",
  "execution_action_accuracy",
  "tooling_profile_accuracy",
  "false_positive_to_agora_rag",
  "false_negative_for_true_agora_tech",
]);

const LOCAL_BENCHMARK_CATALOG = [
  {
    label: "Canonical",
    benchmark_version: "agora_rag_testset_100_standrad_en",
    source_path: "benchmarks/agora_rag_testset_100_standrad_en.json",
  },
  {
    label: "Mixed",
    benchmark_version: "agora_rag_testset_100_mixed_en",
    source_path: "benchmarks/agora_rag_testset_100_mixed_en.json",
  },
  {
    label: "Real User",
    benchmark_version: "agora_rag_testset_100_realUser_en",
    source_path: "benchmarks/agora_rag_testset_100_realUser_en.json",
  },
];

let currentDashboardTab = "scorecard";
let cacheEpoch = 0;
const pageCache = {};
const pageLoadPromises = {};
const caseDetailCache = {};
let caseDetailLoadToken = 0;
let diagnosisDetailLoadToken = 0;
let lastCaseDetailFocusEl = null;
let activeCaseDetailState = null;

const DEFAULT_RAG_FILTERS = {
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
const ragFilters = { ...DEFAULT_RAG_FILTERS };

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

function normalizeDashboardTab(value) {
  const normalized = normalizeString(value).toLowerCase();
  if (normalized === "experiments" || normalized === "production-signals") {
    return "scorecard";
  }
  if (normalized === "datasets" || normalized === "knowledge-supply") {
    return "data-supply";
  }
  return PAGE_LABELS[normalized] ? normalized : "scorecard";
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

function formatPercentageValue(value) {
  if (value === null || value === undefined || value === "") {
    return "-";
  }
  if (typeof value !== "number") {
    return formatMetricValue(value);
  }
  return `${formatDecimal(value * 100, 1)}%`;
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

function buildMetricCards(cards, options = {}) {
  const formatters = options.formatters || {};
  const entries = Object.entries(cards || {}).filter(([, value]) => value !== null && value !== undefined && value !== "");
  if (!entries.length) {
    return `<div class="empty-state">No metrics available in this scope.</div>`;
  }
  return `
    <div class="metric-grid">
      ${entries
        .map(
          ([key, value]) => {
            const formatter = formatters[key];
            const displayValue = formatter ? formatter(value, key) : formatMetricValue(value, key);
            return `
            <article class="metric-card">
              <span class="metric-label">${escapeHtml(humanizeLabel(key))}</span>
              <strong class="metric-value">${displayValue}</strong>
            </article>
          `;
          }
        )
        .join("")}
    </div>
  `;
}

function buildRoutingSummaryCardFormatters(cards) {
  return Object.fromEntries(
    Object.keys(cards || {})
      .map((key) => [key, ROUTING_SUMMARY_PERCENT_KEYS.has(key) ? formatPercentageValue : null])
      .filter(([, formatter]) => formatter)
  );
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

function findExperimentOption(options, identifier) {
  const normalizedIdentifier = normalizeString(identifier);
  if (!normalizedIdentifier) {
    return null;
  }
  return (
    (options || []).find(
      (option) =>
        normalizedIdentifier === normalizeString(option?.experiment_id) ||
        normalizedIdentifier === normalizeString(option?.eval_run_id)
    ) || null
  );
}

function resolveCandidateExperiment(context, candidateIdentifier = null) {
  const options = Array.isArray(context?.available_experiments) ? context.available_experiments : [];
  if (candidateIdentifier !== null) {
    const explicitCandidate = findExperimentOption(options, candidateIdentifier);
    if (explicitCandidate) {
      return explicitCandidate;
    }
    if (!normalizeString(candidateIdentifier)) {
      return options[0] || null;
    }
  }
  return (
    findExperimentOption(
      options,
      context?.candidate_experiment_id || context?.current_experiment_id || context?.current_eval_run_id
    ) ||
    options[0] ||
    null
  );
}

function buildBenchmarkRunMeta(option) {
  const parts = [];
  if (normalizeString(option?.benchmark_version)) {
    parts.push(option.benchmark_version);
  }
  const dateValue = option?.finished_at || option?.created_at || "";
  if (normalizeString(dateValue)) {
    parts.push(formatDateTime(dateValue));
  }
  return parts.join(" · ");
}

function resolveScorecardBaselineExperiment(context) {
  const options = Array.isArray(context?.available_experiments) ? context.available_experiments : [];
  return (
    findExperimentOption(
      options,
      context?.baseline_experiment_id || context?.current_experiment_id || context?.current_eval_run_id
    ) ||
    options[0] ||
    null
  );
}

function getScorecardCandidateOptions(context) {
  const options = Array.isArray(context?.available_experiments) ? context.available_experiments : [];
  const baseline = resolveScorecardBaselineExperiment(context);
  const baselineIdentifier = normalizeString(
    baseline?.experiment_id || baseline?.eval_run_id || context?.baseline_experiment_id || context?.current_experiment_id
  );
  return options.filter(
    (option) => normalizeString(option?.experiment_id || option?.eval_run_id) !== baselineIdentifier
  );
}

function resolveScorecardComparisonCandidate(context) {
  const candidateOptions = getScorecardCandidateOptions(context);
  return findExperimentOption(candidateOptions, context?.candidate_experiment_id) || candidateOptions[0] || null;
}

function buildExperimentComparisonControls(summary, benchmarkSelector = null) {
  const comparisonContext = {
    available_experiments:
      (Array.isArray(summary?.available_experiments) && summary.available_experiments.length
        ? summary.available_experiments
        : benchmarkSelector?.available_experiments) || [],
    current_experiment_id: benchmarkSelector?.current_experiment_id,
    current_eval_run_id: benchmarkSelector?.current_eval_run_id,
    baseline_experiment_id: summary?.baseline_experiment_id,
    candidate_experiment_id: summary?.candidate_experiment_id,
  };
  const baseline = resolveScorecardBaselineExperiment(comparisonContext);
  const candidateOptions = getScorecardCandidateOptions(comparisonContext);
  const candidate = resolveScorecardComparisonCandidate(comparisonContext);
  const hasAlternateCandidate = candidateOptions.length > 0;
  const sharedFootnote = hasAlternateCandidate
    ? "Current Benchmark Run stays pinned as the baseline. Choose another benchmark run as the candidate."
    : "Current Benchmark Run stays pinned as the baseline. No alternate candidate benchmark run is available yet.";
  const baselineLabel = baseline?.label || baseline?.experiment_id || baseline?.eval_run_id || "Current benchmark run";
  const baselineMeta = buildBenchmarkRunMeta(baseline);
  return `
    <div class="comparison-controls">
      <div class="comparison-controls-grid hero-actions">
        <article class="comparison-static-card">
          <span class="comparison-static-label">Baseline</span>
          <strong class="comparison-static-value">${escapeHtml(baselineLabel)}</strong>
          <span class="comparison-static-meta">${escapeHtml(baselineMeta || "Current benchmark run")}</span>
        </article>
        <label class="filter-field">
          <span>Candidate</span>
          <select id="candidate-experiment-selector" ${hasAlternateCandidate ? "" : "disabled"}>
            ${
              hasAlternateCandidate
                ? candidateOptions.map(buildExperimentOption).join("")
                : `<option value="">No alternate run available</option>`
            }
          </select>
        </label>
      </div>
      <p class="comparison-controls-note">${escapeHtml(sharedFootnote)}</p>
    </div>
  `;
}

function buildBenchmarkSessionPanel(benchmarkSession) {
  const session = benchmarkSession && typeof benchmarkSession === "object" ? benchmarkSession : null;
  if (!normalizeString(session?.benchmark_session_id)) {
    return "";
  }
  const improvementEntries = Array.isArray(session.improvement_entries) ? session.improvement_entries : [];
  const runs = Array.isArray(session.runs) ? session.runs : [];
  const improvementSummary = normalizeString(session.improvement_summary);
  const runRows = runs.map((run) => ({
    label: run.label || run.dataset_name || run.benchmark_version || run.eval_run_id,
    benchmark_version: run.benchmark_version,
    status: run.is_current ? `${run.status || "-"} (current)` : run.status,
    experiment_id: run.experiment_id,
    eval_run_id: run.eval_run_id,
    finished_at: run.finished_at || run.started_at || "",
  }));
  return `
    <section class="panel-card benchmark-session-panel">
      <div class="panel-header">
        <div>
          <p class="eyebrow">Benchmark Session</p>
          <h3>${escapeHtml(session.session_name || session.benchmark_session_id || "Benchmark Session")}</h3>
          <p>Persist the changelog delta and sibling benchmark runs for the current selected benchmark context.</p>
        </div>
        <span class="chip chip-neutral">${escapeHtml(session.status || "queued")}</span>
      </div>
      ${buildDefinitionGrid([
        { label: "Session Id", value: session.benchmark_session_id },
        { label: "Previous Session", value: session.previous_session_id || "(none)" },
        { label: "Started At", value: session.started_at ? formatDateTime(session.started_at) : "-" },
        { label: "Finished At", value: session.finished_at ? formatDateTime(session.finished_at) : "-" },
      ])}
      <div class="section-stack">
        <article class="sample-item">
          <div class="sample-item-header">
            <div>
              <h4>Improvements Since Previous Benchmark Session</h4>
              <p>Snapshot stored when this 3-run benchmark session was queued.</p>
            </div>
          </div>
          ${
            improvementSummary
              ? `<p>${escapeHtml(improvementSummary).replaceAll("\n", "<br />")}</p>`
              : `<div class="empty-state">No improvement summary recorded for this benchmark session.</div>`
          }
          <div class="chip-row">
            ${
              improvementEntries.length
                ? improvementEntries
                    .map(
                      (entry) => `
                        <span class="chip chip-neutral">${escapeHtml(entry.title || `Entry ${entry.entry_index ?? ""}`)}</span>
                      `
                    )
                    .join("")
                : `<span class="chip chip-neutral">No changelog entries linked</span>`
            }
          </div>
        </article>
      </div>
    </section>
    ${buildTableSection("Session Runs", runRows, {
      columns: ["label", "benchmark_version", "status", "experiment_id", "eval_run_id", "finished_at"],
      emptyLabel: "No linked benchmark runs have been recorded for this session yet.",
    })}
  `;
}

function renderBenchmarkRunSelector(benchmarkSelector) {
  if (!currentBenchmarkRunSelectorEl || !currentBenchmarkRunMetaEl) {
    return;
  }
  const options = Array.isArray(benchmarkSelector?.available_experiments) ? benchmarkSelector.available_experiments : [];
  const currentOption = resolveCandidateExperiment(benchmarkSelector);
  if (!options.length) {
    currentBenchmarkRunSelectorEl.innerHTML = `<option value="">No benchmark runs available</option>`;
    currentBenchmarkRunSelectorEl.disabled = true;
    currentBenchmarkRunMetaEl.textContent = "No benchmark runs found in this scope.";
    return;
  }
  currentBenchmarkRunSelectorEl.innerHTML = options.map(buildExperimentOption).join("");
  currentBenchmarkRunSelectorEl.disabled = false;
  const currentValue = normalizeString(currentOption?.experiment_id || currentOption?.eval_run_id);
  if (currentValue) {
    currentBenchmarkRunSelectorEl.value = currentValue;
  }
  currentBenchmarkRunMetaEl.textContent = buildBenchmarkRunMeta(currentOption) || "Latest completed benchmark run";
}

function caseDetailCacheKey(prefix, values = []) {
  return [prefix, ...values.map((item) => normalizeString(item) || "-")].join(":");
}

async function fetchBenchmarkCaseDetail(evalRunId, testCaseId, baselineEvalRunId = "") {
  const key = caseDetailCacheKey("benchmark", [evalRunId, testCaseId, baselineEvalRunId]);
  if (caseDetailCache[key]) {
    return caseDetailCache[key];
  }
  const params = new URLSearchParams({
    eval_run_id: normalizeString(evalRunId),
    test_case_id: normalizeString(testCaseId),
  });
  if (normalizeString(baselineEvalRunId)) {
    params.set("baseline_eval_run_id", normalizeString(baselineEvalRunId));
  }
  const payload = await fetchJson(`/api/dashboard/rag/cases/benchmark-detail?${params.toString()}`);
  caseDetailCache[key] = payload;
  return payload;
}

async function fetchLiveCaseDetail(requestId) {
  const key = caseDetailCacheKey("live", [requestId]);
  if (caseDetailCache[key]) {
    return caseDetailCache[key];
  }
  const params = new URLSearchParams({ request_id: normalizeString(requestId) });
  const payload = await fetchJson(`/api/dashboard/rag/cases/live-detail?${params.toString()}`);
  caseDetailCache[key] = payload;
  return payload;
}

function buildCollapsiblePanel({ title, subtitle, count, body, open = true, tone = "neutral", extraClass = "" }) {
  return `
    <details class="collapsible-panel ${escapeHtml(extraClass)}" ${open ? "open" : ""}>
      <summary class="collapsible-summary">
        <div class="collapsible-copy">
          <h3>${escapeHtml(title)}</h3>
          ${subtitle ? `<p>${escapeHtml(subtitle)}</p>` : ""}
        </div>
        <div class="collapsible-meta">
          <span class="chip chip-${escapeHtml(tone)}">${escapeHtml(formatNumber(count || 0))} cases</span>
        </div>
      </summary>
      <div class="collapsible-body">
        ${body}
      </div>
    </details>
  `;
}

function buildCaseMetricGrid(items, className = "case-metric-grid") {
  const normalizedItems = (items || []).filter((item) => item && item.label);
  if (!normalizedItems.length) {
    return "";
  }
  return `
    <dl class="${escapeHtml(className)}">
      ${normalizedItems
        .map(
          (item) => `
            <div>
              <dt>${escapeHtml(item.label)}</dt>
              <dd>${escapeHtml(item.displayValue || formatMetricValue(item.value, item.key || item.label))}</dd>
            </div>
          `
        )
        .join("")}
    </dl>
  `;
}

function buildCaseExplorerItem(row, metricItems, { baselineEvalRunId = "" } = {}) {
  const headerChips = [
    `<span class="chip chip-neutral">${escapeHtml(humanizeLabel(row.category || "unknown"))}</span>`,
  ];
  if (row.failure_bucket) {
    headerChips.push(`<span class="chip chip-warning">${escapeHtml(humanizeLabel(row.failure_bucket))}</span>`);
  }
  return `
    <article class="case-explorer-item">
      <button
        type="button"
        class="case-explorer-button"
        data-open-case-detail-benchmark="${escapeHtml(row.eval_run_id || "")}"
        data-open-case-detail-test-case="${escapeHtml(row.test_case_id || "")}"
        data-open-case-detail-baseline="${escapeHtml(baselineEvalRunId || "")}"
      >
        <div class="case-explorer-header">
          ${headerChips.join("")}
        </div>
        <h4>${escapeHtml(row.question || "Untitled case")}</h4>
        ${buildCaseMetricGrid(metricItems)}
      </button>
    </article>
  `;
}

function buildRoutingCaseRow(row, { baselineEvalRunId = "" } = {}) {
  return buildCaseExplorerItem(
    row,
    [
      {
        label: "Expected Route",
        value: row.expected_route_family,
        displayValue: humanizeLabel(row.expected_route_family || "unknown"),
      },
      {
        label: "Actual Route",
        value: row.actual_route_family,
        displayValue: humanizeLabel(row.actual_route_family || "unknown"),
      },
    ],
    { baselineEvalRunId }
  );
}

function buildRetrievalCaseRow(row, { baselineEvalRunId = "" } = {}) {
  return buildCaseExplorerItem(
    row,
    [
      { label: "Evidence Hit@5", value: row.evidence_hit_at_5, key: "evidence_hit_at_5" },
      { label: "Coverage", value: row.evidence_coverage, key: "coverage_rate" },
      { label: "Noise", value: row.noise_rate, key: "noise_rate" },
    ],
    { baselineEvalRunId }
  );
}

function buildGenerationCaseRow(row, { baselineEvalRunId = "" } = {}) {
  return buildCaseExplorerItem(
    row,
    [
      { label: "Answer Accuracy", value: row.answer_accuracy_score, key: "answer_accuracy_score" },
      { label: "Faithfulness", value: row.faithfulness_score, key: "faithfulness_score" },
      { label: "Policy Followed", value: row.response_policy_followed, key: "response_policy_followed" },
    ],
    { baselineEvalRunId }
  );
}

function buildCaseExplorerSection(title, rows, options = {}) {
  const listRows = Array.isArray(rows) ? rows : [];
  const renderRow = options.renderRow || buildRoutingCaseRow;
  const body = listRows.length
    ? `<div class="case-explorer-list">
        ${listRows.map((row) => renderRow(row, options)).join("")}
      </div>`
    : `<div class="empty-state">No cases in this section for the current candidate run.</div>`;
  return buildCollapsiblePanel({
    title,
    subtitle: options.subtitle,
    count: listRows.length,
    open: options.open !== false,
    tone: options.tone || "neutral",
    body,
    extraClass: "case-explorer-panel",
  });
}

function buildRoutingCaseExplorerSection(title, rows, options = {}) {
  return buildCaseExplorerSection(title, rows, {
    ...options,
    renderRow: buildRoutingCaseRow,
  });
}

function buildDiagnosisChooserItem(item, tone = "neutral") {
  if (item.sample_source === "live_query") {
    return `
      <article class="sample-item">
        <div class="sample-item-header">
          <span class="chip chip-${tone}">${escapeHtml(item.sample_source || "live_query")}</span>
          ${buildChipList(item.root_cause_labels || [], tone)}
        </div>
        <h4>${escapeHtml(item.question || item.user_query || "Untitled sample")}</h4>
        <div class="sample-meta">
          <span>${escapeHtml(humanizeLabel(item.query_type || "unknown"))}</span>
          <span>${escapeHtml(humanizeLabel(item.source_type || "unknown"))}</span>
          ${item.created_at ? `<span>${escapeHtml(formatDateTime(item.created_at))}</span>` : ""}
        </div>
        <div class="sample-item-actions">
          <button
            type="button"
            class="table-action-button"
            data-select-diagnosis-live="${escapeHtml(item.request_id || "")}"
          >
            Inspect
          </button>
        </div>
      </article>
    `;
  }
  return `
    <article class="sample-item">
      <div class="sample-item-header">
        <span class="chip chip-${tone}">${escapeHtml(item.sample_source || "benchmark")}</span>
        ${buildChipList(item.root_cause_labels || [], tone)}
      </div>
      <h4>${escapeHtml(item.question || item.user_query || "Untitled sample")}</h4>
      <div class="sample-meta">
        <span>${escapeHtml(humanizeLabel(item.query_type || "unknown"))}</span>
        <span>${escapeHtml(humanizeLabel(item.source_type || "unknown"))}</span>
        ${
          item.delta_quality_score !== undefined
            ? `<span>${escapeHtml(formatMetricValue(item.delta_quality_score, "score"))} delta</span>`
            : ""
        }
        ${item.created_at ? `<span>${escapeHtml(formatDateTime(item.created_at))}</span>` : ""}
      </div>
      <div class="sample-item-actions">
        <button
          type="button"
          class="table-action-button"
          data-select-diagnosis-benchmark="${escapeHtml(item.eval_run_id || "")}"
          data-select-diagnosis-test-case="${escapeHtml(item.test_case_id || "")}"
        >
          Inspect
        </button>
      </div>
    </article>
  `;
}

function buildDiagnosisChooserSection(title, items, tone = "neutral", options = {}) {
  const rows = Array.isArray(items) ? items : [];
  const body = rows.length
    ? `<div class="sample-list">${rows.map((item) => buildDiagnosisChooserItem(item, tone)).join("")}</div>`
    : `<div class="empty-state">No samples available in this section.</div>`;
  return buildCollapsiblePanel({
    title,
    subtitle: options.subtitle || "Choose a case, then inspect the shared detail surface below.",
    count: rows.length,
    open: options.open !== false,
    tone,
    body,
    extraClass: "diagnosis-chooser-panel",
  });
}

function buildSampleAction(item) {
  if (item.sample_source === "dataset_candidate") {
    return `
      <button
        type="button"
        class="table-action-button"
        data-open-datasets-review="${escapeHtml(item.dataset_item_id || "")}"
      >
        Open Dataset
      </button>
    `;
  }
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

function buildDatasetGenerationRuns(rows) {
  const items = Array.isArray(rows) ? rows : [];
  return `
    <section class="panel-card">
      <div class="panel-header">
        <div>
          <h3>Generation Runs</h3>
          <p>Queued and completed dataset factory runs across the current scope.</p>
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
                        <span class="chip chip-neutral">${escapeHtml(row.status || "queued")}</span>
                        ${buildChipList(row.source_types || [], "neutral")}
                      </div>
                      <h4>${escapeHtml(row.dataset_name || row.generation_run_id || "Dataset Generation")}</h4>
                      <div class="sample-meta">
                        <span>${escapeHtml(row.benchmark_version || "-")}</span>
                        <span>${escapeHtml(row.question_language || "en")}</span>
                        <span>${escapeHtml(formatDateTime(row.created_at || ""))}</span>
                      </div>
                      ${buildDefinitionGrid([
                        { label: "Candidates", value: row.candidate_count_total },
                        { label: "Silver", value: row.silver_item_count },
                        { label: "Gold", value: row.gold_item_count },
                        { label: "Review Required", value: row.review_required_count },
                        { label: "Reviewed", value: row.reviewed_item_count },
                      ])}
                      ${row.error_message ? `<p>${escapeHtml(row.error_message)}</p>` : ""}
                    </article>
                  `
                )
                .join("")}
            </div>`
          : `<div class="empty-state">No dataset generation runs available yet.</div>`
      }
    </section>
  `;
}

function buildDatasetVersionCards(rows) {
  const items = Array.isArray(rows) ? rows : [];
  return `
    <section class="panel-card">
      <div class="panel-header">
        <div>
          <h3>Dataset Versions</h3>
          <p>Gold is the fixed benchmark source. Silver stays available for quicker regression loops.</p>
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
                        <span class="chip chip-neutral">${escapeHtml(row.status || "draft")}</span>
                        ${buildChipList(row.source_types || [], "neutral")}
                      </div>
                      <h4>${escapeHtml(row.dataset_name || row.dataset_id || "Dataset")}</h4>
                      <div class="sample-meta">
                        <span>${escapeHtml(row.benchmark_version || "-")}</span>
                        <span>${escapeHtml(row.question_language || "en")}</span>
                        <span>${escapeHtml(formatDateTime(row.updated_at || row.created_at || ""))}</span>
                      </div>
                      ${buildDefinitionGrid([
                        { label: "Items", value: row.item_count_total },
                        { label: "Silver", value: row.silver_item_count },
                        { label: "Gold", value: row.gold_item_count },
                        { label: "Pending Review", value: row.pending_review_count },
                      ])}
                      <div class="sample-item-actions">
                        <button type="button" class="table-action-button" data-export-dataset="${escapeHtml(row.dataset_id || "")}">
                          Export Gold
                        </button>
                        <button type="button" class="table-action-button" data-run-dataset-benchmark="${escapeHtml(row.dataset_id || "")}">
                          Run Benchmark
                        </button>
                        <button type="button" class="table-action-button" data-open-review-page>
                          Open Review
                        </button>
                      </div>
                    </article>
                  `
                )
                .join("")}
            </div>`
          : `<div class="empty-state">No dataset versions available yet.</div>`
      }
    </section>
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
    { label: "Answer Accuracy", primary: primary.answer_accuracy_score, baseline: baseline?.answer_accuracy_score, delta: deltas?.answer_accuracy_score },
    { label: "Answer Logic", primary: primary.answer_logic_score, baseline: baseline?.answer_logic_score, delta: deltas?.answer_logic_score },
    { label: "Evidence Hit@5", primary: primary.evidence_hit_at_5, baseline: baseline?.evidence_hit_at_5, delta: deltas?.evidence_hit_at_5 },
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

function buildCaseDetailHeader(primary, baseline, deltas) {
  const title = primary?.question || primary?.user_query || primary?.request_id || "Case detail";
  const routeOk = primary?.route_family_correct;
  const statusTone = routeOk === 1 || routeOk === true ? "success" : routeOk === 0 ? "danger" : "neutral";
  const statusLabel =
    routeOk === 1 || routeOk === true
      ? "Route Correct"
      : routeOk === 0
        ? "Route Mismatch"
        : humanizeLabel(primary?.sample_source || "case");
  return `
    <section class="panel-card detail-surface">
      <div class="panel-header case-detail-hero">
        <div>
          <p class="eyebrow">Trace Detail</p>
          <h3>${escapeHtml(title)}</h3>
          <p>Read route contract, answer behavior, evidence, and judge signals in one surface.</p>
        </div>
        <div class="chip-row">
          <span class="chip chip-${statusTone}">${escapeHtml(statusLabel)}</span>
          ${
            primary?.category
              ? `<span class="chip chip-neutral">${escapeHtml(humanizeLabel(primary.category))}</span>`
              : ""
          }
          ${
            primary?.sample_source
              ? `<span class="chip chip-neutral">${escapeHtml(humanizeLabel(primary.sample_source))}</span>`
              : ""
          }
        </div>
      </div>
      ${buildTraceOverview(primary || {})}
      ${buildComparisonCards(primary, baseline, deltas)}
    </section>
  `;
}

function buildCaseDetailRouteContract(primary, baseline) {
  const items = [
    { label: "Expected Route Family", value: primary?.expected_route_family },
    { label: "Actual Route Family", value: primary?.actual_route_family },
    { label: "Expected Execution Action", value: primary?.expected_execution_action },
    { label: "Actual Execution Action", value: primary?.actual_execution_action },
    { label: "Expected Tooling Profile", value: primary?.expected_tooling_profile },
    { label: "Actual Tooling Profile", value: primary?.actual_tooling_profile },
    { label: "Route Family Correct", value: primary?.route_family_correct },
    { label: "Execution Action Correct", value: primary?.execution_action_correct },
    { label: "Tooling Profile Correct", value: primary?.tooling_profile_correct },
  ].filter((item) => item.value !== undefined && item.value !== null && item.value !== "");
  if (!items.length) {
    return "";
  }
  return `
    <section class="panel-card detail-surface">
      <div class="panel-header">
        <div>
          <h3>Route Contract</h3>
          <p>Expected vs actual route family, execution action, and tooling policy.</p>
        </div>
      </div>
      ${buildDefinitionGrid(items)}
      ${
        baseline
          ? `<p class="detail-note">Baseline route family: <strong>${escapeHtml(
              humanizeLabel(baseline.actual_route_family || "-")
            )}</strong></p>`
          : ""
      }
    </section>
  `;
}

function buildCaseDetailAnswer(primary) {
  const sources = normalizeStringList(primary?.answer_sources || []);
  const citations = Array.isArray(primary?.answer_citations) ? primary.answer_citations : [];
  const actualAnswer = primary?.answer || primary?.actual_answer_text || primary?.answer_text || "-";
  return `
    <section class="panel-card detail-surface">
      <div class="panel-header">
        <div>
          <h3>Answer</h3>
          <p>The answer body plus any stored source snapshot.</p>
        </div>
      </div>
      <pre class="answer-block">${escapeHtml(actualAnswer)}</pre>
      ${
        primary?.expected_answer_text
          ? `<div class="detail-subsection">
              <div class="detail-subsection-header">
                <h4>Expected Answer</h4>
              </div>
              <pre class="answer-block">${escapeHtml(primary.expected_answer_text)}</pre>
            </div>`
          : ""
      }
      ${
        sources.length
          ? `<div class="detail-subsection">
              <div class="detail-subsection-header">
                <h4>Sources</h4>
              </div>
              <div class="chip-row">${buildChipList(sources, "neutral")}</div>
            </div>`
          : ""
      }
      ${
        citations.length
          ? buildTableSection("Citations", citations, {
              columns: ["title", "source_url", "chunk_id", "heading"],
              emptyLabel: "No citations captured for this answer.",
            })
          : ""
      }
    </section>
  `;
}

function buildCaseDetailFailureAndPolicy(primary) {
  const items = [
    { label: "Failure Stage", value: primary?.failure_stage },
    { label: "Failure Bucket", value: primary?.failure_bucket },
    { label: "Failure Type", value: primary?.failure_type },
    { label: "Policy Followed", value: primary?.response_policy_followed },
    { label: "Matched Expected Action", value: primary?.matched_expected_execution_action },
    { label: "Used Prohibited Agora Docs", value: primary?.used_prohibited_agora_docs },
    { label: "Abstained Or Deflected Properly", value: primary?.abstained_or_deflected_properly },
    { label: "No Unsupported Claims", value: primary?.no_unsupported_claims },
    { label: "Authoritative Source Used", value: primary?.authoritative_source_used },
    { label: "Citation Present", value: primary?.citation_present },
    { label: "Unsupported Claim Avoidance", value: primary?.unsupported_claim_avoidance },
  ].filter((item) => item.value !== undefined && item.value !== null && item.value !== "");
  return `
    <section class="panel-card detail-surface">
      <div class="panel-header">
        <div>
          <h3>Failure And Policy</h3>
          <p>Bucket the miss first, then verify the route-level behavior checks.</p>
        </div>
      </div>
      ${buildDefinitionGrid(items)}
      <div class="chip-row">
        ${buildChipList(primary?.root_cause_labels || [], "warning")}
      </div>
    </section>
  `;
}

function buildExpectedEvidenceSection(primary) {
  const documentIds = normalizeStringList(primary?.expected_document_ids || []);
  const headingPaths = normalizeStringList(primary?.expected_heading_paths || []);
  const evidenceRefs = Array.isArray(primary?.expected_evidence_refs) ? primary.expected_evidence_refs : [];
  if (!documentIds.length && !headingPaths.length && !evidenceRefs.length) {
    return "";
  }
  return `
    <section class="detail-subsection">
      <div class="detail-subsection-header">
        <h4>Expected Evidence</h4>
      </div>
      ${documentIds.length ? `<div class="chip-row">${buildChipList(documentIds.map((item) => `doc:${item}`), "neutral")}</div>` : ""}
      ${headingPaths.length ? `<div class="chip-row">${buildChipList(headingPaths, "neutral")}</div>` : ""}
      ${
        evidenceRefs.length
          ? buildTableSection("Evidence Refs", evidenceRefs, {
              columns: ["chunk_id", "doc_id", "heading", "evidence_polarity"],
              emptyLabel: "No evidence refs stored for this benchmark case.",
            })
          : ""
      }
    </section>
  `;
}

function buildCaseDetailEvidence(primary) {
  const isWebCase =
    normalizeString(primary?.actual_execution_action) === "web_search" ||
    normalizeString(primary?.expected_route_family) === "web_company_info";
  const summaryItems = [
    { label: "Vector Candidates", value: primary?.vector_candidates_count },
    { label: "BM25 Candidates", value: primary?.bm25_candidates_count },
    { label: "Reranked Candidates", value: primary?.reranked_candidates_count },
    { label: "Selected Docs", value: primary?.selected_doc_count },
    { label: "Top1 Similarity", value: primary?.top1_similarity_score },
    { label: "Avg Selected Similarity", value: primary?.avg_selected_similarity_score },
    { label: "Citation Count", value: primary?.citation_count },
    { label: "Citation Coverage", value: primary?.citation_coverage_ratio },
  ].filter((item) => item.value !== undefined && item.value !== null && item.value !== "");

  if (isWebCase) {
    return `
      <section class="panel-card detail-surface">
        <div class="panel-header">
          <div>
            <h3>Evidence / Trace</h3>
            <p>Web-grounded trace snapshot for a company-info style case.</p>
          </div>
        </div>
        ${summaryItems.length ? buildDefinitionGrid(summaryItems) : `<div class="empty-state">No web trace summary captured for this case.</div>`}
        ${
          (primary?.answer_citations || []).length
            ? buildTableSection("Web Snapshot", primary.answer_citations || [], {
                columns: ["title", "source_url"],
                emptyLabel: "No web citations captured for this case.",
              })
            : `<div class="empty-state">No web snapshot captured for this case.</div>`
        }
      </section>
    `;
  }

  return `
    <section class="panel-card detail-surface">
      <div class="panel-header">
        <div>
          <h3>Evidence / Trace</h3>
          <p>Retrieved candidates, selected contexts, and stored benchmark evidence anchors.</p>
        </div>
      </div>
      ${summaryItems.length ? buildDefinitionGrid(summaryItems) : `<div class="empty-state">No trace counters captured for this sample.</div>`}
      ${buildExpectedEvidenceSection(primary)}
      ${buildCandidateTable(primary?.candidates || [])}
      <section class="detail-subsection">
        <div class="detail-subsection-header">
          <h4>Selected Contexts</h4>
        </div>
        ${buildContextCards(primary?.selected_contexts || [])}
      </section>
    </section>
  `;
}

function buildCaseDetailQuality(primary) {
  const items = [
    { label: "Faithfulness", value: primary?.faithfulness_score },
    { label: "Groundedness", value: primary?.groundedness_score },
    { label: "Response Relevance", value: primary?.response_relevance_score },
    { label: "Response Completeness", value: primary?.response_completeness_score },
    { label: "Citation Correctness", value: primary?.citation_correctness_score },
    { label: "Answer Accuracy", value: primary?.answer_accuracy_score },
    { label: "Answer Logic", value: primary?.answer_logic_score },
    { label: "Document Relevance", value: primary?.document_relevance_score },
    { label: "Hallucination Flag", value: primary?.hallucination_flag },
  ].filter((item) => item.value !== undefined && item.value !== null && item.value !== "");
  return `
    <section class="panel-card detail-surface">
      <div class="panel-header">
        <div>
          <h3>Judge / Quality</h3>
          <p>Judged answer quality and grounding signals captured for this case.</p>
        </div>
      </div>
      ${buildDefinitionGrid(items)}
      ${
        (primary?.judge_votes || []).length
          ? buildTableSection("Judge Votes", primary.judge_votes || [], {
              emptyLabel: "No judge votes stored for this sample.",
            })
          : ""
      }
    </section>
  `;
}

function renderCaseDetailSurface(detailPayload = {}, options = {}) {
  const primary = detailPayload.primary || null;
  const baseline = detailPayload.baseline || null;
  const deltas = detailPayload.deltas || null;
  if (!primary) {
    return `<div class="empty-state">${escapeHtml(options.emptyLabel || "Choose a case to inspect.")}</div>`;
  }
  return [
    buildCaseDetailHeader(primary, baseline, deltas),
    buildCaseDetailRouteContract(primary, baseline),
    buildCaseDetailAnswer(primary),
    buildCaseDetailFailureAndPolicy(primary),
    buildCaseDetailEvidence(primary),
    buildCaseDetailQuality(primary),
    primary?.related_ingestion_ids?.length
      ? `
        <section class="panel-card detail-surface">
          <div class="panel-header">
            <div>
              <h3>Related Ingestion</h3>
              <p>Jump to raw ingestion reports for the chunks used in this case.</p>
            </div>
          </div>
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
        </section>
      `
      : "",
  ].join("");
}

function buildCaseResultsTable(rows) {
  const items = Array.isArray(rows) ? rows : [];
  if (!items.length) {
    return `
      <section class="panel-card">
        <div class="panel-header">
          <div>
            <h3>Case Results</h3>
            <p>Every benchmark row includes the question, the actual answer, the expected answer, and route outcome.</p>
          </div>
        </div>
        <div class="empty-state">No case results available for this scorecard scope.</div>
      </section>
    `;
  }
  return `
    <section class="panel-card">
      <div class="panel-header">
        <div>
          <h3>Case Results</h3>
          <p>Every benchmark row includes the question, the actual answer, the expected answer, and per-case metrics.</p>
        </div>
      </div>
      <div class="table-scroll">
        <table class="data-table">
          <thead>
            <tr>
              <th>Question</th>
              <th>Actual Answer</th>
              <th>Expected Answer</th>
              <th>Answer Accuracy</th>
              <th>Evidence Hit@5</th>
              <th>Failure Type</th>
              <th>Route Correct</th>
              <th>Inspect</th>
            </tr>
          </thead>
          <tbody>
            ${items
              .map((row) => {
                const route_correct =
                  row.route_correct !== undefined && row.route_correct !== null
                    ? row.route_correct
                    : row.route_correct_flag;
                const actual_answer_preview = row.actual_answer_preview || row.answer_preview || "-";
                const expected_answer_preview = row.expected_answer_preview || row.reference_answer || "-";
                return `
                  <tr>
                    <td>
                      <strong>${escapeHtml(row.question || "-")}</strong>
                      <div class="metric-meta">
                        ${escapeHtml(humanizeLabel(row.expected_route || row.expected_execution_action || "rag"))}
                        →
                        ${escapeHtml(humanizeLabel(row.actual_route || row.actual_execution_action || "rag"))}
                      </div>
                    </td>
                    <td>${escapeHtml(actual_answer_preview)}</td>
                    <td>${escapeHtml(expected_answer_preview)}</td>
                    <td>${formatMetricValue(row.answer_accuracy_score, "answer_accuracy_score")}</td>
                    <td>${formatMetricValue(
                      row.evidence_hit_at_5 !== null && row.evidence_hit_at_5 !== undefined ? row.evidence_hit_at_5 : row.hit_at_5,
                      "evidence_hit_at_5"
                    )}</td>
                    <td>${escapeHtml(row.failure_type || "-")}</td>
                    <td>${formatMetricValue(route_correct, "route_correct")}</td>
                    <td>
                      <button
                        type="button"
                        class="table-action-button"
                        data-open-diagnosis-benchmark="${escapeHtml(row.eval_run_id || "")}"
                        data-open-test-case="${escapeHtml(row.test_case_id || "")}"
                      >
                        Inspect
                      </button>
                    </td>
                  </tr>
                `;
              })
              .join("")}
          </tbody>
        </table>
      </div>
    </section>
  `;
}

function renderExperimentsPage(payload) {
  const root = ragPageContainers.experiments.root;
  const sections = payload.sections || {};
  const summary = sections.summary || {};
  const leaderboard = sections.leaderboard?.rows || [];
  const metricRows = sections.metric_matrix?.rows || [];
  const segmentGroups = sections.segment_breakdown?.groups || [];
  const sampleList = sections.sample_list || {};

  root.innerHTML = `
    <section class="hero-card">
      <div class="hero-copy">
        <p class="eyebrow">Offline Benchmark First</p>
        <h2>${escapeHtml(summary.title || "Experiments")}</h2>
        <p>${escapeHtml(summary.subtitle || "Choose the better experiment before you inspect regressions.")}</p>
      </div>
      ${buildExperimentComparisonControls(summary, payload.benchmark_selector)}
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
            "answer_accuracy_score_avg",
            "answer_logic_score_avg",
            "evidence_hit_at_5",
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

  const candidateSelect = document.getElementById("candidate-experiment-selector");
  if (candidateSelect) {
    candidateSelect.value = summary.candidate_experiment_id || "";
  }
}

function renderScorecardPage(payload) {
  const root = ragPageContainers["scorecard"].root;
  const sections = payload.sections || {};
  const summary = sections.summary || {};
  const layerScorecard = sections.layer_scorecard?.rows || [];
  const categoryPassRate = sections.category_pass_rate?.rows || [];
  const caseResults = sections.case_results?.rows || [];
  const sampleList = sections.sample_list || {};

  root.innerHTML = `
    <section class="hero-card">
      <div class="hero-copy">
        <p class="eyebrow">Four-Layer Scorecard</p>
        <h2>${escapeHtml(summary.title || "Scorecard")}</h2>
        <p>${escapeHtml(summary.subtitle || "Read routing, retrieval, generation, and business outcomes together.")}</p>
      </div>
      ${buildExperimentComparisonControls(summary, payload.benchmark_selector)}
      ${buildMetricCards(summary.cards || {})}
    </section>
    ${buildBenchmarkSessionPanel(payload.benchmark_session)}
    ${buildTableSection("Layer Scorecard", layerScorecard, {
      columns: ["layer", "metric", "candidate", "baseline", "delta"],
      emptyLabel: "No scorecard layers available yet.",
    })}
    ${buildTableSection("Category Pass Rate", categoryPassRate, {
      columns: ["category", "case_count", "pass_rate"],
      emptyLabel: "No category pass data available yet.",
    })}
    <div class="two-column-grid">
      ${buildSampleList("Top Regressions", sampleList.top_regressions || [], "danger")}
      ${buildSampleList("Top Wins", sampleList.top_wins || [], "success")}
    </div>
    ${buildCaseResultsTable(caseResults)}
  `;

  const candidateSelect = document.getElementById("candidate-experiment-selector");
  if (candidateSelect) {
    candidateSelect.value = summary.candidate_experiment_id || "";
  }
}

function renderRoutingPage(payload) {
  const root = ragPageContainers["routing"].root;
  const sections = payload.sections || {};
  const summary = sections.summary || {};
  const categoryPassRate = sections.category_pass_rate?.rows || [];
  const sampleList = sections.sample_list || {};
  const routingCases = sections.routing_cases || {};
  const incorrectRows = routingCases.incorrect?.rows || [];
  const correctRows = routingCases.correct?.rows || [];
  const baselineEvalRunId = summary.baseline_eval_run_id || "";

  root.innerHTML = `
    <section class="hero-card">
      <div class="hero-copy">
        <p class="eyebrow">Route Before Rank</p>
        <h2>${escapeHtml(summary.title || "Routing")}</h2>
        <p>${escapeHtml(summary.subtitle || "Audit domain classification separately from retrieval and generation.")}</p>
      </div>
      ${buildMetricCards(summary.cards || {}, {
        formatters: buildRoutingSummaryCardFormatters(summary.cards || {}),
      })}
    </section>
    ${buildBenchmarkSessionPanel(payload.benchmark_session)}
    ${buildTableSection("Per Category Route Health", categoryPassRate, {
      columns: ["category", "case_count", "pass_rate"],
      emptyLabel: "No routing slices available yet.",
    })}
    ${buildRoutingCaseExplorerSection("Routing Errors", incorrectRows, {
      subtitle: "Every case where the route family diverged from the benchmark contract.",
      tone: "danger",
      open: true,
      baselineEvalRunId,
    })}
    ${buildRoutingCaseExplorerSection("Routing Correct", correctRows, {
      subtitle: "Cases where the route family matched the benchmark contract.",
      tone: "success",
      open: true,
      baselineEvalRunId,
    })}
    ${buildCollapsiblePanel({
      title: "Legacy Compare Lists",
      subtitle: "Keep the old regression and win cards as a secondary compare aid.",
      count: (sampleList.top_regressions || []).length + (sampleList.top_wins || []).length,
      open: false,
      tone: "neutral",
      extraClass: "legacy-compare-panel",
      body: `
        <div class="two-column-grid">
          ${buildSampleList("Top Regressions", sampleList.top_regressions || [], "danger")}
          ${buildSampleList("Top Wins", sampleList.top_wins || [], "success")}
        </div>
      `,
    })}
  `;
}

function renderRetrievalDashboardPage(payload) {
  const root = ragPageContainers["retrieval"].root;
  const sections = payload.sections || {};
  const summary = sections.summary || {};
  const sampleList = sections.sample_list || {};
  const retrievalCases = sections.retrieval_cases || {};
  const incorrectRows = retrievalCases.incorrect?.rows || [];
  const correctRows = retrievalCases.correct?.rows || [];
  const baselineEvalRunId = summary.baseline_eval_run_id || "";

  root.innerHTML = `
    <section class="hero-card">
      <div class="hero-copy">
        <p class="eyebrow">Evidence First</p>
        <h2>${escapeHtml(summary.title || "Retrieval")}</h2>
        <p>${escapeHtml(summary.subtitle || "Check whether the right chunks arrived before blaming synthesis.")}</p>
      </div>
      ${buildMetricCards(summary.cards || {})}
    </section>
    ${buildBenchmarkSessionPanel(payload.benchmark_session)}
    ${buildCaseExplorerSection("Retrieval Errors", incorrectRows, {
      subtitle: "Every retrieval-eligible case where the miss was attributed to retrieval.",
      tone: "danger",
      open: true,
      baselineEvalRunId,
      renderRow: buildRetrievalCaseRow,
    })}
    ${buildCaseExplorerSection("Retrieval Correct", correctRows, {
      subtitle: "Retrieval-eligible cases that did not fail at the retrieval stage.",
      tone: "success",
      open: true,
      baselineEvalRunId,
      renderRow: buildRetrievalCaseRow,
    })}
    ${buildCollapsiblePanel({
      title: "Legacy Compare Lists",
      subtitle: "Keep the old regression and win cards as a secondary compare aid.",
      count: (sampleList.top_regressions || []).length + (sampleList.top_wins || []).length,
      open: false,
      tone: "neutral",
      extraClass: "legacy-compare-panel",
      body: `
        <div class="two-column-grid">
          ${buildSampleList("Retrieval Misses", sampleList.top_regressions || [], "danger")}
          ${buildSampleList("Retrieval Wins", sampleList.top_wins || [], "success")}
        </div>
      `,
    })}
  `;
}

function renderGenerationDashboardPage(payload) {
  const root = ragPageContainers["generation"].root;
  const sections = payload.sections || {};
  const summary = sections.summary || {};
  const sampleList = sections.sample_list || {};
  const generationCases = sections.generation_cases || {};
  const incorrectRows = generationCases.incorrect?.rows || [];
  const correctRows = generationCases.correct?.rows || [];
  const baselineEvalRunId = summary.baseline_eval_run_id || "";

  root.innerHTML = `
    <section class="hero-card">
      <div class="hero-copy">
        <p class="eyebrow">Answer Quality</p>
        <h2>${escapeHtml(summary.title || "Generation")}</h2>
        <p>${escapeHtml(summary.subtitle || "Track correctness, relevance, faithfulness, and policy adherence.")}</p>
      </div>
      ${buildMetricCards(summary.cards || {})}
    </section>
    ${buildBenchmarkSessionPanel(payload.benchmark_session)}
    ${buildCaseExplorerSection("Generation Errors", incorrectRows, {
      subtitle: "Cases where answer quality or policy behavior failed after routing.",
      tone: "danger",
      open: true,
      baselineEvalRunId,
      renderRow: buildGenerationCaseRow,
    })}
    ${buildCaseExplorerSection("Generation Correct", correctRows, {
      subtitle: "Generation-eligible cases that did not fail at generation or business policy.",
      tone: "success",
      open: true,
      baselineEvalRunId,
      renderRow: buildGenerationCaseRow,
    })}
    ${buildCollapsiblePanel({
      title: "Legacy Compare Lists",
      subtitle: "Keep the old regression and win cards as a secondary compare aid.",
      count: (sampleList.top_regressions || []).length + (sampleList.top_wins || []).length,
      open: false,
      tone: "neutral",
      extraClass: "legacy-compare-panel",
      body: `
        <div class="two-column-grid">
          ${buildSampleList("Generation Regressions", sampleList.top_regressions || [], "danger")}
          ${buildSampleList("Generation Wins", sampleList.top_wins || [], "success")}
        </div>
      `,
    })}
  `;
}

function renderDataSupplyPage(payload) {
  const root = ragPageContainers["data-supply"].root;
  const sections = payload.sections || {};
  const summary = sections.summary || {};
  const benchmarkSupply = sections.benchmark_supply || {};
  const knowledgeSupply = sections.knowledge_supply || {};
  const currentBenchmarkVersion = normalizeString(
    payload.benchmark_selector?.current_benchmark_version || summary.benchmark_version
  );
  const datasetVersions = benchmarkSupply.dataset_versions?.rows || [];
  const mirroredByBenchmarkVersion = new Map(
    datasetVersions.map((row) => [normalizeString(row.benchmark_version), row])
  );
  const localCatalogRows = LOCAL_BENCHMARK_CATALOG.map((entry) => ({
    ...entry,
    mirror: mirroredByBenchmarkVersion.get(normalizeString(entry.benchmark_version)) || null,
    isCurrent: currentBenchmarkVersion && normalizeString(entry.benchmark_version) === currentBenchmarkVersion,
  }));

  root.innerHTML = `
    <section class="hero-card">
      <div class="hero-copy">
        <p class="eyebrow">Benchmark And Knowledge Inputs</p>
        <h2>${escapeHtml(summary.title || "Data Supply")}</h2>
        <p>${escapeHtml(summary.subtitle || "Keep benchmark quality and knowledge-base health separate.")}</p>
      </div>
      ${buildMetricCards(summary.cards || {})}
    </section>
    ${buildBenchmarkSessionPanel(payload.benchmark_session)}
    <section class="panel-card">
      <div class="panel-header">
        <div>
          <h3>Benchmark Supply</h3>
          <p>Use local benchmark files as the source of truth, then mirror them into dataset inventory tables for audit and coverage.</p>
        </div>
      </div>
      <div class="benchmark-sync-card">
        <div class="benchmark-sync-copy">
          <p class="eyebrow">Local Benchmark Catalog</p>
          <h4>Mirror local benchmark files into dataset tables</h4>
          <p>Benchmark execution still reads local files directly. Sync only refreshes dataset inventory so Data Supply and audit views stay populated.</p>
        </div>
        <div class="button-row">
          <button type="button" class="primary-button" data-sync-local-benchmarks>
            Sync Local Benchmarks
          </button>
        </div>
      </div>
      <div class="sample-list benchmark-catalog-list">
        ${localCatalogRows
          .map(
            (row) => `
              <article class="sample-item ${row.isCurrent ? "benchmark-catalog-current" : ""}">
                <div class="sample-item-header">
                  <span class="chip chip-neutral">${escapeHtml(row.label)}</span>
                  <span class="chip chip-neutral">${escapeHtml(row.isCurrent ? "current run" : "catalog")}</span>
                </div>
                <h4>${escapeHtml(row.benchmark_version)}</h4>
                <div class="sample-meta">
                  <span>${escapeHtml(row.source_path)}</span>
                </div>
                ${buildDefinitionGrid([
                  { label: "Mirror Dataset", value: row.mirror?.dataset_name || "-" },
                  { label: "Dataset Id", value: row.mirror?.dataset_id || "-" },
                  { label: "Gold Items", value: row.mirror?.gold_item_count },
                  { label: "Pending Review", value: row.mirror?.pending_review_count },
                ])}
              </article>
            `
          )
          .join("")}
      </div>
      ${buildTableSection("Sync Runs", benchmarkSupply.generation_runs?.rows || [], {
        emptyLabel: "No local benchmark sync runs available yet.",
      })}
      ${buildTableSection("Dataset Versions", datasetVersions, {
        emptyLabel: "No mirrored dataset versions available yet.",
      })}
      ${buildTableSection("Coverage", benchmarkSupply.coverage?.rows || [], {
        emptyLabel: "No mirrored benchmark coverage available yet.",
      })}
    </section>
    <section class="panel-card">
      <div class="panel-header">
        <div>
          <h3>Knowledge Supply</h3>
          <p>Ingestion health, chunk quality, and index freshness.</p>
        </div>
      </div>
      ${buildMetricCards(knowledgeSupply.summary?.cards || {})}
      ${(knowledgeSupply.segment_breakdown?.groups || []).map(buildGroupBlock).join("")}
    </section>
  `;
}

function renderDatasetsPage(payload) {
  const root = ragPageContainers.datasets.root;
  const sections = payload.sections || {};
  const summary = sections.summary || {};
  const generationRuns = sections.generation_runs || {};
  const datasetVersions = sections.dataset_versions || {};
  const coverage = sections.coverage || {};

  root.innerHTML = `
    <section class="hero-card">
      <div class="hero-copy">
        <p class="eyebrow">Eval Dataset Factory</p>
        <h2>${escapeHtml(summary.title || "Datasets")}</h2>
        <p>${escapeHtml(summary.subtitle || "Generate silver candidates, promote gold items through review, and launch benchmark runs from a fixed snapshot.")}</p>
      </div>
      ${buildMetricCards(summary.cards || {})}
    </section>
    <section class="panel-card">
      <div class="panel-header">
        <div>
          <h3>New Generation Run</h3>
          <p>Create a fresh dataset snapshot from active official and technical knowledge sources.</p>
        </div>
      </div>
      <div class="filter-grid">
        <label class="filter-field">
          <span>Dataset Name</span>
          <input id="dataset-generation-name" type="text" placeholder="supportportal_gold_v1" />
        </label>
        <label class="filter-field">
          <span>Question Language</span>
          <select id="dataset-generation-language">
            <option value="en" selected>English</option>
          </select>
        </label>
        <label class="filter-field">
          <span>Source Types</span>
          <div class="chip-row">
            <label class="chip chip-neutral">
              <input id="dataset-source-official" type="checkbox" checked />
              Official Markdown
            </label>
            <label class="chip chip-neutral">
              <input id="dataset-source-technical" type="checkbox" checked />
              Technical Article
            </label>
          </div>
        </label>
        <div class="button-row">
          <button type="button" class="primary-button" data-create-dataset-generation>
            Start Generation
          </button>
        </div>
      </div>
    </section>
    ${buildDatasetGenerationRuns(generationRuns.rows || [])}
    ${buildDatasetVersionCards(datasetVersions.rows || [])}
    ${buildTableSection("Coverage", coverage.rows || [], {
      emptyLabel: "No dataset coverage available yet.",
    })}
  `;
}

function renderDiagnosisPage(payload) {
  const root = ragPageContainers.diagnosis.root;
  const sections = payload.sections || {};
  const summary = sections.summary || {};
  const sampleList = sections.sample_list || {};
  const selectedListKey = summary.selected_list_key || "top_regressions";

  root.innerHTML = `
    ${buildBenchmarkSessionPanel(payload.benchmark_session)}
    <div class="diagnosis-layout">
      <div class="diagnosis-chooser-stack">
        ${buildDiagnosisChooserSection("Top Regressions", sampleList.top_regressions || [], "danger", {
          open: selectedListKey === "top_regressions",
          subtitle: "Benchmark regressions sorted by quality delta.",
        })}
        ${buildDiagnosisChooserSection("Risky Live Queries", sampleList.risky_live_queries || [], "warning", {
          open: selectedListKey === "risky_live_queries",
          subtitle: "Live traffic that looks risky enough to inspect manually.",
        })}
        ${buildDiagnosisChooserSection("Review Queue", sampleList.review_queue || [], "neutral", {
          open: selectedListKey === "review_queue",
          subtitle: "Pending reviewed samples that may need manual correction or label fixes.",
        })}
      </div>
      <div id="diagnosis-detail-surface" class="detail-surface-stack">
        <div class="empty-state">Loading selected case detail...</div>
      </div>
    </div>
  `;

  void loadDiagnosisDetailSurface(payload);
}

function openCaseDetailModalShell(title, openDiagnosisState) {
  activeCaseDetailState = openDiagnosisState || null;
  lastCaseDetailFocusEl = document.activeElement instanceof HTMLElement ? document.activeElement : null;
  caseDetailTitleEl.textContent = title || "Case detail";
  caseDetailBodyEl.innerHTML = `<div class="empty-state">Loading case detail...</div>`;
  caseDetailModalEl.hidden = false;
  caseDetailModalEl.setAttribute("aria-hidden", "false");
  document.body.classList.add("modal-open");
  if (caseDetailDiagnosisButtonEl) {
    caseDetailDiagnosisButtonEl.disabled = !activeCaseDetailState;
  }
  window.setTimeout(() => {
    caseDetailDialogEl?.focus();
  }, 0);
}

function closeCaseDetailModal() {
  caseDetailModalEl.hidden = true;
  caseDetailModalEl.setAttribute("aria-hidden", "true");
  document.body.classList.remove("modal-open");
  caseDetailBodyEl.innerHTML = "";
  activeCaseDetailState = null;
  if (lastCaseDetailFocusEl) {
    lastCaseDetailFocusEl.focus();
  }
}

function focusableElementsWithin(container) {
  if (!container) {
    return [];
  }
  return Array.from(
    container.querySelectorAll(
      'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'
    )
  ).filter((element) => element instanceof HTMLElement && !element.hidden);
}

function trapCaseDetailFocus(event) {
  if (caseDetailModalEl.hidden || event.key !== "Tab") {
    return;
  }
  const focusable = focusableElementsWithin(caseDetailDialogEl);
  if (!focusable.length) {
    event.preventDefault();
    caseDetailDialogEl?.focus();
    return;
  }
  const first = focusable[0];
  const last = focusable[focusable.length - 1];
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault();
    last.focus();
    return;
  }
  if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault();
    first.focus();
  }
}

async function openCaseDetailModalForBenchmark(evalRunId, testCaseId, baselineEvalRunId = "") {
  const normalizedEvalRunId = normalizeString(evalRunId);
  const normalizedTestCaseId = normalizeString(testCaseId);
  if (!normalizedEvalRunId || !normalizedTestCaseId) {
    return;
  }
  openCaseDetailModalShell("Benchmark Case Detail", {
    source: "benchmark",
    evalRunId: normalizedEvalRunId,
    testCaseId: normalizedTestCaseId,
  });
  const token = ++caseDetailLoadToken;
  try {
    const payload = await fetchBenchmarkCaseDetail(normalizedEvalRunId, normalizedTestCaseId, baselineEvalRunId);
    if (token !== caseDetailLoadToken) {
      return;
    }
    caseDetailTitleEl.textContent = payload?.primary?.question || payload?.primary?.user_query || "Benchmark Case Detail";
    caseDetailBodyEl.innerHTML = renderCaseDetailSurface(payload, {
      emptyLabel: "No benchmark detail available for this case.",
    });
  } catch (error) {
    if (token !== caseDetailLoadToken) {
      return;
    }
    caseDetailBodyEl.innerHTML = `<div class="empty-state">Failed to load case detail: ${escapeHtml(error.message)}</div>`;
  }
}

async function openCaseDetailModalForLive(requestId) {
  const normalizedRequestId = normalizeString(requestId);
  if (!normalizedRequestId) {
    return;
  }
  openCaseDetailModalShell("Live Query Detail", {
    source: "live_query",
    requestId: normalizedRequestId,
  });
  const token = ++caseDetailLoadToken;
  try {
    const payload = await fetchLiveCaseDetail(normalizedRequestId);
    if (token !== caseDetailLoadToken) {
      return;
    }
    caseDetailTitleEl.textContent = payload?.primary?.question || payload?.primary?.user_query || "Live Query Detail";
    caseDetailBodyEl.innerHTML = renderCaseDetailSurface(payload, {
      emptyLabel: "No live detail available for this query.",
    });
  } catch (error) {
    if (token !== caseDetailLoadToken) {
      return;
    }
    caseDetailBodyEl.innerHTML = `<div class="empty-state">Failed to load case detail: ${escapeHtml(error.message)}</div>`;
  }
}

async function loadDiagnosisDetailSurface(payload) {
  const root = document.getElementById("diagnosis-detail-surface");
  if (!root) {
    return;
  }
  const summary = payload?.sections?.summary || {};
  const selectedSource = normalizeString(summary.selected_source);
  const selectedEvalRunId = normalizeString(summary.selected_eval_run_id);
  const selectedTestCaseId = normalizeString(summary.selected_test_case_id);
  const selectedRequestId = normalizeString(summary.selected_request_id);
  const baselineEvalRunId = normalizeString(summary.baseline_eval_run_id);

  if (!selectedSource) {
    root.innerHTML = `<div class="empty-state">Choose a regression, risky live query, or review sample to inspect.</div>`;
    return;
  }

  root.innerHTML = `<div class="empty-state">Loading selected case detail...</div>`;
  const token = ++diagnosisDetailLoadToken;
  try {
    const detailPayload =
      selectedSource === "live_query"
        ? await fetchLiveCaseDetail(selectedRequestId)
        : await fetchBenchmarkCaseDetail(selectedEvalRunId, selectedTestCaseId, baselineEvalRunId);
    if (token !== diagnosisDetailLoadToken) {
      return;
    }
    root.innerHTML = renderCaseDetailSurface(detailPayload, {
      emptyLabel: "No diagnosis detail available for the selected case.",
    });
  } catch (error) {
    if (token !== diagnosisDetailLoadToken) {
      return;
    }
    root.innerHTML = `<div class="empty-state">Failed to load selected case detail: ${escapeHtml(error.message)}</div>`;
  }
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
                        { label: "Difficulty", value: row.difficulty },
                        { label: "Dataset Item Status", value: row.dataset_item_status },
                        { label: "Retrieval Strategy", value: row.retrieval_strategy },
                        { label: "Faithfulness", value: row.faithfulness_score },
                        { label: "Groundedness", value: row.groundedness_score },
                        { label: "Citation Correctness", value: row.citation_correctness_score },
                        { label: "Answer Accuracy", value: row.answer_accuracy_score },
                        { label: "Answer Logic", value: row.answer_logic_score },
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
                        <label class="filter-field">
                          <span>Logic OK</span>
                          <select data-review-logic="${escapeHtml(row.sample_id || "")}">
                            <option value="" ${row.logic_ok === null || row.logic_ok === undefined ? "selected" : ""}>Unset</option>
                            <option value="true" ${row.logic_ok === true ? "selected" : ""}>Yes</option>
                            <option value="false" ${row.logic_ok === false ? "selected" : ""}>No</option>
                          </select>
                        </label>
                        <label class="filter-field">
                          <span>Hallucination Present</span>
                          <select data-review-hallucination="${escapeHtml(row.sample_id || "")}">
                            <option value="" ${row.hallucination_present === null || row.hallucination_present === undefined ? "selected" : ""}>Unset</option>
                            <option value="true" ${row.hallucination_present === true ? "selected" : ""}>Yes</option>
                            <option value="false" ${row.hallucination_present === false ? "selected" : ""}>No</option>
                          </select>
                        </label>
                        <label class="filter-field">
                          <span>Dataset Decision</span>
                          <select data-review-dataset-decision="${escapeHtml(row.sample_id || "")}">
                            <option value="" ${!row.dataset_decision ? "selected" : ""}>Unset</option>
                            <option value="promote_gold" ${row.dataset_decision === "promote_gold" ? "selected" : ""}>Promote Gold</option>
                            <option value="keep_silver" ${row.dataset_decision === "keep_silver" ? "selected" : ""}>Keep Silver</option>
                            <option value="needs_fix" ${row.dataset_decision === "needs_fix" ? "selected" : ""}>Needs Fix</option>
                            <option value="reject" ${row.dataset_decision === "reject" ? "selected" : ""}>Reject</option>
                          </select>
                        </label>
                      </div>
                      <label class="review-note-field">
                        <span>Corrected Reference Answer</span>
                        <textarea data-review-reference-answer="${escapeHtml(row.sample_id || "")}" rows="4">${escapeHtml(
                          row.corrected_reference_answer || ""
                        )}</textarea>
                      </label>
                      <label class="review-note-field">
                        <span>Corrected Citation Targets (JSON array)</span>
                        <textarea data-review-citation-targets="${escapeHtml(row.sample_id || "")}" rows="3">${escapeHtml(
                          JSON.stringify(row.corrected_citation_targets || [])
                        )}</textarea>
                      </label>
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
    ${buildBenchmarkSessionPanel(payload.benchmark_session)}
    ${buildReviewQueue(reviewQueue.pending_rows || [], "Pending First")}
    <div class="two-column-grid">
      ${buildReviewQueue(reviewQueue.benchmark_rows || [], "Benchmark Samples")}
      ${buildReviewQueue(reviewQueue.live_rows || [], "Live Query Samples")}
    </div>
    ${buildReviewQueue(reviewQueue.dataset_rows || [], "Dataset Candidates")}
  `;
}

const pageRenderers = {
  scorecard: { render: renderScorecardPage },
  routing: { render: renderRoutingPage },
  retrieval: { render: renderRetrievalDashboardPage },
  generation: { render: renderGenerationDashboardPage },
  "data-supply": { render: renderDataSupplyPage },
  diagnosis: { render: renderDiagnosisPage },
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
  Object.assign(ragFilters, DEFAULT_RAG_FILTERS);
  const page = normalizeDashboardTab(params.get("page")) || "scorecard";
  currentDashboardTab = page;
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
  currentDashboardTab = normalizeDashboardTab(tabName);
  dashboardTabEls.forEach((button) => {
    button.classList.toggle("active", button.dataset.dashboardTab === currentDashboardTab);
  });
  dashboardPanelEls.forEach((panel) => {
    panel.classList.toggle("active", panel.dataset.dashboardPanel === currentDashboardTab);
  });
  writeStateToUrl();
}

function resetDashboardCache() {
  cacheEpoch += 1;
  Object.keys(pageCache).forEach((key) => delete pageCache[key]);
  Object.keys(pageLoadPromises).forEach((key) => delete pageLoadPromises[key]);
}

function clearBenchmarkCaseSelection() {
  ragFilters.sample_id = "";
  ragFilters.request_id = "";
  ragFilters.eval_run_id = "";
  ragFilters.test_case_id = "";
}

function applyPagePayload(pageName, payload) {
  pageRenderers[pageName]?.render(payload);
  renderBenchmarkRunSelector(payload?.benchmark_selector);
  updateScopeLabel();
  setLastRefreshed(payload?.last_refreshed_at);
}

async function fetchPageData(pageName, { force = false, epoch = cacheEpoch } = {}) {
  if (!force && pageCache[pageName]) {
    return pageCache[pageName];
  }
  if (!force) {
    const existingRequest = pageLoadPromises[pageName];
    if (existingRequest && existingRequest.epoch === epoch) {
      return existingRequest.promise;
    }
  }
  const requestPromise = (async () => {
    const payload = await fetchJson(`/api/dashboard/rag/${pageName}?${buildPageQuery()}`);
    if (epoch !== cacheEpoch) {
      return null;
    }
    pageCache[pageName] = payload;
    return payload;
  })();
  pageLoadPromises[pageName] = { epoch, promise: requestPromise };
  try {
    return await requestPromise;
  } finally {
    if (pageLoadPromises[pageName]?.promise === requestPromise) {
      delete pageLoadPromises[pageName];
    }
  }
}

async function loadPage(pageName, { force = false } = {}) {
  const root = ragPageContainers[pageName]?.root;
  if (!root) {
    return null;
  }
  if (!force && pageCache[pageName]) {
    applyPagePayload(pageName, pageCache[pageName]);
    setStatus(`${PAGE_LABELS[pageName]} ready.`);
    return pageCache[pageName];
  }
  const epoch = cacheEpoch;
  root.innerHTML = `<div class="empty-state">Loading ${escapeHtml(PAGE_LABELS[pageName])}...</div>`;
  setStatus(`Loading ${PAGE_LABELS[pageName]}...`);
  try {
    const payload = await fetchPageData(pageName, { force, epoch });
    if (!payload) {
      return null;
    }
    if (pageName !== currentDashboardTab) {
      return payload;
    }
    applyPagePayload(pageName, payload);
    setStatus(`${PAGE_LABELS[pageName]} ready.`);
    return payload;
  } catch (error) {
    if (pageName !== currentDashboardTab) {
      return null;
    }
    root.innerHTML = `<div class="empty-state">Failed to load ${escapeHtml(PAGE_LABELS[pageName])}: ${escapeHtml(error.message)}</div>`;
    setStatus(`Failed to load ${PAGE_LABELS[pageName]}: ${error.message}`);
    throw error;
  }
}

async function prewarmDashboardPages(activePage = currentDashboardTab) {
  const epoch = cacheEpoch;
  await Promise.allSettled(
    DASHBOARD_PAGE_NAMES.filter((pageName) => pageName !== activePage).map((pageName) =>
      fetchPageData(pageName, { epoch }).catch(() => null)
    )
  );
}

async function loadCurrentPage({ force = false } = {}) {
  writeStateToUrl();
  const payload = await loadPage(currentDashboardTab, { force });
  if (payload) {
    void prewarmDashboardPages(currentDashboardTab);
  }
  return payload;
}

async function refreshDashboardPages({ clearBenchmarkSelection = false } = {}) {
  if (clearBenchmarkSelection) {
    clearBenchmarkCaseSelection();
  }
  resetDashboardCache();
  return loadCurrentPage({ force: true });
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

function openDiagnosisForBenchmark(evalRunId, testCaseId) {
  closeCaseDetailModal();
  ragFilters.request_id = "";
  ragFilters.sample_id = "";
  ragFilters.eval_run_id = normalizeString(evalRunId);
  ragFilters.test_case_id = normalizeString(testCaseId);
  setActiveDashboardTab("diagnosis");
  refreshDashboardPages().catch((error) => {
    setStatus(`Failed to open diagnosis: ${error.message}`);
  });
}

function openDiagnosisForLive(requestId) {
  closeCaseDetailModal();
  ragFilters.eval_run_id = "";
  ragFilters.test_case_id = "";
  ragFilters.sample_id = "";
  ragFilters.request_id = normalizeString(requestId);
  setActiveDashboardTab("diagnosis");
  refreshDashboardPages().catch((error) => {
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
  const logicEl = document.querySelector(`[data-review-logic="${CSS.escape(normalizedSampleId)}"]`);
  const hallucinationEl = document.querySelector(`[data-review-hallucination="${CSS.escape(normalizedSampleId)}"]`);
  const datasetDecisionEl = document.querySelector(`[data-review-dataset-decision="${CSS.escape(normalizedSampleId)}"]`);
  const referenceAnswerEl = document.querySelector(`[data-review-reference-answer="${CSS.escape(normalizedSampleId)}"]`);
  const citationTargetsEl = document.querySelector(`[data-review-citation-targets="${CSS.escape(normalizedSampleId)}"]`);
  const noteEl = document.querySelector(`[data-review-note="${CSS.escape(normalizedSampleId)}"]`);
  let correctedCitationTargets = null;
  if (citationTargetsEl && normalizeString(citationTargetsEl.value)) {
    try {
      const parsed = JSON.parse(citationTargetsEl.value);
      correctedCitationTargets = Array.isArray(parsed) ? parsed : null;
    } catch (error) {
      setStatus(`Failed to save review ${normalizedSampleId}: citation targets must be valid JSON.`);
      return;
    }
  }
  const payload = {
    review_status: statusEl?.value || "pending",
    retrieval_ok: retrievalEl?.value === "" ? null : retrievalEl?.value === "true",
    answer_ok: answerEl?.value === "" ? null : answerEl?.value === "true",
    citation_ok: citationEl?.value === "" ? null : citationEl?.value === "true",
    logic_ok: logicEl?.value === "" ? null : logicEl?.value === "true",
    hallucination_present: hallucinationEl?.value === "" ? null : hallucinationEl?.value === "true",
    dataset_decision: datasetDecisionEl?.value || null,
    corrected_reference_answer: referenceAnswerEl?.value || null,
    corrected_citation_targets: correctedCitationTargets,
    note: noteEl?.value || "",
  };
  setStatus(`Saving review for ${normalizedSampleId}...`);
  try {
    await fetchJson(`/api/dashboard/rag/review-samples/${encodeURIComponent(normalizedSampleId)}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    await refreshDashboardPages();
    setStatus(`Review ${normalizedSampleId} saved.`);
  } catch (error) {
    setStatus(`Failed to save review ${normalizedSampleId}: ${error.message}`);
  }
}

async function createDatasetGenerationRun() {
  const datasetNameEl = document.getElementById("dataset-generation-name");
  const languageEl = document.getElementById("dataset-generation-language");
  const officialSourceEl = document.getElementById("dataset-source-official");
  const technicalSourceEl = document.getElementById("dataset-source-technical");
  const sourceTypes = [];
  if (officialSourceEl?.checked) {
    sourceTypes.push("official_markdown_upload");
  }
  if (technicalSourceEl?.checked) {
    sourceTypes.push("technical_article_api");
  }
  const datasetName = normalizeString(datasetNameEl?.value);
  if (!datasetName) {
    setStatus("Dataset name is required.");
    return;
  }
  if (!sourceTypes.length) {
    setStatus("Select at least one source type.");
    return;
  }
  setStatus(`Starting dataset generation ${datasetName}...`);
  try {
    await fetchJson("/api/dashboard/rag/datasets/generation-runs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        dataset_name: datasetName,
        source_types: sourceTypes,
        question_language: languageEl?.value || "en",
      }),
    });
    await refreshDashboardPages();
    setStatus(`Dataset generation ${datasetName} queued.`);
  } catch (error) {
    setStatus(`Failed to create dataset generation run: ${error.message}`);
  }
}

async function syncLocalBenchmarks() {
  setStatus("Syncing local benchmark catalog into dataset tables...");
  try {
    const payload = await fetchJson("/api/dashboard/rag/benchmarks/local-sync", {
      method: "POST",
    });
    await refreshDashboardPages();
    setStatus(`Local benchmark sync completed: ${formatNumber(payload.synced_count || 0)} datasets mirrored.`);
  } catch (error) {
    setStatus(`Failed to sync local benchmarks: ${error.message}`);
  }
}

async function runDatasetBenchmark(datasetId) {
  const normalizedDatasetId = normalizeString(datasetId);
  if (!normalizedDatasetId) {
    return;
  }
  setStatus(`Queueing benchmark for ${normalizedDatasetId}...`);
  try {
    await fetchJson(`/api/dashboard/rag/datasets/${encodeURIComponent(normalizedDatasetId)}/benchmark-runs`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        experiment_id: ragFilters.experiment_id === "all" ? null : ragFilters.experiment_id,
        tier: "gold",
      }),
    });
    setActiveDashboardTab("scorecard");
    await refreshDashboardPages();
    setStatus(`Benchmark for ${normalizedDatasetId} queued.`);
  } catch (error) {
    setStatus(`Failed to queue benchmark ${normalizedDatasetId}: ${error.message}`);
  }
}

function exportDatasetSnapshot(datasetId) {
  const normalizedDatasetId = normalizeString(datasetId);
  if (!normalizedDatasetId) {
    return;
  }
  window.open(`/api/dashboard/rag/datasets/${encodeURIComponent(normalizedDatasetId)}/export?tier=gold`, "_blank", "noopener");
  setStatus(`Exporting gold snapshot for ${normalizedDatasetId}...`);
}

function openReviewPage() {
  setActiveDashboardTab("review");
  loadCurrentPage().catch((error) => {
    setStatus(`Failed to open review queue: ${error.message}`);
  });
}

function openDatasetsPage() {
  setActiveDashboardTab("data-supply");
  loadCurrentPage().catch((error) => {
    setStatus(`Failed to open data supply page: ${error.message}`);
  });
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
  if (event.target.closest("[data-close-case-detail]")) {
    closeCaseDetailModal();
    return;
  }
  if (event.target.closest("[data-open-full-diagnosis]") && activeCaseDetailState) {
    if (activeCaseDetailState.source === "live_query") {
      openDiagnosisForLive(activeCaseDetailState.requestId);
    } else {
      openDiagnosisForBenchmark(activeCaseDetailState.evalRunId, activeCaseDetailState.testCaseId);
    }
    return;
  }
  if (event.target.closest("[data-close-report]")) {
    closeReportDrawer();
    return;
  }
  const caseDetailBenchmarkButton = event.target.closest("[data-open-case-detail-benchmark]");
  if (caseDetailBenchmarkButton) {
    openCaseDetailModalForBenchmark(
      caseDetailBenchmarkButton.dataset.openCaseDetailBenchmark,
      caseDetailBenchmarkButton.dataset.openCaseDetailTestCase,
      caseDetailBenchmarkButton.dataset.openCaseDetailBaseline
    ).catch((error) => {
      setStatus(`Failed to open case detail: ${error.message}`);
    });
    return;
  }
  const caseDetailLiveButton = event.target.closest("[data-open-case-detail-live]");
  if (caseDetailLiveButton) {
    openCaseDetailModalForLive(caseDetailLiveButton.dataset.openCaseDetailLive).catch((error) => {
      setStatus(`Failed to open case detail: ${error.message}`);
    });
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
  const diagnosisBenchmarkButton = event.target.closest("[data-select-diagnosis-benchmark]");
  if (diagnosisBenchmarkButton) {
    openDiagnosisForBenchmark(
      diagnosisBenchmarkButton.dataset.selectDiagnosisBenchmark,
      diagnosisBenchmarkButton.dataset.selectDiagnosisTestCase
    );
    return;
  }
  const diagnosisLiveButton = event.target.closest("[data-select-diagnosis-live]");
  if (diagnosisLiveButton) {
    openDiagnosisForLive(diagnosisLiveButton.dataset.selectDiagnosisLive);
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
  const createDatasetButton = event.target.closest("[data-create-dataset-generation]");
  if (createDatasetButton) {
    createDatasetGenerationRun();
    return;
  }
  const syncLocalBenchmarksButton = event.target.closest("[data-sync-local-benchmarks]");
  if (syncLocalBenchmarksButton) {
    syncLocalBenchmarks();
    return;
  }
  const runDatasetBenchmarkButton = event.target.closest("[data-run-dataset-benchmark]");
  if (runDatasetBenchmarkButton) {
    runDatasetBenchmark(runDatasetBenchmarkButton.dataset.runDatasetBenchmark);
    return;
  }
  const exportDatasetButton = event.target.closest("[data-export-dataset]");
  if (exportDatasetButton) {
    exportDatasetSnapshot(exportDatasetButton.dataset.exportDataset);
    return;
  }
  if (event.target.closest("[data-open-review-page]")) {
    openReviewPage();
    return;
  }
  if (event.target.closest("[data-open-datasets-review]")) {
    openDatasetsPage();
    return;
  }
}

function handleDocumentChange(event) {
  if (event.target.id === "candidate-experiment-selector") {
    ragFilters.baseline_experiment_id = normalizeString(event.target.value);
    refreshDashboardPages().catch((error) => {
      setStatus(`Failed to update candidate benchmark run: ${error.message}`);
    });
    return;
  }
  if (event.target.id === "current-benchmark-run-selector") {
    const nextCandidateExperimentId = normalizeString(event.target.value);
    ragFilters.candidate_experiment_id = nextCandidateExperimentId;
    if (
      !nextCandidateExperimentId ||
      normalizeString(ragFilters.baseline_experiment_id) === nextCandidateExperimentId
    ) {
      ragFilters.baseline_experiment_id = "";
    }
    refreshDashboardPages({ clearBenchmarkSelection: true }).catch((error) => {
      setStatus(`Failed to update benchmark run: ${error.message}`);
    });
    return;
  }
}

function bindFilters() {
  [ragRangeFilterEl, ragSourceFilterEl, ragQueryTypeFilterEl, ragRetrievalFilterEl, ragChunkFilterEl].forEach((el) => {
    el.addEventListener("change", () => {
      setGlobalFilterState();
      refreshDashboardPages().catch((error) => {
        setStatus(`Failed to apply filters: ${error.message}`);
      });
    });
  });
  [ragProductFilterEl, ragLanguageFilterEl, ragExperimentFilterEl].forEach((el) => {
    el.addEventListener("change", () => {
      setGlobalFilterState();
      refreshDashboardPages().catch((error) => {
        setStatus(`Failed to apply filters: ${error.message}`);
      });
    });
  });
}

function handleDocumentKeydown(event) {
  if (caseDetailModalEl.hidden) {
    return;
  }
  if (event.key === "Escape") {
    closeCaseDetailModal();
    return;
  }
  trapCaseDetailFocus(event);
}

async function initializeDashboard() {
  readStateFromUrl();
  setActiveDashboardTab(currentDashboardTab);
  bindFilters();
  document.addEventListener("click", handleDocumentClick);
  document.addEventListener("change", handleDocumentChange);
  document.addEventListener("keydown", handleDocumentKeydown);
  refreshButtonEl.addEventListener("click", () => {
    refreshDashboardPages().catch((error) => {
      setStatus(`Failed to refresh page: ${error.message}`);
    });
  });
  window.addEventListener("popstate", () => {
    readStateFromUrl();
    setActiveDashboardTab(currentDashboardTab);
    refreshDashboardPages().catch((error) => {
      setStatus(`Failed to restore state: ${error.message}`);
    });
  });
  await loadCurrentPage({ force: true });
}

initializeDashboard().catch((error) => {
  setStatus(`Failed to initialize dashboard: ${error.message}`);
});
