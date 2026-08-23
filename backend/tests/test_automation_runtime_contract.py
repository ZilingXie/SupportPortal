import os
import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from backend.automation_runtime import create_app
from backend.services.automation_contracts import AutomationEnvironment, RouteResult
from backend.services.automation_execution_store import AutomationExecutionStore


class AutomationRuntimeContractTest(unittest.TestCase):
    def _client(self, environment: str):
        env = {
            "AUTOMATION_ENVIRONMENT": environment,
            "AUTOMATION_RUNTIME_ALLOW_MEMORY": "1",
            "n8n_request_token": "execution-token",
            "ROUTE_SERVICE_URL": "http://route.test",
            "ROUTE_SERVICE_TOKEN": "token",
            "PREPRODUCTION_ZENDESK_TICKET_ALLOWLIST": "123",
        }
        return patch.dict(os.environ, env, clear=False)

    def test_admin_login_exchanges_execution_token_without_request_token(self):
        with self._client("staging"):
            with TestClient(create_app()) as client:
                ok = client.post("/v1/auth/login", json={"email": "admin", "password": "admin"})
                self.assertEqual(ok.status_code, 200)
                payload = ok.json()
                self.assertEqual(payload["environment"], "staging")
                self.assertEqual(payload["execution_token"], "execution-token")
                wrong_password = client.post("/v1/auth/login", json={"email": "admin", "password": "wrong"})
                self.assertEqual(wrong_password.status_code, 401)
                wrong_user = client.post("/v1/auth/login", json={"email": "root", "password": "admin"})
                self.assertEqual(wrong_user.status_code, 401)
                missing = client.post("/v1/auth/login", json={})
                self.assertEqual(missing.status_code, 422)
                self.assertEqual(client.get("/v1/executions").status_code, 401)

    def test_admin_login_credentials_come_from_environment(self):
        env = {
            "AUTOMATION_ENVIRONMENT": "staging",
            "AUTOMATION_RUNTIME_ALLOW_MEMORY": "1",
            "n8n_request_token": "execution-token",
            "AUTOMATION_ADMIN_USERNAME": "ops",
            "AUTOMATION_ADMIN_PASSWORD": "secret",
        }
        with patch.dict(os.environ, env, clear=False), TestClient(create_app()) as client:
            default = client.post("/v1/auth/login", json={"email": "admin", "password": "admin"})
            self.assertEqual(default.status_code, 401)
            override = client.post("/v1/auth/login", json={"email": "ops", "password": "secret"})
            self.assertEqual(override.status_code, 200)
            self.assertEqual(override.json()["execution_token"], "execution-token")

    def test_staging_exposes_rerun_and_no_zendesk_visibility(self):
        with self._client("staging"), patch("backend.automation_runtime.call_route", new_callable=AsyncMock) as call:
            call.return_value = RouteResult(
                request_id="req-1",
                idempotency_key="staging:route:req-1",
                environment=AutomationEnvironment.STAGING,
                case_id="AC-1",
                route={"execution_action": "human_review_required"},
                automation={"eligible": False},
            )
            with TestClient(create_app()) as client:
                capabilities = client.get("/v1/capabilities")
                self.assertTrue(capabilities.json()["rerun"])
                result = client.post(
                    "/v1/cases",
                    json={"request_id": "req-1", "case_id": "AC-1", "question": "hello"},
                    headers={"X-N8n-Request-Token": "execution-token"},
                )
                self.assertEqual(result.status_code, 200)
                self.assertEqual(result.json()["execution"]["policy"]["zendesk_delivery"], False)

    def test_production_has_no_rerun_and_requires_visibility(self):
        with self._client("production"), patch("backend.automation_runtime.call_route", new_callable=AsyncMock) as call:
            call.return_value = RouteResult(
                request_id="req-2",
                idempotency_key="production:route:req-2",
                environment=AutomationEnvironment.PRODUCTION,
                case_id="AC-2",
                route={"execution_action": "human_review_required"},
                automation={"eligible": False},
            )
            with TestClient(create_app()) as client:
                self.assertFalse(client.get("/v1/capabilities").json()["rerun"])
                missing_visibility = client.post(
                    "/v1/cases",
                    json={"request_id": "req-2", "case_id": "AC-2", "zendesk_ticket_id": "123", "question": "hello"},
                    headers={"X-N8n-Request-Token": "execution-token"},
                )
                self.assertEqual(missing_visibility.status_code, 422)
                rerun = client.post(
                    "/v1/reruns",
                    json={"request_id": "req-rerun", "case_id": "AC-2", "rerun_of_execution_id": "exec-1"},
                    headers={"X-N8n-Request-Token": "execution-token"},
                )
                self.assertEqual(rerun.status_code, 404)

    def test_execution_endpoints_require_bearer_token(self):
        with self._client("staging"), patch("backend.automation_runtime.call_route", new_callable=AsyncMock) as call:
            with TestClient(create_app()) as client:
                missing = client.post("/v1/cases", json={"request_id": "req-401", "case_id": "AC-1", "question": "hello"})
                self.assertEqual(missing.status_code, 401)
                wrong = client.post(
                    "/v1/cases",
                    json={"request_id": "req-401", "case_id": "AC-1", "question": "hello"},
                    headers={"X-N8n-Request-Token": "wrong"},
                )
                self.assertEqual(wrong.status_code, 401)
                empty_body = client.post("/v1/cases", json={})
                self.assertEqual(empty_body.status_code, 401)
                self.assertEqual(client.post("/v1/reset").status_code, 401)
                self.assertEqual(
                    client.post("/v1/reruns", json={"request_id": "r", "case_id": "AC-1", "rerun_of_execution_id": "e"}).status_code,
                    401,
                )
                call.assert_not_awaited()

    def test_preproduction_human_review_returns_without_side_effects(self):
        with self._client("preproduction"), patch("backend.automation_runtime.call_route", new_callable=AsyncMock) as call:
            call.return_value = RouteResult(
                request_id="req-hr",
                idempotency_key="preproduction:route:req-hr",
                environment=AutomationEnvironment.PREPRODUCTION,
                case_id="AC-HR",
                route={"execution_action": "human_review_required"},
                automation={"eligible": False},
                action_plan={"preparation_status": "human_review"},
            )
            with patch("backend.automation_runtime.execute_side_effects") as effects, TestClient(create_app()) as client:
                response = client.post(
                    "/v1/cases",
                    json={"request_id": "req-hr", "case_id": "AC-HR", "zendesk_ticket_id": "123", "question": "hello"},
                    headers={"X-N8n-Request-Token": "execution-token"},
                )
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["status"], "human_review")
                effects.assert_not_called()

    def test_unknown_write_paths_return_not_found(self):
        with self._client("production"):
            with TestClient(create_app()) as client:
                self.assertEqual(client.post("/v1/not-a-route", json={}).status_code, 404)
                self.assertEqual(client.delete("/anything").status_code, 404)

    def _staging_route(self, request_id: str, case_id: str) -> RouteResult:
        return RouteResult(
            request_id=request_id,
            idempotency_key=f"staging:route:{request_id}",
            environment=AutomationEnvironment.STAGING,
            case_id=case_id,
            route={"execution_action": "human_review_required"},
            automation={"eligible": False},
        )

    def test_list_executions_supports_route_filters_and_counts(self):
        with self._client("staging"), patch("backend.automation_runtime.call_route", new_callable=AsyncMock) as call:
            routes = [
                ("req-f0", "AC-F0", {"category": "conversation", "subcategory": "human_review_required"}),
                ("req-f1", "AC-F1", {"category": "account_billing", "subcategory": "account_suspension"}),
                ("req-f2", "AC-F2", {"category": "conversation", "subcategory": "follow_up"}),
                ("req-f3", "AC-F3", {"scope_label": "backend_operation", "execution_action": "human_review_required"}),
            ]
            call.side_effect = [
                RouteResult(
                    request_id=request_id,
                    idempotency_key=f"staging:route:{request_id}",
                    environment=AutomationEnvironment.STAGING,
                    case_id=case_id,
                    route=route,
                    automation={"eligible": False},
                )
                for request_id, case_id, route in routes
            ]
            headers = {"X-N8n-Request-Token": "execution-token"}
            with TestClient(create_app()) as client:
                for request_id, case_id, _, in routes:
                    result = client.post(
                        "/v1/cases",
                        json={"request_id": request_id, "case_id": case_id, "question": "q"},
                        headers=headers,
                    )
                    self.assertEqual(result.status_code, 200)
                listing = client.get("/v1/executions", headers=headers)
                payload = listing.json()
                self.assertEqual(payload["total"], 4)
                self.assertEqual(
                    payload["route_counts"], {"conversation": 2, "account_billing": 1, "backend_operation": 1}
                )
                self.assertEqual(payload["route_subcategory_counts"], {})
                filtered = client.get("/v1/executions?route_category=conversation", headers=headers)
                filtered_payload = filtered.json()
                self.assertEqual(filtered_payload["total"], 2)
                self.assertTrue(all(item["route_result"]["route"]["category"] == "conversation" for item in filtered_payload["executions"]))
                self.assertEqual(
                    filtered_payload["route_counts"], {"conversation": 2, "account_billing": 1, "backend_operation": 1}
                )
                self.assertEqual(filtered_payload["route_subcategory_counts"], {"human_review_required": 1, "follow_up": 1})
                subcategory = client.get(
                    "/v1/executions?route_category=conversation&route_subcategory=follow_up", headers=headers
                )
                subcategory_payload = subcategory.json()
                self.assertEqual(subcategory_payload["total"], 1)
                self.assertEqual(subcategory_payload["executions"][0]["case_id"], "AC-F2")
                scope_label_filtered = client.get("/v1/executions?route_category=backend_operation", headers=headers)
                scope_label_payload = scope_label_filtered.json()
                self.assertEqual(scope_label_payload["total"], 1)
                self.assertEqual(scope_label_payload["executions"][0]["case_id"], "AC-F3")
                self.assertEqual(
                    scope_label_payload["route_subcategory_counts"], {"human_review_required": 1}
                )
                empty = client.get("/v1/executions?route_category=unknown", headers=headers)
                self.assertEqual(empty.json()["total"], 0)

    def test_list_and_detail_executions_require_token_and_persist_request(self):
        with self._client("staging"), patch("backend.automation_runtime.call_route", new_callable=AsyncMock) as call:
            call.return_value = self._staging_route("req-l2", "AC-L2")
            headers = {"X-N8n-Request-Token": "execution-token"}
            with TestClient(create_app()) as client:
                self.assertEqual(client.get("/v1/executions").status_code, 401)
                for index in range(3):
                    result = client.post(
                        "/v1/cases",
                        json={"request_id": f"req-l{index}", "case_id": f"AC-L{index}", "question": f"q{index}", "subject": f"s{index}"},
                        headers=headers,
                    )
                    self.assertEqual(result.status_code, 200)
                listing = client.get("/v1/executions?page=1&page_size=2", headers=headers)
                self.assertEqual(listing.status_code, 200)
                payload = listing.json()
                self.assertEqual(payload["total"], 3)
                self.assertEqual(payload["page"], 1)
                self.assertEqual(payload["page_size"], 2)
                self.assertEqual(len(payload["executions"]), 2)
                self.assertEqual(payload["status_counts"], {"prepared": 3})
                self.assertEqual(payload["executions"][0]["request"]["question"], "q2")
                filtered = client.get("/v1/executions?status=human_review", headers=headers)
                self.assertEqual(filtered.json()["total"], 0)
                case_lookup = client.get("/v1/executions?case_id=AC-L1", headers=headers)
                self.assertEqual(case_lookup.json()["total"], 1)
                execution_id = payload["executions"][0]["execution_id"]
                detail = client.get(f"/v1/executions/{execution_id}", headers=headers)
                self.assertEqual(detail.status_code, 200)
                self.assertEqual(detail.json()["execution"]["execution_id"], execution_id)
                self.assertEqual(client.get(f"/v1/executions/{execution_id}").status_code, 401)
                self.assertEqual(client.get("/v1/executions/exec-none", headers=headers).status_code, 404)

    def test_rerun_creates_chained_execution_and_original_is_immutable(self):
        with self._client("staging"), patch("backend.automation_runtime.call_route", new_callable=AsyncMock) as call:
            call.return_value = self._staging_route("req-r1", "AC-R1")
            headers = {"X-N8n-Request-Token": "execution-token"}
            with TestClient(create_app()) as client:
                first = client.post(
                    "/v1/cases",
                    json={"request_id": "req-r1", "case_id": "AC-R1", "question": "original question"},
                    headers=headers,
                )
                self.assertEqual(first.status_code, 200)
                original = first.json()["execution"]
                self.assertEqual(original["request"]["question"], "original question")
                rerun = client.post(
                    "/v1/reruns",
                    json={"request_id": "req-r2", "case_id": "AC-R1", "rerun_of_execution_id": original["execution_id"]},
                    headers=headers,
                )
                self.assertEqual(rerun.status_code, 200)
                payload = rerun.json()
                self.assertEqual(payload["status"], "prepared")
                self.assertEqual(payload["rerun_of_execution_id"], original["execution_id"])
                self.assertNotEqual(payload["execution"]["execution_id"], original["execution_id"])
                self.assertEqual(payload["execution"]["rerun_of_execution_id"], original["execution_id"])
                self.assertEqual(payload["execution"]["request"]["question"], "original question")
                detail = client.get(f"/v1/executions/{original['execution_id']}", headers=headers).json()["execution"]
                self.assertNotIn("rerun_of_execution_id", detail)
                self.assertEqual(client.get("/v1/executions", headers=headers).json()["total"], 2)

    def test_rerun_rejects_legacy_records_and_case_mismatch(self):
        with self._client("staging"), patch("backend.automation_runtime.call_route", new_callable=AsyncMock) as call:
            call.return_value = self._staging_route("req-legacy", "AC-LEGACY")
            headers = {"X-N8n-Request-Token": "execution-token"}
            with TestClient(create_app()) as client:
                first = client.post(
                    "/v1/cases",
                    json={"request_id": "req-legacy", "case_id": "AC-LEGACY", "question": "q"},
                    headers=headers,
                )
                original = first.json()["execution"]
                mismatch = client.post(
                    "/v1/reruns",
                    json={"request_id": "req-x", "case_id": "AC-OTHER", "rerun_of_execution_id": original["execution_id"]},
                    headers=headers,
                )
                self.assertEqual(mismatch.status_code, 404)
                missing = client.post(
                    "/v1/reruns",
                    json={"request_id": "req-x", "case_id": "AC-LEGACY", "rerun_of_execution_id": "exec-none"},
                    headers=headers,
                )
                self.assertEqual(missing.status_code, 404)
                legacy_record = dict(original)
                legacy_record.pop("request")
                with patch.object(AutomationExecutionStore, "get", return_value=legacy_record):
                    legacy = client.post(
                        "/v1/reruns",
                        json={"request_id": "req-x", "case_id": "AC-LEGACY", "rerun_of_execution_id": legacy_record["execution_id"]},
                        headers=headers,
                    )
                self.assertEqual(legacy.status_code, 422)
                self.assertEqual(legacy.json()["detail"]["code"], "execution_request_not_persisted")

    def test_reset_staging_deletes_all_executions_and_preproduction_404(self):
        with self._client("staging"), patch("backend.automation_runtime.call_route", new_callable=AsyncMock) as call:
            call.return_value = self._staging_route("req-s1", "AC-S1")
            headers = {"X-N8n-Request-Token": "execution-token"}
            with TestClient(create_app()) as client:
                for index in range(2):
                    result = client.post(
                        "/v1/cases",
                        json={"request_id": f"req-s{index}", "case_id": f"AC-S{index}", "question": "q"},
                        headers=headers,
                    )
                    self.assertEqual(result.status_code, 200)
                reset = client.post("/v1/reset", headers=headers)
                self.assertEqual(reset.status_code, 200)
                self.assertEqual(reset.json()["status"], "completed")
                self.assertEqual(reset.json()["deleted_count"], 2)
                self.assertEqual(client.get("/v1/executions", headers=headers).json()["total"], 0)
        with self._client("preproduction"), TestClient(create_app()) as client:
            self.assertEqual(
                client.post("/v1/reset", headers={"X-N8n-Request-Token": "execution-token"}).status_code, 404
            )


class LegacyAccountIntakeCompatTests(unittest.TestCase):
    headers = {"X-N8n-Request-Token": "execution-token"}

    def _post_form(self, client, **overrides):
        fields = {
            "title": "Legacy intake title",
            "question": "I want to enable media relay",
            "customer_email": "customer@example.com",
            "customer_name": "Customer",
            "source": "https://agoraio.zendesk.com/agent/tickets/12999",
        }
        fields.update(overrides)
        return client.post("/v1/cases", data=fields, headers=self.headers)

    def test_staging_accepts_legacy_form_body_and_derives_identity(self):
        with AutomationRuntimeContractTest()._client("staging"), patch(
            "backend.automation_runtime.call_route", new_callable=AsyncMock
        ) as call:
            call.return_value = AutomationRuntimeContractTest()._staging_route("n8n-zd-12999", "AC-12999")
            with TestClient(create_app()) as client:
                result = self._post_form(client)
                self.assertEqual(result.status_code, 200, result.text)
                execution = result.json()["execution"]
                self.assertEqual(execution["request_id"], "n8n-zd-12999")
                self.assertEqual(execution["case_id"], "AC-12999")
                self.assertEqual(execution["request"]["zendesk_ticket_id"], "12999")
                self.assertEqual(execution["request"]["subject"], "Legacy intake title")
                self.assertEqual(execution["request"]["question"], "I want to enable media relay")

    def test_legacy_form_body_replay_is_idempotent(self):
        with AutomationRuntimeContractTest()._client("staging"), patch(
            "backend.automation_runtime.call_route", new_callable=AsyncMock
        ) as call:
            call.return_value = AutomationRuntimeContractTest()._staging_route("n8n-zd-12999", "AC-12999")
            with TestClient(create_app()) as client:
                first = self._post_form(client)
                self.assertEqual(first.status_code, 200, first.text)
                second = self._post_form(client)
                self.assertEqual(second.status_code, 200, second.text)
                self.assertTrue(second.json().get("idempotent_replay"))

    def test_legacy_json_body_maps_title_and_source(self):
        with AutomationRuntimeContractTest()._client("staging"), patch(
            "backend.automation_runtime.call_route", new_callable=AsyncMock
        ) as call:
            call.return_value = AutomationRuntimeContractTest()._staging_route("n8n-zd-13000", "AC-13000")
            with TestClient(create_app()) as client:
                result = client.post(
                    "/v1/cases",
                    json={
                        "title": "JSON legacy shape",
                        "question": "q",
                        "source": "https://agoraio.zendesk.com/api/v2/tickets/13000.json",
                    },
                    headers=self.headers,
                )
                self.assertEqual(result.status_code, 200, result.text)
                execution = result.json()["execution"]
                self.assertEqual(execution["request_id"], "n8n-zd-13000")
                self.assertEqual(execution["case_id"], "AC-13000")
                self.assertEqual(execution["request"]["subject"], "JSON legacy shape")
                self.assertEqual(execution["request"]["zendesk_ticket_id"], "13000")
                rejected = client.post(
                    "/v1/cases",
                    json={"question": "q", "source": "https://agoraio.zendesk.com/agent/tickets/13000", "foo": "bar"},
                    headers=self.headers,
                )
                self.assertEqual(rejected.status_code, 422)

    def test_body_without_request_id_or_source_generates_identity(self):
        with AutomationRuntimeContractTest()._client("staging"), patch(
            "backend.automation_runtime.call_route", new_callable=AsyncMock
        ) as call:
            call.return_value = AutomationRuntimeContractTest()._staging_route("generated", "AC-GENERATED")
            with TestClient(create_app()) as client:
                result = client.post("/v1/cases", json={"question": "q"}, headers=self.headers)
                self.assertEqual(result.status_code, 200, result.text)
                execution = result.json()["execution"]
                self.assertTrue(execution["request_id"].startswith("n8n-"))
                self.assertTrue(execution["case_id"].startswith("AC-"))
                call.assert_called_once()


if __name__ == "__main__":
    unittest.main()
