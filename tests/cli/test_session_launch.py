"""Tests for cli/_session_launch.py — _run_interactive_session contract."""

from __future__ import annotations

import shutil
import subprocess
import tomllib
from contextlib import contextmanager, nullcontext
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import MagicMock

import pytest

from autoskillit.cli._plugin_artifact import (
    interactive_plugin_authority as _production_interactive_plugin_authority,
)
from autoskillit.cli.session._session_launch import (
    _launch_cook_session,
    _run_interactive_session,
)
from autoskillit.core import (
    BackendConventions,
    ClaudeFlags,
    HookTrustPolicy,
    PreLaunchReadiness,
)
from autoskillit.core._plugin_ids import (
    detect_autoskillit_mcp_prefix as _production_mcp_prefix,
)
from autoskillit.execution.backends.codex import CodexFlags
from autoskillit.workspace import (
    project_default_plugin_authority as _production_project_default_plugin_authority,
)
from tests.cli._interactive_process import InteractiveProcessStub
from tests.fixtures.plugin_artifact_state import (
    PluginArtifactStateKind,
    build_plugin_artifact_state,
)

pytestmark = [pytest.mark.layer("cli"), pytest.mark.medium]


class _TestBinding:
    def __init__(self, plugin_dir: Path | None) -> None:
        self.plugin_dir = plugin_dir
        self.inherited_fds: tuple[int, ...] = ()
        self.closed = False

    def close(self) -> None:
        self.closed = True


class _TestAuthority:
    def __init__(self, plugin_dir: Path | None) -> None:
        self.plugin_dir = plugin_dir

    def acquire_launch_binding(self, **_kwargs) -> _TestBinding:
        return _TestBinding(self.plugin_dir)


@pytest.fixture(autouse=True)
def _stub_artifact_authorities(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    plugin_dir = tmp_path / "autoskillit-test-plugin"
    plugin_dir.mkdir()
    binary_dir = tmp_path / "bin"
    binary_dir.mkdir()
    binaries: dict[str, Path] = {}
    for name in ("claude", "codex"):
        binary = binary_dir / name
        binary.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        binary.chmod(0o755)
        binaries[name] = binary

    monkeypatch.setattr(
        shutil,
        "which",
        lambda name, **_kwargs: str(binaries[name]) if name in binaries else None,
    )
    monkeypatch.setattr(
        "autoskillit.workspace.project_default_plugin_authority",
        lambda **_kwargs: _TestAuthority(plugin_dir),
    )
    monkeypatch.setattr(
        "autoskillit.cli._plugin_artifact.current_installed_plugin_authority",
        lambda: _TestAuthority(None),
    )


class _BackendLifecycleStub:
    """Projection and lifecycle contract shared by local backend doubles."""

    name = "claude-code"
    conventions = BackendConventions()

    def validate_interactive_invocation(self, spec):
        return []

    def ensure_pre_launch(
        self, *, session_dir: Path | None = None, executable=None
    ) -> PreLaunchReadiness:
        del executable
        return PreLaunchReadiness((), {})

    def recover_cook_history(self) -> None:
        return None

    def cook_session_context(
        self,
        *,
        session_home: Path,
        project_dir: Path,
        launch_id: str,
        attempt: int,
        current_resume_spec,
        ceiling_seconds: float = 172800.0,
        systemd_scope_enabled: bool = False,
    ):
        del project_dir, ceiling_seconds, systemd_scope_enabled
        from autoskillit.core import CookSessionHandle

        return nullcontext(
            CookSessionHandle(
                view_id=f"{launch_id}-{attempt}",
                pass_fds=(),
                _record_spawn=lambda _pid, _pgid: None,
                _record_reaped=lambda _pid, _pgid: None,
            )
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _popen_from_run(run):  # type: ignore[no-untyped-def]
    def popen(cmd, **kwargs):  # type: ignore[no-untyped-def]
        result = run(cmd, **kwargs)
        return InteractiveProcessStub(result.returncode)

    return popen


def _capture_subprocess(monkeypatch: pytest.MonkeyPatch) -> dict:
    """Replace the interactive Popen boundary with a capturing stub."""
    captured: dict = {}

    def mock_run(cmd, **kwargs):  # type: ignore[no-untyped-def]
        if len(cmd) > 1 and cmd[1] == "--version":
            return type(
                "Result",
                (),
                {
                    "returncode": 0,
                    "stdout": "2.1.219 (Claude Code)",
                    "stderr": "",
                },
            )()
        captured["cmd"] = list(cmd)
        captured["env"] = kwargs.get("env", {}) or {}
        captured["pass_fds"] = kwargs.get("pass_fds")
        return type("Result", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr(subprocess, "run", mock_run)
    monkeypatch.setattr(subprocess, "Popen", _popen_from_run(mock_run))
    return captured


def _stub_plugin_installed(monkeypatch: pytest.MonkeyPatch, *, installed: bool = True) -> None:
    """Stub detect_autoskillit_mcp_prefix to simulate marketplace/direct install."""
    from autoskillit.core._plugin_ids import DIRECT_PREFIX, MARKETPLACE_PREFIX

    prefix = MARKETPLACE_PREFIX if installed else DIRECT_PREFIX
    monkeypatch.setattr(
        "autoskillit.core.detect_autoskillit_mcp_prefix",
        lambda _capabilities: prefix,
    )


def _stub_codex_pre_launch(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep command-assembly tests independent of an installed Codex binary."""
    from autoskillit.execution.backends.codex import CodexBackend

    monkeypatch.setattr(
        CodexBackend,
        "ensure_pre_launch",
        lambda _self, *, session_dir=None, executable=None: PreLaunchReadiness((), {}),
    )


# Module-level flags map — shared by Tests B, C, and the registry guard.
# Constructed from the enum values themselves so the enums ARE the ground truth.
_BACKEND_FLAGS: dict[str, set[str]] = {
    "claude-code": {str(f) for f in ClaudeFlags},
    "codex": {str(f) for f in CodexFlags},
}


def _make_capturing_backend() -> tuple[object, list[dict]]:
    """Return (backend_instance, captured_kwargs_list)."""
    from autoskillit.core import CLAUDE_CODE_CAPABILITIES, CmdSpec

    captured_kwargs: list[dict] = []

    class _CapturingBackend(_BackendLifecycleStub):
        def binary_name(self) -> str:
            return "claude"

        @property
        def capabilities(self):
            return CLAUDE_CODE_CAPABILITIES

        def build_interactive_cmd(self, **kwargs):
            captured_kwargs.append(kwargs)
            binding = kwargs.get("plugin_binding")
            inherited_fds = () if binding is None else binding.inherited_fds
            return CmdSpec(
                cmd=("claude", "--dangerously-skip-permissions"),
                env=dict(kwargs.get("env_extras") or {}),
                inherited_fds=inherited_fds,
            )

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
    assert len(captured_kwargs) == 2
    assert all(kwargs["system_prompt"] == "my-unique-prompt" for kwargs in captured_kwargs)
    assert captured_kwargs[0].get("executable") is None
    assert captured_kwargs[1]["executable"].path.is_absolute()


def test_run_interactive_session_holds_binding_through_reap_and_passes_descriptors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from autoskillit.core import PluginLoadMode

    backend, captured_kwargs = _make_capturing_backend()
    binding = _TestBinding(Path("/dev/null"))
    binding.inherited_fds = (41, 42)
    authority = _TestAuthority(None)
    authority.acquire_launch_binding = lambda **_kwargs: binding  # type: ignore[method-assign]
    events: list[str] = []

    monkeypatch.setattr(
        "autoskillit.cli._plugin_artifact.interactive_plugin_authority",
        lambda **_kwargs: (authority, PluginLoadMode.EXPLICIT_PLUGIN_DIR),
    )

    def run(_cmd, **kwargs):  # type: ignore[no-untyped-def]
        assert not binding.closed
        assert kwargs["pass_fds"] == (41, 42)
        events.append("reaped")
        return type("Result", (), {"returncode": 0})()

    monkeypatch.setattr(subprocess, "Popen", _popen_from_run(run))
    _run_interactive_session(system_prompt="test", backend=backend)

    assert captured_kwargs[0]["plugin_binding"] is binding
    assert events == ["reaped"]
    assert binding.closed


@pytest.mark.parametrize("failure_site", ["build", "spawn"])
def test_run_interactive_session_closes_binding_on_launch_failure(
    monkeypatch: pytest.MonkeyPatch,
    failure_site: str,
) -> None:
    from autoskillit.core import PluginLoadMode

    backend, _captured_kwargs = _make_capturing_backend()
    binding = _TestBinding(Path("/dev/null"))
    authority = _TestAuthority(None)
    authority.acquire_launch_binding = lambda **_kwargs: binding  # type: ignore[method-assign]
    expected = RuntimeError(f"injected {failure_site} failure")

    monkeypatch.setattr(
        "autoskillit.cli._plugin_artifact.interactive_plugin_authority",
        lambda **_kwargs: (authority, PluginLoadMode.EXPLICIT_PLUGIN_DIR),
    )
    if failure_site == "build":

        def fail_build(**_kwargs):
            raise expected

        monkeypatch.setattr(backend, "build_interactive_cmd", fail_build)
    else:

        def fail_spawn(*_args, **_kwargs):
            raise expected

        monkeypatch.setattr(subprocess, "Popen", fail_spawn)

    with pytest.raises(RuntimeError) as caught:
        _run_interactive_session(system_prompt="test", backend=backend)

    assert caught.value is expected
    assert binding.closed


def test_run_interactive_session_preserves_failure_when_binding_close_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from autoskillit.core import PluginLoadMode

    backend, _captured_kwargs = _make_capturing_backend()
    expected = RuntimeError("injected build failure")
    cleanup = OSError("injected binding close failure")

    class FailingCloseBinding(_TestBinding):
        def close(self) -> None:
            self.closed = True
            raise cleanup

    binding = FailingCloseBinding(Path("/dev/null"))
    authority = _TestAuthority(None)
    authority.acquire_launch_binding = lambda **_kwargs: binding  # type: ignore[method-assign]
    monkeypatch.setattr(
        "autoskillit.cli._plugin_artifact.interactive_plugin_authority",
        lambda **_kwargs: (authority, PluginLoadMode.EXPLICIT_PLUGIN_DIR),
    )

    def fail_build(**_kwargs):
        raise expected

    monkeypatch.setattr(backend, "build_interactive_cmd", fail_build)

    with pytest.raises(RuntimeError) as caught:
        _run_interactive_session(system_prompt="test", backend=backend)

    assert caught.value is expected
    assert binding.closed
    assert any("injected binding close failure" in note for note in expected.__notes__)


# ---------------------------------------------------------------------------
# env extras passed through
# ---------------------------------------------------------------------------


def test_run_interactive_session_extra_env_merged(monkeypatch: pytest.MonkeyPatch) -> None:
    """extra_env values appear in the subprocess env."""
    _stub_plugin_installed(monkeypatch)
    captured = _capture_subprocess(monkeypatch)
    _run_interactive_session(system_prompt="test", extra_env={"MY_UNIQUE_KEY": "MY_VAL"})
    assert captured["env"].get("MY_UNIQUE_KEY") == "MY_VAL"


def test_run_interactive_session_injects_state_root_from_project_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """AUTOSKILLIT_STATE_ROOT is injected from the resolved project_dir so
    PreToolUse guards can locate .autoskillit/ state for this cook session —
    the interactive launch path has no other route through
    _assemble_shared_env_extras (that helper is skill/food-truck only)."""
    from autoskillit.core import AUTOSKILLIT_STATE_ROOT_ENV_VAR

    _stub_plugin_installed(monkeypatch)
    captured = _capture_subprocess(monkeypatch)
    _run_interactive_session(system_prompt="test", project_dir=tmp_path)
    assert captured["env"].get(AUTOSKILLIT_STATE_ROOT_ENV_VAR) == str(tmp_path)


def test_run_interactive_session_state_root_survives_alongside_extra_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Injecting AUTOSKILLIT_STATE_ROOT must not drop caller-supplied extra_env keys."""
    from autoskillit.core import AUTOSKILLIT_STATE_ROOT_ENV_VAR

    _stub_plugin_installed(monkeypatch)
    captured = _capture_subprocess(monkeypatch)
    _run_interactive_session(
        system_prompt="test",
        extra_env={"MY_UNIQUE_KEY": "MY_VAL"},
        project_dir=tmp_path,
    )
    assert captured["env"].get("MY_UNIQUE_KEY") == "MY_VAL"
    assert captured["env"].get(AUTOSKILLIT_STATE_ROOT_ENV_VAR) == str(tmp_path)


@pytest.mark.parametrize(("action", "expected_returncode"), [("terminate", -15), ("kill", -9)])
def test_interactive_process_stub_preserves_signal_returncode(
    action: str, expected_returncode: int
) -> None:
    process = InteractiveProcessStub(returncode=0)

    getattr(process, action)()

    assert process.wait() == expected_returncode


def test_run_interactive_session_binds_launch_owner_before_wait(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from autoskillit.core import LAUNCH_ID_ENV_VAR

    events: list[tuple[str, object]] = []

    class _Process(InteractiveProcessStub):
        def wait(self, timeout: float | None = None) -> int:
            events.append(("wait", timeout))
            return super().wait(timeout)

    monkeypatch.setattr(subprocess, "Popen", lambda *_args, **_kwargs: _Process(pid=777))
    monkeypatch.setattr(
        "autoskillit.core.bind_session_owner",
        lambda project_dir, launch_id, pid: events.append(("bind", (project_dir, launch_id, pid))),
    )
    backend, _captured_kwargs = _make_capturing_backend()

    _run_interactive_session(
        system_prompt="test",
        extra_env={LAUNCH_ID_ENV_VAR: "launch-1"},
        project_dir=tmp_path,
        backend=backend,
    )

    assert events == [("bind", (tmp_path, "launch-1", 777)), ("wait", None)]


def test_run_interactive_session_reaps_child_when_owner_binding_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from autoskillit.core import LAUNCH_ID_ENV_VAR

    process = InteractiveProcessStub(pid=888)
    monkeypatch.setattr(subprocess, "Popen", lambda *_args, **_kwargs: process)

    def fail_owner_binding(_project_dir: Path, _launch_id: str, _pid: int) -> None:
        raise RuntimeError("bind failed")

    monkeypatch.setattr("autoskillit.core.bind_session_owner", fail_owner_binding)
    backend, _captured_kwargs = _make_capturing_backend()

    with pytest.raises(RuntimeError, match="bind failed"):
        _run_interactive_session(
            system_prompt="test",
            extra_env={LAUNCH_ID_ENV_VAR: "launch-1"},
            project_dir=tmp_path,
            backend=backend,
        )

    assert process.terminated
    assert process.returncode is not None


# ---------------------------------------------------------------------------
# exits when claude missing
# ---------------------------------------------------------------------------


def test_run_interactive_session_exits_when_claude_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_run_interactive_session exits 1 when claude is not on PATH."""
    monkeypatch.setattr(shutil, "which", lambda _, **_kwargs: None)
    with pytest.raises(SystemExit, match="1"):
        _run_interactive_session(system_prompt="test")


# ---------------------------------------------------------------------------
# plugin dir passed via explicit binding regardless of marketplace install
# ---------------------------------------------------------------------------


def test_run_interactive_session_includes_plugin_dir_when_installed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_run_interactive_session always passes --plugin-dir for a plugin-install-capable
    backend — EXPLICIT_PLUGIN_DIR generation-store binding, not marketplace-install
    detection, governs the flag (IMPLICIT_INSTALLED was retired in #4480)."""
    _stub_plugin_installed(monkeypatch, installed=True)
    captured = _capture_subprocess(monkeypatch)
    _run_interactive_session(system_prompt="test")
    assert ClaudeFlags.PLUGIN_DIR in captured["cmd"]


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
    from autoskillit.cli.session._session_constants import SESSION_TYPE_COOK, SESSION_TYPE_ORDER

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
        hook_trust_policy=HookTrustPolicy.AUTOMATED,
    )

    class _NoInjectBackend(_BackendLifecycleStub):
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

    def mock_run(cmd, **kwargs):
        captured["cmd"] = list(cmd)
        captured["env"] = kwargs.get("env", {}) or {}
        return type("Result", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr(subprocess, "Popen", _popen_from_run(mock_run))
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


def test_binary_name_from_backend_used_in_which(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """shutil.which is called with the backend's binary_name(), not a hardcoded literal."""
    from autoskillit.core import BackendCapabilities, CmdSpec

    captured_which_arg: list = []

    class _CustomBinaryBackend(_BackendLifecycleStub):
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
                hook_trust_policy=HookTrustPolicy.AUTOMATED,
            )

        def build_interactive_cmd(self, **kwargs):
            executable = kwargs.get("executable")
            binary = str(executable.path) if executable is not None else "test-agent-binary"
            return CmdSpec(
                cmd=(binary, "--dangerously-skip-permissions"),
                env={"PATH": str(tmp_path)},
            )

    binary_path = tmp_path / "test-agent-binary"
    binary_path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    binary_path.chmod(0o755)

    def tracking_which(binary: str, **_kwargs):
        captured_which_arg.append(binary)
        if binary == "test-agent-binary":
            return str(binary_path)
        return None

    monkeypatch.setattr(shutil, "which", tracking_which)
    monkeypatch.setattr(
        subprocess,
        "Popen",
        _popen_from_run(lambda *a, **kw: type("R", (), {"returncode": 0})()),
    )
    from autoskillit.cli.session._session_launch import _run_interactive_session

    _run_interactive_session(system_prompt="test", backend=_CustomBinaryBackend())
    assert "test-agent-binary" in captured_which_arg


def test_run_interactive_session_uses_injected_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    """When backend= is passed, _run_interactive_session uses it directly."""
    from autoskillit.core import BackendCapabilities, CmdSpec

    build_called: list[dict] = []

    class _InjectedBackend(_BackendLifecycleStub):
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
                hook_trust_policy=HookTrustPolicy.AUTOMATED,
            )

        def build_interactive_cmd(self, **kwargs):
            build_called.append(kwargs)
            return CmdSpec(cmd=("claude", "--dangerously-skip-permissions"), env={})

    _stub_plugin_installed(monkeypatch, installed=True)
    _capture_subprocess(monkeypatch)
    _run_interactive_session(system_prompt="test", backend=_InjectedBackend())
    assert build_called, "Injected backend must be used"


def test_run_interactive_session_default_backend_uses_typed_resolver(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When backend= is absent, the configured name crosses the typed resolver boundary."""
    from unittest.mock import MagicMock

    get_backend_called: list = []

    class _FakeBackend(_BackendLifecycleStub):
        def binary_name(self) -> str:
            return "claude"

        @property
        def capabilities(self):
            return MagicMock(
                skill_injection_capable=True,
                mcp_config_capable=False,
                hook_trust_policy=HookTrustPolicy.AUTOMATED,
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
    monkeypatch.setattr(
        "autoskillit.cli.session._session_backend.resolve_global_backend",
        fake_get_backend,
    )
    _run_interactive_session(system_prompt="test")
    assert get_backend_called, "typed resolver must be called when backend is not injected"
    assert get_backend_called[0] == "claude-code"


def test_typed_resolver_di_used_in_session_launch(monkeypatch: pytest.MonkeyPatch) -> None:
    """Typed resolver DI invokes the selected stub's interactive command builder."""
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
        hook_trust_policy=HookTrustPolicy.AUTOMATED,
    )

    class _DIBackend(_BackendLifecycleStub):
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
    monkeypatch.setattr(
        "autoskillit.cli.session._session_backend.resolve_global_backend",
        lambda name: _DIBackend(),
    )
    _stub_plugin_installed(monkeypatch)
    _capture_subprocess(monkeypatch)
    _run_interactive_session(system_prompt="test")
    assert build_calls, "Stub backend's build_interactive_cmd must be invoked via resolver DI"


def test_run_interactive_session_default_backend_threads_mcp_tool_timeout_sec(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When backend= is absent, config.run_skill.mcp_tool_timeout_sec reaches the builder."""
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
        hook_trust_policy=HookTrustPolicy.AUTOMATED,
    )

    class _DIBackend(_BackendLifecycleStub):
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
    mock_config.run_skill.mcp_tool_timeout_sec = 7777.0

    monkeypatch.setattr("autoskillit.config.load_config", lambda: mock_config)
    monkeypatch.setattr(
        "autoskillit.cli.session._session_backend.resolve_global_backend",
        lambda name: _DIBackend(),
    )
    _stub_plugin_installed(monkeypatch)
    _capture_subprocess(monkeypatch)
    _run_interactive_session(system_prompt="test")
    assert build_calls
    assert build_calls[-1]["mcp_tool_timeout_sec"] == 7777.0


def test_skill_injection_false_via_typed_resolver_forwards_system_prompt_kwarg(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A non-injecting resolver-selected backend still receives system_prompt."""
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
        hook_trust_policy=HookTrustPolicy.AUTOMATED,
    )

    class _NoInjectDIBackend(_BackendLifecycleStub):
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
    monkeypatch.setattr(
        "autoskillit.cli.session._session_backend.resolve_global_backend",
        lambda name: _NoInjectDIBackend(),
    )

    def mock_run(cmd, **kwargs):
        return type("Result", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr(subprocess, "Popen", _popen_from_run(mock_run))
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
        hook_trust_policy=HookTrustPolicy.REVIEW_EACH_SESSION,
    )

    build_kwargs: list[dict] = []

    class _CodexLikeBackend(_BackendLifecycleStub):
        def binary_name(self) -> str:
            return "codex"

        @property
        def capabilities(self):
            return caps

        def build_interactive_cmd(self, **kwargs):
            build_kwargs.append(kwargs)
            return CmdSpec(cmd=("codex", "--dangerously-bypass-approvals-and-sandbox"), env={})

    captured: dict = {}

    def mock_run(cmd, **kwargs):
        captured["cmd"] = list(cmd)
        return type("Result", (), {"returncode": 0})()

    monkeypatch.setattr(subprocess, "Popen", _popen_from_run(mock_run))
    _run_interactive_session(system_prompt="test", backend=_CodexLikeBackend())
    assert ClaudeFlags.PLUGIN_DIR not in captured["cmd"]
    assert ClaudeFlags.TOOLS not in captured["cmd"]
    assert "AskUserQuestion" not in captured["cmd"]
    assert build_kwargs, "build_interactive_cmd must be called"
    assert build_kwargs[0].get("tools") == ("AskUserQuestion",), (
        "skill_injection_capable=True backend must receive tools=('AskUserQuestion',)"
    )


# ---------------------------------------------------------------------------
# T-1b: Feature flag gate — codex backend without codex_backend feature enabled
# ---------------------------------------------------------------------------


def test_configured_codex_authority_is_not_implicitly_rerouted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A configured Codex authority remains Codex regardless of capability hints."""
    from unittest.mock import MagicMock

    from autoskillit.core import CmdSpec

    backends_used: list[str] = []

    class _ClaudeStub(_BackendLifecycleStub):
        def binary_name(self) -> str:
            return "claude"

        @property
        def capabilities(self):
            from autoskillit.core import CLAUDE_CODE_CAPABILITIES

            return CLAUDE_CODE_CAPABILITIES

        def build_interactive_cmd(self, **kwargs):
            backends_used.append("claude-code")
            return CmdSpec(cmd=("claude", "--dangerously-skip-permissions"), env={})

    class _CodexStub(_BackendLifecycleStub):
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
                hook_trust_policy=HookTrustPolicy.REVIEW_EACH_SESSION,
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
    monkeypatch.setattr(
        "autoskillit.cli.session._session_backend.resolve_global_backend",
        fake_get_backend,
    )
    monkeypatch.setattr(
        subprocess,
        "Popen",
        _popen_from_run(lambda *a, **kw: type("Result", (), {"returncode": 0})()),
    )
    _run_interactive_session(system_prompt="test")
    assert backends_used == ["codex", "codex"], (
        f"Expected configured Codex authority, got: {backends_used}"
    )


def test_feature_flag_gate_allows_codex_backend_when_feature_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When config specifies backend='codex' and codex_backend feature is enabled,
    _run_interactive_session uses the codex backend (no fallback)."""
    from unittest.mock import MagicMock

    from autoskillit.core import CmdSpec

    backends_used: list[str] = []

    class _CodexStub(_BackendLifecycleStub):
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
                hook_trust_policy=HookTrustPolicy.REVIEW_EACH_SESSION,
            )

        def build_interactive_cmd(self, **kwargs):
            backends_used.append("codex")
            return CmdSpec(cmd=("codex", "--dangerously-bypass-approvals-and-sandbox"), env={})

    mock_config = MagicMock()
    mock_config.agent_backend.backend = "codex"
    mock_config.features = {"codex_backend": True}
    mock_config.experimental_enabled = True

    monkeypatch.setattr("autoskillit.config.load_config", lambda: mock_config)
    monkeypatch.setattr(
        "autoskillit.cli.session._session_backend.resolve_global_backend",
        lambda name: _CodexStub(),
    )
    monkeypatch.setattr(
        subprocess,
        "Popen",
        _popen_from_run(lambda *a, **kw: type("Result", (), {"returncode": 0})()),
    )
    _run_interactive_session(system_prompt="test")
    assert backends_used == ["codex", "codex"], (
        f"Expected codex backend when feature enabled, got: {backends_used}"
    )


# ---------------------------------------------------------------------------
# T-1c: _launch_cook_session accepts backend= parameter
# ---------------------------------------------------------------------------


def test_launch_cook_session_accepts_backend_param(
    monkeypatch: pytest.MonkeyPatch,
    launch_kwargs: dict[str, object],
) -> None:
    """_launch_cook_session must accept a backend= kwarg and forward it to
    _run_interactive_session, which calls backend.build_interactive_cmd."""
    from autoskillit.cli.session._session_launch import _launch_cook_session
    from autoskillit.core import CLAUDE_CODE_CAPABILITIES, CmdSpec

    build_calls: list[dict] = []

    class _CapturingBackend(_BackendLifecycleStub):
        def binary_name(self) -> str:
            return "claude"

        @property
        def capabilities(self):
            return CLAUDE_CODE_CAPABILITIES

        def build_interactive_cmd(self, **kwargs):
            build_calls.append(kwargs)
            return CmdSpec(cmd=("claude", "--dangerously-skip-permissions"), env={})

    monkeypatch.setattr(
        subprocess,
        "Popen",
        _popen_from_run(lambda *a, **kw: type("Result", (), {"returncode": 0})()),
    )
    _stub_plugin_installed(monkeypatch, installed=True)
    _launch_cook_session(
        system_prompt="test",
        backend=_CapturingBackend(),
        required_env=frozenset(),
        skill_compilation=launch_kwargs["skill_compilation"],
        launch_id=launch_kwargs["launch_id"],
        default_base_branch=launch_kwargs["default_base_branch"],
        workspace_temp_dir=launch_kwargs["workspace_temp_dir"],
    )
    assert build_calls, "backend.build_interactive_cmd must be called via _launch_cook_session"


# ---------------------------------------------------------------------------
# ORDER_INTERACTIVE_REQUIRED_ENV behavioral coverage (issue #4253 Part A)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("backend_name", ["claude-code", "codex"])
def test_interactive_builder_satisfies_order_required_env(backend_name: str) -> None:
    """Both interactive builders must produce an env satisfying every key in
    ORDER_INTERACTIVE_REQUIRED_ENV, including MAX_MCP_OUTPUT_TOKENS, when the order
    call site's env_extras (mimicking _write_order_entry) is supplied."""
    from autoskillit.core import ORDER_INTERACTIVE_REQUIRED_ENV, SESSION_TYPE_ENV_VAR
    from autoskillit.execution.backends import get_backend

    backend = get_backend(backend_name)
    spec = backend.build_interactive_cmd(
        env_extras={SESSION_TYPE_ENV_VAR: "orchestrator"},
        required_env=ORDER_INTERACTIVE_REQUIRED_ENV,
    )
    for key in ORDER_INTERACTIVE_REQUIRED_ENV:
        assert key in spec.env, f"{backend_name}: missing required key {key!r}"
    assert spec.env["MAX_MCP_OUTPUT_TOKENS"]


def test_order_interactive_required_env_excludes_headless() -> None:
    """ORDER_INTERACTIVE_REQUIRED_ENV must not require AUTOSKILLIT_HEADLESS — interactive
    order sessions are not headless, unlike the fleet/food-truck orchestrator contract."""
    from autoskillit.core import ORDER_INTERACTIVE_REQUIRED_ENV

    assert "AUTOSKILLIT_HEADLESS" not in ORDER_INTERACTIVE_REQUIRED_ENV


# ---------------------------------------------------------------------------
# T-1d: Multi-backend integration contract — no cross-backend flag contamination
# ---------------------------------------------------------------------------


def test_multi_backend_no_cross_flag_contamination(monkeypatch: pytest.MonkeyPatch) -> None:
    """Each backend in BACKEND_REGISTRY must not produce flags from other backends."""
    from autoskillit.core import ClaudeFlags
    from autoskillit.execution.backends import BACKEND_REGISTRY

    captured: dict = {}
    real_which = shutil.which

    def mock_run(cmd, **kwargs):
        if len(cmd) > 1 and cmd[1] == "--version":
            return type(
                "Result",
                (),
                {"returncode": 0, "stdout": "2.1.219", "stderr": ""},
            )()
        captured["cmd"] = list(cmd)
        return type("Result", (), {"returncode": 0})()

    monkeypatch.setattr(subprocess, "run", mock_run)
    monkeypatch.setattr(subprocess, "Popen", _popen_from_run(mock_run))
    _stub_plugin_installed(monkeypatch, installed=False)
    _stub_codex_pre_launch(monkeypatch)

    for backend_name, backend_cls in BACKEND_REGISTRY.items():
        backend = backend_cls()
        captured.clear()
        _run_interactive_session(system_prompt="test", backend=backend)
        cmd = captured.get("cmd", [])
        expected = Path(real_which(backend.binary_name()) or "").resolve()
        assert Path(cmd[0]) == expected
        if backend.binary_name() != "claude":
            assert ClaudeFlags.PLUGIN_DIR not in cmd, (
                f"{backend_name}: must not contain {ClaudeFlags.PLUGIN_DIR!r}"
            )
            assert ClaudeFlags.TOOLS not in cmd, (
                f"{backend_name}: must not contain {ClaudeFlags.TOOLS!r}"
            )


# ---------------------------------------------------------------------------
# T-1e: Real-backend parametrized — comprehensive foreign flag exclusion
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("backend_name", ["claude-code", "codex"])
def test_real_backend_no_foreign_flags(monkeypatch: pytest.MonkeyPatch, backend_name: str) -> None:
    """Real backend instances must not produce flags from other backends' Flags enums."""
    from autoskillit.execution.backends import BACKEND_REGISTRY

    captured: dict = {}
    real_which = shutil.which

    def mock_run(cmd, **kwargs):
        if len(cmd) > 1 and cmd[1] == "--version":
            return type(
                "Result",
                (),
                {"returncode": 0, "stdout": "2.1.219", "stderr": ""},
            )()
        captured["cmd"] = list(cmd)
        return type("Result", (), {"returncode": 0})()

    monkeypatch.setattr(subprocess, "run", mock_run)
    monkeypatch.setattr(subprocess, "Popen", _popen_from_run(mock_run))
    _stub_plugin_installed(monkeypatch, installed=False)
    _stub_codex_pre_launch(monkeypatch)

    backend = BACKEND_REGISTRY[backend_name]()
    _run_interactive_session(system_prompt="test", backend=backend)
    cmd = captured.get("cmd", [])

    expected = Path(real_which(backend.binary_name()) or "").resolve()
    assert Path(cmd[0]) == expected

    other_backend = "claude-code" if backend_name == "codex" else "codex"
    foreign_only = _BACKEND_FLAGS[other_backend] - _BACKEND_FLAGS[backend_name]
    assert set(cmd).isdisjoint(foreign_only), (
        f"{backend_name}: foreign flags found in command: {set(cmd) & foreign_only}"
    )


# ---------------------------------------------------------------------------
# T-1f: Cross-validation contract — every assembled flag must belong to backend
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("backend_name", ["claude-code", "codex"])
def test_cross_validation_contract_all_flags_known(
    monkeypatch: pytest.MonkeyPatch,
    backend_name: str,
) -> None:
    """Every flag-like token in the assembled command must be a member of the
    backend's own Flags enum. This is the test that would have caught #3270."""
    from autoskillit.execution.backends import BACKEND_REGISTRY

    captured: dict = {}
    real_which = shutil.which

    def mock_run(cmd, **kwargs):
        if len(cmd) > 1 and cmd[1] == "--version":
            return type(
                "Result",
                (),
                {"returncode": 0, "stdout": "2.1.219", "stderr": ""},
            )()
        captured["cmd"] = list(cmd)
        return type("Result", (), {"returncode": 0})()

    monkeypatch.setattr(subprocess, "run", mock_run)
    monkeypatch.setattr(subprocess, "Popen", _popen_from_run(mock_run))
    _stub_plugin_installed(monkeypatch, installed=False)
    _stub_codex_pre_launch(monkeypatch)

    backend = BACKEND_REGISTRY[backend_name]()
    _run_interactive_session(system_prompt="test", backend=backend)
    cmd = captured.get("cmd", [])

    expected = Path(real_which(backend.binary_name()) or "").resolve()
    assert Path(cmd[0]) == expected

    valid_flags = _BACKEND_FLAGS.get(backend_name)
    if valid_flags is None:
        pytest.fail(
            f"{backend_name}: not in _BACKEND_FLAGS — "
            "add it to _BACKEND_FLAGS in test_session_launch.py"
        )
    flag_tokens = {t for t in cmd[1:] if t.startswith("-")}
    unknown = flag_tokens - valid_flags
    assert not unknown, (
        f"{backend_name}: unknown flags in command: {sorted(unknown)}. "
        f"Valid flags: {sorted(valid_flags)}"
    )


def test_backend_flags_mapping_covers_registry() -> None:
    """Every backend in BACKEND_REGISTRY must have an entry in the flags validation map."""
    from autoskillit.execution.backends import BACKEND_REGISTRY

    missing = set(BACKEND_REGISTRY) - set(_BACKEND_FLAGS)
    assert not missing, (
        f"BACKEND_REGISTRY has backends not covered by _BACKEND_FLAGS: {missing}. "
        "Add the new backend's Flags enum to _BACKEND_FLAGS in test_session_launch.py."
    )


# ---------------------------------------------------------------------------
# T-1e: _run_interactive_session calls ensure_pre_launch() for Codex backend
# ---------------------------------------------------------------------------


def test_run_interactive_session_calls_ensure_pre_launch_for_codex_backend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_run_interactive_session must call backend.ensure_pre_launch() before subprocess.run."""
    from autoskillit.core import BackendCapabilities, CmdSpec

    call_sequence: list[str] = []

    caps = BackendCapabilities(
        channel_b_capable=False,
        pty_required=True,
        session_resume_capable=True,
        skill_injection_capable=True,
        supports_thinking_blocks=False,
        supports_claude_format_stdout=False,
        exit_code_is_terminal=True,
        mcp_config_capable=True,
        food_truck_capable=False,
        completion_record_types=frozenset(),
        session_record_types=frozenset(),
        hook_trust_policy=HookTrustPolicy.REVIEW_EACH_SESSION,
    )

    class _CodexBackendStub(_BackendLifecycleStub):
        def binary_name(self) -> str:
            return "codex"

        @property
        def capabilities(self):
            return caps

        def ensure_pre_launch(
            self, *, session_dir: Path | None = None, executable=None
        ) -> PreLaunchReadiness:
            del executable
            call_sequence.append("pre_launch")
            return PreLaunchReadiness((), {})

        def build_interactive_cmd(self, **kwargs):
            return CmdSpec(cmd=("codex",), env={})

    def mock_run(cmd, **kwargs):
        call_sequence.append("subprocess")
        return type("Result", (), {"returncode": 0})()

    monkeypatch.setattr(subprocess, "Popen", _popen_from_run(mock_run))
    _run_interactive_session(system_prompt="test", backend=_CodexBackendStub())
    assert call_sequence == ["pre_launch", "subprocess"], (
        f"ensure_pre_launch() must be called before subprocess.run, got: {call_sequence}"
    )


def test_run_interactive_session_aborts_when_pre_launch_returns_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_run_interactive_session must sys.exit(1) when ensure_pre_launch() returns errors."""
    from autoskillit.core import BackendCapabilities, CmdSpec

    caps = BackendCapabilities(
        channel_b_capable=False,
        pty_required=True,
        session_resume_capable=True,
        skill_injection_capable=True,
        supports_thinking_blocks=False,
        supports_claude_format_stdout=False,
        exit_code_is_terminal=True,
        mcp_config_capable=True,
        food_truck_capable=False,
        completion_record_types=frozenset(),
        session_record_types=frozenset(),
        hook_trust_policy=HookTrustPolicy.REVIEW_EACH_SESSION,
    )

    class _FailingCodexBackend(_BackendLifecycleStub):
        def binary_name(self) -> str:
            return "codex"

        @property
        def capabilities(self):
            return caps

        def ensure_pre_launch(
            self, *, session_dir: Path | None = None, executable=None
        ) -> PreLaunchReadiness:
            del executable
            return PreLaunchReadiness(("Failed to ensure MCP registration: some error",), {})

        def build_interactive_cmd(self, **kwargs):
            return CmdSpec(cmd=("codex",), env={})

    def _must_not_call(*a, **kw):
        raise AssertionError("subprocess.Popen must not be called")

    monkeypatch.setattr(subprocess, "Popen", _must_not_call)
    with pytest.raises(SystemExit, match="1"):
        _run_interactive_session(system_prompt="test", backend=_FailingCodexBackend())


def test_managed_interactive_session_validates_before_shared_process_owner(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    launch_kwargs: dict[str, object],
) -> None:
    from autoskillit.core import (
        CLAUDE_CODE_CAPABILITIES,
        CmdSpec,
        ManagedSessionHome,
        ValidatedAddDir,
    )

    events: list[str] = []

    class _ManagedBackend(_BackendLifecycleStub):
        @property
        def capabilities(self):
            return CLAUDE_CODE_CAPABILITIES

        def binary_name(self) -> str:
            return "claude"

        def build_interactive_cmd(self, **kwargs):
            return CmdSpec(cmd=("claude",), env={}, inherited_fds=(3,))

        def validate_interactive_invocation(self, spec):
            events.append("validated")
            assert spec.cwd == str(tmp_path.resolve())
            return []

    def run_attempt(spec, **kwargs):
        events.append("spawned")
        assert kwargs["observer"] is None
        assert kwargs["pass_fds"] == (3, 7, 8)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(
        "autoskillit.cli.session._session_process.run_cook_attempt",
        run_attempt,
    )
    generated_home = tmp_path / "generated"
    generated_home.mkdir()
    managed_home = ManagedSessionHome(
        launch_id="launch-id",
        generated_home=generated_home,
        skills_dir=ValidatedAddDir(str(generated_home / "add-dir")),
        pass_fds=(7,),
    )
    retained_binding = MagicMock(inherited_fds=(8,))
    trace = MagicMock()

    result = _run_interactive_session(
        system_prompt="test",
        backend=_ManagedBackend(),
        project_dir=tmp_path,
        skill_compilation=launch_kwargs["skill_compilation"],
        managed_home=managed_home,
        retained_projection_binding=retained_binding,
        startup_trace=trace,
        attempt=1,
    )

    assert result is None
    assert events == ["validated", "spawned"]
    trace.record_attempt_anchor.assert_called_once_with(attempt=1, view_id="launch-id-1")


def test_managed_launch_rejects_executable_drift_before_spawn(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    launch_kwargs: dict[str, object],
    capsys: pytest.CaptureFixture[str],
) -> None:
    from autoskillit.core import (
        CLAUDE_CODE_CAPABILITIES,
        CmdSpec,
        ManagedSessionHome,
        ValidatedAddDir,
    )

    class _ManagedBackend(_BackendLifecycleStub):
        @property
        def capabilities(self):
            return CLAUDE_CODE_CAPABILITIES

        def binary_name(self) -> str:
            return "claude"

        def build_interactive_cmd(self, **kwargs):
            executable = kwargs.get("executable")
            command = str(executable.path) if executable is not None else "claude"
            return CmdSpec(cmd=(command,), env={})

    monkeypatch.setattr(
        "autoskillit.cli.session._session_process.run_cook_attempt",
        lambda *_args, **_kwargs: pytest.fail("drifted executable must not spawn"),
    )
    monkeypatch.setattr(
        "autoskillit.cli.session._session_launch.executable_binding_matches_current_file",
        lambda _binding: False,
    )
    generated_home = tmp_path / "generated"
    generated_home.mkdir()
    managed_home = ManagedSessionHome(
        launch_id="launch-id",
        generated_home=generated_home,
        skills_dir=ValidatedAddDir(str(generated_home / "add-dir")),
        pass_fds=(),
    )

    with pytest.raises(SystemExit, match="1"):
        _run_interactive_session(
            system_prompt="test",
            backend=_ManagedBackend(),
            project_dir=tmp_path,
            skill_compilation=launch_kwargs["skill_compilation"],
            managed_home=managed_home,
            retained_projection_binding=MagicMock(inherited_fds=()),
            startup_trace=MagicMock(),
            attempt=1,
        )

    assert "interactive executable changed after capability probing" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("managed_kwargs", "message"),
    [
        ({"managed_home": MagicMock()}, "managed home and attempt must be supplied together"),
        ({"attempt": 1}, "managed home and attempt must be supplied together"),
        (
            {
                "managed_home": MagicMock(),
                "attempt": 0,
                "skill_compilation": MagicMock(),
                "retained_projection_binding": MagicMock(),
                "startup_trace": MagicMock(),
            },
            "managed attempt must be positive",
        ),
        (
            {
                "managed_home": MagicMock(),
                "attempt": 1,
                "retained_projection_binding": MagicMock(),
                "startup_trace": MagicMock(),
            },
            "managed home requires its retained skill compilation",
        ),
        (
            {
                "managed_home": MagicMock(),
                "attempt": 1,
                "skill_compilation": MagicMock(),
                "startup_trace": MagicMock(),
            },
            "managed home requires a retained projection binding",
        ),
        (
            {
                "managed_home": MagicMock(),
                "attempt": 1,
                "skill_compilation": MagicMock(),
                "retained_projection_binding": MagicMock(),
            },
            "managed home requires a launch-scoped startup trace",
        ),
        (
            {"plugin_binding": MagicMock()},
            "managed launch inputs are invalid for a raw session",
        ),
    ],
)
def test_interactive_session_rejects_invalid_managed_inputs(
    managed_kwargs: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        _run_interactive_session(
            system_prompt="test",
            backend=_BackendLifecycleStub(),
            **managed_kwargs,
        )


def _write_codex_mcp_probe_executable(path: Path) -> None:
    path.write_text(
        """#!/usr/bin/env python3
import json
import os
from pathlib import Path
import sys
import tomllib

if "--version" in sys.argv:
    print("codex-cli 0.147.0")
    raise SystemExit(0)

config = tomllib.loads((Path(os.environ["CODEX_HOME"]) / "config.toml").read_text())
transport = dict(config["mcp_servers"]["autoskillit"])
project_config = Path.cwd() / ".codex" / "config.toml"
if project_config.is_file():
    project = tomllib.loads(project_config.read_text())
    override = project.get("mcp_servers", {}).get("autoskillit", {})
    if "command" in override:
        transport["command"] = override["command"]
transport["type"] = "stdio"
entry = {"name": "autoskillit", "enabled": True, "transport": transport}
for key in ("startup_timeout_sec", "tool_timeout_sec"):
    if key in transport:
        entry[key] = transport.pop(key)
print(json.dumps([entry]))
""",
        encoding="utf-8",
    )
    path.chmod(0o755)


def _prepare_codex_order_composition(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    project_mcp_command: str | None = None,
) -> tuple[dict[str, object], object]:
    from autoskillit.core import SkillExecutionRole
    from autoskillit.execution.backends.codex import CodexBackend
    from autoskillit.workspace import DefaultSkillResolver, compile_session_skill_catalog

    state = build_plugin_artifact_state(
        tmp_path / "home",
        PluginArtifactStateKind.VALID_CURRENT,
    )
    _production_mcp_prefix.cache_clear()
    monkeypatch.setattr(
        "autoskillit.core.detect_autoskillit_mcp_prefix",
        _production_mcp_prefix,
    )
    monkeypatch.setattr(
        "autoskillit.workspace.project_default_plugin_authority",
        _production_project_default_plugin_authority,
    )
    monkeypatch.setattr(Path, "home", lambda: state.home)
    monkeypatch.setenv("HOME", str(state.home))
    monkeypatch.setenv("MCP_CLIENT_BACKEND", "codex")

    source_home = state.home / ".codex"
    source_home.mkdir(parents=True)
    (source_home / "config.toml").write_text(
        'cli_auth_credentials_store = "keyring"\n',
        encoding="utf-8",
    )
    project_dir = state.home / "project"
    project_config = project_dir / ".codex" / "config.toml"
    project_config.parent.mkdir(parents=True)
    project_toml = 'sqlite_home = "/project-level-conflict"\n'
    if project_mcp_command is not None:
        project_toml += f'\n[mcp_servers.autoskillit]\ncommand = "{project_mcp_command}"\n'
    project_config.write_text(project_toml, encoding="utf-8")

    executable = tmp_path / "bin" / "codex"
    executable.parent.mkdir(exist_ok=True)
    _write_codex_mcp_probe_executable(executable)
    monkeypatch.setattr(shutil, "which", lambda _name, **_kwargs: str(executable))
    monkeypatch.setattr(
        "autoskillit.execution.backends.codex.default_log_dir",
        lambda: tmp_path / "logs",
    )

    backend = CodexBackend(source_codex_home=source_home)
    catalog = DefaultSkillResolver().list_effective(
        project_dir,
        SkillExecutionRole.ORCHESTRATOR,
    )
    compilation = compile_session_skill_catalog(catalog, backend)
    captured: dict[str, object] = {
        "events": [],
        "pre_launch_dirs": [],
        "process_calls": [],
        "projection_roots": [],
        "validation_errors": [],
    }

    original_ensure_pre_launch = CodexBackend.ensure_pre_launch

    def ensure_pre_launch(self, **kwargs):  # type: ignore[no-untyped-def]
        session_dir = kwargs.get("session_dir")
        if session_dir is not None:
            cast(list[Path], captured["pre_launch_dirs"]).append(Path(session_dir))
        return original_ensure_pre_launch(self, **kwargs)

    monkeypatch.setattr(CodexBackend, "ensure_pre_launch", ensure_pre_launch)
    original_validate = CodexBackend.validate_interactive_invocation

    def validate_interactive_invocation(self, spec):  # type: ignore[no-untyped-def]
        cast(list[str], captured["events"]).append("validated")
        captured["spec"] = spec
        generated_home = Path(spec.env["CODEX_HOME"])
        captured["config_text"] = (generated_home / "config.toml").read_text()
        captured["config"] = tomllib.loads(cast(str, captured["config_text"]))
        errors = original_validate(self, spec)
        cast(list[list[str]], captured["validation_errors"]).append(errors)
        return errors

    monkeypatch.setattr(
        CodexBackend,
        "validate_interactive_invocation",
        validate_interactive_invocation,
    )

    class _CapturingAuthority:
        def __init__(self, delegate: object) -> None:
            self._delegate = delegate

        def acquire_launch_binding(self, **kwargs):  # type: ignore[no-untyped-def]
            binding = self._delegate.acquire_launch_binding(**kwargs)  # type: ignore[attr-defined]
            cast(list[Path], captured["projection_roots"]).append(binding.identity.managed_path)
            return binding

    def capture_interactive_authority(**kwargs):  # type: ignore[no-untyped-def]
        authority, load_mode = _production_interactive_plugin_authority(**kwargs)
        assert authority is not None
        return _CapturingAuthority(authority), load_mode

    monkeypatch.setattr(
        "autoskillit.cli._plugin_artifact.interactive_plugin_authority",
        capture_interactive_authority,
    )

    def record_final_process(spec, **kwargs):  # type: ignore[no-untyped-def]
        cast(list[str], captured["events"]).append("spawned")
        cast(list[tuple[object, dict[str, object]]], captured["process_calls"]).append(
            (spec, kwargs)
        )
        return SimpleNamespace(pid=101, pgid=101, returncode=0)

    monkeypatch.setattr(
        "autoskillit.cli.session._session_process.run_cook_attempt",
        record_final_process,
    )

    def launch() -> None:
        _launch_cook_session(
            "composition contract",
            project_dir=project_dir,
            required_env=frozenset(),
            backend=backend,
            skill_compilation=compilation,
            launch_id="0123456789abcdef",
            default_base_branch="main",
            workspace_temp_dir=None,
        )

    return captured, launch


def test_codex_order_composition_produces_canonical_generated_home(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from autoskillit.core import CmdSpec

    captured, launch = _prepare_codex_order_composition(tmp_path, monkeypatch)
    launch()  # type: ignore[operator]

    assert captured["events"] == ["validated", "spawned"]
    assert captured["validation_errors"] == [[]]
    assert len(cast(list[object], captured["process_calls"])) == 1
    spec = cast(CmdSpec, captured["spec"])
    generated_home = Path(spec.env["CODEX_HOME"])
    assert generated_home == generated_home.resolve()
    assert spec.env["CODEX_SQLITE_HOME"] == str(generated_home)
    assert captured["pre_launch_dirs"] == [generated_home]

    assert spec.origin is not None
    config_overrides = [
        value for flag, value in spec.origin.kv_flags if flag == CodexFlags.CONFIG_OVERRIDE
    ]
    assert config_overrides[-1] == f'sqlite_home="{generated_home}"'

    config = cast(dict[str, object], captured["config"])
    config_text = cast(str, captured["config_text"])
    assert config["cli_auth_credentials_store"] == "file"
    assert config_text.count('cli_auth_credentials_store = "file"') == 1
    assert all(
        f'cli_auth_credentials_store = "{value}"' not in config_text
        for value in ("keyring", "auto", "ephemeral")
    )
    mcp = cast(dict[str, object], cast(dict[str, object], config["mcp_servers"])["autoskillit"])
    assert mcp["command"] == "autoskillit"
    assert mcp.get("args", []) == []
    assert isinstance(mcp["env_vars"], list)
    assert mcp["startup_timeout_sec"] > 0  # type: ignore[operator]
    assert mcp["tool_timeout_sec"] > 0  # type: ignore[operator]

    add_dirs = [
        Path(value) for flag, value in spec.origin.variadic_pairs if flag == CodexFlags.ADD_DIR
    ]
    assert len(add_dirs) == 1
    assert add_dirs[0].is_relative_to(generated_home)
    projection_roots = cast(list[Path], captured["projection_roots"])
    assert len(projection_roots) == 1
    assert not add_dirs[0].is_relative_to(projection_roots[0])


def test_codex_order_composition_rejects_effective_mcp_override(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    captured, launch = _prepare_codex_order_composition(
        tmp_path,
        monkeypatch,
        project_mcp_command="conflicting-command",
    )

    with pytest.raises(SystemExit, match="1"):
        launch()  # type: ignore[operator]

    assert captured["events"] == ["validated"]
    assert captured["process_calls"] == []
    errors = cast(list[list[str]], captured["validation_errors"])
    assert len(errors) == 1
    assert any("command does not match final config" in error for error in errors[0])
    assert "command does not match final config" in capsys.readouterr().err


def test_order_managed_session_keeps_home_across_reload_and_infra_resume(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from autoskillit.core import (
        CmdSpec,
        CompiledSessionSkillCatalogAuthority,
        CookSessionHandle,
        InfraExitCategory,
        ManagedSessionHome,
        NamedResume,
        NoResume,
        PluginLoadMode,
        SkillExecutionRole,
        SkillProjectionContextAuthority,
        ValidatedAddDir,
    )
    from autoskillit.execution import SessionState
    from autoskillit.execution.backends.codex import CodexBackend
    from autoskillit.workspace import DefaultSkillResolver, compile_session_skill_catalog

    events: list[tuple[object, ...]] = []
    generated_home = tmp_path / "managed-home"
    skills_dir = generated_home / "autoskillit-add-dir"
    skills_dir.mkdir(parents=True)
    projection_root = tmp_path / "projection"
    projection_root.mkdir()
    launch_id = "fedcba9876543210"

    class _LifecycleManager:
        def cleanup_stale(self) -> None:
            return None

        @contextmanager
        def managed_session(
            self,
            session_id: str,
            compilation: CompiledSessionSkillCatalogAuthority,
            projection_context: SkillProjectionContextAuthority,
        ):
            assert projection_context.catalog == compilation.catalog
            events.append(("managed-enter", session_id))
            try:
                yield ManagedSessionHome(
                    launch_id=session_id,
                    generated_home=generated_home,
                    skills_dir=ValidatedAddDir(str(skills_dir)),
                    pass_fds=(7,),
                )
            finally:
                events.append(("managed-exit", session_id))

    class _LifecycleBinding:
        def __init__(self) -> None:
            self.identity = SimpleNamespace(managed_path=projection_root)
            self.inherited_fds = (5, 7)
            self.closed = False

        def close(self) -> None:
            self.closed = True
            events.append(("projection-exit",))

    binding = _LifecycleBinding()

    class _LifecycleAuthority:
        def acquire_launch_binding(self, **_kwargs):  # type: ignore[no-untyped-def]
            events.append(("projection-enter",))
            return binding

    class _LifecycleCodexBackend(CodexBackend):
        def binary_name(self) -> str:
            return "true"

        def build_interactive_cmd(self, **kwargs):  # type: ignore[no-untyped-def]
            return CmdSpec(
                cmd=("true",),
                env={
                    "INITIAL": kwargs.get("initial_prompt") or "",
                    "RESUME": type(kwargs["resume_spec"]).__name__,
                },
                inherited_fds=(3,),
            )

        def validate_interactive_invocation(self, spec: CmdSpec) -> list[str]:
            events.append(("validated", spec.env["RESUME"]))
            return []

        @contextmanager
        def cook_session_context(
            self,
            *,
            session_home: Path,
            project_dir: Path,
            launch_id: str,
            attempt: int,
            current_resume_spec: object,
            ceiling_seconds: float = 172800.0,
            systemd_scope_enabled: bool = False,
        ):
            del ceiling_seconds, systemd_scope_enabled
            events.append(
                (
                    "attempt-enter",
                    attempt,
                    current_resume_spec,
                    session_home,
                    project_dir,
                    launch_id,
                )
            )
            try:
                yield CookSessionHandle(
                    view_id=f"{launch_id}-{attempt}",
                    pass_fds=(11,),
                    _record_spawn=lambda pid, pgid: events.append(("spawn", attempt, pid, pgid)),
                    _record_reaped=lambda pid, pgid: events.append(("reaped", attempt, pid, pgid)),
                )
            finally:
                events.append(("attempt-exit", attempt, current_resume_spec))

    source_home = tmp_path / "source-codex"
    source_home.mkdir()
    backend = _LifecycleCodexBackend(source_codex_home=source_home)
    catalog = DefaultSkillResolver().list_effective(
        tmp_path,
        SkillExecutionRole.ORCHESTRATOR,
    )
    compilation = compile_session_skill_catalog(catalog, backend)
    monkeypatch.setattr(shutil, "which", lambda _name, **_kwargs: "/usr/bin/true")
    monkeypatch.setattr(
        "autoskillit.workspace.DefaultSessionSkillManager",
        lambda *args, **kwargs: _LifecycleManager(),
    )
    monkeypatch.setattr(
        "autoskillit.cli._plugin_artifact.interactive_plugin_authority",
        lambda **_kwargs: (_LifecycleAuthority(), PluginLoadMode.GENERATED_HOME),
    )

    results = iter(
        (
            SimpleNamespace(pid=101, pgid=101, returncode=17),
            SimpleNamespace(pid=102, pgid=102, returncode=42),
            SimpleNamespace(pid=103, pgid=103, returncode=0),
        )
    )

    def run_attempt(
        spec: CmdSpec,
        *,
        pass_fds: tuple[int, ...],
        on_spawn,
        on_reaped,
        trace,
        **_kwargs: object,
    ) -> object:
        attempt = sum(event[0] == "run" for event in events) + 1
        events.append(
            (
                "run",
                attempt,
                spec.env["INITIAL"],
                spec.env["RESUME"],
                pass_fds,
            )
        )
        on_spawn(100 + attempt, 100 + attempt)
        trace.record_spawn()
        on_reaped(100 + attempt, 100 + attempt)
        return next(results)

    monkeypatch.setattr(
        "autoskillit.cli.session._session_process.run_cook_attempt",
        run_attempt,
    )
    sentinels = iter(("reload-id", None, None))

    def consume_sentinel(_project_dir: Path) -> str | None:
        value = next(sentinels)
        events.append(("sentinel", value))
        return value

    monkeypatch.setattr(
        "autoskillit.cli.session._session_reload.consume_reload_sentinel",
        consume_sentinel,
    )
    infra_state = SessionState(
        session_id="infra-id",
        pid=102,
        boot_id="boot",
        starttime_ticks=1,
        infra_exit_category=InfraExitCategory.API_ERROR,
    )

    def read_state(_state_dir: Path) -> SessionState:
        events.append(("infra-classified", infra_state.infra_exit_category))
        return infra_state

    monkeypatch.setattr("autoskillit.execution.read_session_state", read_state)

    _launch_cook_session(
        "lifecycle contract",
        initial_message="greeting",
        project_dir=tmp_path,
        required_env=frozenset(),
        backend=backend,
        skill_compilation=compilation,
        launch_id=launch_id,
        default_base_branch="main",
        workspace_temp_dir=None,
    )

    attempt_enters = [event for event in events if event[0] == "attempt-enter"]
    assert [event[1] for event in attempt_enters] == [1, 2, 3]
    assert {event[5] for event in attempt_enters} == {launch_id}
    assert all(event[3] == generated_home for event in attempt_enters)
    assert isinstance(attempt_enters[0][2], NoResume)
    assert [cast(NamedResume, event[2]).session_id for event in attempt_enters[1:]] == [
        "reload-id",
        "infra-id",
    ]

    run_events = [event for event in events if event[0] == "run"]
    assert [event[2] for event in run_events] == ["greeting", "", ""]
    assert [event[3] for event in run_events] == ["NoResume", "NamedResume", "NamedResume"]
    assert [event[4] for event in run_events] == [(3, 7, 5, 11)] * 3
    assert len([event for event in events if event[0] == "spawn"]) == 3
    assert len([event for event in events if event[0] == "reaped"]) == 3
    for attempt in (1, 2, 3):
        assert events.index(("attempt-enter", *attempt_enters[attempt - 1][1:])) < events.index(
            next(event for event in events if event[:2] == ("run", attempt))
        )

    first_exit = events.index(next(event for event in events if event[:2] == ("attempt-exit", 1)))
    first_sentinel = events.index(("sentinel", "reload-id"))
    second_exit = events.index(next(event for event in events if event[:2] == ("attempt-exit", 2)))
    infra_classified = events.index(("infra-classified", InfraExitCategory.API_ERROR))
    assert first_exit < first_sentinel
    assert second_exit < infra_classified
    assert events.index(("managed-enter", launch_id)) < events.index(("run", *run_events[0][1:]))
    assert events.index(("managed-exit", launch_id)) > events.index(
        next(event for event in events if event[:2] == ("attempt-exit", 3))
    )
    assert events.index(("projection-exit",)) > events.index(("managed-exit", launch_id))
    assert binding.closed
