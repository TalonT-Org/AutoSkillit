"""Tests for guards/skill_load_guard.py PreToolUse hook — denies native tools until Skill called."""

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


def _make_event(tool_name: str = "Read", session_id: str = "abc123") -> dict:
    return {
        "tool_name": tool_name,
        "tool_input": {"file_path": "/foo"},
        "session_id": session_id,
    }


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
