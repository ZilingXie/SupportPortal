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
        self.assertIn("20260806-account-case-rerun-search-1", html)

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

    def test_account_app_contains_exact_case_search_and_single_case_rerun(self) -> None:
        app_source = Path("ui/account-ui/app.js").read_text(encoding="utf-8")
        styles = Path("ui/account-ui/styles.css").read_text(encoding="utf-8")

        for marker in (
            "caseSearchQuery",
            "caseSearchError",
            "isSearchingCase",
            "normalizeCaseNumberQuery",
            "searchCaseByNumber",
            'data-case-search-form',
            'placeholder="Case #"',
            'aria-live="polite"',
            "Rerun this case",
            "startSingleCaseRerun",
            "rerouteTargetSnapshot",
            'fetch(`/api/account/cases/${encodeURIComponent(caseId)}/rerun`',
            'fetch(`/api/account/rerun-jobs/${encodeURIComponent(jobId)}`',
            "All non-customer messages will be permanently deleted",
            "Engineer, manual, and internal messages are included",
            "The current route review and correction will be reset",
            "Independent audit records will be retained",
            "invalidateDetailCache(targetCaseId)",
        ):
            self.assertIn(marker, app_source)
        self.assertIn("account-case-search", styles)
        self.assertIn("detail-rerun-button", styles)
        self.assertIn("danger-button", styles)

        helper_start = app_source.index("function normalizeCaseNumberQuery")
        helper_end = app_source.index("\nfunction", helper_start + 1)
        helper = app_source[helper_start:helper_end]
        script = (
            f"{helper}\n"
            "console.log(JSON.stringify(["
            "normalizeCaseNumberQuery('12572'),"
            "normalizeCaseNumberQuery('#12572'),"
            "normalizeCaseNumberQuery('1257x'),"
            "normalizeCaseNumberQuery('')"
            "]));"
        )
        result = subprocess.run(
            ["node", "-e", script],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), ["12572", "12572", "", ""])

    def test_single_case_rerun_reuses_one_idempotency_key_per_confirmation_intent(self) -> None:
        app_source = Path("ui/account-ui/app.js").read_text(encoding="utf-8")
        start = app_source.index("async function startSingleCaseRerun")
        end = app_source.index("\nasync function", start + 1)
        start_source = app_source[start:end]

        self.assertIn("function createSingleCaseRerunIdempotencyKey", app_source)
        self.assertIn("idempotencyKey", app_source)
        self.assertIn('"Idempotency-Key": snapshot.idempotencyKey', start_source)
        self.assertNotIn("randomUUID", start_source)
        self.assertIn("state.rerouteTargetSnapshot = null", start_source)
        self.assertIn("state.rerouteConfirmationOpen = true", start_source)

        open_handler = app_source.index("[data-action='open-single-rerun-confirmation']")
        close_handler = app_source.index("[data-action='close-reroute-confirmation']", open_handler)
        open_source = app_source[open_handler:close_handler]
        self.assertIn("createSingleCaseRerunIdempotencyKey", open_source)
        self.assertIn("idempotencyKey", open_source)

        script = (
            "const state = {"
            "rerouteTargetSnapshot: { caseId: 'AC-12568', ticketNumber: '12568', "
            "idempotencyKey: 'stable-confirmation-key' }, "
            "isStartingReroute: false, rerouteConfirmationOpen: true, rerouteError: '', "
            "rerouteJob: null, rerouteActiveTargetCaseId: '' };\n"
            "let requestKeys = [];\n"
            "let shouldSucceed = false;\n"
            "function isActiveRerouteJob() { return false; }\n"
            "function render() {}\n"
            "function showToast() {}\n"
            "function responseErrorMessage(_payload, fallback) { return fallback; }\n"
            "async function readResponsePayload(response) { return response.payload; }\n"
            "async function fetch(_url, options) {\n"
            "  requestKeys.push(options.headers['Idempotency-Key']);\n"
            "  if (!shouldSucceed) throw new Error('network unavailable');\n"
            "  return { ok: true, status: 202, payload: { job_id: 'job-replayed', status: 'queued' } };\n"
            "}\n"
            f"{start_source}\n"
            "(async () => {\n"
            "  const originalSnapshot = state.rerouteTargetSnapshot;\n"
            "  await startSingleCaseRerun();\n"
            "  const reopenedAfterFailure = state.rerouteConfirmationOpen;\n"
            "  const sameSnapshotAfterFailure = state.rerouteTargetSnapshot === originalSnapshot;\n"
            "  await startSingleCaseRerun();\n"
            "  shouldSucceed = true;\n"
            "  await startSingleCaseRerun();\n"
            "  console.log(JSON.stringify({ requestKeys, reopenedAfterFailure, "
            "sameSnapshotAfterFailure, snapshotAfterSuccess: state.rerouteTargetSnapshot, "
            "jobId: state.rerouteJob?.job_id }));\n"
            "})().catch((error) => { console.error(error); process.exit(1); });\n"
        )
        result = subprocess.run(
            ["node", "-e", script],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        retry_result = json.loads(result.stdout)
        self.assertEqual(retry_result["requestKeys"], ["stable-confirmation-key"] * 3)
        self.assertTrue(retry_result["reopenedAfterFailure"])
        self.assertTrue(retry_result["sameSnapshotAfterFailure"])
        self.assertIsNone(retry_result["snapshotAfterSuccess"])
        self.assertEqual(retry_result["jobId"], "job-replayed")

    def test_account_detail_persona_assignment_is_visible_only_for_automated_cases(self) -> None:
        app_source = Path("ui/account-ui/app.js").read_text(encoding="utf-8")
        self.assertIn("const ACCOUNT_PERSONA_PRESENTATION", app_source)
        self.assertIn("function renderPersonaAssignment", app_source)
        helper_start = app_source.index("const ACCOUNT_PERSONA_PRESENTATION")
        helper_end = app_source.index("\nfunction renderDetailView", helper_start)
        helper_source = app_source[helper_start:helper_end]
        script = f"""
        function escapeHtml(value) {{ return String(value ?? '').replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('"', '&quot;').replaceAll("'", '&#39;'); }}
        function isAutomatedRoute(item) {{ return String(item?.route_status || '').trim() === 'automated'; }}
        {helper_source}
        console.log(JSON.stringify({{
          assigned: renderPersonaAssignment({{ route_status: 'automated', persona_assignment: {{ persona_key: 'sid-bright', version: 2, display_name: 'Sid Bright' }} }}),
          unassigned: renderPersonaAssignment({{ route_status: 'automated', persona_assignment: null }}),
          humanReview: renderPersonaAssignment({{ route_status: 'not_automated', persona_assignment: {{ persona_key: 'sid-bright', version: 2, display_name: 'Sid Bright' }} }}),
          custom: renderPersonaAssignment({{ route_status: 'automated', persona_assignment: {{ persona_key: 'custom-calm', version: 4, display_name: 'Custom Calm' }} }}),
          constructorKey: renderPersonaAssignment({{ route_status: 'automated', persona_assignment: {{ persona_key: 'constructor', version: 5, display_name: 'Constructor Voice' }} }}),
        }}));
        """
        result = subprocess.run(
            ["node", "-e", script],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        rendered = json.loads(result.stdout)
        self.assertIn("Sid Bright", rendered["assigned"])
        self.assertIn("v2", rendered["assigned"])
        self.assertIn("Bright", rendered["assigned"])
        self.assertIn("Not assigned yet", rendered["unassigned"])
        self.assertEqual(rendered["humanReview"], "")
        self.assertIn("Custom Calm", rendered["custom"])
        self.assertNotIn("Precise", rendered["custom"])
        self.assertNotIn("Bright", rendered["custom"])
        self.assertNotIn("Warm", rendered["custom"])
        self.assertIn("Constructor Voice", rendered["constructorKey"])
        self.assertIn("v5", rendered["constructorKey"])
        self.assertNotIn("persona-style-badge", rendered["constructorKey"])
        self.assertNotIn("undefined", rendered["constructorKey"])

        detail_start = app_source.index("function renderDetailView")
        detail_end = app_source.index("\nfunction", detail_start + 1)
        self.assertIn("renderPersonaAssignment(item)", app_source[detail_start:detail_end])

    def test_account_rerun_summary_always_reports_persona_assignment_resets(self) -> None:
        app_source = Path("ui/account-ui/app.js").read_text(encoding="utf-8")
        status_start = app_source.index("function renderRerouteStatus")
        status_end = app_source.index("\nfunction", status_start + 1)
        status_source = app_source[status_start:status_end]
        script = f"""
        function escapeHtml(value) {{ return String(value ?? ''); }}
        function isActiveRerouteJob() {{ return false; }}
        const state = {{ rerouteError: '', rerouteJob: null }};
        {status_source}
        function renderWithResetCount(count) {{
          state.rerouteJob = {{ status: 'completed', succeeded: 1, changed: 1, persona_assignments_deleted: count }};
          return renderRerouteStatus();
        }}
        console.log(JSON.stringify([renderWithResetCount(0), renderWithResetCount(3)]));
        """
        result = subprocess.run(
            ["node", "-e", script],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        zero, nonzero = json.loads(result.stdout)
        self.assertIn("0 Persona assignments reset", zero)
        self.assertIn("3 Persona assignments reset", nonzero)

    def test_account_rerun_confirmation_explains_persona_reselection_for_single_and_all(self) -> None:
        app_source = Path("ui/account-ui/app.js").read_text(encoding="utf-8")
        confirmation_start = app_source.index("function renderRerouteConfirmation")
        confirmation_end = app_source.index("\nfunction", confirmation_start + 1)
        confirmation_source = app_source[confirmation_start:confirmation_end]
        script = f"""
        function escapeHtml(value) {{ return String(value ?? ''); }}
        const state = {{
          rerouteConfirmationOpen: true,
          rerouteTargetSnapshot: null,
          isStartingReroute: false,
        }};
        {confirmation_source}
        const allCases = renderRerouteConfirmation();
        state.rerouteTargetSnapshot = {{ caseId: 'AC-12562', ticketNumber: '12562' }};
        const singleCase = renderRerouteConfirmation();
        console.log(JSON.stringify([singleCase, allCases]));
        """
        result = subprocess.run(
            ["node", "-e", script],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        single_case, all_cases = json.loads(result.stdout)
        for rendered in (single_case, all_cases):
            self.assertIn("pinned Persona assignment will be cleared", rendered)
            self.assertIn("Only if the rerun produces a new Automation customer reply", rendered)
            self.assertIn("enabled and have a published version", rendered)
            self.assertIn("same Persona may be selected again", rendered)

    def test_account_persona_detail_styles_cover_desktop_tablet_and_mobile_contract(self) -> None:
        styles = Path("ui/account-ui/styles.css").read_text(encoding="utf-8")
        for marker in (
            ".persona-assignment",
            ".persona-style-badge",
            "overflow-wrap: anywhere",
            "@media (max-width: 960px)",
            "@media (max-width: 640px)",
            "overflow-x: hidden",
        ):
            self.assertIn(marker, styles)

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
        self.assertIn('{ id: "agora_technical", label: "Tech", children: [] }', app_source)
        self.assertIn('{ id: "agora_non_technical", label: "Non-tech", children: [] }', app_source)
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

    def test_account_filter_uses_primary_buttons_and_one_conditional_select(self) -> None:
        app_source = Path("ui/account-ui/app.js").read_text(encoding="utf-8")
        styles = Path("ui/account-ui/styles.css").read_text(encoding="utf-8")

        for marker in (
            "buildRouteFilterViewModel",
            'data-action="set-route-group"',
            'data-action="set-route-subcategory"',
            "No subcategories",
            "route-filter__group-button",
            "route-filter__subcategory",
        ):
            self.assertIn(marker, app_source)
        self.assertNotIn("filter-child-list", app_source)
        self.assertIn("route-filter", styles)
        self.assertIn("route-filter__group-button", styles)
        self.assertIn("route-filter__subcategory", styles)

        helper_start = app_source.index("function selectedFilterParts")
        helper_end = app_source.index("\nfunction renderFilterCount", helper_start)
        helpers = app_source[helper_start:helper_end]
        script = (
            f"{helpers}\n"
            "const definitions = ["
            "{id:'all',label:'All',children:[]},"
            "{id:'automation',label:'Automation',children:["
            "{id:'enablement',label:'Enablement'}]},"
            "{id:'agora_technical',label:'Tech',children:[]}];"
            "const counts = {'all': 7, 'automation': 2, 'automation:enablement': 2, 'agora_technical': 3};"
            "console.log(JSON.stringify(["
            "buildRouteFilterViewModel(definitions, counts, 'all'),"
            "buildRouteFilterViewModel(definitions, counts, 'automation:enablement'),"
            "buildRouteFilterViewModel(definitions, counts, 'agora_technical')"
            "]));"
        )
        result = subprocess.run(
            ["node", "-e", script],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        view_models = json.loads(result.stdout)
        self.assertEqual(view_models[0]["groupKey"], "all")
        self.assertTrue(view_models[0]["selectDisabled"])
        self.assertEqual(view_models[1]["groupKey"], "automation")
        self.assertEqual(view_models[1]["leafKey"], "enablement")
        self.assertFalse(view_models[1]["selectDisabled"])
        self.assertEqual(view_models[1]["options"][0]["count"], 2)
        self.assertEqual(view_models[2]["groupKey"], "agora_technical")
        self.assertTrue(view_models[2]["selectDisabled"])

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
        self.assertIn("expectedRevision && entry.revision !== expectedRevision", app_source)
        self.assertIn(
            'findDetailCacheEntry(identifier, String(item.detail_revision || ""))',
            app_source,
        )
        self.assertIn(
            'const expectedRevision = String(summary?.detail_revision || "")',
            app_source,
        )
        self.assertIn(
            'findDetailCacheEntry(ticketId, String(summary?.detail_revision || ""))',
            app_source,
        )
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
        # Route filter controls.
        self.assertIn(".route-filter", styles)
        self.assertIn(".route-filter__group-button", styles)
        self.assertIn(".route-filter__group-button--active", styles)
        self.assertIn(".route-filter__subcategory", styles)
        # Route correction controls still use the shared generic chip styles.
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
