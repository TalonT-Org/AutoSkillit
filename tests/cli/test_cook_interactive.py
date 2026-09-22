"""Tests for the cook CLI command (interactive skill session)."""

from __future__ import annotations

import json
import shutil
import tomllib
from collections.abc import Callable
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import autoskillit.cli.install._plugin_artifact as _patch_install__plugin_artifact
import autoskillit.cli.session._session_backend as _patch_session__session_backend
import autoskillit.cli.session._session_launch_intent as _patch_session__session_picker
import autoskillit.cli.session._session_onboarding as _patch_session__session_onboarding
import autoskillit.cli.session._session_process as _patch_session__session_process
import autoskillit.cli.session._session_reload as _patch_session__session_reload
import autoskillit.cli.ui._timed_input as _patch_ui__timed_input
from autoskillit import cli
from autoskillit.core import (
    CODEX_RESERVED_HOME_ENV_VARS,
    CODEX_STARTUP_TRACE_ENV_VAR,
    LAUNCH_ID_ENV_VAR,
    SESSION_TYPE_ENV_VAR,
    BackendConventions,
    CmdSpec,
    CompiledSessionSkillCatalogAuthority,
    FreshLaunch,
    HookTrustPolicy,
    InteractiveInvocationValidation,
    ManagedSessionHome,
    NamedResume,
    PreLaunchReadiness,
    RestoreSession,
    SessionAttemptHandle,
    SkillProjectionContextAuthority,
    SkillSemanticAdaptationResult,
    SkillSemanticPlan,
    ValidatedAddDir,
    atomic_write,
)
from tests.cli._interactive_process import interactive_launch_metadata
from tests.execution.backends._codex_fixtures import managed_selection_catalog
from tests.fakes import adapt_test_skill_semantics

pytestmark = [
    pytest.mark.layer("cli"),
    pytest.mark.medium,
    pytest.mark.usefixtures("_stub_owner_binding"),
]


class _CookBinding:
    def __init__(self, plugin_dir: Path) -> None:
        self.plugin_dir = plugin_dir
        self.identity = SimpleNamespace(managed_path=plugin_dir)
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
        if (
            getattr(backend, "name", None) == "codex"
            and kwargs["generated_home_available"] is True
        ):
            return authority, PluginLoadMode.GENERATED_HOME
        return authority, PluginLoadMode.EXPLICIT_PLUGIN_DIR

    monkeypatch.setattr(
        _patch_install__plugin_artifact,
        "interactive_plugin_authority",
        choose,
    )


class _Backend:
    name = "claude-code"
    conventions = BackendConventions()
    capabilities = SimpleNamespace(
        hook_trust_policy=HookTrustPolicy.AUTOMATED,
        session_dir_persistent=False,
        session_scoped_explorer_capable=True,
        terminal_explorer_capable=False,
        explicit_path_env_var="",
        cook_exact_binding_probe_required=False,
        skill_injection_capable=True,
        supports_tool_list_changed=True,
        managed_fixed_batch_route_capable=False,
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

    def interactive_ordering_flags(self) -> tuple[frozenset[str], frozenset[str]]:
        from autoskillit.execution.backends import ClaudeCodeBackend, CodexBackend

        backend = CodexBackend() if self.binary_name() == "codex" else ClaudeCodeBackend()
        return backend.interactive_ordering_flags()

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
            **interactive_launch_metadata(binary="claude", launch=kwargs["launch"]),
            inherited_fds=(
                *self.extra_inherited_fds,
                *getattr(plugin_binding, "inherited_fds", ()),
            ),
        )

    def validate_interactive_invocation(self, spec: CmdSpec) -> InteractiveInvocationValidation:
        self.validated.append(spec)
        return InteractiveInvocationValidation(errors=())

    @contextmanager
    def session_attempt_context(self, **kwargs: object):
        self.context_calls.append(kwargs)
        yield SessionAttemptHandle(
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
    from autoskillit.cli.install._installed_plugins import InstalledPluginsFile

    generated_home = tmp_path / "managed-home"
    skills_dir = generated_home / "skills"
    skills_dir.mkdir(parents=True)
    manager = MagicMock()
    events: list[tuple[object, ...]] = []
    claims: list[dict[str, object]] = []
    releases: list[str] = []
    captured: dict[str, object] = {
        "events": events,
        "manager": manager,
        "claims": claims,
        "releases": releases,
    }

    @contextmanager
    def managed_session(
        launch_id: str,
        compilation: CompiledSessionSkillCatalogAuthority,
        projection_context: SkillProjectionContextAuthority,
    ):
        events.append(("managed-enter", launch_id, compilation.catalog, projection_context))
        assert projection_context.catalog == compilation.catalog
        try:
            yield ManagedSessionHome(
                launch_id=launch_id,
                generated_home=generated_home,
                skills_dir=ValidatedAddDir(str(skills_dir)),
                pass_fds=(7,),
                unavailability_payload={"backend": None, "unavailable": ()},
            )
        finally:
            events.append(("managed-exit", launch_id))

    manager.managed_session.side_effect = managed_session

    claude_shim = tmp_path / "claude"
    atomic_write(
        claude_shim,
        "#!/bin/sh\n"
        'if [ "${1-}" = "--version" ]; then\n'
        "  printf '%s\\n' '2.1.220 (Claude Code)'\n"
        "fi\n"
        "exit 0\n",
    )
    claude_shim.chmod(0o755)

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
    monkeypatch.setattr(shutil, "which", lambda _name, **_kwargs: str(claude_shim))
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr(
        "autoskillit.workspace.DefaultSessionSkillManager",
        lambda *args, **kwargs: manager,
    )
    monkeypatch.setattr(InstalledPluginsFile, "contains", lambda self, key: False)
    monkeypatch.setattr(
        _patch_session__session_onboarding,
        "is_first_run",
        lambda _project: first_run,
    )
    monkeypatch.setattr(
        _patch_session__session_onboarding,
        "run_onboarding_menu",
        lambda *args, **kwargs: onboarding_prompt,
    )
    monkeypatch.setattr(
        _patch_session__session_onboarding,
        "mark_onboarded",
        lambda project: events.append(("onboarded", project)),
    )
    monkeypatch.setattr(
        _patch_ui__timed_input,
        "timed_prompt",
        lambda *args, **kwargs: confirm,
    )
    monkeypatch.setattr(
        "autoskillit.core.write_registry_entry",
        lambda project, launch_id, session_type, session_id: events.append(
            ("registry", launch_id)
        ),
    )

    def claim_session(project_dir: Path, **kwargs: object) -> str:
        claims.append({"project_dir": project_dir, **kwargs})
        events.append(("claim", kwargs["claude_session_id"]))
        return "0123456789abcdef"

    monkeypatch.setattr(
        "autoskillit.core.claim_launch_for_session",
        claim_session,
    )
    monkeypatch.setattr(
        "autoskillit.core.release_session_claim",
        lambda _project, launch_id: releases.append(launch_id),
    )
    monkeypatch.setattr(
        _patch_session__session_process,
        "run_cook_attempt",
        run_attempt,
    )
    monkeypatch.setattr(
        _patch_session__session_reload,
        "consume_reload_sentinel",
        lambda _project: None,
    )
    monkeypatch.setattr(
        _patch_session__session_picker,
        "pick_session",
        lambda *args, **kwargs: picked_session,
    )
    captured["generated_home"] = generated_home
    captured["skills_dir"] = skills_dir
    return captured


def test_codex_cook_adds_pre_reveal_developer_guidance(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from autoskillit.execution.backends.codex import CodexBackend, CodexFlags

    class _DelegatingCodexBackend(_Backend):
        name = "codex"
        capabilities = CodexBackend.capabilities

        def __init__(self) -> None:
            super().__init__()
            self._command_backend = CodexBackend()

        def binary_name(self) -> str:
            return "codex"

        def adapt_skill_semantics(
            self,
            plan: SkillSemanticPlan,
            adaptation_context=None,
        ) -> SkillSemanticAdaptationResult:
            return self._command_backend.adapt_skill_semantics(plan, adaptation_context)

        def ensure_pre_launch(self, **_kwargs: object) -> PreLaunchReadiness:
            return PreLaunchReadiness((), {})

        def build_interactive_cmd(self, **kwargs: object) -> CmdSpec:
            self.build_calls.append(kwargs)
            return self._command_backend.build_interactive_cmd(**kwargs)  # type: ignore[arg-type]

        def interactive_ordering_flags(self) -> tuple[frozenset[str], frozenset[str]]:
            return self._command_backend.interactive_ordering_flags()

    backend = _DelegatingCodexBackend()
    captured = _install_harness(monkeypatch, tmp_path)

    cli.cook(backend=backend)

    spec = captured["spec"]
    assert isinstance(spec, CmdSpec)
    overrides = (
        spec.cmd[index + 1]
        for index, value in enumerate(spec.cmd[:-1])
        if value == CodexFlags.CONFIG_OVERRIDE
    )
    rendered = next(
        value.removeprefix("developer_instructions=")
        for value in overrides
        if value.startswith("developer_instructions=")
    )
    guidance = tomllib.loads(f"developer_instructions = {rendered}")["developer_instructions"]
    assert "kitchen tools are already active" in guidance
    assert "no arguments solely to gain tool access" in guidance
    assert "explicitly requests activation or promotion" in guidance
    assert "open_kitchen(name=...)" in guidance
    assert "after close_kitchen()" in guidance
    assert "$<name>" in guidance and "/<name>" in guidance
    assert "skill name" in guidance and "recipe identities only" in guidance
    assert "defined as both" in guidance and "rejected" in guidance
    assert len(backend.build_calls) == 2
    assert backend.build_calls[0].get("executable") is None
    assert backend.build_calls[1]["executable"] is not None
    assert spec.managed_skill_catalog is backend.build_calls[-1]["add_dirs"][0]


def test_cook_aborts_before_spawn_when_skill_discovery_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Discovery diagnostics must reject the managed launch before the child starts."""
    diagnostic = "Codex skill discovery is missing managed names ['expected-skill']"

    class _DiscoveryFailureBackend(_Backend):
        name = "codex"

        def validate_interactive_invocation(
            self, _spec: CmdSpec
        ) -> InteractiveInvocationValidation:
            return InteractiveInvocationValidation(errors=(diagnostic,))

    backend = _DiscoveryFailureBackend()
    captured = _install_harness(monkeypatch, tmp_path)

    with pytest.raises(RuntimeError, match="Codex skill discovery is missing managed names"):
        cli.cook(backend=backend)

    events = captured["events"]
    assert isinstance(events, list)
    event_names = [event[0] for event in events]
    assert "run" not in event_names
    assert event_names[-1] == "managed-exit"


def test_codex_cook_admits_compose_pr_roles_from_exact_bundled_catalog_probe(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from autoskillit.core import SkillExecutionRole
    from autoskillit.execution.backends.codex import CodexBackend
    from autoskillit.workspace import DefaultSkillResolver

    project_root = tmp_path / "project"
    project_root.mkdir()
    source_home = tmp_path / "home" / ".codex"
    source_home.mkdir(parents=True)
    (source_home / "config.toml").write_text(
        'cli_auth_credentials_store = "keyring"\n',
        encoding="utf-8",
    )
    (source_home / "auth.json").write_text("{}\n", encoding="utf-8")
    backend = CodexBackend(source_codex_home=source_home)
    catalog = DefaultSkillResolver().list_effective(
        project_root,
        SkillExecutionRole.SESSION,
        cook_session=True,
    )
    compose_pr = next(member for member in catalog.skills if member.name == "compose-pr")
    assert compose_pr.semantic_plan is not None
    mapped_targets = {spawn.role for spawn in compose_pr.semantic_plan.child_spawns}
    captured: dict[str, object] = {}

    @contextmanager
    def session_attempt_context(_self, **_kwargs: object):
        yield SessionAttemptHandle(
            view_id="codex-view",
            pass_fds=(),
            _record_spawn=lambda _pid, _pgid: None,
            _record_reaped=lambda _pid, _pgid: None,
        )

    def run_attempt(spec: CmdSpec, **kwargs: object) -> object:
        generated_home = Path(spec.env["CODEX_HOME"])
        compose_projection = generated_home / "add-dir" / "skills" / "compose-pr" / "SKILL.md"
        captured["compose_projected"] = compose_projection.is_file()
        captured["source_cache_exists"] = (source_home / "models_cache.json").exists()
        captured["mapped_targets"] = mapped_targets
        kwargs["on_spawn"](101, 101)  # type: ignore[operator]
        kwargs["trace"].record_spawn()  # type: ignore[union-attr]
        kwargs["on_reaped"](101, 101)  # type: ignore[operator]
        return SimpleNamespace(pid=101, pgid=101, returncode=0)

    bundled_catalog = tmp_path / "bundled-models.json"
    atomic_write(bundled_catalog, json.dumps(managed_selection_catalog()))
    codex_shim = tmp_path / "codex"
    atomic_write(
        codex_shim,
        "#!/usr/bin/env python3\n"
        "import pathlib\n"
        "import sys\n"
        "if sys.argv[1:] != ['debug', 'models', '--bundled']:\n"
        "    raise SystemExit(64)\n"
        f"sys.stdout.buffer.write(pathlib.Path({str(bundled_catalog)!r}).read_bytes())\n",
    )
    codex_shim.chmod(0o755)

    monkeypatch.chdir(project_root)
    monkeypatch.setenv("MCP_CLIENT_BACKEND", "pre-test-backend")
    monkeypatch.setattr(shutil, "which", lambda _name, **_kwargs: str(codex_shim))
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr(_patch_session__session_onboarding, "is_first_run", lambda _project: False)
    monkeypatch.setattr(
        _patch_ui__timed_input,
        "timed_prompt",
        lambda *args, **kwargs: "",
    )
    monkeypatch.setattr("autoskillit.core.write_registry_entry", lambda *args: None)
    monkeypatch.setattr(
        _patch_session__session_process,
        "run_cook_attempt",
        run_attempt,
    )
    monkeypatch.setattr(
        _patch_session__session_reload,
        "consume_reload_sentinel",
        lambda _project: None,
    )
    monkeypatch.setattr(CodexBackend, "session_attempt_context", session_attempt_context)

    original_ensure_pre_launch = CodexBackend.ensure_pre_launch

    def ensure_pre_launch(  # type: ignore[no-untyped-def]
        self,
        *,
        session_dir=None,
        executable=None,
        plugin_dir=None,
    ):
        if session_dir is None or executable is not None:
            return PreLaunchReadiness((), {})
        return original_ensure_pre_launch(
            self,
            session_dir=session_dir,
            executable=executable,
            plugin_dir=plugin_dir,
        )

    monkeypatch.setattr(
        CodexBackend,
        "ensure_pre_launch",
        ensure_pre_launch,
    )
    monkeypatch.setattr(
        CodexBackend,
        "validate_interactive_invocation",
        lambda _self, _spec: InteractiveInvocationValidation(errors=()),
    )

    cli.cook(backend=backend)

    assert captured["compose_projected"] is True
    assert captured["source_cache_exists"] is False
    assert not (source_home / "models_cache.json").exists()
    assert captured["mapped_targets"] == {"pr-source-reader", "pr-synthesizer"}


def test_cook_captures_managed_preparation_refusal_once(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from autoskillit.server import managed_join_prelaunch

    backend = _Backend()
    capabilities = vars(backend.capabilities) | {"managed_fixed_batch_route_capable": True}
    backend.capabilities = SimpleNamespace(**capabilities)
    _install_harness(monkeypatch, tmp_path)

    def refuse(**kwargs: object) -> None:
        on_refusal = kwargs["on_refusal"]
        assert callable(on_refusal)
        on_refusal(managed_join_prelaunch.ManagedJoinIssuanceRefusal("catalog_probe_failed"))
        return None

    monkeypatch.setattr(managed_join_prelaunch, "acquire_managed_join_evidence", refuse)

    cli.cook(backend=backend)

    output = capsys.readouterr().out
    assert output.count("WARNING: managed join issuance refused: catalog_probe_failed") == 1


def test_notification_capable_cook_has_no_pre_reveal_guidance(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    backend = _Backend()
    _install_harness(monkeypatch, tmp_path)

    cli.cook(backend=backend)

    assert backend.build_calls[0]["launch"] == FreshLaunch()


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
        _patch_install__plugin_artifact,
        "interactive_plugin_authority",
        lambda **_kwargs: (authority, PluginLoadMode.EXPLICIT_PLUGIN_DIR),
    )

    cli.cook(backend=backend)

    generated_home = captured["generated_home"]
    skills_dir = captured["skills_dir"]
    build = backend.build_calls[0]
    assert build["generated_home"] == generated_home
    assert build["add_dirs"] == [ValidatedAddDir(str(skills_dir))]
    assert build["launch"] == FreshLaunch()
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


def test_cook_retains_projection_binding_when_launch_consumes_no_artifact(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from autoskillit.core import PluginLoadMode

    backend = _Backend()
    captured = _install_harness(monkeypatch, tmp_path)
    binding = _CookBinding(tmp_path / "projected-plugin")
    authority = _CookAuthority(binding.plugin_dir)

    def acquire_binding(**kwargs: object) -> _CookBinding:
        assert kwargs["load_mode"] is PluginLoadMode.PROJECTED_HOME
        return binding

    authority.acquire_launch_binding = acquire_binding  # type: ignore[method-assign]
    monkeypatch.setattr(
        _patch_install__plugin_artifact,
        "interactive_plugin_authority",
        lambda **_kwargs: (authority, PluginLoadMode.NONE),
    )

    cli.cook(backend=backend)

    managed_enter = next(
        event
        for event in captured["events"]
        if event[0] == "managed-enter"  # type: ignore[union-attr]
    )
    projection_context = managed_enter[3]
    expected_scripts = str(binding.plugin_dir / "recipes" / "scripts")
    assert projection_context.substitutions["{{AUTOSKILLIT_SCRIPTS}}"] == expected_scripts
    assert backend.build_calls[0]["plugin_binding"] is None
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
    assert backend.build_calls[0]["launch"] == RestoreSession("thread-123")


def test_cook_bare_resume_without_selection_starts_fresh(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    backend = _Backend()
    captured = _install_harness(
        monkeypatch,
        tmp_path,
        first_run=True,
        onboarding_prompt="start here",
    )

    cli.cook(backend=backend, resume=True)

    assert backend.recover_count == 1
    assert backend.build_calls[0]["launch"] == FreshLaunch(initial_prompt="start here")
    assert any(event[0] == "onboarded" for event in captured["events"])


def test_cook_explicit_resume_runs_recovery_without_picker_or_confirmation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    backend = _Backend()
    _install_harness(monkeypatch, tmp_path)
    sweep = MagicMock()
    picker = MagicMock(side_effect=AssertionError("named resume must not open the picker"))
    prompt = MagicMock(side_effect=AssertionError("resume must not ask for confirmation"))
    monkeypatch.setattr(_patch_session__session_picker, "sweep_orphaned_tethers", sweep)
    monkeypatch.setattr(_patch_session__session_picker, "pick_session", picker)
    monkeypatch.setattr(_patch_ui__timed_input, "timed_prompt", prompt)

    cli.cook(backend=backend, session_id="thread-explicit")

    from autoskillit.execution import default_tether_dir

    sweep.assert_called_once_with(default_tether_dir())
    assert backend.recover_count == 1
    picker.assert_not_called()
    prompt.assert_not_called()
    assert backend.build_calls[0]["launch"] == RestoreSession("thread-explicit")


def test_cook_resume_reuses_claimed_launch_identity_everywhere(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from autoskillit.core.runtime.session_registry import (
        claim_launch_for_session,
        read_registry,
        release_session_claim,
        write_registry_entry,
    )

    backend = _Backend()
    captured = _install_harness(monkeypatch, tmp_path)
    thread_id = "7c1b6dc2-02c2-47b8-a3af-77b399278c3b"
    launch_id = "0123456789abcdef"
    write_registry_entry(
        tmp_path,
        launch_id,
        "cook",
        None,
        claude_session_id=thread_id,
    )
    assert release_session_claim(tmp_path, launch_id)
    claims = captured["claims"]
    releases = captured["releases"]
    events = captured["events"]
    assert isinstance(claims, list)
    assert isinstance(releases, list)
    assert isinstance(events, list)

    def real_claim(project_dir: Path, **kwargs: object) -> str:
        claims.append({"project_dir": project_dir, **kwargs})
        events.append(("claim", kwargs["claude_session_id"]))
        return claim_launch_for_session(project_dir, **kwargs)  # type: ignore[arg-type]

    def real_release(project_dir: Path, claimed_launch_id: str) -> bool:
        releases.append(claimed_launch_id)
        return release_session_claim(project_dir, claimed_launch_id)

    monkeypatch.setattr("autoskillit.core.write_registry_entry", write_registry_entry)
    monkeypatch.setattr("autoskillit.core.claim_launch_for_session", real_claim)
    monkeypatch.setattr("autoskillit.core.release_session_claim", real_release)

    cli.cook(backend=backend, session_id=thread_id)

    assert claims == [
        {
            "project_dir": tmp_path,
            "claude_session_id": thread_id,
            "session_type": "cook",
            "recipe_name": None,
        }
    ]
    managed_enter = next(event for event in events if event[0] == "managed-enter")
    assert managed_enter[1] == launch_id
    assert [event[0] for event in events].index("claim") < [event[0] for event in events].index(
        "managed-enter"
    )
    spec = captured["spec"]
    assert isinstance(spec, CmdSpec)
    assert spec.env[LAUNCH_ID_ENV_VAR] == launch_id
    assert backend.context_calls
    for context_call in backend.context_calls:
        assert context_call["launch_id"] == launch_id
        current_resume_spec = context_call["current_resume_spec"]
        assert isinstance(current_resume_spec, NamedResume)
        assert current_resume_spec.session_id == thread_id
    assert releases == [launch_id]
    registry = read_registry(tmp_path)
    assert list(registry) == [launch_id]
    assert registry[launch_id]["claude_session_id"] == thread_id
    assert "claimant_pid" not in registry[launch_id]


@pytest.mark.parametrize(
    ("binding_outcome", "error_match"),
    [
        pytest.param(False, "session owner binding refused", id="refused"),
        pytest.param(RuntimeError("registry unavailable"), "registry unavailable", id="raised"),
    ],
)
def test_cook_aborts_when_managed_owner_binding_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    _stub_owner_binding: Callable[[bool | BaseException], None],
    binding_outcome: bool | RuntimeError,
    error_match: str,
) -> None:
    backend = _Backend()
    captured = _install_harness(monkeypatch, tmp_path)
    _stub_owner_binding(binding_outcome)

    with pytest.raises(RuntimeError, match=error_match):
        cli.cook(backend=backend)

    events = captured["events"]
    assert isinstance(events, list)
    managed_launch_id = next(event[1] for event in events if event[0] == "managed-enter")
    assert events[-1] == ("managed-exit", managed_launch_id)
    releases = captured["releases"]
    assert releases == [managed_launch_id]


def test_cook_fresh_non_interactive_launches_without_confirmation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    backend = _Backend()
    _install_harness(monkeypatch, tmp_path)
    prompt = MagicMock(side_effect=AssertionError("non-interactive cook must not prompt"))
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    monkeypatch.setattr(_patch_ui__timed_input, "timed_prompt", prompt)

    cli.cook(backend=backend)

    prompt.assert_not_called()
    assert backend.build_calls[0]["launch"] == FreshLaunch()


@pytest.mark.parametrize("backend_name", ["claude-code", "codex"])
def test_cook_native_model_restoration_receives_no_cli_override(
    backend_name: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Both native-restoring backends own model restoration on resume."""
    backend = _Backend()
    backend.name = backend_name
    monkeypatch.setattr(
        backend,
        "binary_name",
        lambda: "codex" if backend_name == "codex" else "claude",
    )
    _install_harness(monkeypatch, tmp_path)

    cli.cook(backend=backend, session_id="thread-explicit")

    assert backend.build_calls[0].get("model") is None


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

    assert backend.build_calls[0]["launch"] == FreshLaunch(initial_prompt="start here")
    event_names = [event[0] for event in captured["events"]]
    assert event_names.index("run") < event_names.index("onboarded")


def test_cook_does_not_mark_onboarded_without_prompt(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    backend = _Backend()
    captured = _install_harness(monkeypatch, tmp_path, first_run=True)

    cli.cook(backend=backend)

    assert not any(event[0] == "onboarded" for event in captured["events"])


def test_cook_explicit_resume_skips_onboarding(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    backend = _Backend()
    captured = _install_harness(
        monkeypatch,
        tmp_path,
        first_run=True,
        onboarding_prompt="start here",
    )

    cli.cook(backend=backend, session_id="thread-explicit")

    assert not any(event[0] == "onboarded" for event in captured["events"])
    assert backend.build_calls[0]["launch"] == RestoreSession("thread-explicit")


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
        _patch_session__session_backend,
        "resolve_global_backend",
        lambda name, **_kwargs: requested.append(name) or backend,
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
        HookTrustPolicy,
        ManagedSessionHome,
        SessionAttemptHandle,
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
        events.append(("managed-enter", launch_id))
        try:
            yield ManagedSessionHome(
                launch_id=launch_id,
                generated_home=generated_home,
                skills_dir=ValidatedAddDir(str(skills_dir)),
                pass_fds=(),
                unavailability_payload={"backend": None, "unavailable": ()},
            )
        finally:
            events.append(("managed-exit", launch_id))

    manager.managed_session.side_effect = managed_session
    claude_shim = tmp_path / "claude"
    atomic_write(claude_shim, "#!/bin/sh\nexit 0\n")
    claude_shim.chmod(0o755)

    class _Backend:
        name = "claude-code"
        conventions = BackendConventions()
        capabilities = SimpleNamespace(
            hook_trust_policy=HookTrustPolicy.AUTOMATED,
            session_dir_persistent=False,
            session_scoped_explorer_capable=True,
            terminal_explorer_capable=False,
            explicit_path_env_var="",
            cook_exact_binding_probe_required=False,
            skill_injection_capable=True,
            supports_tool_list_changed=True,
            managed_fixed_batch_route_capable=False,
        )
        adapt_skill_semantics = staticmethod(adapt_test_skill_semantics)

        def binary_name(self) -> str:
            return "claude"

        def recover_cook_history(self) -> None:
            events.append(("recover",))

        def build_interactive_cmd(self, **kwargs: object) -> CmdSpec:
            events.append(("build",))
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
            events.append(("validate", spec))
            return InteractiveInvocationValidation(errors=())

        @contextmanager
        def session_attempt_context(self, **kwargs: object):
            events.append(("attempt-enter",))
            yield SessionAttemptHandle(
                view_id="view-1",
                pass_fds=(),
                _record_spawn=lambda _pid, _pgid: None,
                _record_reaped=lambda _pid, _pgid: None,
            )

    def run_once(answer: str) -> None:
        events.clear()
        monkeypatch.setattr(
            _patch_ui__timed_input,
            "timed_prompt",
            lambda *args, **kwargs: events.append(("confirm", answer)) or answer,
        )
        monkeypatch.setattr(
            "autoskillit.core.write_registry_entry",
            lambda project, launch_id, session_type, claude_id: events.append(
                ("registry", launch_id)
            ),
        )
        monkeypatch.setattr(
            _patch_session__session_process,
            "run_cook_attempt",
            lambda *args, **kwargs: (
                events.append(("run",)) or SimpleNamespace(pid=1, pgid=1, returncode=0)
            ),
        )
        monkeypatch.setattr(
            _patch_session__session_reload,
            "consume_reload_sentinel",
            lambda _project: None,
        )
        cli.cook(backend=_Backend())

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(shutil, "which", lambda _name, **_kwargs: str(claude_shim))
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr(_patch_session__session_onboarding, "is_first_run", lambda _: False)
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
        session_scoped_explorer_capable=False,
        terminal_explorer_capable=False,
        explicit_path_env_var="",
        cook_startup_observer_capable=False,
        cook_exact_binding_probe_required=False,
        skill_injection_capable=True,
        supports_tool_list_changed=True,
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
    assert CODEX_RESERVED_HOME_ENV_VARS.isdisjoint(spec.env)
    assert CODEX_STARTUP_TRACE_ENV_VAR not in spec.env
    assert backend.context_calls[0]["session_home"] == captured["generated_home"]
