"""Fleet session launch helper extracted from _fleet.py."""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from autoskillit.core import NamedResume, NoResume, get_logger

logger = get_logger(__name__)

if TYPE_CHECKING:
    from autoskillit.core import ResumeSpec
    from autoskillit.fleet import ResumeDecision
    from autoskillit.recipe.schema import Recipe

_MAX_RELOADS = 10


def _check_reload_guard(reload_id: str, seen_reload_ids: set[str]) -> None:
    """Enforce max-reload cap and duplicate-ID detection, then register the ID."""
    if len(seen_reload_ids) >= _MAX_RELOADS:
        raise SystemExit(f"Too many reloads ({_MAX_RELOADS} max). Check for infinite loop.")
    if reload_id in seen_reload_ids:
        raise SystemExit(f"Repeated reload_id {reload_id!r} — aborting.")
    seen_reload_ids.add(reload_id)


def _launch_fleet_session(
    campaign_recipe: Recipe | None,
    campaign_id: str | None,
    state_path: Path | None,
    resume_metadata: ResumeDecision | None,
    *,
    fleet_mode: Literal["dispatch", "campaign"],
    ingredients_table: str | None = None,
) -> None:
    """Build the L3 orchestrator prompt and launch an interactive fleet session."""
    from autoskillit.cli import detect_autoskillit_mcp_prefix  # noqa: PLC0415
    from autoskillit.cli.session._session_launch import _run_interactive_session

    mcp_prefix = detect_autoskillit_mcp_prefix()

    project_dir = Path.cwd()

    if campaign_recipe is None:
        # Ad-hoc mode: no campaign, no state, bare kitchen open
        from autoskillit.cli._prompts import _build_fleet_dispatch_prompt

        prompt = _build_fleet_dispatch_prompt(mcp_prefix)
        extra_env: dict[str, str] = {
            "AUTOSKILLIT_SESSION_TYPE": "fleet",
            "AUTOSKILLIT_FLEET_MODE": fleet_mode,
            "AUTOSKILLIT_HEADLESS": "0",
        }
        seen_reload_ids: set[str] = set()
        current_resume_spec: ResumeSpec = NoResume()
        while True:
            reload_id = _run_interactive_session(
                prompt,
                extra_env=extra_env,
                resume_spec=current_resume_spec,
                project_dir=project_dir,
            )
            if reload_id is None:
                break
            _check_reload_guard(reload_id, seen_reload_ids)
            current_resume_spec = NamedResume(session_id=reload_id)
    else:
        # Campaign-driven mode: full orchestrator prompt with manifest and state
        if campaign_id is None:
            raise ValueError("campaign_id must not be None in campaign-driven mode")
        if state_path is None:
            raise ValueError("state_path must not be None in campaign-driven mode")
        from autoskillit.cli._prompts import _build_fleet_campaign_prompt
        from autoskillit.core import dump_yaml_str
        from autoskillit.fleet import FLEET_HALTED_SENTINEL, resume_campaign_from_state

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
            resume_metadata.l3_session_id
            if resume_metadata is not None and resume_metadata.is_resumable
            else ""
        )
        prompt = _build_fleet_campaign_prompt(
            campaign_recipe,
            manifest_yaml,
            completed_dispatches,
            mcp_prefix,
            campaign_id,
            resumable_dispatch_name=resumable_dispatch_name,
            resume_session_id=resume_session_id,
            ingredients_table=ingredients_table,
        )
        extra_env = {
            "AUTOSKILLIT_SESSION_TYPE": "fleet",
            "AUTOSKILLIT_FLEET_MODE": fleet_mode,
            "AUTOSKILLIT_CAMPAIGN_ID": campaign_id,
            "AUTOSKILLIT_CAMPAIGN_STATE_PATH": str(state_path),
            "AUTOSKILLIT_CONTINUE_ON_FAILURE": str(campaign_recipe.continue_on_failure).lower(),
            "AUTOSKILLIT_HEADLESS": "0",
        }

        seen_reload_ids = set[str]()
        current_resume_spec = NoResume()

        while True:
            reload_id = _run_interactive_session(
                prompt,
                extra_env=extra_env,
                resume_spec=current_resume_spec,
                project_dir=project_dir,
            )
            if reload_id is None:
                break
            _check_reload_guard(reload_id, seen_reload_ids)

            fresh_metadata = resume_campaign_from_state(
                state_path, campaign_recipe.continue_on_failure
            )
            if fresh_metadata is None:
                logger.error("Campaign state corrupted during reload — exiting")
                break
            if fresh_metadata.completed_dispatches_block == FLEET_HALTED_SENTINEL:
                logger.info("Campaign halted on failure during reload — exiting")
                break

            completed_dispatches = fresh_metadata.completed_dispatches_block
            resumable_dispatch_name = (
                fresh_metadata.next_dispatch_name if fresh_metadata.is_resumable else ""
            )
            resume_session_id = fresh_metadata.l3_session_id if fresh_metadata.is_resumable else ""
            prompt = _build_fleet_campaign_prompt(
                campaign_recipe,
                manifest_yaml,
                completed_dispatches,
                mcp_prefix,
                campaign_id,
                resumable_dispatch_name=resumable_dispatch_name,
                resume_session_id=resume_session_id,
                ingredients_table=ingredients_table,
            )
            current_resume_spec = NamedResume(session_id=reload_id)
