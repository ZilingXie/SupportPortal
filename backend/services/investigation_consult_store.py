"""Consultation recovery in existing turn/binding JSON, under the existing row locks."""
from __future__ import annotations

import copy
import json
from uuid import uuid4

from psycopg import sql
from psycopg.types.json import Jsonb


def transition(turn, binding, case, run, *, action, run_id, request=None, result=None):
    from backend.services.automation_ecs_store import HermesTurnStateError

    work = copy.deepcopy(turn.get("work_result") or {})
    existing = work.get("zac_consultation")
    current = (turn.get("status") in {"pending", "running"} and turn.get("phase") == "work"
        and turn.get("direction") == "investigation" and binding.get("direction") != "human"
        and (binding.get("case_task") or {}).get("direction") in {None, "investigation"}
        and int(turn["case_revision"]) == int(case.get("case_revision") or 0)
        and run and run.get("run_id") == run_id and run.get("status") == "running")
    if action == "reserve":
        if not current:
            raise HermesTurnStateError(turn["turn_id"], "zac_consultation_stale_or_wrong_phase")
        if existing:
            return work, binding.get("investigation"), existing, False
        investigation = copy.deepcopy(binding.get("investigation") or {})
        user_id = investigation.setdefault("_zac_user_id", uuid4().hex)
        record = {
            "state": "in_flight", "run_id": run_id,
            "case_revision": turn["case_revision"], "user_id": user_id,
            "idempotency_key": uuid4().hex,
            "request_body": json.dumps(request, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        }
        work["zac_consultation"] = record
        return work, investigation, record, True
    if not existing or existing.get("run_id") != run_id:
        raise HermesTurnStateError(turn["turn_id"], "zac_consultation_identity_conflict")
    if existing["state"] != "in_flight":
        return work, binding.get("investigation"), existing, False
    record = {**existing, **result, "stale": not current}
    work["zac_consultation"] = record
    return work, binding.get("investigation"), record, False


class MemoryConsultStore:
    def update_zac_consultation(self, turn_id, *, action, run_id, request=None, result=None):
        from backend.services.automation_ecs_store import HermesTurnStateError
        with self._lock:
            turn = self._hermes_turns.get(turn_id)
            if not turn:
                raise HermesTurnStateError(turn_id, "turn_not_found")
            binding = self._hermes_bindings[(turn["namespace"], turn["zendesk_ticket_id"])]
            case = self._cases[turn["zendesk_ticket_id"]]
            run = self._hermes_turn_runs.get((turn_id, "work"))
            work, investigation, record, created = transition(turn, binding, case, run,
                action=action, run_id=run_id, request=request, result=result)
            turn["work_result"] = work
            binding["investigation"] = investigation
            if action == "finish" and not record.get("stale") and record.get("conversation_id"):
                binding["zac_session_id"] = record["conversation_id"]
            return copy.deepcopy(record), created


class PostgresConsultStore:
    def update_zac_consultation(self, turn_id, *, action, run_id, request=None, result=None):
        from backend.services.automation_ecs_store import HermesTurnStateError
        with self._connect() as connection:
            with connection.transaction(), connection.cursor() as cursor:
                # Same order as native draft/lifecycle writes: case, binding, turn.
                cursor.execute(sql.SQL("SELECT namespace,zendesk_ticket_id FROM {} WHERE turn_id=%s").format(self._table("automation_hermes_agent_turns")), (turn_id,))
                identity = cursor.fetchone()
                if not identity:
                    raise HermesTurnStateError(turn_id, "turn_not_found")
                args = (identity["namespace"], identity["zendesk_ticket_id"])
                rows = []
                for table in ("automation_cases", "automation_hermes_case_bindings"):
                    cursor.execute(sql.SQL("SELECT * FROM {} WHERE namespace=%s AND zendesk_ticket_id=%s FOR UPDATE").format(self._table(table)), args)
                    rows.append(dict(cursor.fetchone() or {}))
                case, binding = rows
                cursor.execute(sql.SQL("SELECT * FROM {} WHERE turn_id=%s FOR UPDATE").format(self._table("automation_hermes_agent_turns")), (turn_id,))
                turn = dict(cursor.fetchone())
                cursor.execute(sql.SQL("SELECT * FROM {} WHERE turn_id=%s AND phase='work'").format(self._table("automation_hermes_turn_runs")), (turn_id,))
                run = cursor.fetchone()
                work, investigation, record, created = transition(turn, binding, case, run,
                    action=action, run_id=run_id, request=request, result=result)
                cursor.execute(sql.SQL("UPDATE {} SET work_result=%s,updated_at=NOW() WHERE turn_id=%s").format(self._table("automation_hermes_agent_turns")), (Jsonb(work), turn_id))
                cursor.execute(sql.SQL("UPDATE {} SET investigation=%s,updated_at=NOW() WHERE namespace=%s AND zendesk_ticket_id=%s").format(self._table("automation_hermes_case_bindings")), (Jsonb(investigation), *args))
                if action == "finish" and not record.get("stale") and record.get("conversation_id"):
                    cursor.execute(sql.SQL("UPDATE {} SET zac_session_id=%s WHERE namespace=%s AND zendesk_ticket_id=%s").format(self._table("automation_hermes_case_bindings")), (record["conversation_id"], *args))
                return record, created
