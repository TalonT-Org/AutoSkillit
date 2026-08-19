"""Capability hashing, secure authority I/O, and authority lifecycle."""

from __future__ import annotations

import base64
import hashlib
import json
import math
import os
import secrets
import stat
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any, Final

from autoskillit.core import (
    EVIDENCE_READER_AUTHORITY_ENV_VAR,
    EVIDENCE_READER_AUTHORITY_PATH_ENV_VAR,
    EVIDENCE_READER_CAPABILITY_ENV_VAR,
    EVIDENCE_READER_ENV_FORWARD_VARS,
    atomic_write,
    canonical_reader_tools_to_bare,
)
from autoskillit.exploration import canonical_json, qualified_digest
from autoskillit.server.tools._evidence_reader._startup import (
    EvidenceReaderError,
    EvidenceReaderLimits,
)

if TYPE_CHECKING:
    from autoskillit.pipeline import ToolContext

_AUTHORITY_SCHEMA: Final = 1
_AUTHORITY_FILE: Final = "authority.json"
_SNAPSHOT_FILE: Final = "snapshot.json"
_AUTHORITY_DOMAIN: Final = b"autoskillit.evidence-reader-authority.v1\0"
_MAX_AUTHORITY_BYTES: Final = 2_000_000
_REQUIRED_ENV: Final = EVIDENCE_READER_ENV_FORWARD_VARS


@dataclass(frozen=True, slots=True)
class _OpenedAuthority:
    invocation_dir: Path
    authority: dict[str, Any]
    capability_hash: str


def _capability_hash(capability: str) -> str:
    return f"sha256:{hashlib.sha256(capability.encode('utf-8')).hexdigest()}"


def _write_secure_json(path: Path, payload: dict[str, Any]) -> None:
    atomic_write(path, canonical_json(payload))
    path.chmod(0o600)


def _secure_json(path: Path, *, max_bytes: int) -> dict[str, Any]:
    if not hasattr(os, "O_NOFOLLOW"):
        raise EvidenceReaderError("platform_unsupported")
    flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    try:
        before = path.lstat()
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise EvidenceReaderError("authority_unavailable") from exc
    try:
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or before.st_dev != opened.st_dev
            or before.st_ino != opened.st_ino
            or (before.st_size, before.st_mode, before.st_mtime_ns, before.st_ctime_ns)
            != (opened.st_size, opened.st_mode, opened.st_mtime_ns, opened.st_ctime_ns)
            or stat.S_IMODE(opened.st_mode) != 0o600
            or opened.st_uid != os.getuid()
            or opened.st_size > max_bytes
        ):
            raise EvidenceReaderError("authority_tampered")
        content = bytearray()
        while len(content) <= max_bytes:
            chunk = os.read(descriptor, min(64 * 1024, max_bytes + 1 - len(content)))
            if not chunk:
                break
            content.extend(chunk)
        after_fd = os.fstat(descriptor)
        after_path = path.lstat()
        if (
            len(content) > max_bytes
            or len(content) != after_fd.st_size
            or opened.st_dev != after_fd.st_dev
            or opened.st_ino != after_fd.st_ino
            or after_fd.st_dev != after_path.st_dev
            or after_fd.st_ino != after_path.st_ino
            or (opened.st_size, opened.st_mode, opened.st_mtime_ns, opened.st_ctime_ns)
            != (after_fd.st_size, after_fd.st_mode, after_fd.st_mtime_ns, after_fd.st_ctime_ns)
            or (after_fd.st_size, after_fd.st_mode, after_fd.st_mtime_ns, after_fd.st_ctime_ns)
            != (
                after_path.st_size,
                after_path.st_mode,
                after_path.st_mtime_ns,
                after_path.st_ctime_ns,
            )
        ):
            raise EvidenceReaderError("authority_tampered")
    except OSError as exc:
        raise EvidenceReaderError("authority_tampered") from exc
    finally:
        os.close(descriptor)
    try:
        decoded = bytes(content).decode("ascii", errors="strict")
        value = json.loads(decoded)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise EvidenceReaderError("authority_tampered") from exc
    if not isinstance(value, dict) or canonical_json(value) != decoded:
        raise EvidenceReaderError("authority_tampered")
    return value


def _verified_readers_root(root: Path) -> Path:
    try:
        metadata = root.lstat()
        resolved = root.resolve(strict=True)
    except OSError as exc:
        raise EvidenceReaderError("authority_root_unavailable") from exc
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or stat.S_IMODE(metadata.st_mode) != 0o700
        or metadata.st_uid != os.getuid()
    ):
        raise EvidenceReaderError("authority_root_unsafe")
    return resolved


def _readers_root(tool_ctx: ToolContext, *, create: bool) -> Path:
    root = tool_ctx.temp_dir / "evidence-readers"
    if create:
        try:
            root.mkdir(mode=0o700, parents=True, exist_ok=False)
        except FileExistsError:
            pass
    return _verified_readers_root(root)


def _environment(values: Mapping[str, str]) -> dict[str, str]:
    if set(values) != _REQUIRED_ENV:
        raise EvidenceReaderError("environment_invalid")
    result = dict(values)
    if any(not isinstance(value, str) or not value for value in result.values()):
        raise EvidenceReaderError("environment_invalid")
    return result


def _open_authority(tool_ctx: ToolContext, environment: Mapping[str, str]) -> _OpenedAuthority:
    env = _environment(environment)
    expected_root = _readers_root(tool_ctx, create=False)
    raw_path = Path(env[EVIDENCE_READER_AUTHORITY_PATH_ENV_VAR])
    if not raw_path.is_absolute():
        raise EvidenceReaderError("authority_path_invalid")
    try:
        resolved_path = raw_path.resolve(strict=True)
        invocation_dir = resolved_path.parent
        root = _verified_readers_root(invocation_dir.parent)
        directory_metadata = invocation_dir.lstat()
    except OSError as exc:
        raise EvidenceReaderError("authority_unavailable") from exc
    if (
        resolved_path != raw_path
        or resolved_path.name != _AUTHORITY_FILE
        or root != expected_root
        or invocation_dir.parent != root
        or not stat.S_ISDIR(directory_metadata.st_mode)
        or stat.S_ISLNK(directory_metadata.st_mode)
        or stat.S_IMODE(directory_metadata.st_mode) != 0o700
        or directory_metadata.st_uid != os.getuid()
    ):
        raise EvidenceReaderError("authority_path_invalid")
    authority = _secure_json(resolved_path, max_bytes=_MAX_AUTHORITY_BYTES)
    authority_digest = authority.pop("authority_digest", None)
    if (
        authority.get("schema_version") != _AUTHORITY_SCHEMA
        or authority.get("invocation_id") != invocation_dir.name
        or authority_digest != qualified_digest(_AUTHORITY_DOMAIN, authority)
        or authority_digest != env[EVIDENCE_READER_AUTHORITY_ENV_VAR]
    ):
        raise EvidenceReaderError("authority_tampered")
    authority["authority_digest"] = authority_digest
    _validate_authority_fields(authority)
    if authority["readers_root"] != str(root):
        raise EvidenceReaderError("authority_path_invalid")
    capability_hash = _capability_hash(env[EVIDENCE_READER_CAPABILITY_ENV_VAR])
    if not secrets.compare_digest(str(authority.get("capability_hash", "")), capability_hash):
        raise EvidenceReaderError("capability_invalid")
    _snapshot_content(invocation_dir, authority)
    return _OpenedAuthority(invocation_dir, authority, capability_hash)


def _validate_authority_fields(authority: Mapping[str, Any]) -> None:
    required_strings = (
        "caller_session_id",
        "role",
        "role_definition_digest",
        "readers_root",
        "repository_root",
        "repository_identity_digest",
        "revision",
        "artifact_path",
        "snapshot_digest",
        "content_digest",
        "policy",
        "capability_hash",
    )
    if any(
        not isinstance(authority.get(name), str) or not authority[name]
        for name in required_strings
    ):
        raise EvidenceReaderError("authority_tampered")
    readers_root = Path(authority["readers_root"])
    repository_root = Path(authority["repository_root"])
    artifact_path = PurePosixPath(authority["artifact_path"])
    if (
        not readers_root.is_absolute()
        or not repository_root.is_absolute()
        or artifact_path.is_absolute()
        or not artifact_path.parts
        or ".." in artifact_path.parts
        or ".git" in artifact_path.parts
        or "\\" in authority["artifact_path"]
    ):
        raise EvidenceReaderError("authority_tampered")
    expires_at = authority.get("expires_at")
    if (
        not isinstance(expires_at, (int, float))
        or isinstance(expires_at, bool)
        or not math.isfinite(expires_at)
    ):
        raise EvidenceReaderError("authority_tampered")
    size = authority.get("size")
    mode = authority.get("mode")
    if (
        not isinstance(size, int)
        or isinstance(size, bool)
        or size < 0
        or not isinstance(mode, int)
        or isinstance(mode, bool)
        or mode < 0
    ):
        raise EvidenceReaderError("authority_tampered")
    canonical_tools = authority.get("canonical_tools")
    bare_tools = authority.get("bare_tools")
    if not isinstance(canonical_tools, list) or not isinstance(bare_tools, list):
        raise EvidenceReaderError("authority_tampered")
    try:
        expected_bare = canonical_reader_tools_to_bare(tuple(canonical_tools))
    except (TypeError, ValueError) as exc:
        raise EvidenceReaderError("authority_tampered") from exc
    if tuple(bare_tools) != expected_bare:
        raise EvidenceReaderError("authority_tampered")
    _limits_from_authority(authority)


def _limits_from_authority(authority: Mapping[str, Any]) -> EvidenceReaderLimits:
    raw = authority.get("limits")
    if not isinstance(raw, dict):
        raise EvidenceReaderError("authority_tampered")
    try:
        return EvidenceReaderLimits(**raw)
    except (TypeError, ValueError) as exc:
        raise EvidenceReaderError("authority_tampered") from exc


def _snapshot_content(invocation_dir: Path, authority: Mapping[str, Any]) -> bytes:
    snapshot = _secure_json(invocation_dir / _SNAPSHOT_FILE, max_bytes=_MAX_AUTHORITY_BYTES)
    if (
        set(snapshot)
        != {
            "schema_version",
            "authority_digest",
            "snapshot_digest",
            "content_digest",
            "size",
            "content_base64",
        }
        or snapshot.get("schema_version") != 1
        or snapshot.get("authority_digest") != authority.get("authority_digest")
        or snapshot.get("snapshot_digest") != authority.get("snapshot_digest")
        or snapshot.get("content_digest") != authority.get("content_digest")
        or snapshot.get("size") != authority.get("size")
    ):
        raise EvidenceReaderError("authority_tampered")
    try:
        content = base64.b64decode(snapshot["content_base64"], validate=True)
    except (KeyError, TypeError, ValueError) as exc:
        raise EvidenceReaderError("authority_tampered") from exc
    if len(content) != authority.get(
        "size"
    ) or f"sha256:{hashlib.sha256(content).hexdigest()}" != authority.get("content_digest"):
        raise EvidenceReaderError("authority_tampered")
    try:
        content.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise EvidenceReaderError("content_not_utf8") from exc
    return content
