"""Shared interactive session launch prelude for CLI commands."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, NoReturn, assert_never, overload

from autoskillit.cli.session._session_backend import verify_launch_home
from autoskillit.core import (
    AUTOSKILLIT_STATE_ROOT_ENV_VAR,
    CODEX_AUTO_COMPACTION_BLOCKED_MESSAGE,
    LAUNCH_ID_ENV_VAR,
    FreshLaunch,
    InfraExitCategory,
    InteractiveLaunch,
    NamedResume,
    NoResume,
    RestoreSession,
    ResumeWithBriefing,
    SkillContractError,
    executable_binding_matches_current_file,
    get_logger,
    plugin_launch_binding_scope,
    resolve_executable_launch_binding,
    resolve_temp_dir,
    temp_dir_display_str,
)

if TYPE_CHECKING:
    from autoskillit.cli.session._session_startup_trace import StartupTrace
    from autoskillit.core import (
        CmdSpec,
        CodingAgentBackend,
        ExecutableLaunchBinding,
        ManagedSessionHome,
        PluginLaunchBinding,
        ResumeSpec,
        SemanticAdaptationContext,
        SkillUnavailabilityPayload,
        ValidatedAddDir,
    )
    from autoskillit.workspace import (
        CompiledSessionSkillCatalog,
        SkillExclusion,
    )

logger = get_logger(__name__)


def render_skill_contract_composition_failure(exc: SkillContractError) -> None:
    """Print a clean, actionable message for a composition-root contract failure.

    No traceback. The underlying message is already actionable (file path,
    invalidity kind's hint, and — for the tier-role gate — an embedded
    doctor pointer) after resolution-boundary containment; this guarantees
    the doctor pointer is present for every SkillContractError shape, not
    only the ones that already embed it.
    """
    print(f"ERROR: {exc}")
    print("Run: autoskillit doctor")


def render_skill_catalog_exclusions(exclusions: tuple[SkillExclusion, ...]) -> None:
    """Print one operator-visible warning line per excluded skill candidate."""
    for exclusion in exclusions:
        hint = "; ".join(exclusion.hints)
        print(f"WARNING: excluded project-local skill {exclusion.name!r} at {exclusion.path}")
        if hint:
            print(f"  hint: {hint}")


def render_skill_unavailability(
    unavailability_payload: SkillUnavailabilityPayload,
) -> None:
    """Print backend exclusions grouped by operation and diagnostic."""
    grouped: dict[tuple[str, str], list[str]] = {}
    for item in unavailability_payload["unavailable"]:
        grouped.setdefault((item["operation"], item["diagnostic"]), []).append(item["skill"])
    for (operation, diagnostic), skill_names in sorted(grouped.items()):
        names = ", ".join(sorted(skill_names))
        print(
            f"{len(skill_names)} skills unavailable on this backend "
            f"({operation}: {diagnostic}): {names}"
        )


@overload
def append_skill_unavailability(
    system_prompt: None,
    unavailability_payload: SkillUnavailabilityPayload,
) -> None: ...


@overload
def append_skill_unavailability(
    system_prompt: str,
    unavailability_payload: SkillUnavailabilityPayload,
) -> str: ...


def append_skill_unavailability(
    system_prompt: str | None,
    unavailability_payload: SkillUnavailabilityPayload,
) -> str | None:
    """Append the canonical machine-readable refusal payload to a prompt."""
    if system_prompt is None or not unavailability_payload["unavailable"]:
        return system_prompt
    unavailable_json = json.dumps(
        unavailability_payload,
        sort_keys=True,
        separators=(",", ":"),
    )
    block = (
        f"<autoskillit_skill_unavailability>{unavailable_json}</autoskillit_skill_unavailability>"
    )
    if block in system_prompt:
        return system_prompt
    return f"{system_prompt.rstrip()}\n\n{block}"


@dataclass(frozen=True, slots=True)
class _InfraExitSignal:
    session_id: str
    category: str


@dataclass(frozen=True, slots=True)
class PreparedInteractiveLaunch:
    """Capability-complete command and exact executable binding for one launch."""

    spec: CmdSpec
    executable: ExecutableLaunchBinding
    pre_spawn_check: Callable[[], None] | None = None


def prepare_interactive_launch(
    backend: CodingAgentBackend,
    *,
    project_dir: Path,
    extra_env: Mapping[str, str] | None,
    required_env: frozenset[str] | None,
    plugin_binding: PluginLaunchBinding | None,
    launch: InteractiveLaunch,
    tools: Sequence[str] = (),
    add_dirs: Sequence[Path | str | ValidatedAddDir] = (),
    managed_home: ManagedSessionHome | None = None,
    force_inactive_agent_teams: bool = False,
    mcp_tool_timeout_sec: float | None = None,
) -> PreparedInteractiveLaunch:
    """Probe an exact executable before sealing the capability-complete session env."""

    probe_env = dict(os.environ)
    if extra_env is not None and "PATH" in extra_env:
        probe_env["PATH"] = extra_env["PATH"]
    selector = backend.capabilities.explicit_path_env_var
    if selector and extra_env is not None and selector in extra_env:
        probe_env[selector] = extra_env[selector]
    explicit_path_env = selector if selector and selector in probe_env else None

    provisional = resolve_executable_launch_binding(
        binary_name=backend.binary_name(),
        environment=probe_env,
        cwd=project_dir,
        explicit_path_env=explicit_path_env,
    )
    merged_extras = dict(extra_env or {})
    if managed_home is not None:
        readiness = backend.probe_launch_readiness(
            session_dir=managed_home.generated_home, executable=provisional
        )
    else:
        readiness = backend.ensure_pre_launch(executable=provisional)
    if readiness.errors:
        raise ValueError("\n".join(readiness.errors))
    merged_extras.update(readiness.attested_env)
    generated_home = managed_home.generated_home if managed_home is not None else None
    env_spec = backend.build_interactive_cmd(
        launch=launch,
        env_extras=merged_extras,
        required_env=required_env,
        plugin_binding=plugin_binding,
        add_dirs=add_dirs,
        generated_home=generated_home,
        tools=tools,
        force_inactive_agent_teams=force_inactive_agent_teams,
        project_root=project_dir,
        mcp_tool_timeout_sec=mcp_tool_timeout_sec,
    )
    final = resolve_executable_launch_binding(
        binary_name=backend.binary_name(),
        environment=env_spec.env,
        cwd=project_dir,
        explicit_path_env=explicit_path_env,
    )
    if backend.capabilities.cook_exact_binding_probe_required and final != provisional:
        raise ValueError(
            "interactive executable identity changed between probe and launch preparation"
        )
    spec = backend.build_interactive_cmd(
        launch=launch,
        executable=final,
        env_extras=merged_extras,
        required_env=required_env,
        plugin_binding=plugin_binding,
        add_dirs=add_dirs,
        generated_home=generated_home,
        tools=tools,
        force_inactive_agent_teams=force_inactive_agent_teams,
        project_root=project_dir,
        mcp_tool_timeout_sec=mcp_tool_timeout_sec,
    )
    verify_launch_home(backend, managed_home)
    return PreparedInteractiveLaunch(spec=spec, executable=final)


def _finalize_interactive_launch(
    backend: CodingAgentBackend,
    *,
    exact_binding_probe_required: bool,
    project_dir: Path,
    extra_env: Mapping[str, str] | None,
    required_env: frozenset[str] | None,
    plugin_binding: PluginLaunchBinding | None,
    launch: InteractiveLaunch,
    tools: Sequence[str] = (),
    add_dirs: Sequence[Path | str | ValidatedAddDir] = (),
    managed_home: ManagedSessionHome | None = None,
    force_inactive_agent_teams: bool = False,
    mcp_tool_timeout_sec: float | None = None,
) -> PreparedInteractiveLaunch:
    """Build and validate the finalized invocation with its exact executable binding."""
    from autoskillit.execution import assert_interactive_ordering, assert_resume_purity

    if exact_binding_probe_required:
        try:
            prepared = prepare_interactive_launch(
                backend,
                project_dir=project_dir,
                extra_env=extra_env,
                required_env=required_env,
                plugin_binding=plugin_binding,
                launch=launch,
                add_dirs=add_dirs,
                managed_home=managed_home,
                tools=tools,
                force_inactive_agent_teams=force_inactive_agent_teams,
                mcp_tool_timeout_sec=mcp_tool_timeout_sec,
            )
        except ValueError as exc:
            _exit_launch_preparation_error(exc)
        built_spec = prepared.spec
        executable = prepared.executable
    else:
        candidate_spec = backend.build_interactive_cmd(
            launch=launch,
            env_extras=extra_env,
            required_env=required_env,
            plugin_binding=plugin_binding,
            add_dirs=add_dirs,
            generated_home=managed_home.generated_home if managed_home else None,
            tools=tools,
            force_inactive_agent_teams=force_inactive_agent_teams,
            project_root=project_dir,
            mcp_tool_timeout_sec=mcp_tool_timeout_sec,
        )
        selector = backend.capabilities.explicit_path_env_var
        try:
            executable = resolve_executable_launch_binding(
                binary_name=backend.binary_name(),
                environment=candidate_spec.env,
                cwd=project_dir,
                explicit_path_env=(selector if selector in candidate_spec.env else None),
            )
        except ValueError as exc:
            _exit_launch_preparation_error(exc)
        built_spec = backend.build_interactive_cmd(
            launch=launch,
            executable=executable,
            env_extras=extra_env,
            required_env=required_env,
            plugin_binding=plugin_binding,
            add_dirs=add_dirs,
            generated_home=managed_home.generated_home if managed_home else None,
            tools=tools,
            force_inactive_agent_teams=force_inactive_agent_teams,
            project_root=project_dir,
            mcp_tool_timeout_sec=mcp_tool_timeout_sec,
        )
        try:
            verify_launch_home(backend, managed_home)
        except ValueError as exc:
            _exit_launch_preparation_error(exc)
    spec = replace(built_spec, cwd=str(project_dir))
    variadic_flags, value_bearing_flags = backend.interactive_ordering_flags()
    assert_interactive_ordering(
        spec=spec,
        variadic_flags=variadic_flags,
        value_bearing_flags=value_bearing_flags,
    )
    assert_resume_purity(spec=spec, launch=launch)
    validation = backend.validate_interactive_invocation(spec)
    if validation.errors:
        _exit_launch_validation_errors(validation.errors)

    return PreparedInteractiveLaunch(
        spec=spec,
        executable=executable,
        pre_spawn_check=validation.pre_spawn_check,
    )


def _exit_launch_preparation_error(exc: ValueError) -> NoReturn:
    for line in str(exc).splitlines() or [str(exc)]:
        sys.stderr.write(f"ERROR: {line}\n")
    raise SystemExit(1)


def _exit_launch_validation_errors(errors: Sequence[str]) -> NoReturn:
    for error in errors:
        sys.stderr.write(f"ERROR: {error}\n")
    raise SystemExit(1)


def _run_interactive_session(
    launch: InteractiveLaunch,
    *,
    extra_env: dict[str, str] | None = None,
    project_dir: Path | None = None,
    required_env: frozenset[str] | None = None,
    backend: CodingAgentBackend | None = None,
    skill_compilation: CompiledSessionSkillCatalog | None = None,
    default_base_branch: str = "main",
    managed_home: ManagedSessionHome | None = None,
    plugin_binding: PluginLaunchBinding | None = None,
    retained_projection_binding: PluginLaunchBinding | None = None,
    startup_trace: StartupTrace | None = None,
    attempt: int | None = None,
    force_inactive_agent_teams: bool = False,
    mcp_tool_timeout_sec: float | None = None,
    cook_ceiling_seconds: float | None = None,
    systemd_scope_enabled: bool | None = None,
) -> str | _InfraExitSignal | None:
    """Launch an interactive Claude Code session.

    Returns:
        str — session_id when a reload sentinel is found
        _InfraExitSignal — when an infrastructure exit is detected
        None — clean exit
    """
    from autoskillit.execution import read_session_state

    if backend is None:
        from autoskillit.cli.session._session_backend import resolve_global_backend
        from autoskillit.config import load_config

        config = load_config()
        backend = resolve_global_backend(
            config.agent_backend.backend,
            codex_runtime_spec=config.codex_runtime.resolve(),
        )
        configured_base_branch = config.branching.default_base_branch
        if isinstance(configured_base_branch, str):
            default_base_branch = configured_base_branch
        if mcp_tool_timeout_sec is None:
            mcp_tool_timeout_sec = config.run_skill.mcp_tool_timeout_sec
        if cook_ceiling_seconds is None:
            cook_ceiling_seconds = config.process_tether.cook_ceiling_seconds
        if systemd_scope_enabled is None:
            systemd_scope_enabled = config.process_tether.systemd_scope_enabled
    if cook_ceiling_seconds is None:
        # backend was pre-resolved by the caller without also supplying this —
        # fall back to ProcessTetherConfig's own literal default rather than
        # leaving run_cook_attempt's required not_after unresolvable.
        cook_ceiling_seconds = 172800.0
    if systemd_scope_enabled is None:
        systemd_scope_enabled = False

    from autoskillit.cli.session._session_reload import consume_reload_sentinel
    from autoskillit.core import bind_session_owner

    managed = managed_home is not None
    if managed != (attempt is not None):
        raise ValueError("managed home and attempt must be supplied together")
    if managed and (attempt is None or attempt <= 0):
        raise ValueError("managed attempt must be positive")
    if managed and skill_compilation is None:
        raise ValueError("managed home requires its retained skill compilation")
    if managed and retained_projection_binding is None:
        raise ValueError("managed home requires a retained projection binding")
    if managed and startup_trace is None:
        raise ValueError("managed home requires a launch-scoped startup trace")
    if not managed and any(
        value is not None for value in (plugin_binding, retained_projection_binding, startup_trace)
    ):
        raise ValueError("managed launch inputs are invalid for a raw session")

    _project_dir = (project_dir if project_dir is not None else Path.cwd()).resolve()
    # Injected so PreToolUse guards can locate `.autoskillit/` state from the
    # orchestrating project root even when a command's own cwd points into a
    # sibling worktree with its own checked-in `.autoskillit/` — the production
    # signal resolve_state_root() (hooks/_hook_payload.py) checks first.
    extra_env = {**(extra_env or {}), AUTOSKILLIT_STATE_ROOT_ENV_VAR: str(_project_dir)}
    tools_arg: tuple[str, ...] = (
        ("AskUserQuestion",) if backend.capabilities.skill_injection_capable else ()
    )

    match launch:
        case FreshLaunch():
            current_resume_spec: ResumeSpec = NoResume()
        case RestoreSession(session_id=session_id) | ResumeWithBriefing(session_id=session_id):
            current_resume_spec = NamedResume(session_id=session_id)
        case _:
            assert_never(launch)
    if managed:
        assert managed_home is not None
        assert attempt is not None
        assert retained_projection_binding is not None
        assert startup_trace is not None
        prepared = _finalize_interactive_launch(
            backend,
            exact_binding_probe_required=backend.capabilities.cook_exact_binding_probe_required,
            project_dir=_project_dir,
            extra_env=extra_env,
            required_env=required_env,
            plugin_binding=plugin_binding,
            launch=launch,
            add_dirs=[managed_home.skills_dir],
            managed_home=managed_home,
            tools=tools_arg,
            force_inactive_agent_teams=force_inactive_agent_teams,
            mcp_tool_timeout_sec=mcp_tool_timeout_sec,
        )
        spec = prepared.spec
        executable = prepared.executable

        from autoskillit.cli.session._session_process import run_cook_attempt

        with backend.session_attempt_context(
            session_home=managed_home.generated_home,
            project_dir=_project_dir,
            launch_id=managed_home.launch_id,
            attempt=attempt,
            current_resume_spec=current_resume_spec,
            ceiling_seconds=cook_ceiling_seconds,
        ) as attempt_handle:
            startup_trace.record_attempt_anchor(
                attempt=attempt,
                view_id=attempt_handle.view_id,
            )
            pass_fds = tuple(
                dict.fromkeys(
                    (
                        *spec.inherited_fds,
                        *managed_home.pass_fds,
                        *retained_projection_binding.inherited_fds,
                        *attempt_handle.pass_fds,
                    )
                )
            )
            if not executable_binding_matches_current_file(executable):
                sys.stderr.write(
                    "ERROR: interactive executable changed after capability probing\n"
                )
                raise SystemExit(1)

            def _record_spawn(pid: int, pgid: int) -> None:
                attempt_handle.record_spawn(pid, pgid)
                launch_id = spec.env.get(LAUNCH_ID_ENV_VAR)
                if launch_id and not bind_session_owner(_project_dir, launch_id, pid):
                    raise RuntimeError(
                        f"session owner binding refused for launch {launch_id!r} and pid {pid}"
                    )

            managed_result = run_cook_attempt(
                spec,
                pass_fds=pass_fds,
                on_spawn=_record_spawn,
                on_reaped=attempt_handle.record_reaped,
                trace=startup_trace,
                observer=None,
                not_after=time.time() + cook_ceiling_seconds,
                systemd_scope_enabled=systemd_scope_enabled,
                pre_spawn_check=prepared.pre_spawn_check,
            )
        returncode = managed_result.returncode
    else:
        from autoskillit.cli.install._plugin_artifact import interactive_plugin_authority
        from autoskillit.cli.ui._terminal import terminal_guard

        skill_catalog = skill_compilation.catalog if skill_compilation is not None else None
        artifact_authority, load_mode = interactive_plugin_authority(
            backend=backend,
            project_dir=_project_dir,
            default_base_branch=default_base_branch,
            skill_catalog=skill_catalog,
            generated_home_available=False,
        )

        with plugin_launch_binding_scope(
            authority=artifact_authority,
            backend=backend,
            load_mode=load_mode,
        ) as binding:
            prepared = _finalize_interactive_launch(
                backend,
                exact_binding_probe_required=True,
                project_dir=_project_dir,
                extra_env=extra_env,
                required_env=required_env,
                plugin_binding=binding,
                launch=launch,
                tools=tools_arg,
                force_inactive_agent_teams=force_inactive_agent_teams,
                mcp_tool_timeout_sec=mcp_tool_timeout_sec,
            )
            spec = prepared.spec
            executable = prepared.executable
            assert Path(spec.cwd) == executable.cwd
            if not executable_binding_matches_current_file(executable):
                sys.stderr.write(
                    "ERROR: interactive executable changed after capability probing\n"
                )
                raise SystemExit(1)
            cmd = [*spec.cmd]
            with terminal_guard():
                process = subprocess.Popen(
                    cmd,
                    env=spec.env,
                    cwd=spec.cwd,
                    pass_fds=spec.inherited_fds,
                )
                try:
                    launch_id = spec.env.get(LAUNCH_ID_ENV_VAR)
                    if launch_id and not bind_session_owner(_project_dir, launch_id, process.pid):
                        raise RuntimeError(
                            "session owner binding refused "
                            f"for launch {launch_id!r} and pid {process.pid}"
                        )
                    returncode = process.wait()
                except BaseException:
                    try:
                        if process.poll() is None:
                            process.terminate()
                        try:
                            process.wait(timeout=5.0)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait(timeout=5.0)
                    except BaseException:
                        logger.warning("interactive child cleanup failed", exc_info=True)
                    raise
    reload_session_id = consume_reload_sentinel(_project_dir)
    if reload_session_id is not None:
        return reload_session_id
    if returncode != 0:
        from autoskillit.core import ensure_project_temp

        state_dir = ensure_project_temp(_project_dir) / "session_state"
        state = read_session_state(state_dir)
        if (
            state is not None
            and state.infra_exit_category
            and state.infra_exit_category != InfraExitCategory.COMPLETED
            and state.session_id
        ):
            return _InfraExitSignal(
                session_id=state.session_id, category=state.infra_exit_category
            )
        sys.exit(returncode)
    return None


def _order_launch_env(
    launch_id: str,
    managed_join_parent_id: str | None = None,
) -> dict[str, str]:
    from autoskillit.core import (
        LAUNCH_ID_ENV_VAR,
        MANAGED_JOIN_PARENT_ID_ENV_VAR,
        SESSION_TYPE_ENV_VAR,
        SessionType,
    )

    env = {
        SESSION_TYPE_ENV_VAR: SessionType.ORCHESTRATOR.value,
        LAUNCH_ID_ENV_VAR: launch_id,
    }
    if managed_join_parent_id is not None:
        env[MANAGED_JOIN_PARENT_ID_ENV_VAR] = managed_join_parent_id
    return env


def _write_order_entry(
    project_dir: Path,
    recipe_name: str | None,
    managed_join_parent_id: str | None = None,
) -> tuple[str, dict[str, str]]:
    import uuid

    from autoskillit.cli.session._session_constants import SESSION_TYPE_ORDER
    from autoskillit.core import write_registry_entry

    launch_id = uuid.uuid4().hex[:16]
    launch_env = _order_launch_env(launch_id, managed_join_parent_id)
    write_registry_entry(project_dir, launch_id, SESSION_TYPE_ORDER, recipe_name)
    return launch_id, launch_env


def _run_cook_session_loop(
    launch: InteractiveLaunch,
    *,
    extra_env: dict[str, str] | None,
    project_dir: Path,
    required_env: frozenset[str],
    backend: CodingAgentBackend,
    skill_compilation: CompiledSessionSkillCatalog,
    default_base_branch: str,
    managed_home: ManagedSessionHome | None = None,
    launch_binding: PluginLaunchBinding | None = None,
    retained_binding: PluginLaunchBinding | None = None,
    trace: StartupTrace | None = None,
    force_inactive_agent_teams: bool = False,
    mcp_tool_timeout_sec: float | None = None,
) -> None:
    from autoskillit.cli.session._session_reload import admit_reload

    max_reloads = 10
    max_infra_resumes = 3
    current_launch = launch
    seen_reload_ids: set[str] = set()
    infra_resume_count = 0
    attempt = 0
    while True:
        if managed_home is not None:
            attempt += 1
        session_signal = _run_interactive_session(
            launch=current_launch,
            extra_env=extra_env,
            project_dir=project_dir,
            required_env=required_env,
            backend=backend,
            skill_compilation=skill_compilation,
            default_base_branch=default_base_branch,
            managed_home=managed_home,
            plugin_binding=launch_binding,
            retained_projection_binding=retained_binding,
            startup_trace=trace,
            attempt=attempt if managed_home is not None else None,
            force_inactive_agent_teams=force_inactive_agent_teams,
            mcp_tool_timeout_sec=mcp_tool_timeout_sec,
        )
        if session_signal is None:
            return
        if isinstance(session_signal, _InfraExitSignal):
            if session_signal.category == InfraExitCategory.CONTEXT_EXHAUSTED:
                print(CODEX_AUTO_COMPACTION_BLOCKED_MESSAGE)
                return
            infra_resume_count += 1
            if infra_resume_count >= max_infra_resumes:
                raise SystemExit(
                    f"Too many infrastructure resumes ({max_infra_resumes} max). "
                    f"Last exit: {session_signal.category}"
                )
            current_launch = RestoreSession(session_id=session_signal.session_id)
            continue
        resumed = admit_reload(session_signal, seen_reload_ids, max_reloads)
        current_launch = RestoreSession(session_id=resumed.session_id)


def _launch_cook_session(
    launch: InteractiveLaunch,
    *,
    extra_env: dict[str, str] | None = None,
    project_dir: Path | None = None,
    required_env: frozenset[str],
    backend: CodingAgentBackend,
    skill_compilation: CompiledSessionSkillCatalog,
    launch_id: str,
    default_base_branch: str,
    workspace_temp_dir: str | None,
    force_inactive_agent_teams: bool = False,
    mcp_tool_timeout_sec: float | None = None,
    adaptation_context: SemanticAdaptationContext | None = None,
) -> None:
    """Launch an interactive Claude Code cook session with reload and infra-resume support."""
    launch_project_dir = (project_dir if project_dir is not None else Path.cwd()).resolve()
    unavailability_payload = skill_compilation.unavailability_payload

    if not backend.capabilities.session_dir_persistent:
        render_skill_unavailability(unavailability_payload)
        if isinstance(launch, FreshLaunch):
            launch = replace(
                launch,
                system_prompt=append_skill_unavailability(
                    launch.system_prompt,
                    unavailability_payload,
                ),
            )
        _run_cook_session_loop(
            launch,
            extra_env=extra_env,
            project_dir=launch_project_dir,
            required_env=required_env,
            backend=backend,
            skill_compilation=skill_compilation,
            default_base_branch=default_base_branch,
            force_inactive_agent_teams=force_inactive_agent_teams,
            mcp_tool_timeout_sec=mcp_tool_timeout_sec,
        )
        return

    from autoskillit.cli.install._plugin_artifact import interactive_plugin_authority
    from autoskillit.cli.session._session_startup_trace import StartupTrace
    from autoskillit.core import PluginLoadMode
    from autoskillit.execution import all_backends
    from autoskillit.workspace import (
        DefaultSessionSkillManager,
        SkillsDirectoryProvider,
        resolve_ephemeral_root,
        resolve_persistent_session_roots,
    )

    resolved_temp_dir = resolve_temp_dir(launch_project_dir, workspace_temp_dir)
    persistent_roots = resolve_persistent_session_roots(
        resolved_temp_dir,
        all_backends(),
        required_backend_names={backend.name},
    )
    provider = SkillsDirectoryProvider(
        temp_dir_relpath=temp_dir_display_str(workspace_temp_dir),
        default_base_branch=default_base_branch,
    )
    manager = DefaultSessionSkillManager(
        provider,
        resolve_ephemeral_root(),
        persistent_roots=persistent_roots,
    )
    manager.cleanup_stale()
    artifact_authority, launch_load_mode = interactive_plugin_authority(
        backend=backend,
        project_dir=launch_project_dir,
        default_base_branch=default_base_branch,
        skill_catalog=skill_compilation.catalog,
        generated_home_available=True,
        retain_projection_source=True,
    )
    projection_load_mode = (
        launch_load_mode if launch_load_mode.consumes_artifact else PluginLoadMode.PROJECTED_HOME
    )
    with plugin_launch_binding_scope(
        authority=artifact_authority,
        backend=backend,
        load_mode=projection_load_mode,
    ) as projection_binding:
        if projection_binding is None:
            raise RuntimeError("retained projection mode did not produce a binding")
        managed_codex_route = None
        if adaptation_context is not None:
            from autoskillit.execution.backends._codex_hooks import (
                managed_codex_route_for_launch_context,
            )

            managed_codex_route = managed_codex_route_for_launch_context("interactive")
        projection_context = provider.catalog_projection_context(
            skill_compilation.catalog,
            launch_project_dir,
            backend=backend,
            durable_scripts_root=projection_binding.identity.managed_path,
            adaptation_context=adaptation_context,
            managed_codex_route=managed_codex_route,
        )
        with manager.managed_session(
            launch_id,
            skill_compilation,
            projection_context,
        ) as managed_home:
            render_skill_unavailability(managed_home.unavailability_payload)
            if isinstance(launch, FreshLaunch):
                launch = replace(
                    launch,
                    system_prompt=append_skill_unavailability(
                        launch.system_prompt,
                        managed_home.unavailability_payload,
                    ),
                )
            trace = StartupTrace(launch_project_dir, launch_id, enabled=False)
            trace.record_launch_anchor()
            launch_binding = projection_binding if launch_load_mode.consumes_artifact else None
            try:
                _run_cook_session_loop(
                    launch,
                    extra_env=extra_env,
                    project_dir=launch_project_dir,
                    required_env=required_env,
                    backend=backend,
                    skill_compilation=skill_compilation,
                    default_base_branch=default_base_branch,
                    managed_home=managed_home,
                    launch_binding=launch_binding,
                    retained_binding=projection_binding,
                    trace=trace,
                    force_inactive_agent_teams=force_inactive_agent_teams,
                    mcp_tool_timeout_sec=mcp_tool_timeout_sec,
                )
            except BaseException:
                trace.close(status="failed")
                raise
            trace.close(status="success")
