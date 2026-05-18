from __future__ import annotations

import json
from typing import Any

REQUEST_BODY_EVIDENCE_PROMPT_VERSION = "request_body_evidence_v1"


def build_request_body_evidence_system_prompt() -> str:
    return """## Role
You extract request-body and API-configuration retrieval clues for a RAG system.

## Scope
- Decide whether the customer message contains a request body, JSON payload, Python dict payload, curl request, fetch/axios/request call, or API configuration object.
- Extract only retrieval clues for schema evidence.
- Do not answer the customer question.
- Do not judge whether the payload is correct.
- Do not invent fields, endpoints, values, or schema names.
- Do not classify ordinary natural-language how-to questions as request-body/API-config messages.

## Required Output
Return JSON only with this exact object shape:
{
  "is_request_body_or_api_config": true,
  "confidence": 0.0,
  "endpoint_hints": [],
  "body_keys": [],
  "nested_paths": [],
  "field_value_hints": {},
  "question_need": "explain_behavior|correct_payload|parameter_meaning|unknown",
  "schema_evidence_goals": []
}
"""


def build_request_body_evidence_user_prompt(*, question: str, rule_hints: dict[str, Any]) -> str:
    return "\n".join(
        [
            "## Customer Message",
            str(question or "").strip(),
            "",
            "## Rule Extracted Hints",
            json.dumps(rule_hints, ensure_ascii=False, sort_keys=True),
            "",
            "Return JSON only.",
        ]
    )
