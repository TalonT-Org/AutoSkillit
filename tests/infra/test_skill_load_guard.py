"""Tests for guards/skill_load_guard.py PreToolUse hook."""

from __future__ import annotations

import io
import json
import os
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import pytest

pytestmark = [pytest.mark.layer("infra"), pytest.mark.small]

_GUARDED_TOOLS = ["Read", "Write", "Edit", "Bash", "Grep", "Glob"]


def _run_guard(
    stdin_data: dict | str,
    *,
    tmp_dir: Path,
    provider_profile: str | None = None,
    headless: bool = False,
    session_type: str | None = None,
    agent_backend: str | None = None,
    applicable_guards: str | None = None,
) -> str:
    """Run skill_load_guard.main(), return stdout."""
    from autoskillit.hooks.guards.skill_load_guard import main

    stdin_content = stdin_data if isinstance(stdin_data, str) else json.dumps(stdin_data)

    env_updates: dict[str, str] = {}
    env_removals: list[str] = []

    if provider_profile is not None:
        env_updates["AUTOSKILLIT_PROVIDER_PROFILE"] = provider_profile
    else:
        env_removals.append("AUTOSKILLIT_PROVIDER_PROFILE")

    if headless:
        env_updates["AUTOSKILLIT_HEADLESS"] = "1"
    else:
        env_removals.append("AUTOSKILLIT_HEADLESS")

    if session_type is not None:
        env_updates["AUTOSKILLIT_SESSION_TYPE"] = session_type
    else:
        env_removals.append("AUTOSKILLIT_SESSION_TYPE")

    if agent_backend is not None:
        env_updates["AUTOSKILLIT_AGENT_BACKEND"] = agent_backend
    else:
        env_removals.append("AUTOSKILLIT_AGENT_BACKEND")

    if applicable_guards is not None:
        env_updates["AUTOSKILLIT_APPLICABLE_GUARDS"] = applicable_guards
    else:
        env_removals.append("AUTOSKILLIT_APPLICABLE_GUARDS")

    base_env = {k: v for k, v in os.environ.items() if k not in env_removals}
    base_env.update(env_updates)

    with (
        patch.dict(os.environ, base_env, clear=True),
        patch("sys.stdin", io.StringIO(stdin_content)),
        patch("autoskillit.hooks.guards.skill_load_guard.Path.cwd", return_value=tmp_dir),
    ):
        buf = io.StringIO()
        with redirect_stdout(buf):
            try:
                main()
            except SystemExit:
                pass
        return buf.getvalue()


def _make_event(
    tool_name: str = "Read", session_id: str = "abc123", agent_id: str | None = None
) -> dict:
    event = {
        "tool_name": tool_name,
        "tool_input": {"file_path": "/foo"},
        "session_id": session_id,
    }
    if agent_id is not None:
        event["agent_id"] = agent_id
    return event


def _create_flag(
    tmp_dir: Path, session_id: str = "abc123", content: str = "implement-worktree-no-merge"
) -> None:
    flag = tmp_dir / ".autoskillit" / "temp" / f"skill_guard_{session_id}.flag"
    flag.parent.mkdir(parents=True, exist_ok=True)
    flag.write_text(content)


def test_denies_read_when_flag_absent_and_non_anthropic_headless_skill(tmp_path):
    """T2-1: Deny when all gate conditions met and no flag file."""
    out = _run_guard(
        _make_event("Read"),
        tmp_dir=tmp_path,
        provider_profile="minimax",
        headless=True,
        session_type="skill",
    )
    response = json.loads(out)
    assert response["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "SKILL LOADING REQUIRED" in response["hookSpecificOutput"]["permissionDecisionReason"]


def test_allows_read_when_flag_exists(tmp_path):
    """T2-2: Allow when flag file exists."""
    _create_flag(tmp_path)
    out = _run_guard(
        _make_event("Read"),
        tmp_dir=tmp_path,
        provider_profile="minimax",
        headless=True,
        session_type="skill",
    )
    assert not out.strip()


def test_allows_silently_when_provider_profile_empty(tmp_path):
    """T2-3: Allow when provider profile is not set."""
    out = _run_guard(
        _make_event("Read"),
        tmp_dir=tmp_path,
        provider_profile=None,
        headless=True,
        session_type="skill",
    )
    assert not out.strip()


def test_allows_silently_when_provider_is_anthropic(tmp_path):
    """T2-4: Allow when provider is anthropic."""
    out = _run_guard(
        _make_event("Read"),
        tmp_dir=tmp_path,
        provider_profile="anthropic",
        headless=True,
        session_type="skill",
    )
    assert not out.strip()


def test_allows_silently_when_not_headless(tmp_path):
    """T2-5: Allow when not headless."""
    out = _run_guard(
        _make_event("Read"),
        tmp_dir=tmp_path,
        provider_profile="minimax",
        headless=False,
        session_type="skill",
    )
    assert not out.strip()


def test_allows_silently_when_session_type_not_skill(tmp_path):
    """T2-6: Allow when session type is not skill."""
    out = _run_guard(
        _make_event("Read"),
        tmp_dir=tmp_path,
        provider_profile="minimax",
        headless=True,
        session_type="orchestrator",
    )
    assert not out.strip()


@pytest.mark.parametrize("tool_name", _GUARDED_TOOLS)
def test_denies_all_guarded_tools(tmp_path, tool_name):
    """T2-7: Deny for each guarded native tool."""
    out = _run_guard(
        _make_event(tool_name),
        tmp_dir=tmp_path,
        provider_profile="minimax",
        headless=True,
        session_type="skill",
    )
    response = json.loads(out)
    assert response["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_survives_malformed_stdin(tmp_path):
    """T2-8: Fail-open on malformed JSON."""
    out = _run_guard(
        "not valid json",
        tmp_dir=tmp_path,
        provider_profile="minimax",
        headless=True,
        session_type="skill",
    )
    assert not out.strip()


def test_deny_message_contains_directive_keywords(tmp_path):
    """T2-9: Deny message contains directive keywords."""
    out = _run_guard(
        _make_event("Read"),
        tmp_dir=tmp_path,
        provider_profile="minimax",
        headless=True,
        session_type="skill",
    )
    reason = json.loads(out)["hookSpecificOutput"]["permissionDecisionReason"]
    assert "MANDATORY" in reason
    assert "Skill tool" in reason
    assert "Do NOT" in reason


def test_allows_silently_for_anthropic_case_insensitive(tmp_path):
    """T2-10: Case-insensitive bypass for Anthropic."""
    out = _run_guard(
        _make_event("Read"),
        tmp_dir=tmp_path,
        provider_profile="Anthropic",
        headless=True,
        session_type="skill",
    )
    assert not out.strip()


def test_allows_when_agent_id_present(tmp_path):
    """T2-11: Subagent exemption — allow when agent_id is in payload."""
    out = _run_guard(
        _make_event("Read", agent_id="agent-uuid-123"),
        tmp_dir=tmp_path,
        provider_profile="minimax",
        headless=True,
        session_type="skill",
    )
    assert not out.strip()


def test_denies_when_agent_id_is_empty_string(tmp_path):
    """T2-12: Empty agent_id is falsy — guard proceeds normally."""
    out = _run_guard(
        _make_event("Read", agent_id=""),
        tmp_dir=tmp_path,
        provider_profile="minimax",
        headless=True,
        session_type="skill",
    )
    response = json.loads(out)
    assert response["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_flag_found_via_ancestor_walk_when_cwd_is_subdirectory(tmp_path):
    """T2-13: Flag at project root found when CWD is a subdirectory."""
    project = tmp_path / "project"
    flag_dir = project / ".autoskillit" / "temp"
    flag_dir.mkdir(parents=True)
    (flag_dir / "skill_guard_abc123.flag").write_text("make-plan")

    deep_cwd = project / "sub" / "deep"
    out = _run_guard(
        _make_event("Read"),
        tmp_dir=deep_cwd,
        provider_profile="minimax",
        headless=True,
        session_type="skill",
    )
    assert not out.strip()


def test_denies_when_no_autoskillit_dir_in_ancestors(tmp_path):
    """T2-14: Deny when no .autoskillit/ found in any ancestor (fallback to CWD)."""
    bare_dir = tmp_path / "bare" / "dir"
    out = _run_guard(
        _make_event("Read"),
        tmp_dir=bare_dir,
        provider_profile="minimax",
        headless=True,
        session_type="skill",
    )
    response = json.loads(out)
    assert response["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_guard_auto_exempts_after_deny_count_threshold(tmp_path):
    """T2-15: After DENY_THRESHOLD accumulated denials, the guard auto-writes the flag."""
    from autoskillit.hooks.guards.skill_load_guard import DENY_THRESHOLD

    session_id = "auto_exempt_test"
    deny_dir = tmp_path / ".autoskillit" / "temp" / f"skill_guard_{session_id}_denials"
    deny_dir.mkdir(parents=True)
    for i in range(DENY_THRESHOLD):
        (deny_dir / f"deny_{i}").touch()

    out = _run_guard(
        _make_event("Read", session_id=session_id),
        tmp_dir=tmp_path,
        provider_profile="minimax",
        headless=True,
        session_type="skill",
    )
    assert not out.strip()

    flag_path = tmp_path / ".autoskillit" / "temp" / f"skill_guard_{session_id}.flag"
    assert flag_path.exists()
    assert flag_path.read_text() == "__auto_exempt__"


def test_guard_records_denial_when_below_threshold(tmp_path):
    """T2-16: Each denial creates a file in the denial directory."""
    session_id = "denial_record_test"
    out = _run_guard(
        _make_event("Read", session_id=session_id),
        tmp_dir=tmp_path,
        provider_profile="minimax",
        headless=True,
        session_type="skill",
    )
    response = json.loads(out)
    assert response["hookSpecificOutput"]["permissionDecision"] == "deny"

    deny_dir = tmp_path / ".autoskillit" / "temp" / f"skill_guard_{session_id}_denials"
    assert deny_dir.exists()
    assert len(list(deny_dir.iterdir())) == 1


def test_codex_backend_early_exit(tmp_path):
    """T2-17: Codex backend triggers early exit via applicable_guards env var."""
    out = _run_guard(
        _make_event("Read"),
        tmp_dir=tmp_path,
        provider_profile="minimax",
        headless=True,
        session_type="skill",
        agent_backend="codex",
        applicable_guards="",
    )
    assert not out.strip()


@pytest.mark.parametrize("provider_profile", ["minimax", "anthropic", "", None])
def test_codex_backend_ignores_provider_profile(tmp_path, provider_profile):
    """T2-18: Codex bypass via applicable_guards fires regardless of provider profile."""
    out = _run_guard(
        _make_event("Read"),
        tmp_dir=tmp_path,
        provider_profile=provider_profile,
        headless=True,
        session_type="skill",
        agent_backend="codex",
        applicable_guards="",
    )
    assert not out.strip()


def test_applicable_guards_includes_skill_load_guard_proceeds(tmp_path):
    """T2-18b: When AUTOSKILLIT_APPLICABLE_GUARDS includes skill_load_guard, guard proceeds."""
    out = _run_guard(
        _make_event("Read"),
        tmp_dir=tmp_path,
        provider_profile="minimax",
        headless=True,
        session_type="skill",
        agent_backend="codex",
        applicable_guards="skill_load_guard",
    )
    response = json.loads(out)
    assert response["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_non_codex_backend_still_denies(tmp_path):
    """T2-19: claude-code backend does NOT trigger early exit - deny proceeds."""
    out = _run_guard(
        _make_event("Read"),
        tmp_dir=tmp_path,
        provider_profile="minimax",
        headless=True,
        session_type="skill",
        agent_backend="claude-code",
    )
    response = json.loads(out)
    assert response["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_absent_backend_still_denies(tmp_path):
    """T2-20: Missing AUTOSKILLIT_AGENT_BACKEND does NOT trigger early exit."""
    out = _run_guard(
        _make_event("Read"),
        tmp_dir=tmp_path,
        provider_profile="minimax",
        headless=True,
        session_type="skill",
        agent_backend=None,
    )
    response = json.loads(out)
    assert response["hookSpecificOutput"]["permissionDecision"] == "deny"
