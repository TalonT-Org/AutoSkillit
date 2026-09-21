"""Tests for the session reload sentinel and loop mechanics."""

from __future__ import annotations

import json
import shutil
import subprocess
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import autoskillit.cli._init_helpers as _patch_cli__init_helpers
import autoskillit.cli.install._plugin_artifact as _patch_install__plugin_artifact
import autoskillit.cli.prompts as _patch_cli_prompts
import autoskillit.cli.session._session_cook as _patch_session__session_cook
import autoskillit.cli.session._session_launch as _patch_session__session_launch
import autoskillit.cli.session._session_onboarding as _patch_session__session_onboarding
import autoskillit.cli.session._session_process as _patch_session__session_process
import autoskillit.cli.session._session_reload as _patch_session__session_reload
import autoskillit.cli.ui._terminal as _patch_ui__terminal
import autoskillit.cli.ui._timed_input as _patch_ui__timed_input
from autoskillit.core import InteractiveInvocationValidation
from tests.cli._cook_launch_helpers import RecordingLifecycle
from tests.cli._interactive_process import InteractiveProcessStub, interactive_launch_metadata
from tests.fakes import adapt_test_skill_semantics

pytestmark = [
    pytest.mark.layer("cli"),
    pytest.mark.small,
    pytest.mark.usefixtures("_stub_interactive_prelaunch"),
    pytest.mark.usefixtures("_stub_owner_binding"),
]


class _ReloadBinding:
    def __init__(self, plugin_dir: Path) -> None:
        self.plugin_dir = plugin_dir
        self.identity = SimpleNamespace(managed_path=plugin_dir)
        self.inherited_fds: tuple[int, ...] = ()
        self.closed = False

    def close(self) -> None:
        self.closed = True


class _ReloadAuthority:
    def __init__(self, plugin_dir: Path) -> None:
        self.plugin_dir = plugin_dir

    def acquire_launch_binding(self, **_kwargs: object) -> _ReloadBinding:
        return _ReloadBinding(self.plugin_dir)


@pytest.fixture(autouse=True)
def _stub_plugin_artifact_authority(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from autoskillit.core import PluginLoadMode

    plugin_dir = tmp_path / "projected-plugin"
    plugin_dir.mkdir(exist_ok=True)
    authority = _ReloadAuthority(plugin_dir)
    monkeypatch.setattr(
        _patch_install__plugin_artifact,
        "interactive_plugin_authority",
        lambda **_kwargs: (authority, PluginLoadMode.EXPLICIT_PLUGIN_DIR),
    )


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


def test_cook_keeps_managed_home_across_reload_and_transfers_resume_after_attempt_exit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from autoskillit.core import (
        BackendConventions,
        CmdSpec,
        FreshLaunch,
        HookTrustPolicy,
        NamedResume,
        NoResume,
        RestoreSession,
        SkillUnavailabilityPayload,
    )

    generated_home = tmp_path / "managed-home"
    skills_dir = generated_home / "skills"
    skills_dir.mkdir(parents=True)
    profile_payload: SkillUnavailabilityPayload = {
        "backend": "claude-code",
        "unavailable": (
            {
                "skill": "profile-required-join",
                "backend": "claude-code",
                "operation": "required_join",
                "diagnostic": "fixed join unavailable",
            },
        ),
    }
    lifecycle = RecordingLifecycle(
        generated_home=generated_home,
        skills_dir=skills_dir,
        projection_root=tmp_path / "projected-plugin",
        unavailability_payload=profile_payload,
        returncodes=(17, 0),
    )
    events = lifecycle.events

    class _MockBackend:
        name = "claude-code"
        conventions = BackendConventions()
        capabilities = SimpleNamespace(
            hook_trust_policy=HookTrustPolicy.AUTOMATED,
            session_dir_persistent=False,
            session_scoped_explorer_capable=True,
            terminal_explorer_capable=False,
            explicit_path_env_var="",
            supports_tool_list_changed=False,
            cook_exact_binding_probe_required=False,
            skill_injection_capable=True,
        )
        adapt_skill_semantics = staticmethod(adapt_test_skill_semantics)

        def binary_name(self) -> str:
            return "claude"

        def recover_cook_history(self) -> None:
            events.append(("recover",))

        def build_interactive_cmd(self, **kwargs):
            launch = kwargs["launch"]
            plugin_binding = kwargs["plugin_binding"]
            events.append(("build", launch))
            return CmdSpec(
                cmd=("claude",),
                env={"ATTEMPT": str(len(events))},
                **interactive_launch_metadata(binary="claude", launch=launch),
                inherited_fds=plugin_binding.inherited_fds,
            )

        def interactive_ordering_flags(self) -> tuple[frozenset[str], frozenset[str]]:
            from autoskillit.execution.backends import ClaudeCodeBackend

            return ClaudeCodeBackend().interactive_ordering_flags()

        def validate_interactive_invocation(
            self, spec: CmdSpec
        ) -> InteractiveInvocationValidation:
            events.append(("validate", spec))
            return InteractiveInvocationValidation(errors=())

        def session_attempt_context(self, **kwargs):  # type: ignore[no-untyped-def]
            return lifecycle.session_attempt_context(**kwargs)

    sentinels = iter(("sess-001", None))
    onboarded: list[Path] = []

    def consume_sentinel(project_dir: Path) -> str | None:
        assert lifecycle.projection_bindings and not lifecycle.projection_bindings[-1].closed
        value = next(sentinels)
        events.append(("sentinel", value, project_dir))
        return value

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(shutil, "which", lambda x: "/usr/bin/claude")
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr(_patch_session__session_onboarding, "is_first_run", lambda _: True)
    monkeypatch.setattr(
        _patch_session__session_onboarding,
        "run_onboarding_menu",
        lambda *args, **kwargs: "/autoskillit:setup-project",
    )
    monkeypatch.setattr(
        _patch_session__session_onboarding,
        "mark_onboarded",
        lambda project_dir: onboarded.append(project_dir),
    )
    monkeypatch.setattr(_patch_ui__timed_input, "timed_prompt", lambda *args, **kwargs: "")
    monkeypatch.setattr(
        "autoskillit.workspace.DefaultSessionSkillManager", lambda *args, **kwargs: lifecycle
    )
    monkeypatch.setattr(
        _patch_session__session_cook,
        "render_skill_unavailability",
        lifecycle.record_render,
    )
    monkeypatch.setattr(
        _patch_session__session_process,
        "run_cook_attempt",
        lifecycle.run_cook_attempt,
    )
    monkeypatch.setattr(
        _patch_session__session_reload, "consume_reload_sentinel", consume_sentinel
    )
    from autoskillit.core import PluginLoadMode

    monkeypatch.setattr(
        _patch_install__plugin_artifact,
        "interactive_plugin_authority",
        lambda **_kwargs: (
            lifecycle,
            PluginLoadMode.EXPLICIT_PLUGIN_DIR,
        ),
    )

    from autoskillit import cli

    cli.cook(backend=_MockBackend())

    managed_enters = lifecycle.events_of_type("managed-enter")
    managed_exits = lifecycle.events_of_type("managed-exit")
    assert len(managed_enters) == len(managed_exits) == 1
    assert lifecycle.rendered_payloads == [profile_payload]
    assert (
        events.index(managed_enters[0])
        < events.index(("render", profile_payload))
        < events.index(lifecycle.events_of_type("build")[0])
    )

    attempt_enters = lifecycle.events_of_type("attempt-enter")
    assert [event[1] for event in attempt_enters] == [1, 2]
    assert all(event[3] == generated_home for event in attempt_enters)
    assert isinstance(attempt_enters[0][2], NoResume)
    assert isinstance(attempt_enters[1][2], NamedResume)
    assert attempt_enters[1][2].session_id == "sess-001"

    first_sentinel = events.index(lifecycle.event_for("sentinel", "sess-001"))
    first_reaped = events.index(lifecycle.event_for("reaped", 1))
    first_exit = events.index(lifecycle.event_for("attempt-exit", 1))
    second_build = events.index(
        next(
            event
            for event in events
            if event[0] == "build" and isinstance(event[1], RestoreSession)
        )
    )
    assert first_reaped < first_sentinel < first_exit < second_build

    run_events = lifecycle.events_of_type("run")
    assert [event[3] for event in run_events] == [(5, 7, 11), (5, 7, 11)]
    build_launches = [event[1] for event in events if event[0] == "build"]
    assert len(build_launches) == 4
    fresh_launches = [launch for launch in build_launches if isinstance(launch, FreshLaunch)]
    restored_launches = [launch for launch in build_launches if isinstance(launch, RestoreSession)]
    assert len(fresh_launches) == 2
    assert restored_launches == [RestoreSession(session_id="sess-001")] * 2
    assert all(
        (launch.system_prompt or "").count("<autoskillit_skill_unavailability>") == 1
        for launch in fresh_launches
    )
    assert all(
        "profile-required-join" in (launch.system_prompt or "") for launch in fresh_launches
    )
    assert len(lifecycle.projection_bindings) == 1
    assert lifecycle.projection_bindings[0].closed
    assert events.index(managed_exits[0]) > events.index(lifecycle.event_for("attempt-exit", 2))
    assert onboarded == [tmp_path]
    assert ("recover",) not in events


@pytest.mark.parametrize(
    ("reload_ids", "expected_attempts", "message"),
    [
        (("same", "same"), 2, "Repeated reload_id"),
        (tuple(f"reload-{index}" for index in range(11)), 11, "Too many reloads"),
    ],
)
def test_cook_rejects_repeated_and_excessive_reload_requests(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    reload_ids: tuple[str, ...],
    expected_attempts: int,
    message: str,
) -> None:
    from autoskillit.core import (
        BackendConventions,
        CmdSpec,
        CompiledSessionSkillCatalogAuthority,
        HookTrustPolicy,
        ManagedSessionHome,
        SessionAttemptHandle,
        SkillProjectionContextAuthority,
        ValidatedAddDir,
    )

    generated_home = tmp_path / "managed-home"
    skills_dir = generated_home / "skills"
    skills_dir.mkdir(parents=True)
    manager = MagicMock()
    managed_exits: list[str] = []
    attempts: list[int] = []

    @contextmanager
    def managed_session(
        launch_id: str,
        compilation: CompiledSessionSkillCatalogAuthority,
        projection_context: SkillProjectionContextAuthority,
    ):
        assert projection_context.catalog == compilation.catalog
        try:
            yield ManagedSessionHome(
                launch_id=launch_id,
                generated_home=generated_home,
                skills_dir=ValidatedAddDir(str(skills_dir)),
                pass_fds=(),
                unavailability_payload={"backend": "claude-code", "unavailable": ()},
            )
        finally:
            managed_exits.append(launch_id)

    manager.managed_session.side_effect = managed_session

    class _Backend:
        name = "claude-code"
        conventions = BackendConventions()
        capabilities = SimpleNamespace(
            hook_trust_policy=HookTrustPolicy.AUTOMATED,
            session_dir_persistent=False,
            session_scoped_explorer_capable=True,
            terminal_explorer_capable=False,
            explicit_path_env_var="",
            supports_tool_list_changed=True,
            cook_exact_binding_probe_required=False,
            skill_injection_capable=True,
        )
        adapt_skill_semantics = staticmethod(adapt_test_skill_semantics)

        def binary_name(self) -> str:
            return "claude"

        def recover_cook_history(self) -> None:
            return None

        def build_interactive_cmd(self, **kwargs: object) -> CmdSpec:
            return CmdSpec(
                cmd=("claude",),
                env={},
                **interactive_launch_metadata(binary="claude", launch=kwargs["launch"]),
            )

        def interactive_ordering_flags(self) -> tuple[frozenset[str], frozenset[str]]:
            from autoskillit.execution.backends import ClaudeCodeBackend

            return ClaudeCodeBackend().interactive_ordering_flags()

        def validate_interactive_invocation(
            self, spec: CmdSpec
        ) -> InteractiveInvocationValidation:
            return InteractiveInvocationValidation(errors=())

        @contextmanager
        def session_attempt_context(self, *, attempt: int, **kwargs: object):
            attempts.append(attempt)
            yield SessionAttemptHandle(
                view_id=f"view-{attempt}",
                pass_fds=(),
                _record_spawn=lambda _pid, _pgid: None,
                _record_reaped=lambda _pid, _pgid: None,
            )

    sentinel_values = iter(reload_ids)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(shutil, "which", lambda _name: "/usr/bin/claude")
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr(_patch_session__session_onboarding, "is_first_run", lambda _: False)
    monkeypatch.setattr(
        _patch_ui__timed_input,
        "timed_prompt",
        lambda *args, **kwargs: "",
    )
    monkeypatch.setattr(
        "autoskillit.workspace.DefaultSessionSkillManager",
        lambda *args, **kwargs: manager,
    )
    monkeypatch.setattr(
        _patch_session__session_process,
        "run_cook_attempt",
        lambda *args, **kwargs: SimpleNamespace(pid=1, pgid=1, returncode=0),
    )
    monkeypatch.setattr(
        _patch_session__session_reload,
        "consume_reload_sentinel",
        lambda _project: next(sentinel_values),
    )

    from autoskillit import cli

    with pytest.raises(SystemExit, match=message):
        cli.cook(backend=_Backend())

    assert attempts == list(range(1, expected_attempts + 1))
    assert len(managed_exits) == 1


# ---------------------------------------------------------------------------
# RL-5 — _run_interactive_session returns session_id when sentinel exists
# ---------------------------------------------------------------------------


def test_interactive_session_reload_uses_named_resume(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from autoskillit.core import FreshLaunch

    _write_sentinel(tmp_path, "isess-001")
    monkeypatch.setattr(shutil, "which", lambda _: "/usr/bin/claude")
    monkeypatch.setattr(
        subprocess,
        "Popen",
        lambda *a, **kw: InteractiveProcessStub(pid=123),
    )
    monkeypatch.setattr(_patch_ui__terminal, "terminal_guard", _noop_terminal_guard)
    monkeypatch.setattr(_patch_cli__init_helpers, "_is_plugin_installed", lambda **_: True)

    from autoskillit.cli.session._session_launch import _run_interactive_session

    result = _run_interactive_session(
        launch=FreshLaunch(system_prompt="test"), project_dir=tmp_path
    )
    assert result == "isess-001"


# ---------------------------------------------------------------------------
# RL-6 — Fleet reload restores the session without replaying its system prompt
# ---------------------------------------------------------------------------


@pytest.mark.feature("fleet")
def test_fleet_reload_restores_session_without_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from autoskillit.core import FreshLaunch, RestoreSession

    call_count = [0]
    captured_launches: list[object] = []
    captured_skill_compilations: list[object | None] = []
    skill_compilation = MagicMock()
    skill_compilation.unavailability_payload = {"backend": "claude-code", "unavailable": ()}

    def fake_run_interactive_session(
        *,
        launch: object,
        skill_compilation=None,
        **kwargs: object,
    ) -> str | None:
        call_count[0] += 1
        captured_launches.append(launch)
        captured_skill_compilations.append(skill_compilation)
        if call_count[0] == 1:
            return "franchise-sess"
        return None

    monkeypatch.setattr(
        _patch_session__session_launch,
        "_run_interactive_session",
        fake_run_interactive_session,
    )
    monkeypatch.setattr(
        "autoskillit.cli.detect_autoskillit_mcp_prefix",
        lambda _capabilities: "autoskillit",
    )
    monkeypatch.setattr(
        _patch_cli_prompts,
        "_build_fleet_dispatch_prompt",
        lambda mcp_prefix, **kw: "test-prompt",
    )
    monkeypatch.setattr(
        "autoskillit.workspace.compile_session_skill_catalog",
        lambda *_args, **_kwargs: skill_compilation,
    )
    monkeypatch.chdir(tmp_path)

    from autoskillit.cli.fleet import _launch_fleet_session

    _launch_fleet_session(
        campaign_recipe=None,
        campaign_id=None,
        state_path=None,
        resume_metadata=None,
        fleet_mode="dispatch",
    )

    assert call_count[0] == 2
    assert captured_launches == [
        FreshLaunch(system_prompt="test-prompt"),
        RestoreSession(session_id="franchise-sess"),
    ]
    assert all(compilation is skill_compilation for compilation in captured_skill_compilations)
