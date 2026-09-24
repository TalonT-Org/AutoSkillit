"""Hermetic harness for real-backend cook launch tests."""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import autoskillit.cli.install._plugin_artifact as _patch_install__plugin_artifact
import autoskillit.cli.session._session_cook as _patch_session__session_cook
import autoskillit.cli.session._session_onboarding as _patch_session__session_onboarding
import autoskillit.cli.session._session_process as _patch_session__session_process
import autoskillit.cli.ui._timed_input as _patch_ui__timed_input
from autoskillit.cli.session._session_process import CookAttemptResult
from autoskillit.config import AutomationConfig
from autoskillit.core import (
    CmdSpec,
    CompiledSessionSkillCatalogAuthority,
    ManagedSessionHome,
    PluginLoadMode,
    ProcessCleanupResult,
    SessionAttemptHandle,
    SkillProjectionContextAuthority,
    SkillUnavailabilityPayload,
    TerminationReason,
    ValidatedAddDir,
)


def cook_attempt_result(
    *,
    returncode: int | None = 0,
    termination: TerminationReason = TerminationReason.NATURAL_EXIT,
) -> CookAttemptResult:
    """Build a complete result for fake cook-attempt runners."""
    pid = 101
    return CookAttemptResult(
        pid=pid,
        pgid=pid,
        returncode=returncode,
        termination=termination,
        elapsed_seconds=0.0,
        cleanup=ProcessCleanupResult(
            root_pid=pid,
            process_identities=((pid, 0.0),),
            terminated_pids=(pid,),
            observation_complete=True,
        ),
    )


class _RecordingProjectionBinding:
    def __init__(
        self,
        managed_path: Path,
        inherited_fds: tuple[int, ...],
        events: list[tuple[object, ...]],
    ) -> None:
        self.plugin_dir = managed_path
        self.identity = SimpleNamespace(managed_path=managed_path)
        self.inherited_fds = inherited_fds
        self.closed = False
        self._events = events

    def close(self) -> None:
        self.closed = True
        self._events.append(("projection-exit",))


class RecordingLifecycle:
    """Fresh managed-session and attempt recording for one launch test."""

    def __init__(
        self,
        *,
        generated_home: Path,
        skills_dir: Path,
        projection_root: Path,
        unavailability_payload: SkillUnavailabilityPayload,
        returncodes: Iterable[int],
        record_trace_spawn: bool = False,
    ) -> None:
        self.events: list[tuple[object, ...]] = []
        self._generated_home = generated_home
        self._skills_dir = skills_dir
        self._projection_root = projection_root
        self._unavailability_payload = unavailability_payload
        self._results = iter(
            cook_attempt_result(returncode=returncode) for returncode in returncodes
        )
        self._record_trace_spawn = record_trace_spawn
        self.projection_bindings: list[_RecordingProjectionBinding] = []
        self.rendered_payloads: list[SkillUnavailabilityPayload] = []

    def events_of_type(self, event_type: str) -> list[tuple[object, ...]]:
        return [event for event in self.events if event[0] == event_type]

    def event_for(self, event_type: str, subject: object) -> tuple[object, ...]:
        return next(event for event in self.events if event[:2] == (event_type, subject))

    def record_render(self, payload: SkillUnavailabilityPayload) -> None:
        self.rendered_payloads.append(payload)
        self.events.append(("render", payload))

    def acquire_launch_binding(self, **_kwargs: object) -> _RecordingProjectionBinding:
        self.events.append(("projection-enter",))
        binding = _RecordingProjectionBinding(self._projection_root, (5, 7), self.events)
        self.projection_bindings.append(binding)
        return binding

    def cleanup_stale(self, max_age_seconds: int = 86400) -> int:
        del max_age_seconds
        return 0

    @contextmanager
    def managed_session(
        self,
        launch_id: str,
        compilation: CompiledSessionSkillCatalogAuthority,
        projection_context: SkillProjectionContextAuthority,
    ) -> Iterator[ManagedSessionHome]:
        assert projection_context.catalog == compilation.catalog
        self.events.append(("managed-enter", launch_id, projection_context))
        try:
            yield ManagedSessionHome(
                managed_projection=None,
                launch_id=launch_id,
                generated_home=self._generated_home,
                skills_dir=ValidatedAddDir(str(self._skills_dir)),
                pass_fds=(7,),
                unavailability_payload=self._unavailability_payload,
            )
        finally:
            self.events.append(("managed-exit", launch_id))

    @contextmanager
    def session_attempt_context(
        self,
        *,
        session_home: Path,
        project_dir: Path,
        launch_id: str,
        attempt: int,
        current_resume_spec: object,
    ) -> Iterator[SessionAttemptHandle]:
        self.events.append(
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
            yield SessionAttemptHandle(
                view_id=f"{launch_id}-{attempt}",
                pass_fds=(11,),
                _record_spawn=lambda pid, pgid: self.events.append(("spawn", attempt, pid, pgid)),
                _record_reaped=lambda pid, pgid: self.events.append(
                    ("reaped", attempt, pid, pgid)
                ),
                _record_teardown_unproven=lambda _pid, _pgid: None,
            )
        finally:
            self.events.append(("attempt-exit", attempt, current_resume_spec))

    def run_cook_attempt(
        self,
        spec: CmdSpec,
        *,
        pass_fds: tuple[int, ...],
        on_spawn,
        on_reaped,
        trace: object | None = None,
        **_kwargs: object,
    ) -> CookAttemptResult:  # type: ignore[no-untyped-def]
        attempt = sum(event[0] == "run" for event in self.events) + 1
        self.events.append(("run", attempt, spec, pass_fds))
        result = next(self._results)
        on_spawn(result.pid, result.pgid)
        if self._record_trace_spawn:
            trace.record_spawn()  # type: ignore[union-attr]
        on_reaped(result.pid, result.pgid)
        return result


class _Binding:
    def __init__(self, plugin_dir: Path) -> None:
        self.plugin_dir = plugin_dir
        self.identity = SimpleNamespace(managed_path=plugin_dir)
        self.inherited_fds: tuple[int, ...] = ()
        self.closed = False

    def close(self) -> None:
        self.closed = True


class _Authority:
    def __init__(self, plugin_dir: Path) -> None:
        self.plugin_dir = plugin_dir

    def acquire_launch_binding(self, **_kwargs: object) -> _Binding:
        return _Binding(self.plugin_dir)


def arrange_cook(
    monkeypatch,
    tmp_path: Path,
    *,
    config: AutomationConfig | None = None,
    settings_content: dict | None = None,
    project_dir_override: Path | None = None,
) -> list[CmdSpec]:
    """Patch cook's materialization edges while retaining real backend builders.

    settings_content, when given, is written as the project's
    .claude/settings.local.json before cook runs — the composition #4684's
    regression tests exercise (a real populated settings file, not an empty
    tmp_path project directory). None (the default) writes nothing, matching
    every pre-existing call site's behavior unchanged.

    project_dir_override, when given, is used as the resolved project
    directory instead of creating tmp_path/"project" — for the opt-in live
    gate that exercises the real repository root's own .claude/settings.local.json
    (test_cook_real_root_smoke.py). The generated managed-home/plugin
    directories remain tmp_path-isolated regardless; only project_dir (and
    therefore what settings.local.json read) changes. Callers passing an
    override own creating/populating that directory themselves — this
    function will not mkdir() or write settings_content into it, since doing
    so on a real, pre-existing repository root would be destructive.
    """
    if project_dir_override is not None and settings_content is not None:
        raise ValueError(
            "arrange_cook() cannot combine settings_content with "
            "project_dir_override — callers passing project_dir_override own "
            "creating/populating that directory themselves."
        )
    if project_dir_override is not None:
        project_dir = project_dir_override
    else:
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        if settings_content is not None:
            claude_dir = project_dir / ".claude"
            claude_dir.mkdir(parents=True, exist_ok=True)
            (claude_dir / "settings.local.json").write_text(json.dumps(settings_content))
    generated_home = tmp_path / "managed-home"
    skills_dir = generated_home / "skills"
    skills_dir.mkdir(parents=True)
    plugin_dir = tmp_path / "projected-plugin"
    plugin_dir.mkdir()
    manager = MagicMock()
    captured: list[CmdSpec] = []

    @contextmanager
    def managed_session(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        yield ManagedSessionHome(
            managed_projection=None,
            launch_id="launch",
            generated_home=generated_home,
            skills_dir=ValidatedAddDir(str(skills_dir)),
            pass_fds=(),
            unavailability_payload={"backend": None, "unavailable": ()},
        )

    manager.managed_session.side_effect = managed_session
    monkeypatch.chdir(project_dir)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("AUTOSKILLIT_CODEX_STARTUP_TRACE", raising=False)
    monkeypatch.setattr(
        "autoskillit.config.load_config",
        lambda: config or AutomationConfig(),
    )
    monkeypatch.setattr(
        _patch_session__session_cook,
        "resolve_project_dir",
        lambda: project_dir,
    )
    monkeypatch.setattr(_patch_session__session_onboarding, "is_first_run", lambda _path: False)
    monkeypatch.setattr(_patch_ui__timed_input, "timed_prompt", lambda *args, **kwargs: "")
    monkeypatch.setattr(
        "autoskillit.workspace.DefaultSessionSkillManager", lambda *args, **kwargs: manager
    )
    monkeypatch.setattr(
        _patch_install__plugin_artifact,
        "interactive_plugin_authority",
        lambda **_kwargs: (_Authority(plugin_dir), PluginLoadMode.EXPLICIT_PLUGIN_DIR),
    )
    monkeypatch.setattr("autoskillit.core.write_registry_entry", lambda *args, **kwargs: None)

    def capture(spec: CmdSpec, **_kwargs: object) -> CookAttemptResult:
        captured.append(spec)
        return cook_attempt_result()

    monkeypatch.setattr(
        _patch_session__session_process,
        "run_cook_attempt",
        capture,
    )
    return captured
