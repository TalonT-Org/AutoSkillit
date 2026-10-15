"""Tests for backend-specific PreCompact automatic-compaction blocking."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from autoskillit.hooks._runtime._hook_constants import CODEX_AUTO_COMPACTION_DENIED_REASON
from tests.conftest import production_interpreter_env

pytestmark = [pytest.mark.layer("hooks"), pytest.mark.small]

SCRIPT = Path(__file__).resolve().parents[2] / "src/autoskillit/hooks/guards/auto_compact_guard.py"


def _run(
    event: object,
    *,
    backend: str = "codex",
    session_type: str | None = None,
) -> tuple[int, str, str]:
    env = {**production_interpreter_env(), "AUTOSKILLIT_AGENT_BACKEND": backend}
    env.pop("AUTOSKILLIT_SESSION_TYPE", None)
    if session_type is not None:
        env["AUTOSKILLIT_SESSION_TYPE"] = session_type
    result = subprocess.run(
        [sys.executable, str(SCRIPT)],
        input=json.dumps(event),
        capture_output=True,
        text=True,
        env=env,
    )
    return result.returncode, result.stdout, result.stderr


def test_auto_compact_guard_preserves_codex_automatic_trigger_veto() -> None:
    code, stdout, stderr = _run({"hook_event_name": "PreCompact", "trigger": "auto"})

    assert code == 0
    assert json.loads(stdout) == {
        "continue": False,
        "stopReason": CODEX_AUTO_COMPACTION_DENIED_REASON,
        "systemMessage": (
            "AutoSkillit blocked automatic compaction before changing history. "
            "Start a new session, or compact manually and resume deliberately."
        ),
    }
    assert stderr == ""


def test_auto_compact_guard_blocks_claude_orchestrator_automatic_trigger() -> None:
    code, stdout, stderr = _run(
        {"hook_event_name": "PreCompact", "trigger": "auto"},
        backend="claude-code",
        session_type="orchestrator",
    )

    assert code == 2
    assert stdout == ""
    assert "compaction" in stderr.lower()


@pytest.mark.parametrize(
    ("event", "backend", "session_type"),
    [
        ({"hook_event_name": "PreCompact", "trigger": "manual"}, "codex", None),
        ({"hook_event_name": "PreCompact", "trigger": "auto"}, "claude-code", "skill"),
        ({"hook_event_name": "PreCompact", "trigger": "auto"}, "claude-code", None),
        (
            {"hook_event_name": "PreCompact", "trigger": "manual"},
            "claude-code",
            "orchestrator",
        ),
        ({"trigger": "auto"}, "codex", None),
    ],
)
def test_auto_compact_guard_leaves_other_invocations_inert(
    event: object,
    backend: str,
    session_type: str | None,
) -> None:
    code, stdout, stderr = _run(event, backend=backend, session_type=session_type)

    assert code == 0
    assert stdout == ""
    assert stderr == ""
