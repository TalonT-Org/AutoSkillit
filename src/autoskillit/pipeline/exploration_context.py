"""Private lifecycle primitives for brokered repository exploration.

The public request and response contracts intentionally live in ``core.types``.
This module only owns the process-local capability lifecycle used by the server
composition root: capabilities are opaque, short lived, and bound to one
caller identity.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import stat
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Generic, Protocol, TypeVar, cast, runtime_checkable

from autoskillit.core import (
    BUNDLED_EXPLORER_ROLES,
    CapabilityResolution,
    CapabilityResolutionStatus,
    CompletenessReport,
    ContinuationCursor,
    EvidencePage,
    EvidenceRecord,
    ExplorationContextStoreProtocol,
    ExplorationQuerySpec,
    RepositorySnapshot,
    SessionType,
    canonical_json_bytes,
    get_logger,
    read_versioned_json,
    truncate_text,
    write_versioned_json,
)

__all__ = [
    "CapabilityResolution",
    "CapabilityResolutionStatus",
    "EXPLORER_ROLE_NAMES",
    "EXPLORER_INELIGIBLE_SESSION_TYPES",
    "EXPLORATION_AUTHORITY_PATH_ENV",
    "EXPLORATION_CAPABILITY_ENV",
    "EXPLORATION_PRINCIPAL_ROLE",
    "EXPLORATION_ROLE_ENV",
    "EXPLORATION_SESSION_ENV",
    "ExplorationLaunchBinding",
    "ExplorationContext",
    "ExplorationContextStoreProtocol",
    "ExplorationServiceProtocol",
    "OwnerBoundExplorationContextStore",
    "is_explorer_binding_eligible",
]


_T = TypeVar("_T")
_MAX_CAPABILITY_LENGTH = 128
_MAX_TTL_SECONDS = 300.0
_MAX_ACTIVE_LEASES = 256
_MAX_SOURCE_IDENTITY_LENGTH = 1_024
_AUTHORITY_SCHEMA_VERSION = 1
_AUTHORITY_FILENAME = ".autoskillit-exploration-authority.json"
_AUTHORITY_SIGNATURE_DOMAIN = b"autoskillit.exploration.launch-authority.v1\x00"
_SHARED_SOURCE_IDENTITY_DOMAIN = b"autoskillit.exploration.shared-source.v1\x00"
_MAX_SUBMIT_FAILURE_REASON_LENGTH = 512

logger = get_logger(__name__)

# These names are an intentionally narrow launch adapter contract.  Codex may
# preserve them while materializing an explorer child, but never mint or alter
# their authority.
EXPLORER_ROLE_NAMES = BUNDLED_EXPLORER_ROLES
EXPLORER_INELIGIBLE_SESSION_TYPES = frozenset({SessionType.ORCHESTRATOR, SessionType.FLEET})
EXPLORATION_CAPABILITY_ENV = "AUTOSKILLIT_EXPLORATION_CAPABILITY"
EXPLORATION_ROLE_ENV = "AUTOSKILLIT_EXPLORATION_ROLE"
EXPLORATION_SESSION_ENV = "AUTOSKILLIT_EXPLORATION_SESSION_ID"
EXPLORATION_AUTHORITY_PATH_ENV = "AUTOSKILLIT_EXPLORATION_AUTHORITY_PATH"
EXPLORATION_PRINCIPAL_ROLE = "shared-explorer-session"


def is_explorer_binding_eligible(
    *,
    has_identity: bool,
    has_backend: bool,
    terminal_explorer_capable: bool,
    session_scoped_explorer_capable: bool,
    parent_sandbox_mode: str,
    session_type: SessionType | None = None,
) -> bool:
    """Pure eligibility predicate for explorer binding mint.

    Used by the server corridor in ``_explorer_projection.py``.  The server
    wrapper adds store presence and invocation-identity resolution; this
    function owns only the structural gates.
    """
    if not has_identity or not has_backend:
        return False
    if session_type in EXPLORER_INELIGIBLE_SESSION_TYPES:
        return False
    if terminal_explorer_capable or session_scoped_explorer_capable:
        return parent_sandbox_mode == "read-only"
    return False


@dataclass(frozen=True, slots=True)
class ExplorationLaunchBinding:
    """Server-issued material for one explorer child launch.

    The capability is opaque; role and session are trusted launch metadata, not
    authority accepted back from a child MCP call.
    """

    capability: str
    role: str
    session_id: str
    authority_path: Path

    def provider_extras(self) -> dict[str, str]:
        return {
            EXPLORATION_CAPABILITY_ENV: self.capability,
            EXPLORATION_ROLE_ENV: self.role,
            EXPLORATION_SESSION_ENV: self.session_id,
            EXPLORATION_AUTHORITY_PATH_ENV: str(self.authority_path),
        }


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
    for value in sensitive_values:
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
            or not OwnerBoundExplorationContextStore._is_capability_shape(capability)
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


@dataclass(frozen=True, slots=True)
class _CapabilityLease(Generic[_T]):
    """In-memory owner-bound capability over a typed value and snapshot scope."""

    owner_id: str
    role: str
    session_id: str
    expires_at: float
    value: _T
    cwd: Path | None = None
    repository_root: Path | None = None
    source_identity: str = ""
    snapshot_digest: str = ""


@dataclass(frozen=True, slots=True)
class ExplorationContext:
    """One server-owned immutable evidence generation for a broker capability."""

    query: ExplorationQuerySpec
    snapshot: RepositorySnapshot
    evidence: tuple[EvidenceRecord, ...]
    completeness: CompletenessReport


@runtime_checkable
class ExplorationServiceProtocol(Protocol):
    """Injectable deterministic collector gateway; handlers never call collectors."""

    def capture_snapshot(self, root: Path) -> RepositorySnapshot: ...

    def collect(self, query: ExplorationQuerySpec, *, root: Path) -> ExplorationContext: ...

    def page(
        self,
        context: ExplorationContext,
        *,
        page_size: int,
        cursor: ContinuationCursor | None = None,
    ) -> EvidencePage: ...


class OwnerBoundExplorationContextStore(Generic[_T]):
    """Keep opaque exploration state for the lifetime of one server process.

    Callers may retain a capability only as an opaque handle.  Resolution is
    deliberately bound to the authenticated owner, registered explorer role,
    and backend session; a leaked handle therefore cannot be replayed by a
    different child.  Entries are removed eagerly when expired, discarded, or
    when the containing server lifecycle closes.
    """

    def __init__(
        self,
        *,
        trusted_root: Path | None = None,
        service: ExplorationServiceProtocol | None = None,
        max_ttl_seconds: float = _MAX_TTL_SECONDS,
        max_active_leases: int = _MAX_ACTIVE_LEASES,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not 0 < max_ttl_seconds <= _MAX_TTL_SECONDS:
            raise ValueError("max_ttl_seconds must be in (0, 300]")
        if not 0 < max_active_leases <= _MAX_ACTIVE_LEASES:
            raise ValueError("max_active_leases must be in [1, 256]")
        self._max_ttl_seconds = max_ttl_seconds
        self._max_active_leases = max_active_leases
        self._clock = clock
        self._trusted_root = (trusted_root or Path.cwd()).resolve()
        self._service = service
        self._leases: dict[str, _CapabilityLease[_T]] = {}
        self._session_capabilities: dict[str, set[str]] = {}
        self._session_authority_paths: dict[str, Path] = {}
        self._launch_authorities = _ExplorationLaunchAuthorityStore()
        self._lock = threading.RLock()
        self._closed = False

    @property
    def trusted_root(self) -> Path:
        """Return the factory-injected repository root; callers never supply it."""
        return self._trusted_root

    @classmethod
    def verified_repository_root_from_launch_environment(cls) -> Path | None:
        """Recover only the HMAC-verified root needed to bootstrap an explorer store."""
        reopened = _ExplorationLaunchAuthorityStore().load_from_environment()
        if reopened is None:
            return None
        _capability, authority = reopened
        return authority.repository_root

    def __enter__(self) -> OwnerBoundExplorationContextStore[_T]:
        return self

    def __exit__(self, *_unused: object) -> None:
        """Clear capabilities even if the surrounding operation fails."""
        self.close()

    def issue(
        self,
        *,
        owner_id: str,
        role: str,
        session_id: str,
        value: _T,
        ttl_seconds: float | None = None,
    ) -> str:
        """Create one bounded opaque capability for an already trusted value."""
        self._validate_binding(owner_id=owner_id, role=role, session_id=session_id)
        ttl = self._max_ttl_seconds if ttl_seconds is None else ttl_seconds
        if not 0 < ttl <= self._max_ttl_seconds:
            raise ValueError("ttl_seconds must be in (0, max_ttl_seconds]")
        with self._lock:
            if self._closed:
                raise RuntimeError("exploration context store is closed")
            self._cleanup_expired_locked()
            return self._issue_locked(
                owner_id=owner_id,
                role=role,
                session_id=session_id,
                value=value,
                ttl=ttl,
            )

    def bind_launch(
        self,
        *,
        owner_id: str,
        role: str,
        session_id: str,
        cwd: Path,
        repository_root: Path,
        source_identity: str,
        authority_home: Path,
    ) -> ExplorationLaunchBinding:
        """Atomically replace a session's capability with trusted launch material.

        ``repository_root`` and ``source_identity`` are supplied by the
        server's already-validated skill invocation, never by an explorer.
        Only ``cwd`` is later used for collection, preserving the canonical
        post-backend-binding projection path.
        """
        self._validate_binding(owner_id=owner_id, role=role, session_id=session_id)
        if role not in EXPLORER_ROLE_NAMES:
            raise ValueError("role is not an explorer role")
        if not isinstance(source_identity, str) or not source_identity:
            raise ValueError("source_identity must be a non-empty string")
        if len(source_identity) > _MAX_SOURCE_IDENTITY_LENGTH:
            raise ValueError("source_identity is too long")
        canonical_cwd = cwd.resolve()
        if repository_root.resolve() != self._trusted_root:
            raise ValueError("repository_root does not match the trusted project root")
        canonical_repository_root = self._trusted_root
        bindings = self._bind_launches(
            owner_id=owner_id,
            session_id=session_id,
            cwd=canonical_cwd,
            repository_root=canonical_repository_root,
            source_identities={name: source_identity for name in EXPLORER_ROLE_NAMES},
            authority_home=authority_home,
        )
        return bindings[role]

    def bind_launches(
        self,
        *,
        owner_id: str,
        session_id: str,
        cwd: Path,
        repository_root: Path,
        source_identities: Mapping[str, str],
        authority_home: Path,
    ) -> dict[str, dict[str, str]]:
        """Replicate one shared principal into both behavioral role projections."""
        return {
            role: binding.provider_extras()
            for role, binding in self._bind_launches(
                owner_id=owner_id,
                session_id=session_id,
                cwd=cwd,
                repository_root=repository_root,
                source_identities=source_identities,
                authority_home=authority_home,
            ).items()
        }

    def _bind_launches(
        self,
        *,
        owner_id: str,
        session_id: str,
        cwd: Path,
        repository_root: Path,
        source_identities: Mapping[str, str],
        authority_home: Path,
    ) -> dict[str, ExplorationLaunchBinding]:
        """Atomically rotate one principal shared by the parent and both roles."""
        self._validate_binding(owner_id=owner_id, role="server", session_id=session_id)
        if set(source_identities) != EXPLORER_ROLE_NAMES:
            raise ValueError("source_identities must name both explorer roles exactly")
        if any(
            not isinstance(source_identity, str)
            or not source_identity
            or len(source_identity) > _MAX_SOURCE_IDENTITY_LENGTH
            for source_identity in source_identities.values()
        ):
            raise ValueError("source_identities must contain bounded non-empty strings")
        canonical_cwd = cwd.resolve()
        canonical_repository_root = repository_root.resolve()
        if canonical_repository_root != self._trusted_root:
            raise ValueError("repository_root does not match the trusted project root")
        if self._service is None:
            raise RuntimeError("exploration service is not configured")
        issuance_snapshot = self._service.capture_snapshot(canonical_repository_root)
        snapshot_digest = issuance_snapshot.digest
        if issuance_snapshot.stale or issuance_snapshot.truncated:
            raise ValueError("exploration issuance requires a complete immutable snapshot")
        shared_source_identity = self._shared_source_identity(source_identities)
        with self._lock:
            if self._closed:
                raise RuntimeError("exploration context store is closed")
            self._cleanup_expired_locked()
            replaced_count = len(self._session_capabilities.get(session_id, ()))
            if len(self._leases) - replaced_count + 1 > self._max_active_leases:
                raise RuntimeError("exploration context store capacity exceeded")
            capability = self._new_capability_locked()
            expires_at = time.time_ns() + int(self._max_ttl_seconds * 1_000_000_000)
            authority_path = self._launch_authorities.write(
                authority_home=authority_home,
                session_id=session_id,
                cwd=canonical_cwd,
                repository_root=canonical_repository_root,
                capability=capability,
                source_identity=shared_source_identity,
                snapshot_digest=snapshot_digest,
                expires_at=expires_at,
            )
            self._discard_session_locked(session_id)
            self._session_authority_paths[session_id] = authority_path
            self._leases[capability] = _CapabilityLease(
                owner_id=owner_id,
                role=EXPLORATION_PRINCIPAL_ROLE,
                session_id=session_id,
                expires_at=self._clock() + self._max_ttl_seconds,
                value=cast(_T, None),
                cwd=canonical_cwd,
                repository_root=canonical_repository_root,
                source_identity=shared_source_identity,
                snapshot_digest=snapshot_digest,
            )
            self._session_capabilities.setdefault(session_id, set()).add(capability)
            binding = ExplorationLaunchBinding(
                capability=capability,
                role=EXPLORATION_PRINCIPAL_ROLE,
                session_id=session_id,
                authority_path=authority_path,
            )
            return {role: binding for role in sorted(EXPLORER_ROLE_NAMES)}

    def bind_session_scoped(
        self,
        *,
        owner_id: str,
        session_id: str,
        cwd: Path,
        repository_root: Path,
        source_identity: str,
    ) -> str:
        """Mint session-scoped in-process authority without env/sidecar round-trip.

        Used by the Claude-native exploration path where subagents share the
        parent process and per-child env binding is structurally impossible.
        Returns the capability string for per-call verification.
        """
        self._validate_binding(owner_id=owner_id, role="server", session_id=session_id)
        if not source_identity or len(source_identity) > _MAX_SOURCE_IDENTITY_LENGTH:
            raise ValueError("source_identity must be bounded non-empty text")
        canonical_cwd = cwd.resolve()
        canonical_repository_root = repository_root.resolve()
        if canonical_repository_root != self._trusted_root:
            raise ValueError("repository_root does not match the trusted project root")
        if self._service is None:
            raise RuntimeError("exploration service is not configured")
        issuance_snapshot = self._service.capture_snapshot(canonical_repository_root)
        if issuance_snapshot.stale or issuance_snapshot.truncated:
            raise ValueError("exploration issuance requires a complete immutable snapshot")
        with self._lock:
            if self._closed:
                raise RuntimeError("exploration context store is closed")
            self._cleanup_expired_locked()
            replaced_count = len(self._session_capabilities.get(session_id, ()))
            if len(self._leases) - replaced_count + 1 > self._max_active_leases:
                raise RuntimeError("exploration context store capacity exceeded")
            capability = self._new_capability_locked()
            self._discard_session_locked(session_id)
            self._leases[capability] = _CapabilityLease(
                owner_id=owner_id,
                role=EXPLORATION_PRINCIPAL_ROLE,
                session_id=session_id,
                expires_at=self._clock() + self._max_ttl_seconds,
                value=cast(_T, None),
                cwd=canonical_cwd,
                repository_root=canonical_repository_root,
                source_identity=source_identity,
                snapshot_digest=issuance_snapshot.digest,
            )
            self._session_capabilities.setdefault(session_id, set()).add(capability)
        return capability

    def session_scoped_capability(self, session_id: str) -> str | None:
        """Return the session-scoped capability if one is active, else None."""
        with self._lock:
            caps = self._session_capabilities.get(session_id)
            if not caps:
                return None
            for cap in caps:
                lease = self._leases.get(cap)
                if lease is not None and lease.expires_at > self._clock():
                    return cap
            return None

    def resolve(
        self,
        *,
        capability: str,
        owner_id: str,
        role: str,
        session_id: str,
    ) -> CapabilityResolution[_T]:
        """Resolve a capability for its owner/session and authorized principal."""
        self._validate_binding(owner_id=owner_id, role=role, session_id=session_id)
        if not self._is_capability_shape(capability):
            return CapabilityResolution(CapabilityResolutionStatus.INVALID)
        with self._lock:
            lease = self._leases.get(capability)
            if lease is None:
                return CapabilityResolution(CapabilityResolutionStatus.INVALID)
            if lease.expires_at <= self._clock():
                self._discard_locked(capability)
                return CapabilityResolution(CapabilityResolutionStatus.EXPIRED)
            if not hmac.compare_digest(lease.owner_id, owner_id):
                return CapabilityResolution(CapabilityResolutionStatus.OWNER_MISMATCH)
            if lease.role == EXPLORATION_PRINCIPAL_ROLE:
                role_matches = role in EXPLORER_ROLE_NAMES or role == EXPLORATION_PRINCIPAL_ROLE
            else:
                role_matches = hmac.compare_digest(lease.role, role)
            if not role_matches:
                return CapabilityResolution(CapabilityResolutionStatus.ROLE_MISMATCH)
            if not hmac.compare_digest(lease.session_id, session_id):
                return CapabilityResolution(CapabilityResolutionStatus.SESSION_MISMATCH)
            return CapabilityResolution(CapabilityResolutionStatus.OK, lease.value)

    def submit(
        self,
        *,
        owner_id: str,
        role: str,
        session_id: str,
        query: ExplorationQuerySpec,
        page_size: int,
    ) -> tuple[str, EvidencePage]:
        """Collect against the injected root, then bind its immutable generation."""
        if self._service is None:
            raise RuntimeError("exploration service is not configured")
        context = self._service.collect(query, root=self._trusted_root)
        capability = self.issue(
            owner_id=owner_id,
            role=role,
            session_id=session_id,
            value=cast(_T, context),
        )
        return capability, self._service.page(context, page_size=page_size)

    def submit_for_capability(
        self,
        *,
        capability: str,
        query: ExplorationQuerySpec,
        page_size: int,
    ) -> tuple[str, EvidencePage]:
        """Atomically update one server-issued capability with an evidence generation."""
        if self._service is None:
            raise RuntimeError("exploration service is not configured")
        with self._lock:
            lease = self._resolve_capability_locked(capability)
            if lease is None or lease.cwd is None or lease.repository_root is None:
                raise ValueError("exploration capability is unavailable")
            (
                owner_id,
                role,
                session_id,
                cwd,
                repository_root,
                source_identity,
                snapshot_digest,
            ) = (
                lease.owner_id,
                lease.role,
                lease.session_id,
                lease.cwd,
                lease.repository_root,
                lease.source_identity,
                lease.snapshot_digest,
            )
        context = self._service.collect(query, root=repository_root)
        if context.snapshot.digest != snapshot_digest:
            raise ValueError("repository snapshot changed since exploration authority issuance")
        page = self._service.page(context, page_size=page_size)
        with self._lock:
            current = self._resolve_capability_locked(capability)
            if current is None or (
                current.owner_id,
                current.role,
                current.session_id,
                current.cwd,
                current.repository_root,
                current.source_identity,
                current.snapshot_digest,
            ) != (
                owner_id,
                role,
                session_id,
                cwd,
                repository_root,
                source_identity,
                snapshot_digest,
            ):
                raise ValueError("exploration capability was replaced")
            self._leases[capability] = _CapabilityLease(
                owner_id=owner_id,
                role=role,
                session_id=session_id,
                expires_at=current.expires_at,
                value=cast(_T, context),
                cwd=cwd,
                repository_root=repository_root,
                source_identity=source_identity,
                snapshot_digest=snapshot_digest,
            )
        return capability, page

    def get_page_for_capability(
        self,
        *,
        capability: str,
        page_size: int,
        cursor: ContinuationCursor | None = None,
    ) -> tuple[CapabilityResolutionStatus, EvidencePage | None]:
        """Read a page using only the server-issued opaque capability."""
        with self._lock:
            lease = self._resolve_capability_locked(capability)
            if lease is None:
                return CapabilityResolutionStatus.INVALID, None
            context = lease.value
        if not isinstance(context, ExplorationContext) or self._service is None:
            return CapabilityResolutionStatus.INVALID, None
        return (
            CapabilityResolutionStatus.OK,
            self._service.page(context, page_size=page_size, cursor=cursor),
        )

    def submit_from_launch_environment(
        self,
        *,
        query: ExplorationQuerySpec,
        page_size: int,
    ) -> tuple[CapabilityResolutionStatus, EvidencePage | None]:
        """Collect using only authority injected into the role-local server config."""
        reopened = self._reopen_launch_environment()
        if reopened is None:
            return CapabilityResolutionStatus.INVALID, None
        capability, authority = reopened
        try:
            _replacement, page = self.submit_for_capability(
                capability=capability,
                query=query,
                page_size=page_size,
            )
        except (RuntimeError, ValueError) as exc:
            logger.warning(
                "exploration_submit_failed",
                exception_type=type(exc).__name__,
                reason=_safe_submit_failure_reason(
                    exc,
                    capability=capability,
                    authority=authority,
                ),
            )
            return CapabilityResolutionStatus.INVALID, None
        # A terminal cleanup or replacement may have occurred while collecting.
        if self._reopen_launch_environment() != reopened:
            self.discard(capability)
            return CapabilityResolutionStatus.INVALID, None
        return CapabilityResolutionStatus.OK, page

    def get_page_from_launch_environment(
        self,
        *,
        page_size: int,
        cursor: ContinuationCursor | None = None,
    ) -> tuple[CapabilityResolutionStatus, EvidencePage | None]:
        """Read an active page while proving the durable authority is still live."""
        reopened = self._reopen_launch_environment()
        if reopened is None:
            return CapabilityResolutionStatus.INVALID, None
        capability, _authority = reopened
        status, page = self.get_page_for_capability(
            capability=capability,
            page_size=page_size,
            cursor=cursor,
        )
        if status is not CapabilityResolutionStatus.OK or page is None:
            return status, page
        if self._reopen_launch_environment() != reopened:
            self.discard(capability)
            return CapabilityResolutionStatus.INVALID, None
        return CapabilityResolutionStatus.OK, page

    def validate_launch_environment(self) -> bool:
        """Prove that this process holds a current durable explorer authority."""
        return self._reopen_launch_environment() is not None

    def get_page(
        self,
        *,
        capability: str,
        owner_id: str,
        role: str,
        session_id: str,
        page_size: int,
        cursor: ContinuationCursor | None = None,
    ) -> tuple[CapabilityResolutionStatus, EvidencePage | None]:
        """Return one canonical page without exposing another capability's state."""
        resolved = self.resolve(
            capability=capability,
            owner_id=owner_id,
            role=role,
            session_id=session_id,
        )
        if resolved.status is not CapabilityResolutionStatus.OK:
            return resolved.status, None
        context = resolved.value
        if not isinstance(context, ExplorationContext):
            return CapabilityResolutionStatus.INVALID, None
        if self._service is None:
            return CapabilityResolutionStatus.INVALID, None
        return (
            CapabilityResolutionStatus.OK,
            self._service.page(context, page_size=page_size, cursor=cursor),
        )

    def discard(self, capability: str) -> None:
        """Delete one capability without revealing whether it existed."""
        if not self._is_capability_shape(capability):
            return
        with self._lock:
            self._discard_locked(capability)

    def cleanup_session(self, session_id: str) -> None:
        self._validate_binding(owner_id="server", role="server", session_id=session_id)
        with self._lock:
            authority_path = self._session_authority_paths.pop(session_id, None)
            if authority_path is not None:
                self._launch_authorities.delete(authority_path)
            self._discard_session_locked(session_id)

    def cleanup_expired(self) -> int:
        with self._lock:
            return self._cleanup_expired_locked()

    def close(self) -> None:
        """Remove every entry when the owning ToolContext is retired."""
        with self._lock:
            for authority_path in self._session_authority_paths.values():
                self._launch_authorities.delete(authority_path)
            self._session_authority_paths.clear()
            self._leases.clear()
            self._session_capabilities.clear()
            self._closed = True

    def _cleanup_expired_locked(self) -> int:
        now = self._clock()
        expired = [key for key, lease in self._leases.items() if lease.expires_at <= now]
        for key in expired:
            self._discard_locked(key)
        for session_id, authority_path in tuple(self._session_authority_paths.items()):
            if session_id not in self._session_capabilities:
                self._launch_authorities.delete(authority_path)
                del self._session_authority_paths[session_id]
        return len(expired)

    def _issue_locked(
        self,
        *,
        owner_id: str,
        role: str,
        session_id: str,
        value: _T,
        ttl: float,
        cwd: Path | None = None,
        repository_root: Path | None = None,
        source_identity: str = "",
        snapshot_digest: str = "",
    ) -> str:
        if len(self._leases) >= self._max_active_leases:
            raise RuntimeError("exploration context store capacity exceeded")
        capability = self._new_capability_locked()
        self._leases[capability] = _CapabilityLease(
            owner_id=owner_id,
            role=role,
            session_id=session_id,
            expires_at=self._clock() + ttl,
            value=value,
            cwd=cwd,
            repository_root=repository_root,
            source_identity=source_identity,
            snapshot_digest=snapshot_digest,
        )
        self._session_capabilities.setdefault(session_id, set()).add(capability)
        return capability

    def _resolve_capability_locked(self, capability: str) -> _CapabilityLease[_T] | None:
        if not self._is_capability_shape(capability):
            return None
        lease = self._leases.get(capability)
        if lease is None:
            return None
        if lease.expires_at <= self._clock():
            self._discard_locked(capability)
            return None
        return lease

    def _discard_locked(self, capability: str) -> None:
        lease = self._leases.pop(capability, None)
        if lease is None:
            return
        capabilities = self._session_capabilities.get(lease.session_id)
        if capabilities is None:
            return
        capabilities.discard(capability)
        if not capabilities:
            del self._session_capabilities[lease.session_id]
            authority_path = self._session_authority_paths.get(lease.session_id)
            if authority_path is not None:
                self._launch_authorities.delete(authority_path)
                del self._session_authority_paths[lease.session_id]

    def _discard_session_locked(self, session_id: str) -> None:
        for capability in tuple(self._session_capabilities.get(session_id, ())):
            self._discard_locked(capability)

    def _reopen_launch_environment(
        self,
    ) -> tuple[str, _ReopenedLaunchAuthority] | None:
        """Reopen only a current, verifier-matched durable child authority."""
        reopened = self._launch_authorities.load_from_environment()
        if reopened is None:
            return None
        capability, authority = reopened
        with self._lock:
            if self._closed or authority.repository_root != self._trusted_root:
                return None
            current = self._resolve_capability_locked(capability)
            expected = (
                authority.session_id,
                authority.cwd,
                authority.repository_root,
                authority.source_identity,
                authority.snapshot_digest,
            )
            if (
                current is not None
                and (
                    current.session_id,
                    current.cwd,
                    current.repository_root,
                    current.source_identity,
                    current.snapshot_digest,
                )
                == expected
                and current.role == EXPLORATION_PRINCIPAL_ROLE
            ):
                return reopened
            if current is not None:
                self._discard_locked(capability)
            if len(self._leases) >= self._max_active_leases:
                self._cleanup_expired_locked()
            if len(self._leases) >= self._max_active_leases:
                return None
            self._leases[capability] = _CapabilityLease(
                owner_id="launch-authority",
                role=EXPLORATION_PRINCIPAL_ROLE,
                session_id=authority.session_id,
                expires_at=self._clock()
                + min(
                    self._max_ttl_seconds,
                    max(0.0, authority.expires_at - time.time()),
                ),
                value=cast(_T, None),
                cwd=authority.cwd,
                repository_root=self._trusted_root,
                source_identity=authority.source_identity,
                snapshot_digest=authority.snapshot_digest,
            )
            self._session_capabilities.setdefault(authority.session_id, set()).add(capability)
            return reopened

    @staticmethod
    def _shared_source_identity(source_identities: Mapping[str, str]) -> str:
        """Bind one principal to the complete current explorer definition set."""
        records = [
            {"role": role, "source_identity": source_identities[role]}
            for role in sorted(source_identities)
        ]
        digest = hashlib.sha256(
            _SHARED_SOURCE_IDENTITY_DOMAIN + canonical_json_bytes(records)
        ).hexdigest()
        return f"sha256:{digest}"

    def _new_capability_locked(self) -> str:
        # Cryptographic collision is implausible; the loop preserves the
        # dictionary's single-source-of-truth invariant.
        while True:
            capability = f"explore_{secrets.token_urlsafe(32)}"
            if capability not in self._leases:
                return capability

    @staticmethod
    def _validate_binding(*, owner_id: str, role: str, session_id: str) -> None:
        for field_name, value in (
            ("owner_id", owner_id),
            ("role", role),
            ("session_id", session_id),
        ):
            if not isinstance(value, str) or not value or len(value) > _MAX_CAPABILITY_LENGTH:
                raise ValueError(f"{field_name} must be a non-empty bounded string")

    @staticmethod
    def _is_capability_shape(value: str) -> bool:
        return (
            isinstance(value, str)
            and value.startswith("explore_")
            and len(value) <= _MAX_CAPABILITY_LENGTH
        )
