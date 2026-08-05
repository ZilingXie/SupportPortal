from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path


class AccountUiContractTests(unittest.TestCase):
    def test_account_mount_and_assets_exist(self) -> None:
        main_source = Path("backend/main.py").read_text(encoding="utf-8")

        self.assertIn('ACCOUNT_DIR = UI_DIR / "account-ui"', main_source)
        self.assertIn('app.mount("/account", StaticFiles(directory=ACCOUNT_DIR, html=True), name="account-ui")', main_source)

        expected_files = [
            Path("ui/account-ui/index.html"),
            Path("ui/account-ui/styles.css"),
            Path("ui/account-ui/app.js"),
        ]
        for file_path in expected_files:
            self.assertTrue(file_path.exists(), str(file_path))

    def test_account_html_uses_client_shared_assets(self) -> None:
        html = Path("ui/account-ui/index.html").read_text(encoding="utf-8")

        self.assertIn("<title>Account Intake</title>", html)
        self.assertIn('/shared-ui/composer.css', html)
        self.assertIn('/shared-ui/composer.js', html)
        self.assertIn("./styles.css", html)
        self.assertIn("./app.js", html)
        self.assertIn("20260805-route-filters-1", html)

    def test_account_app_contains_full_reroute_job_controls(self) -> None:
        app_source = Path("ui/account-ui/app.js").read_text(encoding="utf-8")
        styles = Path("ui/account-ui/styles.css").read_text(encoding="utf-8")

        self.assertIn("Rerun", app_source)
        self.assertIn('fetch("/api/account/rerun-jobs"', app_source)
        self.assertIn('fetch("/api/account/rerun-jobs/latest"', app_source)
        self.assertIn("readResponsePayload", app_source)
        self.assertIn("responseErrorMessage", app_source)
        self.assertIn("detail.message", app_source)
        self.assertIn("Rerun all Account Cases?", app_source)
        self.assertIn("Field Extractors", app_source)
        self.assertIn("Previously sent internal emails will be sent again", app_source)
        self.assertIn("Existing Account-only AI replies and reply jobs will be deleted", app_source)
        self.assertNotIn("old replies archived", app_source)
        self.assertIn("Account & Billing classification extractors also run again", app_source)
        self.assertIn('aria-live="polite"', app_source)
        self.assertIn('role="progressbar"', app_source)
        self.assertIn("reroute-modal", styles)
        self.assertIn("reroute-progress", styles)

    def test_account_app_posts_title_and_question_to_account_endpoint(self) -> None:
        app_source = Path("ui/account-ui/app.js").read_text(encoding="utf-8")

        self.assertIn('fetch("/account"', app_source)
        self.assertIn("/api/account/cases", app_source)
        self.assertIn("title", app_source)
        self.assertIn("question", app_source)
        self.assertIn("account_case_id", app_source)
        self.assertIn("billing_ticket_id", app_source)
        self.assertIn("history", app_source)
        self.assertIn("renderHistorySidebar", app_source)
        self.assertIn("renderDetailView", app_source)
        self.assertIn("not_automated", app_source)
        self.assertIn("needs_more_info", app_source)
        self.assertIn("automation", app_source)
        self.assertIn("No Account Cases yet", app_source)
        self.assertIn("Account Case detail", app_source)
        self.assertIn("selectedFilterLabel", app_source)
        self.assertIn('"manual"', app_source)
        self.assertIn('"api"', app_source)
        self.assertIn("sourceLabel", app_source)
        self.assertIn("sourceClass", app_source)
        self.assertIn("renderSourceValue", app_source)
        self.assertIn("safeSourceLink", app_source)
        self.assertIn('target="_blank"', app_source)
        self.assertIn('rel="noopener noreferrer"', app_source)
        self.assertIn('parsed.protocol === "http:"', app_source)

    def test_account_app_contains_filter_state_and_reply_composer(self) -> None:
        app_source = Path("ui/account-ui/app.js").read_text(encoding="utf-8")

        # Filter state and options.
        self.assertIn("statusFilter", app_source)
        self.assertIn('statusFilter: "all"', app_source)
        self.assertIn("DEFAULT_FILTER_DEFINITIONS", app_source)
        self.assertIn("filterCounts", app_source)
        self.assertIn("filter_counts", app_source)
        self.assertIn("filter_definitions", app_source)
        self.assertIn("renderFilterControls", app_source)
        self.assertIn("route_errors", app_source)
        self.assertIn("filter-chip", app_source)
        self.assertIn("unreviewed", app_source)
        self.assertIn("reviewed", app_source)
        self.assertIn("route_review_status", app_source)
        self.assertIn("pass-route", app_source)
        self.assertIn("unreview-route", app_source)
        self.assertIn("submitRouteReview", app_source)
        self.assertIn("selectedFilterLabel", app_source)
        self.assertIn("pagination", app_source)
        self.assertIn("renderPaginationControls", app_source)
        self.assertIn("data-action=\"set-page\"", app_source)
        self.assertIn("page_size", app_source)
        self.assertIn("total_pages", app_source)
        self.assertIn('params.set("route_errors", "true")', app_source)
        self.assertIn('params.set("route_group", group)', app_source)
        self.assertIn('params.set("route_subcategory", leaf)', app_source)
        self.assertIn('{ id: "fraud_account", label: "Fraud Account" }', app_source)
        self.assertIn('{ id: "detailed_invoice", label: "Detailed Invoice" }', app_source)
        self.assertIn('{ id: "enablement", label: "Enablement" }', app_source)
        self.assertIn('{ id: "quota", label: "Quota" }', app_source)
        self.assertIn('{ id: "unregistered", label: "Unregistered" }', app_source)
        self.assertIn("Account & Billing / Account Suspension", app_source)
        self.assertIn("Account & Billing / Other", app_source)
        self.assertNotIn("Automation / Account suspension", app_source)
        self.assertIn('label: "Conversation"', app_source)
        self.assertIn('label: "Non-Agora"', app_source)
        self.assertIn('{ id: "all", label: "All", children: [] }', app_source)
        self.assertIn('{ id: "agora_technical", label: "Agora Technical", children: [] }', app_source)
        self.assertIn('{ id: "agora_non_technical", label: "Agora Non-technical", children: [] }', app_source)
        self.assertIn('id: "conversation"', app_source)
        self.assertIn('id: "human_review"', app_source)
        self.assertIn("PAGE_SIZE", app_source)
        self.assertIn("currentPage", app_source)
        self.assertIn("const PAGE_SIZE = 10", app_source)

        # Reply composer state and flow.
        self.assertIn("replyMessage", app_source)
        self.assertIn("isSubmittingReply", app_source)
        self.assertIn("replyError", app_source)
        self.assertIn("renderReplyComposer", app_source)
        self.assertIn("submitReply", app_source)
        self.assertIn("renderMessageThread", app_source)
        self.assertIn("msg-bubble", app_source)

        # Reply endpoint references.
        self.assertIn("/api/account/cases/", app_source)
        self.assertIn("/reply", app_source)
        self.assertNotIn("/api/tickets/query", app_source)
        self.assertIn("ai_reply_scheduled_for", app_source)
        self.assertIn("formatMessageTimestamp", app_source)
        self.assertIn("AI reply scheduled", app_source)
        self.assertNotIn("Customer reply", app_source)

    def test_account_app_uses_memory_only_two_level_cache_and_batch_prefetch(self) -> None:
        app_source = Path("ui/account-ui/app.js").read_text(encoding="utf-8")
        styles = Path("ui/account-ui/styles.css").read_text(encoding="utf-8")

        self.assertIn("const summaryCache = new Map()", app_source)
        self.assertIn("const detailCache = new Map()", app_source)
        self.assertIn("const detailInflight = new Map()", app_source)
        self.assertIn("const SUMMARY_FRESH_MS = 30_000", app_source)
        self.assertIn("const DETAIL_FRESH_MS = 60_000", app_source)
        self.assertIn("const CACHE_HARD_EXPIRY_MS = 5 * 60_000", app_source)
        self.assertIn("const SUMMARY_CACHE_LIMIT = 20", app_source)
        self.assertIn("const DETAIL_CACHE_LIMIT = 20", app_source)
        self.assertIn('fetch("/api/account/cases/batch-details"', app_source)
        self.assertIn("AbortController", app_source)
        self.assertIn("summaryRequestGeneration", app_source)
        self.assertIn("filterCountsVersion", app_source)
        self.assertIn("countsVersion", app_source)
        self.assertIn("countsVersion >=", app_source)
        self.assertIn('window.addEventListener("pagehide"', app_source)
        self.assertIn('document.addEventListener("visibilitychange"', app_source)
        self.assertIn('cache: "no-store"', app_source)
        self.assertNotIn("localStorage", app_source)
        self.assertNotIn("sessionStorage", app_source)
        self.assertNotIn("indexedDB", app_source)
        self.assertIn("detail-loading", app_source)
        self.assertIn("history-loading", styles)

    def test_account_app_contains_route_correction_flow(self) -> None:
        app_source = Path("ui/account-ui/app.js").read_text(encoding="utf-8")

        self.assertIn("correctionScope", app_source)
        self.assertIn("correctionAction", app_source)
        self.assertIn("isSubmittingCorrection", app_source)
        self.assertIn("correctionError", app_source)
        self.assertIn("routeErrorSummary", app_source)
        self.assertIn("routeCorrectionExpanded", app_source)
        self.assertIn("toggle-route-correction", app_source)
        self.assertNotIn("correctionNote", app_source)
        self.assertNotIn("data-correction-note", app_source)
        self.assertIn("routeErrorSummary", app_source)
        self.assertIn("ROUTE_TUPLE_OPTIONS", app_source)
        self.assertIn(
            '{ scope: "account_suspension", action: "human_review_required", label: "Agora / Account & Billing / Account Suspension" }',
            app_source,
        )
        self.assertNotIn("Automation / Account suspension", app_source)
        self.assertIn(
            '{ scope: "automation", action: "enablement", label: "Automation / Enablement" }',
            app_source,
        )
        self.assertIn(
            '{ scope: "automation", action: "quota", label: "Automation / Quota" }',
            app_source,
        )
        self.assertIn("scope|action", app_source)
        self.assertIn("renderRouteCorrectionPanel", app_source)
        self.assertIn("submitRouteCorrection", app_source)
        self.assertNotIn("correctRoute", app_source)
        self.assertIn("/route-correction", app_source)
        self.assertIn("Conversation / Follow-up", app_source)
        self.assertIn("Agora / Account & Billing", app_source)
        self.assertIn("Agora / Uncategorized", app_source)
        self.assertIn("Automation / Unregistered", app_source)
        self.assertIn("Uncertain / Human Review", app_source)
        self.assertIn("scope_label", app_source)
        self.assertIn("execution_action", app_source)
        self.assertIn("corrector", app_source)

    def test_account_app_contains_route_error_summary_flow(self) -> None:
        app_source = Path("ui/account-ui/app.js").read_text(encoding="utf-8")

        self.assertIn("fetchRouteErrorSummary", app_source)
        self.assertIn("renderRouteErrorSummaryPanel", app_source)
        self.assertIn("/api/account/route-errors/summary?limit=100", app_source)
        self.assertIn("state.routeErrorSummary", app_source)
        self.assertIn("entry.transition", app_source)
        self.assertIn("render();", app_source)

    def test_account_app_does_not_expose_clear_all_ticket_flow(self) -> None:
        app_source = Path("ui/account-ui/app.js").read_text(encoding="utf-8")

        self.assertNotIn("clearAllTickets", app_source)
        self.assertNotIn("Delete all account tickets? This cannot be undone.", app_source)
        self.assertNotIn('fetch("/api/account/billing-tickets", { method: "DELETE" })', app_source)
        self.assertNotIn('data-action="clear-all-tickets"', app_source)
        self.assertNotIn("ticket(s) deleted", app_source)

    def test_account_app_javascript_syntax(self) -> None:
        result = subprocess.run(
            ["node", "--check", "ui/account-ui/app.js"],
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)

    def test_account_automation_filter_uses_stable_route_status(self) -> None:
        app_source = Path("ui/account-ui/app.js").read_text(encoding="utf-8")
        helper_start = app_source.index("function isAutomationStatus")
        helper_end = app_source.index("\nfunction renderFilterCount", helper_start)
        helpers = app_source[helper_start:helper_end]
        cases = [
            {"route_status": "automated", "automation_status": "customer_notified"},
            {"route_status": "not_automated", "automation_status": "automation"},
            {"automation_status": "automation"},
        ]
        script = (
            f"{helpers}\n"
            f"const cases = {json.dumps(cases)};\n"
            "console.log(JSON.stringify({"
            "matches: cases.map(isAutomatedRoute), "
            "displayStatuses: cases.map(displayRouteStatus)"
            "}));"
        )

        result = subprocess.run(
            ["node", "-e", script],
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            json.loads(result.stdout),
            {
                "matches": [True, False, True],
                "displayStatuses": ["automation", "not_automated", "automation"],
            },
        )
        self.assertEqual(app_source.count("const itemStatus = displayRouteStatus(item);"), 2)

    def test_account_styles_include_filter_message_reply_classes(self) -> None:
        styles = Path("ui/account-ui/styles.css").read_text(encoding="utf-8")

        self.assertIn(".status-badge--automation", styles)
        self.assertIn(".source-link", styles)
        # Filter chips.
        self.assertIn(".filter-groups", styles)
        self.assertIn(".filter-chip", styles)
        self.assertIn(".filter-chip--active", styles)
        # Pagination.
        self.assertIn(".history-pagination", styles)
        self.assertIn(".pagination-button", styles)
        self.assertIn(".pagination-button--active", styles)
        self.assertIn(".pagination-ellipsis", styles)
        # Message thread.
        self.assertIn(".message-thread", styles)
        self.assertIn(".msg-bubble", styles)
        self.assertIn(".msg-bubble--customer", styles)
        self.assertIn(".msg-bubble--assistant", styles)
        self.assertIn(".msg-row", styles)
        # Reply composer.
        self.assertIn(".reply-composer", styles)
        self.assertIn(".reply-textarea", styles)
        self.assertIn(".reply-actions", styles)

    def test_account_app_source_link_contract(self) -> None:
        app_source = Path("ui/account-ui/app.js").read_text(encoding="utf-8")

        # safeSourceLink supports Link, link, url field variants.
        self.assertIn("source.Link", app_source)
        self.assertIn("source.link", app_source)
        self.assertIn("source.url", app_source)

        # renderSourceValue has zen# label for zendesk.com ticket links.
        self.assertIn("zendesk.com", app_source)
        self.assertIn("zen#", app_source)
        self.assertIn("zendeskTicketLabel", app_source)
        self.assertIn("zendeskTicketId", app_source)
        self.assertIn("accountTicketNumber", app_source)
        self.assertIn("history-ticket-number", app_source)
        self.assertIn("detail-ticket-number", app_source)
        self.assertIn("Internal Ticket ID", app_source)

        # Keep existing safety markers.
        self.assertIn('target="_blank"', app_source)
        self.assertIn('rel="noopener noreferrer"', app_source)
        self.assertIn('parsed.protocol === "http:"', app_source)
        self.assertIn('parsed.protocol === "https:"', app_source)

        styles = Path("ui/account-ui/styles.css").read_text(encoding="utf-8")
        self.assertIn(".history-ticket-number", styles)
        self.assertIn(".detail-ticket-number", styles)
        self.assertIn("letter-spacing: 0", styles)

    def test_account_app_route_result_label_contract(self) -> None:
        app_source = Path("ui/account-ui/app.js").read_text(encoding="utf-8")

        # Route result helper joins scope_label / route_family / route into a readable string.
        self.assertIn("routeResultLabel", app_source)
        self.assertIn("item.scope_label", app_source)
        self.assertIn("item.route_family", app_source)
        self.assertIn("item.execution_action", app_source)

        # Detail view renders a Route result meta-row.
        self.assertIn("Route result", app_source)

        # Route reason explanation is still rendered alongside the route result.
        self.assertIn("Route reason", app_source)

    def test_account_detail_shows_internal_email_and_response_link_status(self) -> None:
        app_source = Path("ui/account-ui/app.js").read_text(encoding="utf-8")

        self.assertIn("internalEmailResponseLinkStatus", app_source)
        self.assertIn("internal_email_payload", app_source)
        self.assertIn("internal_email_send_status", app_source)
        self.assertIn("internal_email_send_reason", app_source)
        self.assertIn("Internal email", app_source)
        self.assertIn("Response link", app_source)
        self.assertIn("Generated", app_source)
        self.assertIn("Not generated", app_source)
