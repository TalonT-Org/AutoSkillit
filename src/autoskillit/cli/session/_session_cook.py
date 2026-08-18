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
    prepare_interactive_launch,
    render_skill_catalog_exclusions,
    render_skill_contract_composition_failure,
)
from autoskillit.core import (
    PluginLaunchBinding,
    PluginLoadMode,
    SkillContractError,
    executable_binding_matches_current_file,
    is_feature_enabled,
    plugin_launch_binding_scope,
    resolve_project_dir,
)

if TYPE_CHECKING:
    from autoskillit.cli.session._session_startup_trace import StartupTrace
    from autoskillit.cli.session.pty._observer import PtyObserver
    from autoskillit.core import CodingAgentBackend, RepositoryProfileId
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


def _build_cook_projection_context(
    skills_provider: SkillsDirectoryProvider,
    session_catalog: EffectiveSkillCatalog,
    project_dir: Path,
    backend: CodingAgentBackend,
    binding: PluginLaunchBinding | None,
    resolved_exploration_profile: RepositoryProfileId | None,
    *,
    explorer_provisioning_eligible: bool | None = None,
) -> SkillProjectionContext:
    """Bind scripts to the exact artifact selected for this cook session."""
    if binding is None:
        raise RuntimeError("cook projection requires a retained plugin artifact binding")

    base = skills_provider.catalog_projection_context(
        session_catalog,
        project_dir,
        backend=backend,
        durable_scripts_root=binding.identity.managed_path,
        resolved_exploration_profile=resolved_exploration_profile,
    )
    if explorer_provisioning_eligible is not None:
        return replace(
            base,
            explorer_provisioning_eligible=explorer_provisioning_eligible,
            parent_sandbox_mode=(
                "read-only"
                if explorer_provisioning_eligible
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


def cook(
    *,
    resume: bool = False,
    session_id: str | None = None,
    profile: str | None = None,
    backend: CodingAgentBackend | None = None,
) -> None:
    """Launch Claude with all bundled AutoSkillit skills as slash commands."""
    from autoskillit.config import iter_display_categories, load_config
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
    # Same derivation the MCP server uses (git toplevel -> cwd). Running `cook`
    # from a repository subdirectory used to yield a different project_dir than
    # the server derived on the same machine, and both values flow into the
    # execution-bound dispatch contract.
    project_dir = resolve_project_dir()
    skill_resolver = DefaultSkillResolver()
    skill_visibility = config.skill_visibility_spec()
    try:
        validate_skill_tier_roles(skill_visibility, skill_resolver, project_dir)
    except SkillContractError as exc:
        render_skill_contract_composition_failure(exc)
        raise SystemExit(1) from exc
    if backend is None:
        from autoskillit.cli.session._session_backend import resolve_global_backend

        backend = resolve_global_backend(config.agent_backend.backend)
    cook_system_prompt = (
        _COOK_PRE_REVEALED_KITCHEN_PROMPT
        if not backend.capabilities.supports_tool_list_changed
        else None
    )

    if shutil.which(backend.binary_name()) is None:
        print(
            f"ERROR: '{backend.binary_name()}' not found. "
            "Install: https://docs.anthropic.com/en/docs/claude-code"
        )
        raise SystemExit(1)

    from autoskillit import __version__
    from autoskillit.cli.ui._ansi import supports_color

    color = supports_color()
    _B = "\x1b[1m" if color else ""
    _C = "\x1b[96m" if color else ""
    _D = "\x1b[2m" if color else ""
    _G = "\x1b[32m" if color else ""
    _Y = "\x1b[33m" if color else ""
    _R = "\x1b[0m" if color else ""

    if profile is not None:
        if not is_feature_enabled(
            "providers", config.features, experimental_enabled=config.experimental_enabled
        ):
            print(
                "Error: --profile requires the 'providers' feature to be enabled.\n"
                "Enable it in .autoskillit/config.yaml:\n"
                "  features:\n"
                "    providers: true",
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

    print(f"{_B}{_C}AUTOSKILLIT {__version__}{_R} {_D}Kitchen open. All tools active.{_R}")
    skip = {"Telemetry & Diagnostics", "Kitchen"}
    for name, tools in iter_display_categories(
        config.features, experimental_enabled=config.experimental_enabled
    ):
        if name in skip:
            continue
        tool_list = f"{_D}, {_R}".join(f"{_G}{t}{_R}" for t in tools)
        print(f"  {_Y}{name:>20}{_R}  {tool_list}")
    print()

    from autoskillit.cli.ui._ansi import permissions_warning

    print(permissions_warning())

    from autoskillit.cli._onboarding import is_first_run, run_onboarding_menu
    from autoskillit.cli.session._session_constants import SESSION_TYPE_COOK
    from autoskillit.core import (
        CODEX_STARTUP_TRACE_ENV_VAR,
        LAUNCH_ID_ENV_VAR,
        PROVIDER_PROFILE_ENV_VAR,
        SESSION_TYPE_ENV_VAR,
        BareResume,
        ExplorationVectorApplicabilityId,
        ExplorationVectorDisposition,
        NamedResume,
        NoResume,
        RepositoryProfileId,
        SessionType,
        SkillExecutionRole,
        bind_session_owner,
        configure_logging,
        resolve_temp_dir,
        resume_spec_from_cli,
        temp_dir_display_str,
        write_registry_entry,
    )

    configure_logging()

    launch_id = uuid.uuid4().hex[:16]
    resume_spec = resume_spec_from_cli(resume=resume, session_id=session_id)
    trace_setting = os.environ.pop(CODEX_STARTUP_TRACE_ENV_VAR, None)
    if trace_setting not in {None, "1"}:
        raise ValueError(f"{CODEX_STARTUP_TRACE_ENV_VAR} must be absent or exactly '1'")
    trace_enabled = trace_setting == "1" and backend.capabilities.cook_startup_observer_capable

    persistent_roots = resolve_persistent_session_roots(
        resolve_temp_dir(project_dir, config.workspace.temp_dir),
        all_backends(),
        required_backend_names={backend.name},
    )
    initial_prompt: str | None = None
    first_run = is_first_run(project_dir)
    if first_run:
        initial_prompt = run_onboarding_menu(project_dir, color=color)

    ephemeral_root = resolve_ephemeral_root()
    skills_provider = SkillsDirectoryProvider(
        temp_dir_relpath=temp_dir_display_str(config.workspace.temp_dir),
        default_base_branch=config.branching.default_base_branch,
    )
    try:
        session_catalog = skill_resolver.list_effective(
            project_dir,
            SkillExecutionRole.SESSION,
            visibility=skill_visibility,
            cook_session=True,
        )
    except SkillContractError as exc:
        render_skill_contract_composition_failure(exc)
        raise SystemExit(1) from exc
    render_skill_catalog_exclusions(session_catalog.exclusions)
    skill_compilation = compile_session_skill_catalog(session_catalog, backend)
    session_catalog = skill_compilation.catalog
    requires_resolved_exploration_profile = any(
        vector.disposition is ExplorationVectorDisposition.MIGRATED
        and vector.applicability is ExplorationVectorApplicabilityId.ALWAYS
        and vector.profile is RepositoryProfileId.AUTO
        for member in session_catalog.skills
        for vector in member.exploration_vectors
    )
    resolved_exploration_profile = (
        resolve_repository_profile(project_dir) if requires_resolved_exploration_profile else None
    )

    from autoskillit.cli._plugin_artifact import interactive_plugin_authority

    # The selected authority also owns the scripts rendered into the catalog.
    artifact_authority, load_mode = interactive_plugin_authority(
        backend=backend,
        default_base_branch=config.branching.default_base_branch,
        project_dir=project_dir,
        skill_catalog=session_catalog,
        generated_home_available=True,
        retain_projection_source=True,
    )
    session_mgr = DefaultSessionSkillManager(
        skills_provider,
        ephemeral_root,
        persistent_roots=persistent_roots,
    )
    session_mgr.cleanup_stale()

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
                explorer_provisioning_eligible=(
                    True if backend.capabilities.session_scoped_explorer_capable else None
                ),
            ),
        ) as managed_home,
    ):
        if isinstance(resume_spec, BareResume):
            backend.recover_cook_history()
            from autoskillit.cli.session._session_picker import pick_session

            selected_id = pick_session(
                SESSION_TYPE_COOK,
                project_dir,
                backend.session_locator(),
            )
            if selected_id is not None:
                resume_spec = NamedResume(session_id=selected_id)
            else:
                resume_spec = NoResume()

        from autoskillit.cli.session._session_startup_trace import StartupTrace
        from autoskillit.cli.ui._timed_input import timed_prompt

        trace = StartupTrace(project_dir, launch_id, enabled=trace_enabled)
        confirm = timed_prompt(
            "\nLaunch session? [Enter/n]",
            default="",
            timeout=120,
            label="autoskillit cook",
        )
        if confirm.lower() in ("n", "no"):
            return
        trace.record_launch_anchor()
        write_registry_entry(project_dir, launch_id, SESSION_TYPE_COOK, None)

        cook_env_extras: dict[str, str] = {
            SESSION_TYPE_ENV_VAR: SessionType.SKILL.value,
            LAUNCH_ID_ENV_VAR: launch_id,
        }
        if profile is not None:
            cook_env_extras[PROVIDER_PROFILE_ENV_VAR] = profile
            cook_env_extras.update(
                {
                    key: value
                    for key, value in config.providers.profiles[profile].items()
                    if value is not None and key != CODEX_STARTUP_TRACE_ENV_VAR
                }
            )
        cook_env_extras.pop(CODEX_STARTUP_TRACE_ENV_VAR, None)

        current_resume_spec = resume_spec
        current_initial_prompt = initial_prompt
        max_reloads = 10
        seen_reload_ids: set[str] = set()
        attempt = 0

        from autoskillit.cli.session._session_process import run_cook_attempt
        from autoskillit.cli.session._session_reload import consume_reload_sentinel
        from autoskillit.execution import assert_interactive_ordering

        try:
            while True:
                attempt += 1
                launch_binding = projection_binding if load_mode.consumes_artifact else None
                prepared = None
                if backend.capabilities.cook_exact_binding_probe_required:
                    try:
                        prepared = prepare_interactive_launch(
                            backend,
                            project_dir=project_dir,
                            extra_env=cook_env_extras,
                            required_env=None,
                            plugin_binding=launch_binding,
                            resume_spec=current_resume_spec,
                            system_prompt=cook_system_prompt,
                            initial_prompt=current_initial_prompt,
                            add_dirs=[managed_home.skills_dir],
                            generated_home=managed_home.generated_home,
                            force_inactive_agent_teams=force_inactive_agent_teams,
                            mcp_tool_timeout_sec=config.run_skill.mcp_tool_timeout_sec,
                        )
                    except ValueError as exc:
                        _exit_launch_preparation_error(exc)
                    built_spec = prepared.spec
                else:
                    built_spec = backend.build_interactive_cmd(
                        plugin_binding=launch_binding,
                        add_dirs=[managed_home.skills_dir],
                        generated_home=managed_home.generated_home,
                        initial_prompt=current_initial_prompt,
                        resume_spec=current_resume_spec,
                        system_prompt=cook_system_prompt,
                        env_extras=cook_env_extras,
                        force_inactive_agent_teams=force_inactive_agent_teams,
                        project_root=project_dir,
                        mcp_tool_timeout_sec=config.run_skill.mcp_tool_timeout_sec,
                    )
                final_cmd = built_spec.cmd
                final_origin = built_spec.origin
                final_env = dict(built_spec.env)
                spec = replace(
                    built_spec,
                    cmd=final_cmd,
                    env=final_env,
                    cwd=str(project_dir),
                    origin=final_origin,
                )
                assert_interactive_ordering(spec=spec)
                validation_errors = backend.validate_interactive_invocation(spec)
                if validation_errors:
                    raise RuntimeError(
                        "Interactive invocation validation failed: " + "; ".join(validation_errors)
                    )

                with backend.cook_session_context(
                    session_home=managed_home.generated_home,
                    project_dir=project_dir,
                    launch_id=launch_id,
                    attempt=attempt,
                    current_resume_spec=current_resume_spec,
                    ceiling_seconds=config.process_tether.cook_ceiling_seconds,
                ) as attempt_handle:
                    trace.record_attempt_anchor(
                        attempt=attempt,
                        view_id=attempt_handle.view_id,
                    )
                    observer = _startup_observer(
                        backend=backend,
                        trace=trace,
                        enabled=trace_enabled,
                        sqlite_home=managed_home.generated_home,
                        attempt=attempt,
                        view_id=attempt_handle.view_id,
                    )
                    pass_fds = tuple(
                        dict.fromkeys(
                            (
                                *spec.inherited_fds,
                                *managed_home.pass_fds,
                                *attempt_handle.pass_fds,
                            )
                        )
                    )
                    if prepared is not None and not executable_binding_matches_current_file(
                        prepared.executable
                    ):
                        sys.stderr.write(
                            "ERROR: interactive executable changed after capability probing\n"
                        )
                        raise SystemExit(1)

                    def _record_spawn(pid: int, pgid: int) -> None:
                        attempt_handle.record_spawn(pid, pgid)
                        bind_session_owner(project_dir, launch_id, pid)

                    result = run_cook_attempt(
                        spec,
                        pass_fds=pass_fds,
                        on_spawn=_record_spawn,
                        on_reaped=attempt_handle.record_reaped,
                        trace=trace,
                        observer=observer,
                        not_after=time.time() + config.process_tether.cook_ceiling_seconds,
                    )
                    reload_session_id = consume_reload_sentinel(project_dir)
                    _require_observer_ready(observer)
                    trace.require_startup_budgets()

                if reload_session_id is None:
                    if result.returncode != 0:
                        raise SystemExit(result.returncode)
                    if first_run and initial_prompt is not None:
                        from autoskillit.cli._onboarding import mark_onboarded

                        mark_onboarded(project_dir)
                    trace.close(status="success")
                    return

                if len(seen_reload_ids) >= max_reloads:
                    raise SystemExit(
                        f"Too many reloads ({max_reloads} max). Check for infinite loop."
                    )
                if reload_session_id in seen_reload_ids:
                    raise SystemExit(f"Repeated reload_id {reload_session_id!r} — aborting.")
                seen_reload_ids.add(reload_session_id)
                current_resume_spec = NamedResume(session_id=reload_session_id)
                current_initial_prompt = None
        except BaseException:
            trace.close(status="failed")
            raise


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
