"""Tests for the cook CLI command (interactive skill session)."""

from __future__ import annotations

import shutil
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from autoskillit import cli
from autoskillit.core import (
    CODEX_COOK_RESERVED_ENV_VARS,
    CODEX_STARTUP_TRACE_ENV_VAR,
    LAUNCH_ID_ENV_VAR,
    SESSION_TYPE_ENV_VAR,
    BackendConventions,
    CmdSpec,
    CookSessionHandle,
    EffectiveSkillCatalogAuthority,
    HookTrustPolicy,
    ManagedSessionHome,
    NamedResume,
    NoResume,
    SkillProjectionContextAuthority,
    ValidatedAddDir,
)
from tests.fakes import adapt_test_skill_semantics

pytestmark = [pytest.mark.layer("cli"), pytest.mark.medium]


class _CookBinding:
    def __init__(self, plugin_dir: Path) -> None:
        self.plugin_dir = plugin_dir
        self.inherited_fds: tuple[int, ...] = ()
        self.closed = False

    def close(self) -> None:
        self.closed = True


class _CookAuthority:
    def __init__(self, plugin_dir: Path) -> None:
        self.plugin_dir = plugin_dir

    def acquire_launch_binding(self, **_kwargs: object) -> _CookBinding:
        return _CookBinding(self.plugin_dir)


@pytest.fixture(autouse=True)
def _stub_plugin_artifact_authority(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from autoskillit.core import PluginLoadMode

    plugin_dir = tmp_path / ".autoskillit" / "plugin-projections" / "test-artifact"
    plugin_dir.mkdir(parents=True)
    plugin_metadata = plugin_dir / ".claude-plugin" / "plugin.json"
    plugin_metadata.parent.mkdir()
    plugin_metadata.write_text("{}\n", encoding="utf-8")
    authority = _CookAuthority(plugin_dir)

    def choose(**kwargs: object):
        backend = kwargs["backend"]
        if getattr(backend, "name", None) == "codex" and kwargs["generated_home"] is not None:
            return None, PluginLoadMode.GENERATED_HOME
        return authority, PluginLoadMode.EXPLICIT_PLUGIN_DIR

    monkeypatch.setattr(
        "autoskillit.cli._plugin_artifact.interactive_plugin_authority",
        choose,
    )


class _Backend:
    name = "claude-code"
    conventions = BackendConventions()
    capabilities = SimpleNamespace(
        hook_trust_policy=HookTrustPolicy.AUTOMATED,
        session_dir_persistent=False,
        skill_injection_capable=True,
    )
    adapt_skill_semantics = staticmethod(adapt_test_skill_semantics)

    def __init__(self) -> None:
        self.build_calls: list[dict[str, object]] = []
        self.context_calls: list[dict[str, object]] = []
        self.validated: list[CmdSpec] = []
        self.recover_count = 0
        self.extra_inherited_fds: tuple[int, ...] = ()

    def binary_name(self) -> str:
        return "claude"

    def recover_cook_history(self) -> None:
        self.recover_count += 1

    def session_locator(self) -> object:
        return SimpleNamespace()

    def build_interactive_cmd(self, **kwargs: object) -> CmdSpec:
        self.build_calls.append(kwargs)
        command = ["claude", "--dangerously-skip-permissions"]
        plugin_binding = kwargs["plugin_binding"]
        plugin_dir = getattr(plugin_binding, "plugin_dir", None)
        if plugin_dir is not None:
            command.extend(("--plugin-dir", str(plugin_dir)))
        for add_dir in kwargs["add_dirs"]:  # type: ignore[union-attr]
            command.extend(("--add-dir", str(add_dir)))
        return CmdSpec(
            cmd=tuple(command),
            env=dict(kwargs["env_extras"]),  # type: ignore[arg-type]
            inherited_fds=(
                *self.extra_inherited_fds,
                *getattr(plugin_binding, "inherited_fds", ()),
            ),
        )

    def validate_interactive_invocation(self, spec: CmdSpec) -> list[str]:
        self.validated.append(spec)
        return []

    @contextmanager
    def cook_session_context(self, **kwargs: object):
        self.context_calls.append(kwargs)
        yield CookSessionHandle(
            view_id="test-view",
            pass_fds=(9,),
            _record_spawn=lambda _pid, _pgid: None,
            _record_reaped=lambda _pid, _pgid: None,
        )


def _install_harness(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    first_run: bool = False,
    onboarding_prompt: str | None = None,
    confirm: str = "",
    returncode: int = 0,
    picked_session: str | None = None,
) -> dict[str, object]:
    generated_home = tmp_path / "managed-home"
    skills_dir = generated_home / "skills"
    skills_dir.mkdir(parents=True)
    manager = MagicMock()
    events: list[tuple[object, ...]] = []
    captured: dict[str, object] = {"events": events, "manager": manager}

    @contextmanager
    def managed_session(
        launch_id: str,
        catalog: EffectiveSkillCatalogAuthority,
        projection_context: SkillProjectionContextAuthority,
    ):
        events.append(("managed-enter", launch_id, catalog, projection_context))
        assert projection_context.catalog == catalog
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

    def run_attempt(spec: CmdSpec, **kwargs: object) -> object:
        captured["spec"] = spec
        captured["run_kwargs"] = kwargs
        events.append(("run",))
        assertion = captured.get("run_attempt_assertion")
        if callable(assertion):
            assertion()
        kwargs["on_spawn"](101, 101)  # type: ignore[operator]
        kwargs["trace"].record_spawn()  # type: ignore[union-attr]
        kwargs["on_reaped"](101, 101)  # type: ignore[operator]
        if callable(assertion):
            assertion()
        return SimpleNamespace(pid=101, pgid=101, returncode=returncode)

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(shutil, "which", lambda _name, **_kwargs: "/usr/bin/claude")
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr(
        "autoskillit.workspace.DefaultSessionSkillManager",
        lambda *args, **kwargs: manager,
    )
    monkeypatch.setattr(
        "autoskillit.cli._installed_plugins.InstalledPluginsFile.contains",
        lambda self, key: False,
    )
    monkeypatch.setattr(
        "autoskillit.cli._onboarding.is_first_run",
        lambda _project: first_run,
    )
    monkeypatch.setattr(
        "autoskillit.cli._onboarding.run_onboarding_menu",
        lambda *args, **kwargs: onboarding_prompt,
    )
    monkeypatch.setattr(
        "autoskillit.cli._onboarding.mark_onboarded",
        lambda project: events.append(("onboarded", project)),
    )
    monkeypatch.setattr(
        "autoskillit.cli.ui._timed_input.timed_prompt",
        lambda *args, **kwargs: confirm,
    )
    monkeypatch.setattr(
        "autoskillit.core.write_registry_entry",
        lambda project, launch_id, session_type, session_id: events.append(
            ("registry", launch_id)
        ),
    )
    monkeypatch.setattr(
        "autoskillit.cli.session._session_process.run_cook_attempt",
        run_attempt,
    )
    monkeypatch.setattr(
        "autoskillit.cli.session._session_reload.consume_reload_sentinel",
        lambda _project: None,
    )
    monkeypatch.setattr(
        "autoskillit.cli.session._session_picker.pick_session",
        lambda *args, **kwargs: picked_session,
    )
    captured["generated_home"] = generated_home
    captured["skills_dir"] = skills_dir
    return captured


def test_cook_uses_managed_home_for_final_child_context(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from autoskillit.core import PluginLoadMode

    backend = _Backend()
    backend.extra_inherited_fds = (17, 13)
    captured = _install_harness(monkeypatch, tmp_path)
    binding = _CookBinding(tmp_path / "projected-plugin")
    binding.inherited_fds = (13, 7)

    def assert_binding_open() -> None:
        assert not binding.closed

    captured["run_attempt_assertion"] = assert_binding_open
    authority = _CookAuthority(binding.plugin_dir)
    authority.acquire_launch_binding = lambda **_kwargs: binding  # type: ignore[method-assign]
    monkeypatch.setattr(
        "autoskillit.cli._plugin_artifact.interactive_plugin_authority",
        lambda **_kwargs: (authority, PluginLoadMode.EXPLICIT_PLUGIN_DIR),
    )

    cli.cook(backend=backend)

    generated_home = captured["generated_home"]
    skills_dir = captured["skills_dir"]
    build = backend.build_calls[0]
    assert build["generated_home"] == generated_home
    assert build["add_dirs"] == [ValidatedAddDir(str(skills_dir))]
    assert build["resume_spec"] == NoResume()
    assert backend.recover_count == 0
    assert backend.context_calls[0]["session_home"] == generated_home
    assert backend.context_calls[0]["project_dir"] == tmp_path
    spec = captured["spec"]
    assert isinstance(spec, CmdSpec)
    assert spec is backend.validated[0]
    assert spec.cwd == str(tmp_path)
    assert spec.env[SESSION_TYPE_ENV_VAR] == "skill"
    assert len(spec.env[LAUNCH_ID_ENV_VAR]) == 16
    assert captured["run_kwargs"]["pass_fds"] == (17, 13, 7, 9)  # type: ignore[index]
    assert binding.closed


def test_cook_real_claude_builder_receives_plugin_and_skills(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from autoskillit.execution.backends.claude import ClaudeCodeBackend

    captured = _install_harness(monkeypatch, tmp_path)
    cli.cook(backend=ClaudeCodeBackend())

    spec = captured["spec"]
    assert isinstance(spec, CmdSpec)
    assert "--plugin-dir" in spec.cmd
    plugin_index = spec.cmd.index("--plugin-dir")
    projected_plugin = Path(spec.cmd[plugin_index + 1])
    assert projected_plugin.is_dir()
    assert projected_plugin.parent.name == "plugin-projections"
    assert (projected_plugin / ".claude-plugin" / "plugin.json").is_file()
    assert "--add-dir" in spec.cmd
    assert str(captured["skills_dir"]) in spec.cmd
    assert "--dangerously-skip-permissions" in spec.cmd


def test_cook_bare_resume_recovers_then_uses_picker(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    backend = _Backend()
    _install_harness(monkeypatch, tmp_path, picked_session="thread-123")

    cli.cook(backend=backend, resume=True)

    assert backend.recover_count == 1
    assert backend.build_calls[0]["resume_spec"] == NamedResume("thread-123")


def test_cook_bare_resume_without_selection_starts_fresh(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    backend = _Backend()
    _install_harness(monkeypatch, tmp_path)

    cli.cook(backend=backend, resume=True)

    assert backend.recover_count == 1
    assert backend.build_calls[0]["resume_spec"] == NoResume()


def test_cook_explicit_resume_does_not_run_recovery(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    backend = _Backend()
    _install_harness(monkeypatch, tmp_path)

    cli.cook(backend=backend, session_id="thread-explicit")

    assert backend.recover_count == 0
    assert backend.build_calls[0]["resume_spec"] == NamedResume("thread-explicit")


def test_cook_marks_onboarded_only_after_success(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    backend = _Backend()
    captured = _install_harness(
        monkeypatch,
        tmp_path,
        first_run=True,
        onboarding_prompt="start here",
    )

    cli.cook(backend=backend)

    assert backend.build_calls[0]["initial_prompt"] == "start here"
    event_names = [event[0] for event in captured["events"]]
    assert event_names.index("run") < event_names.index("onboarded")


def test_cook_does_not_mark_onboarded_without_prompt(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    backend = _Backend()
    captured = _install_harness(monkeypatch, tmp_path, first_run=True)

    cli.cook(backend=backend)

    assert not any(event[0] == "onboarded" for event in captured["events"])


def test_cook_nonzero_exit_propagates_after_managed_cleanup(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    backend = _Backend()
    captured = _install_harness(monkeypatch, tmp_path, returncode=7)

    with pytest.raises(SystemExit, match="7"):
        cli.cook(backend=backend)

    assert captured["events"][-1][0] == "managed-exit"


def test_cook_resolves_default_backend(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    backend = _Backend()
    _install_harness(monkeypatch, tmp_path)
    requested: list[str] = []
    monkeypatch.setattr(
        "autoskillit.cli.session._session_backend.resolve_global_backend",
        lambda name: requested.append(name) or backend,
    )

    cli.cook()

    assert requested
    assert backend.build_calls


def test_cook_missing_backend_binary_exits(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    backend = _Backend()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(shutil, "which", lambda _name, **_kwargs: None)

    with pytest.raises(SystemExit, match="1"):
        cli.cook(backend=backend)

    assert "not found" in capsys.readouterr().out


def test_cook_final_confirmation_precedes_registry_and_attempt(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A declined final prompt must not leave a registry row or enter an attempt."""
    from autoskillit.core import (
        CmdSpec,
        CookSessionHandle,
        HookTrustPolicy,
        ManagedSessionHome,
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
        catalog: EffectiveSkillCatalogAuthority,
        projection_context: SkillProjectionContextAuthority,
    ):
        assert projection_context.catalog == catalog
        events.append(("managed-enter", launch_id))
        try:
            yield ManagedSessionHome(
                launch_id=launch_id,
                generated_home=generated_home,
                skills_dir=ValidatedAddDir(str(skills_dir)),
                pass_fds=(),
            )
        finally:
            events.append(("managed-exit", launch_id))

    manager.managed_session.side_effect = managed_session

    class _Backend:
        name = "claude-code"
        conventions = BackendConventions()
        capabilities = SimpleNamespace(
            hook_trust_policy=HookTrustPolicy.AUTOMATED,
            session_dir_persistent=False,
            skill_injection_capable=True,
        )
        adapt_skill_semantics = staticmethod(adapt_test_skill_semantics)

        def binary_name(self) -> str:
            return "claude"

        def recover_cook_history(self) -> None:
            events.append(("recover",))

        def build_interactive_cmd(self, **kwargs: object) -> CmdSpec:
            events.append(("build",))
            return CmdSpec(cmd=("claude",), env={})

        def validate_interactive_invocation(self, spec: CmdSpec) -> list[str]:
            events.append(("validate", spec))
            return []

        @contextmanager
        def cook_session_context(self, **kwargs: object):
            events.append(("attempt-enter",))
            yield CookSessionHandle(
                view_id="view-1",
                pass_fds=(),
                _record_spawn=lambda _pid, _pgid: None,
                _record_reaped=lambda _pid, _pgid: None,
            )

    def run_once(answer: str) -> None:
        events.clear()
        monkeypatch.setattr(
            "autoskillit.cli.ui._timed_input.timed_prompt",
            lambda *args, **kwargs: events.append(("confirm", answer)) or answer,
        )
        monkeypatch.setattr(
            "autoskillit.core.write_registry_entry",
            lambda project, launch_id, session_type, claude_id: events.append(
                ("registry", launch_id)
            ),
        )
        monkeypatch.setattr(
            "autoskillit.cli.session._session_process.run_cook_attempt",
            lambda *args, **kwargs: (
                events.append(("run",)) or SimpleNamespace(pid=1, pgid=1, returncode=0)
            ),
        )
        monkeypatch.setattr(
            "autoskillit.cli.session._session_reload.consume_reload_sentinel",
            lambda _project: None,
        )
        cli.cook(backend=_Backend())

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(shutil, "which", lambda _name, **_kwargs: "/usr/bin/claude")
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("autoskillit.cli._onboarding.is_first_run", lambda _: False)
    monkeypatch.setattr(
        "autoskillit.workspace.DefaultSessionSkillManager", lambda *args, **kwargs: manager
    )

    run_once("n")
    assert [event[0] for event in events] == [
        "managed-enter",
        "confirm",
        "managed-exit",
    ]

    run_once("")
    names = [event[0] for event in events]
    assert "recover" not in names
    assert names.index("confirm") < names.index("registry")
    assert names.index("registry") < names.index("attempt-enter")
    launch_id = next(event[1] for event in events if event[0] == "managed-enter")
    assert next(event[1] for event in events if event[0] == "registry") == launch_id


def test_cook_does_not_treat_persistent_sessions_as_codex(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Persistent storage alone must not activate Codex runtime behavior."""
    from autoskillit.execution.backends import _codex_session_storage as storage

    backend = _Backend()
    backend.name = "persistent-non-codex"
    backend.conventions = BackendConventions(
        persistent_session_root_subdir=Path("persistent-non-codex")
    )
    backend.capabilities = SimpleNamespace(
        hook_trust_policy=HookTrustPolicy.AUTOMATED,
        session_dir_persistent=True,
        cook_startup_observer_capable=False,
        skill_injection_capable=True,
    )
    captured = _install_harness(monkeypatch, tmp_path)

    def fail_codex_runtime(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("persistent non-Codex backend entered Codex runtime")

    monkeypatch.setenv(CODEX_STARTUP_TRACE_ENV_VAR, "1")
    monkeypatch.setattr(
        "autoskillit.execution.CodexStateReadinessProbe",
        fail_codex_runtime,
    )
    monkeypatch.setattr(
        storage.CodexSessionStore,
        "prepare_attempt",
        fail_codex_runtime,
    )

    cli.cook(backend=backend)

    spec = captured["spec"]
    assert isinstance(spec, CmdSpec)
    assert CODEX_COOK_RESERVED_ENV_VARS.isdisjoint(spec.env)
    assert CODEX_STARTUP_TRACE_ENV_VAR not in spec.env
    assert backend.context_calls[0]["session_home"] == captured["generated_home"]
