"""Interactive cook lifetime policy forwarding and single-authority tests."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

import autoskillit.cli.fleet._fleet_session as _patch_fleet_session
import autoskillit.cli.install._plugin_artifact as _patch_plugin_artifact
import autoskillit.cli.prompts as _patch_prompts
import autoskillit.cli.session._session_backend as _patch_session_backend
import autoskillit.cli.session._session_launch as _patch_session_launch
import autoskillit.cli.session._session_process as _patch_session_process
import autoskillit.cli.session._session_reload as _patch_session_reload
import autoskillit.cli.session._session_startup_trace as _patch_startup_trace
import autoskillit.config as _patch_config
import autoskillit.core as _patch_core
import autoskillit.execution as execution
import autoskillit.execution.process as execution_process
import autoskillit.execution.process._process_tether as process_tether
import autoskillit.workspace as _patch_workspace
from autoskillit.config import AutomationConfig, ProcessTetherConfig
from autoskillit.core import FreshLaunch, PluginLoadMode
from tests.cli._cook_launch_helpers import arrange_cook

pytestmark = [pytest.mark.layer("cli"), pytest.mark.medium]


def test_second_interactive_ceiling_authority_is_gone() -> None:
    assert not hasattr(process_tether, "INTERACTIVE_TETHER_CEILING_SECONDS")
    assert not hasattr(execution_process, "INTERACTIVE_TETHER_CEILING_SECONDS")
    assert not hasattr(execution, "INTERACTIVE_TETHER_CEILING_SECONDS")


@pytest.mark.usefixtures("_stub_interactive_prelaunch")
def test_cook_path_threads_process_tether_identity(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from autoskillit import cli
    from autoskillit.execution.backends import ClaudeCodeBackend

    policy = ProcessTetherConfig(cook_ceiling_seconds=7.0)
    arrange_cook(monkeypatch, tmp_path, config=AutomationConfig(process_tether=policy))
    captured: dict[str, object] = {}

    def capture_attempt(_spec: object, **kwargs: object) -> SimpleNamespace:
        captured.update(kwargs)
        return SimpleNamespace(pid=101, pgid=101, returncode=0)

    monkeypatch.setattr(_patch_session_process, "run_cook_attempt", capture_attempt)
    monkeypatch.setattr(
        _patch_session_reload,
        "consume_reload_sentinel",
        lambda _project_dir: None,
    )

    cli.cook(backend=ClaudeCodeBackend())

    assert captured["lifetime"] is policy


def test_order_path_threads_process_tether_identity(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from autoskillit.cli.session._session_launch import _launch_cook_session

    policy = ProcessTetherConfig(cook_ceiling_seconds=7.0)
    captured: dict[str, object] = {}
    backend = SimpleNamespace(capabilities=SimpleNamespace(session_dir_persistent=False))
    compilation = SimpleNamespace(unavailability_payload={"backend": None, "unavailable": ()})
    monkeypatch.setattr(
        _patch_session_launch,
        "_run_interactive_session",
        lambda **kwargs: captured.update(kwargs),
    )

    _launch_cook_session(
        FreshLaunch(system_prompt="prompt"),
        project_dir=tmp_path,
        required_env=frozenset(),
        backend=backend,
        skill_compilation=compilation,
        launch_id="launch-id",
        default_base_branch="main",
        workspace_temp_dir=None,
        process_tether=policy,
    )

    assert captured["process_tether"] is policy


def test_fleet_raw_session_threads_process_tether_identity(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from autoskillit.cli.fleet._fleet_session import _fleet_session_launcher

    policy = ProcessTetherConfig(cook_ceiling_seconds=7.0)
    captured: dict[str, object] = {}
    backend = SimpleNamespace(
        name="claude-code",
        capabilities=SimpleNamespace(session_dir_persistent=False),
    )
    monkeypatch.setattr(
        _patch_session_launch,
        "_run_interactive_session",
        lambda **kwargs: captured.update(kwargs),
    )

    with _fleet_session_launcher(
        backend=backend,
        project_dir=tmp_path,
        skill_compilation=SimpleNamespace(),
        default_base_branch="main",
        workspace_temp_dir=None,
        force_inactive_agent_teams=False,
        mcp_tool_timeout_sec=1.0,
        process_tether=policy,
    ) as launch_session:
        launch_session(FreshLaunch(system_prompt="prompt"), {})

    assert captured["process_tether"] is policy


def test_fleet_managed_session_threads_process_tether_identity(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from autoskillit.cli.fleet._fleet_session import _fleet_session_launcher

    policy = ProcessTetherConfig(cook_ceiling_seconds=7.0)
    captured: dict[str, object] = {}
    backend = SimpleNamespace(
        name="codex",
        capabilities=SimpleNamespace(session_dir_persistent=True),
    )
    binding = SimpleNamespace(
        identity=SimpleNamespace(managed_path=tmp_path / "plugin"),
        inherited_fds=(),
    )
    manager = SimpleNamespace(cleanup_stale=lambda: 0)

    @contextmanager
    def managed_session(*_args: object, **_kwargs: object):
        yield SimpleNamespace(
            generated_home=tmp_path / "home",
            skills_dir=tmp_path / "skills",
            pass_fds=(),
            unavailability_payload={"backend": None, "unavailable": ()},
        )

    manager.managed_session = managed_session

    @contextmanager
    def launch_binding_scope(**_kwargs: object):
        yield binding

    class Trace:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        def record_launch_anchor(self) -> None:
            pass

        def close(self, **_kwargs: object) -> None:
            pass

    provider = SimpleNamespace(
        catalog_projection_context=lambda *args, **_kwargs: SimpleNamespace(catalog=args[0])
    )
    monkeypatch.setattr(
        _patch_session_launch,
        "_run_interactive_session",
        lambda **kwargs: captured.update(kwargs),
    )
    monkeypatch.setattr(_patch_core, "plugin_launch_binding_scope", launch_binding_scope)
    monkeypatch.setattr(_patch_core, "resolve_temp_dir", lambda *_args, **_kwargs: tmp_path)
    monkeypatch.setattr(_patch_core, "temp_dir_display_str", lambda _path: ".autoskillit/temp")
    monkeypatch.setattr(execution, "all_backends", lambda: ())
    monkeypatch.setattr(
        _patch_workspace,
        "resolve_persistent_session_roots",
        lambda *_args, **_kwargs: {},
    )
    monkeypatch.setattr(_patch_workspace, "resolve_ephemeral_root", lambda: tmp_path)
    monkeypatch.setattr(
        _patch_workspace,
        "SkillsDirectoryProvider",
        lambda **_kwargs: provider,
    )
    monkeypatch.setattr(
        _patch_workspace,
        "DefaultSessionSkillManager",
        lambda *_args, **_kwargs: manager,
    )
    monkeypatch.setattr(
        _patch_plugin_artifact,
        "interactive_plugin_authority",
        lambda **_kwargs: (SimpleNamespace(), PluginLoadMode.PROJECTED_HOME),
    )
    monkeypatch.setattr(_patch_startup_trace, "StartupTrace", Trace)

    with _fleet_session_launcher(
        backend=backend,
        project_dir=tmp_path,
        skill_compilation=SimpleNamespace(catalog=object()),
        default_base_branch="main",
        workspace_temp_dir=None,
        force_inactive_agent_teams=False,
        mcp_tool_timeout_sec=1.0,
        process_tether=policy,
    ) as launch_session:
        launch_session(FreshLaunch(system_prompt="prompt"), {})

    assert captured["process_tether"] is policy


def test_launch_fleet_session_forwards_config_process_tether(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    policy = ProcessTetherConfig(cook_ceiling_seconds=7.0)
    config = AutomationConfig(process_tether=policy)
    backend = SimpleNamespace(
        name="claude-code",
        capabilities=SimpleNamespace(
            managed_fixed_batch_route_capable=False,
            has_unguarded_filesystem_access=False,
        ),
    )
    captured: dict[str, object] = {}

    @contextmanager
    def capture_launcher(**kwargs: object):
        captured.update(kwargs)
        yield lambda *_args, **_kwargs: None

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(_patch_config, "load_config", lambda *_args: config)
    monkeypatch.setattr(
        _patch_session_backend,
        "resolve_global_backend",
        lambda *_args, **_kwargs: backend,
    )
    monkeypatch.setattr(
        "autoskillit.cli.detect_autoskillit_mcp_prefix",
        lambda _capabilities: "mcp__autoskillit",
    )
    monkeypatch.setattr(
        _patch_workspace,
        "default_skill_resolver",
        lambda: SimpleNamespace(list_effective=lambda *_args, **_kwargs: ()),
    )
    monkeypatch.setattr(
        _patch_workspace,
        "compile_session_skill_catalog",
        lambda *_args, **_kwargs: SimpleNamespace(
            unavailability_payload={"backend": None, "unavailable": ()}
        ),
    )
    monkeypatch.setattr(
        _patch_prompts,
        "_build_fleet_dispatch_prompt",
        lambda *_args, **_kwargs: "dispatch prompt",
    )
    monkeypatch.setattr(_patch_fleet_session, "_fleet_session_launcher", capture_launcher)
    monkeypatch.setattr(_patch_fleet_session, "_run_fleet_session_loop", lambda **_kwargs: None)

    _patch_fleet_session._launch_fleet_session(
        None,
        None,
        None,
        None,
        fleet_mode="dispatch",
    )

    assert captured["process_tether"] is policy


def test_managed_interactive_session_requires_process_tether(
    tmp_path: Path,
) -> None:
    from autoskillit.cli.session._session_launch import _run_interactive_session

    with pytest.raises(ValueError, match="process_tether"):
        _run_interactive_session(
            launch=FreshLaunch(system_prompt="prompt"),
            project_dir=tmp_path,
            required_env=frozenset(),
            backend=SimpleNamespace(capabilities=SimpleNamespace()),
            skill_compilation=SimpleNamespace(catalog=object()),
            managed_home=SimpleNamespace(),
            retained_projection_binding=SimpleNamespace(),
            startup_trace=SimpleNamespace(),
            attempt=1,
        )
