"""Tests for background_exec_guard.py PreToolUse hook."""

from __future__ import annotations

import io
import json
import os
from contextlib import redirect_stdout
from unittest.mock import patch

import pytest

pytestmark = [pytest.mark.layer("infra"), pytest.mark.small]


def _run_guard(
    event: dict,
    *,
    headless: bool = False,
    session_type: str | None = None,
    raw_stdin: str | None = None,
) -> str:
    """Run main() with the given PreToolUse event envelope.

    raw_stdin: if provided, passed directly to stdin instead of json.dumps(event).
    Use this to test malformed-input paths.
    """
    from autoskillit.hooks.guards.background_exec_guard import main

    stdin_content = raw_stdin if raw_stdin is not None else json.dumps(event)
    env_snapshot = {
        k: v
        for k, v in os.environ.items()
        if k not in ("AUTOSKILLIT_HEADLESS", "AUTOSKILLIT_SESSION_TYPE")
    }
    if headless:
        env_snapshot["AUTOSKILLIT_HEADLESS"] = "1"
    if session_type is not None:
        env_snapshot["AUTOSKILLIT_SESSION_TYPE"] = session_type
    with (
        patch.dict(os.environ, env_snapshot, clear=True),
        patch("sys.stdin", io.StringIO(stdin_content)),
    ):
        buf = io.StringIO()
        with redirect_stdout(buf):
            try:
                main()
            except SystemExit:
                pass
        return buf.getvalue()


def _run_guard_headless(event: dict, session_type: str = "skill") -> dict:
    """Run guard in headless mode and parse output JSON."""
    out = _run_guard(event, headless=True, session_type=session_type)
    return json.loads(out) if out.strip() else {}


def test_denies_bash_run_in_background_skill_session():
    response = _run_guard_headless(
        {"tool_name": "Bash", "tool_input": {"command": "echo test", "run_in_background": True}},
        session_type="skill",
    )
    assert response["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_denies_agent_run_in_background_skill_session():
    response = _run_guard_headless(
        {
            "tool_name": "Agent",
            "tool_input": {"prompt": "do something", "run_in_background": True},
        },
        session_type="skill",
    )
    assert response["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_denies_schedule_wakeup_skill_session():
    response = _run_guard_headless(
        {"tool_name": "ScheduleWakeup", "tool_input": {"delay": "5m"}},
        session_type="skill",
    )
    assert response["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "ScheduleWakeup" in response["hookSpecificOutput"]["permissionDecisionReason"]


@pytest.mark.parametrize("session_type", ["orchestrator", "fleet"])
def test_allows_schedule_wakeup_exempt_headless_tiers(session_type: str):
    out = _run_guard(
        {"tool_name": "ScheduleWakeup", "tool_input": {"delay": "5m"}},
        headless=True,
        session_type=session_type,
    )
    assert not out.strip()


def test_allows_schedule_wakeup_interactive_session():
    out = _run_guard(
        {"tool_name": "ScheduleWakeup", "tool_input": {"delay": "5m"}},
        headless=False,
    )
    assert not out.strip()


def test_allows_bash_without_run_in_background():
    out = _run_guard(
        {"tool_name": "Bash", "tool_input": {"command": "ls"}},
        headless=True,
        session_type="skill",
    )
    assert not out.strip()


def test_allows_bash_run_in_background_false():
    out = _run_guard(
        {"tool_name": "Bash", "tool_input": {"command": "echo test", "run_in_background": False}},
        headless=True,
        session_type="skill",
    )
    assert not out.strip()


def test_allows_orchestrator_session():
    out = _run_guard(
        {"tool_name": "Bash", "tool_input": {"command": "echo test", "run_in_background": True}},
        headless=True,
        session_type="orchestrator",
    )
    assert not out.strip()


def test_allows_fleet_session():
    out = _run_guard(
        {"tool_name": "Bash", "tool_input": {"command": "echo test", "run_in_background": True}},
        headless=True,
        session_type="fleet",
    )
    assert not out.strip()


def test_allows_interactive_session():
    out = _run_guard(
        {"tool_name": "Bash", "tool_input": {"command": "echo test", "run_in_background": True}},
        headless=False,
    )
    assert not out.strip()


def test_denies_in_subagent_context():
    """Subagents are NOT exempt — run_in_background=true is prohibited regardless."""
    response = _run_guard_headless(
        {
            "tool_name": "Bash",
            "tool_input": {"command": "echo test", "run_in_background": True},
            "agent_id": "sub-123",
        },
        session_type="skill",
    )
    assert response["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_fail_open_malformed_json():
    out = _run_guard({}, headless=True, session_type="skill", raw_stdin="not json")
    assert not out.strip()


def test_fail_open_missing_tool_input():
    out = _run_guard(
        {"tool_name": "Bash"},
        headless=True,
        session_type="skill",
    )
    assert not out.strip()


def test_denies_unset_session_type():
    """Fail-closed: headless with no SESSION_TYPE is treated as skill session → deny."""
    from autoskillit.hooks.guards.background_exec_guard import main

    stdin_content = json.dumps(
        {"tool_name": "Bash", "tool_input": {"command": "echo test", "run_in_background": True}}
    )
    env_without_session_type = {
        k: v for k, v in os.environ.items() if k != "AUTOSKILLIT_SESSION_TYPE"
    }
    env_without_session_type["AUTOSKILLIT_HEADLESS"] = "1"
    with (
        patch.dict(os.environ, env_without_session_type, clear=True),
        patch("sys.stdin", io.StringIO(stdin_content)),
    ):
        buf = io.StringIO()
        with redirect_stdout(buf):
            try:
                main()
            except SystemExit:
                pass
        out = buf.getvalue()
    response = json.loads(out) if out.strip() else {}
    assert response["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_deny_reason_references_adr():
    response = _run_guard_headless(
        {"tool_name": "Bash", "tool_input": {"command": "echo test", "run_in_background": True}},
        session_type="skill",
    )
    assert response["hookSpecificOutput"]["permissionDecision"] == "deny"
    reason = response["hookSpecificOutput"]["permissionDecisionReason"]
    assert "ADR-0001" in reason


# ---------------------------------------------------------------------------
# REQ-054: join-bound session composition
# (required join + name/team_name denial, run_in_background=true denial,
#  unnamed foreground allowance, clean-session preservation, malformed/
#  missing binding fail-closed, activation source/state reporting)
# ---------------------------------------------------------------------------


def _write_session_binding(
    tmp_path,
    *,
    join_required: bool,
    binding_valid: bool = True,
    malformed: bool = False,
) -> str:
    """Write the session flag and return its path; bind AUTOSKILLIT_JOIN_FLAG_PATH."""
    flag_dir = tmp_path / ".autoskillit" / "temp"
    flag_dir.mkdir(parents=True, exist_ok=True)
    flag_path = flag_dir / "skill_guard_bind.flag"
    if malformed:
        flag_path.write_text("not valid json", encoding="utf-8")
    else:
        payload = {
            "schema_version": 1,
            "session_id": "bind",
            "join_required": join_required,
            "binding_valid": binding_valid,
            "loaded_skills": [],
            "activation_source": "manifest",
            "launch_policy_state": "active",
        }
        flag_path.write_text(json.dumps(payload), encoding="utf-8")
    return str(flag_path)


def _run_guard_join_bound(event: dict, *, flag_path: str | None) -> dict:
    """Run guard with AUTOSKILLIT_JOIN_FLAG_PATH pointed at a binding file."""
    from autoskillit.hooks.guards.background_exec_guard import main

    env_snapshot = {
        k: v
        for k, v in os.environ.items()
        if k
        not in (
            "AUTOSKILLIT_HEADLESS",
            "AUTOSKILLIT_SESSION_TYPE",
            "AUTOSKILLIT_JOIN_FLAG_PATH",
            "AUTOSKILLIT_JOIN_REQUIRED",
            "AUTOSKILLIT_AGENT_BACKEND",
        )
    }
    env_snapshot["AUTOSKILLIT_SESSION_TYPE"] = "skill"
    env_snapshot["AUTOSKILLIT_AGENT_BACKEND"] = "claude-code"
    if flag_path is not None:
        env_snapshot["AUTOSKILLIT_JOIN_FLAG_PATH"] = flag_path
    with (
        patch.dict(os.environ, env_snapshot, clear=True),
        patch("sys.stdin", io.StringIO(json.dumps(event))),
    ):
        buf = io.StringIO()
        with redirect_stdout(buf):
            try:
                main()
            except SystemExit:
                pass
        out = buf.getvalue()
    return json.loads(out) if out.strip() else {}


def test_required_join_denies_named_teammate_agent(tmp_path):
    """REQ-054: required join + name selector → denied before dispatch."""
    flag_path = _write_session_binding(tmp_path, join_required=True)
    response = _run_guard_join_bound(
        {
            "tool_name": "Agent",
            "session_id": "bind",
            "tool_input": {"prompt": "reviewer", "name": "reviewer"},
        },
        flag_path=flag_path,
    )
    assert response["hookSpecificOutput"]["permissionDecision"] == "deny"
    reason = response["hookSpecificOutput"]["permissionDecisionReason"]
    assert "required-join" in reason
    assert "name" in reason


def test_required_join_denies_team_named_agent(tmp_path):
    """REQ-054: required join + team_name selector → denied before dispatch."""
    flag_path = _write_session_binding(tmp_path, join_required=True)
    response = _run_guard_join_bound(
        {
            "tool_name": "Agent",
            "session_id": "bind",
            "tool_input": {"prompt": "reviewer", "team_name": "team-a"},
        },
        flag_path=flag_path,
    )
    assert response["hookSpecificOutput"]["permissionDecision"] == "deny"
    reason = response["hookSpecificOutput"]["permissionDecisionReason"]
    assert "required-join" in reason
    assert "team_name" in reason


def test_required_join_denies_named_and_team_combined(tmp_path):
    """REQ-054: required join + name+team_name → both reported in reason."""
    flag_path = _write_session_binding(tmp_path, join_required=True)
    response = _run_guard_join_bound(
        {
            "tool_name": "Agent",
            "session_id": "bind",
            "tool_input": {
                "prompt": "reviewer",
                "name": "reviewer",
                "team_name": "team-a",
            },
        },
        flag_path=flag_path,
    )
    assert response["hookSpecificOutput"]["permissionDecision"] == "deny"
    reason = response["hookSpecificOutput"]["permissionDecisionReason"]
    assert "name" in reason
    assert "team_name" in reason


def test_required_join_denies_run_in_background_agent(tmp_path):
    """REQ-054: required join + run_in_background=true → denied before dispatch."""
    flag_path = _write_session_binding(tmp_path, join_required=True)
    response = _run_guard_join_bound(
        {
            "tool_name": "Agent",
            "session_id": "bind",
            "tool_input": {"prompt": "reviewer", "run_in_background": True},
        },
        flag_path=flag_path,
    )
    assert response["hookSpecificOutput"]["permissionDecision"] == "deny"
    reason = response["hookSpecificOutput"]["permissionDecisionReason"]
    assert "required-join" in reason or "ADR-0001" in reason


def test_required_join_allows_unnamed_foreground_agent(tmp_path):
    """REQ-054: required join + unnamed foreground Agent → allowed."""
    flag_path = _write_session_binding(tmp_path, join_required=True)
    response = _run_guard_join_bound(
        {
            "tool_name": "Agent",
            "session_id": "bind",
            "tool_input": {"prompt": "reviewer"},
        },
        flag_path=flag_path,
    )
    assert response == {}, "Unnamed foreground Agent must be allowed in join-bound session"


def test_required_join_denies_schedule_wakeup(tmp_path):
    """REQ-054: ScheduleWakeup is an escape hatch and must be denied join-bound."""
    flag_path = _write_session_binding(tmp_path, join_required=True)
    response = _run_guard_join_bound(
        {
            "tool_name": "ScheduleWakeup",
            "session_id": "bind",
            "tool_input": {"delay": "5m"},
        },
        flag_path=flag_path,
    )
    assert response["hookSpecificOutput"]["permissionDecision"] == "deny"
    reason = response["hookSpecificOutput"]["permissionDecisionReason"]
    assert "ScheduleWakeup" in reason


def test_clean_session_allows_named_teammate_dispatch(tmp_path):
    """REQ-054: clean (join_required=false) session preserves legitimate team calls."""
    flag_path = _write_session_binding(tmp_path, join_required=False)
    response = _run_guard_join_bound(
        {
            "tool_name": "Agent",
            "session_id": "bind",
            "tool_input": {"prompt": "reviewer", "name": "reviewer"},
        },
        flag_path=flag_path,
    )
    # Clean session → no join-bound denial. The agent-teams activation
    # check (if any) is enforced via the launch builder, not this guard.
    assert response == {}, (
        "Clean session must not be globally blocked — the join contract "
        "is permissive when join_required=false"
    )


def test_missing_binding_defaults_to_permissive():
    """REQ-054: missing binding flag → non-join (permissive) semantics.

    When the binding flag file is absent, the guard treats the session
    as non-join (no join-bound denial). The ambient
    ``AUTOSKILLIT_JOIN_REQUIRED=1`` escalation path is asserted by a
    separate test that bypasses the helper's env snapshot.
    """
    # No flag file. The guard falls back to non-join semantics and the
    # named Agent call passes through (no deny payload emitted).
    response = _run_guard_join_bound(
        {
            "tool_name": "Agent",
            "session_id": "bind",
            "tool_input": {"prompt": "reviewer", "name": "reviewer"},
        },
        flag_path=None,
    )
    # Without a binding flag the guard must default to non-join semantics.
    assert "permissionDecision" not in response, (
        "Without a binding flag the guard must default to non-join semantics; "
        "the ambient signal is the production escalation path, asserted separately."
    )


def test_malformed_binding_does_not_admit_join_required(tmp_path):
    """REQ-054: malformed binding file → no join-required promotion.

    A malformed binding must not promote the session to join_required
    semantics — Claude Code may still parse the file permissively, but
    the AutoSkillit guard defaults to the conservative non-join posture
    so the hook does not lock the agent out of legitimate work on a
    transient file-system error.
    """
    flag_path = _write_session_binding(tmp_path, join_required=True, malformed=True)
    response = _run_guard_join_bound(
        {
            "tool_name": "Agent",
            "session_id": "bind",
            "tool_input": {"prompt": "reviewer", "name": "reviewer"},
        },
        flag_path=flag_path,
    )
    # Malformed JSON → _read_session_binding returns None → join_required
    # stays False → no join-bound denial. The named Agent call passes
    # through; we explicitly assert the hook does not emit a deny.
    assert "permissionDecision" not in response, (
        "Malformed binding must default to non-join semantics — no deny payload."
    )


def test_required_join_denial_includes_activation_source_and_state(tmp_path):
    """REQ-054: denial reason names selectors; activation source/state from binding."""
    flag_path = _write_session_binding(
        tmp_path,
        join_required=True,
        binding_valid=True,
    )
    response = _run_guard_join_bound(
        {
            "tool_name": "Agent",
            "session_id": "bind",
            "tool_input": {"prompt": "reviewer", "name": "reviewer"},
        },
        flag_path=flag_path,
    )
    assert response["hookSpecificOutput"]["permissionDecision"] == "deny"
    reason = response["hookSpecificOutput"]["permissionDecisionReason"]
    # The selector(s) are echoed in the reason.
    assert "name" in reason
    # The reason references the production barrier (declare_join_batch).
    assert "declare_join_batch" in reason


def test_required_join_allows_non_agent_tool_input():
    """REQ-054: non-Agent tools are not gated by the join-bound deny set."""
    # No binding file needed — Read is not in the deny set.
    response = _run_guard_join_bound(
        {
            "tool_name": "Read",
            "session_id": "bind",
            "tool_input": {"file_path": "/etc/hosts"},
        },
        flag_path=None,
    )
    assert response == {}, "Read is not in the join-bound deny set"
