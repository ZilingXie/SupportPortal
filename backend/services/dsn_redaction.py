"""DSN secret redaction for logs, reports and evidence (p2-209, C7).

Incident 2026-10-21: a ``key=value``-form DSN printed during diagnosis
bypassed a URL-shaped masking ``sed`` and leaked the runtime DB password
into the conversation record. Every piece of text that can carry a DSN
(exception messages, aborted reasons, report fields) must pass through
:func:`redact_dsn_secrets` before it is logged, written to a report file,
or included in evidence.

Supported shapes (password value replaced with ``***``):

- URL form: ``postgresql://user:password@host:port/db?...``
- key=value form: ``password=...`` (optionally quoted), case-insensitive,
  terminated by whitespace, semicolon, or the closing quote.
- Any of the above embedded in free text (exception bodies, log lines).

The function is idempotent: already-redacted text is returned unchanged.
Structure (user, host, database, options) is preserved so diagnosis stays
useful; only the secret is destroyed.
"""

from __future__ import annotations

import re

_URL_DSN_PASSWORD = re.compile(r"([a-zA-Z][a-zA-Z0-9+.-]*://[^:/?\s]+:)([^@\s/]+)(@)")
_KEY_VALUE_PASSWORD = re.compile(
    r"(password\s*=\s*)(\"[^\"]*\"|'[^']*'|[^\s;'\"]+)", re.IGNORECASE
)
_REDACTED = "***"


def redact_dsn_secrets(text: object) -> str:
    """Return ``text`` as a string with every DSN password shape redacted."""
    rendered = "" if text is None else str(text)
    # URL form first: its password segment also matches the key=value pattern
    # only in rare query strings, but ordering keeps the output stable.
    rendered = _URL_DSN_PASSWORD.sub(lambda m: f"{m.group(1)}{_REDACTED}{m.group(3)}", rendered)
    rendered = _KEY_VALUE_PASSWORD.sub(lambda m: f"{m.group(1)}{_REDACTED}", rendered)
    return rendered
