"""Steps 2+3 (C1/C2): server-owned compatibility_relay_required basis flag."""

TOOLS = "backend/services/automation_hermes_tools.py"
BASIS = "backend/services/hermes_customer_reply_basis.py"
DELIVERY = "backend/services/automation_hermes_delivery.py"

with open(TOOLS, "r", encoding="utf-8") as fh:
    tools = fh.read()

old = '''                skip_persona_result = {
                    "status": "workflow_completed",
                    "route": normalized_route,
                    "outcome": workflow_outcome,
                    "skip_persona": False,
                    "reply_owner": "hermes_persona",
                    "compatibility_account_reply_job_id": compatibility_job_id,'''
new = '''                skip_persona_result = {
                    "status": "workflow_completed",
                    "route": normalized_route,
                    "outcome": workflow_outcome,
                    "skip_persona": False,
                    "reply_owner": "hermes_persona",
                    # C2: the relay workflow created a pending internal
                    # email/relay release bound to this job — the worker
                    # MUST validate the job before releasing (14009: only
                    # real relay turns carry this flag).
                    "compatibility_relay_required": True,
                    "compatibility_account_reply_job_id": compatibility_job_id,'''
assert tools.count(old) == 1, "workflow result anchor"
tools = tools.replace(old, new)

old3 = '''        # A reply job created by the tool (fresh send or sent-reuse) is the
        # SOLE customer reply; persona must not draft a second one (F4).
        "skip_persona": bool(skip_persona),
    }'''
new3 = '''        # A reply job created by the tool (fresh send or sent-reuse) is the
        # SOLE customer reply; persona must not draft a second one (F4).
        "skip_persona": bool(skip_persona),
    }
    if normalized_route == "enablement":
        # C1: no relay workflow ran in this turn's tool path (the
        # workflow-completed branch above returns early with its own
        # explicit True) — there is nothing for the worker's compatibility
        # job gate to release, so the server decision is False.
        result.setdefault("compatibility_relay_required", False)'''
assert tools.count(old3) == 1, "final result anchor"
tools = tools.replace(old3, new3)
with open(TOOLS, "w", encoding="utf-8") as fh:
    fh.write(tools)

with open(BASIS, "r", encoding="utf-8") as fh:
    basis = fh.read()
old4 = '''        extra={
            key: status if key == "source_work_status" else result.get(key)
            for key in ("source_work_status", "compatibility_account_reply_job_id")
            if (key == "source_work_status" and status)
            or (key != "source_work_status" and result.get(key) not in (None, ""))
        },'''
new4 = '''        extra={
            key: status if key == "source_work_status" else result.get(key)
            for key in (
                "source_work_status",
                "compatibility_relay_required",
                "compatibility_account_reply_job_id",
            )
            if (key == "source_work_status" and status)
            or (key != "source_work_status" and result.get(key) is not None)
        },'''
assert basis.count(old4) == 1, "basis extra anchor"
basis = basis.replace(old4, new4)
with open(BASIS, "w", encoding="utf-8") as fh:
    fh.write(basis)

with open(DELIVERY, "r", encoding="utf-8") as fh:
    delivery = fh.read()
old6 = '''        delivery_attachments.append({"_hermes_compatibility_job_id": compatibility_job_id})'''
new6 = '''        delivery_attachments.append({"_hermes_compatibility_job_id": compatibility_job_id})
    relay_required_flag = (draft.get("basis") or {}).get("compatibility_relay_required")
    if relay_required_flag is not None:
        delivery_attachments.append(
            {"_hermes_compatibility_relay_required": bool(relay_required_flag)}
        )'''
assert delivery.count(old6) == 1, "delivery attachment anchor"
delivery = delivery.replace(old6, new6)
with open(DELIVERY, "w", encoding="utf-8") as fh:
    fh.write(delivery)

print("tools+basis+delivery patched")
