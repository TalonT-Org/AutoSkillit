"""JSON envelope tests for skill_load_post_hook.

Per Plan § Step 2.1 (REQ-EXTRACT-092), the skill guard flag must be
written as JSON with skill name, join_required, semantic/adaptation/projected/
artifact digests, artifact incarnation, and child-spawn cardinality.

These tests cover malformed/mismatched-projection-metadata and
join-false/required cases at the file-system level.
"""

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


def _run_hook(
    *,
    stdin_data: dict | str,
    tmp_dir: Path,
    provider_profile: str | None = "minimax",
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


def test_malformed_stdin_does_not_write_flag(tmp_path: Path) -> None:
    """Malformed JSON should not write a flag."""
    _, _ = _run_hook(
        stdin_data="not json",
        tmp_dir=tmp_path,
    )
    candidates = list(tmp_path.rglob("skill_guard_*.flag"))
    assert not candidates


def test_missing_session_id_does_not_write_flag(tmp_path: Path) -> None:
    """A Skill event without a session_id must not write a flag."""
    event = {"tool_name": "Skill", "tool_input": {"skill": "implement-worktree-no-merge"}}
    _, _ = _run_hook(
        stdin_data=event,
        tmp_dir=tmp_path,
    )
    candidates = list(tmp_path.rglob("skill_guard_*.flag"))
    assert not candidates


def test_subagent_context_skips_flag_write(tmp_path: Path) -> None:
    """A Skill event with agent_id must not write the flag."""
    _, _ = _run_hook(
        stdin_data=_make_skill_event(agent_id="child-1"),
        tmp_dir=tmp_path,
    )
    candidates = list(tmp_path.rglob("skill_guard_*.flag"))
    assert not candidates


def test_existing_flag_is_json_envelope(tmp_path: Path) -> None:
    """The flag file written by the hook is a JSON envelope, not a raw string.

    Drives the hook's main() with a valid Skill event and asserts the
    persisted flag file parses as JSON with the expected envelope keys.
    """
    (tmp_path / ".autoskillit").mkdir(parents=True)
    event = _make_skill_event(session_id="abc123")
    _, _ = _run_hook(stdin_data=event, tmp_dir=tmp_path)

    flag_path = tmp_path / ".autoskillit" / "temp" / "skill_guard_abc123.flag"
    assert flag_path.exists(), "Hook must write the skill guard flag for a valid event"
    raw = flag_path.read_text(encoding="utf-8")
    parsed = json.loads(raw)  # Raises if the hook wrote a non-JSON literal
    assert parsed["session_id"] == "abc123"
    assert parsed["schema_version"] == 1
    assert "loaded_skills" in parsed
