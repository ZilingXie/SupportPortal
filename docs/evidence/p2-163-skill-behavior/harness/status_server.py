"""Mock read-only status endpoint for behavior acceptance.

Serves GET /automation/preproduction/v1/enablement-relay/requests/{id}
with scenario-configured responses; logs every query to queries.log.
Scenario 'unreadable' returns 503 for every request.
"""
import json, os, sys, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HARNESS = os.path.dirname(os.path.abspath(__file__))

RESPONSES = {
    "s1": {
        "enr-NEW-v1": {"request_id": "enr-NEW-v1", "status": "dispatched", "dispatch_status": "created",
                        "relay_task_id": "task-AAA", "zendesk_ticket_id": "13605", "request_version": 1,
                        "ticket_status": "open", "ticket_valid": True},
        "enr-OLD-v1": {"request_id": "enr-OLD-v1", "status": "cancelled", "dispatch_status": "created",
                        "relay_task_id": "task-BBB", "zendesk_ticket_id": "13601", "request_version": 1,
                        "ticket_status": "solved", "ticket_valid": False},
    },
    "s2": {
        "enr-NEW-v1": {"request_id": "enr-NEW-v1", "status": "dispatched", "dispatch_status": "created",
                        "relay_task_id": "task-AAA", "zendesk_ticket_id": "13605", "request_version": 1,
                        "ticket_status": "open", "ticket_valid": True},
        "enr-OLD-v1": {"request_id": "enr-OLD-v1", "status": "dispatched", "dispatch_status": "created",
                        "relay_task_id": "task-BBB", "zendesk_ticket_id": "13601", "request_version": 1,
                        "ticket_status": "pending", "ticket_valid": True},
    },
    "s3": {},
}

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        scenario = os.environ.get("BEHAVIOR_SCENARIO", "s1")
        with open(os.path.join(HARNESS, "queries.log"), "a") as fh:
            fh.write(f"{scenario} GET {self.path}\n")
        if scenario == "s3":
            self.send_response(503)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"detail": "ticket status unreadable"}).encode())
            return
        request_id = self.path.rstrip("/").rsplit("/", 1)[-1]
        payload = RESPONSES.get(scenario, {}).get(request_id)
        if payload is None:
            self.send_response(404)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"detail": "relay request not found"}).encode())
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(payload).encode())

    def log_message(self, *args):
        pass

if __name__ == "__main__":
    port = int(sys.argv[1])
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
