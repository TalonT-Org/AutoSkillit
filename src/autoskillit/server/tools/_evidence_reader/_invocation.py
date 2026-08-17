"""Invocation creation, call locking, and persisted receipt state."""

from __future__ import annotations

import base64
import math
import os
import secrets
import shutil
import tempfile
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

from autoskillit.core import (
    EVIDENCE_READER_AUTHORITY_ENV_VAR,
    EVIDENCE_READER_AUTHORITY_PATH_ENV_VAR,
    EVIDENCE_READER_CAPABILITY_ENV_VAR,
    canonical_reader_tools_to_bare,
    get_logger,
)
from autoskillit.exploration import StableArtifactCapture, qualified_digest
from autoskillit.server.tools._evidence_reader._authority import (
    _AUTHORITY_DOMAIN,
    _AUTHORITY_FILE,
    _AUTHORITY_SCHEMA,
    _SNAPSHOT_FILE,
    _capability_hash,
    _OpenedAuthority,
    _readers_root,
    _secure_json,
    _validate_authority_fields,
    _write_secure_json,
)
from autoskillit.server.tools._evidence_reader._startup import (
    EvidenceReaderError,
    EvidenceReaderInvocation,
    EvidenceReaderLimits,
)

if TYPE_CHECKING:
    from autoskillit.pipeline import ToolContext

_RECEIPT_SCHEMA: Final = 1
_RECEIPT_FILE: Final = "receipts.json"
_CALL_LOCK_FILE: Final = "call.lock"
_RECEIPT_DOMAIN: Final = b"autoskillit.evidence-reader-receipts.v1\0"
_MAX_RECEIPT_BYTES: Final = 1_000_000

logger = get_logger(__name__)


def _limits_payload(limits: EvidenceReaderLimits) -> dict[str, int]:
    return {
        "max_calls": limits.max_calls,
        "max_pages": limits.max_pages,
        "max_output_bytes": limits.max_output_bytes,
        "max_page_bytes": limits.max_page_bytes,
        "max_page_lines": limits.max_page_lines,
        "max_receipts": limits.max_receipts,
    }


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
    payload["state_digest"] = qualified_digest(_RECEIPT_DOMAIN, payload)
    return payload


def _receipt_state(opened: _OpenedAuthority) -> dict[str, Any]:
    state = _secure_json(opened.invocation_dir / _RECEIPT_FILE, max_bytes=_MAX_RECEIPT_BYTES)
    state_digest = state.pop("state_digest", None)
    if (
        state.get("schema_version") != _RECEIPT_SCHEMA
        or state.get("authority_digest") != opened.authority["authority_digest"]
        or state.get("capability_hash") != opened.capability_hash
        or state_digest != qualified_digest(_RECEIPT_DOMAIN, state)
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
    state["state_digest"] = qualified_digest(_RECEIPT_DOMAIN, state)
    _write_secure_json(opened.invocation_dir / _RECEIPT_FILE, state)


def _acquire_call_lock(invocation_dir: Path) -> tuple[int, os.stat_result]:
    path = invocation_dir / _CALL_LOCK_FILE
    if not hasattr(os, "O_NOFOLLOW"):
        raise EvidenceReaderError("platform_unsupported")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags, 0o600)
    except FileExistsError as exc:
        raise EvidenceReaderError("call_in_flight") from exc
    except OSError as exc:
        raise EvidenceReaderError("call_lock_unavailable") from exc
    try:
        opened = os.fstat(descriptor)
    except OSError as exc:
        os.close(descriptor)
        raise EvidenceReaderError("call_lock_unavailable") from exc
    return descriptor, opened


def _release_call_lock(
    invocation_dir: Path,
    descriptor: int,
    opened: os.stat_result,
) -> EvidenceReaderError | None:
    """Release the call lock. Returns a tamper error instead of raising so the
    caller's ``finally`` block can preserve any in-flight rejection reason."""
    path = invocation_dir / _CALL_LOCK_FILE
    tamper: EvidenceReaderError | None = None
    try:
        current = path.lstat()
        if current.st_dev != opened.st_dev or current.st_ino != opened.st_ino:
            tamper = EvidenceReaderError("call_lock_tampered")
    except OSError as exc:
        logger.warning("call_lock_lstat_failed", exc_info=True)
        tamper = EvidenceReaderError("call_lock_unavailable")
        tamper.__cause__ = exc
    finally:
        os.close(descriptor)
        try:
            path.unlink()
        except OSError:
            logger.warning("call_lock_unlink_failed", exc_info=True)
    return tamper


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
        "readers_root": str(root),
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
    }
    _validate_authority_fields(authority)
    authority["authority_digest"] = qualified_digest(_AUTHORITY_DOMAIN, authority)
    snapshot = {
        "schema_version": 1,
        "authority_digest": authority["authority_digest"],
        "snapshot_digest": capture.snapshot_digest,
        "content_digest": capture.content_digest,
        "size": capture.size,
        "content_base64": base64.b64encode(capture.content).decode("ascii"),
    }
    authority_path = invocation_dir / _AUTHORITY_FILE
    try:
        _write_secure_json(authority_path, authority)
        _write_secure_json(invocation_dir / _SNAPSHOT_FILE, snapshot)
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
