"""Stdlib-only bounded JSONL sinks for hook subprocesses.

Decomposed from ``_hook_settings.py`` so that module stays under the
REQ-CNST-010 750 non-import line hard cap. Owns the atomic-write
helper plus the JSONL sinks for ``quota_events.jsonl``,
``join_diagnostics.jsonl``, and ``hook_dispatch_diagnostics.jsonl``.

The two ``write_*_diagnostic`` helpers resolve the log directory via a
function-local import of ``_hook_settings.resolve_quota_log_dir`` so the
load-time import cycle stays one-directional (``_hook_settings`` imports
this module, not the other way around).
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path


def _atomic_write_marker(marker_path: Path, payload: str) -> None:
    """Atomic write for a small JSON marker (mirrors recipe_confirmed_post_hook pattern)."""
    marker_path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(marker_path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(payload)
        os.replace(tmp, str(marker_path))
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


#: Oldest-first line bound applied on every write to either JSONL sink in this module.
#: Duplicated (not imported) from core.runtime._reclamation.append_and_trim_jsonl's shape --
#: this module is stdlib-only with no autoskillit.* imports by design, same
#: boundary that already duplicates HOOK_CONFIG_PATH_COMPONENTS instead of
#: sharing it in _hook_settings.
_MAX_HOOK_LOG_LINES = 5000


def _append_and_trim_jsonl_line(path: Path, line: str, *, max_lines: int) -> None:
    existing: list[str] = []
    if path.exists():
        existing = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    existing.append(line)
    if max_lines > 0 and len(existing) > max_lines:
        existing = existing[-max_lines:]
    _atomic_write_marker(path, "\n".join(existing) + "\n")


def write_quota_log_event(event: dict, log_dir: Path | None, *, caller: str = "") -> None:
    """Append a quota event to quota_events.jsonl at the log root, bounded to
    _MAX_HOOK_LOG_LINES.

    No-ops when ``log_dir`` is None. On write failure, prints to stderr when
    ``caller`` is provided; otherwise silently returns.
    """
    if log_dir is None:
        return
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        _append_and_trim_jsonl_line(
            log_dir / "quota_events.jsonl", json.dumps(event), max_lines=_MAX_HOOK_LOG_LINES
        )
    except Exception as exc:
        if caller:
            print(f"{caller}: failed to write quota log event: {exc}", file=sys.stderr)


#: Bounded set of allowed join-diagnostic record keys. Anything else is
#: stripped before write so child bodies, prompts, secrets, and private
#: task IDs never land in the diagnostic sink.
DIAGNOSTIC_KEYS: frozenset[str] = frozenset(
    {
        "ts",
        "session_id",
        "top_level_parent",
        "join_batch_id",
        "original_join_batch_id",
        "replacement_join_batch_id",
        "assignment",
        "tool_use_id",
        "tool_name",
        "skill_name",
        "semantic_digest",
        "adaptation_digest",
        "artifact_digest",
        "artifact_incarnation",
        "source_artifact_digest",
        "source_artifact_incarnation_id",
        "managed_parent_id",
        "managed_leaf_id",
        "assignment_id",
        "attempt_id",
        "run_id",
        "terminal_event_id",
        "terminal_payload_digest",
        "lifecycle_state",
        "selector_presence",
        "activation_source",
        "launch_policy_state",
        "status",
        "public_child_id",
        "team_name",
        "execution_mode",
        "wave_outcome",
        "gate",
        "binding_valid",
    }
)


def write_join_diagnostic(record: dict, *, caller: str = "") -> None:
    """Append one bounded join-gate diagnostic record to ``join_diagnostics.jsonl``.

    The record is redacted to ``DIAGNOSTIC_KEYS`` before write.
    Child bodies, prompts, secrets, and private task IDs are never persisted.
    No-ops when the resolved log dir is None.
    """
    # Function-local import keeps the load-time cycle one-directional:
    # _hook_settings imports this module, not the other way around.
    import _hook_settings  # type: ignore[import-not-found]  # noqa: PLC0415

    bounded = {key: value for key, value in record.items() if key in DIAGNOSTIC_KEYS}
    bounded.setdefault("ts", datetime.now(UTC).isoformat())
    log_dir = _hook_settings.resolve_quota_log_dir(caller=caller or "join_diagnostic")
    if log_dir is None:
        return
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        _append_and_trim_jsonl_line(
            log_dir / "join_diagnostics.jsonl",
            json.dumps(bounded, sort_keys=True),
            max_lines=_MAX_HOOK_LOG_LINES,
        )
    except Exception as exc:
        if caller:
            print(f"{caller}: failed to write join diagnostic: {exc}", file=sys.stderr)


def write_dispatch_diagnostic(
    event_kind: str,
    logical_hook_name: str,
    reason: str,
) -> None:
    """Append one bounded dispatcher-degradation record without masking the hook."""
    # Function-local import keeps the load-time cycle one-directional.
    import _hook_settings  # type: ignore[import-not-found]  # noqa: PLC0415

    record = {
        "ts": datetime.now(UTC).isoformat(),
        "event_kind": str(event_kind)[:64],
        "logical_hook_name": str(logical_hook_name)[:256],
        "reason": str(reason)[:512],
    }
    log_dir = _hook_settings.resolve_quota_log_dir(caller="hook_dispatch")
    if log_dir is None:
        return
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        _append_and_trim_jsonl_line(
            log_dir / "hook_dispatch_diagnostics.jsonl",
            json.dumps(record, sort_keys=True),
            max_lines=_MAX_HOOK_LOG_LINES,
        )
    except Exception:
        return
