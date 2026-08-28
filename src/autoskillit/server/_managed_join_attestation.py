"""Server-owned issuance and verification for managed-join adaptation evidence."""

from __future__ import annotations

import threading
from collections.abc import Callable

from autoskillit.core import (
    MANAGED_JOIN_ATTESTATION_SCHEMA_VERSION,
    ManagedJoinAttestation,
    SemanticAdaptationContext,
)

__all__ = ["DefaultManagedJoinAttestationAuthority"]


class DefaultManagedJoinAttestationAuthority:
    """Retain only contexts issued by this server process and activation epoch."""

    def __init__(self) -> None:
        self._activation_epoch = 0
        self._issued: dict[str, SemanticAdaptationContext] = {}
        self._lock = threading.RLock()
        self._recovery_gate: Callable[[], bool] | None = None

    def set_recovery_gate(self, recovery_gate: Callable[[], bool]) -> None:
        """Require successful managed recovery before issue or revalidation."""
        with self._lock:
            self._recovery_gate = recovery_gate
            self._issued.clear()

    @property
    def activation_epoch(self) -> int:
        with self._lock:
            return self._activation_epoch

    def rotate_activation_epoch(self) -> int:
        """Invalidate unopened contexts after a visibility or hook-state transition."""
        with self._lock:
            self._activation_epoch += 1
            self._issued.clear()
            return self._activation_epoch

    def issue(
        self,
        *,
        backend: str,
        launch_context: str,
        parent_session_id: str,
        direct_tool_mode: bool,
        resolved_model: str,
        fixed_batch_tool_registry_digest: str,
        hook_registry_digest: str,
        skill_load_applies: bool,
        guards_apply: bool,
    ) -> SemanticAdaptationContext:
        with self._lock:
            if self._recovery_gate is not None and not self._recovery_gate():
                raise RuntimeError("managed join attestation is blocked by recovery")
            context = SemanticAdaptationContext(
                managed_join_attestation=ManagedJoinAttestation(
                    schema_version=MANAGED_JOIN_ATTESTATION_SCHEMA_VERSION,
                    backend=backend,
                    launch_context=launch_context,
                    parent_session_id=parent_session_id,
                    activation_epoch=self._activation_epoch,
                    direct_tool_mode=direct_tool_mode,
                    resolved_model=resolved_model,
                    fixed_batch_tool_registry_digest=fixed_batch_tool_registry_digest,
                    hook_registry_digest=hook_registry_digest,
                    skill_load_applies=skill_load_applies,
                    guards_apply=guards_apply,
                    provenance="autoskillit-server",
                )
            )
            self._issued[context.digest] = context
            return context

    def verify(
        self,
        context: SemanticAdaptationContext | None,
        *,
        backend: str,
        parent_session_id: str,
    ) -> SemanticAdaptationContext | None:
        if context is None:
            return None
        with self._lock:
            issued = self._issued.get(context.digest)
            attestation = context.managed_join_attestation
            if (
                (self._recovery_gate is not None and not self._recovery_gate())
                or issued != context
                or attestation is None
                or attestation.backend != backend
                or attestation.parent_session_id != parent_session_id
                or attestation.activation_epoch != self._activation_epoch
                or not attestation.admits_backend(backend)
            ):
                return None
            return issued

    def find_verified_context(
        self,
        *,
        backend: str,
        parent_session_id: str,
    ) -> SemanticAdaptationContext | None:
        """Return the sole server-issued context for one live managed parent."""
        with self._lock:
            matches = [
                context
                for context in self._issued.values()
                if context.managed_join_attestation is not None
                and context.managed_join_attestation.backend == backend
                and context.managed_join_attestation.parent_session_id == parent_session_id
            ]
        if len(matches) != 1:
            return None
        return self.verify(matches[0], backend=backend, parent_session_id=parent_session_id)
