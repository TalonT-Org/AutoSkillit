"""Behavior of the shared hook session-scope prologue."""

from __future__ import annotations

import io
import json
from contextlib import redirect_stdout
from pathlib import Path

import pytest

from autoskillit.core.types import ALL_SESSION_SHAPES, hookdef_session_scope
from autoskillit.hook_registry import HOOK_REGISTRY
from autoskillit.hooks._runtime._hook_settings import enforce_session_scope

pytestmark = [pytest.mark.layer("hooks"), pytest.mark.small]


def test_prologue_exits_zero_outside_declared_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("AUTOSKILLIT_HEADLESS", raising=False)
    monkeypatch.delenv("AUTOSKILLIT_SESSION_TYPE", raising=False)

    with pytest.raises(SystemExit) as exc_info:
        enforce_session_scope("headless_only")
    assert exc_info.value.code == 0

    monkeypatch.setenv("AUTOSKILLIT_HEADLESS", "1")
    assert enforce_session_scope("headless_only") is None


def test_prologue_applies_exempt_tiers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AUTOSKILLIT_HEADLESS", raising=False)
    monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "orchestrator")

    with pytest.raises(SystemExit) as exc_info:
        enforce_session_scope("any", exempt_tiers=frozenset({"orchestrator"}))
    assert exc_info.value.code == 0

    monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "skill")
    assert enforce_session_scope("any", exempt_tiers=frozenset({"orchestrator"})) is None


def test_invalid_session_type_preserves_policy_denial(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A malformed headless tier reaches its guard after the diagnostic is recorded."""
    monkeypatch.setenv("AUTOSKILLIT_LOG_DIR", str(tmp_path))
    monkeypatch.setenv("AUTOSKILLIT_HEADLESS", "1")
    monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "leaf")

    assert enforce_session_scope("headless_only") is None
    records = [
        json.loads(line)
        for line in (tmp_path / "hook_dispatch_diagnostics.jsonl").read_text().splitlines()
    ]
    assert any(
        record["event_kind"] == "invalid_session_shape" and "leaf" in record["reason"]
        for record in records
    )

    from autoskillit.hooks.guards.skill_orchestration_guard import main

    monkeypatch.setattr(
        "sys.stdin",
        io.StringIO(json.dumps({"tool_name": "mcp__autoskillit__run_skill"})),
    )
    stdout = io.StringIO()
    with redirect_stdout(stdout):
        with pytest.raises(SystemExit) as exc_info:
            main()
    assert exc_info.value.code == 0
    result = json.loads(stdout.getvalue())
    assert result["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "leaf" in result["hookSpecificOutput"]["permissionDecisionReason"]

    monkeypatch.delenv("AUTOSKILLIT_HEADLESS")
    with pytest.raises(SystemExit) as exc_info:
        enforce_session_scope("headless_only")
    assert exc_info.value.code == 0


def test_registry_scope_truth_table(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every registry declaration yields identical hook and core admission."""
    for hookdef in HOOK_REGISTRY:
        scope = hookdef_session_scope(
            hookdef.session_scope,
            hookdef.exempt_session_types,
        )
        for shape in ALL_SESSION_SHAPES:
            if shape.headless:
                monkeypatch.setenv("AUTOSKILLIT_HEADLESS", "1")
            else:
                monkeypatch.delenv("AUTOSKILLIT_HEADLESS", raising=False)
            monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", shape.tier.value)

            if scope.admits(shape):
                assert (
                    enforce_session_scope(
                        hookdef.session_scope,
                        exempt_tiers=hookdef.exempt_session_types,
                    )
                    is None
                )
            else:
                with pytest.raises(SystemExit) as exc_info:
                    enforce_session_scope(
                        hookdef.session_scope,
                        exempt_tiers=hookdef.exempt_session_types,
                    )
                assert exc_info.value.code == 0


def test_expected_hook_scope_declarations() -> None:
    """The registry records the scoped hooks that would otherwise leak globally."""
    hooks_by_script = {script: hookdef for hookdef in HOOK_REGISTRY for script in hookdef.scripts}
    assert hooks_by_script["guards/fleet_dispatch_guard.py"].session_scope == "headless_only"
    assert hooks_by_script["guards/resume_ownership_guard.py"].session_scope == "headless_only"
    assert hooks_by_script["guards/pr_create_guard.py"].exempt_session_types == frozenset(
        {"orchestrator"}
    )
    assert hooks_by_script["session_start_hook.py"].session_scope == "interactive_only"
    assert hooks_by_script["guards/git_ops_guard.py"].session_scope == "any"
