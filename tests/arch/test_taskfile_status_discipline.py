"""Structural coverage for task status handling around temporary errexit disablement."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from autoskillit.core.io import load_yaml

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]

_TASKFILE = Path(__file__).resolve().parents[2] / "Taskfile.yml"
_DISABLE_ERREXIT = re.compile(r"(?m)^\s*set \+e\s*$")
_ENABLE_ERREXIT = re.compile(r"(?m)^\s*set -e\s*$")
_STATUS_CAPTURE = re.compile(
    r"(?m)^\s*([A-Za-z_][A-Za-z0-9_]*)=\$(?:\?|\{PIPESTATUS\[[0-9]+\]\})\s*$"
)


def _has_status_exit(body: str, status_name: str) -> bool:
    direct_exit = re.compile(rf"\bexit\s+(?:\"?\${status_name}\"?|\${{{status_name}}})\b")
    conditional_exit = re.compile(
        rf"\b(?:if|elif)\b.*?\${status_name}.*?\bthen\b.*?\bexit\s+[1-9][0-9]*\b",
        re.DOTALL,
    )
    return bool(direct_exit.search(body) or conditional_exit.search(body))


def test_tasks_restore_and_propagate_captured_status_after_set_plus_e() -> None:
    """Temporary errexit disablement must not allow a task failure to disappear."""
    taskfile = load_yaml(_TASKFILE)
    assert isinstance(taskfile, dict)
    tasks = taskfile["tasks"]
    assert isinstance(tasks, dict)

    for task_name, task in tasks.items():
        assert isinstance(task, dict)
        body = "\n".join(str(command) for command in task.get("cmds", []))
        for disabled in _DISABLE_ERREXIT.finditer(body):
            enabled = _ENABLE_ERREXIT.search(body, disabled.end())
            assert enabled is not None, f"{task_name}: set +e is never paired with set -e"

            capture = _STATUS_CAPTURE.search(body, disabled.end(), enabled.start())
            assert capture is not None, (
                f"{task_name}: set +e must capture $? or ${{PIPESTATUS[...]}} before set -e"
            )
            assert _has_status_exit(body[enabled.end() :], capture.group(1)), (
                f"{task_name}: captured status {capture.group(1)!r} is not used on an exit path"
            )
