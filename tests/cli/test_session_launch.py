"""Tests for cli/_session_launch.py — _run_interactive_session contract."""

from __future__ import annotations

import shutil
import subprocess

import pytest

from autoskillit.cli._session_launch import _run_interactive_session
from autoskillit.core import ClaudeFlags

pytestmark = [pytest.mark.layer("cli"), pytest.mark.small]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _capture_subprocess(monkeypatch: pytest.MonkeyPatch) -> dict:
    """Replace subprocess.run with a capturing stub. Stubs shutil.which to /usr/bin/claude."""
    captured: dict = {}
    monkeypatch.setattr(shutil, "which", lambda _: "/usr/bin/claude")

    def mock_run(cmd, **kwargs):  # type: ignore[no-untyped-def]
        captured["cmd"] = list(cmd)
        captured["env"] = kwargs.get("env", {}) or {}
        return type("Result", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr(subprocess, "run", mock_run)
    return captured


def _stub_plugin_installed(monkeypatch: pytest.MonkeyPatch, *, installed: bool = True) -> None:
    """Stub _is_plugin_installed to return the given value."""
    monkeypatch.setattr("autoskillit.cli._init_helpers._is_plugin_installed", lambda: installed)


# ---------------------------------------------------------------------------
# T13. _session_launch.py — plugin flags when plugin not installed
# ---------------------------------------------------------------------------


def test_run_interactive_session_passes_plugin_flags(monkeypatch: pytest.MonkeyPatch) -> None:
    """_run_interactive_session adds --plugin-dir when plugin not installed."""
    monkeypatch.setattr("autoskillit.cli._init_helpers._is_plugin_installed", lambda: False)
    captured = _capture_subprocess(monkeypatch)
    _run_interactive_session(system_prompt="test")
    assert ClaudeFlags.PLUGIN_DIR in captured["cmd"]


# ---------------------------------------------------------------------------
# T14. _session_launch.py — tool restriction
# ---------------------------------------------------------------------------


def test_run_interactive_session_restricts_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    """_run_interactive_session passes --tools AskUserQuestion."""
    _stub_plugin_installed(monkeypatch, installed=True)
    captured = _capture_subprocess(monkeypatch)
    _run_interactive_session(system_prompt="test")
    idx = captured["cmd"].index(ClaudeFlags.TOOLS)
    assert captured["cmd"][idx + 1] == "AskUserQuestion"


# ---------------------------------------------------------------------------
# system prompt appended
# ---------------------------------------------------------------------------


def test_run_interactive_session_appends_system_prompt(monkeypatch: pytest.MonkeyPatch) -> None:
    """_run_interactive_session appends --append-system-prompt <prompt>."""
    _stub_plugin_installed(monkeypatch)
    captured = _capture_subprocess(monkeypatch)
    _run_interactive_session(system_prompt="my-unique-prompt")
    assert ClaudeFlags.APPEND_SYSTEM_PROMPT in captured["cmd"]
    idx = captured["cmd"].index(ClaudeFlags.APPEND_SYSTEM_PROMPT)
    assert captured["cmd"][idx + 1] == "my-unique-prompt"


# ---------------------------------------------------------------------------
# env extras passed through
# ---------------------------------------------------------------------------


def test_run_interactive_session_extra_env_merged(monkeypatch: pytest.MonkeyPatch) -> None:
    """extra_env values appear in the subprocess env."""
    _stub_plugin_installed(monkeypatch)
    captured = _capture_subprocess(monkeypatch)
    _run_interactive_session(system_prompt="test", extra_env={"MY_UNIQUE_KEY": "MY_VAL"})
    assert captured["env"].get("MY_UNIQUE_KEY") == "MY_VAL"


# ---------------------------------------------------------------------------
# exits when claude missing
# ---------------------------------------------------------------------------


def test_run_interactive_session_exits_when_claude_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_run_interactive_session exits 1 when claude is not on PATH."""
    monkeypatch.setattr(shutil, "which", lambda _: None)
    with pytest.raises(SystemExit, match="1"):
        _run_interactive_session(system_prompt="test")


# ---------------------------------------------------------------------------
# no plugin dir when plugin installed
# ---------------------------------------------------------------------------


def test_run_interactive_session_no_plugin_dir_when_installed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_run_interactive_session omits --plugin-dir when plugin is installed."""
    _stub_plugin_installed(monkeypatch, installed=True)
    captured = _capture_subprocess(monkeypatch)
    _run_interactive_session(system_prompt="test")
    assert ClaudeFlags.PLUGIN_DIR not in captured["cmd"]
