from __future__ import annotations

import pytest

from autoskillit.smoke_utils import (
    AUDITOR_FINDINGS_MAX_BYTES,
    parse_auditor_findings_output,
)

pytestmark = [pytest.mark.medium]


@pytest.mark.parametrize(
    ("text", "ok", "findings", "form", "reason", "notes"),
    [
        ("```json\n[]\n```", True, [], "fenced", "accepted", []),
        (
            'Summary prose.\n```json\n[{"file":"a.py"}]\n```\n',
            True,
            [{"file": "a.py"}],
            "fenced",
            "accepted",
            [],
        ),
        ("```json\n[]\n```\nTrailing prose.", True, [], "fenced", "accepted", []),
        ("```json\n[]\n``", True, [], "fenced", "accepted", ["closing_fence_incomplete"]),
        ("```json\n[]", True, [], "fenced", "accepted", ["closing_fence_incomplete"]),
        ("[]", True, [], "bare", "accepted", []),
        ("  [ ]\n", True, [], "bare", "accepted", []),
        ("[]\n[]", False, [], "bare", "malformed_json", []),
        ("[", False, [], "bare", "malformed_json", []),
        ('[{"file": "a.py"', False, [], "bare", "malformed_json", []),
        (
            '```json\n[{"file":1}',
            False,
            [],
            "fenced",
            "malformed_json",
            ["closing_fence_incomplete"],
        ),
        (None, False, [], "", "no_final_text", []),
        ("", False, [], "", "no_final_text", []),
        ("   ", False, [], "", "no_final_text", []),
        ("No issues found.", False, [], "bare", "missing_findings_array", []),
        ("```json\n[]\n```\n```json\n[]\n```", False, [], "", "multiple_findings_blocks", []),
        ("```json\n{}\n```", False, [], "fenced", "non_array", []),
        ("{}", False, [], "bare", "non_array", []),
        ("```\n[]\n```", False, [], "bare", "missing_findings_array", []),
        ("x" * (AUDITOR_FINDINGS_MAX_BYTES + 1), False, [], "", "oversized", []),
    ],
)
def test_parse_auditor_findings_output(
    text: str | None,
    ok: bool,
    findings: list[object],
    form: str,
    reason: str,
    notes: list[str],
) -> None:
    result = parse_auditor_findings_output(text)
    assert result == {
        "ok": ok,
        "findings": findings,
        "form": form,
        "reason_code": reason,
        "notes": notes,
    }
