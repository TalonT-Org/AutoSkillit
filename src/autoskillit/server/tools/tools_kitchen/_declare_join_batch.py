"""declare_join_batch tool: opens one join-ledger wave for a skill's direct children."""

from __future__ import annotations

import json
import os
from pathlib import Path

from fastmcp import Context
from fastmcp.dependencies import CurrentContext

from autoskillit.core import get_logger
from autoskillit.execution import get_backend
from autoskillit.hooks._hook_settings import _validate_session_id, write_join_diagnostic
from autoskillit.hooks._join_ledger import JoinLedgerError, declare_batch
from autoskillit.hooks._session_binding import (
    SessionBindingError,
    enumerate_binding_paths,
    normalize_skill_name,
    read_binding,
    resolve_binding_path,
)
from autoskillit.server import mcp
from autoskillit.server._notify import track_response_size
from autoskillit.server.tools._cancellation_shield import _cancellation_shield

logger = get_logger(__name__)


def _declare_join_batch_handler(
    skill_name: str,
    assignments: list[str],
    session_id: str,
    project_root: Path,
    top_level_parent: str | None = None,
) -> dict[str, object]:
    """Core logic for the declare_join_batch tool — testable without FastMCP."""
    # Imports are hoisted to module level (see top of file).

    try:
        _validate_session_id(session_id)
    except ValueError as exc:
        return {"success": False, "error": str(exc)}

    normalized_skill_name = normalize_skill_name(skill_name)
    binding_path = resolve_binding_path(str(project_root), session_id)
    channel_dir = binding_path.parent
    channel_dir.mkdir(parents=True, exist_ok=True)
    try:
        binding = read_binding(binding_path)
    except SessionBindingError:
        binding = None

    # Fail-closed validation: a valid, join-bearing session binding, a loaded
    # skill entry, and the backend's fixed-set-join capability must all line
    # up before we open a wave.
    if binding is None:
        wrong_session_error = _wrong_session_error(channel_dir, session_id)
        if wrong_session_error is not None:
            return {"success": False, "error": wrong_session_error}
        return {
            "success": False,
            "error": "declare_join_batch requires a valid session binding",
        }
    if binding.session_id != session_id:
        _emit_join_diagnostic(
            {
                "gate": "declare_join_batch",
                "session_id": session_id,
                "status": "wrong_session_id",
            }
        )
        return {
            "success": False,
            "error": _session_mismatch_error(session_id, binding.session_id),
        }
    if not binding.binding_valid:
        return {
            "success": False,
            "error": "declare_join_batch requires a valid session binding",
        }
    selected_entry = next(
        (
            entry
            for entry in reversed(binding.loaded_skills)
            if entry.skill_name == normalized_skill_name
        ),
        None,
    )
    if selected_entry is None:
        return {
            "success": False,
            "error": (
                f"declare_join_batch: skill {normalized_skill_name!r} "
                "is not loaded in this session"
            ),
        }
    if not selected_entry.join_required:
        return {
            "success": False,
            "error": (f"declare_join_batch: skill {normalized_skill_name!r} is not join-bearing"),
        }
    backend_name = (
        os.environ.get("AUTOSKILLIT_AGENT_BACKEND", "claude-code").strip() or "claude-code"
    )
    backend = None
    try:
        backend = get_backend(backend_name)
    except (ImportError, AttributeError, ValueError, RuntimeError, OSError):
        logger.warning("declare_join_batch_backend_lookup_failed", exc_info=True)
        backend = None
    if backend is None or not getattr(backend.capabilities, "fixed_set_join_capable", False):
        return {
            "success": False,
            "error": (
                f"declare_join_batch: backend {backend_name!r} does not attest "
                "fixed_set_join_capable"
            ),
        }
    # Check backend supports the requested assignments against the manifest's
    # declared child_spawn_cardinality.
    manifest_cardinality = selected_entry.child_spawn_cardinality
    # Sum declared per-role counts. Any string entry (for_each) makes the
    # total indeterminate, so we skip the strict check in that case.
    declared_count: int | None = None
    has_static_count = all(isinstance(v, int) for v in manifest_cardinality.values())
    if has_static_count and manifest_cardinality:
        declared_count = sum(manifest_cardinality.values())  # type: ignore[arg-type]
    if declared_count is not None:
        if len(assignments) != declared_count:
            return {
                "success": False,
                "error": (
                    f"declare_join_batch: skill {normalized_skill_name!r} declares "
                    f"count={declared_count}; received {len(assignments)} assignments"
                ),
            }

    artifact_digest = binding.artifact_digest
    if not artifact_digest:
        return {
            "success": False,
            "error": "declare_join_batch requires a non-empty top-level artifact_digest",
        }
    parent = top_level_parent or "top_level"
    try:
        batch = declare_batch(
            channel_dir,
            session_id=session_id,
            top_level_parent=parent,
            skill_name=normalized_skill_name,
            artifact_digest=artifact_digest,
            assignments=assignments,
        )
    except JoinLedgerError as exc:
        _emit_join_diagnostic(
            {
                "gate": "declare_join_batch",
                "session_id": session_id,
                "top_level_parent": parent,
                "skill_name": normalized_skill_name,
                "status": "declare_refused",
            }
        )
        return {"success": False, "error": str(exc)}
    _emit_join_diagnostic(
        {
            "gate": "declare_join_batch",
            "session_id": session_id,
            "top_level_parent": parent,
            "join_batch_id": batch.get("join_batch_id", ""),
            "skill_name": normalized_skill_name,
            "status": "declared",
        }
    )
    return {"success": True, "join_batch_id": batch.get("join_batch_id"), "wave": batch}


def _session_mismatch_error(requested_session_id: str, recorded_session_id: str) -> str:
    return (
        "declare_join_batch session mismatch: "
        f"requested {requested_session_id!r}, recorded {recorded_session_id!r}"
    )


def _wrong_session_error(channel_dir: Path, requested_session_id: str) -> str | None:
    recorded_session_ids: set[str] = set()
    for candidate_path in enumerate_binding_paths(channel_dir):
        try:
            candidate = read_binding(candidate_path)
        except SessionBindingError:
            continue
        if candidate is not None and candidate.session_id != requested_session_id:
            recorded_session_ids.add(candidate.session_id)
    if not recorded_session_ids:
        return None

    status = "wrong_session_id" if len(recorded_session_ids) == 1 else "ambiguous_session_bindings"
    _emit_join_diagnostic(
        {
            "gate": "declare_join_batch",
            "session_id": requested_session_id,
            "status": status,
        }
    )
    if len(recorded_session_ids) == 1:
        recorded_session_id = next(iter(recorded_session_ids))
        return _session_mismatch_error(requested_session_id, recorded_session_id)
    return (
        "declare_join_batch session mismatch: "
        f"requested {requested_session_id!r}, but multiple recorded bindings are ambiguous"
    )


def _emit_join_diagnostic(record: dict[str, object]) -> None:
    """Bounded MCP-side diagnostic emission. Falls back to stderr on failure.

    ``write_join_diagnostic`` already redacts to ``DIAGNOSTIC_KEYS``; the
    caller passes the raw record and lets the canonical filter run.
    """
    try:
        write_join_diagnostic(record, caller="declare_join_batch")
    except (ImportError, AttributeError, ValueError, RuntimeError, OSError) as exc:
        logger.warning(
            "declare_join_batch_diagnostic_emission_failed",
            exc_info=True,
            error=str(exc),
        )


@mcp.tool(
    tags={"autoskillit"},
    annotations={"readOnlyHint": False},
    meta={"anthropic/alwaysLoad": False},
)
@_cancellation_shield()
@track_response_size("declare_join_batch")
async def declare_join_batch(
    skill_name: str,
    assignments: list[str],
    session_id: str,
    top_level_parent: str | None = None,
    ctx: Context = CurrentContext(),
) -> str:
    """Open one declared batch ledger for the next wave of direct children.

    Validates that the loaded skill, the session flag binding, and the
    artifact identity are all consistent. Returns the new ``join_batch_id``
    on success; a structured refusal on conflict.

    Never raises.
    """
    try:
        from autoskillit.server import _get_ctx  # circular-break

        tool_ctx = _get_ctx()
        result = _declare_join_batch_handler(
            skill_name=skill_name,
            assignments=assignments,
            session_id=session_id,
            project_root=tool_ctx.project_dir,
            top_level_parent=top_level_parent,
        )
    except Exception as exc:
        logger.error("declare_join_batch unhandled exception", exc_info=True)
        return json.dumps({"success": False, "error": f"{type(exc).__name__}: {exc}"})
    return json.dumps(result, sort_keys=True)
