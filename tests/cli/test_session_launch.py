"""Tests for cli/_session_launch.py — _run_interactive_session contract."""

from __future__ import annotations

import shutil
import subprocess
from contextlib import nullcontext
from pathlib import Path

import pytest
from packaging.version import Version

from autoskillit.cli.session._session_launch import _run_interactive_session
from autoskillit.core import BackendConventions, ClaudeFlags, HookTrustPolicy
from autoskillit.execution.backends import claude as _claude_mod
from autoskillit.execution.backends.codex import CodexFlags


@pytest.fixture(autouse=True)
def _pre_freeze_attestation_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pre-freeze the Claude attestation env so the executable binding capture
    and the final cmd build see identical values.

    Without this, ensure_pre_launch() sets _FROZEN_ATTESTATION_ENV between the
    two build_interactive_cmd calls, causing env mismatch errors. The mock
    subprocess returns version 2.1.197 (see _capture_subprocess).
    """
    monkeypatch.setattr(
        _claude_mod,
        "_FROZEN_ATTESTATION_ENV",
        _claude_mod._claude_host_attestation_env(Version("2.1.197")),
    )


pytestmark = [pytest.mark.layer("cli"), pytest.mark.small]


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
    # Interactive command building copies the ambient os.environ verbatim
    # (see claude.py build_interactive_cmd), so CLAUDE_CODE_EXECPATH set by a
    # host Claude Code session running this test suite would otherwise leak
    # through and pin executable resolution to the real system binary instead
    # of the fake binaries/ stub above.
    monkeypatch.delenv("CLAUDE_CODE_EXECPATH", raising=False)


class _BackendLifecycleStub:
    """Projection and lifecycle contract shared by local backend doubles."""

    name = "claude-code"
    conventions = BackendConventions()

    def validate_interactive_invocation(self, spec):
        return []

    def ensure_pre_launch(self, *, session_dir: Path | None = None, executable=None) -> list[str]:
        del executable
        return []

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
    ):
        del project_dir
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


def _capture_subprocess(monkeypatch: pytest.MonkeyPatch) -> dict:
    """Replace subprocess.run with a capturing stub."""
    captured: dict = {}

    def mock_run(cmd, **kwargs):  # type: ignore[no-untyped-def]
        if len(cmd) > 1 and cmd[1] == "--version":
            return type(
                "Result",
                (),
                {
                    "returncode": 0,
                    "stdout": "2.1.197 (Claude Code)",
                    "stderr": "",
                },
            )()
        captured["cmd"] = list(cmd)
        captured["env"] = kwargs.get("env", {}) or {}
        captured["pass_fds"] = kwargs.get("pass_fds")
        return type("Result", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr(subprocess, "run", mock_run)
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
        lambda _self, *, session_dir=None, executable=None: [],
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
                env={},
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

    monkeypatch.setattr(subprocess, "run", run)
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

        monkeypatch.setattr(subprocess, "run", fail_spawn)

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
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: type("R", (), {"returncode": 0})())
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

    monkeypatch.setattr(subprocess, "run", mock_run)
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
        subprocess, "run", lambda *a, **kw: type("Result", (), {"returncode": 0})()
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
        subprocess, "run", lambda *a, **kw: type("Result", (), {"returncode": 0})()
    )
    _run_interactive_session(system_prompt="test")
    assert backends_used == ["codex", "codex"], (
        f"Expected codex backend when feature enabled, got: {backends_used}"
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
        subprocess, "run", lambda *a, **kw: type("Result", (), {"returncode": 0})()
    )
    _stub_plugin_installed(monkeypatch, installed=True)
    _launch_cook_session(
        system_prompt="test", backend=_CapturingBackend(), required_env=frozenset()
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
                {"returncode": 0, "stdout": "2.1.197", "stderr": ""},
            )()
        captured["cmd"] = list(cmd)
        return type("Result", (), {"returncode": 0})()

    monkeypatch.setattr(subprocess, "run", mock_run)
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
                {"returncode": 0, "stdout": "2.1.197", "stderr": ""},
            )()
        captured["cmd"] = list(cmd)
        return type("Result", (), {"returncode": 0})()

    monkeypatch.setattr(subprocess, "run", mock_run)
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
                {"returncode": 0, "stdout": "2.1.197", "stderr": ""},
            )()
        captured["cmd"] = list(cmd)
        return type("Result", (), {"returncode": 0})()

    monkeypatch.setattr(subprocess, "run", mock_run)
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
        ) -> list[str]:
            del executable
            call_sequence.append("pre_launch")
            return []

        def build_interactive_cmd(self, **kwargs):
            return CmdSpec(cmd=("codex",), env={})

    def mock_run(cmd, **kwargs):
        call_sequence.append("subprocess")
        return type("Result", (), {"returncode": 0})()

    monkeypatch.setattr(subprocess, "run", mock_run)
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
        ) -> list[str]:
            del executable
            return ["Failed to ensure MCP registration: some error"]

        def build_interactive_cmd(self, **kwargs):
            return CmdSpec(cmd=("codex",), env={})

    def _must_not_call(*a, **kw):
        raise AssertionError("subprocess.run must not be called")

    monkeypatch.setattr(subprocess, "run", _must_not_call)
    with pytest.raises(SystemExit, match="1"):
        _run_interactive_session(system_prompt="test", backend=_FailingCodexBackend())
