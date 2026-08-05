from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from html import escape
from typing import Any
from urllib.parse import urlparse


INTERNAL_EMAIL_TEMPLATE_VERSION = "internal-handoff-v1"
INTERNAL_EMAIL_HTML_CONTENT_TYPE = "HTML"


@dataclass(frozen=True)
class InternalEmailSection:
    title: str
    fields: tuple[tuple[str, Any], ...] = ()
    body: str = ""
    items: tuple[str, ...] = ()
    tone: str = "neutral"


def render_internal_handoff_email(
    *,
    request_type: str,
    title: str,
    ticket_id: str,
    intro: str,
    summary_fields: Sequence[tuple[str, Any]],
    sections: Sequence[InternalEmailSection] = (),
    missing_fields: Sequence[str] = (),
    missing_title: str = "Missing details after one follow-up",
    original_message: str | None = None,
    action_text: str,
    action_url: str | None = None,
) -> dict[str, str]:
    """Render one stable plain-text and HTML internal handoff payload."""
    normalized_request_type = _clean_text(request_type) or "Internal request"
    normalized_title = _clean_text(title) or normalized_request_type
    normalized_ticket_id = _clean_text(ticket_id) or "{{ticket_id}}"
    normalized_intro = _normalize_multiline(intro)
    normalized_summary = _normalize_fields(summary_fields)
    normalized_sections = tuple(sections)
    normalized_missing = tuple(_clean_text(item) for item in missing_fields if _clean_text(item))
    normalized_message = _normalize_multiline(original_message)
    safe_action_url = _safe_http_url(action_url)

    body = _render_text(
        request_type=normalized_request_type,
        title=normalized_title,
        ticket_id=normalized_ticket_id,
        intro=normalized_intro,
        summary_fields=normalized_summary,
        sections=normalized_sections,
        missing_fields=normalized_missing,
        missing_title=_clean_text(missing_title) or "Missing details after one follow-up",
        original_message=normalized_message,
        action_text=_normalize_multiline(action_text),
        action_url=safe_action_url,
    )
    body_html = _render_html(
        request_type=normalized_request_type,
        title=normalized_title,
        ticket_id=normalized_ticket_id,
        intro=normalized_intro,
        summary_fields=normalized_summary,
        sections=normalized_sections,
        missing_fields=normalized_missing,
        missing_title=_clean_text(missing_title) or "Missing details after one follow-up",
        original_message=normalized_message,
        action_text=_normalize_multiline(action_text),
        action_url=safe_action_url,
    )
    return {
        "body": body,
        "body_html": body_html,
        "body_content_type": INTERNAL_EMAIL_HTML_CONTENT_TYPE,
        "template_version": INTERNAL_EMAIL_TEMPLATE_VERSION,
    }


def _render_text(
    *,
    request_type: str,
    title: str,
    ticket_id: str,
    intro: str,
    summary_fields: Sequence[tuple[str, str]],
    sections: Sequence[InternalEmailSection],
    missing_fields: Sequence[str],
    missing_title: str,
    original_message: str,
    action_text: str,
    action_url: str | None,
) -> str:
    blocks = [
        "Hi team,",
        f"{request_type}: {title} - Ticket {ticket_id}",
        intro,
        "Request summary:\n" + _text_fields(summary_fields),
    ]
    for section in sections:
        section_lines = [section.title]
        if section.body:
            section_lines.append(section.body)
        if section.fields:
            section_lines.append(_text_fields(_normalize_fields(section.fields)))
        if section.items:
            section_lines.append("\n".join(f"- {_clean_text(item)}" for item in section.items))
        blocks.append("\n".join(line for line in section_lines if line))
    if missing_fields:
        blocks.append(missing_title + ":\n" + "\n".join(f"- {item}" for item in missing_fields))
    if original_message:
        blocks.append(f"Original customer message:\n{original_message}")
    action = action_text
    if action_url:
        action = f"{action}\n{action_url}"
    blocks.append(action)
    return "\n\n".join(block for block in blocks if block).strip()


def _render_html(
    *,
    request_type: str,
    title: str,
    ticket_id: str,
    intro: str,
    summary_fields: Sequence[tuple[str, str]],
    sections: Sequence[InternalEmailSection],
    missing_fields: Sequence[str],
    missing_title: str,
    original_message: str,
    action_text: str,
    action_url: str | None,
) -> str:
    request_type_html = _html_text(request_type)
    title_html = _html_text(title)
    ticket_html = _html_text(ticket_id)
    summary_html = _html_field_rows(summary_fields)
    sections_html = "".join(_html_section(section) for section in sections)
    missing_html = _html_missing_fields(missing_fields, missing_title)
    original_html = _html_original_message(original_message)
    action_html = _html_action(action_text, action_url)
    preheader = _html_text(f"{request_type} for Ticket {ticket_id}")

    return f"""<!doctype html>
<html lang=\"en\">
<head>
  <meta charset=\"utf-8\">
  <meta name=\"x-apple-disable-message-reformatting\">
  <meta name=\"color-scheme\" content=\"light dark\">
  <meta name=\"supported-color-schemes\" content=\"light dark\">
  <title>{title_html}</title>
  <style>
    :root {{ color-scheme: light dark; }}
    @media (prefers-color-scheme: dark) {{
      .email-shell {{ background: #171c21 !important; }}
      .email-panel {{ background: #242b32 !important; color: #f5f7f9 !important; }}
      .email-muted {{ color: #c2cbd3 !important; }}
      .email-surface {{ background: #303941 !important; color: #f5f7f9 !important; }}
      .email-quote {{ background: #20262c !important; border-color: #59656f !important; }}
      .email-action {{ background: #173b4a !important; border-color: #4e9fc0 !important; }}
    }}
    @media only screen and (max-width: 620px) {{
      .email-panel {{ width: 100% !important; }}
      .email-pad {{ padding: 22px 18px !important; }}
      .summary-cell {{ display: block !important; width: 100% !important; padding-right: 0 !important; }}
    }}
  </style>
</head>
<body style=\"margin:0;padding:0;background:#eef2f5;font-family:Arial,Helvetica,sans-serif;color:#17212b;\">
  <div style=\"display:none;max-height:0;overflow:hidden;opacity:0;mso-hide:all;\">{preheader}</div>
  <table role=\"presentation\" width=\"100%\" cellpadding=\"0\" cellspacing=\"0\" border=\"0\" class=\"email-shell\" style=\"width:100%;background:#eef2f5;\">
    <tr>
      <td align=\"center\" style=\"padding:28px 12px;\">
        <table role=\"presentation\" width=\"680\" cellpadding=\"0\" cellspacing=\"0\" border=\"0\" class=\"email-panel\" style=\"width:100%;max-width:680px;background:#ffffff;border-radius:8px;overflow:hidden;\">
          <tr>
            <td style=\"height:6px;background:#006493;font-size:0;line-height:0;\">&nbsp;</td>
          </tr>
          <tr>
            <td class=\"email-pad\" style=\"padding:28px 34px 24px;\">
              <table role=\"presentation\" width=\"100%\" cellpadding=\"0\" cellspacing=\"0\" border=\"0\">
                <tr>
                  <td style=\"font-size:12px;line-height:18px;letter-spacing:.08em;text-transform:uppercase;color:#006493;font-weight:bold;\">Agora Support Operations</td>
                  <td align=\"right\" class=\"email-muted\" style=\"font-size:12px;line-height:18px;color:#6b7782;\">{request_type_html}</td>
                </tr>
              </table>
              <h1 style=\"margin:14px 0 6px;font-size:25px;line-height:32px;color:inherit;font-weight:700;\">{title_html}</h1>
              <p class=\"email-muted\" style=\"margin:0;font-size:14px;line-height:22px;color:#5c6873;\">Ticket {ticket_html}</p>
            </td>
          </tr>
          <tr>
            <td class=\"email-pad\" style=\"padding:0 34px 8px;\">
              <p style=\"margin:0 0 18px;font-size:16px;line-height:25px;color:inherit;\">{_html_paragraph(intro)}</p>
              <table role=\"presentation\" width=\"100%\" cellpadding=\"0\" cellspacing=\"0\" border=\"0\" class=\"email-surface\" style=\"background:#f5f8fb;border-radius:6px;\">
                <tr><td style=\"padding:16px 18px 8px;font-size:12px;line-height:18px;letter-spacing:.06em;text-transform:uppercase;color:#006493;font-weight:bold;\">Request summary</td></tr>
                <tr><td style=\"padding:0 18px 14px;\"><table role=\"presentation\" width=\"100%\" cellpadding=\"0\" cellspacing=\"0\" border=\"0\">{summary_html}</table></td></tr>
              </table>
              {sections_html}
              {missing_html}
              {original_html}
              {action_html}
            </td>
          </tr>
          <tr>
            <td class=\"email-pad email-muted\" style=\"padding:20px 34px 28px;color:#6b7782;font-size:12px;line-height:18px;border-top:1px solid #e5eaee;\">Internal handoff. Customer data is included for case handling only.</td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>"""


def _html_section(section: InternalEmailSection) -> str:
    title = _html_text(section.title)
    body = _html_paragraph(section.body) if section.body else ""
    fields = _html_field_rows(_normalize_fields(section.fields)) if section.fields else ""
    items = "".join(f"<li style=\"margin:0 0 5px;\">{_html_text(item)}</li>" for item in section.items)
    list_html = f"<ul style=\"margin:0;padding-left:18px;\">{items}</ul>" if items else ""
    tone = _section_tone(section.tone)
    return f"""<table role=\"presentation\" width=\"100%\" cellpadding=\"0\" cellspacing=\"0\" border=\"0\" style=\"margin-top:18px;\"><tr><td class=\"email-surface\" style=\"padding:16px 18px;background:{tone['background']};border-left:4px solid {tone['border']};border-radius:4px;\"><div style=\"font-size:12px;line-height:18px;letter-spacing:.05em;text-transform:uppercase;color:{tone['label']};font-weight:bold;margin-bottom:8px;\">{title}</div>{body}{('<table role=\"presentation\" width=\"100%\" cellpadding=\"0\" cellspacing=\"0\" border=\"0\">' + fields + '</table>') if fields else ''}{list_html}</td></tr></table>"""


def _html_missing_fields(fields: Sequence[str], title: str) -> str:
    if not fields:
        return ""
    items = "".join(f"<li style=\"margin:0 0 5px;\">{_html_text(item)}</li>" for item in fields)
    return f"""<table role=\"presentation\" width=\"100%\" cellpadding=\"0\" cellspacing=\"0\" border=\"0\" style=\"margin-top:18px;\"><tr><td class=\"email-action\" style=\"padding:16px 18px;background:#fff8ed;border-left:4px solid #c77c1a;border-radius:4px;\"><div style=\"font-size:12px;line-height:18px;letter-spacing:.05em;text-transform:uppercase;color:#8a5510;font-weight:bold;margin-bottom:8px;\">{_html_text(title)}</div><ul style=\"margin:0;padding-left:18px;\">{items}</ul></td></tr></table>"""


def _html_original_message(message: str) -> str:
    if not message:
        return ""
    return f"""<table role=\"presentation\" width=\"100%\" cellpadding=\"0\" cellspacing=\"0\" border=\"0\" style=\"margin-top:18px;\"><tr><td class=\"email-quote\" style=\"padding:16px 18px;background:#f8fafb;border:1px solid #dfe6eb;border-radius:4px;\"><div style=\"font-size:12px;line-height:18px;letter-spacing:.05em;text-transform:uppercase;color:#6b7782;font-weight:bold;margin-bottom:8px;\">Original customer message</div><div style=\"font-size:14px;line-height:22px;color:inherit;overflow-wrap:anywhere;word-break:break-word;\">{_html_multiline(message)}</div></td></tr></table>"""


def _html_action(text: str, url: str | None) -> str:
    if not text:
        return ""
    if url:
        return f"""<table role=\"presentation\" width=\"100%\" cellpadding=\"0\" cellspacing=\"0\" border=\"0\" style=\"margin-top:20px;\"><tr><td class=\"email-action\" style=\"padding:17px 18px;background:#edf8fb;border:1px solid #b9dce6;border-radius:4px;\"><div style=\"font-size:15px;line-height:23px;font-weight:bold;color:#174e61;margin-bottom:11px;\">{_html_paragraph(text)}</div><a href=\"{escape(url, quote=True)}\" style=\"display:inline-block;background:#006493;color:#ffffff;text-decoration:none;padding:10px 16px;border-radius:4px;font-size:14px;line-height:18px;font-weight:bold;\">Open handling form</a><div style=\"margin-top:10px;font-size:12px;line-height:18px;color:#5c6873;overflow-wrap:anywhere;\">{_html_text(url)}</div></td></tr></table>"""
    return f"""<table role=\"presentation\" width=\"100%\" cellpadding=\"0\" cellspacing=\"0\" border=\"0\" style=\"margin-top:20px;\"><tr><td class=\"email-action\" style=\"padding:17px 18px;background:#edf8fb;border:1px solid #b9dce6;border-radius:4px;\"><div style=\"font-size:15px;line-height:23px;font-weight:bold;color:#174e61;\">{_html_paragraph(text)}</div></td></tr></table>"""


def _html_field_rows(fields: Sequence[tuple[str, str]]) -> str:
    rows: list[str] = []
    for index, (label, value) in enumerate(fields):
        border = "border-top:1px solid #e5eaee;" if index else ""
        rows.append(
            f"<tr><td class=\"summary-cell\" style=\"width:35%;padding:8px 14px 8px 0;{border}font-size:12px;line-height:18px;color:#6b7782;vertical-align:top;\">{_html_text(label)}</td><td class=\"summary-cell\" style=\"padding:8px 0;{border}font-size:14px;line-height:20px;color:inherit;vertical-align:top;overflow-wrap:anywhere;word-break:break-word;\">{_html_text(value)}</td></tr>"
        )
    return "".join(rows)


def _text_fields(fields: Sequence[tuple[str, str]]) -> str:
    return "\n".join(f"{label}: {value}" for label, value in fields)


def _normalize_fields(fields: Sequence[tuple[str, Any]] | Mapping[str, Any]) -> tuple[tuple[str, str], ...]:
    items = fields.items() if isinstance(fields, Mapping) else fields
    return tuple(
        (_clean_text(label), _display_value(value))
        for label, value in items
        if _clean_text(label) and _display_value(value)
    )


def _display_value(value: Any) -> str:
    if isinstance(value, Mapping):
        return "; ".join(
            f"{_humanize_key(key)}: {_display_value(item)}"
            for key, item in value.items()
            if _display_value(item)
        )
    if isinstance(value, (list, tuple, set, frozenset)):
        return ", ".join(_display_value(item) for item in value if _display_value(item))
    return " ".join(str(value or "").replace("\r", "\n").split()).strip()


def _humanize_key(value: Any) -> str:
    return _clean_text(str(value).replace("_", " ")).capitalize()


def _normalize_multiline(value: Any) -> str:
    return "\n".join(line.strip() for line in str(value or "").replace("\r", "\n").split("\n") if line.strip())


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _html_text(value: Any) -> str:
    return escape(str(value or ""), quote=True)


def _html_multiline(value: str) -> str:
    return "<br>".join(_html_text(line) for line in value.splitlines())


def _html_paragraph(value: str) -> str:
    return _html_multiline(_normalize_multiline(value))


def _safe_http_url(value: Any) -> str | None:
    candidate = _clean_text(value)
    if not candidate:
        return None
    parsed = urlparse(candidate)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        return None
    return candidate


def _section_tone(tone: str) -> dict[str, str]:
    normalized = _clean_text(tone).lower()
    if normalized == "warning":
        return {"background": "#fff8ed", "border": "#c77c1a", "label": "#8a5510"}
    if normalized == "success":
        return {"background": "#eef9f4", "border": "#25845d", "label": "#176340"}
    return {"background": "#f5f8fb", "border": "#78a9c1", "label": "#006493"}
