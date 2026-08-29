"""Shared authority for the cross-process session-binding channel.

This stdlib-only module is imported both as ``_session_binding`` by hook
subprocesses and as ``autoskillit.hooks._session_binding`` by in-venv callers.
It has no mutable module-level state, and the two import identities exchange
only serialized JSON and filesystem paths, never Python class instances.
"""

from __future__ import annotations

import errno
import fcntl
import json
import os
import tempfile
import time
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import NamedTuple

_FLOCK_TIMEOUT_S = 5.0
_FLOCK_POLL_INTERVAL_S = 0.05

if __package__:
    from . import _hook_payload as _hook_payload_module
else:
    import _hook_payload as _hook_payload_module  # type: ignore[import-not-found,no-redef]


SESSION_BINDING_SCHEMA_VERSION: int = 3
PROJECTION_MANIFEST_SCHEMA_VERSION: int = 2

_BINDING_CANDIDATE_LIMIT = 20
_CANONICAL_SKILL_PREFIX = "autoskillit:"
_BINDING_LOCK_SUFFIX = ".lock"


class SessionBindingError(Exception):
    """Raised when a binding or projection manifest violates its schema."""


def normalize_skill_name(skill_name: str) -> str:
    """Return the bare name used by projection manifests and join records."""
    if skill_name.startswith(_CANONICAL_SKILL_PREFIX):
        return skill_name[len(_CANONICAL_SKILL_PREFIX) :]
    return skill_name


def _json_object(value: str | bytes | bytearray | dict[str, object]) -> dict[str, object]:
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(value)
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        raise SessionBindingError(f"invalid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise SessionBindingError("JSON top level must be an object")
    return parsed


def _cardinality(value: object) -> dict[str, int | str]:
    if not isinstance(value, dict):
        raise SessionBindingError("child_spawn_cardinality must be an object")
    result: dict[str, int | str] = {}
    for key, item in value.items():
        if not isinstance(key, str) or isinstance(item, bool) or not isinstance(item, (int, str)):
            raise SessionBindingError(
                "child_spawn_cardinality must map strings to integers or strings"
            )
        result[key] = item
    return result


def _string_field(value: dict[str, object], field: str) -> str:
    item = value.get(field, "")
    if not isinstance(item, str):
        raise SessionBindingError(f"{field} must be a string")
    return item


def _managed_guard_set(value: object) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
        raise SessionBindingError("managed_guard_set must be an array of non-empty strings")
    if len(set(value)) != len(value):
        raise SessionBindingError("managed_guard_set must not contain duplicates")
    return tuple(sorted(value))


def _managed_route(value: object) -> str:
    if value not in ("", "parent", "leaf"):
        raise SessionBindingError("managed_route must be empty, parent, or leaf")
    return str(value)


class LoadedSkillEntry(NamedTuple):
    skill_name: str
    ts: str
    join_required: bool
    child_spawn_cardinality: dict[str, int | str]
    semantic_digest: str
    adaptation_digest: str
    projected_digest: str
    canonical_digest: str
    source_artifact_digest: str
    source_artifact_incarnation_id: str
    binding_valid: bool
    binding_error: str | None

    def _as_json_object(self) -> dict[str, object]:
        return {
            "skill_name": self.skill_name,
            "ts": self.ts,
            "join_required": self.join_required,
            "child_spawn_cardinality": self.child_spawn_cardinality,
            "semantic_digest": self.semantic_digest,
            "adaptation_digest": self.adaptation_digest,
            "projected_digest": self.projected_digest,
            "canonical_digest": self.canonical_digest,
            "source_artifact_digest": self.source_artifact_digest,
            "source_artifact_incarnation_id": self.source_artifact_incarnation_id,
            "binding_valid": self.binding_valid,
            "binding_error": self.binding_error,
        }

    def to_json(self) -> str:
        return json.dumps(self._as_json_object(), sort_keys=True)

    @classmethod
    def from_json(
        cls,
        value: str | bytes | bytearray | dict[str, object],
    ) -> LoadedSkillEntry:
        return _loaded_skill_from_mapping(_json_object(value))


def _loaded_skill_from_mapping(value: dict[str, object]) -> LoadedSkillEntry:
    error = value.get("binding_error")
    if error is not None and not isinstance(error, str):
        raise SessionBindingError("binding_error must be a string or null")
    return LoadedSkillEntry(
        skill_name=normalize_skill_name(_string_field(value, "skill_name")),
        ts=_string_field(value, "ts"),
        join_required=bool(value.get("join_required", False)),
        child_spawn_cardinality=_cardinality(value.get("child_spawn_cardinality", {})),
        semantic_digest=_string_field(value, "semantic_digest"),
        adaptation_digest=_string_field(value, "adaptation_digest"),
        projected_digest=_string_field(value, "projected_digest"),
        canonical_digest=_string_field(value, "canonical_digest"),
        source_artifact_digest=_string_field(value, "source_artifact_digest"),
        source_artifact_incarnation_id=_string_field(value, "source_artifact_incarnation_id"),
        binding_valid=bool(value.get("binding_valid", False)),
        binding_error=error,
    )


class SessionBinding(NamedTuple):
    schema_version: int
    session_id: str
    join_required: bool
    binding_valid: bool
    artifact_digest: str
    loaded_skills: tuple[LoadedSkillEntry, ...]
    managed_parent_id: str = "top_level"
    managed_leaf_id: str = ""
    managed_route: str = ""
    managed_guard_set: tuple[str, ...] = ()
    managed_config_digest: str = ""

    def to_json(self) -> str:
        return json.dumps(
            {
                "schema_version": self.schema_version,
                "session_id": self.session_id,
                "join_required": self.join_required,
                "binding_valid": self.binding_valid,
                "artifact_digest": self.artifact_digest,
                "loaded_skills": [entry._as_json_object() for entry in self.loaded_skills],
                "managed_parent_id": self.managed_parent_id,
                "managed_leaf_id": self.managed_leaf_id,
                "managed_route": self.managed_route,
                "managed_guard_set": list(self.managed_guard_set),
                "managed_config_digest": self.managed_config_digest,
            },
            sort_keys=True,
        )

    @classmethod
    def from_json(
        cls,
        value: str | bytes | bytearray | dict[str, object],
    ) -> SessionBinding:
        parsed = _json_object(value)
        schema_version = parsed.get("schema_version")
        loaded_raw = parsed.get("loaded_skills", [])
        if not isinstance(loaded_raw, list):
            raise SessionBindingError("loaded_skills must be an array")
        if not all(isinstance(entry, dict) for entry in loaded_raw):
            raise SessionBindingError("loaded_skills entries must be objects")

        if schema_version == SESSION_BINDING_SCHEMA_VERSION:
            loaded = tuple(_loaded_skill_from_mapping(entry) for entry in loaded_raw)
            return cls(
                schema_version=SESSION_BINDING_SCHEMA_VERSION,
                session_id=str(parsed.get("session_id", "")),
                join_required=bool(parsed.get("join_required", False)),
                binding_valid=bool(parsed.get("binding_valid", False)),
                artifact_digest=str(parsed.get("artifact_digest", "")),
                loaded_skills=loaded,
                managed_parent_id=_string_field(parsed, "managed_parent_id"),
                managed_leaf_id=_string_field(parsed, "managed_leaf_id"),
                managed_route=_managed_route(parsed.get("managed_route", "")),
                managed_guard_set=_managed_guard_set(parsed.get("managed_guard_set", [])),
                managed_config_digest=_string_field(parsed, "managed_config_digest"),
            )
        raise SessionBindingError(
            f"unsupported session-binding schema_version: {schema_version!r}"
        )


def resolve_channel_dir(anchor: Path) -> Path:
    """Return the normalized directory shared by bindings and the join ledger."""
    resolved = anchor.resolve()
    for candidate in (resolved, *resolved.parents):
        state_dir = candidate / ".autoskillit"
        if state_dir.is_dir():
            return state_dir / "temp"
    return resolved / ".autoskillit" / "temp"


def resolve_binding_path(payload_cwd: str, session_id: str) -> Path:
    if not session_id:
        raise SessionBindingError("session_id must be a non-empty string")
    state_root = _hook_payload_module.resolve_state_root(
        _hook_payload_module.normalize_payload_cwd(payload_cwd)
    )
    return resolve_channel_dir(state_root) / f"skill_guard_{session_id}.flag"


def binding_lock_path(path: Path) -> Path:
    """Return the sibling lock that serializes binding snapshots and writes."""
    return path.with_name(f"{path.name}{_BINDING_LOCK_SUFFIX}")


@contextmanager
def binding_lock(path: Path) -> Generator[None, None, None]:
    """Hold the binding lock.

    Managed callers acquire this lock before opening the join ledger and retain
    it through the ledger mutation.  That fixed ordering prevents a selected
    source entry from being mixed with a later binding rewrite.
    """
    lock_path = binding_lock_path(path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR | os.O_CLOEXEC, 0o600)
    try:
        # Bounded deadline retry on LOCK_NB contention — mirrors
        # _codex_config_lock.py and the _join_ledger._flock helper.
        deadline = time.monotonic() + _FLOCK_TIMEOUT_S
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as exc:
                if exc.errno not in {errno.EACCES, errno.EAGAIN}:
                    raise
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(
                        f"timed out acquiring binding_lock on {lock_path} "
                        f"after {_FLOCK_TIMEOUT_S:.3f}s"
                    ) from exc
                time.sleep(min(_FLOCK_POLL_INTERVAL_S, remaining))
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            pass
        os.close(fd)


@contextmanager
def binding_snapshot(path: Path) -> Generator[SessionBinding | None, None, None]:
    """Yield a locked binding snapshot for a subsequent ledger operation."""
    with binding_lock(path):
        yield read_binding(path)


def enumerate_binding_paths(channel_dir: Path) -> tuple[Path, ...]:
    """Return a bounded, sorted snapshot of binding candidates for diagnostics."""
    try:
        candidates = sorted(channel_dir.glob("skill_guard_*.flag"))
    except OSError:
        return ()
    return tuple(candidates[:_BINDING_CANDIDATE_LIMIT])


def resolve_projection_manifest_path(hook_file: Path) -> Path | None:
    """Resolve the live projection sidecar from the installed hook location."""
    resolved = hook_file.resolve()
    for candidate in (resolved.parent, *resolved.parents):
        if candidate.name != "hooks":
            continue
        plugin_root = candidate.parent
        manifest = plugin_root.parent / (f".{plugin_root.name}.autoskillit-projection.json")
        return manifest if manifest.is_file() else None
    return None


def read_manifest(path: Path) -> dict[str, object]:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SessionBindingError(f"projection manifest is unreadable: {exc}") from exc
    parsed = _json_object(raw)
    schema_version = parsed.get("schema_version")
    if schema_version != PROJECTION_MANIFEST_SCHEMA_VERSION:
        raise SessionBindingError(
            f"unsupported projection manifest schema_version: {schema_version!r}"
        )
    if not isinstance(parsed.get("artifact_digest"), str):
        raise SessionBindingError("projection manifest artifact_digest must be a string")
    if not isinstance(parsed.get("incarnation_id"), str):
        raise SessionBindingError("projection manifest incarnation_id must be a string")
    if not isinstance(parsed.get("skills"), dict):
        raise SessionBindingError("projection manifest skills must be an object")
    return parsed


def loaded_skill_from_manifest(
    manifest: dict[str, object],
    skill_name: str,
    ts: str,
) -> LoadedSkillEntry:
    skill_name = normalize_skill_name(skill_name)
    skills = manifest.get("skills")
    raw = skills.get(skill_name) if isinstance(skills, dict) else None
    if not isinstance(raw, dict):
        raise SessionBindingError(f"manifest entry not found for skill {skill_name!r}")
    return LoadedSkillEntry(
        skill_name=skill_name,
        ts=ts,
        join_required=bool(raw.get("join_required", False)),
        child_spawn_cardinality=_cardinality(raw.get("child_spawn_cardinality", {})),
        semantic_digest=str(raw.get("semantic_digest", "")),
        adaptation_digest=str(raw.get("adaptation_digest", "")),
        projected_digest=str(raw.get("projected_digest", "")),
        canonical_digest=str(raw.get("canonical_digest", "")),
        source_artifact_digest=str(manifest["artifact_digest"]),
        source_artifact_incarnation_id=str(manifest["incarnation_id"]),
        binding_valid=True,
        binding_error=None,
    )


def unresolved_loaded_skill(
    skill_name: str,
    ts: str,
    error: str,
) -> LoadedSkillEntry:
    skill_name = normalize_skill_name(skill_name)
    return LoadedSkillEntry(
        skill_name=skill_name,
        ts=ts,
        join_required=True,
        child_spawn_cardinality={},
        semantic_digest="",
        adaptation_digest="",
        projected_digest="",
        canonical_digest="",
        source_artifact_digest="",
        source_artifact_incarnation_id="",
        binding_valid=False,
        binding_error=error,
    )


def merge_binding(
    existing: SessionBinding | None,
    *,
    session_id: str,
    new_entry: LoadedSkillEntry,
    artifact_digest: str,
    managed_parent_id: str = "top_level",
    managed_leaf_id: str = "",
    managed_route: str = "",
    managed_guard_set: tuple[str, ...] = (),
    managed_config_digest: str = "",
) -> SessionBinding:
    loaded = (*existing.loaded_skills, new_entry) if existing else (new_entry,)
    return SessionBinding(
        schema_version=SESSION_BINDING_SCHEMA_VERSION,
        session_id=session_id,
        join_required=(existing.join_required if existing else False) or new_entry.join_required,
        binding_valid=(existing.binding_valid if existing else True) and new_entry.binding_valid,
        artifact_digest=(
            artifact_digest
            if new_entry.binding_valid
            else (existing.artifact_digest if existing else "")
        ),
        loaded_skills=loaded,
        managed_parent_id=(
            existing.managed_parent_id if existing is not None else managed_parent_id
        ),
        managed_leaf_id=existing.managed_leaf_id if existing is not None else managed_leaf_id,
        managed_route=existing.managed_route
        if existing is not None
        else _managed_route(managed_route),
        managed_guard_set=(
            existing.managed_guard_set
            if existing is not None
            else _managed_guard_set(list(managed_guard_set))
        ),
        managed_config_digest=(
            existing.managed_config_digest if existing is not None else managed_config_digest
        ),
    )


def read_binding(path: Path) -> SessionBinding | None:
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise SessionBindingError(f"session binding is unreadable: {exc}") from exc
    return SessionBinding.from_json(raw)


def atomic_write(path: Path, content: str) -> None:
    """Persist text through the session channel's existing durable replace sequence."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    handle = None
    try:
        handle = os.fdopen(fd, "w", encoding="utf-8")
        with handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except Exception:
        if handle is None:
            try:
                os.close(fd)
            except OSError:
                pass
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def write_binding(path: Path, binding: SessionBinding) -> None:
    atomic_write(path, binding.to_json())


def merge_and_write_binding(
    path: Path,
    *,
    session_id: str,
    new_entry: LoadedSkillEntry,
    artifact_digest: str,
    managed_parent_id: str = "top_level",
    managed_leaf_id: str = "",
    managed_route: str = "",
    managed_guard_set: tuple[str, ...] = (),
    managed_config_digest: str = "",
) -> SessionBinding:
    """Atomically merge one skill-load entry under the binding lock."""
    with binding_lock(path):
        merged = merge_binding(
            read_binding(path),
            session_id=session_id,
            new_entry=new_entry,
            artifact_digest=artifact_digest,
            managed_parent_id=managed_parent_id,
            managed_leaf_id=managed_leaf_id,
            managed_route=managed_route,
            managed_guard_set=managed_guard_set,
            managed_config_digest=managed_config_digest,
        )
        write_binding(path, merged)
        return merged
