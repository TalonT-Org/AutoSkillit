"""Fleet session launch helper extracted from _fleet.py."""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import TYPE_CHECKING, Literal, cast

from autoskillit.core import (
    FLEET_SESSION_REQUIRED_ENV,
    FleetSessionEnv,
    NamedResume,
    NoResume,
    SkillExecutionRole,
    dump_yaml_str,
    get_logger,
)

logger = get_logger(__name__)

if TYPE_CHECKING:
    from autoskillit.core import ResumeSpec
    from autoskillit.fleet import ResumeDecision
    from autoskillit.recipe.schema import Recipe

_MAX_RELOADS = 10
_MAX_INFRA_RESUMES = 3


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
    from autoskillit.cli.session._session_launch import (
        _run_interactive_session,
        render_skill_unavailability,
    )
    from autoskillit.cli.session._session_reload import admit_reload

    project_dir = Path.cwd()

    from autoskillit.config import load_config  # noqa: PLC0415

    cfg = load_config(project_dir)

    from autoskillit.execution import get_backend  # noqa: PLC0415
    from autoskillit.workspace import (  # noqa: PLC0415
        compile_session_skill_catalog,
        default_skill_resolver,
    )

    _backend = get_backend(cfg.agent_backend.backend)
    _backend_caps = _backend.capabilities
    mcp_prefix = detect_autoskillit_mcp_prefix(_backend_caps)
    skill_compilation = compile_session_skill_catalog(
        default_skill_resolver().list_effective(project_dir, SkillExecutionRole.ORCHESTRATOR),
        _backend,
    )
    render_skill_unavailability(skill_compilation.unavailability_payload)

    if campaign_recipe is None:
        # Ad-hoc mode: no campaign, no state, bare kitchen open
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
        env_spec = FleetSessionEnv(
            session_type="fleet",
            fleet_mode=fleet_mode,
            project_dir=str(project_dir),
        )
        extra_env: dict[str, str] = env_spec.to_dict()
        current_resume_spec: ResumeSpec = NoResume()
    else:
        # Campaign-driven mode: full orchestrator prompt with manifest and state
        if campaign_id is None:
            raise ValueError("campaign_id must not be None in campaign-driven mode")
        if state_path is None:
            raise ValueError("state_path must not be None in campaign-driven mode")
        from autoskillit.cli.prompts import _build_fleet_campaign_prompt
        from autoskillit.fleet import (
            FLEET_HALTED_SENTINEL,
            derive_orchestrator_resume_spec,
            read_state,
            resume_campaign_from_state,
            update_orchestrator_session_id,
        )

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
        env_spec = FleetSessionEnv(
            session_type="fleet",
            fleet_mode=fleet_mode,
            project_dir=str(project_dir),
            campaign_id=campaign_id,
            campaign_state_path=str(state_path),
            continue_on_failure=str(campaign_recipe.continue_on_failure).lower(),
        )
        extra_env = env_spec.to_dict()

        if resume_metadata is not None:
            state = read_state(state_path)
            current_resume_spec = (
                derive_orchestrator_resume_spec(state, current_backend=cfg.agent_backend.backend)
                if state is not None
                else NoResume()
            )
        else:
            current_resume_spec = NoResume()

    seen_reload_ids: set[str] = set()
    infra_resume_count = 0
    current_initial_message = initial_message

    while True:
        session_signal = _run_interactive_session(
            prompt,
            initial_message=current_initial_message,
            extra_env=extra_env,
            resume_spec=current_resume_spec,
            project_dir=project_dir,
            required_env=FLEET_SESSION_REQUIRED_ENV,
            backend=_backend,
            skill_compilation=skill_compilation,
            force_inactive_agent_teams=cfg.agent_backend.force_inactive_agent_teams,
            mcp_tool_timeout_sec=cfg.run_skill.mcp_tool_timeout_sec,
            cook_ceiling_seconds=cfg.process_tether.cook_ceiling_seconds,
            systemd_scope_enabled=cfg.process_tether.systemd_scope_enabled,
        )
        if session_signal is None:
            break
        if isinstance(session_signal, str):
            current_resume_spec = admit_reload(session_signal, seen_reload_ids, _MAX_RELOADS)
            resume_session_id = session_signal
            is_reload = True
        else:
            infra_resume_count += 1
            if infra_resume_count >= _MAX_INFRA_RESUMES:
                raise SystemExit(
                    f"Too many infrastructure resumes ({_MAX_INFRA_RESUMES} max). "
                    f"Last exit: {session_signal.category}"
                )
            resume_session_id = session_signal.session_id
            current_resume_spec = NamedResume(session_id=resume_session_id)
            is_reload = False

        if campaign_recipe is None:
            if is_reload:
                current_initial_message = None
            continue

        campaign_state_path = cast(Path, state_path)
        active_campaign_id = cast(str, campaign_id)
        update_orchestrator_session_id(campaign_state_path, resume_session_id)
        current_initial_message = None

        fresh_metadata = resume_campaign_from_state(
            campaign_state_path, campaign_recipe.continue_on_failure
        )
        if fresh_metadata is None:
            logger.error("Campaign state corrupted during resume — exiting")
            break
        if fresh_metadata.completed_dispatches_block == FLEET_HALTED_SENTINEL:
            logger.info("Campaign halted on failure during resume — exiting")
            break

        completed_dispatches = fresh_metadata.completed_dispatches_block
        resumable_dispatch_name = (
            fresh_metadata.next_dispatch_name if fresh_metadata.is_resumable else ""
        )
        resume_session_id = (
            fresh_metadata.dispatched_session_id if fresh_metadata.is_resumable else ""
        )
        resume_dispatch_id = fresh_metadata.dispatch_id if fresh_metadata.is_resumable else ""
        resume_retry_reason = fresh_metadata.retry_reason if fresh_metadata.is_resumable else ""
        resume_checkpoint = (
            fresh_metadata.resume_checkpoint if fresh_metadata.is_resumable else None
        )
        prompt = _build_fleet_campaign_prompt(
            campaign_recipe,
            manifest_yaml,
            completed_dispatches,
            mcp_prefix,
            active_campaign_id,
            resumable_dispatch_name=resumable_dispatch_name,
            resume_session_id=resume_session_id,
            resume_retry_reason=resume_retry_reason,
            ingredients_table=ingredients_table,
            prior_dispatch_id=resume_dispatch_id,
            resume_checkpoint=resume_checkpoint,
            max_issues_per_food_truck=cfg.fleet.max_issues_per_food_truck,
            has_unguarded_filesystem_access=_backend_caps.has_unguarded_filesystem_access,
        )
