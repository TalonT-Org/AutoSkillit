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

from tests.cli._interactive_process import InteractiveProcessStub
from tests.fakes import adapt_test_skill_semantics

pytestmark = [
    pytest.mark.layer("cli"),
    pytest.mark.small,
    pytest.mark.usefixtures("_stub_interactive_prelaunch"),
]


@pytest.fixture(autouse=True)
def _stub_owner_binding(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("autoskillit.core.bind_session_owner", lambda *_args: None)


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
        "autoskillit.cli._plugin_artifact.interactive_plugin_authority",
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
        CompiledSessionSkillCatalogAuthority,
        CookSessionHandle,
        HookTrustPolicy,
        ManagedSessionHome,
        NamedResume,
        NoResume,
        SkillProjectionContextAuthority,
        ValidatedAddDir,
    )

    events: list[tuple[object, ...]] = []
    generated_home = tmp_path / "managed-home"
    skills_dir = generated_home / "skills"
    skills_dir.mkdir(parents=True)
    manager = MagicMock()

    @contextmanager
    def managed_session(
        launch_id: str,
        compilation: CompiledSessionSkillCatalogAuthority,
        projection_context: SkillProjectionContextAuthority,
    ):
        assert projection_context.catalog == compilation.catalog
        events.append(("managed-enter", launch_id, projection_context))
        try:
            yield ManagedSessionHome(
                launch_id=launch_id,
                generated_home=generated_home,
                skills_dir=ValidatedAddDir(str(skills_dir)),
                pass_fds=(7,),
            )
        finally:
            events.append(("managed-exit", launch_id))

    manager.managed_session.side_effect = managed_session

    class _MockBackend:
        name = "claude-code"
        conventions = BackendConventions()
        capabilities = SimpleNamespace(
            hook_trust_policy=HookTrustPolicy.AUTOMATED,
            session_dir_persistent=False,
            session_scoped_explorer_capable=True,
            terminal_explorer_capable=False,
            supports_tool_list_changed=True,
            cook_exact_binding_probe_required=False,
            skill_injection_capable=True,
        )
        adapt_skill_semantics = staticmethod(adapt_test_skill_semantics)

        def binary_name(self) -> str:
            return "claude"

        def recover_cook_history(self) -> None:
            events.append(("recover",))

        def build_interactive_cmd(self, **kwargs):
            resume_spec = kwargs["resume_spec"]
            plugin_binding = kwargs["plugin_binding"]
            events.append(("build", resume_spec))
            return CmdSpec(
                cmd=("claude",),
                env={"ATTEMPT": str(len(events))},
                inherited_fds=plugin_binding.inherited_fds,
            )

        def validate_interactive_invocation(self, spec: CmdSpec) -> list[str]:
            events.append(("validate", spec))
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
        ):
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

    results = iter(
        (
            SimpleNamespace(pid=101, pgid=101, returncode=17),
            SimpleNamespace(pid=102, pgid=102, returncode=42),
        )
    )

    def fake_run_cook_attempt(
        spec: CmdSpec,
        *,
        pass_fds: tuple[int, ...],
        on_spawn,
        on_reaped,
        **_: object,
    ) -> object:
        attempt = sum(event[0] == "run" for event in events) + 1
        events.append(("run", attempt, spec, pass_fds))
        on_spawn(100 + attempt, 100 + attempt)
        on_reaped(100 + attempt, 100 + attempt)
        return next(results)

    sentinels = iter(("sess-001", None))
    onboarded: list[Path] = []

    def consume_sentinel(project_dir: Path) -> str | None:
        assert bindings and not bindings[-1].closed
        value = next(sentinels)
        events.append(("sentinel", value, project_dir))
        return value

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(shutil, "which", lambda x: "/usr/bin/claude")
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("autoskillit.cli._onboarding.is_first_run", lambda _: True)
    monkeypatch.setattr(
        "autoskillit.cli._onboarding.run_onboarding_menu",
        lambda *args, **kwargs: "/autoskillit:setup-project",
    )
    monkeypatch.setattr(
        "autoskillit.cli._onboarding.mark_onboarded",
        lambda project_dir: onboarded.append(project_dir),
    )
    monkeypatch.setattr("autoskillit.cli.ui._timed_input.timed_prompt", lambda *args, **kwargs: "")
    monkeypatch.setattr(
        "autoskillit.workspace.DefaultSessionSkillManager", lambda *args, **kwargs: manager
    )
    monkeypatch.setattr(
        "autoskillit.cli.session._session_process.run_cook_attempt", fake_run_cook_attempt
    )
    monkeypatch.setattr(
        "autoskillit.cli.session._session_reload.consume_reload_sentinel", consume_sentinel
    )
    from autoskillit.core import PluginLoadMode

    bindings: list[_ReloadBinding] = []

    class _SessionAuthority:
        def acquire_launch_binding(self, **_kwargs: object) -> _ReloadBinding:
            binding = _ReloadBinding(tmp_path / "projected-plugin")
            binding.inherited_fds = (5, 7)
            bindings.append(binding)
            return binding

    monkeypatch.setattr(
        "autoskillit.cli._plugin_artifact.interactive_plugin_authority",
        lambda **_kwargs: (
            _SessionAuthority(),
            PluginLoadMode.EXPLICIT_PLUGIN_DIR,
        ),
    )

    from autoskillit import cli

    with pytest.raises(SystemExit) as exc_info:
        cli.cook(backend=_MockBackend())
    assert exc_info.value.code == 42

    managed_enters = [event for event in events if event[0] == "managed-enter"]
    managed_exits = [event for event in events if event[0] == "managed-exit"]
    assert len(managed_enters) == len(managed_exits) == 1

    attempt_enters = [event for event in events if event[0] == "attempt-enter"]
    assert [event[1] for event in attempt_enters] == [1, 2]
    assert all(event[3] == generated_home for event in attempt_enters)
    assert isinstance(attempt_enters[0][2], NoResume)
    assert isinstance(attempt_enters[1][2], NamedResume)
    assert attempt_enters[1][2].session_id == "sess-001"

    first_sentinel = events.index(
        next(event for event in events if event[:2] == ("sentinel", "sess-001"))
    )
    first_reaped = events.index(next(event for event in events if event[:2] == ("reaped", 1)))
    first_exit = events.index(next(event for event in events if event[:2] == ("attempt-exit", 1)))
    second_build = events.index(
        next(
            event for event in events if event[0] == "build" and isinstance(event[1], NamedResume)
        )
    )
    assert first_reaped < first_sentinel < first_exit < second_build

    run_events = [event for event in events if event[0] == "run"]
    assert [event[3] for event in run_events] == [(5, 7, 11), (5, 7, 11)]
    assert len(bindings) == 1
    assert bindings[0].closed
    assert events.index(managed_exits[0]) > events.index(
        next(event for event in events if event[:2] == ("attempt-exit", 2))
    )
    assert onboarded == []
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
        CookSessionHandle,
        HookTrustPolicy,
        ManagedSessionHome,
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
            return CmdSpec(cmd=("claude",), env={})

        def validate_interactive_invocation(self, spec: CmdSpec) -> list[str]:
            return []

        @contextmanager
        def cook_session_context(self, *, attempt: int, **kwargs: object):
            attempts.append(attempt)
            yield CookSessionHandle(
                view_id=f"view-{attempt}",
                pass_fds=(),
                _record_spawn=lambda _pid, _pgid: None,
                _record_reaped=lambda _pid, _pgid: None,
            )

    sentinel_values = iter(reload_ids)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(shutil, "which", lambda _name: "/usr/bin/claude")
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("autoskillit.cli._onboarding.is_first_run", lambda _: False)
    monkeypatch.setattr(
        "autoskillit.cli.ui._timed_input.timed_prompt",
        lambda *args, **kwargs: "",
    )
    monkeypatch.setattr(
        "autoskillit.workspace.DefaultSessionSkillManager",
        lambda *args, **kwargs: manager,
    )
    monkeypatch.setattr(
        "autoskillit.cli.session._session_process.run_cook_attempt",
        lambda *args, **kwargs: SimpleNamespace(pid=1, pgid=1, returncode=0),
    )
    monkeypatch.setattr(
        "autoskillit.cli.session._session_reload.consume_reload_sentinel",
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
    _write_sentinel(tmp_path, "isess-001")
    monkeypatch.setattr(shutil, "which", lambda _: "/usr/bin/claude")
    monkeypatch.setattr(
        subprocess,
        "Popen",
        lambda *a, **kw: InteractiveProcessStub(pid=123),
    )
    monkeypatch.setattr("autoskillit.cli.ui._terminal.terminal_guard", _noop_terminal_guard)
    monkeypatch.setattr("autoskillit.cli._init_helpers._is_plugin_installed", lambda **_: True)

    from autoskillit.cli.session._session_launch import _run_interactive_session

    result = _run_interactive_session(system_prompt="test", project_dir=tmp_path)
    assert result == "isess-001"


# ---------------------------------------------------------------------------
# RL-6 — Fleet reload re-launches with same system_prompt, no --resume
# ---------------------------------------------------------------------------


@pytest.mark.feature("fleet")
def test_fleet_reload_relaunches_without_resume(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from autoskillit.core import NamedResume, NoResume

    call_count = [0]
    captured_resume_specs: list = []

    def fake_run_interactive_session(
        prompt,
        *,
        extra_env=None,
        resume_spec=None,
        project_dir=None,
        initial_message=None,
        required_env=None,
        backend=None,
    ):
        call_count[0] += 1
        captured_resume_specs.append(resume_spec)
        if call_count[0] == 1:
            return "franchise-sess"
        return None

    monkeypatch.setattr(
        "autoskillit.cli.session._session_launch._run_interactive_session",
        fake_run_interactive_session,
    )
    monkeypatch.setattr(
        "autoskillit.cli.detect_autoskillit_mcp_prefix",
        lambda _capabilities: "autoskillit",
    )
    monkeypatch.setattr(
        "autoskillit.cli._prompts._build_fleet_dispatch_prompt",
        lambda mcp_prefix, **kw: "test-prompt",
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
    assert isinstance(captured_resume_specs[0], NoResume)
    assert isinstance(captured_resume_specs[1], NamedResume)
    assert captured_resume_specs[1].session_id == "franchise-sess"
