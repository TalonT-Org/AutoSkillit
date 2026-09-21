"""Server-owned issuance and revalidation for managed-join evidence.

Trust boundary
--------------
The attestation record, the binding flags, the projected manifest and the join
ledger all live under the session temp channel and ``CODEX_HOME``. The immunity
here covers honest-path erasure and drift:
an override, stale home, refreshed catalog, missing guard, mismatched code
version, or missing issuer cannot admit a join-required skill without
evidence. A model deliberately forging files under paths it owns is outside
this boundary, as it is for the existing Claude route.
"""

from __future__ import annotations

import fcntl
import os
import threading
from collections.abc import Callable, Generator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from autoskillit.core import (
    CODEX_HOME_ENV_VAR,
    MANAGED_JOIN_ATTESTATION_SCHEMA_VERSION,
    CodingAgentBackend,
    ManagedJoinAttestation,
    SemanticAdaptationContext,
    SkillContractError,
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

    def load(self, parent_session_id: str) -> tuple[ManagedJoinAttestation, str] | None:
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
        return attestation, document["route"]


def _write_managed_parent_binding(
    *,
    binding_path: Path,
    binding_session_id: str,
    normalized_skill_name: str,
    backend: CodingAgentBackend,
    attestation: ManagedJoinAttestation,
) -> None:
    """Write the live-home source binding for one attested managed parent."""
    projected_manifest_path = getattr(backend, "projected_manifest_path", None)
    if not callable(projected_manifest_path):
        raise SkillContractError("run_fixed_batch managed backend cannot locate its projection")
    home_text = os.environ.get(CODEX_HOME_ENV_VAR)
    if not home_text:
        raise SkillContractError("run_fixed_batch managed binding requires CODEX_HOME to be set")
    try:
        manifest = read_manifest(projected_manifest_path(Path(home_text)))
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
                )
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
        """Find a live context, reloading and validating its issued record if needed."""
        with self._lock:
            matches = [
                context
                for context in self._issued.values()
                if context.managed_join_attestation is not None
                and context.managed_join_attestation.backend == backend
                and context.managed_join_attestation.parent_session_id == parent_session_id
            ]
        if len(matches) == 1:
            return self.verify(matches[0], backend=backend, parent_session_id=parent_session_id)
        if matches or self._record_store is None or self._backend is None:
            return None
        if self._backend.name != backend:
            return None

        loaded = self._record_store.load(parent_session_id)
        if loaded is None:
            return None
        attestation, route = loaded
        try:
            expected_route = managed_codex_route_for_launch_context(attestation.launch_context)
        except ValueError:
            return None
        if (
            attestation.backend != backend
            or route != expected_route
            or attestation.hook_registry_digest != HOOK_REGISTRY_HASH
            or attestation.fixed_batch_tool_registry_digest != managed_codex_route_digest()
        ):
            return None
        home_text = os.environ.get(CODEX_HOME_ENV_VAR)
        if not home_text:
            return None
        home = Path(home_text)
        if not home.is_dir() or home != home.resolve():
            return None
        verifier = getattr(self._backend, "verify_managed_session_dir", None)
        if not callable(verifier):
            return None
        try:
            errors = verifier(home, attestation, route)
        except (OSError, ValueError):
            return None
        if errors:
            return None
        with self._lock:
            if self._recovery_gate is not None and not self._recovery_gate():
                return None
            context = SemanticAdaptationContext(managed_join_attestation=attestation)
            self._issued[context.digest] = context
        return self.verify(context, backend=backend, parent_session_id=parent_session_id)
