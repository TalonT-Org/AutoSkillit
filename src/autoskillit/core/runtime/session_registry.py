"""Session registry: maps autoskillit launch IDs to Claude Code session UUIDs.

Written at interactive session launch; bridged on open_kitchen hook fire.
Read by the scoped resume picker to classify sessions by type.
"""

from __future__ import annotations

import fcntl
import json
import logging
import os
import tempfile
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from .._json import fast_dumps as _fast_dumps
from ._linux_proc import (
    _is_valid_identity,
    owner_liveness,
    read_boot_id,
    read_starttime_ticks,
)
from .artifact_lease import ARTIFACT_LEASE_TIMEOUT_SECONDS, acquire_flock_with_timeout

logger = logging.getLogger(__name__)  # noqa: TID251 — IL-0 runtime is stdlib-only

__all__ = [
    "registry_path",
    "read_registry",
    "write_registry_entry",
    "claim_launch_for_session",
    "release_session_claim",
    "bind_session_owner",
    "bridge_claude_session_id",
]


def registry_path(project_dir: Path) -> Path:
    """Return .autoskillit/temp/session_registry.json path."""
    return project_dir / ".autoskillit" / "temp" / "session_registry.json"


def read_registry(project_dir: Path) -> dict[str, dict]:
    """Read registry. Returns {} on missing file or malformed JSON."""
    path = registry_path(project_dir)
    try:
        registry = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return registry if isinstance(registry, dict) else {}


@contextmanager
def _registry_lock(path: Path) -> Iterator[None]:
    """Serialize mutations of one session registry."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_suffix(".lock").open("w") as lock_file:
        acquire_flock_with_timeout(
            lock_file.fileno(),
            operation=fcntl.LOCK_EX,
            timeout=ARTIFACT_LEASE_TIMEOUT_SECONDS,
            path=path.with_suffix(".lock"),
        )
        yield


def write_registry_entry(
    project_dir: Path,
    launch_id: str,
    session_type: str,
    recipe_name: str | None,
    *,
    claude_session_id: str | None = None,
) -> None:
    """Atomically add/update entry keyed by launch_id.

    Fresh rows reserve the calling CLI identity. A same-row retry preserves an
    already recorded conversation and child owner, while a live or unavailable
    reservation/child observation refuses replacement.
    """
    if claude_session_id is not None:
        _validate_session_id(claude_session_id)
    path = registry_path(project_dir)
    with _registry_lock(path):
        registry = _read_registry_for_mutation(path)
        previous = registry.get(launch_id)
        previous_session_id = _row_session_id(previous) if previous is not None else None
        requested_session_id = (
            claude_session_id if claude_session_id is not None else previous_session_id
        )
        stored_session_id = _check_session_assignment(
            registry,
            launch_id,
            requested_session_id,
            previous_session_id,
        )
        entry = dict(previous) if previous is not None else {}
        entry.update(
            {
                "session_type": session_type,
                "launched_at": datetime.now(UTC).isoformat(),
                "recipe_name": recipe_name,
                "claude_session_id": stored_session_id,
            }
        )
        _reserve_calling_cli(entry)
        registry[launch_id] = entry
        _atomic_write(path, _fast_dumps(registry))


def claim_launch_for_session(
    project_dir: Path,
    *,
    claude_session_id: str,
    session_type: str,
    recipe_name: str | None,
) -> str:
    """Return the launch id owning ``claude_session_id``, reserving it for this CLI.

    The sidecar lock serializes registry writes but cannot freeze observed
    kernel state. A live or unavailable claimant/child identity therefore
    refuses this claim rather than being guessed dead.
    """
    _validate_session_id(claude_session_id)
    path = registry_path(project_dir)
    with _registry_lock(path):
        registry = _read_registry_for_mutation(path)
        matches = _claiming_launch_ids(registry, claude_session_id)
        if len(matches) > 1:
            raise ValueError(
                f"Session {claude_session_id!r} is claimed by multiple launches: "
                f"{sorted(matches)!r}"
            )
        if matches:
            launch_id = matches[0]
            entry = registry[launch_id]
        else:
            launch_id = uuid.uuid4().hex[:16]
            entry = {
                "session_type": session_type,
                "launched_at": datetime.now(UTC).isoformat(),
                "recipe_name": recipe_name,
                "claude_session_id": claude_session_id,
            }

        _reserve_calling_cli(entry)
        registry[launch_id] = entry
        _atomic_write(path, _fast_dumps(registry))
    return launch_id


def bridge_claude_session_id(
    project_dir: Path,
    launch_id: str,
    claude_session_id: str,
) -> None:
    """Update entry for launch_id with the Claude Code session UUID.

    No-op if launch_id not found. This is the reference behavior for the
    stdlib-only hook bridge parity contract.
    """
    _validate_session_id(claude_session_id)
    path = registry_path(project_dir)
    with _registry_lock(path):
        registry = _read_registry_for_mutation(path)

        if launch_id not in registry:
            return

        entry = registry[launch_id]
        existing_session_id = _row_session_id(entry)
        _check_session_assignment(
            registry,
            launch_id,
            claude_session_id,
            existing_session_id,
        )
        entry["claude_session_id"] = claude_session_id
        _atomic_write(path, _fast_dumps(registry))


def bind_session_owner(project_dir: Path, launch_id: str, owner_pid: int) -> bool:
    """Bind an existing launch row to the exact spawned client process.

    The caller must have already reserved the claimant on ``launch_id`` via
    ``write_registry_entry`` or ``claim_launch_for_session``; this function
    returns ``False`` (rather than raising) when that claimant does not match
    the current process identity.

    Return ``False`` when the registry or process identity cannot be read or
    persisted.  A non-positive or non-integer PID remains a caller error.
    """
    if isinstance(owner_pid, bool) or not isinstance(owner_pid, int) or owner_pid <= 0:
        raise ValueError("owner_pid must be a positive integer")

    try:
        caller_identity = _current_identity()
        owner_identity = _identity_for_pid(owner_pid)
        if caller_identity is None or owner_identity is None:
            return False

        path = registry_path(project_dir)
        with _registry_lock(path):
            registry = _read_registry_for_mutation(path)

            if launch_id not in registry:
                return False

            entry = registry[launch_id]
            if _stored_identity(entry, "claimant") != caller_identity:
                return False

            stored_owner = _stored_identity(entry, "owner")
            if stored_owner == owner_identity:
                return True
            if _stored_identity_liveness(entry, "owner") is not False:
                return False

            entry.update(_identity_fields("owner", owner_identity))
            _atomic_write(path, _fast_dumps(registry))
    except (OSError, ValueError):
        logger.warning("session owner binding failed", exc_info=True)
        return False
    return True


def release_session_claim(project_dir: Path, launch_id: str) -> bool:
    """Clear only this CLI's still-current reservation for ``launch_id``."""
    caller_identity = _current_identity()
    if caller_identity is None:
        return False
    path = registry_path(project_dir)
    try:
        with _registry_lock(path):
            registry = _read_registry_for_mutation(path)
            entry = registry.get(launch_id)
            if entry is None or _stored_identity(entry, "claimant") != caller_identity:
                return False
            for key in _identity_fields("claimant", caller_identity):
                entry.pop(key, None)
            _atomic_write(path, _fast_dumps(registry))
    except (OSError, ValueError):
        logger.warning("session claim release failed", exc_info=True)
        return False
    return True


def _read_registry_for_mutation(path: Path) -> dict[str, dict]:
    """Read mutation state strictly so a corrupt registry is never overwritten."""
    try:
        registry = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError as error:
        raise ValueError("session registry is malformed") from error
    if not isinstance(registry, dict) or not all(
        isinstance(launch_id, str) and isinstance(entry, dict)
        for launch_id, entry in registry.items()
    ):
        raise ValueError("session registry is malformed")
    for entry in registry.values():
        session_id = entry.get("claude_session_id")
        if session_id is not None and (not isinstance(session_id, str) or not session_id):
            raise ValueError("session registry is malformed")
    return registry


def _validate_session_id(session_id: str) -> None:
    if not isinstance(session_id, str) or not session_id:
        raise ValueError("claude_session_id must be a nonempty string")


def _row_session_id(entry: dict | None) -> str | None:
    if entry is None:
        return None
    session_id = entry.get("claude_session_id")
    return session_id if isinstance(session_id, str) else None


def _claiming_launch_ids(registry: dict[str, dict], session_id: str) -> list[str]:
    return [
        launch_id
        for launch_id, entry in registry.items()
        if entry.get("claude_session_id") == session_id
    ]


def _check_session_assignment(
    registry: dict[str, dict],
    launch_id: str,
    requested_session_id: str | None,
    existing_session_id: str | None,
) -> str | None:
    """Preserve a row's session identity and reject duplicate ownership."""
    if requested_session_id is None:
        return existing_session_id
    if existing_session_id is not None and existing_session_id != requested_session_id:
        raise ValueError(
            f"Launch {launch_id!r} is already bound to session {existing_session_id!r}; "
            f"cannot bind {requested_session_id!r}"
        )

    matches = _claiming_launch_ids(registry, requested_session_id)
    if len(matches) > 1:
        raise ValueError(
            f"Session {requested_session_id!r} is claimed by multiple launches: "
            f"{sorted(matches)!r}"
        )
    if matches and matches[0] != launch_id:
        raise ValueError(
            f"Session {requested_session_id!r} is already claimed by launch {matches[0]!r}; "
            f"cannot assign it to launch {launch_id!r}"
        )
    return requested_session_id


def _identity_for_pid(pid: int) -> tuple[int, str, int] | None:
    boot_id = read_boot_id()
    starttime_ticks = read_starttime_ticks(pid)
    if not _is_valid_identity(pid, boot_id, starttime_ticks):
        return None
    assert isinstance(boot_id, str) and isinstance(starttime_ticks, int)
    return pid, boot_id, starttime_ticks


def _current_identity() -> tuple[int, str, int] | None:
    return _identity_for_pid(os.getpid())


def _identity_fields(prefix: str, identity: tuple[int, str, int]) -> dict[str, int | str]:
    pid, boot_id, starttime_ticks = identity
    return {
        f"{prefix}_pid": pid,
        f"{prefix}_boot_id": boot_id,
        f"{prefix}_starttime_ticks": starttime_ticks,
    }


def _stored_identity(entry: dict, prefix: str) -> tuple[int, str, int] | None:
    pid = entry.get(f"{prefix}_pid")
    boot_id = entry.get(f"{prefix}_boot_id")
    starttime_ticks = entry.get(f"{prefix}_starttime_ticks")
    if not _is_valid_identity(pid, boot_id, starttime_ticks):
        return None
    assert isinstance(pid, int) and isinstance(boot_id, str) and isinstance(starttime_ticks, int)
    return pid, boot_id, starttime_ticks


def _stored_identity_liveness(entry: dict, prefix: str) -> bool | None:
    values = (
        entry.get(f"{prefix}_pid"),
        entry.get(f"{prefix}_boot_id"),
        entry.get(f"{prefix}_starttime_ticks"),
    )
    if values == (None, None, None):
        return False
    identity = _stored_identity(entry, prefix)
    if identity is None:
        return None
    return owner_liveness(*identity)


def _reserve_calling_cli(entry: dict) -> None:
    caller_identity = _current_identity()
    if caller_identity is None:
        raise ValueError("calling CLI identity is unavailable")

    stored_claimant = _stored_identity(entry, "claimant")
    if (
        stored_claimant != caller_identity
        and _stored_identity_liveness(entry, "claimant") is not False
    ):
        raise ValueError("launch is reserved by a live or unavailable claimant")
    if _stored_identity_liveness(entry, "owner") is not False:
        raise ValueError("launch has a live or unavailable child owner")
    entry.update(_identity_fields("claimant", caller_identity))


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
