"""Fleet session launch helper extracted from _fleet.py."""

from __future__ import annotations

import dataclasses
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from autoskillit.core import (
    CODEX_AUTO_COMPACTION_BLOCKED_MESSAGE,
    FLEET_SESSION_REQUIRED_ENV,
    CodingAgentBackend,
    FleetSessionEnv,
    FreshLaunch,
    InfraExitCategory,
    InteractiveLaunch,
    NamedResume,
    NoResume,
    RestoreSession,
    ResumeSpec,
    ResumeWithBriefing,
    SkillExecutionRole,
    dump_yaml_str,
    get_logger,
)
from autoskillit.execution.backends import managed_codex_route_for_launch_context

logger = get_logger(__name__)

if TYPE_CHECKING:
    from autoskillit.config import ProcessTetherConfig
    from autoskillit.core import SemanticAdaptationContext
    from autoskillit.fleet import ResumeDecision
    from autoskillit.recipe.schema import Recipe

_MAX_RELOADS = 10
_MAX_INFRA_RESUMES = 3


def _check_reload_guard(reload_id: str, seen_reload_ids: set[str]) -> None:
    """Enforce max-reload cap and duplicate-ID detection, then register the ID."""
    if len(seen_reload_ids) >= _MAX_RELOADS:
        raise SystemExit(f"Too many reloads ({_MAX_RELOADS} max). Check for infinite loop.")
    if reload_id in seen_reload_ids:
        raise SystemExit(f"Repeated reload_id {reload_id!r} — aborting.")
    seen_reload_ids.add(reload_id)


@contextmanager
def _fleet_session_launcher(
    *,
    backend: CodingAgentBackend,
    project_dir: Path,
    skill_compilation: Any,
    default_base_branch: str,
    workspace_temp_dir: str | None,
    force_inactive_agent_teams: bool,
    mcp_tool_timeout_sec: float,
    process_tether: ProcessTetherConfig,
    adaptation_context: SemanticAdaptationContext | None = None,
    managed_join_parent_id: str | None = None,
) -> Iterator[Callable[[InteractiveLaunch, dict[str, str]], Any]]:
    """Keep one leased wrapper home through a fleet session's retry loop."""
    from autoskillit.cli.session._session_launch import _run_interactive_session

    def raw_launch(
        launch: InteractiveLaunch,
        extra_env: dict[str, str],
    ) -> Any:
        launch_env = dict(extra_env)
        if managed_join_parent_id is not None:
            from autoskillit.core import MANAGED_JOIN_PARENT_ID_ENV_VAR

            launch_env[MANAGED_JOIN_PARENT_ID_ENV_VAR] = managed_join_parent_id
        return _run_interactive_session(
            launch=launch,
            extra_env=launch_env,
            project_dir=project_dir,
            required_env=FLEET_SESSION_REQUIRED_ENV,
            backend=backend,
            skill_compilation=skill_compilation,
            force_inactive_agent_teams=force_inactive_agent_teams,
            mcp_tool_timeout_sec=mcp_tool_timeout_sec,
            process_tether=process_tether,
        )

    if not backend.capabilities.session_dir_persistent:
        yield raw_launch
        return

    from autoskillit.cli.install._plugin_artifact import interactive_plugin_authority
    from autoskillit.cli.session._session_startup_trace import StartupTrace
    from autoskillit.core import (
        LAUNCH_ID_ENV_VAR,
        MANAGED_JOIN_PARENT_ID_ENV_VAR,
        PluginLoadMode,
        plugin_launch_binding_scope,
        resolve_temp_dir,
        temp_dir_display_str,
    )
    from autoskillit.execution import all_backends
    from autoskillit.workspace import (
        DefaultSessionSkillManager,
        SkillsDirectoryProvider,
        resolve_ephemeral_root,
        resolve_persistent_session_roots,
    )

    persistent_roots = resolve_persistent_session_roots(
        resolve_temp_dir(project_dir, workspace_temp_dir),
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
    authority, launch_load_mode = interactive_plugin_authority(
        backend=backend,
        project_dir=project_dir,
        default_base_branch=default_base_branch,
        skill_catalog=skill_compilation.catalog,
        generated_home_available=True,
        retain_projection_source=True,
    )
    projection_load_mode = (
        launch_load_mode if launch_load_mode.consumes_artifact else PluginLoadMode.PROJECTED_HOME
    )
    launch_id = uuid.uuid4().hex[:16]
    with plugin_launch_binding_scope(
        authority=authority,
        backend=backend,
        load_mode=projection_load_mode,
    ) as projection_binding:
        if projection_binding is None:
            raise RuntimeError("retained projection mode did not produce a binding")
        managed_codex_route = None
        if adaptation_context is not None:
            managed_codex_route = managed_codex_route_for_launch_context("interactive")
        projection_context = provider.catalog_projection_context(
            skill_compilation.catalog,
            project_dir,
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
            trace = StartupTrace(project_dir, launch_id, enabled=False)
            trace.record_launch_anchor()
            attempt = 0
            launch_binding = projection_binding if launch_load_mode.consumes_artifact else None

            def managed_launch(
                launch: InteractiveLaunch,
                extra_env: dict[str, str],
            ) -> Any:
                nonlocal attempt
                attempt += 1
                launch_env = {**extra_env, LAUNCH_ID_ENV_VAR: launch_id}
                if managed_join_parent_id is not None:
                    launch_env[MANAGED_JOIN_PARENT_ID_ENV_VAR] = managed_join_parent_id
                return _run_interactive_session(
                    launch=launch,
                    extra_env=launch_env,
                    project_dir=project_dir,
                    required_env=FLEET_SESSION_REQUIRED_ENV,
                    backend=backend,
                    skill_compilation=skill_compilation,
                    default_base_branch=default_base_branch,
                    managed_home=managed_home,
                    plugin_binding=launch_binding,
                    retained_projection_binding=projection_binding,
                    startup_trace=trace,
                    attempt=attempt,
                    force_inactive_agent_teams=force_inactive_agent_teams,
                    mcp_tool_timeout_sec=mcp_tool_timeout_sec,
                    process_tether=process_tether,
                )

            try:
                yield managed_launch
            except BaseException:
                trace.close(status="failed")
                raise
            trace.close(status="success")


def _refresh_campaign_after_resume(
    *,
    state_path: Path,
    campaign_recipe: Recipe,
    campaign_id: str,
    resume_session_id: str,
    manifest_yaml: str,
    mcp_prefix: str,
    ingredients_table: str | None,
    max_issues_per_food_truck: int,
    has_unguarded_filesystem_access: bool,
) -> bool:
    from autoskillit.cli.prompts import _build_fleet_campaign_prompt
    from autoskillit.fleet import (
        FLEET_HALTED_SENTINEL,
        resume_campaign_from_state,
        update_orchestrator_session_id,
    )

    update_orchestrator_session_id(state_path, resume_session_id)
    fresh_metadata = resume_campaign_from_state(state_path, campaign_recipe.continue_on_failure)
    if fresh_metadata is None:
        logger.error("Campaign state corrupted during resume — exiting")
        return False
    if fresh_metadata.completed_dispatches_block == FLEET_HALTED_SENTINEL:
        logger.info("Campaign halted on failure during resume — exiting")
        return False

    _build_fleet_campaign_prompt(
        campaign_recipe,
        manifest_yaml,
        fresh_metadata.completed_dispatches_block,
        mcp_prefix,
        campaign_id,
        resumable_dispatch_name=(
            fresh_metadata.next_dispatch_name if fresh_metadata.is_resumable else ""
        ),
        resume_session_id=(
            fresh_metadata.dispatched_session_id if fresh_metadata.is_resumable else ""
        ),
        resume_retry_reason=(fresh_metadata.retry_reason if fresh_metadata.is_resumable else ""),
        ingredients_table=ingredients_table,
        prior_dispatch_id=(fresh_metadata.dispatch_id if fresh_metadata.is_resumable else ""),
        resume_checkpoint=(
            fresh_metadata.resume_checkpoint if fresh_metadata.is_resumable else None
        ),
        max_issues_per_food_truck=max_issues_per_food_truck,
        has_unguarded_filesystem_access=has_unguarded_filesystem_access,
    )
    return True


def _run_fleet_session_loop(
    *,
    launch_session: Callable[[InteractiveLaunch, dict[str, str]], Any],
    current_launch: InteractiveLaunch,
    extra_env: dict[str, str],
    campaign_recipe: Recipe | None,
    state_path: Path | None,
    campaign_id: str | None,
    manifest_yaml: str,
    mcp_prefix: str,
    ingredients_table: str | None,
    max_issues_per_food_truck: int,
    has_unguarded_filesystem_access: bool,
) -> None:
    from autoskillit.cli.session._session_reload import admit_reload

    seen_reload_ids: set[str] = set()
    infra_resume_count = 0
    while True:
        session_signal = launch_session(current_launch, extra_env)
        if session_signal is None:
            break
        if isinstance(session_signal, str):
            resumed = admit_reload(session_signal, seen_reload_ids, _MAX_RELOADS)
            current_launch = RestoreSession(session_id=resumed.session_id)
            resume_session_id = session_signal
        else:
            if session_signal.category == InfraExitCategory.CONTEXT_EXHAUSTED:
                print(CODEX_AUTO_COMPACTION_BLOCKED_MESSAGE)
                break
            infra_resume_count += 1
            if infra_resume_count >= _MAX_INFRA_RESUMES:
                raise SystemExit(
                    f"Too many infrastructure resumes ({_MAX_INFRA_RESUMES} max). "
                    f"Last exit: {session_signal.category}"
                )
            resume_session_id = session_signal.session_id
            current_launch = RestoreSession(session_id=resume_session_id)

        if campaign_recipe is None:
            continue
        if state_path is None or campaign_id is None:
            raise RuntimeError(
                "fleet resume invariant violated: state_path and campaign_id "
                "must be set whenever campaign_recipe is set"
            )
        if not _refresh_campaign_after_resume(
            state_path=state_path,
            campaign_recipe=campaign_recipe,
            campaign_id=campaign_id,
            resume_session_id=resume_session_id,
            manifest_yaml=manifest_yaml,
            mcp_prefix=mcp_prefix,
            ingredients_table=ingredients_table,
            max_issues_per_food_truck=max_issues_per_food_truck,
            has_unguarded_filesystem_access=has_unguarded_filesystem_access,
        ):
            break


def _launch_fleet_session(
    campaign_recipe: Recipe | None,
    campaign_id: str | None,
    state_path: Path | None,
    resume_metadata: ResumeDecision | None,
    *,
    fleet_mode: Literal["dispatch", "campaign"],
    ingredients_table: str | None = None,
    initial_message: str | None = None,
    recipe_table: str | None = None,
) -> None:
    """Build the L3 orchestrator prompt and launch an interactive fleet session."""
    from autoskillit.cli import detect_autoskillit_mcp_prefix  # noqa: PLC0415
    from autoskillit.cli.session._session_backend import (  # noqa: PLC0415
        resolve_global_backend,
    )
    from autoskillit.cli.session._session_launch import render_skill_unavailability
    from autoskillit.config import load_config  # noqa: PLC0415
    from autoskillit.workspace import (  # noqa: PLC0415
        compile_session_skill_catalog,
        default_skill_resolver,
    )

    project_dir = Path.cwd()
    cfg = load_config(project_dir)
    _backend = resolve_global_backend(
        cfg.agent_backend.backend,
        codex_runtime_spec=cfg.codex_runtime.resolve(),
    )
    _backend_caps = _backend.capabilities
    mcp_prefix = detect_autoskillit_mcp_prefix(_backend_caps)
    managed_join_context = None
    managed_join_parent_id: str | None = None
    if getattr(_backend_caps, "managed_fixed_batch_route_capable", False):
        from autoskillit.core import new_managed_launch_id
        from autoskillit.server.managed_join_prelaunch import acquire_managed_join_evidence

        managed_join_parent_id = new_managed_launch_id()
        evidence = acquire_managed_join_evidence(
            backend=_backend,
            configured_model=cfg.model.model_override or cfg.model.default_model,
            state_root=project_dir,
            parent_id=managed_join_parent_id,
            launch_context="interactive",
        )
        if evidence is not None:
            managed_join_context = evidence.context
        else:
            managed_join_parent_id = None
    skill_compilation = compile_session_skill_catalog(
        default_skill_resolver().list_effective(project_dir, SkillExecutionRole.ORCHESTRATOR),
        _backend,
        adaptation_context=managed_join_context,
    )
    render_skill_unavailability(skill_compilation.unavailability_payload)
    manifest_yaml = ""

    if campaign_recipe is None:
        from autoskillit.cli.prompts import _build_fleet_dispatch_prompt

        prompt = _build_fleet_dispatch_prompt(
            mcp_prefix,
            recipe_table=recipe_table,
            max_total_issues=cfg.fleet.max_total_issues,
            max_concurrent_dispatches=cfg.fleet.max_concurrent_dispatches,
            has_unguarded_filesystem_access=_backend_caps.has_unguarded_filesystem_access,
            skill_compilation=skill_compilation,
            project_root=project_dir,
            backend=_backend,
        )
        extra_env = FleetSessionEnv(
            session_type="fleet",
            fleet_mode=fleet_mode,
            project_dir=str(project_dir),
        ).to_dict()
        current_resume_spec: ResumeSpec = NoResume()
    else:
        if campaign_id is None:
            raise ValueError("campaign_id must not be None in campaign-driven mode")
        if state_path is None:
            raise ValueError("state_path must not be None in campaign-driven mode")
        from autoskillit.cli.prompts import _build_fleet_campaign_prompt
        from autoskillit.fleet import derive_orchestrator_resume_spec, read_state

        manifest_yaml = dump_yaml_str(
            [dataclasses.asdict(d) for d in campaign_recipe.dispatches],
            default_flow_style=False,
            allow_unicode=True,
        )
        completed_dispatches = (
            resume_metadata.completed_dispatches_block if resume_metadata is not None else ""
        )
        resumable_dispatch_name = (
            resume_metadata.next_dispatch_name
            if resume_metadata is not None and resume_metadata.is_resumable
            else ""
        )
        resume_session_id = (
            resume_metadata.dispatched_session_id
            if resume_metadata is not None and resume_metadata.is_resumable
            else ""
        )
        resume_dispatch_id = (
            resume_metadata.dispatch_id
            if resume_metadata is not None and resume_metadata.is_resumable
            else ""
        )
        resume_retry_reason = (
            resume_metadata.retry_reason
            if resume_metadata is not None and resume_metadata.is_resumable
            else ""
        )
        resume_checkpoint = (
            resume_metadata.resume_checkpoint
            if resume_metadata is not None and resume_metadata.is_resumable
            else None
        )
        prompt = _build_fleet_campaign_prompt(
            campaign_recipe,
            manifest_yaml,
            completed_dispatches,
            mcp_prefix,
            campaign_id,
            resumable_dispatch_name=resumable_dispatch_name,
            resume_session_id=resume_session_id,
            resume_retry_reason=resume_retry_reason,
            ingredients_table=ingredients_table,
            prior_dispatch_id=resume_dispatch_id,
            resume_checkpoint=resume_checkpoint,
            max_issues_per_food_truck=cfg.fleet.max_issues_per_food_truck,
            has_unguarded_filesystem_access=_backend_caps.has_unguarded_filesystem_access,
        )
        extra_env = FleetSessionEnv(
            session_type="fleet",
            fleet_mode=fleet_mode,
            project_dir=str(project_dir),
            campaign_id=campaign_id,
            campaign_state_path=str(state_path),
            continue_on_failure=str(campaign_recipe.continue_on_failure).lower(),
        ).to_dict()
        if resume_metadata is not None:
            state = read_state(state_path)
            current_resume_spec = (
                derive_orchestrator_resume_spec(state, current_backend=cfg.agent_backend.backend)
                if state is not None
                else NoResume()
            )
        else:
            current_resume_spec = NoResume()

    if isinstance(current_resume_spec, NamedResume):
        current_launch: InteractiveLaunch = ResumeWithBriefing(
            session_id=current_resume_spec.session_id,
            briefing=prompt,
        )
    else:
        current_launch = FreshLaunch(system_prompt=prompt, initial_prompt=initial_message)

    with _fleet_session_launcher(
        backend=_backend,
        project_dir=project_dir,
        skill_compilation=skill_compilation,
        default_base_branch=cfg.branching.default_base_branch,
        workspace_temp_dir=cfg.workspace.temp_dir,
        force_inactive_agent_teams=cfg.agent_backend.force_inactive_agent_teams,
        mcp_tool_timeout_sec=cfg.run_skill.mcp_tool_timeout_sec,
        process_tether=cfg.process_tether,
        adaptation_context=managed_join_context,
        managed_join_parent_id=managed_join_parent_id,
    ) as launch_session:
        _run_fleet_session_loop(
            launch_session=launch_session,
            current_launch=current_launch,
            extra_env=extra_env,
            campaign_recipe=campaign_recipe,
            state_path=state_path,
            campaign_id=campaign_id,
            manifest_yaml=manifest_yaml,
            mcp_prefix=mcp_prefix,
            ingredients_table=ingredients_table,
            max_issues_per_food_truck=cfg.fleet.max_issues_per_food_truck,
            has_unguarded_filesystem_access=_backend_caps.has_unguarded_filesystem_access,
        )
