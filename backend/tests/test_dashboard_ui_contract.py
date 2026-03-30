from __future__ import annotations

import re
import unittest
from pathlib import Path


class DashboardUiContractTests(unittest.TestCase):
    def test_root_dashboard_is_ticket_operations_admin_surface(self) -> None:
        source = Path("ui/dashboard-ui/index.html").read_text(encoding="utf-8")
        css = Path("ui/dashboard-ui/styles.css").read_text(encoding="utf-8")

        for required_id in [
            'id="ticket-volume"',
            'id="resolution-rate"',
            'id="sentiment-alerts"',
            'id="waiting-for-engineer"',
            'id="event-stream"',
            'id="header-user-controls"',
            'id="ws-status"',
            'id="event-volume-bars"',
        ]:
            self.assertIn(required_id, source)

        for required_copy in [
            "Admin Operations",
            "AI Managing",
            "Queue Health &amp; Throughput",
            "Escalation Watch",
            "Operator Summary",
            "Live Ticket Feed",
            "Investigating",
        ]:
            self.assertIn(required_copy, source)

        self.assertIn('class="dashboard-rail"', source)
        self.assertIn('class="rail-footer"', source)
        self.assertIn('href="/dashboard/rag/"', source)
        self.assertNotIn('data-dashboard-tab="experiments"', source)
        self.assertNotIn('data-dashboard-tab="overview"', source)
        self.assertIn(".dashboard-rail", css)
        self.assertIn(".rail-footer", css)
        self.assertIn(".queue-health-card", css)
        self.assertIn(".feed-card", css)

    def test_rag_dashboard_nav_uses_scorecard_pages(self) -> None:
        source = Path("ui/dashboard-ui/rag/index.html").read_text(encoding="utf-8")

        expected_tabs = {
            "scorecard": "Scorecard",
            "routing": "Routing",
            "retrieval": "Retrieval",
            "generation": "Generation",
            "data-supply": "Data Supply",
            "diagnosis": "Diagnosis",
            "review": "Review Queue",
        }
        for page_name, label in expected_tabs.items():
            self.assertIn(f'data-dashboard-tab="{page_name}"', source)
            self.assertIn(f">{label}</button>", source)

        self.assertIn('class="dashboard-tab active" data-dashboard-tab="scorecard"', source)

        for legacy_page in [
            "overview",
            "ingestion",
            "chunking",
            "embedding-index",
            "handoff",
            "performance-cost",
            "failures",
            "reports",
        ]:
            self.assertNotIn(f'data-dashboard-tab="{legacy_page}"', source)

    def test_rag_dashboard_app_defaults_to_scorecard_and_registers_new_pages(self) -> None:
        source = Path("ui/dashboard-ui/rag/app.js").read_text(encoding="utf-8")
        self.assertIn('let currentDashboardTab = "scorecard";', source)

        for page_name in [
            "scorecard",
            "routing",
            "retrieval",
            "generation",
            "data-supply",
            "diagnosis",
            "review",
        ]:
            self.assertRegex(
                source,
                rf'["\']{re.escape(page_name)}["\']\s*:\s*\{{',
            )

    def test_rag_dashboard_topbar_exposes_current_benchmark_run_selector_and_prewarm_cache(self) -> None:
        html = Path("ui/dashboard-ui/rag/index.html").read_text(encoding="utf-8")
        source = Path("ui/dashboard-ui/rag/app.js").read_text(encoding="utf-8")

        self.assertIn("Current Benchmark Run", html)
        self.assertIn('id="current-benchmark-run-selector"', html)
        for marker in [
            "cacheEpoch",
            "pageLoadPromises",
            "prewarmDashboardPages",
            "benchmark_selector",
        ]:
            self.assertIn(marker, source)

    def test_rag_data_supply_page_exposes_benchmark_and_knowledge_panels(self) -> None:
        source = Path("ui/dashboard-ui/rag/app.js").read_text(encoding="utf-8")
        for marker in [
            "Benchmark Supply",
            "Knowledge Supply",
            "Sync Local Benchmarks",
            "data-sync-local-benchmarks",
            "Local Benchmark Catalog",
            "Sync Runs",
            "Dataset Versions",
            "Coverage",
        ]:
            self.assertIn(marker, source)

    def test_routing_page_uses_case_explorer_and_legacy_compare_sections(self) -> None:
        source = Path("ui/dashboard-ui/rag/app.js").read_text(encoding="utf-8")
        css = Path("ui/dashboard-ui/rag/styles.css").read_text(encoding="utf-8")

        for marker in [
            "Routing Errors",
            "Routing Correct",
            "Legacy Compare Lists",
            "expected_route_family",
            "actual_route_family",
            "route_family_correct",
        ]:
            self.assertIn(marker, source)

        for marker in [
            "case-explorer-list",
            "case-explorer-item",
            "collapsible-panel",
        ]:
            self.assertIn(marker, css)

    def test_routing_summary_cards_use_percentage_only_overrides(self) -> None:
        source = Path("ui/dashboard-ui/rag/app.js").read_text(encoding="utf-8")

        for marker in [
            "const ROUTING_SUMMARY_PERCENT_KEYS = new Set([",
            "function formatPercentageValue(value) {",
            "function buildMetricCards(cards, options = {}) {",
            "const formatters = options.formatters || {};",
            "const formatter = formatters[key];",
            "ROUTING_SUMMARY_PERCENT_KEYS.has(key) ? formatPercentageValue : null",
            "route_family_accuracy",
            "execution_action_accuracy",
            "tooling_profile_accuracy",
            "false_positive_to_agora_rag",
            "false_negative_for_true_agora_tech",
        ]:
            self.assertIn(marker, source)

    def test_retrieval_and_generation_pages_use_case_explorer_sections(self) -> None:
        source = Path("ui/dashboard-ui/rag/app.js").read_text(encoding="utf-8")

        for marker in [
            "Retrieval Errors",
            "Retrieval Correct",
            "Generation Errors",
            "Generation Correct",
            "retrieval_cases",
            "generation_cases",
            "buildCaseExplorerSection",
        ]:
            self.assertIn(marker, source)

    def test_case_detail_modal_and_diagnosis_single_column_surface_exist(self) -> None:
        html = Path("ui/dashboard-ui/rag/index.html").read_text(encoding="utf-8")
        source = Path("ui/dashboard-ui/rag/app.js").read_text(encoding="utf-8")
        css = Path("ui/dashboard-ui/rag/styles.css").read_text(encoding="utf-8")

        for marker in [
            'id="case-detail-modal"',
            'id="case-detail-title"',
            'id="case-detail-body"',
            'data-close-case-detail',
            'data-open-full-diagnosis',
        ]:
            self.assertIn(marker, html)

        self.assertIn("renderCaseDetailSurface", source)
        self.assertIn("fetchBenchmarkCaseDetail", source)
        self.assertIn("fetchLiveCaseDetail", source)
        self.assertNotIn("diagnosis-grid", source)
        self.assertNotIn(".diagnosis-grid", css)

        for marker in [
            "case-detail-modal",
            "case-detail-dialog",
            "diagnosis-layout",
            "diagnosis-chooser-stack",
            "Benchmark Case Detail",
            "Live Query Detail",
        ]:
            self.assertIn(marker, source if "Detail" in marker else css)

        self.assertNotIn("Routing case detail", source)
        self.assertNotIn("Live query detail", source)

    def test_case_detail_surface_wraps_long_titles_and_definition_values(self) -> None:
        html = Path("ui/dashboard-ui/rag/index.html").read_text(encoding="utf-8")
        css = Path("ui/dashboard-ui/rag/styles.css").read_text(encoding="utf-8")

        self.assertIn('class="case-detail-header-copy"', html)
        self.assertRegex(css, r"\.panel-header\s*>\s*div\s*\{[^}]*min-width:\s*0;")
        self.assertRegex(css, r"\.case-detail-header-copy\s*\{[^}]*min-width:\s*0;")
        self.assertRegex(css, r"\.definition-value\s*\{[^}]*overflow-wrap:\s*anywhere;")
        self.assertRegex(css, r"\.definition-value\s*\{[^}]*word-break:\s*break-word;")
        self.assertRegex(css, r"\.chip\s*\{[^}]*white-space:\s*normal;")

    def test_scorecard_comparison_controls_pin_baseline_to_current_run_and_allow_alternate_candidate(self) -> None:
        source = Path("ui/dashboard-ui/rag/app.js").read_text(encoding="utf-8")
        css = Path("ui/dashboard-ui/rag/styles.css").read_text(encoding="utf-8")

        for marker in [
            "candidate-experiment-selector",
            "Current Benchmark Run stays pinned as the baseline.",
            "No alternate candidate benchmark run is available yet.",
            "getScorecardCandidateOptions",
            "resolveScorecardComparisonCandidate",
            "comparison-controls-note",
        ]:
            self.assertIn(marker, source if marker != "comparison-controls-note" else css)
        self.assertNotIn('id="baseline-experiment-selector"', source)

    def test_scorecard_comparison_controls_use_shared_footnote_for_alignment(self) -> None:
        source = Path("ui/dashboard-ui/rag/app.js").read_text(encoding="utf-8")
        css = Path("ui/dashboard-ui/rag/styles.css").read_text(encoding="utf-8")

        for marker in [
            "comparison-controls",
            "comparison-controls-grid",
            "Current Benchmark Run stays pinned as the baseline.",
        ]:
            self.assertIn(marker, source if marker != "comparison-controls-grid" else css)

    def test_scorecard_surfaces_case_results_expected_answer_and_external_benchmark_filter(self) -> None:
        js_source = Path("ui/dashboard-ui/rag/app.js").read_text(encoding="utf-8")
        html_source = Path("ui/dashboard-ui/rag/index.html").read_text(encoding="utf-8")

        for marker in [
            "Case Results",
            "Expected Answer",
            "expected_answer_preview",
            "actual_answer_preview",
            "route_correct",
        ]:
            self.assertIn(marker, js_source)

        self.assertIn('option value="external_benchmark"', html_source)


if __name__ == "__main__":
    unittest.main()
