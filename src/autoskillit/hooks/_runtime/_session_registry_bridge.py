"""Stdlib-only session-registry bridge and hook applicability authority."""

from __future__ import annotations

import fcntl
import json
import os
import tempfile
import time
from pathlib import Path

if __package__:
    from .._session_binding import read_session_binding
    from ._hook_payload import resolve_state_root
else:
    from _hook_payload import resolve_state_root  # type: ignore[import-not-found,no-redef]
    from _session_binding import (  # type: ignore[import-not-found,no-redef]
        read_session_binding,
    )

_REGISTRY_LOCK_TIMEOUT_SECONDS = 2.0
_LOCK_RETRY_INTERVAL_SECONDS = 0.01


def _registry_path(payload_cwd: str) -> Path:
    return resolve_state_root(payload_cwd) / ".autoskillit" / "temp" / "session_registry.json"


def _acquire_registry_lock(fd: int) -> None:
    """Acquire the registry lock without delaying a hook indefinitely."""
    deadline = time.monotonic() + _REGISTRY_LOCK_TIMEOUT_SECONDS
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise
            time.sleep(min(_LOCK_RETRY_INTERVAL_SECONDS, remaining))
        else:
            return


def _load_bridge_registry(registry_file: Path) -> dict[str, object] | None:
    try:
        registry = json.loads(registry_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return registry if isinstance(registry, dict) else None


def _claim_session_id(registry: dict[str, object], launch_id: str, session_id: str) -> bool:
    row = registry.get(launch_id)
    if not isinstance(row, dict):
        return False
    existing_session_id = row.get("claude_session_id")
    if existing_session_id == session_id:
        return False
    if existing_session_id is not None:
        raise ValueError(
            f"Launch {launch_id!r} is already bound to session "
            f"{existing_session_id!r}; cannot bind {session_id!r}"
        )
    for existing_launch_id, existing_row in registry.items():
        if existing_launch_id == launch_id or not isinstance(existing_row, dict):
            continue
        if existing_row.get("claude_session_id") == session_id:
            raise ValueError(
                f"Session {session_id!r} is already claimed by launch "
                f"{existing_launch_id!r}; cannot assign it to launch {launch_id!r}"
            )
    row["claude_session_id"] = session_id
    return True


def _write_registry(registry_file: Path, registry: dict[str, object]) -> None:
    target = registry_file.parent / "session_registry.json"
    fd, tmp = tempfile.mkstemp(dir=registry_file.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(json.dumps(registry))
        os.replace(tmp, target)
    except (OSError, TypeError, ValueError):
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _bridge_locked_registry(registry_file: Path, launch_id: str, session_id: str) -> None:
    registry = _load_bridge_registry(registry_file)
    if registry is None or not _claim_session_id(registry, launch_id, session_id):
        return
    _write_registry(registry_file, registry)


def bridge_session_registry(
    session_id: str,
    payload_cwd: str = "",
    *,
    launch_id: str,
) -> None:
    """Bind the selected launch row to one unique native session identity."""
    if not launch_id or not session_id:
        return

    registry_file = _registry_path(payload_cwd)
    if not registry_file.is_file():
        return
    registry_file.parent.mkdir(parents=True, exist_ok=True)
    lock_fd = os.open(
        str(registry_file.with_suffix(".lock")),
        os.O_CREAT | os.O_RDWR | os.O_CLOEXEC,
        0o644,
    )
    locked = False
    try:
        _acquire_registry_lock(lock_fd)
        locked = True
        _bridge_locked_registry(registry_file, launch_id, session_id)
    finally:
        try:
            if locked:
                try:
                    fcntl.flock(lock_fd, fcntl.LOCK_UN)
                except OSError:
                    pass
        finally:
            os.close(lock_fd)


def _object_without_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key!r}")
        result[key] = value
    return result


def _read_registry(payload_cwd: str) -> dict[str, dict[str, object]] | None:
    try:
        decoded = json.loads(
            _registry_path(payload_cwd).read_text(encoding="utf-8"),
            object_pairs_hook=_object_without_duplicate_keys,
        )
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return None
    if not isinstance(decoded, dict):
        return None
    registry: dict[str, dict[str, object]] = {}
    for launch_id, row in decoded.items():
        if not isinstance(launch_id, str) or not launch_id or not isinstance(row, dict):
            return None
        native_id = row.get("claude_session_id")
        if native_id is not None and (not isinstance(native_id, str) or not native_id):
            return None
        registry[launch_id] = row
    return registry


def is_authenticated_top_level_cook(
    payload: dict[str, object],
    payload_cwd: str,
    binding_session_id: str,
    *,
    headless: bool = False,
    backend: str = "",
    launch_id: str = "",
    managed_parent_id: str = "",
) -> bool:
    """Apply payload identity before checking the authenticated cook session."""
    if payload.get("agent_id"):
        return False
    if backend != "codex" and (
        not isinstance(payload.get("session_id"), str)
        or payload["session_id"] != binding_session_id
    ):
        return False
    return is_authenticated_top_level_cook_session(
        payload_cwd,
        binding_session_id,
        headless=headless,
        backend=backend,
        launch_id=launch_id,
        managed_parent_id=managed_parent_id,
    )


def is_authenticated_top_level_cook_session(
    payload_cwd: str,
    binding_session_id: str,
    *,
    headless: bool,
    backend: str,
    launch_id: str,
    managed_parent_id: str,
) -> bool:
    """Return whether durable state authenticates the top-level cook session."""
    if headless or not payload_cwd or not binding_session_id:
        return False

    registry = _read_registry(payload_cwd)
    if registry is None:
        return False

    if backend == "codex":
        if (
            not managed_parent_id
            or binding_session_id != managed_parent_id
            or (launch_id and launch_id != managed_parent_id)
        ):
            return False
        row = registry.get(managed_parent_id)
        if not isinstance(row, dict) or row.get("session_type") != "cook":
            return False
        binding = read_session_binding(payload_cwd, binding_session_id)
        return bool(
            binding is not None
            and binding.get("managed_parent_id") == managed_parent_id
            and binding.get("managed_route") in {"parent", "interactive-parent"}
            and binding.get("managed_leaf_id") == ""
        )

    if not launch_id:
        return False
    matches = [
        candidate_launch_id
        for candidate_launch_id, row in registry.items()
        if row.get("claude_session_id") == binding_session_id
    ]
    if matches != [launch_id]:
        return False
    return registry[launch_id].get("session_type") == "cook"
