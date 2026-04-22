"""Franchise CLI sub-app: campaign management commands."""

from __future__ import annotations

import dataclasses
import json
import os
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Annotated
from uuid import uuid4

from cyclopts import App, Parameter

from autoskillit.core import get_logger

_log = get_logger(__name__)

if TYPE_CHECKING:
    from autoskillit.franchise import ResumeDecision
    from autoskillit.recipe.schema import Recipe

franchise_app = App(name="franchise", help="Campaign franchise management.")


def _remove_clone_fn(path: str, _flag: str) -> dict[str, str]:
    """Remove a clone directory for batch_delete."""
    try:
        shutil.rmtree(path)
        return {"removed": "true"}
    except Exception as exc:
        _log.warning("Failed to remove clone %s: %s", path, exc, exc_info=True)
        return {"removed": "false", "reason": str(exc)}


def _launch_franchise_session(
    campaign_recipe: Recipe,
    campaign_id: str,
    state_path: Path,
    resume_metadata: ResumeDecision | None,
) -> None:
    """Build the L3 orchestrator prompt and launch an interactive franchise session."""
    from autoskillit.cli._mcp_names import detect_autoskillit_mcp_prefix
    from autoskillit.cli._prompts import _build_l3_orchestrator_prompt
    from autoskillit.cli._session_launch import _run_interactive_session
    from autoskillit.core import dump_yaml_str

    mcp_prefix = detect_autoskillit_mcp_prefix()
    manifest_yaml = dump_yaml_str(
        [dataclasses.asdict(d) for d in campaign_recipe.dispatches],
        default_flow_style=False,
        allow_unicode=True,
    )
    completed_dispatches = (
        resume_metadata.completed_dispatches_block if resume_metadata is not None else ""
    )
    prompt = _build_l3_orchestrator_prompt(
        campaign_recipe, manifest_yaml, completed_dispatches, mcp_prefix, campaign_id
    )
    extra_env: dict[str, str] = {
        "AUTOSKILLIT_SESSION_TYPE": "franchise",
        "AUTOSKILLIT_CAMPAIGN_ID": campaign_id,
        "AUTOSKILLIT_CAMPAIGN_STATE_PATH": str(state_path),
        "AUTOSKILLIT_HEADLESS": "0",
    }
    _run_interactive_session(prompt, extra_env=extra_env)


@franchise_app.command(name="run")
def franchise_run(
    campaign_name: str,
    *,
    resume_campaign: str | None = None,
) -> None:
    """Launch an interactive Claude Code session to execute a campaign."""
    if os.environ.get("CLAUDECODE"):
        print("ERROR: 'franchise run' cannot run inside a Claude Code session.")
        print("Run this command in a regular terminal.")
        sys.exit(1)
    if os.environ.get("AUTOSKILLIT_SESSION_TYPE") == "leaf":
        print("ERROR: 'franchise run' cannot run inside a leaf session.")
        sys.exit(1)
    if shutil.which("claude") is None:
        print("ERROR: 'claude' not found. Install: https://docs.anthropic.com/en/docs/claude-code")
        sys.exit(1)

    from autoskillit.core import YAMLError
    from autoskillit.franchise import (
        DispatchRecord,
        resume_campaign_from_state,
        write_initial_state,
    )
    from autoskillit.recipe import find_campaign_by_name, load_recipe, validate_recipe

    match = find_campaign_by_name(campaign_name, Path.cwd())
    if match is None:
        print(f"Campaign not found: '{campaign_name}'")
        sys.exit(1)

    try:
        parsed = load_recipe(match.path)
    except YAMLError as exc:
        print(f"Campaign YAML parse error: {exc}")
        sys.exit(1)
    except ValueError as exc:
        print(f"Campaign structure error: {exc}")
        sys.exit(1)

    errors = validate_recipe(parsed)
    if errors:
        print(f"Campaign '{campaign_name}' failed validation:")
        for err in errors:
            print(f"  - {err}")
        sys.exit(1)

    resume_metadata: ResumeDecision | None = None
    campaign_id: str
    state_path: Path
    franchise_dir = Path.cwd() / ".autoskillit" / "temp" / "franchise"

    if resume_campaign is not None:
        campaign_id = resume_campaign
        state_path = franchise_dir / campaign_id / "state.json"
        resume_metadata = resume_campaign_from_state(state_path, parsed.continue_on_failure)
        if resume_metadata is None:
            print(f"ERROR: Campaign state not found or corrupted for '{campaign_id}'")
            sys.exit(1)
    else:
        campaign_id = uuid4().hex[:16]
        state_dir = franchise_dir / campaign_id
        state_dir.mkdir(parents=True, exist_ok=True)
        state_path = state_dir / "state.json"
        dispatches = [DispatchRecord(name=d.name) for d in parsed.dispatches]
        write_initial_state(state_path, campaign_id, campaign_name, str(match.path), dispatches)

    _launch_franchise_session(parsed, campaign_id, state_path, resume_metadata)


@franchise_app.command(name="list")
def franchise_list() -> None:
    """List available campaign recipes."""
    from autoskillit.core import TerminalColumn, _render_terminal_table
    from autoskillit.recipe import list_campaign_recipes

    result = list_campaign_recipes(Path.cwd())
    if not result.items:
        print("No campaigns found.")
        return

    columns = [
        TerminalColumn("NAME", 30, "<"),
        TerminalColumn("SOURCE", 10, "<"),
        TerminalColumn("DIR", 40, "<"),
    ]
    rows = [(r.name, r.source.value, str(r.path.parent.name)) for r in result.items]
    print(_render_terminal_table(columns, rows))


@franchise_app.command(name="status")
def franchise_status(
    campaign_id: str | None = None,
    *,
    cleanup: bool = False,
    reap: bool = False,
    watch: bool = False,
    json_output: Annotated[bool, Parameter(name=["--json"])] = False,
) -> None:
    """Show franchise campaign status."""
    from autoskillit.core import TerminalColumn, _render_terminal_table
    from autoskillit.franchise import read_state

    franchise_dir = Path.cwd() / ".autoskillit" / "temp" / "franchise"

    if campaign_id is not None:
        state_path = franchise_dir / campaign_id / "state.json"
        state = read_state(state_path)
        if state is None:
            print(f"ERROR: Campaign '{campaign_id}' not found or state corrupted.")
            sys.exit(1)

        if json_output:
            data = {
                "campaign_id": state.campaign_id,
                "campaign_name": state.campaign_name,
                "started_at": state.started_at,
                "dispatches": [d.to_dict() for d in state.dispatches],
            }
            print(json.dumps(data))
            return

        started = datetime.fromtimestamp(state.started_at, tz=UTC).strftime(
            "%Y-%m-%d %H:%M:%S UTC"
        )
        print(f"Campaign: {state.campaign_name}  ID: {state.campaign_id}  Started: {started}")
        columns = [
            TerminalColumn("NAME", 30, "<"),
            TerminalColumn("STATUS", 12, "<"),
            TerminalColumn("DISPATCH_ID", 36, "<"),
            TerminalColumn("REASON", 40, "<"),
        ]
        rows = [(d.name, str(d.status), d.dispatch_id, d.reason) for d in state.dispatches]
        print(_render_terminal_table(columns, rows))

        if cleanup:
            from autoskillit.core import sweep_stale_markers
            from autoskillit.workspace import (
                DefaultSessionSkillManager,
                SkillsDirectoryProvider,
                batch_delete,
                resolve_ephemeral_root,
            )

            batch_delete("", _remove_clone_fn, owner=campaign_id)
            try:
                skill_mgr = DefaultSessionSkillManager(
                    provider=SkillsDirectoryProvider(),
                    ephemeral_root=resolve_ephemeral_root(),
                )
                skill_mgr.cleanup_session(campaign_id)
            except Exception:
                _log.warning(
                    "Session skill cleanup failed for campaign %s", campaign_id, exc_info=True
                )
            sweep_stale_markers()
            print(f"Cleanup complete for campaign '{campaign_id}'.")

        if reap:
            raise NotImplementedError("--reap is not yet implemented.")

        if watch:
            raise NotImplementedError("--watch is not yet implemented.")

    else:
        if not franchise_dir.exists():
            print("No campaigns found.")
            return

        subdirs = [d for d in franchise_dir.iterdir() if d.is_dir()]
        if not subdirs:
            print("No campaigns found.")
            return

        if json_output:
            summaries = []
            for subdir in sorted(subdirs):
                s = read_state(subdir / "state.json")
                if s is None:
                    continue
                status_counts: dict[str, int] = {}
                for d in s.dispatches:
                    status_counts[d.status] = status_counts.get(d.status, 0) + 1
                summaries.append(
                    {
                        "campaign_id": s.campaign_id,
                        "campaign_name": s.campaign_name,
                        "started_at": s.started_at,
                        "dispatch_count": len(s.dispatches),
                        "status_counts": status_counts,
                    }
                )
            print(json.dumps(summaries))
            return

        columns = [
            TerminalColumn("CAMPAIGN_NAME", 30, "<"),
            TerminalColumn("ID", 18, "<"),
            TerminalColumn("DISPATCHES", 10, "<"),
            TerminalColumn("STARTED", 24, "<"),
        ]
        rows = []
        for subdir in sorted(subdirs):
            s = read_state(subdir / "state.json")
            if s is None:
                continue
            started = datetime.fromtimestamp(s.started_at, tz=UTC).strftime("%Y-%m-%d %H:%M UTC")
            rows.append((s.campaign_name, s.campaign_id, str(len(s.dispatches)), started))

        if not rows:
            print("No campaigns found.")
            return

        print(_render_terminal_table(columns, rows))
