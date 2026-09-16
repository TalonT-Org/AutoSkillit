"""Tests for the Codex PreCompact automatic-compaction veto."""

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


def _run(event: object, *, backend: str = "codex") -> tuple[int, str]:
    result = subprocess.run(
        [sys.executable, str(SCRIPT)],
        input=json.dumps(event),
        capture_output=True,
        text=True,
        env={**production_interpreter_env(), "AUTOSKILLIT_AGENT_BACKEND": backend},
    )
    return result.returncode, result.stdout


def test_auto_compact_guard_vetoes_only_the_codex_automatic_trigger() -> None:
    code, stdout = _run({"hook_event_name": "PreCompact", "trigger": "auto"})

    assert code == 0
    assert json.loads(stdout) == {
        "continue": False,
        "stopReason": CODEX_AUTO_COMPACTION_DENIED_REASON,
        "systemMessage": (
            "AutoSkillit blocked automatic compaction before changing history. "
            "Start a new session, or compact manually and resume deliberately."
        ),
    }


@pytest.mark.parametrize(
    ("event", "backend"),
    [
        ({"hook_event_name": "PreCompact", "trigger": "manual"}, "codex"),
        ({"hook_event_name": "PreCompact", "trigger": "auto"}, "claude-code"),
        ({"trigger": "auto"}, "codex"),
    ],
)
def test_auto_compact_guard_leaves_other_invocations_inert(event: object, backend: str) -> None:
    code, stdout = _run(event, backend=backend)

    assert code == 0
    assert stdout == ""
