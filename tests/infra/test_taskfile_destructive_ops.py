from __future__ import annotations

import re
from pathlib import Path

import pytest

from autoskillit.core.io import load_yaml

pytestmark = [pytest.mark.layer("infra"), pytest.mark.small]

TASKFILE = Path(__file__).resolve().parents[2] / "Taskfile.yml"

AUDITED_DESTRUCTIVE_TASKFILE_OPS: dict[str, str] = {
    "cleanup-shm::headless-*": (
        "Age-gated cleanup for session artifacts owned by the #3214 session lifecycle."
    ),
    "cleanup-shm::pytest_tmp_lifecycle.py reap": (
        "Liveness-verified pytest reaper; macOS ps environment visibility is narrower, "
        "and scan failures prevent deletion."
    ),
    "install-worktree::uv venv --clear": (
        "Per-worktree environment rebuild; same-worktree concurrency remains tracked separately."
    ),
}

_RECURSIVE_RM = re.compile(r"(?:^|\s)rm\s+-[A-Za-z]*r[A-Za-z]*(?:\s|$)")
_FIND_EXEC_RM = re.compile(r"\bfind\b.*\s-exec\s+rm\s+-[A-Za-z]*r[A-Za-z]*")


def _destructive_operations() -> set[str]:
    taskfile = load_yaml(TASKFILE)
    findings: set[str] = set()
    for task_name, task in taskfile["tasks"].items():
        for command in task.get("cmds", []):
            for line in str(command).splitlines():
                if "uv venv --clear" in line:
                    findings.add(f"{task_name}::uv venv --clear")
                if "pytest_tmp_lifecycle.py reap" in line:
                    findings.add(f"{task_name}::pytest_tmp_lifecycle.py reap")
                if _FIND_EXEC_RM.search(line) or _RECURSIVE_RM.search(line):
                    marker = "headless-*" if "headless-*" in line else line.strip()
                    findings.add(f"{task_name}::{marker}")
    return findings


def test_every_destructive_taskfile_operation_is_audited() -> None:
    """Recursive deletion must be explicit, justified, and bidirectionally registered."""
    actual = _destructive_operations()
    audited = set(AUDITED_DESTRUCTIVE_TASKFILE_OPS)
    assert actual == audited, (
        "Taskfile destructive-operation invariant violated. Pytest lifecycle cleanup must "
        "route through scripts/pytest_tmp_lifecycle.py; every other recursive deletion "
        "needs an exact AUDITED_DESTRUCTIVE_TASKFILE_OPS entry. "
        f"Unaudited: {sorted(actual - audited)}; stale entries: {sorted(audited - actual)}"
    )


def test_destructive_operation_justifications_are_specific() -> None:
    assert all(len(reason.split()) >= 6 for reason in AUDITED_DESTRUCTIVE_TASKFILE_OPS.values())
