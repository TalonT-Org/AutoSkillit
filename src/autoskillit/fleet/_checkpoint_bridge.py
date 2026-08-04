"""Bridge fleet progress and launch evidence to core contracts.

Two progress sources feed into SessionCheckpoint:
- Sidecar (issue-level): completed issue URLs from IssueSidecarEntry
- Pipeline tracker (step-level): completed recipe steps from tracker JSON
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from autoskillit.core import ResolvedLaunchContract, SessionCheckpoint, get_logger
from autoskillit.fleet.sidecar import IssueSidecarEntry, read_sidecar_from_path
from autoskillit.fleet.state import CampaignStateMutator
from autoskillit.fleet.state_types import DispatchStatus

if TYPE_CHECKING:
    from autoskillit.pipeline.context import ToolContext

logger = get_logger(__name__)


def bind_dispatch_launch_contract(
    state_path: Path,
    dispatch_name: str,
    launch_contract: ResolvedLaunchContract,
) -> None:
    """Persist exact pre-spawn launch evidence for one dispatch attempt."""
    with CampaignStateMutator(state_path) as mutator:
        if mutator.state is None:
            raise FileNotFoundError(f"State file not found or corrupted: {state_path}")
        for dispatch in mutator.state.dispatches:
            if dispatch.name != dispatch_name:
                continue
            if dispatch.status not in {DispatchStatus.PENDING, DispatchStatus.RUNNING}:
                raise ValueError(
                    f"cannot bind launch contract while dispatch {dispatch.name!r} "
                    f"is {dispatch.status.value!r}"
                )
            previous = dispatch.launch_contract
            if previous is not None:
                stable_fields = (
                    ("launch_contract_digest", previous.digest, launch_contract.digest),
                    ("surface", previous.surface, launch_contract.surface),
                    (
                        "backend_authority",
                        previous.backend_authority,
                        launch_contract.backend_authority,
                    ),
                    ("backend", previous.effective_backend, launch_contract.effective_backend),
                    (
                        "semantic_digest",
                        previous.semantic_digest,
                        launch_contract.semantic_digest,
                    ),
                    (
                        "projection_digest",
                        previous.projection_digest,
                        launch_contract.projection_digest,
                    ),
                    ("cwd", previous.cwd, launch_contract.cwd),
                )
                for field_name, old_value, new_value in stable_fields:
                    if old_value != new_value:
                        raise ValueError(
                            f"fleet launch {field_name} drifted across physical attempts"
                        )
            dispatch.backend_name = launch_contract.effective_backend
            dispatch.backend_authority = launch_contract.backend_authority
            dispatch.launch_contract = launch_contract
            dispatch.launch_contract_digest = launch_contract.digest
            mutator.mark_dirty()
            return
        raise ValueError(f"Dispatch '{dispatch_name}' not found in state")


def checkpoint_from_sidecar(
    entries: list[IssueSidecarEntry], *, backend_name: str, skill_name: str
) -> SessionCheckpoint:
    completed = [e.issue_url for e in entries if e.status == "completed"]
    ts = entries[-1].ts if entries else ""
    return SessionCheckpoint(
        completed_items=completed,
        step_name="fleet_dispatch",
        progress_pct=0.0,
        ts=ts,
        backend_name=backend_name,
        skill_name=skill_name,
    )


def checkpoint_from_tracker(
    tracker_data: dict[str, Any] | None, *, backend_name: str, skill_name: str
) -> SessionCheckpoint | None:
    if tracker_data is None:
        return None
    steps = tracker_data.get("steps", {})
    if not isinstance(steps, dict):
        return None
    completed = [
        name
        for name, info in steps.items()
        if isinstance(info, dict) and info.get("status") == "complete"
    ]
    if not completed:
        return None
    completed_with_ts = [(name, steps[name].get("completed_at", "")) for name in completed]
    completed_with_ts.sort(key=lambda x: x[1])
    last_step_name = completed_with_ts[-1][0]
    last_ts = completed_with_ts[-1][1]
    return SessionCheckpoint(
        completed_items=[name for name, _ in completed_with_ts],
        step_name=last_step_name,
        progress_pct=len(completed) / len(steps),
        ts=last_ts,
        backend_name=backend_name,
        skill_name=skill_name,
    )


def load_dispatch_progress(
    *,
    tool_ctx: ToolContext,
    dispatch_sidecar_path: str,
    dispatch_id: str,
    backend_name: str,
    recipe: str,
) -> tuple[Path, list[IssueSidecarEntry], SessionCheckpoint | None]:
    """Load sidecar/tracker progress and choose the authoritative checkpoint."""
    sidecar_file = Path(dispatch_sidecar_path)
    sidecar_entries: list[IssueSidecarEntry] = []
    dispatch_checkpoint: SessionCheckpoint | None = None
    if sidecar_file.exists():
        sidecar_entries = read_sidecar_from_path(sidecar_file).entries
        if sidecar_entries:
            dispatch_checkpoint = checkpoint_from_sidecar(
                sidecar_entries,
                backend_name=backend_name,
                skill_name=recipe,
            )

    tracker_checkpoint: SessionCheckpoint | None = None
    tracker_path = (
        tool_ctx.project_dir / ".autoskillit" / "temp" / "pipeline_tracker" / f"{dispatch_id}.json"
    )
    if tracker_path.exists():
        try:
            tracker_data = json.loads(tracker_path.read_text())
            tracker_checkpoint = checkpoint_from_tracker(
                tracker_data,
                backend_name=backend_name,
                skill_name=recipe,
            )
        except (OSError, ValueError, TypeError, AttributeError):
            logger.debug("tracker read failed for %s", dispatch_id, exc_info=True)

    if dispatch_checkpoint is None and tracker_checkpoint is not None:
        dispatch_checkpoint = tracker_checkpoint
    return sidecar_file, sidecar_entries, dispatch_checkpoint
