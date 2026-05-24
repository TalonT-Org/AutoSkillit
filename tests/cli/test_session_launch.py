"""Tests for cli/_session_launch.py — _run_interactive_session contract."""

from __future__ import annotations

import shutil
import subprocess

import pytest

from autoskillit.cli.session._session_launch import _run_interactive_session
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
    """Stub detect_autoskillit_mcp_prefix to simulate marketplace/direct install."""
    from autoskillit.core._plugin_ids import DIRECT_PREFIX, MARKETPLACE_PREFIX

    prefix = MARKETPLACE_PREFIX if installed else DIRECT_PREFIX
    monkeypatch.setattr("autoskillit.core.detect_autoskillit_mcp_prefix", lambda: prefix)


# ---------------------------------------------------------------------------
# T13. _session_launch.py — plugin flags when plugin not installed
# ---------------------------------------------------------------------------


def test_run_interactive_session_passes_plugin_flags(monkeypatch: pytest.MonkeyPatch) -> None:
    """_run_interactive_session adds --plugin-dir when plugin not installed."""
    _stub_plugin_installed(monkeypatch, installed=False)
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
    """--append-system-prompt <prompt> present in subprocess cmd (via build_interactive_cmd)."""
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


# ---------------------------------------------------------------------------
# system prompt suppressed for resume sessions
# ---------------------------------------------------------------------------


def test_run_interactive_session_suppresses_system_prompt_on_named_resume(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """--append-system-prompt absent on NamedResume (suppressed by build_interactive_cmd)."""
    from autoskillit.core import NamedResume

    _stub_plugin_installed(monkeypatch)
    captured = _capture_subprocess(monkeypatch)
    _run_interactive_session(
        system_prompt="should-not-appear",
        resume_spec=NamedResume(session_id="4b581974-1f19-4aec-8405-78c5ede5e233"),
    )
    assert ClaudeFlags.APPEND_SYSTEM_PROMPT not in captured["cmd"]


def test_run_interactive_session_suppresses_system_prompt_on_bare_resume(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """--append-system-prompt absent on BareResume (suppressed by build_interactive_cmd)."""
    from autoskillit.core import BareResume

    _stub_plugin_installed(monkeypatch)
    captured = _capture_subprocess(monkeypatch)
    _run_interactive_session(
        system_prompt="should-not-appear",
        resume_spec=BareResume(),
    )
    assert ClaudeFlags.APPEND_SYSTEM_PROMPT not in captured["cmd"]


def test_session_type_cook_order_in_cli_session() -> None:
    from autoskillit.cli.session._constants import SESSION_TYPE_COOK, SESSION_TYPE_ORDER

    assert SESSION_TYPE_COOK == "cook"
    assert SESSION_TYPE_ORDER == "order"


def test_run_interactive_session_appends_system_prompt_on_fresh_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """--append-system-prompt present for fresh NoResume sessions (via build_interactive_cmd)."""
    from autoskillit.core import NoResume

    _stub_plugin_installed(monkeypatch)
    captured = _capture_subprocess(monkeypatch)
    _run_interactive_session(
        system_prompt="my-prompt",
        resume_spec=NoResume(),
    )
    assert ClaudeFlags.APPEND_SYSTEM_PROMPT in captured["cmd"]
    idx = captured["cmd"].index(ClaudeFlags.APPEND_SYSTEM_PROMPT)
    assert captured["cmd"][idx + 1] == "my-prompt"


# ---------------------------------------------------------------------------
# New tests — backend binary_name() and capability gate
# ---------------------------------------------------------------------------


def test_skill_injection_disabled_omits_flags(monkeypatch: pytest.MonkeyPatch) -> None:
    """When skill_injection_capable=False, _run_interactive_session omits plugin/tools flags"""
    from autoskillit.core import BackendCapabilities, CmdSpec

    no_inject_caps = BackendCapabilities(
        channel_b_capable=True,
        pty_required=True,
        session_resume_capable=True,
        skill_injection_capable=False,
        supports_thinking_blocks=True,
        supports_claude_format_stdout=True,
        exit_code_is_terminal=False,
        mcp_config_capable=False,
        completion_record_types=frozenset({"result"}),
        session_record_types=frozenset({"assistant"}),
    )

    class _NoInjectBackend:
        def binary_name(self) -> str:
            return "claude"

        @property
        def capabilities(self):
            return no_inject_caps

        def build_interactive_cmd(self, **kwargs):
            return CmdSpec(cmd=("claude", "--dangerously-skip-permissions"), env={})

    from autoskillit.cli.session._session_launch import _run_interactive_session

    captured: dict = {}
    monkeypatch.setattr(shutil, "which", lambda _: "/usr/bin/claude")

    def mock_run(cmd, **kwargs):
        captured["cmd"] = list(cmd)
        captured["env"] = kwargs.get("env", {}) or {}
        return type("Result", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr(subprocess, "run", mock_run)
    _run_interactive_session(system_prompt="test", backend=_NoInjectBackend())
    assert ClaudeFlags.PLUGIN_DIR not in captured["cmd"]
    assert ClaudeFlags.TOOLS not in captured["cmd"]
    assert ClaudeFlags.APPEND_SYSTEM_PROMPT not in captured["cmd"]


def test_skill_injection_enabled_preserves_flags(monkeypatch: pytest.MonkeyPatch) -> None:
    """When skill_injection_capable=True, plugin/tools/system-prompt flags are present."""
    _stub_plugin_installed(monkeypatch)
    captured = _capture_subprocess(monkeypatch)
    _run_interactive_session(system_prompt="test")
    assert ClaudeFlags.TOOLS in captured["cmd"]
    idx = captured["cmd"].index(ClaudeFlags.TOOLS)
    assert captured["cmd"][idx + 1] == "AskUserQuestion"
    assert ClaudeFlags.APPEND_SYSTEM_PROMPT in captured["cmd"]


def test_binary_name_from_backend_used_in_which(monkeypatch: pytest.MonkeyPatch) -> None:
    """shutil.which is called with the backend's binary_name(), not a hardcoded literal."""
    from autoskillit.core import BackendCapabilities, CmdSpec

    captured_which_arg: list = []

    class _CustomBinaryBackend:
        def binary_name(self) -> str:
            return "test-agent-binary"

        @property
        def capabilities(self):
            return BackendCapabilities(
                channel_b_capable=True,
                pty_required=True,
                session_resume_capable=True,
                skill_injection_capable=True,
                supports_thinking_blocks=True,
                supports_claude_format_stdout=True,
                exit_code_is_terminal=False,
                mcp_config_capable=False,
                completion_record_types=frozenset({"result"}),
                session_record_types=frozenset({"assistant"}),
            )

        def build_interactive_cmd(self, **kwargs):
            return CmdSpec(cmd=("test-agent-binary", "--dangerously-skip-permissions"), env={})

    def tracking_which(binary: str):
        captured_which_arg.append(binary)
        if binary == "test-agent-binary":
            return "/usr/bin/test-agent-binary"
        return None

    monkeypatch.setattr(shutil, "which", tracking_which)
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: type("R", (), {"returncode": 0})())
    from autoskillit.cli.session._session_launch import _run_interactive_session

    _run_interactive_session(system_prompt="test", backend=_CustomBinaryBackend())
    assert "test-agent-binary" in captured_which_arg


def test_run_interactive_session_uses_injected_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    """When backend= is passed, _run_interactive_session uses it directly."""
    from autoskillit.core import BackendCapabilities, CmdSpec

    build_called: list[dict] = []

    class _InjectedBackend:
        def binary_name(self) -> str:
            return "claude"

        @property
        def capabilities(self):
            return BackendCapabilities(
                channel_b_capable=True,
                pty_required=True,
                session_resume_capable=True,
                skill_injection_capable=True,
                supports_thinking_blocks=True,
                supports_claude_format_stdout=True,
                exit_code_is_terminal=False,
                mcp_config_capable=False,
                completion_record_types=frozenset({"result"}),
                session_record_types=frozenset({"assistant"}),
            )

        def build_interactive_cmd(self, **kwargs):
            build_called.append(kwargs)
            return CmdSpec(cmd=("claude", "--dangerously-skip-permissions"), env={})

    _stub_plugin_installed(monkeypatch, installed=True)
    _capture_subprocess(monkeypatch)
    _run_interactive_session(system_prompt="test", backend=_InjectedBackend())
    assert build_called, "Injected backend must be used"


def test_run_interactive_session_default_backend_calls_get_backend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When backend= is not passed, _run_interactive_session calls get_backend()."""
    from unittest.mock import MagicMock

    get_backend_called: list = []

    class _FakeBackend:
        def binary_name(self) -> str:
            return "claude"

        @property
        def capabilities(self):
            return MagicMock(
                skill_injection_capable=True,
            )

        def build_interactive_cmd(self, **kwargs):
            from autoskillit.core import CmdSpec

            return CmdSpec(cmd=("claude", "--dangerously-skip-permissions"), env={})

    mock_config = MagicMock()
    mock_config.agent_backend.backend = "claude-code"

    def fake_get_backend(name: str):
        get_backend_called.append(name)
        return _FakeBackend()

    _stub_plugin_installed(monkeypatch, installed=True)
    _capture_subprocess(monkeypatch)
    monkeypatch.setattr("autoskillit.execution.get_backend", fake_get_backend)
    _run_interactive_session(system_prompt="test")
    assert get_backend_called, "get_backend must be called when backend is not injected"
    assert get_backend_called[0] == "claude-code"
