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


def _make_capturing_backend() -> tuple[object, list[dict]]:
    """Return (backend_instance, captured_kwargs_list)."""
    from autoskillit.core import CLAUDE_CODE_CAPABILITIES, CmdSpec

    captured_kwargs: list[dict] = []

    class _CapturingBackend:
        def binary_name(self) -> str:
            return "claude"

        @property
        def capabilities(self):
            return CLAUDE_CODE_CAPABILITIES

        def build_interactive_cmd(self, **kwargs):
            captured_kwargs.append(kwargs)
            return CmdSpec(cmd=("claude", "--dangerously-skip-permissions"), env={})

    return _CapturingBackend(), captured_kwargs


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
# system prompt kwarg forwarded
# ---------------------------------------------------------------------------


def test_run_interactive_session_appends_system_prompt(monkeypatch: pytest.MonkeyPatch) -> None:
    """_run_interactive_session forwards system_prompt kwarg to build_interactive_cmd."""
    backend, captured_kwargs = _make_capturing_backend()
    _capture_subprocess(monkeypatch)
    _run_interactive_session(system_prompt="my-unique-prompt", backend=backend)
    assert len(captured_kwargs) == 1
    assert captured_kwargs[0]["system_prompt"] == "my-unique-prompt"


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
# system prompt kwarg forwarded with resume specs
# ---------------------------------------------------------------------------


def test_run_interactive_session_forwards_system_prompt_with_named_resume(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_run_interactive_session forwards system_prompt and NamedResume to build_interactive_cmd."""
    from autoskillit.core import NamedResume

    backend, captured_kwargs = _make_capturing_backend()
    _capture_subprocess(monkeypatch)
    _run_interactive_session(
        system_prompt="should-not-appear",
        resume_spec=NamedResume(session_id="4b581974-1f19-4aec-8405-78c5ede5e233"),
        backend=backend,
    )
    assert captured_kwargs[0]["system_prompt"] == "should-not-appear"
    assert isinstance(captured_kwargs[0]["resume_spec"], NamedResume)


def test_run_interactive_session_forwards_system_prompt_with_bare_resume(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_run_interactive_session forwards system_prompt and BareResume to build_interactive_cmd."""
    from autoskillit.core import BareResume

    backend, captured_kwargs = _make_capturing_backend()
    _capture_subprocess(monkeypatch)
    _run_interactive_session(
        system_prompt="should-not-appear",
        resume_spec=BareResume(),
        backend=backend,
    )
    assert captured_kwargs[0]["system_prompt"] == "should-not-appear"
    assert isinstance(captured_kwargs[0]["resume_spec"], BareResume)


def test_session_type_cook_order_in_cli_session() -> None:
    from autoskillit.cli.session._constants import SESSION_TYPE_COOK, SESSION_TYPE_ORDER

    assert SESSION_TYPE_COOK == "cook"
    assert SESSION_TYPE_ORDER == "order"


def test_run_interactive_session_forwards_system_prompt_on_fresh_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_run_interactive_session forwards system_prompt with NoResume to build_interactive_cmd."""
    from autoskillit.core import NoResume

    backend, captured_kwargs = _make_capturing_backend()
    _capture_subprocess(monkeypatch)
    _run_interactive_session(
        system_prompt="my-prompt",
        resume_spec=NoResume(),
        backend=backend,
    )
    assert captured_kwargs[0]["system_prompt"] == "my-prompt"
    assert isinstance(captured_kwargs[0]["resume_spec"], NoResume)


# ---------------------------------------------------------------------------
# New tests — backend binary_name() and capability gate
# ---------------------------------------------------------------------------


def test_skill_injection_disabled_omits_flags(monkeypatch: pytest.MonkeyPatch) -> None:
    """When skill_injection_capable=False, _run_interactive_session omits plugin/tools flags
    but still forwards system_prompt to build_interactive_cmd."""
    from autoskillit.core import BackendCapabilities, CmdSpec

    build_kwargs: list[dict] = []
    no_inject_caps = BackendCapabilities(
        channel_b_capable=True,
        pty_required=True,
        session_resume_capable=True,
        skill_injection_capable=False,
        supports_thinking_blocks=True,
        supports_claude_format_stdout=True,
        exit_code_is_terminal=False,
        mcp_config_capable=False,
        food_truck_capable=True,
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
            build_kwargs.append(kwargs)
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
    assert build_kwargs[0]["system_prompt"] == "test"


def test_skill_injection_enabled_passes_tools_to_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    """When skill_injection_capable=True, tools=('AskUserQuestion',) is passed to
    build_interactive_cmd and system_prompt is forwarded."""
    backend, captured_kwargs = _make_capturing_backend()
    _capture_subprocess(monkeypatch)
    _run_interactive_session(system_prompt="test", backend=backend)
    assert captured_kwargs[0]["tools"] == ("AskUserQuestion",)
    assert captured_kwargs[0]["system_prompt"] == "test"


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
                food_truck_capable=True,
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
                food_truck_capable=True,
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


def test_get_backend_di_used_in_session_launch(monkeypatch: pytest.MonkeyPatch) -> None:
    """get_backend DI path in _run_interactive_session invokes stub's build_interactive_cmd."""
    from unittest.mock import MagicMock

    from autoskillit.core import BackendCapabilities, CmdSpec

    build_calls: list[dict] = []

    caps = BackendCapabilities(
        channel_b_capable=True,
        pty_required=True,
        session_resume_capable=True,
        skill_injection_capable=True,
        supports_thinking_blocks=True,
        supports_claude_format_stdout=True,
        exit_code_is_terminal=False,
        mcp_config_capable=False,
        food_truck_capable=True,
        completion_record_types=frozenset({"result"}),
        session_record_types=frozenset({"assistant"}),
    )

    class _DIBackend:
        def binary_name(self) -> str:
            return "claude"

        @property
        def capabilities(self):
            return caps

        def build_interactive_cmd(self, **kwargs):
            build_calls.append(kwargs)
            return CmdSpec(cmd=("claude", "--dangerously-skip-permissions"), env={})

    mock_config = MagicMock()
    mock_config.agent_backend.backend = "claude-code"

    monkeypatch.setattr("autoskillit.config.load_config", lambda: mock_config)
    monkeypatch.setattr("autoskillit.execution.get_backend", lambda name: _DIBackend())
    _stub_plugin_installed(monkeypatch)
    _capture_subprocess(monkeypatch)
    _run_interactive_session(system_prompt="test")
    assert build_calls, "Stub backend's build_interactive_cmd must be invoked via get_backend DI"


def test_skill_injection_false_via_get_backend_forwards_system_prompt_kwarg(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """skill_injection_capable=False stub via get_backend DI still receives system_prompt kwarg."""
    from unittest.mock import MagicMock

    from autoskillit.core import BackendCapabilities, CmdSpec

    build_kwargs: list[dict] = []
    no_inject_caps = BackendCapabilities(
        channel_b_capable=True,
        pty_required=True,
        session_resume_capable=True,
        skill_injection_capable=False,
        supports_thinking_blocks=True,
        supports_claude_format_stdout=True,
        exit_code_is_terminal=False,
        mcp_config_capable=False,
        food_truck_capable=True,
        completion_record_types=frozenset({"result"}),
        session_record_types=frozenset({"assistant"}),
    )

    class _NoInjectDIBackend:
        def binary_name(self) -> str:
            return "claude"

        @property
        def capabilities(self):
            return no_inject_caps

        def build_interactive_cmd(self, **kwargs):
            build_kwargs.append(kwargs)
            return CmdSpec(cmd=("claude", "--dangerously-skip-permissions"), env={})

    mock_config = MagicMock()
    mock_config.agent_backend.backend = "claude-code"

    monkeypatch.setattr("autoskillit.config.load_config", lambda: mock_config)
    monkeypatch.setattr("autoskillit.execution.get_backend", lambda name: _NoInjectDIBackend())
    monkeypatch.setattr(shutil, "which", lambda _: "/usr/bin/claude")

    def mock_run(cmd, **kwargs):
        return type("Result", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr(subprocess, "run", mock_run)
    _run_interactive_session(system_prompt="sentinel")
    assert build_kwargs[0]["system_prompt"] == "sentinel"


# ---------------------------------------------------------------------------
# T-1a: Injection mismatch — skill_injection_capable=True, binary_name="codex"
# ---------------------------------------------------------------------------


def test_codex_like_backend_no_claude_flags(monkeypatch: pytest.MonkeyPatch) -> None:
    """A backend with skill_injection_capable=True but binary_name='codex' must not receive
    Claude-specific flags (--plugin-dir / --tools AskUserQuestion) in the subprocess command."""
    from autoskillit.core import BackendCapabilities, CmdSpec

    caps = BackendCapabilities(
        channel_b_capable=False,
        pty_required=True,
        session_resume_capable=True,
        skill_injection_capable=True,
        supports_thinking_blocks=False,
        supports_claude_format_stdout=False,
        exit_code_is_terminal=True,
        mcp_config_capable=False,
        food_truck_capable=False,
        completion_record_types=frozenset(),
        session_record_types=frozenset(),
    )

    class _CodexLikeBackend:
        def binary_name(self) -> str:
            return "codex"

        @property
        def capabilities(self):
            return caps

        def build_interactive_cmd(self, **kwargs):
            return CmdSpec(cmd=("codex", "--dangerously-bypass-approvals-and-sandbox"), env={})

    captured: dict = {}
    monkeypatch.setattr(shutil, "which", lambda _: "/usr/bin/codex")

    def mock_run(cmd, **kwargs):
        captured["cmd"] = list(cmd)
        return type("Result", (), {"returncode": 0})()

    monkeypatch.setattr(subprocess, "run", mock_run)
    _run_interactive_session(system_prompt="test", backend=_CodexLikeBackend())
    assert ClaudeFlags.PLUGIN_DIR not in captured["cmd"]
    assert ClaudeFlags.TOOLS not in captured["cmd"]
    assert "AskUserQuestion" not in captured["cmd"]


# ---------------------------------------------------------------------------
# T-1b: Feature flag gate — codex backend without codex_backend feature enabled
# ---------------------------------------------------------------------------


def test_feature_flag_gate_blocks_codex_backend_without_feature(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When config specifies backend='codex' but codex_backend feature is disabled,
    _run_interactive_session falls back to claude-code backend."""
    from unittest.mock import MagicMock

    from autoskillit.core import CmdSpec

    backends_used: list[str] = []

    class _ClaudeStub:
        def binary_name(self) -> str:
            return "claude"

        @property
        def capabilities(self):
            from autoskillit.core import CLAUDE_CODE_CAPABILITIES

            return CLAUDE_CODE_CAPABILITIES

        def build_interactive_cmd(self, **kwargs):
            backends_used.append("claude-code")
            return CmdSpec(cmd=("claude", "--dangerously-skip-permissions"), env={})

    class _CodexStub:
        def binary_name(self) -> str:
            return "codex"

        @property
        def capabilities(self):
            from autoskillit.core import BackendCapabilities

            return BackendCapabilities(
                channel_b_capable=False,
                pty_required=True,
                session_resume_capable=True,
                skill_injection_capable=True,
                supports_thinking_blocks=False,
                supports_claude_format_stdout=False,
                exit_code_is_terminal=True,
                mcp_config_capable=False,
                food_truck_capable=False,
                completion_record_types=frozenset(),
                session_record_types=frozenset(),
            )

        def build_interactive_cmd(self, **kwargs):
            backends_used.append("codex")
            return CmdSpec(cmd=("codex", "--dangerously-bypass-approvals-and-sandbox"), env={})

    mock_config = MagicMock()
    mock_config.agent_backend.backend = "codex"
    mock_config.features = {}
    mock_config.experimental_enabled = False

    def fake_get_backend(name: str):
        if name == "claude-code":
            return _ClaudeStub()
        return _CodexStub()

    monkeypatch.setattr("autoskillit.config.load_config", lambda: mock_config)
    monkeypatch.setattr("autoskillit.execution.get_backend", fake_get_backend)
    monkeypatch.setattr(shutil, "which", lambda _: "/usr/bin/claude")
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **kw: type("Result", (), {"returncode": 0})()
    )
    _run_interactive_session(system_prompt="test")
    assert backends_used == ["claude-code"], (
        f"Expected fallback to claude-code, got: {backends_used}"
    )


# ---------------------------------------------------------------------------
# T-1c: _launch_cook_session accepts backend= parameter
# ---------------------------------------------------------------------------


def test_launch_cook_session_accepts_backend_param(monkeypatch: pytest.MonkeyPatch) -> None:
    """_launch_cook_session must accept a backend= kwarg and forward it to
    _run_interactive_session, which calls backend.build_interactive_cmd."""
    from autoskillit.cli.session._session_launch import _launch_cook_session
    from autoskillit.core import CLAUDE_CODE_CAPABILITIES, CmdSpec

    build_calls: list[dict] = []

    class _CapturingBackend:
        def binary_name(self) -> str:
            return "claude"

        @property
        def capabilities(self):
            return CLAUDE_CODE_CAPABILITIES

        def build_interactive_cmd(self, **kwargs):
            build_calls.append(kwargs)
            return CmdSpec(cmd=("claude", "--dangerously-skip-permissions"), env={})

    monkeypatch.setattr(shutil, "which", lambda _: "/usr/bin/claude")
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **kw: type("Result", (), {"returncode": 0})()
    )
    _stub_plugin_installed(monkeypatch, installed=True)
    _launch_cook_session(system_prompt="test", backend=_CapturingBackend())
    assert build_calls, "backend.build_interactive_cmd must be called via _launch_cook_session"


# ---------------------------------------------------------------------------
# T-1d: Multi-backend integration contract — no cross-backend flag contamination
# ---------------------------------------------------------------------------


def test_multi_backend_no_cross_flag_contamination(monkeypatch: pytest.MonkeyPatch) -> None:
    """Each backend in BACKEND_REGISTRY must not produce flags from other backends."""
    from autoskillit.core import ClaudeFlags
    from autoskillit.execution.backends import BACKEND_REGISTRY

    captured: dict = {}
    monkeypatch.setattr(shutil, "which", lambda _: "/usr/bin/fake-agent")

    def mock_run(cmd, **kwargs):
        captured["cmd"] = list(cmd)
        return type("Result", (), {"returncode": 0})()

    monkeypatch.setattr(subprocess, "run", mock_run)
    _stub_plugin_installed(monkeypatch, installed=False)

    for backend_name, backend_cls in BACKEND_REGISTRY.items():
        backend = backend_cls()
        captured.clear()
        _run_interactive_session(system_prompt="test", backend=backend)
        cmd = captured.get("cmd", [])
        assert cmd[0] == backend.binary_name(), (
            f"{backend_name}: cmd must start with binary_name(), got {cmd[0]!r}"
        )
        if backend.binary_name() != "claude":
            assert ClaudeFlags.PLUGIN_DIR not in cmd, (
                f"{backend_name}: must not contain {ClaudeFlags.PLUGIN_DIR!r}"
            )
            assert ClaudeFlags.TOOLS not in cmd, (
                f"{backend_name}: must not contain {ClaudeFlags.TOOLS!r}"
            )
