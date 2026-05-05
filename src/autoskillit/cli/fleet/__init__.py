"""Fleet CLI sub-app: campaign management commands."""

from __future__ import annotations

import json
import os
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Annotated
from uuid import uuid4

from cyclopts import App, Parameter

from autoskillit.cli.fleet._fleet_display import (
    _aggregate_totals,
    _compute_exit_code,
    _cross_check_tokens,
    _render_status_display,
    _watch_loop,
    render_fleet_error,
)
from autoskillit.cli.fleet._fleet_lifecycle import (
    _pick_resume_campaign,
    _reap_stale_dispatches,
)
from autoskillit.cli.fleet._fleet_preview import (
    _FLEET_DISPATCH_GREETINGS,
    _print_dispatch_preview,
)
from autoskillit.cli.fleet._fleet_session import _launch_fleet_session
from autoskillit.core import TerminalColumn, get_logger, is_feature_enabled

logger = get_logger(__name__)


def _require_fleet(cfg: AutomationConfig) -> None:
    """Exit with clear message if fleet feature is not enabled."""
    if not is_feature_enabled(
        "fleet", cfg.features, experimental_enabled=cfg.experimental_enabled
    ):
        print(
            "The 'fleet' feature is not enabled.\n"
            "Enable it with: features.experimental_enabled: true in your config\n"
            "Or set: AUTOSKILLIT_FEATURES__FLEET=true",
            file=sys.stderr,
        )
        raise SystemExit(1)


if TYPE_CHECKING:
    from autoskillit.config import AutomationConfig
    from autoskillit.fleet import ResumeDecision


fleet_app = App(name="fleet", help="Campaign fleet management.")
__all__ = ["fleet_app", "render_fleet_error"]


def _remove_clone_fn(path: str, _flag: str) -> dict[str, str]:
    """Remove a clone directory for batch_delete."""
    try:
        shutil.rmtree(path)
        return {"removed": "true"}
    except Exception as exc:
        logger.warning("Failed to remove clone %s: %s", path, exc, exc_info=True)
        return {"removed": "false", "reason": str(exc)}


@fleet_app.command(name="dispatch")
def fleet_dispatch() -> None:
    """Launch a bare fleet dispatcher session (free-flow mode)."""
    if os.environ.get("CLAUDECODE"):
        print("ERROR: 'fleet dispatch' cannot run inside a Claude Code session.")
        print("Run this command in a regular terminal.")
        sys.exit(1)
    if os.environ.get("AUTOSKILLIT_SESSION_TYPE") in ("skill", "leaf"):
        print("ERROR: 'fleet dispatch' cannot run inside a skill or leaf (deprecated) session.")
        sys.exit(1)

    from autoskillit.config import load_config

    cfg = load_config(Path.cwd())
    _require_fleet(cfg)

    recipe_table = _print_dispatch_preview()

    from autoskillit.cli.ui._timed_input import timed_prompt

    confirm = timed_prompt(
        "\nLaunch session? [Enter/n]", default="", timeout=120, label="autoskillit fleet dispatch"
    )
    if confirm.lower() in ("n", "no"):
        return

    import random

    greeting = random.choice(_FLEET_DISPATCH_GREETINGS).format(recipe_table=recipe_table)

    _launch_fleet_session(
        None,
        None,
        None,
        None,
        fleet_mode="dispatch",
        initial_message=greeting,
        recipe_table=recipe_table,
    )


@fleet_app.command(name="campaign")
def fleet_campaign(
    campaign_name: str | None = None,
    *,
    resume_campaign: Annotated[str | None, Parameter(name=["--resume"])] = None,
) -> None:
    """Launch an interactive Claude Code session to execute a named campaign."""
    if os.environ.get("CLAUDECODE"):
        print("ERROR: 'fleet campaign' cannot run inside a Claude Code session.")
        print("Run this command in a regular terminal.")
        sys.exit(1)
    if os.environ.get("AUTOSKILLIT_SESSION_TYPE") in ("skill", "leaf"):
        print("ERROR: 'fleet campaign' cannot run inside a skill or leaf (deprecated) session.")
        sys.exit(1)

    from autoskillit.config import load_config

    cfg = load_config(Path.cwd())
    _require_fleet(cfg)
    from autoskillit.cli.ui._menu import run_selection_menu

    if campaign_name is None and resume_campaign is None:
        from autoskillit.recipe import list_campaign_recipes

        result = list_campaign_recipes(Path.cwd())
        if not result.items:
            print("No campaigns found. Place campaign recipes in .autoskillit/recipes/campaigns/")
            sys.exit(1)

        selected = run_selection_menu(
            result.items,
            header="Available campaigns:",
            display_fn=lambda r: f"{r.name}  {r.description[:60]}" if r.description else r.name,
            name_key=lambda r: r.name,
            timeout=120,
            label="autoskillit fleet campaign",
        )
        if selected is None or isinstance(selected, str):
            print("No campaign selected.")
            sys.exit(1)
        campaign_name = selected.name

    elif campaign_name is None and resume_campaign is not None:
        campaign_name, resume_campaign = _pick_resume_campaign(Path.cwd())

    if campaign_name is None:
        raise RuntimeError("campaign_name must be set before launching fleet session")

    from autoskillit.core import YAMLError
    from autoskillit.fleet import (
        FLEET_HALTED_SENTINEL,
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
    fleet_dir = Path.cwd() / ".autoskillit" / "temp" / "fleet"

    if resume_campaign is not None:
        campaign_id = resume_campaign
        state_path = fleet_dir / campaign_id / "state.json"
        resume_metadata = resume_campaign_from_state(state_path, parsed.continue_on_failure)
        if resume_metadata is None:
            print(f"ERROR: Campaign state not found or corrupted for '{campaign_id}'")
            sys.exit(1)
        if resume_metadata.completed_dispatches_block == FLEET_HALTED_SENTINEL:
            print(
                f"ERROR: Campaign '{campaign_id}' halted on dispatch failure. "
                "Set 'continue_on_failure: true' in the campaign recipe to resume past failures.",
                file=sys.stderr,
            )
            sys.exit(1)
    else:
        campaign_id = uuid4().hex[:16]
        state_dir = fleet_dir / campaign_id
        state_dir.mkdir(parents=True, exist_ok=True)
        state_path = state_dir / "state.json"
        dispatches = [DispatchRecord(name=d.name) for d in parsed.dispatches]
        write_initial_state(state_path, campaign_id, campaign_name, str(match.path), dispatches)

    from autoskillit.cli._preview import _pre_launch_campaign  # noqa: PLC0415

    _itable, proceed = _pre_launch_campaign(
        campaign_name, parsed, match, Path.cwd(), is_resume=resume_campaign is not None
    )
    if not proceed:
        return

    _launch_fleet_session(
        parsed,
        campaign_id,
        state_path,
        resume_metadata,
        fleet_mode="campaign",
        ingredients_table=_itable,
    )


@fleet_app.command(name="list")
def fleet_list() -> None:
    """List available campaign recipes."""
    from autoskillit.config import load_config

    cfg = load_config(Path.cwd())
    _require_fleet(cfg)

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


@fleet_app.command(name="status")
def fleet_status(
    campaign_id: str | None = None,
    *,
    cleanup: bool = False,
    reap: bool = False,
    dry_run: bool = False,
    watch: bool = False,
    json_output: Annotated[bool, Parameter(name=["--json"])] = False,
) -> None:
    """Show fleet campaign status."""
    from autoskillit.config import load_config

    cfg = load_config(Path.cwd())
    _require_fleet(cfg)
    from autoskillit.fleet import read_state  # noqa: PLC0415

    fleet_dir = Path.cwd() / ".autoskillit" / "temp" / "fleet"

    if campaign_id is not None:
        state_path = fleet_dir / campaign_id / "state.json"
        state = read_state(state_path)
        if state is None:
            print(f"ERROR: Campaign '{campaign_id}' not found or state corrupted.")
            sys.exit(3)

        if json_output:
            totals = _aggregate_totals(state)
            data = {
                "campaign_id": state.campaign_id,
                "campaign_name": state.campaign_name,
                "started_at": state.started_at,
                "dispatches": [d.to_dict() for d in state.dispatches],
                "totals": totals,
            }
            print(json.dumps(data))
            _cross_check_tokens(state, totals)
            sys.exit(_compute_exit_code(state))

        if watch and cleanup:
            print("ERROR: --watch and --cleanup are mutually exclusive.")
            sys.exit(3)

        if watch:
            sys.exit(_watch_loop(state_path))

        _render_status_display(state)

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
                for d in state.dispatches:
                    if d.l3_session_id:
                        skill_mgr.cleanup_session(d.l3_session_id)
                skill_mgr.cleanup_session(campaign_id)
            except Exception:
                logger.warning(
                    "Session skill cleanup failed for campaign %s", campaign_id, exc_info=True
                )
            sweep_stale_markers()
            kitchen_state_dir = (
                Path.cwd() / ".autoskillit" / "temp" / "kitchen_state" / campaign_id
            )
            if kitchen_state_dir.is_dir():
                shutil.rmtree(kitchen_state_dir, ignore_errors=True)
            print(f"Cleanup complete for campaign '{campaign_id}'.")

        if reap or dry_run:
            _reap_stale_dispatches(state_path, dry_run=dry_run)

        totals = _aggregate_totals(state)
        _cross_check_tokens(state, totals)
        sys.exit(_compute_exit_code(state))

    else:
        from autoskillit.core import _render_terminal_table

        if not fleet_dir.exists():
            print("No campaigns found.")
            return

        subdirs = [d for d in fleet_dir.iterdir() if d.is_dir()]
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
        rows_list = []
        for subdir in sorted(subdirs):
            s = read_state(subdir / "state.json")
            if s is None:
                continue
            started = datetime.fromtimestamp(s.started_at, tz=UTC).strftime("%Y-%m-%d %H:%M UTC")
            rows_list.append((s.campaign_name, s.campaign_id, str(len(s.dispatches)), started))

        if not rows_list:
            print("No campaigns found.")
            return

        print(_render_terminal_table(columns, rows_list))
