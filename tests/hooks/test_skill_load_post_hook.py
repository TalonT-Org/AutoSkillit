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
) -> tuple[str, int]:
    """Run skill_load_post_hook.main(), return (stdout, exit_code)."""
    from autoskillit.hooks.skill_load_post_hook import main  # noqa: PLC0415

    stdin_content = stdin_data if isinstance(stdin_data, str) else json.dumps(stdin_data)

    env_base = {k: v for k, v in os.environ.items() if k != "AUTOSKILLIT_PROVIDER_PROFILE"}
    if provider_profile is not None:
        env_base["AUTOSKILLIT_PROVIDER_PROFILE"] = provider_profile

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
    session_id: str = "abc123", skill: str = "implement-worktree-no-merge"
) -> dict:
    return {
        "tool_name": "Skill",
        "tool_input": {"skill": skill},
        "session_id": session_id,
    }


def test_writes_flag_when_provider_profile_set(tmp_path: Path) -> None:
    """T1-1: Flag file written with skill name when provider profile is set."""
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
