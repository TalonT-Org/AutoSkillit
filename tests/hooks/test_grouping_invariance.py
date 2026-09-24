"""Grouping must not change decisions made by command guards."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from .test_github_mutation_guard import (
    _bash_event as _github_bash_event,
)
from .test_github_mutation_guard import (
    _decision as _github_decision,
)
from .test_installation_integrity_guard import (
    _bash as _installation_bash,
)
from .test_installation_integrity_guard import (
    _decision as _installation_decision,
)
from .test_installation_integrity_guard import (
    _run as _run_installation_guard,
)
from .test_write_guard import _build_bash_event as _write_bash_event
from .test_write_guard import _run_hook as _run_write_guard

pytestmark = [pytest.mark.layer("hooks"), pytest.mark.small]


def _grouped_forms(command: str) -> tuple[str, str, str]:
    return command, f"({command})", f"{{ {command}; }}"


def _write_decision(output: str) -> str:
    if not output:
        return "allow"
    return json.loads(output)["hookSpecificOutput"]["permissionDecision"]


@pytest.mark.parametrize(
    "command_template",
    [
        "cp /tmp/s {target}",
        "tee {target} < /dev/null",
        "echo x > {target}",
    ],
    ids=["copy", "tee", "redirect"],
)
def test_installation_guard_decision_is_invariant_under_grouping(
    tmp_path: Path, command_template: str
) -> None:
    target = tmp_path / "lib/python3.13/site-packages/autoskillit/x.py"
    command = command_template.replace("{target}", str(target))

    decisions = [
        _installation_decision(_run_installation_guard(_installation_bash(form))[1])
        for form in _grouped_forms(command)
    ]

    assert decisions == ["deny", "deny", "deny"]


def test_write_guard_decision_is_invariant_under_grouping(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AUTOSKILLIT_HEADLESS", "1")
    monkeypatch.delenv("AUTOSKILLIT_AGENT_BACKEND", raising=False)
    monkeypatch.setenv("AUTOSKILLIT_CWD", "/workspace")
    monkeypatch.setenv("AUTOSKILLIT_ALLOWED_WRITE_PREFIX", "/workspace/.autoskillit/temp/")

    cases = [
        ("echo x > /workspace/src/main.py", "deny"),
        ("echo x > /workspace/.autoskillit/temp/result.txt", "allow"),
    ]
    for command, expected in cases:
        decisions = [
            _write_decision(_run_write_guard(_write_bash_event(form)))
            for form in _grouped_forms(command)
        ]
        assert decisions == [expected, expected, expected]


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("gh pr review 7 --comment --body 'review body'", "deny"),
        ("gh pr list", None),
    ],
    ids=["review-mutation", "read-only"],
)
def test_github_guard_decision_is_invariant_under_grouping(
    command: str,
    expected: str | None,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    decisions = [
        _github_decision(
            _github_bash_event(form, cwd=str(tmp_path)),
            monkeypatch,
        )
        for form in _grouped_forms(command)
    ]

    assert decisions == [expected, expected, expected]
