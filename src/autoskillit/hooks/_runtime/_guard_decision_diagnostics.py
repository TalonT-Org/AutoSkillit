"""Bounded, privacy-preserving hook-decision diagnostics."""

from __future__ import annotations

import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

if __package__:
    from .._session_binding import atomic_write, binding_lock, resolve_channel_dir
    from ._hook_payload import normalize_payload_cwd, resolve_state_root
else:
    from _hook_payload import (  # type: ignore[import-not-found,no-redef]
        normalize_payload_cwd,
        resolve_state_root,
    )
    from _session_binding import (  # type: ignore[import-not-found,no-redef]
        atomic_write,
        binding_lock,
        resolve_channel_dir,
    )

_DECISION_FILE = "guard_decisions.jsonl"
_MAX_FILE_BYTES = 1024 * 1024
_MAX_RECORDS = 1000
_MAX_SESSION_ID_CHARS = 128
# Whitelisted guards allowed to emit decision records. The set is intentionally
# narrow (a hidden producer/consumer coupling): adding a new guard requires
# updating this frozenset in lockstep with the producer, otherwise records are
# silently dropped. See ``record_guard_decision`` for the producer contract.
_ALLOWED_GUARDS = frozenset({"write_guard", "skill_load_post_hook"})
_ALLOWED_ACTIVATION_SOURCES = frozenset({"inactive", "headless", "skill_post_hook"})
_ALLOWED_SCOPES = frozenset({"none", "workspace", "write_prefix", "session_binding"})
_ALLOWED_DECISIONS = frozenset({"allow", "deny"})
_ALLOWED_REASONS = frozenset(
    {
        "not_headless",
        "codex",
        "no_scope",
        "malformed_input",
        "tool_exempt",
        "scope_violation",
        "in_scope",
        "binding_write_failed",
    }
)


def _decision_path(data: object) -> Path:
    payload = data if isinstance(data, dict) else {}
    cwd = normalize_payload_cwd(payload.get("cwd"))
    return resolve_channel_dir(resolve_state_root(cwd)) / _DECISION_FILE


def _session_id(data: object) -> str:
    if not isinstance(data, dict):
        return ""
    value = data.get("session_id")
    return value[:_MAX_SESSION_ID_CHARS] if isinstance(value, str) else ""


def _backend() -> str:
    backend = os.environ.get("AUTOSKILLIT_AGENT_BACKEND")
    if backend == "codex":
        return "codex"
    if backend in {"claude", "claude_code"}:
        return "claude_code"
    return "unknown"


def _retained_records(path: Path, line: bytes) -> bytes:
    """Return the newest complete JSONL records within both retention limits."""
    size = 0
    try:
        size = path.stat().st_size
        with path.open("rb") as handle:
            if size > _MAX_FILE_BYTES:
                handle.seek(-_MAX_FILE_BYTES, os.SEEK_END)
            existing = handle.read(_MAX_FILE_BYTES)
    except FileNotFoundError:
        existing = b""

    lines = existing.splitlines()
    if size > _MAX_FILE_BYTES and lines:
        lines = lines[1:]
    lines.append(line)
    lines = lines[-_MAX_RECORDS:]
    while lines and len(b"\n".join(lines) + b"\n") > _MAX_FILE_BYTES:
        lines.pop(0)
    return b"\n".join(lines) + b"\n" if lines else b""


def record_guard_decision(
    data: object,
    *,
    guard: str,
    activation_source: str,
    scope: str,
    decision: str,
    reason: str,
) -> None:
    """Best-effort append of a fixed-shape decision record.

    Callers pass only fixed vocabulary values. The payload contributes just the
    session ID required to correlate hook activity, never tool input or env
    values. A recording failure is intentionally invisible to enforcement.
    """
    if (
        guard not in _ALLOWED_GUARDS
        or activation_source not in _ALLOWED_ACTIVATION_SOURCES
        or scope not in _ALLOWED_SCOPES
        or decision not in _ALLOWED_DECISIONS
        or reason not in _ALLOWED_REASONS
    ):
        return
    record = {
        "ts": datetime.now(UTC).isoformat(),
        "guard": guard,
        "session_id": _session_id(data),
        "backend": _backend(),
        "activation_source": activation_source,
        "scope": scope,
        "decision": decision,
        "reason": reason,
    }
    try:
        encoded = json.dumps(record, separators=(",", ":"), sort_keys=True).encode("utf-8")
        path = _decision_path(data)
        with binding_lock(path):
            atomic_write(path, _retained_records(path, encoded).decode("utf-8"))
    except (OSError, TimeoutError, TypeError, ValueError) as exc:
        # Recording failures must remain invisible to enforcement, but a
        # persistently silent failure (binding_lock contention, channel-dir
        # perm denial) should be diagnosable. Surface to stderr so operators
        # can correlate missing decision records with environmental causes.
        # This file is listed in tests/arch/_rules._PRINT_EXEMPT (alongside
        # _hook_settings.py) because the stdlib-only import boundary
        # precludes autoskillit.core.get_logger.
        print(
            f"guard_decision_record_failed: guard={guard!r} error={exc!r}",
            file=sys.stderr,
        )
