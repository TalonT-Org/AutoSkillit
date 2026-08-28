"""``OwnerBoundExplorationContextStore`` — sole production aggregate lease-state owner."""

from __future__ import annotations

import hmac
import secrets
import threading
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Generic, Literal, NoReturn, assert_never, cast

from autoskillit.core import (
    CapabilityResolution,
    CapabilityResolutionStatus,
    ContinuationCursor,
    EvidencePage,
    ExplorationQuerySpec,
    SnapshotCaptureReason,
    SnapshotCaptureStatus,
    SnapshotUnavailable,
)
from autoskillit.pipeline.exploration_context_durable import (
    EXPLORATION_PRINCIPAL_ROLE,
    _ExplorationLaunchAuthorityStore,
    _ReopenedLaunchAuthority,
)

from . import _launch_adapter as launch_adapter
from ._constants import (
    _MAX_ACTIVE_LEASES,
    _MAX_CAPABILITY_LENGTH,
    _MAX_SOURCE_IDENTITY_LENGTH,
    _MAX_TTL_SECONDS,
    EXPLORER_ROLE_NAMES,
)
from ._types import (
    _T,
    ExplorationContext,
    ExplorationLaunchBinding,
    ExplorationServiceProtocol,
    _CapabilityLease,
)


class OwnerBoundExplorationContextStore(Generic[_T]):
    """Keep opaque exploration state for the lifetime of one server process.

    Callers may retain a capability only as an opaque handle.  Resolution is
    deliberately bound to the authenticated owner, registered explorer role,
    and backend session; a leaked handle therefore cannot be replayed by a
    different child.  Entries are removed eagerly when expired, discarded, or
    when the containing server lifecycle closes.
    """

    class TrustedRootMismatch(ValueError):
        """repository_root does not match the bound session's trusted root."""

    class InvalidSourceIdentity(ValueError):
        """source_identity is missing or exceeds the bounded length."""

    class InvalidSessionBinding(ValueError):
        """owner_id, role, or session_id is missing or exceeds the bounded length."""

    class ServiceNotConfigured(RuntimeError):
        """exploration service is not configured."""

    class SnapshotStale(ValueError):
        """exploration issuance requires a complete immutable snapshot."""

        def __init__(self, reason: SnapshotCaptureReason, detail: str) -> None:
            self.reason = reason
            super().__init__(detail)

    class SnapshotTruncated(ValueError):
        """exploration issuance's repository snapshot capture was truncated."""

        def __init__(self, reason: SnapshotCaptureReason, detail: str) -> None:
            self.reason = reason
            super().__init__(detail)

    class SnapshotCaptureFailed(RuntimeError):
        """exploration issuance's repository snapshot capture failed outright."""

        def __init__(self, reason: SnapshotCaptureReason, detail: str) -> None:
            self.reason = reason
            super().__init__(detail)

    class StoreClosed(RuntimeError):
        """exploration context store is closed."""

    class CapacityExceeded(RuntimeError):
        """exploration context store capacity exceeded."""

    def _raise_for_snapshot_unavailable(self, exc: SnapshotUnavailable) -> NoReturn:
        """Translate a capture-layer failure into the one matching store exception.

        Shared by ``bind_session_scoped`` and ``_bind_launches`` so a stale
        or truncated snapshot always raises the same typed exception.
        Exhaustive over every :class:`SnapshotCaptureStatus` member; a
        future status without a translation is a mypy ``assert_never``
        failure, not a silent swallow.
        """
        reason = exc.reason
        assert reason is not None, "a non-COMPLETE SnapshotUnavailable must carry a reason"
        match exc.status:
            case SnapshotCaptureStatus.TRUNCATED:
                raise self.SnapshotTruncated(reason, exc.detail) from exc
            case SnapshotCaptureStatus.STALE:
                raise self.SnapshotStale(reason, exc.detail) from exc
            case SnapshotCaptureStatus.FAILED:
                raise self.SnapshotCaptureFailed(reason, exc.detail) from exc
            case SnapshotCaptureStatus.COMPLETE:
                raise AssertionError(
                    "SnapshotUnavailable must not be raised for a COMPLETE capture"
                ) from exc
            case _ as unreachable:
                assert_never(unreachable)

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
        return launch_adapter.verified_repository_root_from_launch_environment()

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
        origin: Literal["session", "launch"],
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
                origin=origin,
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
        """Atomically replace a session's capability with trusted launch material."""
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
        try:
            issuance_snapshot = self._service.capture_snapshot(canonical_repository_root)
        except SnapshotUnavailable as exc:
            self._raise_for_snapshot_unavailable(exc)
        snapshot_digest = issuance_snapshot.digest
        shared_source_identity = launch_adapter._shared_source_identity(source_identities)
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
                origin="launch",
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

        Claude-native path: subagents share the parent process; per-child
        env binding is structurally impossible.
        """
        self._validate_binding(owner_id=owner_id, role="server", session_id=session_id)
        if not source_identity or len(source_identity) > _MAX_SOURCE_IDENTITY_LENGTH:
            raise self.InvalidSourceIdentity("source_identity must be bounded non-empty text")
        canonical_cwd = cwd.resolve()
        canonical_repository_root = repository_root.resolve()
        if canonical_repository_root != self._trusted_root:
            raise self.TrustedRootMismatch("repository_root does not match the trusted root")
        if self._service is None:
            raise self.ServiceNotConfigured("exploration service is not configured")
        try:
            issuance_snapshot = self._service.capture_snapshot(canonical_repository_root)
        except SnapshotUnavailable as exc:
            self._raise_for_snapshot_unavailable(exc)
        with self._lock:
            if self._closed:
                raise self.StoreClosed("exploration context store is closed")
            self._cleanup_expired_locked()
            replaced_count = len(self._session_capabilities.get(session_id, ()))
            if len(self._leases) - replaced_count + 1 > self._max_active_leases:
                raise self.CapacityExceeded("exploration context store capacity exceeded")
            capability = self._new_capability_locked()
            self._discard_session_locked(session_id)
            self._leases[capability] = _CapabilityLease(
                owner_id=owner_id,
                role=EXPLORATION_PRINCIPAL_ROLE,
                session_id=session_id,
                expires_at=self._clock() + self._max_ttl_seconds,
                value=cast(_T, None),
                origin="session",
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

    def has_session_scoped_binding(self) -> bool:
        """Return whether any live session-origin lease is currently bound.

        Counts only ``origin == "session"`` leases — a launch-mode binding
        must not be reported as an available session-scoped broker.
        """
        now = self._clock()
        with self._lock:
            return any(
                lease.origin == "session" and lease.expires_at > now
                for lease in self._leases.values()
            )

    def lease_for_capability(self, capability: str) -> _CapabilityLease[_T] | None:
        """Return the active lease for an already-minted capability, else None.

        Used by ``bind_session_scoped_durable`` so the durable authority
        file records the same values as the in-memory lease.
        """
        with self._lock:
            lease = self._leases.get(capability)
            return lease if lease is not None and lease.expires_at > self._clock() else None

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
            origin="session",
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
                origin,
                cwd,
                repository_root,
                source_identity,
                snapshot_digest,
            ) = (
                lease.owner_id,
                lease.role,
                lease.session_id,
                lease.origin,
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
                current.origin,
                current.cwd,
                current.repository_root,
                current.source_identity,
                current.snapshot_digest,
            ) != (
                owner_id,
                role,
                session_id,
                origin,
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
                origin=origin,
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
        return launch_adapter.submit_from_launch_environment(
            self, query, page_size, self._max_ttl_seconds, self._clock
        )

    def get_page_from_launch_environment(
        self,
        *,
        page_size: int,
        cursor: ContinuationCursor | None = None,
    ) -> tuple[CapabilityResolutionStatus, EvidencePage | None]:
        """Read an active page while proving the durable authority is still live."""
        return launch_adapter.get_page_from_launch_environment(
            self, page_size, cursor, self._max_ttl_seconds, self._clock
        )

    def validate_launch_environment(self) -> bool:
        """Prove that this process holds a current durable explorer authority."""
        return launch_adapter.validate_launch_environment(self, self._max_ttl_seconds, self._clock)

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
        origin: Literal["session", "launch"],
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
            origin=origin,
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
        return launch_adapter.reopen_launch_environment(
            self,
            max_ttl_seconds=self._max_ttl_seconds,
            clock=self._clock,
        )

    def _new_capability_locked(self) -> str:
        # Collision is implausible; the loop preserves the dict's single-source invariant.
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
                raise OwnerBoundExplorationContextStore.InvalidSessionBinding(
                    f"{field_name} must be a non-empty bounded string"
                )

    @staticmethod
    def _is_capability_shape(value: str) -> bool:
        return (
            isinstance(value, str)
            and value.startswith("explore_")
            and len(value) <= _MAX_CAPABILITY_LENGTH
        )
