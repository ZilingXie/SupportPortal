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
            'id="ws-status"',
            'id="event-volume-bars"',
            'id="dashboard-view-region"',
            'id="ticket-board-region"',
            'id="ticket-detail-modal"',
            'id="ticket-detail-dialog"',
            'id="ticket-detail-body"',
        ]:
            self.assertIn(required_id, source)

        for required_copy in [
            "Admin Operations",
            "Queue Health &amp; Throughput",
            "Escalation Watch",
            "Operator Summary",
            "Ticket Ops",
            "RAG Benchmark",
            "Ticket Details",
            "Investigating",
            "Communicating",
            "Escalated",
            "Resolved",
            "Sentiment Breakdown",
            "Bad Sentiment",
        ]:
            self.assertIn(required_copy, source)

        self.assertRegex(
            source,
            re.compile(r"Ticket Ops.*RAG Benchmark.*Ticket Details", re.DOTALL),
        )
        self.assertRegex(
            source,
            re.compile(r"Investigating.*Escalated.*Communicating.*Resolved", re.DOTALL),
        )
        self.assertIn('data-ticket-detail-group-toggle', source)
        self.assertIn('aria-expanded="true"', source)
        self.assertNotIn("Priority Breakdown", source)
        self.assertNotIn(">Urgent<", source)
        self.assertNotIn("Live Ticket Feed", source)
        self.assertNotIn("Live Stream", source)
        self.assertNotIn("RAG Workbench", source)
        self.assertNotIn("Open RAG Workbench", source)
        self.assertNotIn('id="event-stream"', source)

        self.assertIn('class="dashboard-rail"', source)
        self.assertIn('class="rail-footer"', source)
        self.assertIn('href="/dashboard/rag/"', source)
        self.assertIn("Realtime", source)
        self.assertIn("Logout", source)
        self.assertNotIn('id="header-user-controls"', source)
        self.assertNotIn("user-profile-chip", source)
        self.assertNotIn("user-meta", source)
        self.assertNotIn('data-dashboard-tab="experiments"', source)
        self.assertNotIn('data-dashboard-tab="overview"', source)
        self.assertIn(".dashboard-rail", css)
        self.assertIn(".rail-nav-group", css)
        self.assertIn(".rail-subnav", css)
        self.assertIn(".rail-footer", css)
        self.assertIn(".queue-health-card", css)
        self.assertIn(".ticket-board", css)
        self.assertIn(".ticket-detail-modal", css)
        self.assertNotIn(".feed-card", css)

    def test_root_dashboard_app_defaults_to_ticket_ops_and_uses_engineer_ticket_endpoints(self) -> None:
        source = Path("ui/dashboard-ui/app.js").read_text(encoding="utf-8")
        css = Path("ui/dashboard-ui/styles.css").read_text(encoding="utf-8")

        for marker in [
            'const opsHeaderEl = document.getElementById("ops-header");',
            'let currentDashboardView = "ticket-ops";',
            'let ticketBoardViewMode = "grid";',
            'const TICKET_DETAIL_STATUSES = ["investigating", "escalated", "communicating", "resolved"];',
            "renderRailNav",
            "/api/dashboard/metrics",
            "/api/engineer/tickets?",
            "/api/engineer/tickets/${encodeURIComponent(requestedTicketId)}",
            'opsHeaderEl.hidden = !isTicketOpsView;',
            "buildTicketBoardViewToggleHtml",
            'viewMode === "list" ? renderTicketBoardList(boardRows) : renderTicketBoardGrid(boardRows)',
            'document.body.classList.add("modal-open");',
            'document.body.classList.remove("modal-open");',
        ]:
            self.assertIn(marker, source)

        self.assertIn("body.modal-open", css)

    def test_root_dashboard_ticket_details_board_exposes_view_toggle_scaffold(self) -> None:
        source = Path("ui/dashboard-ui/index.html").read_text(encoding="utf-8")
        app_source = Path("ui/dashboard-ui/app.js").read_text(encoding="utf-8")
        css = Path("ui/dashboard-ui/styles.css").read_text(encoding="utf-8")

        self.assertIn('id="ops-header"', source)
        self.assertIn("buildTicketBoardViewToggleHtml", app_source)
        self.assertIn('data-ticket-board-view-option="grid"', app_source)
        self.assertIn('data-ticket-board-view-option="list"', app_source)
        self.assertIn("List view", app_source)
        self.assertIn("Grid view", app_source)
        self.assertIn(".ticket-board-view-toggle", css)
        self.assertIn(".ticket-board-view-toggle-btn", css)
        self.assertIn(".ticket-board-list", css)
        self.assertIn(".ticket-board-row", css)

    def test_root_dashboard_ops_header_hidden_state_overrides_layout_display(self) -> None:
        css = Path("ui/dashboard-ui/styles.css").read_text(encoding="utf-8")

        self.assertRegex(
            css,
            r"\.ops-header\[hidden\]\s*\{[^}]*display:\s*none(?:\s*!important)?;",
        )

    def test_root_dashboard_collapsed_rail_uses_shared_icon_track_for_ticket_details_group(self) -> None:
        css = Path("ui/dashboard-ui/styles.css").read_text(encoding="utf-8")

        self.assertRegex(
            css,
            r"\.rail-nav-item,\s*\.rail-subnav-item\s*\{[^}]*justify-content:\s*center;[^}]*gap:\s*0;[^}]*padding:\s*0 14px;",
        )
        self.assertRegex(
            css,
            r"\.rail-subnav-item\s*\{[^}]*min-height:\s*52px;[^}]*padding-inline:\s*14px;[^}]*border-radius:\s*18px;",
        )
        self.assertRegex(
            css,
            r"\.dashboard-rail:hover \.rail-subnav-item\s*\{[^}]*justify-content:\s*flex-start;[^}]*gap:\s*14px;[^}]*min-height:\s*42px;",
        )
        self.assertRegex(
            css,
            r"\.rail-nav-group-toggle\s*\{[^}]*position:\s*relative;",
        )
        self.assertRegex(
            css,
            r"\.rail-nav-chevron\s*\{[^}]*position:\s*absolute;[^}]*(?:right|inset-inline-end):\s*16px;",
        )
        self.assertNotRegex(
            css,
            r"\.rail-nav-chevron\s*\{[^}]*margin-left:\s*auto;",
        )

    def test_root_dashboard_footer_uses_shared_rail_item_geometry_without_profile_chip(self) -> None:
        source = Path("ui/dashboard-ui/index.html").read_text(encoding="utf-8")
        css = Path("ui/dashboard-ui/styles.css").read_text(encoding="utf-8")

        self.assertIn('data-rail-footer-status="realtime"', source)
        self.assertIn('id="logout-btn"', source)
        self.assertNotIn("rail-status-card", source)
        self.assertNotIn("logout-icon-btn", source)
        self.assertNotIn("user-avatar", source)
        self.assertRegex(
            css,
            r"\.rail-footer-item\s*\{[^}]*min-height:\s*52px;[^}]*justify-content:\s*center;[^}]*padding:\s*0 14px;",
        )
        self.assertRegex(
            css,
            r"\.dashboard-rail:hover \.rail-footer-item\s*\{[^}]*justify-content:\s*flex-start;[^}]*gap:\s*14px;[^}]*padding-inline:\s*16px;",
        )

    def test_root_dashboard_collapsed_brand_icon_uses_same_centerline_as_rail_icons(self) -> None:
        css = Path("ui/dashboard-ui/styles.css").read_text(encoding="utf-8")

        self.assertRegex(
            css,
            r"\.rail-brand\s*\{[^}]*justify-content:\s*center;[^}]*gap:\s*0;",
        )
        self.assertRegex(
            css,
            r"\.dashboard-rail:hover \.rail-brand\s*\{[^}]*justify-content:\s*flex-start;[^}]*gap:\s*14px;",
        )

    def test_root_dashboard_rail_is_viewport_anchored_with_lifted_footer_inset(self) -> None:
        css = Path("ui/dashboard-ui/styles.css").read_text(encoding="utf-8")

        self.assertRegex(
            css,
            r"\.dashboard-rail\s*\{[^}]*position:\s*sticky;[^}]*top:\s*0;[^}]*align-self:\s*start;[^}]*height:\s*100vh;[^}]*max-height:\s*100vh;[^}]*overflow-y:\s*auto;[^}]*padding:\s*26px 14px 40px;",
        )
        self.assertRegex(
            css,
            r"\.rail-footer\s*\{[^}]*margin-top:\s*auto;",
        )

    def test_root_dashboard_tablet_keeps_fixed_collapsed_rail_until_mobile_breakpoint(self) -> None:
        css = Path("ui/dashboard-ui/styles.css").read_text(encoding="utf-8")

        self.assertRegex(
            css,
            r"@media \(max-width:\s*900px\) and \(min-width:\s*721px\)\s*\{[^}]*\.dashboard-app,\s*\.dashboard-app:has\(\.dashboard-rail:hover\)\s*\{[^}]*grid-template-columns:\s*var\(--rail-width-collapsed\)\s+minmax\(0,\s*1fr\);",
        )
        self.assertRegex(
            css,
            r"@media \(max-width:\s*900px\) and \(min-width:\s*721px\)\s*\{[\s\S]*?\.dashboard-rail:hover \.rail-nav-label,[\s\S]*?opacity:\s*0;[\s\S]*?max-width:\s*0;",
        )
        self.assertRegex(
            css,
            r"@media \(max-width:\s*720px\)\s*\{[^}]*\.dashboard-app\s*\{[^}]*grid-template-columns:\s*1fr;",
        )

    def test_rag_dashboard_nav_uses_scorecard_pages(self) -> None:
        source = Path("ui/dashboard-ui/rag/index.html").read_text(encoding="utf-8")

        expected_tabs = {
            "scorecard": "Overview",
            "routing": "Routing",
            "retrieval": "Retrieval",
            "generation": "Generation",
            "performance": "Performance",
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
            "performance",
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
            "Performance",
            "context_relevance_score",
            "answer_relevance_score",
            "evidence_precision_at_5",
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

    def test_rag_overview_surfaces_token_only_summary(self) -> None:
        js_source = Path("ui/dashboard-ui/rag/app.js").read_text(encoding="utf-8")

        for marker in [
            "Overview",
            "Token Summary",
            "total_input_tokens",
            "total_output_tokens",
            "total_embedding_tokens",
            "token_by_model",
        ]:
            self.assertIn(marker, js_source)

        for marker in [
            "Provider-aware token and cost summary.",
            "Known Cost Total",
            "known_cost_total",
            "unknown_cost_present",
            "cost_by_model",
        ]:
            self.assertNotIn(marker, js_source)

    def test_benchmark_session_panel_is_rendered_for_benchmark_pages(self) -> None:
        js_source = Path("ui/dashboard-ui/rag/app.js").read_text(encoding="utf-8")

        for marker in [
            "buildBenchmarkSessionPanel",
            "payload.benchmark_session",
            "Improvements Since Previous Benchmark Session",
            "Session Runs",
            "No changelog entries linked",
        ]:
            self.assertIn(marker, js_source)

    def test_benchmark_session_panel_exposes_run_history_comparison_and_distributions(self) -> None:
        js_source = Path("ui/dashboard-ui/rag/app.js").read_text(encoding="utf-8")
        css_source = Path("ui/dashboard-ui/rag/styles.css").read_text(encoding="utf-8")

        for marker in [
            "Run History",
            "Run Comparison",
            "Failure Stage Distribution",
            "Root Cause Distribution",
            "Execution Mode Slice",
            "Agent Fallback Slice",
            "Category Slice",
            "Query Type Slice",
            "Source Type Slice",
            "buildRunDistributionTable",
            "buildBenchmarkRunHistory",
            "buildBenchmarkRunComparison",
        ]:
            self.assertIn(marker, js_source)

        for marker in [
            ".benchmark-run-history",
            ".benchmark-run-comparison",
            ".benchmark-distribution-grid",
            ".benchmark-run-card",
        ]:
            self.assertIn(marker, css_source)

    def test_case_detail_surface_exposes_query_understanding_and_candidate_funnel(self) -> None:
        js_source = Path("ui/dashboard-ui/rag/app.js").read_text(encoding="utf-8")
        css_source = Path("ui/dashboard-ui/rag/styles.css").read_text(encoding="utf-8")

        for marker in [
            "Query Understanding",
            "Filter Provenance",
            "Candidate Funnel",
            "Judge Disagreement",
            "Strategy Snapshot",
            "Execution Mode",
            "Agent Fallback Used",
            "Agent Fallback Reason",
            "dictionary_hits",
            "hard_filter_sources",
            "candidate_funnel",
            "judge_summary",
        ]:
            self.assertIn(marker, js_source)

        for marker in [
            ".case-detail-diagnostic-grid",
            ".case-detail-funnel-grid",
            ".case-detail-code-block",
        ]:
            self.assertIn(marker, css_source)

    def test_ticket_detail_modal_exposes_token_usage_panel(self) -> None:
        js_source = Path("ui/dashboard-ui/app.js").read_text(encoding="utf-8")

        for marker in [
            "Token Usage",
            "canonical_ticket_id",
            "total_input_tokens",
            "total_output_tokens",
            "total_embedding_tokens",
            "token_by_model",
        ]:
            self.assertIn(marker, js_source)


if __name__ == "__main__":
    unittest.main()
