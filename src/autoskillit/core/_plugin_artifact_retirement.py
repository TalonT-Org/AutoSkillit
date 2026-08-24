"""Shared exact-identity retirement engine (``PluginArtifactRetirementEngine``).

Calls into ``_retiring_cache`` for lock acquisition and mutation primitives;
the lifecycle-lock invariant is preserved by importing ``_InstallLock`` from
that module rather than re-implementing the lock.
"""

from __future__ import annotations

import os
import shutil
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ._retiring_cache import (
    _InstallLock,
    _retirement_staging_path,
    append_retiring_record,
    is_reclaimable_artifact_path,
    read_retiring_cache,
    remove_retiring_records,
)
from .logging import log_plugin_artifact_lifecycle
from .paths import destination_location
from .runtime.artifact_lease import ArtifactLease, ArtifactLeaseContention
from .types import (
    LegacyRetiringEvidence,
    ManagedHome,
    PluginArtifactIdentity,
    PluginArtifactKind,
    PluginArtifactUnavailableError,
    PluginArtifactValidationError,
    RetirementOutcome,
    RetiringAppendResult,
    RetiringArtifactRecord,
    RetiringCacheState,
)


class PluginArtifactRetirementEngine:
    """Shared exact-identity retirement algorithm parameterized by artifact hooks."""

    def __init__(
        self,
        *,
        home: ManagedHome,
        managed_root: Path,
        artifact_kind: PluginArtifactKind,
        manifest_path: Callable[[Path], Path],
        lease_path: Callable[[Path], Path],
        current_identity: Callable[[RetiringArtifactRecord], PluginArtifactIdentity],
        logger: Any,
        is_current: Callable[[Path], bool] | None,
    ) -> None:
        self._home = home
        self.managed_root = Path(managed_root).expanduser().resolve(strict=False)
        self.artifact_kind = artifact_kind
        self._manifest_path = manifest_path
        self._lease_path = lease_path
        self._current_identity = current_identity
        self._logger = logger
        self._is_current = is_current

    def contains(self, path: Path) -> bool:
        """Return whether *path* is a child artifact owned by this engine."""
        try:
            location = destination_location(Path(path))
        except (OSError, ValueError):
            return False
        return location != self.managed_root and location.is_relative_to(self.managed_root)

    def enqueue_retirement(
        self,
        identity: PluginArtifactIdentity,
        not_before: datetime,
        *,
        on_persisted: Callable[[str], None] | None = None,
    ) -> RetiringAppendResult | None:
        """Queue one exact incarnation after validating owner-specific paths."""
        if not self.contains(identity.managed_path):
            raise PluginArtifactValidationError(
                f"{self.artifact_kind.value} artifact is outside managed root: "
                f"{identity.managed_path}"
            )
        if not self._home.contains(identity.managed_path):
            raise PluginArtifactValidationError(
                f"{self.artifact_kind.value} artifact at {identity.managed_path} is outside "
                f"the managed home this queue writes to ({self._home.root}); the validating "
                "root and the writing root disagree"
            )
        if identity.manifest_path != self._manifest_path(identity.managed_path):
            raise PluginArtifactValidationError(
                f"{self.artifact_kind.value} artifact manifest path is not canonical: "
                f"{identity.manifest_path}"
            )
        retired_at = datetime.now(UTC)
        if not_before.tzinfo is not None and not_before.utcoffset() is not None:
            retired_at = min(retired_at, not_before.astimezone(UTC))
        result = append_retiring_record(
            RetiringArtifactRecord(
                record_id=uuid.uuid4().hex,
                artifact_kind=self.artifact_kind,
                semantic_key=identity.semantic_key,
                managed_path=identity.managed_path,
                manifest_path=identity.manifest_path,
                incarnation_id=identity.incarnation_id,
                manifest_schema_version=identity.manifest_schema_version,
                artifact_digest=identity.artifact_digest,
                retired_at=retired_at,
                not_before=not_before,
            ),
            home=self._home,
            on_persisted=on_persisted,
        )
        if result is None:
            return None
        log_plugin_artifact_lifecycle(
            self._logger,
            action="retire",
            outcome="succeeded",
            artifact_kind=self.artifact_kind.value,
            semantic_key=identity.semantic_key,
            incarnation=identity.incarnation_id,
            not_before=not_before,
        )
        return result

    def cancel_obsolete_retirements(
        self,
        identity: PluginArtifactIdentity,
    ) -> tuple[str, ...] | None:
        state = read_retiring_cache(home=self._home)
        if state.state is RetiringCacheState.ABSENT:
            return ()
        if state.state is not RetiringCacheState.EXACT_V2:
            log_plugin_artifact_lifecycle(
                self._logger,
                action="cancel_retirement",
                outcome="deferred_unreadable_queue",
                artifact_kind=self.artifact_kind.value,
                semantic_key=identity.semantic_key,
                incarnation=identity.incarnation_id,
                contention_detail=f"retiring cache is unreadable: {state.state.value}",
            )
            return None
        record_ids = tuple(
            record.record_id
            for record in state.records
            if record.artifact_kind is self.artifact_kind
            and record.managed_path == identity.managed_path
        ) + tuple(
            evidence.record_id
            for evidence in state.legacy_evidence
            if evidence.recognized_kind is self.artifact_kind
            and Path(evidence.path) == identity.managed_path
        )
        if not record_ids:
            return ()
        if remove_retiring_records(record_ids, home=self._home) is None:
            return None
        log_plugin_artifact_lifecycle(
            self._logger,
            action="cancel_retirement",
            outcome="succeeded",
            artifact_kind=self.artifact_kind.value,
            semantic_key=identity.semantic_key,
            incarnation=identity.incarnation_id,
        )
        return record_ids

    def try_reclaim(self, record: RetiringArtifactRecord, now: datetime) -> RetirementOutcome:
        """Reclaim one queued record only while its lease and identity remain exact."""
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("artifact retirement sweep time must be timezone-aware")
        now = now.astimezone(UTC)
        if record.artifact_kind is not self.artifact_kind:
            return self._log_reclaim(record, RetirementOutcome.REJECTED_IDENTITY)
        if now < record.not_before:
            return RetirementOutcome.DEFERRED_NOT_DUE
        if not self.contains(record.managed_path):
            return self._log_reclaim(record, RetirementOutcome.REJECTED_IDENTITY)
        try:
            writer = ArtifactLease.acquire_exclusive(
                self._lease_path(record.managed_path),
                blocking=False,
            )
        except ArtifactLeaseContention as exc:
            return self._log_reclaim(
                record,
                RetirementOutcome.DEFERRED_CONTENDED,
                detail=str(exc),
            )
        except (OSError, RuntimeError) as exc:
            return self._log_reclaim(
                record,
                RetirementOutcome.DEFERRED_IO_ERROR,
                detail=str(exc),
            )
        try:
            with _InstallLock(self._home):
                state = read_retiring_cache(home=self._home)
                if state.state is RetiringCacheState.ABSENT:
                    return RetirementOutcome.RECORD_REMOVED
                if state.state is not RetiringCacheState.EXACT_V2:
                    return self._log_reclaim(
                        record,
                        RetirementOutcome.DEFERRED_IO_ERROR,
                        detail=f"retiring cache became unsafe: {state.state.value}",
                    )
                queued = next(
                    (
                        current
                        for current in state.records
                        if current.record_id == record.record_id
                    ),
                    None,
                )
                if queued is None:
                    return RetirementOutcome.RECORD_REMOVED
                if queued != record:
                    return self._log_reclaim(
                        record,
                        RetirementOutcome.REJECTED_IDENTITY,
                    )
                if now < queued.not_before:
                    return RetirementOutcome.DEFERRED_NOT_DUE
                if self._is_current is not None and self._is_current(record.managed_path):
                    return self._log_reclaim(
                        record,
                        RetirementOutcome.DEFERRED_CONTENDED,
                        detail="managed_path is the actively selected generation",
                    )
                staging_path = _retirement_staging_path(record)
                managed_exists = record.managed_path.exists() or record.managed_path.is_symlink()
                manifest_exists = (
                    record.manifest_path.exists() or record.manifest_path.is_symlink()
                )
                staging_exists = staging_path.exists() or staging_path.is_symlink()
                if not managed_exists and not manifest_exists and not staging_exists:
                    if remove_retiring_records((record.record_id,), home=self._home) is None:
                        return self._log_reclaim(
                            record,
                            RetirementOutcome.DEFERRED_IO_ERROR,
                            detail="retiring cache became unsafe while removing record",
                        )
                    return RetirementOutcome.RECORD_REMOVED
                if staging_exists:
                    if managed_exists or staging_path.is_symlink() or not staging_path.is_dir():
                        return self._log_reclaim(
                            record,
                            RetirementOutcome.DEFERRED_IO_ERROR,
                            detail=f"retirement staging path is ambiguous: {staging_path}",
                        )
                else:
                    try:
                        current = self._current_identity(record)
                    except PluginArtifactUnavailableError as exc:
                        return self._log_reclaim(
                            record,
                            RetirementOutcome.DEFERRED_IO_ERROR,
                            detail=str(exc),
                        )
                    except PluginArtifactValidationError:
                        if remove_retiring_records((record.record_id,), home=self._home) is None:
                            return self._log_reclaim(
                                record,
                                RetirementOutcome.DEFERRED_IO_ERROR,
                                detail="retiring cache became unsafe while rejecting identity",
                            )
                        return self._log_reclaim(
                            record,
                            RetirementOutcome.REJECTED_IDENTITY,
                            failed_validation=True,
                        )
                    if current != record.identity:
                        if remove_retiring_records((record.record_id,), home=self._home) is None:
                            return self._log_reclaim(
                                record,
                                RetirementOutcome.DEFERRED_IO_ERROR,
                                detail="retiring cache became unsafe while rejecting identity",
                            )
                        return self._log_reclaim(
                            record,
                            RetirementOutcome.REJECTED_IDENTITY,
                        )
                    try:
                        os.rename(record.managed_path, staging_path)
                    except OSError as exc:
                        return self._log_reclaim(
                            record,
                            RetirementOutcome.DEFERRED_IO_ERROR,
                            detail=str(exc),
                        )
                try:
                    if record.manifest_path.is_file() or record.manifest_path.is_symlink():
                        record.manifest_path.unlink()
                    elif record.manifest_path.exists():
                        raise OSError(
                            f"retirement manifest is not removable: {record.manifest_path}"
                        )
                    shutil.rmtree(staging_path)
                    if remove_retiring_records((record.record_id,), home=self._home) is None:
                        return self._log_reclaim(
                            record,
                            RetirementOutcome.DEFERRED_IO_ERROR,
                            detail="retiring cache became unsafe after artifact removal",
                        )
                except OSError as exc:
                    return self._log_reclaim(
                        record,
                        RetirementOutcome.DEFERRED_IO_ERROR,
                        detail=str(exc),
                    )
                return self._log_reclaim(record, RetirementOutcome.RECLAIMED)
        finally:
            writer.close_preserving()

    def try_promote_legacy_evidence(
        self,
        evidence: LegacyRetiringEvidence,
        now: datetime,
        *,
        identity_for_path: Callable[[Path], PluginArtifactIdentity],
    ) -> RetirementOutcome:
        """Promote path-only legacy evidence into an exact v2 record.

        Migrated v1 evidence carries no incarnation or digest, so it can never
        authorize deletion by itself. This re-derives an exact identity from
        disk and hands it to the normal queue; the artifact is then removed by
        ``try_reclaim`` under the same lease and identity checks as any other
        record. Nothing is deleted here.

        Eligibility is re-derived from scratch rather than trusting the stored
        ``recognized_kind`` — evidence already persisted with a wrong kind
        must not become authority now.
        """
        if evidence.recognized_kind is not self.artifact_kind:
            return RetirementOutcome.LEGACY_EVIDENCE
        try:
            path = destination_location(Path(evidence.path))
        except (OSError, TypeError, ValueError):
            return RetirementOutcome.LEGACY_EVIDENCE
        if not is_reclaimable_artifact_path(path, self.managed_root):
            return RetirementOutcome.LEGACY_EVIDENCE
        if not self.contains(path):
            return RetirementOutcome.LEGACY_EVIDENCE
        if not path.exists():
            # Nothing left to protect (also covers a broken symlink: exists()
            # follows the link and is False when the target is gone).
            if remove_retiring_records((evidence.record_id,), home=self._home) is None:
                return RetirementOutcome.DEFERRED_IO_ERROR
            return RetirementOutcome.RECORD_REMOVED
        try:
            writer = ArtifactLease.acquire_exclusive(self._lease_path(path), blocking=False)
        except ArtifactLeaseContention:
            return RetirementOutcome.DEFERRED_CONTENDED
        except (OSError, RuntimeError) as exc:
            self._logger.warning(
                "plugin_artifact_legacy_promotion_lease_failed",
                artifact_kind=self.artifact_kind.value,
                path=str(path),
                error=str(exc),
            )
            return RetirementOutcome.DEFERRED_IO_ERROR
        try:
            if self._is_current is not None and self._is_current(path):
                return RetirementOutcome.DEFERRED_CONTENDED
            try:
                identity = identity_for_path(path)
            except (
                PluginArtifactValidationError,
                PluginArtifactUnavailableError,
                OSError,
            ):
                # Cannot positively identify it; never delete on ambiguity.
                return RetirementOutcome.LEGACY_EVIDENCE
            appended = self.enqueue_retirement(identity, now)
            if appended is None:
                return RetirementOutcome.DEFERRED_IO_ERROR
            created = appended.created
            if remove_retiring_records((evidence.record_id,), home=self._home) is None:
                return RetirementOutcome.DEFERRED_IO_ERROR
        finally:
            writer.close_preserving()
        return RetirementOutcome.RECLAIMED if created else RetirementOutcome.RECORD_REMOVED

    def _log_reclaim(
        self,
        record: RetiringArtifactRecord,
        outcome: RetirementOutcome,
        *,
        detail: str | None = None,
        failed_validation: bool = False,
    ) -> RetirementOutcome:
        event_outcome = {
            RetirementOutcome.RECLAIMED: "succeeded",
            RetirementOutcome.DEFERRED_CONTENDED: "deferred_contended",
            RetirementOutcome.DEFERRED_IO_ERROR: "deferred_io_error",
            RetirementOutcome.REJECTED_IDENTITY: "rejected_identity",
        }[outcome]
        if failed_validation:
            event_outcome = "failed_validation"
        log_plugin_artifact_lifecycle(
            self._logger,
            action="reclaim",
            outcome=event_outcome,
            artifact_kind=self.artifact_kind.value,
            semantic_key=record.semantic_key,
            incarnation=record.incarnation_id,
            not_before=record.not_before,
            contention_detail=detail,
        )
        return outcome
