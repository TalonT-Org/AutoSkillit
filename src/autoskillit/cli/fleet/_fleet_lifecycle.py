"""Fleet CLI lifecycle helpers — stale dispatch reaping CLI wrapper and campaign picker."""

from __future__ import annotations

import sys
from pathlib import Path

from autoskillit.core import get_logger

logger = get_logger(__name__)


def _reap_stale_dispatches(state_path: Path, *, dry_run: bool = False) -> None:
    from autoskillit.fleet._dispatch_reaper import reap_stale_dispatches  # noqa: PLC0415

    reap_stale_dispatches(state_path, dry_run=dry_run)


def _pick_resume_campaign(project_dir: Path) -> tuple[str, str]:
    """Interactively pick a resumable campaign. Returns (campaign_name, campaign_id) or exits."""
    from autoskillit.cli.ui._menu import run_selection_menu  # noqa: PLC0415
    from autoskillit.fleet import TERMINAL_DISPATCH_STATUSES, read_state  # noqa: PLC0415

    fleet_dir = project_dir / ".autoskillit" / "temp" / "fleet"
    active = []
    if fleet_dir.is_dir():
        for subdir in sorted(fleet_dir.iterdir()):
            if not subdir.is_dir():
                continue
            state = read_state(subdir / "state.json")
            if state is None:
                continue
            if any(d.status not in TERMINAL_DISPATCH_STATUSES for d in state.dispatches):
                active.append(state)

    if not active:
        print("No active campaigns to resume.")
        sys.exit(1)

    selected = run_selection_menu(
        active,
        header="Active campaigns (resumable):",
        display_fn=lambda s: f"{s.campaign_name}  [{(s.campaign_id or '')[:8]}…]",
        name_key=lambda s: s.campaign_name,
        timeout=120,
        label="autoskillit fleet campaign --resume",
    )
    if selected is None or isinstance(selected, str):
        print("No campaign selected.")
        sys.exit(1)
    return selected.campaign_name, selected.campaign_id  # type: ignore[union-attr]
