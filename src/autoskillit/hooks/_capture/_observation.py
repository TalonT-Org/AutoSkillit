"""Descriptor-bound managed-lineage observations for the isolated runner."""

from __future__ import annotations

import hashlib
import json
import os
import stat
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from autoskillit.hooks._capture_contract import CaptureLineageRef
else:
    from _capture_contract import CaptureLineageRef

_LINEAGE_NAMESPACE = "managed-headless-session-lineage"
_RECORDS_DIR = "records"
_OBSERVATIONS_DIR = "runner-observations"
_SCHEMA_VERSION = 1
_MAX_RECORD_BYTES = 256 * 1024
_MAX_MARKER_BYTES = 8 * 1024
_LEGACY_IDENTITY_FIELDS = {
    "schema_version",
    "generation",
    "launch_id",
    "decision",
    "backend",
    "session_kind",
    "lineage_anchor",
    "anchor_device",
    "anchor_inode",
    "lineage_digest",
    "record_digest",
    "attempt_ids",
    "candidate_native_session_ids",
    "final_native_session_id",
    "dispatch_id",
    "terminal_state",
    "observations",
    "dropped_observation_count",
}
_IDENTITY_FIELDS = _LEGACY_IDENTITY_FIELDS | {"launch_contract_digest"}
_VALID_MODES = {"capture", "direct"}
_VALID_REASONS = {
    "capture_enabled",
    "launch_authorized_direct",
    "project_policy_disabled",
}


def validate_lineage_reference(
    reference: CaptureLineageRef,
    attempt_id: str,
) -> bool:
    """Return whether the exact existing record authorizes this physical attempt."""

    try:
        with _open_validated_lineage(reference, attempt_id):
            return True
    except (OSError, TypeError, ValueError):
        return False


def record_runner_observation(
    reference: CaptureLineageRef,
    attempt_id: str,
    *,
    effective_mode: str,
    reason: str,
    project_policy_disabled: bool,
) -> bool:
    """Create one canonical idempotent marker beneath a validated lineage."""

    if (
        effective_mode not in _VALID_MODES
        or reason not in _VALID_REASONS
        or not isinstance(project_policy_disabled, bool)
    ):
        return False
    try:
        with _open_validated_lineage(reference, attempt_id) as lineage_fd:
            observations_fd = _open_or_create_directory(
                lineage_fd,
                _OBSERVATIONS_DIR,
            )
            try:
                launch_fd = _open_or_create_directory(
                    observations_fd,
                    reference.launch_id,
                )
            finally:
                os.close(observations_fd)
            try:
                marker = {
                    "schema_version": _SCHEMA_VERSION,
                    "launch_id": reference.launch_id,
                    "lineage_digest": reference.lineage_digest,
                    "observation": {
                        "attempt_id": attempt_id,
                        "effective_mode": effective_mode,
                        "reason": reason,
                        "project_policy_disabled": project_policy_disabled,
                    },
                }
                raw = _canonical_json(marker)
                if len(raw) > _MAX_MARKER_BYTES:
                    return False
                name = f"{hashlib.sha256(raw).hexdigest()}.json"
                return _write_idempotent_marker(launch_fd, name, raw)
            finally:
                os.close(launch_fd)
    except (OSError, TypeError, ValueError):
        return False


class _OpenLineage:
    def __init__(self, anchor_fd: int, autoskillit_fd: int, lineage_fd: int) -> None:
        self.anchor_fd = anchor_fd
        self.autoskillit_fd = autoskillit_fd
        self.lineage_fd = lineage_fd

    def __enter__(self) -> int:
        return self.lineage_fd

    def __exit__(self, *_args: object) -> None:
        os.close(self.lineage_fd)
        os.close(self.autoskillit_fd)
        os.close(self.anchor_fd)


def _open_validated_lineage(
    reference: CaptureLineageRef,
    attempt_id: str,
) -> _OpenLineage:
    _validate_identity(reference.launch_id, "launch_id")
    _validate_identity(attempt_id, "attempt_id")
    _validate_digest(reference.lineage_digest)
    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    anchor_fd = os.open(reference.lineage_anchor, directory_flags | nofollow)
    autoskillit_fd = -1
    lineage_fd = -1
    try:
        anchor = os.fstat(anchor_fd)
        if (
            not stat.S_ISDIR(anchor.st_mode)
            or anchor.st_dev != reference.anchor_device
            or anchor.st_ino != reference.anchor_inode
        ):
            raise ValueError("lineage anchor identity mismatch")
        autoskillit_fd = os.open(
            ".autoskillit",
            directory_flags | nofollow,
            dir_fd=anchor_fd,
        )
        lineage_fd = os.open(
            _LINEAGE_NAMESPACE,
            directory_flags | nofollow,
            dir_fd=autoskillit_fd,
        )
        _validate_record(lineage_fd, reference, attempt_id)
        return _OpenLineage(anchor_fd, autoskillit_fd, lineage_fd)
    except BaseException:
        if lineage_fd >= 0:
            os.close(lineage_fd)
        if autoskillit_fd >= 0:
            os.close(autoskillit_fd)
        os.close(anchor_fd)
        raise


def _validate_record(
    lineage_fd: int,
    reference: CaptureLineageRef,
    attempt_id: str,
) -> None:
    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    records_fd = os.open(
        _RECORDS_DIR,
        directory_flags | nofollow,
        dir_fd=lineage_fd,
    )
    record_fd = -1
    try:
        record_fd = os.open(
            f"{reference.launch_id}.json",
            os.O_RDONLY | nofollow,
            dir_fd=records_fd,
        )
        metadata = os.fstat(record_fd)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or metadata.st_size > _MAX_RECORD_BYTES
        ):
            raise ValueError("invalid lineage record metadata")
        raw = os.read(record_fd, _MAX_RECORD_BYTES + 1)
    finally:
        if record_fd >= 0:
            os.close(record_fd)
        os.close(records_fd)
    value = _strict_json(raw)
    if _canonical_json(value) != raw:
        raise ValueError("lineage record is not canonical")
    if not isinstance(value, dict) or set(value) not in (
        _LEGACY_IDENTITY_FIELDS,
        _IDENTITY_FIELDS,
    ):
        raise ValueError("invalid lineage record shape")
    if (
        value["schema_version"] != _SCHEMA_VERSION
        or value["launch_id"] != reference.launch_id
        or value["lineage_digest"] != reference.lineage_digest
        or value["lineage_anchor"] != reference.lineage_anchor
        or value["anchor_device"] != reference.anchor_device
        or value["anchor_inode"] != reference.anchor_inode
    ):
        raise ValueError("lineage record identity mismatch")
    attempt_ids = value["attempt_ids"]
    if not isinstance(attempt_ids, list) or attempt_id not in attempt_ids:
        raise ValueError("attempt is absent from lineage")
    record_digest = value["record_digest"]
    _validate_digest(record_digest)
    payload = {key: item for key, item in value.items() if key != "record_digest"}
    if hashlib.sha256(_canonical_json(payload)).hexdigest() != record_digest:
        raise ValueError("lineage record digest mismatch")
    identity = {
        "schema_version": value["schema_version"],
        "launch_id": value["launch_id"],
        "decision": value["decision"],
        "backend": value["backend"],
        "session_kind": value["session_kind"],
        "lineage_anchor": value["lineage_anchor"],
        "anchor_device": value["anchor_device"],
        "anchor_inode": value["anchor_inode"],
    }
    if hashlib.sha256(_canonical_json(identity)).hexdigest() != reference.lineage_digest:
        raise ValueError("lineage identity digest mismatch")


def _open_or_create_directory(parent_fd: int, name: str) -> int:
    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    try:
        os.mkdir(name, 0o700, dir_fd=parent_fd)
        os.fsync(parent_fd)
    except FileExistsError:
        pass
    fd = os.open(name, directory_flags | nofollow, dir_fd=parent_fd)
    metadata = os.fstat(fd)
    if not stat.S_ISDIR(metadata.st_mode):
        os.close(fd)
        raise ValueError("runner observation component is not a directory")
    return fd


def _write_idempotent_marker(directory_fd: int, name: str, raw: bytes) -> bool:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(name, flags, 0o600, dir_fd=directory_fd)
    except FileExistsError:
        existing_fd = os.open(
            name,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=directory_fd,
        )
        try:
            return os.read(existing_fd, _MAX_MARKER_BYTES + 1) == raw
        finally:
            os.close(existing_fd)
    try:
        view = memoryview(raw)
        while view:
            written = os.write(fd, view)
            if written <= 0:
                raise OSError("runner observation write made no progress")
            view = view[written:]
        os.fsync(fd)
    finally:
        os.close(fd)
    os.fsync(directory_fd)
    return True


def _strict_json(raw: bytes) -> object:
    def reject_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate lineage record field")
            result[key] = value
        return result

    return json.loads(raw.decode("utf-8"), object_pairs_hook=reject_duplicates)


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")


def _validate_identity(value: object, label: str) -> None:
    if (
        not isinstance(value, str)
        or len(value) != 32
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError(f"invalid {label}")


def _validate_digest(value: object) -> None:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError("invalid lineage digest")


__all__ = [
    "record_runner_observation",
    "validate_lineage_reference",
]
