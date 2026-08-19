"""declare_join_batch tool: opens one join-ledger wave for a skill's direct children."""

from __future__ import annotations

import json
import os
from pathlib import Path

from fastmcp import Context
from fastmcp.dependencies import CurrentContext

from autoskillit.core import get_logger
from autoskillit.execution import get_backend
from autoskillit.hooks._hook_settings import write_join_diagnostic
from autoskillit.hooks._join_ledger import JoinLedgerError, declare_batch, resolve_flag_dir
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

    flag_dir = resolve_flag_dir(project_root)
    flag_dir.mkdir(parents=True, exist_ok=True)
    flag_path = flag_dir / f"skill_guard_{session_id}.flag"
    binding: dict[str, object] = {}
    if flag_path.exists():
        try:
            binding = json.loads(flag_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, ValueError, OSError):
            binding = {}
    if not isinstance(binding, dict):
        binding = {}

    # Fail-closed validation: a join-bearing session binding, a loaded skill
    # entry, and the backend's fixed-set-join capability must all line up
    # before we open a wave.
    if not bool(binding.get("join_required", False)):
        return {
            "success": False,
            "error": "declare_join_batch requires a join-bearing session binding",
        }
    loaded = binding.get("loaded_skills", [])
    if not isinstance(loaded, list) or not any(
        isinstance(entry, dict) and entry.get("skill_name") == skill_name for entry in loaded
    ):
        return {
            "success": False,
            "error": f"declare_join_batch: skill {skill_name!r} is not loaded in this session",
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
    manifest_cardinality: dict[str, object] = {}
    for entry in loaded:
        if isinstance(entry, dict) and entry.get("skill_name") == skill_name:
            card = entry.get("child_spawn_cardinality", {})
            if isinstance(card, dict):
                manifest_cardinality = card
            break
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
                    f"declare_join_batch: skill {skill_name!r} declares "
                    f"count={declared_count}; received {len(assignments)} assignments"
                ),
            }

    artifact_digest = str(binding.get("artifact_digest", "")) or _derive_artifact_digest(binding)
    parent = top_level_parent or "top_level"
    try:
        batch = declare_batch(
            flag_dir,
            session_id=session_id,
            top_level_parent=parent,
            skill_name=skill_name,
            artifact_digest=artifact_digest,
            assignments=assignments,
        )
    except JoinLedgerError as exc:
        _emit_join_diagnostic(
            {
                "gate": "declare_join_batch",
                "session_id": session_id,
                "top_level_parent": parent,
                "skill_name": skill_name,
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
            "skill_name": skill_name,
            "status": "declared",
        }
    )
    return {"success": True, "join_batch_id": batch.get("join_batch_id"), "wave": batch}


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


def _derive_artifact_digest(binding: dict[str, object]) -> str:
    """Reconstruct the artifact digest for the most recent loaded skill."""
    loaded = binding.get("loaded_skills", [])
    if isinstance(loaded, list) and loaded:
        last = loaded[-1]
        if isinstance(last, dict):
            candidate = last.get("artifact_digest")
            if isinstance(candidate, str) and candidate:
                return candidate
    return ""


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
