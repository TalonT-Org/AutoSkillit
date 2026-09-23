"""cook command: interactive skill session launcher."""

from __future__ import annotations

import os
import shutil
import sys
import time
import uuid
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING

from autoskillit.cli.session._session_launch import (
    _exit_launch_preparation_error,
    append_skill_unavailability,
    prepare_interactive_launch,
    render_skill_catalog_exclusions,
    render_skill_contract_composition_failure,
    render_skill_unavailability,
)
from autoskillit.core import (
    PluginLaunchBinding,
    PluginLoadMode,
    SkillContractError,
    executable_binding_matches_current_file,
    is_feature_enabled,
    plugin_launch_binding_scope,
    resolve_installed_generation_root,
    resolve_project_dir,
    source_currency,
)
from autoskillit.execution.backends import managed_codex_route_for_launch_context

if TYPE_CHECKING:
    from autoskillit.cli.session._session_process import CookAttemptResult
    from autoskillit.cli.session._session_startup_trace import StartupTrace
    from autoskillit.cli.session.pty._observer import PtyObserver
    from autoskillit.config import AutomationConfig
    from autoskillit.core import (
        CodingAgentBackend,
        FreshLaunch,
        InteractiveLaunch,
        ManagedSessionHome,
        RepositoryProfileId,
        RestoreSession,
        ResumeSpec,
        ResumeWithBriefing,
        SemanticAdaptationContext,
        SkillUnavailabilityPayload,
    )
    from autoskillit.workspace import (
        EffectiveSkillCatalog,
        SkillProjectionContext,
        SkillsDirectoryProvider,
    )

_COOK_PRE_REVEALED_KITCHEN_PROMPT = (
    "This interactive cook session's AutoSkillit kitchen tools are already active and "
    "pre-revealed. Do not call open_kitchen() with no arguments solely to gain tool "
    "access. open_kitchen remains valid when the user explicitly requests activation "
    "or promotion, to load a named recipe with open_kitchen(name=...), or to reopen "
    "the kitchen after close_kitchen(). $<name> or /<name> denotes an in-session skill "
    "invocation. Do not pass a skill name to open_kitchen, load_recipe, migrate_recipe, "
    "or recipe://; those surfaces accept recipe identities only. A name defined as both "
    "a recipe and a skill is rejected until one artifact is renamed."
)


def _print_source_currency_warning(status: str, behind_by: int | None, color: bool) -> None:
    yellow = "\x1b[33m" if color else ""
    reset = "\x1b[0m" if color else ""
    if status == "stale":
        print(
            f"{yellow}WARNING: installed AutoSkillit generation is {behind_by} commits "
            f"behind this checkout. Run `autoskillit install` to refresh it.{reset}"
        )
    elif status == "diverged":
        print(
            f"{yellow}WARNING: installed AutoSkillit generation diverges from this checkout. "
            f"Run `autoskillit install` to refresh it.{reset}"
        )


def _validate_provider_profile(profile: str | None, config: AutomationConfig) -> None:
    if profile is None:
        return
    if not is_feature_enabled(
        "providers", config.features, experimental_enabled=config.experimental_enabled
    ):
        print(
            "Error: --profile requires the 'providers' feature to be enabled.\n"
            "Enable it in .autoskillit/config.yaml:\n  features:\n    providers: true",
            file=sys.stderr,
        )
        raise SystemExit(1)
    if profile not in config.providers.profiles:
        known = ", ".join(sorted(config.providers.profiles)) or "(none defined)"
        print(
            f"Error: Unknown provider profile {profile!r}. Known profiles: {known}\n"
            "Define profiles in .autoskillit/config.yaml under providers.profiles.",
            file=sys.stderr,
        )
        raise SystemExit(1)


def _render_cook_banner(config: AutomationConfig, *, color: bool) -> None:
    from autoskillit import __version__
    from autoskillit.config import iter_display_categories

    bold, cyan, dim = ("\x1b[1m", "\x1b[96m", "\x1b[2m") if color else ("", "", "")
    green, yellow, reset = ("\x1b[32m", "\x1b[33m", "\x1b[0m") if color else ("", "", "")
    print(
        f"{bold}{cyan}AUTOSKILLIT {__version__}{reset} {dim}Kitchen open. All tools active.{reset}"
    )
    for name, tools in iter_display_categories(
        config.features, experimental_enabled=config.experimental_enabled
    ):
        if name not in {"Telemetry & Diagnostics", "Kitchen"}:
            tool_list = f"{dim}, {reset}".join(f"{green}{tool}{reset}" for tool in tools)
            print(f"  {yellow}{name:>20}{reset}  {tool_list}")
    print()


def _resolve_cook_backend(
    config: AutomationConfig, backend: CodingAgentBackend | None
) -> CodingAgentBackend:
    if backend is not None:
        return backend
    from autoskillit.cli.session._session_backend import resolve_global_backend

    return resolve_global_backend(
        config.agent_backend.backend,
        codex_runtime_spec=config.codex_runtime.resolve(),
    )


def _require_cook_binary(backend: CodingAgentBackend) -> None:
    if shutil.which(backend.binary_name()) is None:
        print(
            f"ERROR: '{backend.binary_name()}' not found. "
            "Install: https://docs.anthropic.com/en/docs/claude-code"
        )
        raise SystemExit(1)


def _resolve_cook_trace_enabled(backend: CodingAgentBackend) -> bool:
    import autoskillit.core as core

    trace_setting = os.environ.pop(core.CODEX_STARTUP_TRACE_ENV_VAR, None)
    if trace_setting not in {None, "1"}:
        raise ValueError(f"{core.CODEX_STARTUP_TRACE_ENV_VAR} must be absent or exactly '1'")
    return trace_setting == "1" and backend.capabilities.cook_startup_observer_capable


def _build_cook_projection_context(
    skills_provider: SkillsDirectoryProvider,
    session_catalog: EffectiveSkillCatalog,
    project_dir: Path,
    backend: CodingAgentBackend,
    binding: PluginLaunchBinding | None,
    resolved_exploration_profile: RepositoryProfileId | None,
    *,
    adaptation_context: SemanticAdaptationContext | None = None,
    provisioning_disposition: bool | None = None,
) -> SkillProjectionContext:
    """Bind scripts to the exact artifact selected for this cook session."""
    if binding is None:
        raise RuntimeError("cook projection requires a retained plugin artifact binding")

    managed_codex_route = None
    if adaptation_context is not None:
        managed_codex_route = managed_codex_route_for_launch_context("interactive")
    base = skills_provider.catalog_projection_context(
        session_catalog,
        project_dir,
        backend=backend,
        durable_scripts_root=binding.identity.managed_path,
        resolved_exploration_profile=resolved_exploration_profile,
        adaptation_context=adaptation_context,
        managed_codex_route=managed_codex_route,
    )
    if provisioning_disposition is not None:
        return replace(
            base,
            provisioning_disposition=provisioning_disposition,
            parent_sandbox_mode=(
                "read-only"
                if provisioning_disposition is True
                and backend.capabilities.terminal_explorer_capable
                else base.parent_sandbox_mode
            ),
        )
    return base


def _print_recipes_list() -> None:
    """Print available recipes grouped by category to stdout."""
    from autoskillit.recipe import GROUP_LABELS, group_rank, list_recipes

    recipes = list_recipes(Path.cwd()).items
    if not recipes:
        print("No recipes found.")
        return

    name_w = max(len(r.name) for r in recipes)
    src_w = max(len(r.source) for r in recipes)
    current_rank = -1
    for r in recipes:
        rank = group_rank(r)
        if rank != current_rank:
            current_rank = rank
            print(f"\n{GROUP_LABELS.get(rank, str(rank))}")
            print(f"{'NAME':<{name_w}}  {'SOURCE':<{src_w}}  DESCRIPTION")
            print(f"{'-' * name_w}  {'-' * src_w}  {'-' * 11}")
        print(f"{r.name:<{name_w}}  {r.source:<{src_w}}  {r.description}")


def _acquire_cook_managed_join(
    backend: CodingAgentBackend,
    *,
    configured_model: str,
    project_dir: Path,
    launch_id: str,
) -> tuple[SemanticAdaptationContext | None, str | None]:
    """Acquire cook readiness evidence and retain any rendered refusal."""
    if not getattr(backend.capabilities, "managed_fixed_batch_route_capable", False):
        return None, None

    from autoskillit.server.managed_join_prelaunch import (
        ManagedJoinIssuanceRefusal,
        acquire_managed_join_evidence,
        render_managed_join_refusal,
    )

    rendered_refusal: str | None = None

    def capture_refusal(refusal: ManagedJoinIssuanceRefusal) -> None:
        nonlocal rendered_refusal
        rendered_refusal = render_managed_join_refusal(refusal)

    evidence = acquire_managed_join_evidence(
        backend=backend,
        configured_model=configured_model,
        state_root=project_dir,
        parent_id=launch_id,
        launch_context="interactive",
        on_refusal=capture_refusal,
    )
    return (evidence.context if evidence is not None else None), rendered_refusal


def _resolve_cook_launch(
    backend: CodingAgentBackend,
    resume_spec: ResumeSpec,
    project_dir: Path,
    session_type: str,
) -> InteractiveLaunch:
    import autoskillit.core as core
    from autoskillit.cli.session import _session_launch_intent

    if not isinstance(resume_spec, core.NoResume):
        _session_launch_intent.prepare_resume_housekeeping(backend, resume_spec=resume_spec)
    return _session_launch_intent.resolve_interactive_launch(
        resume_spec=resume_spec,
        session_type=session_type,
        project_dir=project_dir,
        backend=backend,
    )


def _prepare_resumed_cook_launch(
    project_dir: Path, launch: RestoreSession | ResumeWithBriefing
) -> str:
    import autoskillit.core as core

    return core.claim_launch_for_session(
        project_dir,
        claude_session_id=launch.session_id,
        session_type="cook",
        recipe_name=None,
    )


def _prepare_fresh_cook_launch(
    project_dir: Path,
    launch: FreshLaunch,
    launch_id: str,
    session_type: str,
    trace: StartupTrace,
) -> str | None:
    import autoskillit.core as core
    from autoskillit.cli.session import _session_launch_intent

    if not _session_launch_intent._run_fresh_launch_ceremony(
        launch=launch,
        is_tty=sys.stdin.isatty(),
        label="autoskillit cook",
    ):
        return None
    trace.record_launch_anchor()
    core.write_registry_entry(project_dir, launch_id, session_type, None)
    return launch_id


def _prepare_cook_managed_launch(
    launch: InteractiveLaunch,
    system_prompt: str | None,
    unavailability_payload: SkillUnavailabilityPayload,
    project_dir: Path,
    *,
    color: bool,
    managed_join_refusal: str | None,
) -> tuple[InteractiveLaunch, bool]:
    import autoskillit.core as core
    from autoskillit.cli.session import _session_onboarding

    render_skill_unavailability(unavailability_payload)
    if managed_join_refusal is not None:
        print(f"WARNING: {managed_join_refusal}")
    if not isinstance(launch, core.FreshLaunch):
        return launch, False
    initial_prompt = (
        _session_onboarding.run_onboarding_menu(project_dir, color=color)
        if _session_onboarding.is_first_run(project_dir)
        else None
    )
    return replace(
        launch,
        system_prompt=append_skill_unavailability(system_prompt, unavailability_payload),
        initial_prompt=initial_prompt,
    ), initial_prompt is not None


def _build_cook_attempt_env(
    launch_id: str,
    profile: str | None,
    config: AutomationConfig,
    managed_join_context: SemanticAdaptationContext | None,
) -> dict[str, str]:
    import autoskillit.core as core

    extras = {
        core.SESSION_TYPE_ENV_VAR: core.SessionType.SKILL.value,
        core.LAUNCH_ID_ENV_VAR: launch_id,
    }
    if managed_join_context is not None:
        extras[core.MANAGED_JOIN_PARENT_ID_ENV_VAR] = launch_id
    if profile is not None:
        extras[core.PROVIDER_PROFILE_ENV_VAR] = profile
        extras.update(
            {
                key: value
                for key, value in config.providers.profiles[profile].items()
                if value is not None and key != core.CODEX_STARTUP_TRACE_ENV_VAR
            }
        )
    extras.pop(core.CODEX_STARTUP_TRACE_ENV_VAR, None)
    return extras


def _execute_cook_attempt(
    *,
    backend: CodingAgentBackend,
    project_dir: Path,
    cook_env_extras: dict[str, str],
    launch: InteractiveLaunch,
    managed_home: ManagedSessionHome,
    projection_binding: PluginLaunchBinding,
    load_mode: PluginLoadMode,
    launch_id: str,
    attempt: int,
    config: AutomationConfig,
    trace: StartupTrace,
    trace_enabled: bool,
    force_inactive_agent_teams: bool,
) -> tuple[CookAttemptResult, str | None]:
    import autoskillit.core as core
    from autoskillit.cli.session import _session_process, _session_reload
    from autoskillit.execution import assert_interactive_ordering, assert_resume_purity

    match launch:
        case core.FreshLaunch():
            current_resume_spec: ResumeSpec = core.NoResume()
        case (
            core.RestoreSession(session_id=session_id)
            | core.ResumeWithBriefing(session_id=session_id)
        ):
            current_resume_spec = core.NamedResume(session_id=session_id)
    try:
        prepared = prepare_interactive_launch(
            backend,
            project_dir=project_dir,
            extra_env=cook_env_extras,
            required_env=None,
            plugin_binding=projection_binding if load_mode.consumes_artifact else None,
            launch=launch,
            add_dirs=[managed_home.skills_dir],
            generated_home=managed_home.generated_home,
            home_prepared=True,
            force_inactive_agent_teams=force_inactive_agent_teams,
            mcp_tool_timeout_sec=config.run_skill.mcp_tool_timeout_sec,
        )
    except ValueError as exc:
        _exit_launch_preparation_error(exc)
    built_spec = prepared.spec
    spec = replace(
        built_spec,
        cmd=built_spec.cmd,
        env=dict(built_spec.env),
        cwd=str(project_dir),
        origin=built_spec.origin,
    )
    variadic_flags, value_bearing_flags = backend.interactive_ordering_flags()
    assert_interactive_ordering(
        spec=spec,
        variadic_flags=variadic_flags,
        value_bearing_flags=value_bearing_flags,
    )
    assert_resume_purity(spec=spec, launch=launch)
    validation = backend.validate_interactive_invocation(spec)
    if validation.errors:
        raise RuntimeError(
            "Interactive invocation validation failed: " + "; ".join(validation.errors)
        )
    with backend.session_attempt_context(
        session_home=managed_home.generated_home,
        project_dir=project_dir,
        launch_id=launch_id,
        attempt=attempt,
        current_resume_spec=current_resume_spec,
        ceiling_seconds=config.process_tether.cook_ceiling_seconds,
    ) as attempt_handle:
        trace.record_attempt_anchor(attempt=attempt, view_id=attempt_handle.view_id)
        observer = _startup_observer(
            backend=backend,
            trace=trace,
            enabled=trace_enabled,
            sqlite_home=managed_home.generated_home,
            attempt=attempt,
            view_id=attempt_handle.view_id,
        )
        pass_fds = tuple(
            dict.fromkeys((*spec.inherited_fds, *managed_home.pass_fds, *attempt_handle.pass_fds))
        )
        if not executable_binding_matches_current_file(prepared.executable):
            sys.stderr.write("ERROR: interactive executable changed after capability probing\n")
            raise SystemExit(1)

        def _record_spawn(pid: int, pgid: int) -> None:
            attempt_handle.record_spawn(pid, pgid)
            if not core.bind_session_owner(project_dir, launch_id, pid):
                raise RuntimeError(
                    f"session owner binding refused for launch {launch_id!r} and pid {pid}"
                )

        result = _session_process.run_cook_attempt(
            spec,
            pass_fds=pass_fds,
            on_spawn=_record_spawn,
            on_reaped=attempt_handle.record_reaped,
            trace=trace,
            observer=observer,
            not_after=time.time() + config.process_tether.cook_ceiling_seconds,
            systemd_scope_enabled=config.process_tether.systemd_scope_enabled,
            pre_spawn_check=validation.pre_spawn_check,
        )
        reload_session_id = _session_reload.consume_reload_sentinel(project_dir)
        _require_observer_ready(observer)
        trace.require_startup_budgets()
    return result, reload_session_id


def _run_managed_cook(
    *,
    backend: CodingAgentBackend,
    project_dir: Path,
    launch: InteractiveLaunch,
    launch_id: str,
    config: AutomationConfig,
    cook_env_extras: dict[str, str],
    managed_home: ManagedSessionHome,
    projection_binding: PluginLaunchBinding | None,
    load_mode: PluginLoadMode,
    trace: StartupTrace,
    trace_enabled: bool,
    force_inactive_agent_teams: bool,
    showed_onboarding: bool,
) -> None:
    import autoskillit.core as core
    from autoskillit.cli.session import _session_onboarding, _session_reload

    if projection_binding is None:
        raise RuntimeError(
            "cook: missing plugin launch binding — load_mode.consumes_artifact "
            "must hold when entering the managed cook loop"
        )
    current_launch = launch
    seen_reload_ids: set[str] = set()
    max_reloads = 10
    attempt = 0
    try:
        while True:
            attempt += 1
            result, reload_session_id = _execute_cook_attempt(
                backend=backend,
                project_dir=project_dir,
                cook_env_extras=cook_env_extras,
                launch=current_launch,
                managed_home=managed_home,
                projection_binding=projection_binding,
                load_mode=load_mode,
                launch_id=launch_id,
                attempt=attempt,
                config=config,
                trace=trace,
                trace_enabled=trace_enabled,
                force_inactive_agent_teams=force_inactive_agent_teams,
            )
            if reload_session_id is None:
                if result.returncode != 0:
                    raise SystemExit(result.returncode)
                if showed_onboarding:
                    _session_onboarding.mark_onboarded(project_dir)
                trace.close(status="success")
                return
            current_launch = core.RestoreSession(
                session_id=_session_reload.admit_reload(
                    reload_session_id, seen_reload_ids, max_reloads
                ).session_id
            )
    except BaseException:
        trace.close(status="failed")
        raise


def cook(
    *,
    resume: bool = False,
    session_id: str | None = None,
    profile: str | None = None,
    backend: CodingAgentBackend | None = None,
) -> None:
    """Launch Claude with all bundled AutoSkillit skills as slash commands."""
    from autoskillit.config import load_config
    from autoskillit.execution import all_backends
    from autoskillit.exploration import resolve_repository_profile
    from autoskillit.workspace import (
        DefaultSessionSkillManager,
        DefaultSkillResolver,
        SkillsDirectoryProvider,
        compile_session_skill_catalog,
        resolve_ephemeral_root,
        resolve_persistent_session_roots,
        validate_skill_tier_roles,
    )

    config = load_config()
    force_inactive_agent_teams = config.agent_backend.force_inactive_agent_teams
    project_dir = resolve_project_dir()
    skill_resolver = DefaultSkillResolver()
    skill_visibility = config.skill_visibility_spec()
    try:
        validate_skill_tier_roles(skill_visibility, skill_resolver, project_dir)
    except SkillContractError as exc:
        render_skill_contract_composition_failure(exc)
        raise SystemExit(1) from exc
    backend = _resolve_cook_backend(config, backend)
    cook_system_prompt = (
        _COOK_PRE_REVEALED_KITCHEN_PROMPT
        if not backend.capabilities.supports_tool_list_changed
        else None
    )
    _require_cook_binary(backend)

    import autoskillit.core as core
    from autoskillit.cli.session._session_constants import SESSION_TYPE_COOK
    from autoskillit.cli.ui._ansi import permissions_warning, supports_color

    color = supports_color()
    currency = source_currency(
        project_dir,
        generation_root=resolve_installed_generation_root(),
    )
    _print_source_currency_warning(currency.status, currency.behind_by, color)
    _validate_provider_profile(profile, config)
    _render_cook_banner(config, color=color)
    print(permissions_warning())

    core.configure_logging()
    resume_spec = core.resume_spec_from_cli(resume=resume, session_id=session_id)
    trace_enabled = _resolve_cook_trace_enabled(backend)
    persistent_roots = resolve_persistent_session_roots(
        core.resolve_temp_dir(project_dir, config.workspace.temp_dir),
        all_backends(),
        required_backend_names={backend.name},
    )
    skills_provider = SkillsDirectoryProvider(
        temp_dir_relpath=core.temp_dir_display_str(config.workspace.temp_dir),
        default_base_branch=config.branching.default_base_branch,
    )
    session_mgr = DefaultSessionSkillManager(
        skills_provider,
        resolve_ephemeral_root(),
        persistent_roots=persistent_roots,
    )
    session_mgr.cleanup_stale()

    claimed_launch_id: str | None = None
    try:
        launch = _resolve_cook_launch(backend, resume_spec, project_dir, SESSION_TYPE_COOK)
        match launch:
            case core.FreshLaunch():
                launch_id = uuid.uuid4().hex[:16]
            case core.RestoreSession() | core.ResumeWithBriefing():
                claimed_launch_id = _prepare_resumed_cook_launch(project_dir, launch)
                launch_id = claimed_launch_id

        managed_join_context, managed_join_refusal = _acquire_cook_managed_join(
            backend,
            configured_model=config.model.model_override or config.model.default_model,
            project_dir=project_dir,
            launch_id=launch_id,
        )
        try:
            session_catalog = skill_resolver.list_effective(
                project_dir,
                core.SkillExecutionRole.SESSION,
                visibility=skill_visibility,
                cook_session=True,
            )
        except SkillContractError as exc:
            render_skill_contract_composition_failure(exc)
            raise SystemExit(1) from exc
        render_skill_catalog_exclusions(session_catalog.exclusions)
        skill_compilation = compile_session_skill_catalog(
            session_catalog, backend, adaptation_context=managed_join_context
        )
        session_catalog = skill_compilation.catalog
        requires_resolved_exploration_profile = any(
            vector.disposition is core.ExplorationVectorDisposition.MIGRATED
            and vector.applicability is core.ExplorationVectorApplicabilityId.ALWAYS
            and vector.profile is core.RepositoryProfileId.AUTO
            for member in session_catalog.skills
            for vector in member.exploration_vectors
        )
        resolved_exploration_profile = (
            resolve_repository_profile(project_dir)
            if requires_resolved_exploration_profile
            else None
        )

        from autoskillit.cli.install._plugin_artifact import interactive_plugin_authority

        artifact_authority, load_mode = interactive_plugin_authority(
            backend=backend,
            default_base_branch=config.branching.default_base_branch,
            project_dir=project_dir,
            skill_catalog=session_catalog,
            generated_home_available=True,
            retain_projection_source=True,
        )
        projection_load_mode = (
            load_mode if load_mode.consumes_artifact else PluginLoadMode.PROJECTED_HOME
        )
        with (
            plugin_launch_binding_scope(
                authority=artifact_authority,
                backend=backend,
                load_mode=projection_load_mode,
            ) as projection_binding,
            session_mgr.managed_session(
                launch_id,
                skill_compilation,
                _build_cook_projection_context(
                    skills_provider,
                    session_catalog,
                    project_dir,
                    backend,
                    projection_binding,
                    resolved_exploration_profile,
                    adaptation_context=managed_join_context,
                    provisioning_disposition=(
                        True if backend.capabilities.session_scoped_explorer_capable else None
                    ),
                ),
            ) as managed_home,
        ):
            launch, showed_onboarding = _prepare_cook_managed_launch(
                launch,
                cook_system_prompt,
                managed_home.unavailability_payload,
                project_dir,
                color=color,
                managed_join_refusal=managed_join_refusal,
            )
            from autoskillit.cli.session._session_startup_trace import StartupTrace

            trace = StartupTrace(project_dir, launch_id, enabled=trace_enabled)
            if isinstance(launch, core.FreshLaunch):
                claimed_launch_id = _prepare_fresh_cook_launch(
                    project_dir, launch, launch_id, SESSION_TYPE_COOK, trace
                )
                if claimed_launch_id is None:
                    return
            else:
                trace.record_launch_anchor()
            cook_env_extras = _build_cook_attempt_env(
                launch_id, profile, config, managed_join_context
            )
            _run_managed_cook(
                backend=backend,
                project_dir=project_dir,
                launch=launch,
                launch_id=launch_id,
                config=config,
                cook_env_extras=cook_env_extras,
                managed_home=managed_home,
                projection_binding=projection_binding,
                load_mode=load_mode,
                trace=trace,
                trace_enabled=trace_enabled,
                force_inactive_agent_teams=force_inactive_agent_teams,
                showed_onboarding=showed_onboarding,
            )
    finally:
        if claimed_launch_id is not None:
            core.release_session_claim(project_dir, claimed_launch_id)


def _startup_observer(
    *,
    backend: CodingAgentBackend,
    trace: StartupTrace,
    enabled: bool,
    sqlite_home: Path,
    attempt: int,
    view_id: str,
) -> PtyObserver | None:
    """Build the optional Codex PTY observer without leaking trace state to children."""
    if not enabled:
        return None
    from autoskillit.cli.session.pty._observer import PtyObserver
    from autoskillit.core import ObserverStatus
    from autoskillit.execution import CodexStateReadinessProbe

    def record_readiness(status: ObserverStatus) -> None:
        if status is ObserverStatus.READY:
            trace.record_stage(
                "state_ready",
                attempt=attempt,
                view_id=view_id,
            )

    return PtyObserver(
        readiness_probe=CodexStateReadinessProbe(
            codex_version=backend.version(),
            sqlite_home=sqlite_home,
        ),
        on_first_output=lambda: trace.record_stage(
            "first_output",
            attempt=attempt,
            view_id=view_id,
        ),
        on_hook_review=lambda: trace.record_stage(
            "hook_review",
            attempt=attempt,
            view_id=view_id,
        ),
        on_readiness=record_readiness,
    )


def _require_observer_ready(observer: PtyObserver | None) -> None:
    """Fail an enabled traced launch when the guarded state probe never became ready."""
    if observer is None:
        return
    from autoskillit.core import ObserverStatus

    if observer.readiness_status is not ObserverStatus.READY:
        status = observer.readiness_status
        status_name = "unobserved" if status is None else status.value
        raise RuntimeError(f"Codex state readiness failed closed: {status_name}")
