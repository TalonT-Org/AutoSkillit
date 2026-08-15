"""Disk-backed, per-invocation authority for behavioral evidence readers."""

from __future__ import annotations

import base64
import hashlib
import json
import math
import os
import secrets
import shutil
import stat
import tempfile
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any, Final, cast

from autoskillit.core import (
    EVIDENCE_READER_AUTHORITY_ENV_VAR,
    EVIDENCE_READER_AUTHORITY_PATH_ENV_VAR,
    EVIDENCE_READER_CAPABILITY_ENV_VAR,
    EVIDENCE_READER_TOOLS,
    atomic_write,
    canonical_reader_tools_to_bare,
)
from autoskillit.exploration import (
    ArtifactCaptureError,
    ArtifactCaptureStatus,
    StableArtifactCapture,
    canonical_json,
    capture_stable_artifact,
    qualified_digest,
    resolve_repository_identity,
    stable_artifact_matches,
)

if TYPE_CHECKING:
    from autoskillit.pipeline import ToolContext

__all__ = [
    "ArtifactCaptureError",
    "ArtifactCaptureStatus",
    "capture_stable_artifact",
    "resolve_repository_identity",
    "stable_artifact_matches",
    "validate_evidence_reader_startup",
]

_AUTHORITY_SCHEMA: Final = 1
_RECEIPT_SCHEMA: Final = 1
_AUTHORITY_FILE: Final = "authority.json"
_RECEIPT_FILE: Final = "receipts.json"
_CALL_LOCK_FILE: Final = "call.lock"
_AUTHORITY_DOMAIN: Final = b"autoskillit.evidence-reader-authority.v1\0"
_RECEIPT_DOMAIN: Final = b"autoskillit.evidence-reader-receipts.v1\0"
_SCOPE_DOMAIN: Final = b"autoskillit.evidence-reader-scope.v1\0"
_CITATION_DOMAIN: Final = b"autoskillit.evidence-reader-citation.v1\0"
_MAX_AUTHORITY_BYTES: Final = 2_000_000
_MAX_RECEIPT_BYTES: Final = 1_000_000
_REQUIRED_ENV: Final = frozenset(
    {
        EVIDENCE_READER_AUTHORITY_ENV_VAR,
        EVIDENCE_READER_CAPABILITY_ENV_VAR,
        EVIDENCE_READER_AUTHORITY_PATH_ENV_VAR,
    }
)


class EvidenceReaderError(RuntimeError):
    """A fail-closed evidence-reader authority rejection."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class EvidenceReaderLimits:
    max_calls: int = 32
    max_pages: int = 32
    max_output_bytes: int = 1_000_000
    max_page_bytes: int = 64_000
    max_page_lines: int = 1_000
    max_receipts: int = 64

    def __post_init__(self) -> None:
        values = (
            self.max_calls,
            self.max_pages,
            self.max_output_bytes,
            self.max_page_bytes,
            self.max_page_lines,
            self.max_receipts,
        )
        if any(
            not isinstance(value, int) or isinstance(value, bool) or value <= 0 for value in values
        ):
            raise ValueError("evidence reader limits must be positive integers")


@dataclass(frozen=True, slots=True)
class EvidenceReaderInvocation:
    invocation_dir: Path
    environment: tuple[tuple[str, str], ...]
    expires_at: float


@dataclass(frozen=True, slots=True)
class EvidenceReaderPage:
    content: str
    citation_id: str
    continuation: str | None
    byte_start: int
    byte_end: int
    line_start: int
    line_end: int
    snapshot_digest: str


@dataclass(frozen=True, slots=True)
class EvidenceReaderReceipt:
    sequence: int
    outcome: str
    citation_id: str
    byte_start: int
    byte_end: int
    recorded_at: float


@dataclass(frozen=True, slots=True)
class _OpenedAuthority:
    invocation_dir: Path
    authority: dict[str, Any]
    capability_hash: str


def _digest(domain: bytes, payload: object) -> str:
    return qualified_digest(domain, payload)


def _capability_hash(capability: str) -> str:
    return f"sha256:{hashlib.sha256(capability.encode('utf-8')).hexdigest()}"


def _write_secure_json(path: Path, payload: dict[str, Any]) -> None:
    atomic_write(path, canonical_json(payload))
    path.chmod(0o600)


def _secure_json(path: Path, *, max_bytes: int) -> dict[str, Any]:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    if not hasattr(os, "O_NOFOLLOW"):
        raise EvidenceReaderError("platform_unsupported")
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
    del tool_ctx
    env = _environment(environment)
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
        or root.name != "evidence-readers"
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
        or authority_digest != _digest(_AUTHORITY_DOMAIN, authority)
        or authority_digest != env[EVIDENCE_READER_AUTHORITY_ENV_VAR]
    ):
        raise EvidenceReaderError("authority_tampered")
    authority["authority_digest"] = authority_digest
    _validate_authority_fields(authority)
    _authority_content(authority)
    capability_hash = _capability_hash(env[EVIDENCE_READER_CAPABILITY_ENV_VAR])
    if not secrets.compare_digest(str(authority.get("capability_hash", "")), capability_hash):
        raise EvidenceReaderError("capability_invalid")
    return _OpenedAuthority(invocation_dir, authority, capability_hash)


def _limits_payload(limits: EvidenceReaderLimits) -> dict[str, int]:
    return {
        "max_calls": limits.max_calls,
        "max_pages": limits.max_pages,
        "max_output_bytes": limits.max_output_bytes,
        "max_page_bytes": limits.max_page_bytes,
        "max_page_lines": limits.max_page_lines,
        "max_receipts": limits.max_receipts,
    }


def _validate_authority_fields(authority: Mapping[str, Any]) -> None:
    required_strings = (
        "caller_session_id",
        "role",
        "role_definition_digest",
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
    repository_root = Path(authority["repository_root"])
    artifact_path = PurePosixPath(authority["artifact_path"])
    if (
        not repository_root.is_absolute()
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


def _initial_receipts(authority_digest: str, capability_hash: str) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": _RECEIPT_SCHEMA,
        "authority_digest": authority_digest,
        "capability_hash": capability_hash,
        "calls": 0,
        "pages": 0,
        "output_bytes": 0,
        "receipts": [],
        "continuations": {},
    }
    payload["state_digest"] = _digest(_RECEIPT_DOMAIN, payload)
    return payload


def _receipt_state(opened: _OpenedAuthority) -> dict[str, Any]:
    state = _secure_json(opened.invocation_dir / _RECEIPT_FILE, max_bytes=_MAX_RECEIPT_BYTES)
    state_digest = state.pop("state_digest", None)
    if (
        state.get("schema_version") != _RECEIPT_SCHEMA
        or state.get("authority_digest") != opened.authority["authority_digest"]
        or state.get("capability_hash") != opened.capability_hash
        or state_digest != _digest(_RECEIPT_DOMAIN, state)
    ):
        raise EvidenceReaderError("authority_tampered")
    counters = (state.get("calls"), state.get("pages"), state.get("output_bytes"))
    if (
        any(
            not isinstance(value, int) or isinstance(value, bool) or value < 0
            for value in counters
        )
        or not isinstance(state.get("receipts"), list)
        or not isinstance(state.get("continuations"), dict)
    ):
        raise EvidenceReaderError("authority_tampered")
    return state


def _write_receipt_state(opened: _OpenedAuthority, state: dict[str, Any]) -> None:
    state["state_digest"] = _digest(_RECEIPT_DOMAIN, state)
    _write_secure_json(opened.invocation_dir / _RECEIPT_FILE, state)


def _acquire_call_lock(invocation_dir: Path) -> tuple[int, os.stat_result]:
    path = invocation_dir / _CALL_LOCK_FILE
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags, 0o600)
        opened = os.fstat(descriptor)
    except FileExistsError as exc:
        raise EvidenceReaderError("call_in_flight") from exc
    except OSError as exc:
        raise EvidenceReaderError("call_lock_unavailable") from exc
    return descriptor, opened


def _release_call_lock(invocation_dir: Path, descriptor: int, opened: os.stat_result) -> None:
    path = invocation_dir / _CALL_LOCK_FILE
    try:
        current = path.lstat()
        if current.st_dev != opened.st_dev or current.st_ino != opened.st_ino:
            raise EvidenceReaderError("call_lock_tampered")
        path.unlink()
    finally:
        os.close(descriptor)


def create_evidence_reader_invocation(
    tool_ctx: ToolContext,
    capture: StableArtifactCapture,
    *,
    caller_session_id: str,
    role: str,
    role_definition_digest: str,
    canonical_tools: tuple[str, ...],
    bare_tools: tuple[str, ...],
    policy: str,
    expires_at: float,
    limits: EvidenceReaderLimits | None = None,
) -> EvidenceReaderInvocation:
    """Persist one exact captured artifact under a random capability."""

    strings = (caller_session_id, role, role_definition_digest, policy)
    if any(not isinstance(value, str) or not value for value in strings):
        raise EvidenceReaderError("binding_invalid")
    try:
        expected_bare = canonical_reader_tools_to_bare(canonical_tools)
    except ValueError as exc:
        raise EvidenceReaderError("tool_policy_invalid") from exc
    if bare_tools != expected_bare:
        raise EvidenceReaderError("tool_policy_invalid")
    if not isinstance(expires_at, (int, float)) or not math.isfinite(expires_at):
        raise EvidenceReaderError("expiry_invalid")
    if expires_at <= time.time():
        raise EvidenceReaderError("authority_expired")
    try:
        capture.content.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise EvidenceReaderError("content_not_utf8") from exc
    active_limits = limits or EvidenceReaderLimits()
    root = _readers_root(tool_ctx, create=True)
    invocation_dir = Path(tempfile.mkdtemp(prefix="reader-", dir=root))
    invocation_dir.chmod(0o700)
    capability = secrets.token_urlsafe(32)
    capability_hash = _capability_hash(capability)
    authority: dict[str, Any] = {
        "schema_version": _AUTHORITY_SCHEMA,
        "invocation_id": invocation_dir.name,
        "created_at": time.time(),
        "expires_at": float(expires_at),
        "caller_session_id": caller_session_id,
        "role": role,
        "role_definition_digest": role_definition_digest,
        "repository_root": str(capture.repository_root),
        "repository_identity_digest": capture.repository_identity_digest,
        "revision": capture.revision,
        "artifact_path": capture.artifact_path,
        "snapshot_digest": capture.snapshot_digest,
        "content_digest": capture.content_digest,
        "size": capture.size,
        "mode": capture.mode,
        "index_records": list(capture.index_records),
        "canonical_tools": list(canonical_tools),
        "bare_tools": list(bare_tools),
        "policy": policy,
        "limits": _limits_payload(active_limits),
        "capability_hash": capability_hash,
        "content_base64": base64.b64encode(capture.content).decode("ascii"),
    }
    _validate_authority_fields(authority)
    _authority_content(authority)
    authority["authority_digest"] = _digest(_AUTHORITY_DOMAIN, authority)
    authority_path = invocation_dir / _AUTHORITY_FILE
    try:
        _write_secure_json(authority_path, authority)
        _write_secure_json(
            invocation_dir / _RECEIPT_FILE,
            _initial_receipts(authority["authority_digest"], capability_hash),
        )
    except BaseException:
        shutil.rmtree(invocation_dir, ignore_errors=True)
        raise
    environment = tuple(
        sorted(
            {
                EVIDENCE_READER_AUTHORITY_ENV_VAR: authority["authority_digest"],
                EVIDENCE_READER_CAPABILITY_ENV_VAR: capability,
                EVIDENCE_READER_AUTHORITY_PATH_ENV_VAR: str(authority_path),
            }.items()
        )
    )
    return EvidenceReaderInvocation(invocation_dir, environment, float(expires_at))


def _validate_call_binding(
    authority: Mapping[str, Any],
    *,
    caller_session_id: str,
    role: str,
    role_definition_digest: str,
    canonical_tool: str,
    bare_tool: str,
    policy: str,
    deadline: float,
) -> None:
    if not isinstance(deadline, (int, float)) or not math.isfinite(deadline):
        raise EvidenceReaderError("deadline_invalid")
    if time.monotonic() >= deadline:
        raise EvidenceReaderError("deadline_exceeded")
    if time.time() >= authority.get("expires_at", 0):
        raise EvidenceReaderError("authority_expired")
    bindings = (
        ("caller_session_id", caller_session_id),
        ("role", role),
        ("role_definition_digest", role_definition_digest),
        ("policy", policy),
    )
    if any(authority.get(name) != value for name, value in bindings):
        raise EvidenceReaderError("scope_mismatch")
    tools = authority.get("canonical_tools")
    bare_tools = authority.get("bare_tools")
    if (
        not isinstance(tools, list)
        or not isinstance(bare_tools, list)
        or canonical_tool not in tools
        or bare_tool not in bare_tools
        or tools.index(canonical_tool) != bare_tools.index(bare_tool)
    ):
        raise EvidenceReaderError("tool_not_authorized")


def _authority_content(authority: Mapping[str, Any]) -> bytes:
    try:
        content = base64.b64decode(authority["content_base64"], validate=True)
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


def _scope_digest(authority: Mapping[str, Any]) -> str:
    return _digest(
        _SCOPE_DOMAIN,
        {
            "invocation_id": authority["invocation_id"],
            "caller_session_id": authority["caller_session_id"],
            "role": authority["role"],
            "role_definition_digest": authority["role_definition_digest"],
            "canonical_tools": authority["canonical_tools"],
            "bare_tools": authority["bare_tools"],
            "policy": authority["policy"],
        },
    )


def evidence_reader_scope_digest(
    tool_ctx: ToolContext,
    environment: Mapping[str, str],
) -> str:
    """Return the verified invocation-wide scope expected from the child."""

    return _scope_digest(_open_authority(tool_ctx, environment).authority)


def _page_end(content: bytes, offset: int, byte_limit: int, line_limit: int) -> int:
    end = min(len(content), offset + byte_limit)
    while end > offset:
        try:
            content[offset:end].decode("utf-8", errors="strict")
            break
        except UnicodeDecodeError:
            end -= 1
    if end == offset and offset < len(content):
        raise EvidenceReaderError("page_size_too_small")
    segment = content[offset:end]
    newline_positions = [index for index, value in enumerate(segment) if value == 10]
    if len(newline_positions) >= line_limit:
        line_end = newline_positions[line_limit - 1] + 1
        if line_end < len(segment):
            end = offset + line_end
    return end


def read_evidence_reader_page(
    tool_ctx: ToolContext,
    environment: Mapping[str, str],
    *,
    caller_session_id: str,
    role: str,
    role_definition_digest: str,
    canonical_tool: str,
    bare_tool: str,
    policy: str,
    page_size: int,
    continuation: str | None,
    deadline: float,
) -> EvidenceReaderPage:
    """Serve one bounded immutable page after reopening all disk authority."""

    opened = _open_authority(tool_ctx, environment)
    authority = opened.authority
    _validate_call_binding(
        authority,
        caller_session_id=caller_session_id,
        role=role,
        role_definition_digest=role_definition_digest,
        canonical_tool=canonical_tool,
        bare_tool=bare_tool,
        policy=policy,
        deadline=deadline,
    )
    limits = _limits_from_authority(authority)
    if (
        not isinstance(page_size, int)
        or isinstance(page_size, bool)
        or not 1 <= page_size <= limits.max_page_bytes
    ):
        raise EvidenceReaderError("page_size_invalid")
    lock_fd, lock_stat = _acquire_call_lock(opened.invocation_dir)
    try:
        state = _receipt_state(opened)
        if state.get("calls", limits.max_calls) >= limits.max_calls:
            raise EvidenceReaderError("call_budget_exhausted")
        if state.get("pages", limits.max_pages) >= limits.max_pages:
            raise EvidenceReaderError("page_budget_exhausted")
        scope_digest = _scope_digest(authority)
        if continuation is None:
            offset = 0
        else:
            if not isinstance(continuation, str) or not continuation:
                raise EvidenceReaderError("continuation_invalid")
            continuation_hash = _capability_hash(continuation)
            cursor = state.get("continuations", {}).pop(continuation_hash, None)
            expected = {
                "capability_hash": opened.capability_hash,
                "scope_digest": scope_digest,
                "snapshot_digest": authority["snapshot_digest"],
                "page_size": page_size,
            }
            if not isinstance(cursor, dict) or any(
                cursor.get(key) != value for key, value in expected.items()
            ):
                raise EvidenceReaderError("continuation_invalid")
            raw_offset = cursor.get("offset")
            if not isinstance(raw_offset, int) or isinstance(raw_offset, bool) or raw_offset < 0:
                raise EvidenceReaderError("continuation_invalid")
            offset = raw_offset
        content = _authority_content(authority)
        if offset > len(content):
            raise EvidenceReaderError("continuation_invalid")
        end = _page_end(content, offset, page_size, limits.max_page_lines)
        page_bytes = content[offset:end]
        if (
            state.get("output_bytes", limits.max_output_bytes) + len(page_bytes)
            > limits.max_output_bytes
        ):
            raise EvidenceReaderError("output_budget_exhausted")
        citation_id = _digest(
            _CITATION_DOMAIN,
            {
                "invocation_id": authority["invocation_id"],
                "snapshot_digest": authority["snapshot_digest"],
                "byte_start": offset,
                "byte_end": end,
            },
        )
        next_token: str | None = None
        if end < len(content):
            next_token = secrets.token_urlsafe(32)
            state["continuations"][_capability_hash(next_token)] = {
                "capability_hash": opened.capability_hash,
                "scope_digest": scope_digest,
                "snapshot_digest": authority["snapshot_digest"],
                "offset": end,
                "page_size": page_size,
            }
        line_start = content[:offset].count(b"\n") + 1
        newline_count = page_bytes.count(b"\n")
        line_end = line_start + newline_count - int(page_bytes.endswith(b"\n"))
        line_end = max(line_start, line_end)
        state["calls"] += 1
        state["pages"] += 1
        state["output_bytes"] += len(page_bytes)
        receipt = {
            "sequence": state["calls"],
            "outcome": "complete",
            "citation_id": citation_id,
            "byte_start": offset,
            "byte_end": end,
            "recorded_at": time.time(),
        }
        state["receipts"].append(receipt)
        state["receipts"] = state["receipts"][-limits.max_receipts :]
        if time.monotonic() >= deadline:
            raise EvidenceReaderError("deadline_exceeded")
        _write_receipt_state(opened, state)
        return EvidenceReaderPage(
            content=page_bytes.decode("utf-8", errors="strict"),
            citation_id=citation_id,
            continuation=next_token,
            byte_start=offset,
            byte_end=end,
            line_start=line_start,
            line_end=line_end,
            snapshot_digest=authority["snapshot_digest"],
        )
    finally:
        _release_call_lock(opened.invocation_dir, lock_fd, lock_stat)


def read_bound_evidence_reader_page(
    tool_ctx: ToolContext,
    environment: Mapping[str, str],
    *,
    canonical_tool: str,
    page_size: int,
    continuation: str | None,
    deadline: float,
) -> EvidenceReaderPage:
    """Read a page using bindings recovered from verified disk authority."""

    opened = _open_authority(tool_ctx, environment)
    authority = opened.authority
    canonical_tools = authority.get("canonical_tools")
    bare_tools = authority.get("bare_tools")
    if not isinstance(canonical_tools, list) or not isinstance(bare_tools, list):
        raise EvidenceReaderError("authority_tampered")
    try:
        tool_index = canonical_tools.index(canonical_tool)
        bare_tool = bare_tools[tool_index]
    except (ValueError, IndexError) as exc:
        raise EvidenceReaderError("tool_not_authorized") from exc
    raw_bindings = {
        name: authority.get(name)
        for name in (
            "caller_session_id",
            "role",
            "role_definition_digest",
            "policy",
        )
    }
    if not isinstance(bare_tool, str) or any(
        not isinstance(value, str) or not value for value in raw_bindings.values()
    ):
        raise EvidenceReaderError("authority_tampered")
    bindings = cast(dict[str, str], raw_bindings)
    return read_evidence_reader_page(
        tool_ctx,
        environment,
        caller_session_id=bindings["caller_session_id"],
        role=bindings["role"],
        role_definition_digest=bindings["role_definition_digest"],
        canonical_tool=canonical_tool,
        bare_tool=bare_tool,
        policy=bindings["policy"],
        page_size=page_size,
        continuation=continuation,
        deadline=deadline,
    )


def validate_evidence_reader_startup(
    tool_ctx: ToolContext,
    environment: Mapping[str, str],
) -> None:
    """Reopen and authenticate one complete reader authority before visibility."""

    opened = _open_authority(tool_ctx, environment)
    authority = opened.authority
    if time.time() >= authority["expires_at"]:
        raise EvidenceReaderError("authority_expired")
    canonical_tools = authority.get("canonical_tools")
    bare_tools = authority.get("bare_tools")
    if (
        not isinstance(canonical_tools, list)
        or not canonical_tools
        or not isinstance(bare_tools, list)
        or frozenset(bare_tools) != EVIDENCE_READER_TOOLS
        or len(bare_tools) != len(EVIDENCE_READER_TOOLS)
    ):
        raise EvidenceReaderError("tool_not_authorized")
    try:
        expected_bare = canonical_reader_tools_to_bare(tuple(canonical_tools))
    except (TypeError, ValueError) as exc:
        raise EvidenceReaderError("tool_not_authorized") from exc
    if tuple(bare_tools) != expected_bare:
        raise EvidenceReaderError("tool_not_authorized")
    _receipt_state(opened)


def load_evidence_reader_receipts(
    tool_ctx: ToolContext,
    environment: Mapping[str, str],
    *,
    max_receipts: int = 64,
) -> tuple[EvidenceReaderReceipt, ...]:
    """Load a bounded suffix of verified receipts for one invocation."""

    if (
        not isinstance(max_receipts, int)
        or isinstance(max_receipts, bool)
        or not 1 <= max_receipts <= 64
    ):
        raise EvidenceReaderError("receipt_limit_invalid")
    opened = _open_authority(tool_ctx, environment)
    state = _receipt_state(opened)
    raw_receipts = state.get("receipts")
    if not isinstance(raw_receipts, list):
        raise EvidenceReaderError("authority_tampered")
    try:
        receipts = tuple(EvidenceReaderReceipt(**item) for item in raw_receipts[-max_receipts:])
    except (TypeError, ValueError) as exc:
        raise EvidenceReaderError("authority_tampered") from exc
    return receipts


def revoke_evidence_reader_invocation(
    tool_ctx: ToolContext,
    environment: Mapping[str, str],
) -> None:
    """Synchronously revoke one verified authority and prove its directory absent."""

    opened = _open_authority(tool_ctx, environment)
    lock_fd, lock_stat = _acquire_call_lock(opened.invocation_dir)
    try:
        shutil.rmtree(opened.invocation_dir)
    except BaseException:
        if opened.invocation_dir.exists():
            _release_call_lock(opened.invocation_dir, lock_fd, lock_stat)
        else:
            os.close(lock_fd)
        raise
    else:
        os.close(lock_fd)
    if opened.invocation_dir.exists() or opened.invocation_dir.is_symlink():
        raise EvidenceReaderError("revocation_failed")
