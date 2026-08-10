"""MCP tool: record_pipeline_step — pipeline step tracker init, status, and complete."""

from __future__ import annotations

import fcntl
import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

import regex as re

from autoskillit.core import DISPATCH_ID_ENV_VAR, atomic_write, get_logger
from autoskillit.server import mcp
from autoskillit.server._guards import _require_enabled
from autoskillit.server._misc import (
    _pipeline_tracker_dir,
    _pipeline_tracker_path,
)
from autoskillit.server._notify import track_response_size
from autoskillit.server.tools._cancellation_shield import _cancellation_shield
from autoskillit.server.tools._overlay_state import read_overlay
from autoskillit.server.tools._types import deny_envelope

logger = get_logger(__name__)

_STEP_SUFFIX_RE = re.compile(r"-\d+$")


class _TrackerCtx(Protocol):
    """Minimal ToolContext duck-type — avoids circular import with tools_execution.py."""

    kitchen_id: str
    project_dir: Path


@dataclass(frozen=True, slots=True)
class ResolvedTracker:
    """Successfully resolved tracker file."""

    order_id: str
    path: Path


@dataclass(frozen=True, slots=True)
class ResolutionRefusal:
    """Tracker resolution failed — carry a reason for the caller to wrap."""

    reason: str
    multi_pipeline: bool = False


def resolve_tracker_order_id(
    tool_ctx: _TrackerCtx, order_id: str
) -> ResolvedTracker | ResolutionRefusal:
    """Resolve the effective tracker order_id with three-tier precedence.

    1. Explicit ``order_id`` parameter
    2. ``AUTOSKILLIT_DISPATCH_ID`` environment variable
    3. Kitchen-scoped fallback via internal ``kitchen_id`` field scan

    Shared by ``_check_pipeline_deps`` (enforcement reader) and the
    adjudication-point marker (writer) so they can never disagree on
    which tracker file to target.
    """
    effective_oid = order_id or os.environ.get(DISPATCH_ID_ENV_VAR, "")
    kitchen_id = tool_ctx.kitchen_id
    project_dir = tool_ctx.project_dir

    if not effective_oid:
        if not kitchen_id:
            return ResolutionRefusal(reason="no order_id and no kitchen_id")
        tracker_dir = _pipeline_tracker_dir(project_dir)
        if not tracker_dir.is_dir():
            return ResolutionRefusal(reason="tracker directory does not exist")
        active: set[str] = set()
        for path in tracker_dir.glob("*.json"):
            if path.stem == kitchen_id:
                continue
            try:
                tracker = json.loads(path.read_text())
            except (OSError, json.JSONDecodeError):
                continue
            if tracker.get("kitchen_id") == kitchen_id:
                active.add(path.stem)
        if len(active) > 1:
            return ResolutionRefusal(
                reason=(
                    f"multiple pipelines are active under this kitchen "
                    f"({sorted(active)}). Pass order_id explicitly to scope "
                    "the dependency check."
                ),
                multi_pipeline=True,
            )
        effective_oid = next(iter(active)) if active else kitchen_id
    tracker_path = _pipeline_tracker_path(project_dir, effective_oid)
    return ResolvedTracker(order_id=effective_oid, path=tracker_path)


def _resolve_skipped_steps(project_dir: Path, pipeline_id: str) -> set[str]:
    try:
        overlay = read_overlay(project_dir)
        pid_locks = overlay.get("locked_steps", {}).get(pipeline_id, {})
        return {s for s, v in pid_locks.items() if v is False}
    except OSError:
        return set()


def _build_tracker_steps(
    active_steps: dict[str, object], skipped: set[str]
) -> dict[str, dict[str, str]]:
    return {
        name: {"status": "skipped"} if name in skipped else {"status": "pending"}
        for name in active_steps
    }


def _compute_status_counts(
    steps: dict[str, dict[str, object]], dependencies: dict[str, list[str]]
) -> dict[str, int]:
    return {
        "complete": sum(1 for s in steps.values() if s.get("status") == "complete"),
        "pending": sum(1 for s in steps.values() if s.get("status") == "pending"),
        "skipped": sum(1 for s in steps.values() if s.get("status") == "skipped"),
        "blocked": sum(
            1
            for sname, sdata in steps.items()
            if sdata.get("status") == "pending" and sname in dependencies
        ),
    }


@mcp.tool(tags={"autoskillit", "kitchen", "kitchen-core"}, annotations={"readOnlyHint": True})
@_cancellation_shield()
@track_response_size("record_pipeline_step")
async def record_pipeline_step(
    pipeline_id: str = "",
    op: str = "status",
    dependencies: dict[str, list[str]] | None = None,
    step_name: str = "",
) -> str:
    """Initialize, query, or mark completion on the pipeline step tracker.

    **op="init"**: Creates the tracker file with the server-authoritative step list
    from the currently open recipe. The LLM provides the dependency graph at init time.
    Idempotent — calling init twice with the same pipeline_id returns an error.

    **op="status"**: Returns the current state of all tracked steps.

    **op="complete"**: Marks a tracked step as complete. Requires ``step_name``
    parameter. Canonicalizes retry suffixes (e.g. ``rectify-2`` → ``rectify``).
    Operator repair tool — do not use to bypass running a prerequisite skill.

    Requires the kitchen to be open. Never raises.
    """
    if (gate := _require_enabled()) is not None:
        return gate

    try:
        effective_pipeline_id = pipeline_id or os.environ.get(DISPATCH_ID_ENV_VAR, "")

        from autoskillit.server import (  # circular-break
            _get_ctx,
        )  # circular-break: server-internal circular dependency

        ctx = _get_ctx()

        if op == "complete":
            return _handle_complete(ctx, effective_pipeline_id, step_name)

        if not effective_pipeline_id:
            return json.dumps(
                deny_envelope(
                    "record_pipeline_step: pipeline_id is required. "
                    "Pass pipeline_id explicitly or set "
                    "AUTOSKILLIT_DISPATCH_ID in the environment.",
                    stage="preflight:pipeline_tracker",
                    retriable=False,
                )
            )

        tracker_path = _pipeline_tracker_path(ctx.project_dir, effective_pipeline_id)

        if op == "init":
            return _handle_init(ctx, tracker_path, effective_pipeline_id, dependencies)

        if op == "status":
            return _handle_status(tracker_path, effective_pipeline_id)

        return json.dumps(
            deny_envelope(
                f"record_pipeline_step: unknown op '{op}'. Use 'init', 'status', or 'complete'.",
                stage="preflight:pipeline_tracker",
                retriable=False,
            )
        )
    except Exception:
        logger.exception("record_pipeline_step_unexpected_error")
        return json.dumps(
            {
                "success": False,
                "is_error": True,
                "error": "record_pipeline_step: unexpected internal error.",
            }
        )


def _handle_init(
    ctx: object,
    tracker_path: Path,
    effective_pipeline_id: str,
    dependencies: dict[str, list[str]] | None,
) -> str:
    if tracker_path.exists():
        return json.dumps(
            {
                "success": False,
                "is_error": True,
                "error": (
                    f"record_pipeline_step: pipeline '{effective_pipeline_id}' "
                    "has already been initialized."
                ),
            }
        )

    active_steps = ctx.active_recipe_steps  # type: ignore[attr-defined]
    if active_steps is None:
        return json.dumps(
            {
                "success": False,
                "is_error": True,
                "error": (
                    "record_pipeline_step: no active recipe steps found. "
                    "Ensure the kitchen is open with a loaded recipe."
                ),
            }
        )

    skipped = _resolve_skipped_steps(ctx.project_dir, effective_pipeline_id)  # type: ignore[attr-defined]
    steps = _build_tracker_steps(active_steps, skipped)

    tracker_data = {
        "pipeline_id": effective_pipeline_id,
        "kitchen_id": ctx.kitchen_id,  # type: ignore[attr-defined]
        "initialized_at": datetime.now(UTC).isoformat(),
        "steps": steps,
        "dependencies": dependencies or {},
    }

    tracker_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(tracker_path, json.dumps(tracker_data))

    return json.dumps(
        {
            "success": True,
            "pipeline_id": effective_pipeline_id,
            "step_count": len(steps),
            "dependency_count": len(dependencies or {}),
        }
    )


def _handle_status(tracker_path: Path, effective_pipeline_id: str) -> str:
    if not tracker_path.exists():
        return json.dumps(
            {
                "success": False,
                "is_error": True,
                "error": (
                    f"record_pipeline_step: no tracker found for pipeline "
                    f"'{effective_pipeline_id}'. Initialize with op='init' first."
                ),
            }
        )

    try:
        tracker_data = json.loads(tracker_path.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        return json.dumps(
            {
                "success": False,
                "is_error": True,
                "error": f"record_pipeline_step: failed to read tracker: {exc}",
            }
        )

    steps = tracker_data.get("steps", {})
    deps = tracker_data.get("dependencies", {})
    counts = _compute_status_counts(steps, deps)

    return json.dumps(
        {
            "success": True,
            "pipeline_id": effective_pipeline_id,
            "steps": steps,
            "dependencies": deps,
            **counts,
            "total": len(steps),
        }
    )


def _handle_complete(ctx: _TrackerCtx, effective_pipeline_id: str, step_name: str) -> str:
    if not step_name:
        return json.dumps(
            deny_envelope(
                "record_pipeline_step: step_name is required for op='complete'.",
                stage="preflight:pipeline_tracker",
                retriable=False,
            )
        )

    resolved = resolve_tracker_order_id(ctx, effective_pipeline_id)
    if isinstance(resolved, ResolutionRefusal):
        return json.dumps(
            deny_envelope(
                f"record_pipeline_step: cannot resolve pipeline tracker: {resolved.reason}",
                stage="preflight:pipeline_tracker",
                retriable=False,
            )
        )
    if not resolved.path.exists():
        return json.dumps(
            deny_envelope(
                f"record_pipeline_step: no tracker found for pipeline "
                f"'{resolved.order_id}'. Initialize with op='init' first.",
                stage="preflight:pipeline_tracker",
                retriable=False,
            )
        )

    result = mark_step_complete(resolved.path, step_name, resolved.order_id)
    return json.dumps(result)


def mark_step_complete(
    tracker_path: Path,
    step_name: str,
    order_id: str,
) -> dict:
    """Mark a single step as complete in the tracker file.

    Used by both ``op="complete"`` (operator repair) and the adjudication-point
    marker in ``run_skill``. Returns a result dict (always includes ``success``).
    """
    canonical = _STEP_SUFFIX_RE.sub("", step_name)
    lock_path = tracker_path.parent / ".pipeline_tracker.lock"

    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock_fd = os.open(str(lock_path), os.O_RDWR | os.O_CREAT, 0o644)
    except OSError as exc:
        return deny_envelope(
            f"mark_step_complete: failed to open lock file: {exc}",
            stage="mark_step_complete",
            retriable=True,
        )

    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
    except OSError as exc:
        os.close(lock_fd)
        return deny_envelope(
            f"mark_step_complete: failed to acquire lock: {exc}",
            stage="mark_step_complete",
            retriable=True,
        )

    try:
        if not tracker_path.exists():
            return deny_envelope(
                f"mark_step_complete: tracker file disappeared: {tracker_path}",
                stage="mark_step_complete",
                retriable=False,
            )
        try:
            tracker = json.loads(tracker_path.read_text())
        except (json.JSONDecodeError, OSError) as exc:
            return deny_envelope(
                f"mark_step_complete: failed to read tracker: {exc}",
                stage="mark_step_complete",
                retriable=False,
            )

        steps = tracker.get("steps", {})
        if canonical not in steps:
            return deny_envelope(
                f"mark_step_complete: step '{canonical}' not found in tracker. "
                f"Known steps: {sorted(steps.keys())}",
                stage="mark_step_complete",
                retriable=False,
            )

        steps[canonical]["status"] = "complete"
        steps[canonical]["completed_at"] = datetime.now(UTC).isoformat()
        tracker["steps"] = steps
        atomic_write(tracker_path, json.dumps(tracker))

        done = sum(1 for s in steps.values() if s.get("status") in ("complete", "skipped"))
        total = len(steps)
        pipeline_id = tracker.get("pipeline_id", order_id)

        dependencies = tracker.get("dependencies", {})
        dependents_with_unmet = sorted(
            dependent
            for dependent, prereqs in dependencies.items()
            if canonical in prereqs
            and any(
                steps.get(prereq, {}).get("status") not in ("complete", "skipped")
                for prereq in prereqs
            )
        )

        result = {
            "success": True,
            "step": canonical,
            "order_id": order_id,
            "status": "complete",
            "pipeline_id": pipeline_id,
            "done": done,
            "total": total,
        }
        if dependents_with_unmet:
            result["advisory"] = (
                f"Step '{canonical}' marked complete but dependent steps "
                f"{dependents_with_unmet} still show unmet prerequisites — verify correctness"
            )
        return result
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)
