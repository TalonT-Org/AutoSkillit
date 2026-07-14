"""Tests for skill_load_post_hook.py PostToolUse hook."""

from __future__ import annotations

import contextlib
import io
import json
import os
import unittest.mock
from pathlib import Path
from unittest.mock import patch

import pytest

pytestmark = [pytest.mark.layer("infra"), pytest.mark.small]

_FLAG_RELPATH = ".autoskillit/temp/skill_guard_abc123.flag"


def _run_hook(
    *,
    stdin_data: dict | str,
    tmp_dir: Path,
    provider_profile: str | None = None,
    agent_backend: str | None = "claude-code",
) -> tuple[str, int]:
    """Run skill_load_post_hook.main(), return (stdout, exit_code)."""
    from autoskillit.hooks.skill_load_post_hook import main  # noqa: PLC0415

    stdin_content = stdin_data if isinstance(stdin_data, str) else json.dumps(stdin_data)

    env_base = {
        k: v
        for k, v in os.environ.items()
        if k not in ("AUTOSKILLIT_PROVIDER_PROFILE", "AUTOSKILLIT_AGENT_BACKEND")
    }
    if provider_profile is not None:
        env_base["AUTOSKILLIT_PROVIDER_PROFILE"] = provider_profile
    if agent_backend is not None:
        env_base["AUTOSKILLIT_AGENT_BACKEND"] = agent_backend

    buf = io.StringIO()
    exit_code = 0
    with (
        patch.dict(os.environ, env_base, clear=True),
        contextlib.redirect_stdout(buf),
        unittest.mock.patch("sys.stdin", io.StringIO(stdin_content)),
        unittest.mock.patch(
            "autoskillit.hooks.skill_load_post_hook.Path.cwd", return_value=tmp_dir
        ),
    ):
        try:
            main()
        except SystemExit as exc:
            exit_code = int(exc.code) if exc.code is not None else 0

    return buf.getvalue(), exit_code


def _make_skill_event(
    session_id: str = "abc123",
    skill: str = "implement-worktree-no-merge",
    agent_id: str | None = None,
) -> dict:
    event = {
        "tool_name": "Skill",
        "tool_input": {"skill": skill},
        "session_id": session_id,
    }
    if agent_id is not None:
        event["agent_id"] = agent_id
    return event


def test_writes_flag_when_provider_profile_set(tmp_path: Path) -> None:
    """T1-1: Flag file written with skill name when provider profile is set."""
    (tmp_path / ".autoskillit").mkdir(parents=True)
    _run_hook(
        stdin_data=_make_skill_event(),
        tmp_dir=tmp_path,
        provider_profile="minimax",
    )
    flag = tmp_path / _FLAG_RELPATH
    assert flag.exists(), "Flag file must be written"
    assert "implement-worktree-no-merge" in flag.read_text()


def test_skips_when_provider_profile_empty(tmp_path: Path) -> None:
    """T1-2: No flag file when provider profile is not set."""
    _run_hook(
        stdin_data=_make_skill_event(),
        tmp_dir=tmp_path,
        provider_profile=None,
    )
    flag = tmp_path / _FLAG_RELPATH
    assert not flag.exists(), "Flag file must NOT be created when provider profile is empty"


def test_skips_for_non_skill_tool(tmp_path: Path) -> None:
    """T1-3: No flag file for non-Skill tool."""
    event = {
        "tool_name": "Read",
        "tool_input": {"file_path": "/foo"},
        "session_id": "abc123",
    }
    _run_hook(
        stdin_data=event,
        tmp_dir=tmp_path,
        provider_profile="minimax",
    )
    flag = tmp_path / _FLAG_RELPATH
    assert not flag.exists()


def test_survives_malformed_stdin(tmp_path: Path) -> None:
    """T1-4: Exit 0 on malformed JSON."""
    _, exit_code = _run_hook(
        stdin_data="not valid json",
        tmp_dir=tmp_path,
        provider_profile="minimax",
    )
    assert exit_code == 0


def test_skips_when_session_id_absent(tmp_path: Path) -> None:
    """T1-5: No flag file when session_id is missing."""
    event = {
        "tool_name": "Skill",
        "tool_input": {"skill": "make-plan"},
    }
    _run_hook(
        stdin_data=event,
        tmp_dir=tmp_path,
        provider_profile="minimax",
    )
    flag_dir = tmp_path / ".autoskillit" / "temp"
    if flag_dir.exists():
        flags = list(flag_dir.glob("skill_guard_*.flag"))
        assert not flags, "No flag file should be created when session_id is absent"


def _run_hook_with_marker(
    *,
    stdin_data: dict | str,
    tmp_dir: Path,
    provider_profile: str | None = None,
    agent_backend: str | None = "claude-code",
    completion_marker: str | None = None,
) -> tuple[str, int]:
    """Run skill_load_post_hook.main() with AUTOSKILLIT_COMPLETION_MARKER support."""
    from autoskillit.hooks.skill_load_post_hook import main  # noqa: PLC0415

    stdin_content = stdin_data if isinstance(stdin_data, str) else json.dumps(stdin_data)

    env_base = {
        k: v
        for k, v in os.environ.items()
        if k
        not in (
            "AUTOSKILLIT_PROVIDER_PROFILE",
            "AUTOSKILLIT_AGENT_BACKEND",
            "AUTOSKILLIT_COMPLETION_MARKER",
        )
    }
    if provider_profile is not None:
        env_base["AUTOSKILLIT_PROVIDER_PROFILE"] = provider_profile
    if agent_backend is not None:
        env_base["AUTOSKILLIT_AGENT_BACKEND"] = agent_backend
    if completion_marker is not None:
        env_base["AUTOSKILLIT_COMPLETION_MARKER"] = completion_marker

    buf = io.StringIO()
    exit_code = 0
    with (
        patch.dict(os.environ, env_base, clear=True),
        contextlib.redirect_stdout(buf),
        unittest.mock.patch("sys.stdin", io.StringIO(stdin_content)),
        unittest.mock.patch(
            "autoskillit.hooks.skill_load_post_hook.Path.cwd", return_value=tmp_dir
        ),
    ):
        try:
            main()
        except SystemExit as exc:
            exit_code = int(exc.code) if exc.code is not None else 0

    return buf.getvalue(), exit_code


def test_emits_additional_context_when_completion_marker_set(tmp_path: Path) -> None:
    """T1-6: When AUTOSKILLIT_COMPLETION_MARKER is set, hook emits additionalContext JSON."""
    marker = "%%ORDER_UP::abc12345%%"
    stdout, exit_code = _run_hook_with_marker(
        stdin_data=_make_skill_event(),
        tmp_dir=tmp_path,
        provider_profile="minimax",
        completion_marker=marker,
    )
    assert exit_code == 0
    assert stdout.strip(), "Hook must emit additionalContext to stdout"
    payload = json.loads(stdout)
    assert "additionalContext" in payload
    assert marker in payload["additionalContext"]


def test_skips_flag_write_when_agent_id_present(tmp_path: Path) -> None:
    """T1-7: No flag written when agent_id is present (subagent context)."""
    _run_hook(
        stdin_data=_make_skill_event(agent_id="agent-uuid-123"),
        tmp_dir=tmp_path,
        provider_profile="minimax",
    )
    flag_dir = tmp_path / ".autoskillit" / "temp"
    flags = list(flag_dir.glob("skill_guard_*.flag"))
    assert not flags, "No flag file should be created in subagent context"


def test_writes_flag_to_project_root_via_ancestor_walk(tmp_path: Path) -> None:
    """T1-8: Flag written to project root when CWD is a subdirectory."""
    project = tmp_path / "project"
    (project / ".autoskillit").mkdir(parents=True)

    deep_cwd = project / "sub" / "deep"
    _run_hook(
        stdin_data=_make_skill_event(),
        tmp_dir=deep_cwd,
        provider_profile="minimax",
    )
    flag = project / ".autoskillit" / "temp" / "skill_guard_abc123.flag"
    assert flag.exists(), "Flag must be written to project root, not CWD"
    assert "implement-worktree-no-merge" in flag.read_text()


@pytest.mark.parametrize(
    ("agent_backend", "expected_flag"),
    [
        ("codex", False),
        ("claude-code", True),
        (None, True),
        ("unexpected", True),
    ],
    ids=[
        "codex_bypasses_flag_write",
        "claude-code_writes_flag",
        "unset_backend_writes_flag",
        "unrecognized_backend_writes_flag",
    ],
)
def test_skill_load_post_hook_backend_authority(
    tmp_path: Path, agent_backend: str | None, expected_flag: bool
) -> None:
    """Backend identity is the primary gate; provider profile is secondary.

    Codex backend must NEVER trigger the skill-load flag even when the
    provider profile would otherwise suggest a provider-aware session.
    Unset and unrecognized backends do not silently inherit Codex's
    exemption — they fall through to the existing profile check.
    """
    (tmp_path / ".autoskillit").mkdir(parents=True)
    _run_hook(
        stdin_data=_make_skill_event(),
        tmp_dir=tmp_path,
        provider_profile="anthropic",
        agent_backend=agent_backend,
    )
    flag = tmp_path / _FLAG_RELPATH
    if expected_flag:
        assert flag.exists(), "Flag file must be written for non-Codex backends"
    else:
        assert not flag.exists(), "Flag file must NOT be written for Codex backend"


def test_codex_bypass_with_nonempty_profile_writes_no_flag(tmp_path: Path) -> None:
    """The specific bug case: Codex + non-empty Anthropic profile → no flag."""
    (tmp_path / ".autoskillit").mkdir(parents=True)
    _run_hook(
        stdin_data=_make_skill_event(),
        tmp_dir=tmp_path,
        provider_profile="anthropic",
        agent_backend="codex",
    )
    flag = tmp_path / _FLAG_RELPATH
    assert not flag.exists(), "Backend check must win over provider profile"


def test_unrecognized_backend_does_not_inherit_codex_exemption(tmp_path: Path) -> None:
    """An unrecognized backend + non-empty profile must still write the flag.

    Unknown/future backend values fall through to the profile check
    rather than being silently exempted as if they were Codex.
    """
    (tmp_path / ".autoskillit").mkdir(parents=True)
    _run_hook(
        stdin_data=_make_skill_event(),
        tmp_dir=tmp_path,
        provider_profile="minimax",
        agent_backend="future-backend",
    )
    flag = tmp_path / _FLAG_RELPATH
    assert flag.exists(), "Unrecognized backend must not silently bypass the flag write"
