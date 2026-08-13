"""Real-selector integration coverage for interactive plugin launch consumers."""

from __future__ import annotations

import shutil
import subprocess
import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager, nullcontext
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

from autoskillit import cli
from autoskillit.cli._plugin_artifact import interactive_plugin_authority
from autoskillit.cli.session._session_launch import (
    _launch_cook_session,
    _run_interactive_session,
)
from autoskillit.core import (
    CLAUDE_CODE_CAPABILITIES,
    BackendConventions,
    CmdSpec,
    CompiledSessionSkillCatalogAuthority,
    CookSessionHandle,
    ManagedSessionHome,
    PluginLaunchBinding,
    PluginLoadMode,
    PreLaunchReadiness,
    SkillExecutionRole,
    SkillProjectionContextAuthority,
    ValidatedAddDir,
    plugin_launch_binding_scope,
)
from autoskillit.core._plugin_ids import (
    detect_autoskillit_mcp_prefix as _production_mcp_prefix,
)
from autoskillit.execution.backends import ClaudeCodeBackend, CodexBackend
from autoskillit.workspace import DefaultSkillResolver, compile_session_skill_catalog
from autoskillit.workspace._projection_cache import projected_plugin_artifact_digest
from tests.cli._interactive_process import InteractiveProcessStub
from tests.fakes import adapt_test_skill_semantics
from tests.fixtures.plugin_artifact_state import (
    INVALID_PLUGIN_ARTIFACT_STATE_KINDS,
    PluginArtifactState,
    PluginArtifactStateKind,
    build_plugin_artifact_state,
)

pytestmark = [pytest.mark.layer("cli"), pytest.mark.medium]


@pytest.fixture(autouse=True)
def _stub_owner_binding(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("autoskillit.core.bind_session_owner", lambda *_args: None)


_CLAUDE_INSTALLED_INVALID_STATES = tuple(
    kind
    for kind in INVALID_PLUGIN_ARTIFACT_STATE_KINDS
    if kind
    not in {
        # These lexical-evidence states intentionally do not require registry
        # publication, so they exercise verifier behavior after selection
        # rather than the "fail before child spawn" registration contract.
        PluginArtifactStateKind.DANGLING_MANAGED_ROOT,
        PluginArtifactStateKind.DANGLING_MANIFEST,
        PluginArtifactStateKind.DANGLING_LEASE,
    }
)
_CODEX_IGNORED_CLAUDE_ARTIFACT_STATES = (
    *INVALID_PLUGIN_ARTIFACT_STATE_KINDS,
    PluginArtifactStateKind.VALID_CURRENT,
)


class _RecordingBackend:
    conventions = BackendConventions()
    adapt_skill_semantics = staticmethod(adapt_test_skill_semantics)

    def __init__(self, name: str) -> None:
        self.name = name
        self.build_calls: list[dict[str, object]] = []
        self.recover_count = 0

    @property
    def capabilities(self):
        if self.name == "claude-code":
            return CLAUDE_CODE_CAPABILITIES
        return replace(
            CodexBackend().capabilities,
            session_dir_persistent=False,
        )

    @property
    def exploration_dispatch_renderer(self):
        if self.name == "claude-code":
            return ClaudeCodeBackend().exploration_dispatch_renderer
        return CodexBackend().exploration_dispatch_renderer

    def binary_name(self) -> str:
        return "claude" if self.name == "claude-code" else "codex"

    def ensure_pre_launch(
        self,
        *,
        session_dir: Path | None = None,
        executable: object = None,
    ) -> PreLaunchReadiness:
        del session_dir, executable
        return PreLaunchReadiness((), {})

    def recover_cook_history(self) -> None:
        self.recover_count += 1

    def session_locator(self) -> object:
        return SimpleNamespace()

    def build_interactive_cmd(self, **kwargs: object) -> CmdSpec:
        self.build_calls.append(kwargs)
        binding = kwargs.get("plugin_binding")
        return CmdSpec(
            cmd=(self.binary_name(),),
            env={},
            inherited_fds=getattr(binding, "inherited_fds", ()),
        )

    def validate_interactive_invocation(self, spec: CmdSpec) -> list[str]:
        del spec
        return []

    def cook_session_context(
        self,
        *,
        session_home: Path,
        project_dir: Path,
        launch_id: str,
        attempt: int,
        current_resume_spec: object,
    ):
        del session_home, project_dir, current_resume_spec
        return nullcontext(
            CookSessionHandle(
                view_id=f"{launch_id}-{attempt}",
                pass_fds=(),
                _record_spawn=lambda _pid, _pgid: None,
                _record_reaped=lambda _pid, _pgid: None,
            )
        )


class _PersistentCodexRecordingBackend(_RecordingBackend):
    @property
    def capabilities(self):
        return CodexBackend().capabilities


class _CookSessionManager:
    def __init__(self, generated_home: Path, events: list[tuple[object, ...]]) -> None:
        self._generated_home = generated_home
        self._events = events

    def cleanup_stale(self) -> None:
        return None

    @contextmanager
    def managed_session(
        self,
        launch_id: str,
        compilation: CompiledSessionSkillCatalogAuthority,
        projection_context: SkillProjectionContextAuthority,
    ) -> Iterator[ManagedSessionHome]:
        self._events.append(("managed-enter", launch_id))
        assert projection_context.catalog == compilation.catalog
        skills_dir = self._generated_home / "skills"
        skills_dir.mkdir(parents=True, exist_ok=True)
        try:
            yield ManagedSessionHome(
                launch_id=launch_id,
                generated_home=self._generated_home,
                skills_dir=ValidatedAddDir(str(skills_dir)),
                pass_fds=(),
            )
        finally:
            self._events.append(("managed-exit", launch_id))


def _install_cook_harness(
    monkeypatch: pytest.MonkeyPatch,
    project_dir: Path,
    *,
    during_attempt: Callable[[Path], None] | None = None,
) -> dict[str, object]:
    generated_home = project_dir / "managed-home"
    events: list[tuple[object, ...]] = []
    captured: dict[str, object] = {
        "events": events,
        "generated_home": generated_home,
    }
    manager = _CookSessionManager(generated_home, events)

    def run_attempt(spec: CmdSpec, **kwargs: object) -> object:
        captured["spec"] = spec
        events.append(("run",))
        on_spawn = cast(object, kwargs["on_spawn"])
        on_reaped = cast(object, kwargs["on_reaped"])
        trace = kwargs["trace"]
        on_spawn(101, 101)  # type: ignore[operator]
        trace.record_spawn()  # type: ignore[attr-defined]
        if during_attempt is not None:
            during_attempt(generated_home)
        on_reaped(101, 101)  # type: ignore[operator]
        return SimpleNamespace(pid=101, pgid=101, returncode=0)

    generated_home.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(project_dir)
    monkeypatch.setattr(shutil, "which", lambda _binary, **_kwargs: sys.executable)
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr(
        "autoskillit.workspace.DefaultSessionSkillManager",
        lambda *args, **kwargs: manager,
    )
    monkeypatch.setattr(
        "autoskillit.cli._onboarding.is_first_run",
        lambda _project_dir: False,
    )
    monkeypatch.setattr(
        "autoskillit.cli.ui._timed_input.timed_prompt",
        lambda *args, **kwargs: "",
    )
    monkeypatch.setattr(
        "autoskillit.core.write_registry_entry",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        "autoskillit.cli.session._session_process.run_cook_attempt",
        run_attempt,
    )
    monkeypatch.setattr(
        "autoskillit.cli.session._session_reload.consume_reload_sentinel",
        lambda _project_dir: None,
    )
    return captured


def _activate_production_selector(
    monkeypatch: pytest.MonkeyPatch,
    state: PluginArtifactState,
) -> None:
    """Undo the CLI test directory's direct-prefix stub for this integration."""
    _production_mcp_prefix.cache_clear()
    monkeypatch.setattr(
        "autoskillit.core.detect_autoskillit_mcp_prefix",
        _production_mcp_prefix,
    )
    monkeypatch.setattr(Path, "home", lambda: state.home)
    monkeypatch.setenv("HOME", str(state.home))


def _run_session_launch(
    monkeypatch: pytest.MonkeyPatch,
    state: PluginArtifactState,
    backend: _RecordingBackend,
    spawn_calls: list[tuple[tuple[object, ...], dict[str, object]]],
) -> None:
    (state.home / "project").mkdir(parents=True, exist_ok=True)
    agent_path = state.home / "agent"
    agent_path.write_text("#!/bin/sh\nexit 0\n")
    agent_path.chmod(0o755)

    def resolve_agent(_binary: str, *, path: str | None = None) -> str:
        del path
        return str(agent_path)

    monkeypatch.setattr(shutil, "which", resolve_agent)

    def record_spawn(*args: object, **kwargs: object) -> object:
        spawn_calls.append((args, kwargs))
        return InteractiveProcessStub(pid=123)

    monkeypatch.setattr(subprocess, "Popen", record_spawn)
    _run_interactive_session(
        system_prompt="selector integration",
        project_dir=state.home / "project",
        backend=backend,
    )


@pytest.mark.parametrize(
    "kind",
    _CLAUDE_INSTALLED_INVALID_STATES,
    ids=lambda kind: kind.value,
)
def test_invalid_claude_cache_does_not_block_interactive_session(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    kind: PluginArtifactStateKind,
) -> None:
    """Invalid Claude-cache artifacts no longer block sessions.

    Since the generation-keyed publication migration (#4480),
    ``interactive_plugin_authority`` returns the projection authority
    (which resolves from ``pkg_root()``) rather than the installed-artifact
    authority.  Invalid states in ``~/.claude/plugins/cache/`` are
    therefore invisible to session launch.
    """
    state = build_plugin_artifact_state(tmp_path / "home", kind)
    _activate_production_selector(monkeypatch, state)
    backend = _RecordingBackend("claude-code")
    spawn_calls: list[tuple[tuple[object, ...], dict[str, object]]] = []
    authority, load_mode = interactive_plugin_authority(
        backend=backend,
        project_dir=state.home / "project",
        default_base_branch="main",
        skill_catalog=None,
        generated_home_available=False,
    )
    assert authority is not None
    assert load_mode is PluginLoadMode.EXPLICIT_PLUGIN_DIR

    _run_session_launch(monkeypatch, state, backend, spawn_calls)

    assert len(backend.build_calls) == 2
    assert len(spawn_calls) == 1


def test_matching_claude_artifact_binds_through_real_selector_session_launch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Session-launch binds through the projection authority with valid artifact."""
    state = build_plugin_artifact_state(
        tmp_path / "home",
        PluginArtifactStateKind.VALID_CURRENT,
    )
    _activate_production_selector(monkeypatch, state)
    backend = _RecordingBackend("claude-code")
    spawn_calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    _run_session_launch(monkeypatch, state, backend, spawn_calls)

    assert len(backend.build_calls) == 2
    binding = cast(PluginLaunchBinding, backend.build_calls[-1]["plugin_binding"])
    assert binding.load_mode is PluginLoadMode.EXPLICIT_PLUGIN_DIR
    assert binding.plugin_dir.is_relative_to(state.home / ".autoskillit" / "plugin-projections")
    assert (binding.plugin_dir / "hooks" / "_dispatch.py").is_file()
    assert binding.closed
    assert len(spawn_calls) == 1


def test_cook_binds_through_projection_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cook uses the projection authority, not the installed-artifact authority."""
    state = build_plugin_artifact_state(
        tmp_path / "home",
        PluginArtifactStateKind.VALID_CURRENT,
    )
    _activate_production_selector(monkeypatch, state)
    backend = _RecordingBackend("claude-code")

    _install_cook_harness(monkeypatch, state.home / "project")
    cli.cook(backend=backend)

    assert len(backend.build_calls) == 2
    binding = cast(PluginLaunchBinding, backend.build_calls[-1]["plugin_binding"])
    assert binding.load_mode is PluginLoadMode.EXPLICIT_PLUGIN_DIR
    assert isinstance(binding.plugin_dir, Path)
    assert binding.closed


@pytest.mark.parametrize(
    "kind",
    _CODEX_IGNORED_CLAUDE_ARTIFACT_STATES,
    ids=lambda kind: kind.value,
)
def test_codex_cook_remains_generated_home_and_ignores_claude_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    kind: PluginArtifactStateKind,
) -> None:
    state = build_plugin_artifact_state(tmp_path / "home", kind)
    _activate_production_selector(monkeypatch, state)
    backend = _RecordingBackend("codex")
    captured = _install_cook_harness(monkeypatch, state.home / "project")
    generated_home = captured["generated_home"]

    authority, load_mode = interactive_plugin_authority(
        backend=backend,
        project_dir=state.home / "project",
        default_base_branch="main",
        skill_catalog=None,
        generated_home_available=generated_home is not None,
    )
    assert authority is not None
    assert load_mode is PluginLoadMode.GENERATED_HOME

    cli.cook(backend=backend)

    assert len(backend.build_calls) == 1
    assert backend.build_calls[0]["plugin_binding"] is None
    assert backend.build_calls[0]["generated_home"] == generated_home


def test_codex_managed_order_runtime_writes_do_not_mutate_projection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = build_plugin_artifact_state(
        tmp_path / "home",
        PluginArtifactStateKind.VALID_CURRENT,
    )
    _activate_production_selector(monkeypatch, state)
    project_dir = state.home / "project"
    project_dir.mkdir(parents=True)
    backend = _PersistentCodexRecordingBackend("codex")
    catalog = DefaultSkillResolver().list_effective(
        project_dir,
        SkillExecutionRole.ORCHESTRATOR,
    )
    compilation = compile_session_skill_catalog(catalog, backend)
    authority, load_mode = interactive_plugin_authority(
        backend=backend,
        project_dir=project_dir,
        default_base_branch="main",
        skill_catalog=compilation.catalog,
        generated_home_available=True,
        retain_projection_source=True,
    )
    assert authority is not None
    assert load_mode is PluginLoadMode.GENERATED_HOME

    with plugin_launch_binding_scope(
        authority=authority,
        backend=backend,
        load_mode=PluginLoadMode.PROJECTED_HOME,
    ) as before_binding:
        assert before_binding is not None
        before_identity = before_binding.identity
        projection_root = before_identity.managed_path
        before_digest = projected_plugin_artifact_digest(projection_root)
        assert before_identity.artifact_digest == before_digest
        before_tree = tuple(
            sorted(
                path.relative_to(projection_root).as_posix() for path in projection_root.rglob("*")
            )
        )

    def write_runtime_artifacts(generated_home: Path) -> None:
        (generated_home / "auth.json").write_bytes(b"runtime auth marker")
        (generated_home / "state_5.sqlite").write_bytes(b"runtime sqlite marker")
        session_file = generated_home / "sessions" / "2026" / "08" / "rollout-test.jsonl"
        session_file.parent.mkdir(parents=True)
        session_file.write_text('{"type":"runtime marker"}\n')

    captured = _install_cook_harness(
        monkeypatch,
        project_dir,
        during_attempt=write_runtime_artifacts,
    )
    launch_id = "0123456789abcdef"
    _launch_cook_session(
        "projection immutability integration",
        project_dir=project_dir,
        required_env=frozenset(),
        backend=backend,
        skill_compilation=compilation,
        launch_id=launch_id,
        default_base_branch="main",
        workspace_temp_dir=None,
    )

    generated_home = cast(Path, captured["generated_home"])
    assert captured["events"] == [
        ("managed-enter", launch_id),
        ("run",),
        ("managed-exit", launch_id),
    ]
    assert len(backend.build_calls) == 2
    assert backend.build_calls[-1]["generated_home"] == generated_home
    assert backend.build_calls[-1]["plugin_binding"] is None
    assert not generated_home.is_relative_to(projection_root)
    assert (generated_home / "auth.json").is_file()
    assert (generated_home / "state_5.sqlite").is_file()
    assert (generated_home / "sessions" / "2026" / "08" / "rollout-test.jsonl").is_file()

    with plugin_launch_binding_scope(
        authority=authority,
        backend=backend,
        load_mode=PluginLoadMode.PROJECTED_HOME,
    ) as after_binding:
        assert after_binding is not None
        assert after_binding.identity == before_identity
        assert after_binding.identity.artifact_digest == before_digest
        assert projected_plugin_artifact_digest(projection_root) == before_digest
        assert (
            tuple(
                sorted(
                    path.relative_to(projection_root).as_posix()
                    for path in projection_root.rglob("*")
                )
            )
            == before_tree
        )

    for runtime_path in ("auth.json", "state_5.sqlite", "sessions", "archived_sessions"):
        projection_runtime_path = projection_root / runtime_path
        assert not projection_runtime_path.exists()
        assert not projection_runtime_path.is_symlink()


@pytest.mark.parametrize(
    "kind",
    _CODEX_IGNORED_CLAUDE_ARTIFACT_STATES,
    ids=lambda kind: kind.value,
)
def test_codex_session_launch_ignores_claude_installed_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    kind: PluginArtifactStateKind,
) -> None:
    state = build_plugin_artifact_state(tmp_path / "home", kind)
    _activate_production_selector(monkeypatch, state)
    backend = _RecordingBackend("codex")
    spawn_calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    _run_session_launch(monkeypatch, state, backend, spawn_calls)

    assert len(backend.build_calls) == 2
    binding = cast(PluginLaunchBinding, backend.build_calls[-1]["plugin_binding"])
    assert binding.load_mode is PluginLoadMode.PROJECTED_HOME
    assert binding.identity.managed_path != state.managed_root
    assert len(spawn_calls) == 1
