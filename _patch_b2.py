"""B-mode: update eval script to carry reply_basis; update test assertions; add basis tests."""

EVAL = "scripts/testing/fraud_reply_style_eval.py"
TEST = "backend/tests/test_hermes_fraud_reply_style.py"

# --- eval: inject real reply_basis into the WORK RESULT block ---
with open(EVAL, "r", encoding="utf-8") as fh:
    eval_src = fh.read()

old_wr = '''    if sample.get("tool_result_executed"):
        work_result = {
            "status": "executed",
            "route": "fraud_account",
            "missing_fields": [],
            "collected_fields": sample["collected_fields"],
            "internal_email_send_status": "sent",
        }
    else:
        work_result = {
            "status": "missing_fields",
            "route": "fraud_account",
            "missing_fields": sample["missing_fields"],
            "collected_fields": sample["collected_fields"],
        }'''
new_wr = '''    from backend.services.account_fraud_reply_basis import build_fraud_reply_basis

    basis = build_fraud_reply_basis(
        missing_fields=sample["missing_fields"],
        collected_fields=sample["collected_fields"],
    )
    if sample.get("tool_result_executed"):
        work_result = {
            "status": "executed",
            "route": "fraud_account",
            "missing_fields": [],
            "collected_fields": sample["collected_fields"],
            "internal_email_send_status": "sent",
            "reply_basis": basis,
        }
    else:
        work_result = {
            "status": "missing_fields",
            "route": "fraud_account",
            "missing_fields": sample["missing_fields"],
            "collected_fields": sample["collected_fields"],
            "reply_basis": basis,
        }'''
assert eval_src.count(old_wr) == 1
eval_src = eval_src.replace(old_wr, new_wr)
with open(EVAL, "w", encoding="utf-8") as fh:
    fh.write(eval_src)

# --- tests: B-mode assertions + basis builder + tool-level tests ---
with open(TEST, "r", encoding="utf-8") as fh:
    test_src = fh.read()

old_asserts = '''        # v4 manual structure rules.
        self.assertIn("the work result dictates", flat)
        self.assertIn("never re-ask", flat)
        self.assertIn("continue coordinating the review", flat)
        self.assertIn("one item per line", flat)
        self.assertIn('"Account type"', flat)
        self.assertIn('"Agora Console configuration"', flat)
        self.assertIn("never numbered", flat)'''
new_asserts = '''        # v4 B-mode: server reply basis rendered verbatim.
        self.assertIn("server-built reply basis", flat)
        self.assertIn("VERBATIM", flat)
        self.assertIn("never re-ask", flat)'''
assert test_src.count(old_asserts) == 1
test_src = test_src.replace(old_asserts, new_asserts)

old_contract = '''        self.assertIn("routes=fraud_account|detailed_invoice", flat)
        self.assertIn("routing signal only", flat)
        self.assertIn("within 24 hours", flat)
        self.assertIn("do not promise the 24-hour contact timeline", flat)'''
new_contract = '''        self.assertIn("routes=fraud_account|detailed_invoice", flat)
        self.assertIn("fraud_account_reply_basis_v1", flat)
        self.assertIn("closing_anchor", flat)
        self.assertIn("contact_commitment_anchor", flat)
        self.assertIn("routing signal only", flat)'''
assert test_src.count(old_contract) == 1
test_src = test_src.replace(old_contract, new_contract)

basis_tests = '''

class ReplyBasisBuilderTests(unittest.TestCase):
    """Option B: the structured parts are deterministic code output."""

    def test_zero_fields_bullet_list_and_closing_anchor(self):
        from backend.services.account_fraud_reply_basis import (
            ASK_CLOSING_ANCHOR,
            build_fraud_reply_basis,
        )

        basis = build_fraud_reply_basis(missing_fields=list(ALL_FIELDS), collected_fields={})
        self.assertEqual(basis["ask_layout"], "bullets")
        self.assertEqual(
            basis["ask_bullets"],
            "\\n".join(
                [
                    "- Account type",
                    "- Name",
                    "- Office address",
                    "- Official contact number",
                    "- Official contact email",
                    "- Use-case description",
                    "- Last known console configuration",
                ]
            ),
        )
        self.assertEqual(basis["closing_anchor"], ASK_CLOSING_ANCHOR)
        self.assertEqual(basis["collected_facts"], [])

    def test_two_fields_prose_core(self):
        from backend.services.account_fraud_reply_basis import build_fraud_reply_basis

        basis = build_fraud_reply_basis(
            missing_fields=["office_address", "console_configuration"],
            collected_fields={"name": "Jordan Lee"},
        )
        self.assertEqual(basis["ask_layout"], "prose")
        self.assertEqual(
            basis["ask_sentence_core"],
            "please share your office address and last known console configuration",
        )
        self.assertEqual(
            basis["collected_facts"], [{"label": "Name", "value": "Jordan Lee"}]
        )

    def test_complete_fields_confirmation_anchors(self):
        from backend.services.account_fraud_reply_basis import (
            CONFIRMATION_ANCHOR,
            CONTACT_COMMITMENT_ANCHOR,
            build_fraud_reply_basis,
        )

        basis = build_fraud_reply_basis(missing_fields=[], collected_fields={"account_type": "company"})
        self.assertEqual(basis["missing_fields"], [])
        self.assertEqual(basis["confirmation_anchor"], CONFIRMATION_ANCHOR)
        self.assertEqual(basis["contact_commitment_anchor"], CONTACT_COMMITMENT_ANCHOR)
        self.assertEqual(
            basis["collected_facts"], [{"label": "Account type", "value": "company"}]
        )

    def test_labels_match_legacy_persona_map(self):
        from backend.services.account_fraud_reply_basis import FRAUD_REPLY_FIELD_LABELS
        from backend.services.automation_persona import _FIELD_LABELS

        for field, label in FRAUD_REPLY_FIELD_LABELS.items():
            self.assertEqual(_FIELD_LABELS.get(field), label, field)

    def test_unknown_fields_kept_after_canonical_order(self):
        from backend.services.account_fraud_reply_basis import build_fraud_reply_basis

        basis = build_fraud_reply_basis(
            missing_fields=["extra_document", "office_address"], collected_fields={}
        )
        self.assertEqual(basis["missing_fields"], ["office_address", "extra_document"])
        self.assertIn("- Office address", basis["ask_bullets"])
        self.assertIn("- Extra document", basis["ask_bullets"])


class ToolReplyBasisTests(unittest.TestCase):
    """Option B: the real tool entry records the deterministic basis."""

    def test_fraud_missing_fields_work_result_carries_basis(self):
        import asyncio
        from types import SimpleNamespace as NS
        from unittest.mock import patch

        from backend.services.automation_hermes_tools import tool_execute_automation_action
        from backend.services.account_automation_ownership import (
            OWNERSHIP_STATE_ASSIGNED,
            OwnershipGateResult,
        )

        import backend.tests.test_hermes_email_execution as email_harness

        store = email_harness._store()
        handoff = email_harness._seed_turn(store, route="fraud_account")
        repository = email_harness._repository(
            email_harness._account_case(route="fraud_account", status="not_applicable")
        )
        gate = OwnershipGateResult(
            eligible=True,
            state=OWNERSHIP_STATE_ASSIGNED,
            assignee_id="48557297720084",
            group_id="29388501432596",
        )
        attempt = email_harness._attempt("fraud_account")
        attempt["missing_fields"] = list(ALL_FIELDS)
        attempt["internal_email_to_send"] = None
        attempt["internal_email_payload"] = None

        with patch(
            "backend.services.account_automation_ownership.ensure_production_automation_ownership",
            return_value=gate,
        ), patch(
            "backend.services.automation_account_intake._build_verification_attempt",
            return_value=attempt,
        ):
            result = asyncio.run(
                tool_execute_automation_action(
                    store,
                    repository,
                    turn_id=handoff["turn_id"],
                    route="fraud_account",
                    environment="preproduction",
                    zendesk_side_effects_enabled=True,
                )
            )

        self.assertEqual(result["status"], "missing_fields")
        basis = result.get("reply_basis") or {}
        self.assertEqual(basis.get("kind"), "fraud_account_reply_basis_v1")
        self.assertEqual(basis.get("ask_layout"), "bullets")
        self.assertIn("- Official contact number", basis.get("ask_bullets") or "")
        # The recorded turn work result carries the same basis (replayable).
        turn = store.get_hermes_turn(handoff["turn_id"])
        recorded = (turn.get("work_result") or {}).get("reply_basis") or {}
        self.assertEqual(recorded.get("kind"), "fraud_account_reply_basis_v1")
'''

anchor = '''

class JudgeScenarioTests(unittest.TestCase):'''
assert test_src.count(anchor) == 1
test_src = test_src.replace(anchor, basis_tests + anchor)

with open(TEST, "w", encoding="utf-8") as fh:
    fh.write(test_src)

print("B-mode tests updated")
