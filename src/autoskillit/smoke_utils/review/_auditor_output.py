"""Strict parser for findings arrays returned by review auditors.

Parsing the complete body rejects syntactically incomplete JSON and accepts
empty arrays. A cutoff after a complete array cannot be detected by parsing alone.
"""

from __future__ import annotations

import json

import regex as re

from autoskillit.smoke_utils.review._constants import AUDITOR_FINDINGS_MAX_BYTES

_JSON_FENCE_OPEN = re.compile(r"^[ \t]*```[ \t]*json[ \t]*$", re.IGNORECASE | re.MULTILINE)
_FENCE_CLOSE = re.compile(r"^[ \t]*```[ \t]*$", re.MULTILINE)


def _result(
    *,
    findings: list[object] | None = None,
    form: str = "",
    reason_code: str,
    notes: list[str] | None = None,
) -> dict[str, object]:
    ok = reason_code == "accepted"
    return {
        "ok": ok,
        "findings": findings if ok and findings is not None else [],
        "form": form,
        "reason_code": reason_code,
        "notes": notes or [],
    }


def parse_auditor_findings_output(text: str | None) -> dict[str, object]:
    """Accept one complete JSON array, bare or inside a single JSON fence."""
    if text is None or not text.strip():
        return _result(reason_code="no_final_text")
    if len(text.encode("utf-8")) > AUDITOR_FINDINGS_MAX_BYTES:
        return _result(reason_code="oversized")

    fences = list(_JSON_FENCE_OPEN.finditer(text))
    if len(fences) > 1:
        return _result(reason_code="multiple_findings_blocks")

    form = "fenced" if fences else "bare"
    body = text
    notes: list[str] = []
    if fences:
        opening = fences[0]
        closing = _FENCE_CLOSE.search(text, opening.end())
        if closing is None:
            body = text[opening.end() :].rstrip().rstrip("`").rstrip()
            notes.append("closing_fence_incomplete")
        else:
            body = text[opening.end() : closing.start()]

    stripped = body.strip()
    try:
        parsed = json.loads(stripped)
    except (json.JSONDecodeError, UnicodeDecodeError, TypeError, ValueError):
        reason = (
            "malformed_json" if fences or stripped.startswith("[") else "missing_findings_array"
        )
        return _result(form=form, reason_code=reason, notes=notes)
    if not isinstance(parsed, list):
        return _result(form=form, reason_code="non_array", notes=notes)
    return _result(findings=parsed, form=form, reason_code="accepted", notes=notes)
