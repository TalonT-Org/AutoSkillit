"""Tests for the session reload sentinel and loop mechanics."""

from __future__ import annotations

import json
import shutil
import subprocess
from collections.abc import Mapping
from contextlib import contextmanager
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("cli"), pytest.mark.small]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@contextmanager
def _noop_terminal_guard():  # type: ignore[misc]
    yield


def _make_result(returncode: int = 0) -> object:
    return type("Result", (), {"returncode": returncode})()


def _write_sentinel(project_dir: Path, session_id: str) -> Path:
    sentinel_dir = project_dir / ".autoskillit" / "temp" / "reload_sentinel"
    sentinel_dir.mkdir(parents=True, exist_ok=True)
    sentinel = sentinel_dir / f"{session_id}.json"
    sentinel.write_text(
        json.dumps({"session_id": session_id, "requested_at": "2026-01-01T00:00:00+00:00"}),
        encoding="utf-8",
    )
    return sentinel


# ---------------------------------------------------------------------------
# RL-1 — _run_cook_session returns None on normal exit (no sentinel)
# ---------------------------------------------------------------------------


def test_cook_session_no_sentinel_returns_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: _make_result(0))
    monkeypatch.setattr("autoskillit.cli._cook.terminal_guard", _noop_terminal_guard)

    from autoskillit.cli._cook import _run_cook_session

    result = _run_cook_session(
        cmd=["claude"],
        env={},
        _first_run=False,
        initial_prompt=None,
        project_dir=tmp_path,
    )
    assert result is None


# ---------------------------------------------------------------------------
# RL-2 — _run_cook_session returns session_id when sentinel file exists
# ---------------------------------------------------------------------------


def test_cook_session_with_sentinel_returns_session_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_sentinel(tmp_path, "sess-001")
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: _make_result(0))
    monkeypatch.setattr("autoskillit.cli._cook.terminal_guard", _noop_terminal_guard)

    from autoskillit.cli._cook import _run_cook_session

    result = _run_cook_session(
        cmd=["claude"],
        env={},
        _first_run=False,
        initial_prompt=None,
        project_dir=tmp_path,
    )
    assert result == "sess-001"


# ---------------------------------------------------------------------------
# RL-3 — _run_cook_session deletes sentinel after reading it
# ---------------------------------------------------------------------------


def test_cook_session_sentinel_consumed_after_reload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sentinel = _write_sentinel(tmp_path, "sess-del")
    assert sentinel.exists()

    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: _make_result(0))
    monkeypatch.setattr("autoskillit.cli._cook.terminal_guard", _noop_terminal_guard)

    from autoskillit.cli._cook import _run_cook_session

    _run_cook_session(
        cmd=["claude"],
        env={},
        _first_run=False,
        initial_prompt=None,
        project_dir=tmp_path,
    )
    assert not sentinel.exists()


# ---------------------------------------------------------------------------
# RL-4 — cook() reload loop rebuilds with NamedResume on reload
# ---------------------------------------------------------------------------


def test_cook_reload_loop_uses_named_resume(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from autoskillit.execution.commands import ClaudeInteractiveCmd

    run_count = [0]
    captured_resume_specs: list = []

    def fake_run_cook_session(*, cmd, env, _first_run, initial_prompt, project_dir):
        run_count[0] += 1
        if run_count[0] == 1:
            return "sess-001"
        return None

    def fake_build_interactive_cmd(**kwargs):
        captured_resume_specs.append(kwargs.get("resume_spec"))
        return ClaudeInteractiveCmd(cmd=["claude"], env={})

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(shutil, "which", lambda x: "/usr/bin/claude")
    monkeypatch.setattr("builtins.input", lambda _prompt="": "")
    monkeypatch.setattr("autoskillit.cli._onboarding.is_first_run", lambda _: False)
    monkeypatch.setattr("autoskillit.cli._cook._run_cook_session", fake_run_cook_session)
    monkeypatch.setattr("autoskillit.execution.build_interactive_cmd", fake_build_interactive_cmd)

    from autoskillit.workspace.session_skills import DefaultSessionSkillManager

    fake_skills_dir = tmp_path / "fake-skills"
    fake_skills_dir.mkdir()
    monkeypatch.setattr(
        DefaultSessionSkillManager,
        "init_session",
        lambda self, sid, *, cook_session=False, config=None, project_dir=None: fake_skills_dir,
    )

    from autoskillit import cli
    from autoskillit.core import NamedResume

    cli.cook()

    assert run_count[0] == 2
    assert len(captured_resume_specs) == 2
    assert isinstance(captured_resume_specs[1], NamedResume)
    assert captured_resume_specs[1].session_id == "sess-001"


# ---------------------------------------------------------------------------
# RL-5 — _run_interactive_session returns session_id when sentinel exists
# ---------------------------------------------------------------------------


def test_interactive_session_reload_uses_named_resume(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_sentinel(tmp_path, "isess-001")
    monkeypatch.setattr(shutil, "which", lambda _: "/usr/bin/claude")
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: _make_result(0))
    monkeypatch.setattr("autoskillit.cli._terminal.terminal_guard", _noop_terminal_guard)
    monkeypatch.setattr("autoskillit.cli._init_helpers._is_plugin_installed", lambda: True)

    from autoskillit.cli._session_launch import _run_interactive_session

    result = _run_interactive_session(system_prompt="test", project_dir=tmp_path)
    assert result == "isess-001"


# ---------------------------------------------------------------------------
# RL-6 — Franchise reload re-launches with same system_prompt, no --resume
# ---------------------------------------------------------------------------


def test_franchise_reload_relaunches_without_resume(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from autoskillit.core import NoResume

    call_count = [0]
    captured_resume_specs: list = []

    def fake_run_interactive_session(prompt, *, extra_env=None, resume_spec=None, project_dir=None):
        call_count[0] += 1
        captured_resume_specs.append(resume_spec)
        if call_count[0] == 1:
            return "franchise-sess"
        return None

    monkeypatch.setattr(
        "autoskillit.cli._session_launch._run_interactive_session", fake_run_interactive_session
    )
    monkeypatch.setattr(
        "autoskillit.cli._mcp_names.detect_autoskillit_mcp_prefix", lambda: "autoskillit"
    )
    monkeypatch.setattr(
        "autoskillit.cli._prompts._build_franchise_open_prompt",
        lambda mcp_prefix: "test-prompt",
    )
    monkeypatch.chdir(tmp_path)

    from autoskillit.cli._franchise import _launch_franchise_session

    _launch_franchise_session(
        campaign_recipe=None,
        campaign_id=None,
        state_path=None,
        resume_metadata=None,
    )

    assert call_count[0] == 2
    # Franchise always re-launches with NoResume — no resume on reload
    assert all(isinstance(r, NoResume) for r in captured_resume_specs)


# ---------------------------------------------------------------------------
# RL-7 — Non-zero exit without sentinel raises SystemExit
# ---------------------------------------------------------------------------


def test_non_zero_exit_without_sentinel_raises_system_exit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: _make_result(42))
    monkeypatch.setattr("autoskillit.cli._cook.terminal_guard", _noop_terminal_guard)

    from autoskillit.cli._cook import _run_cook_session

    with pytest.raises(SystemExit) as exc_info:
        _run_cook_session(
            cmd=["claude"],
            env={},
            _first_run=False,
            initial_prompt=None,
            project_dir=tmp_path,
        )
    assert exc_info.value.code == 42
