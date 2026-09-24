"""Server-owned issuance and revalidation for managed-join evidence.

Trust boundary covers honest-path erasure and drift — overrides, stale homes,
refreshed catalogs, missing guards, mismatched code versions, or missing
issuers cannot admit a join-required skill without evidence. Deliberate forgery
under paths a model owns is out of scope, as it is for the Claude route.
"""

from __future__ import annotations

import fcntl
import os
import threading
from collections.abc import Callable, Generator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from autoskillit.core import (
    CODEX_HOME_ENV_VAR,
    MANAGED_JOIN_ATTESTATION_SCHEMA_VERSION,
    CodingAgentBackend,
    ManagedCodexRoute,
    ManagedJoinAttestation,
    ManagedJoinRefusalReason,
    ManagedJoinVerificationRefusal,
    SemanticAdaptationContext,
    SkillContractError,
    get_logger,
    managed_route_backend,
    read_versioned_json,
    write_versioned_json,
)
from autoskillit.execution.backends import (
    MANAGED_CODEX_ROUTE_NAMES,
    managed_codex_guard_set,
    managed_codex_route_digest,
    managed_codex_route_for_launch_context,
)
from autoskillit.hook_registry import HOOK_REGISTRY_HASH
from autoskillit.hooks._runtime._hook_settings import validate_session_id
from autoskillit.hooks._session_binding import (
    SESSION_BINDING_SCHEMA_VERSION,
    SessionBinding,
    SessionBindingError,
    binding_lock,
    loaded_skill_from_manifest,
    merge_binding,
    read_binding,
    read_manifest,
    resolve_channel_dir,
    write_binding,
)

logger = get_logger(__name__)


def _refuse(reason: ManagedJoinRefusalReason, *detail: str) -> ManagedJoinVerificationRefusal:
    logger.warning("managed_join_verification_refused", reason=reason.value, detail=detail)
    return ManagedJoinVerificationRefusal(reason, detail)


class ManagedJoinRecordStore:
    """Durably retain one server-issued attestation per managed parent."""

    def __init__(self, state_root: Path) -> None:
        self._state_root = Path(state_root)

    def path_for(self, parent_session_id: str) -> Path:
        validated_id = validate_session_id(parent_session_id)
        return resolve_channel_dir(self._state_root) / (
            f"managed_join_attestation_{validated_id}.json"
        )

    @contextmanager
    def _write_lock(self, record_path: Path) -> Generator[None, None, None]:
        lock_path = record_path.with_name(f"{record_path.name}.lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR | os.O_CLOEXEC, 0o600)
        locked = False
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            locked = True
            yield
        finally:
            if locked:
                fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def write(self, context: SemanticAdaptationContext, *, route: str) -> None:
        attestation = context.managed_join_attestation
        if attestation is None:
            raise ValueError("managed join record requires an attestation")
        record_path = self.path_for(attestation.parent_session_id)
        document = {
            "route": route,
            "attestation": dict(attestation.canonical_payload),
        }
        with self._write_lock(record_path):
            write_versioned_json(
                record_path,
                document,
                MANAGED_JOIN_ATTESTATION_SCHEMA_VERSION,
            )

    def load(
        self, parent_session_id: str
    ) -> tuple[ManagedJoinAttestation, ManagedCodexRoute] | None:
        record_path = self.path_for(parent_session_id)
        try:
            document = read_versioned_json(
                record_path,
                MANAGED_JOIN_ATTESTATION_SCHEMA_VERSION,
            )
            if (
                not isinstance(document, dict)
                or document.get("route") not in MANAGED_CODEX_ROUTE_NAMES
                or not isinstance(document.get("attestation"), dict)
            ):
                return None
            attestation = ManagedJoinAttestation(**document["attestation"])
        except (OSError, TypeError, ValueError):
            return None
        if attestation.parent_session_id != parent_session_id:
            return None
        return attestation, cast(ManagedCodexRoute, document["route"])


def _write_managed_parent_binding(
    *,
    binding_path: Path,
    binding_session_id: str,
    normalized_skill_name: str,
    backend: CodingAgentBackend,
    attestation: ManagedJoinAttestation,
) -> None:
    """Write the live-home source binding for one attested managed parent."""
    managed = managed_route_backend(backend)
    if managed is None:
        raise SkillContractError("run_fixed_batch managed backend cannot locate its projection")
    home_text = os.environ.get(CODEX_HOME_ENV_VAR)
    if not home_text:
        raise SkillContractError("run_fixed_batch managed binding requires CODEX_HOME to be set")
    try:
        manifest = read_manifest(managed.projected_manifest_path(Path(home_text)))
        entry = loaded_skill_from_manifest(
            manifest,
            normalized_skill_name,
            datetime.now(UTC).isoformat(),
        )
    except SessionBindingError as exc:
        raise SkillContractError(
            "run_fixed_batch skill is not projected in the live generated home"
        ) from exc
    route = managed_codex_route_for_launch_context(attestation.launch_context)
    expected_guards = tuple(sorted(managed_codex_guard_set(route)))
    with binding_lock(binding_path):
        existing = read_binding(binding_path)
        if existing is None:
            binding = SessionBinding(
                schema_version=SESSION_BINDING_SCHEMA_VERSION,
                session_id=binding_session_id,
                join_required=entry.join_required,
                binding_valid=True,
                artifact_digest=entry.source_artifact_digest,
                loaded_skills=(entry,),
                managed_parent_id=binding_session_id,
                managed_leaf_id="",
                managed_route=route,
                managed_guard_set=expected_guards,
                managed_config_digest=attestation.hook_registry_digest,
            )
        else:
            binding = merge_binding(
                existing,
                session_id=binding_session_id,
                new_entry=entry,
                artifact_digest=entry.source_artifact_digest,
                managed_parent_id=binding_session_id,
                managed_route=route,
                managed_guard_set=expected_guards,
                managed_config_digest=attestation.hook_registry_digest,
            )
            if (
                binding.session_id != binding_session_id
                or binding.managed_parent_id != binding_session_id
                or binding.managed_leaf_id
                or binding.managed_route != route
                or binding.managed_guard_set != expected_guards
                or binding.managed_config_digest != attestation.hook_registry_digest
            ):
                raise SkillContractError(
                    "run_fixed_batch binding does not match the managed parent route"
                )
        write_binding(binding_path, binding)


class DefaultManagedJoinAttestationAuthority:
    """Retain only contexts issued by this server process and activation epoch."""

    def __init__(
        self,
        *,
        record_store: ManagedJoinRecordStore | None = None,
        backend: CodingAgentBackend | None = None,
    ) -> None:
        self._activation_epoch = 0
        self._issued: dict[str, SemanticAdaptationContext] = {}
        self._lock = threading.RLock()
        self._recovery_gate: Callable[[], bool] | None = None
        self._record_store = record_store
        self._backend = backend

    def set_recovery_gate(self, recovery_gate: Callable[[], bool]) -> None:
        """Require successful managed recovery before issue or revalidation."""
        with self._lock:
            self._recovery_gate = recovery_gate
            self._issued.clear()

    @property
    def activation_epoch(self) -> int:
        with self._lock:
            return self._activation_epoch

    def issue(
        self,
        *,
        backend: str,
        launch_context: str,
        parent_session_id: str,
        direct_tool_mode: bool,
        resolved_model: str,
        resolved_reasoning_effort: str,
        codex_catalog_digest: str,
        managed_codex_catalog: bytes | None = None,
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
                    resolved_reasoning_effort=resolved_reasoning_effort,
                    codex_catalog_digest=codex_catalog_digest,
                    fixed_batch_tool_registry_digest=fixed_batch_tool_registry_digest,
                    hook_registry_digest=hook_registry_digest,
                    skill_load_applies=skill_load_applies,
                    guards_apply=guards_apply,
                    provenance="autoskillit-server",
                ),
                managed_codex_catalog=managed_codex_catalog,
            )
            if self._record_store is not None:
                self._record_store.write(
                    context,
                    route=managed_codex_route_for_launch_context(launch_context),
                )
            self._issued[context.digest] = context
            return context

    def verify(
        self,
        context: SemanticAdaptationContext | None,
        *,
        backend: str,
        parent_session_id: str,
    ) -> SemanticAdaptationContext | ManagedJoinVerificationRefusal:
        if context is None:
            return _refuse(ManagedJoinRefusalReason.NO_CONTEXT)
        with self._lock:
            issued = self._issued.get(context.digest)
            attestation = context.managed_join_attestation
            if self._recovery_gate is not None and not self._recovery_gate():
                return _refuse(ManagedJoinRefusalReason.RECOVERY_BLOCKED)
            if attestation is None:
                return _refuse(ManagedJoinRefusalReason.NO_ATTESTATION)
            if issued != context:
                return _refuse(ManagedJoinRefusalReason.NOT_ISSUED)
            if attestation.backend != backend:
                return _refuse(
                    ManagedJoinRefusalReason.BACKEND_MISMATCH,
                    f"attested {attestation.backend!r}, requested {backend!r}",
                )
            if attestation.parent_session_id != parent_session_id:
                return _refuse(ManagedJoinRefusalReason.PARENT_MISMATCH)
            if attestation.activation_epoch != self._activation_epoch:
                return _refuse(
                    ManagedJoinRefusalReason.STALE_EPOCH,
                    f"attested epoch {attestation.activation_epoch}, "
                    f"current {self._activation_epoch}",
                )
            if not attestation.admits_backend(backend):
                return _refuse(ManagedJoinRefusalReason.MODE_NOT_ADMITTED)
            return issued

    def _verified_live_catalog(
        self, attestation: ManagedJoinAttestation, route: ManagedCodexRoute
    ) -> bytes | ManagedJoinVerificationRefusal:
        if attestation.hook_registry_digest != HOOK_REGISTRY_HASH:
            return _refuse(
                ManagedJoinRefusalReason.REGISTRY_DIGEST_MISMATCH, "hook_registry_digest"
            )
        if attestation.fixed_batch_tool_registry_digest != managed_codex_route_digest():
            return _refuse(
                ManagedJoinRefusalReason.REGISTRY_DIGEST_MISMATCH,
                "fixed_batch_tool_registry_digest",
            )
        home_text = os.environ.get(CODEX_HOME_ENV_VAR)
        if not home_text:
            return _refuse(ManagedJoinRefusalReason.HOME_UNAVAILABLE, "CODEX_HOME is unset")
        home = Path(home_text)
        try:
            if not home.is_dir():
                return _refuse(
                    ManagedJoinRefusalReason.HOME_UNAVAILABLE, "CODEX_HOME is not a directory"
                )
            if home != home.resolve():
                return _refuse(
                    ManagedJoinRefusalReason.HOME_UNAVAILABLE,
                    "CODEX_HOME does not resolve to itself",
                )
        except (OSError, ValueError) as exc:
            return _refuse(ManagedJoinRefusalReason.HOME_UNAVAILABLE, str(exc))
        assert self._backend is not None
        managed = managed_route_backend(self._backend)
        if managed is None:
            return _refuse(ManagedJoinRefusalReason.BACKEND_NOT_MANAGED)
        try:
            catalog = managed.read_managed_session_catalog(home)
            errors = managed.verify_managed_session_dir(
                home,
                attestation,
                route,
                managed_codex_catalog=catalog,
            )
        except (OSError, ValueError) as exc:
            return _refuse(ManagedJoinRefusalReason.HOME_UNREADABLE, str(exc))
        if errors:
            return _refuse(ManagedJoinRefusalReason.HOME_DRIFT, *errors)
        return catalog

    def find_verified_context(
        self,
        *,
        backend: str,
        parent_session_id: str,
    ) -> SemanticAdaptationContext | ManagedJoinVerificationRefusal:
        """Find an issued context and revalidate its live home on every lookup."""
        with self._lock:
            matches = [
                context
                for context in self._issued.values()
                if context.managed_join_attestation is not None
                and context.managed_join_attestation.backend == backend
                and context.managed_join_attestation.parent_session_id == parent_session_id
            ]
        if len(matches) > 1:
            return _refuse(ManagedJoinRefusalReason.AMBIGUOUS_CONTEXT)
        if self._record_store is None or self._backend is None:
            return _refuse(ManagedJoinRefusalReason.RECORD_STORE_UNAVAILABLE)
        if self._backend.name != backend:
            return _refuse(
                ManagedJoinRefusalReason.BACKEND_MISMATCH,
                f"configured {self._backend.name!r}, requested {backend!r}",
            )
        if not matches:
            return self._recover_verified_context(
                backend=backend, parent_session_id=parent_session_id
            )
        verified = self.verify(matches[0], backend=backend, parent_session_id=parent_session_id)
        if isinstance(verified, ManagedJoinVerificationRefusal):
            return verified
        attestation = verified.managed_join_attestation
        assert attestation is not None
        try:
            route = managed_codex_route_for_launch_context(attestation.launch_context)
        except ValueError as exc:
            return _refuse(ManagedJoinRefusalReason.ROUTE_MISMATCH, str(exc))
        catalog = self._verified_live_catalog(attestation, route)
        if isinstance(catalog, ManagedJoinVerificationRefusal):
            return catalog
        # The recovery gate and epoch can change while the home is checked.
        return self.verify(verified, backend=backend, parent_session_id=parent_session_id)

    def _recover_verified_context(
        self,
        *,
        backend: str,
        parent_session_id: str,
    ) -> SemanticAdaptationContext | ManagedJoinVerificationRefusal:
        assert self._record_store is not None
        loaded = self._record_store.load(parent_session_id)
        if loaded is None:
            return _refuse(ManagedJoinRefusalReason.RECORD_UNAVAILABLE)
        attestation, route = loaded
        try:
            expected_route = managed_codex_route_for_launch_context(attestation.launch_context)
        except ValueError as exc:
            return _refuse(ManagedJoinRefusalReason.ROUTE_MISMATCH, str(exc))
        if attestation.backend != backend:
            return _refuse(
                ManagedJoinRefusalReason.BACKEND_MISMATCH,
                f"attested {attestation.backend!r}, requested {backend!r}",
            )
        if route != expected_route:
            return _refuse(
                ManagedJoinRefusalReason.ROUTE_MISMATCH,
                f"record route {route!r}, expected {expected_route!r}",
            )
        catalog = self._verified_live_catalog(attestation, route)
        if isinstance(catalog, ManagedJoinVerificationRefusal):
            return catalog
        with self._lock:
            if self._recovery_gate is not None and not self._recovery_gate():
                return _refuse(ManagedJoinRefusalReason.RECOVERY_BLOCKED)
            context = SemanticAdaptationContext(
                managed_join_attestation=attestation,
                managed_codex_catalog=catalog,
            )
            self._issued[context.digest] = context
        return self.verify(context, backend=backend, parent_session_id=parent_session_id)
