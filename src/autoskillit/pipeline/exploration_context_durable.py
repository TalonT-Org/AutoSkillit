"""Durable (disk-persisted) exploration authority — split from exploration_context.py.

Extracted per #4684 Fix E (capability-tied visibility): the session-scoped
Claude-native authority path (``bind_session_scoped``) is in-process-memory
only, unlike the launch-environment path (``bind_launch``), which survives a
server restart via a signed 0600 authority file. This module adds the same
durability to the session-scoped path (``bind_session_scoped_durable``) and
owns the durable-record read/write primitives (``_ExplorationLaunchAuthorityStore``)
those two paths share.

Split into a sibling module — not a new method on
``OwnerBoundExplorationContextStore`` — because that class's file sits at
its 1100-line REQ-CNST-010-E22 exemption ceiling with zero headroom (see
open issue #4667, which tracks the file's further decomposition). Every
import here flows one way (this module has zero imports from
``exploration_context``) so there is no import cycle: exploration_context.py
imports ``_ExplorationLaunchAuthorityStore``, ``_ReopenedLaunchAuthority``,
and ``_safe_submit_failure_reason`` from here, not the reverse.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import stat
import time
from dataclasses import dataclass, fields
from pathlib import Path
from typing import TYPE_CHECKING

from autoskillit.core import (
    canonical_json_bytes,
    read_versioned_json,
    truncate_text,
    write_versioned_json,
)

if TYPE_CHECKING:
    from autoskillit.pipeline.exploration_context import (
        OwnerBoundExplorationContextStore,
        _CapabilityLease,
    )

__all__ = [
    "EXPLORATION_AUTHORITY_PATH_ENV",
    "EXPLORATION_CAPABILITY_ENV",
    "EXPLORATION_PRINCIPAL_ROLE",
    "EXPLORATION_ROLE_ENV",
    "EXPLORATION_SESSION_ENV",
    "bind_session_scoped_durable",
]

_AUTHORITY_SCHEMA_VERSION = 1
_AUTHORITY_FILENAME = ".autoskillit-exploration-authority.json"
_AUTHORITY_SIGNATURE_DOMAIN = b"autoskillit.exploration.launch-authority.v1\x00"
# Mirrors OwnerBoundExplorationContextStore._MAX_CAPABILITY_LENGTH — kept as
# an independent constant rather than imported, to avoid the import cycle
# this module's docstring describes; both bound the same "explore_<token>"
# capability string shape and must be changed together.
_MAX_CAPABILITY_LENGTH = 128
_MAX_SUBMIT_FAILURE_REASON_LENGTH = 512

EXPLORATION_CAPABILITY_ENV = "AUTOSKILLIT_EXPLORATION_CAPABILITY"
EXPLORATION_ROLE_ENV = "AUTOSKILLIT_EXPLORATION_ROLE"
EXPLORATION_SESSION_ENV = "AUTOSKILLIT_EXPLORATION_SESSION_ID"
EXPLORATION_AUTHORITY_PATH_ENV = "AUTOSKILLIT_EXPLORATION_AUTHORITY_PATH"
EXPLORATION_PRINCIPAL_ROLE = "shared-explorer-session"


def _is_capability_shape(value: str) -> bool:
    return (
        isinstance(value, str)
        and value.startswith("explore_")
        and len(value) <= _MAX_CAPABILITY_LENGTH
    )


@dataclass(frozen=True, slots=True)
class _ReopenedLaunchAuthority:
    """Validated durable authority with no raw capability retained on disk."""

    authority_path: Path
    session_id: str
    cwd: Path
    repository_root: Path
    source_identity: str
    snapshot_digest: str
    generation: str
    expires_at: float


def _safe_submit_failure_reason(
    exc: RuntimeError | ValueError,
    *,
    capability: str,
    authority: _ReopenedLaunchAuthority,
) -> str:
    """Return a bounded diagnostic with all launch-authority material removed."""
    reason = str(exc)
    sensitive_values = (capability,) + tuple(
        str(getattr(authority, field.name)) for field in fields(authority)
    )
    for value in sorted(sensitive_values, key=len, reverse=True):
        if value:
            reason = reason.replace(value, "[redacted]")
    return truncate_text(reason, _MAX_SUBMIT_FAILURE_REASON_LENGTH)


class _ExplorationLaunchAuthorityStore:
    """Read/write the one 0600 authority record owned by a generated session."""

    def write(
        self,
        *,
        authority_home: Path,
        session_id: str,
        cwd: Path,
        repository_root: Path,
        capability: str,
        source_identity: str,
        snapshot_digest: str,
        expires_at: int,
    ) -> Path:
        home = authority_home.resolve()
        if not home.is_dir():
            raise ValueError("authority_home must be an existing generated session directory")
        authority_path = home / _AUTHORITY_FILENAME
        principal = {
            "session_home": str(home),
            "session_id": session_id,
            "cwd": str(cwd.resolve()),
            "repository_root": str(repository_root.resolve()),
            "source_identity": source_identity,
            "snapshot_digest": snapshot_digest,
            "capability_sha256": hashlib.sha256(capability.encode("utf-8")).hexdigest(),
            "expires_at": expires_at,
            "generation": secrets.token_hex(16),
        }
        signature = hmac.new(
            capability.encode("utf-8"),
            _AUTHORITY_SIGNATURE_DOMAIN + canonical_json_bytes(principal),
            hashlib.sha256,
        ).hexdigest()
        write_versioned_json(
            authority_path,
            {
                "principal": principal,
                "signature": signature,
            },
            _AUTHORITY_SCHEMA_VERSION,
        )
        os.chmod(authority_path, 0o600)
        return authority_path

    def load_from_environment(self) -> tuple[str, _ReopenedLaunchAuthority] | None:
        capability = os.environ.get(EXPLORATION_CAPABILITY_ENV)
        role = os.environ.get(EXPLORATION_ROLE_ENV)
        session_id = os.environ.get(EXPLORATION_SESSION_ENV)
        raw_path = os.environ.get(EXPLORATION_AUTHORITY_PATH_ENV)
        if (
            not isinstance(capability, str)
            or not _is_capability_shape(capability)
            or role != EXPLORATION_PRINCIPAL_ROLE
            or not isinstance(session_id, str)
            or not session_id
            or not isinstance(raw_path, str)
            or not raw_path
        ):
            return None
        authority_path = Path(raw_path)
        if not authority_path.is_absolute():
            return None
        reopened = self._load(
            authority_path=authority_path,
            capability=capability,
            role=role,
            session_id=session_id,
        )
        if reopened is None:
            return None
        return capability, reopened

    def delete(self, authority_path: Path) -> None:
        resolved = authority_path.resolve(strict=False)
        if resolved.name != _AUTHORITY_FILENAME:
            return
        if authority_path.is_symlink():
            return
        authority_path.unlink(missing_ok=True)

    @staticmethod
    def _load(
        *,
        authority_path: Path,
        capability: str,
        role: str,
        session_id: str,
    ) -> _ReopenedLaunchAuthority | None:
        try:
            metadata = authority_path.lstat()
            if (
                authority_path.name != _AUTHORITY_FILENAME
                or not stat.S_ISREG(metadata.st_mode)
                or metadata.st_mode & 0o077
            ):
                return None
            payload = read_versioned_json(authority_path, _AUTHORITY_SCHEMA_VERSION)
        except OSError:
            return None
        if not isinstance(payload, dict) or set(payload) != {
            "principal",
            "schema_version",
            "signature",
        }:
            return None
        try:
            principal = payload["principal"]
            signature = payload["signature"]
            if not isinstance(principal, dict) or set(principal) != {
                "capability_sha256",
                "cwd",
                "expires_at",
                "generation",
                "repository_root",
                "session_home",
                "session_id",
                "snapshot_digest",
                "source_identity",
            }:
                return None
            session_home = Path(str(principal["session_home"])).resolve()
            resolved_path = authority_path.resolve()
            if resolved_path != session_home / _AUTHORITY_FILENAME:
                return None
            if principal["session_id"] != session_id:
                return None
            expected_digest = principal["capability_sha256"]
            source_identity = principal["source_identity"]
            snapshot_digest = principal["snapshot_digest"]
            expires_at_ns = principal["expires_at"]
            generation = principal["generation"]
            cwd = Path(str(principal["cwd"])).resolve()
            repository_root = Path(str(principal["repository_root"])).resolve()
            process_cwd = Path.cwd().resolve()
        except (KeyError, TypeError, ValueError, OSError):
            return None
        if (
            not isinstance(expected_digest, str)
            or len(expected_digest) != 64
            or not isinstance(source_identity, str)
            or not source_identity
            or not isinstance(snapshot_digest, str)
            or len(snapshot_digest) != 64
            or any(character not in "0123456789abcdef" for character in snapshot_digest)
            or not isinstance(generation, str)
            or len(generation) != 32
            or isinstance(expires_at_ns, bool)
            or not isinstance(expires_at_ns, int)
            or not isinstance(signature, str)
            or len(signature) != 64
            or expires_at_ns <= time.time_ns()
            or cwd != process_cwd
            or not hmac.compare_digest(
                expected_digest,
                hashlib.sha256(capability.encode("utf-8")).hexdigest(),
            )
        ):
            return None
        expected_signature = hmac.new(
            capability.encode("utf-8"),
            _AUTHORITY_SIGNATURE_DOMAIN + canonical_json_bytes(principal),
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(signature, expected_signature):
            return None
        return _ReopenedLaunchAuthority(
            authority_path=resolved_path,
            session_id=session_id,
            cwd=cwd,
            repository_root=repository_root,
            source_identity=source_identity,
            snapshot_digest=snapshot_digest,
            generation=generation,
            expires_at=expires_at_ns / 1_000_000_000,
        )


def bind_session_scoped_durable(
    store: OwnerBoundExplorationContextStore,
    *,
    authority_home: Path,
    owner_id: str,
    session_id: str,
    cwd: Path,
    repository_root: Path,
    source_identity: str,
) -> _CapabilityLease:
    """Mint session-scoped authority AND write its 0600 HMAC-signed record.

    Symmetric to ``bind_launch`` (``exploration_context.py:466-503``), which
    always writes a durable record; ``bind_session_scoped`` alone does not
    (in-process memory only — lost on server restart within the lease TTL).
    A free function taking ``store`` explicitly, not a method, per this
    module's docstring on why the split lives here.

    ``authority_home`` is caller-supplied — for the session-scoped path it
    is resolved from the active session context (see
    ``server/_factory.py``'s ``session_authority_home``, added alongside the
    pre-existing ``exploration_trusted_root``, both derived from the same
    ``project_dir``).
    """
    capability = store.bind_session_scoped(
        owner_id=owner_id,
        session_id=session_id,
        cwd=cwd,
        repository_root=repository_root,
        source_identity=source_identity,
    )
    lease = store.lease_for_capability(capability)
    assert lease is not None, "bind_session_scoped must mint a lease for its own capability"
    _ExplorationLaunchAuthorityStore().write(
        authority_home=authority_home,
        session_id=session_id,
        cwd=cwd,
        repository_root=repository_root,
        capability=capability,
        source_identity=source_identity,
        snapshot_digest=lease.snapshot_digest,
        expires_at=int(lease.expires_at * 1_000_000_000),
    )
    return lease
