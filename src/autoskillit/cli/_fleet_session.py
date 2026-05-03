"""Fleet session launch helper extracted from _fleet.py."""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from autoskillit.core import NoResume, get_logger

logger = get_logger(__name__)

if TYPE_CHECKING:
    from autoskillit.fleet import ResumeDecision
    from autoskillit.recipe.schema import Recipe


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
    from autoskillit.cli._session_launch import _run_interactive_session

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
        while True:
            reload_id = _run_interactive_session(
                prompt, extra_env=extra_env, resume_spec=NoResume(), project_dir=project_dir
            )
            if reload_id is None:
                break
    else:
        # Campaign-driven mode: full orchestrator prompt with manifest and state
        if campaign_id is None:
            raise ValueError("campaign_id must not be None in campaign-driven mode")
        if state_path is None:
            raise ValueError("state_path must not be None in campaign-driven mode")
        from autoskillit.cli._prompts import _build_fleet_campaign_prompt
        from autoskillit.core import dump_yaml_str

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
        prompt = _build_fleet_campaign_prompt(
            campaign_recipe,
            manifest_yaml,
            completed_dispatches,
            mcp_prefix,
            campaign_id,
            resumable_dispatch_name=resumable_dispatch_name,
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
        while True:
            reload_id = _run_interactive_session(
                prompt, extra_env=extra_env, resume_spec=NoResume(), project_dir=project_dir
            )
            if reload_id is None:
                break
