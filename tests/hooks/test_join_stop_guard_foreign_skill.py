"""A foreign skill load never poisons Stop in a non-cook interactive session (R1)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.hooks._interactive_guard_harness import (
    GuardRuntime,
    bind,
    bundled_entries,
    interactive_env,
    make_runtime,
    run_guard,
)

pytestmark = [pytest.mark.layer("infra"), pytest.mark.medium]


def _stop(runtime: GuardRuntime, tmp_path: Path) -> str:
    result = run_guard(
        runtime,
        "join_stop_guard.py",
        {"hook_event_name": "Stop", "session_id": "interactive", "cwd": str(runtime.project)},
        env=interactive_env(
            runtime,
            AUTOSKILLIT_SESSION_TYPE="skill",
            AUTOSKILLIT_LOG_DIR=str(tmp_path / "logs"),
        ),
    )
    assert result.returncode == (2 if result.stdout else 0), result.stderr
    return result.stdout


def test_foreign_only_binding_releases_stop(tmp_path: Path) -> None:
    runtime = make_runtime(tmp_path, bundled_entries("investigate"))
    bind(runtime, ("code-review",))

    assert _stop(runtime, tmp_path) == ""


def test_foreign_entry_neither_poisons_nor_suppresses_join_enforcement(tmp_path: Path) -> None:
    alone = make_runtime(tmp_path / "alone", bundled_entries("investigate"))
    bind(alone, ("investigate",))
    mixed = make_runtime(tmp_path / "mixed", bundled_entries("investigate"))
    bind(mixed, ("investigate", "code-review"))

    alone_stop = _stop(alone, tmp_path)
    mixed_stop = _stop(mixed, tmp_path)

    assert mixed_stop == alone_stop
    decision = json.loads(mixed_stop)
    assert decision["decision"] == "block"
    assert "no declared wave" in decision["reason"]


def test_unresolved_autoskillit_skill_still_blocks_stop(tmp_path: Path) -> None:
    runtime = make_runtime(tmp_path, bundled_entries("investigate"))
    bind(runtime, ("autoskillit:ghost",))

    decision = json.loads(_stop(runtime, tmp_path))

    assert decision["decision"] == "block"
    assert decision["reason"] == "Stop cannot verify the required-join binding scope."
