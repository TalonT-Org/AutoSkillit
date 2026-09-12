"""Managed physical-attempt lineage and launch policy."""

from __future__ import annotations

import dataclasses
import os
from collections.abc import Callable, Mapping, Sequence
from contextlib import AbstractContextManager, nullcontext
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

from autoskillit.core import (
    SESSION_TYPE_ENV_VAR,
    CmdSpec,
    CodingAgentBackend,
    ExecutionIdentity,
    ManagedHeadlessSessionKind,
    ManagedHeadlessSessionLineage,
    ManagedHeadlessSessionLineageRef,
    ManagedHeadlessSessionLineageStore,
    ManagedHeadlessSessionTerminalState,
    NamedResume,
    NativeShellCaptureDecision,
    NativeShellCaptureDiagnostic,
    NativeShellCaptureMode,
    NativeShellCaptureReason,
    NoResume,
    PluginLaunchBinding,
    PluginLoadMode,
    SessionAttemptHandle,
    SessionType,
    SkillResult,
    SubprocessResult,
    ValidatedAddDir,
    get_logger,
    new_managed_attempt_id,
)
from autoskillit.execution.child_outcomes import ManagedAttemptRecorder, normalize_backend_name
from autoskillit.execution.evidence.session_log import resolve_log_dir
from autoskillit.execution.session import ManagedHeadlessSessionLineageCASMismatch

logger = get_logger(__name__)

_BuildSpec = Callable[
    [PluginLaunchBinding | None, Mapping[str, str] | None, str | None],
    CmdSpec,
]


def _bind_effective_execution_identity(
    skill_result: SkillResult,
    backend: CodingAgentBackend,
    requested: ExecutionIdentity,
) -> SkillResult:
    effective = requested
    if requested.children and skill_result.session_id:
        try:
            effective = backend.resolve_effective_execution_identity(
                requested=requested,
                session_id=skill_result.session_id,
            )
        except (OSError, ValueError):
            logger.warning(
                "effective_execution_identity_resolution_failed",
                session_id=skill_result.session_id,
                exc_info=True,
            )
    return dataclasses.replace(skill_result, execution_identity=effective)


def _generated_home_attempt(
    backend: CodingAgentBackend,
    spec: CmdSpec,
    *,
    plugin_load_mode: PluginLoadMode,
    managed_attempt_id: str | None,
    attempt: int,
    resume_session_id: str,
    ceiling_seconds: float,
) -> AbstractContextManager[SessionAttemptHandle | None]:
    if plugin_load_mode is not PluginLoadMode.GENERATED_HOME:
        return nullcontext(None)
    session_home = spec.env.get("CODEX_HOME")
    if not session_home:
        raise ValueError("A managed Codex attempt requires its generated session home")
    return backend.session_attempt_context(
        session_home=Path(session_home),
        project_dir=Path(spec.cwd).resolve(strict=True),
        launch_id=(managed_attempt_id or uuid4().hex)[:16],
        attempt=attempt,
        current_resume_spec=NamedResume(resume_session_id) if resume_session_id else NoResume(),
        ceiling_seconds=ceiling_seconds,
    )


class _ManagedLineageObserver:
    """CAS-safe physical-attempt and native-session observer for one lineage."""

    _MAX_CAS_RETRIES = 8

    def __init__(
        self,
        *,
        store: ManagedHeadlessSessionLineageStore,
        decision: NativeShellCaptureDecision,
        reference: ManagedHeadlessSessionLineageRef,
        backend: CodingAgentBackend,
        session_kind: ManagedHeadlessSessionKind,
    ) -> None:
        self.store = store
        self.decision = decision
        self.reference = reference
        lineage = store.load_reference(reference)
        if lineage.decision != decision:
            raise ValueError("Managed lineage capture decision mismatch")
        if lineage.backend != backend.name:
            raise ValueError("Managed lineage backend mismatch")
        if lineage.session_kind is not session_kind:
            raise ValueError("Managed lineage session kind mismatch")
        capabilities = backend.capabilities
        self._verified_resume_final_session_id = (
            lineage.final_native_session_id
            if (
                capabilities.session_resume_capable
                and not capabilities.channel_b_capable
                and session_kind is ManagedHeadlessSessionKind.SKILL
            )
            else None
        )

    @classmethod
    def create(
        cls,
        *,
        store: ManagedHeadlessSessionLineageStore,
        decision: NativeShellCaptureDecision | None,
        reference: ManagedHeadlessSessionLineageRef | None,
        backend: CodingAgentBackend,
        session_kind: ManagedHeadlessSessionKind,
    ) -> _ManagedLineageObserver | None:
        if decision is None and reference is None:
            return None
        if decision is None or reference is None:
            raise ValueError(
                "Managed native shell capture requires both a decision and lineage reference"
            )
        return cls(
            store=store,
            decision=decision,
            reference=reference,
            backend=backend,
            session_kind=session_kind,
        )

    def _mutate(
        self,
        operation: Callable[[ManagedHeadlessSessionLineage], ManagedHeadlessSessionLineage],
    ) -> ManagedHeadlessSessionLineage:
        for _ in range(self._MAX_CAS_RETRIES):
            current = self.store.load_reference(self.reference)
            try:
                return operation(current)
            except ManagedHeadlessSessionLineageCASMismatch:
                continue
        raise ManagedHeadlessSessionLineageCASMismatch("Managed lineage CAS retry limit exceeded")

    def allocate_attempt(self) -> str:
        attempt_id = new_managed_attempt_id()
        self._mutate(
            lambda current: self.store.append_attempt(
                lineage_anchor=Path(self.reference.lineage_anchor),
                launch_id=self.reference.launch_id,
                attempt_id=attempt_id,
                expected_generation=current.generation,
                expected_record_digest=current.record_digest,
            )
        )
        return attempt_id

    def bind_candidate(self, session_id: str) -> None:
        if not session_id:
            return
        self._mutate(
            lambda current: self.store.bind_candidate_native_session_id(
                lineage_anchor=Path(self.reference.lineage_anchor),
                launch_id=self.reference.launch_id,
                session_id=session_id,
                expected_generation=current.generation,
                expected_record_digest=current.record_digest,
            )
        )

    def bind_launch_contract_digest(self, launch_contract_digest: str) -> None:
        """CAS-persist one pre-spawn physical contract digest."""
        self._mutate(
            lambda current: self.store.bind_launch_contract_digest(
                lineage_anchor=Path(self.reference.lineage_anchor),
                launch_id=self.reference.launch_id,
                launch_contract_digest=launch_contract_digest,
                expected_generation=current.generation,
                expected_record_digest=current.record_digest,
            )
        )

    def bind_returned_final(self, session_id: str) -> None:
        if not session_id:
            return
        self.bind_candidate(session_id)
        current = self.store.load_reference(self.reference)
        if current.final_native_session_id == session_id:
            return
        if current.final_native_session_id is not None:
            verified_final_session_id = self._verified_resume_final_session_id
            if (
                verified_final_session_id is None
                or current.final_native_session_id != verified_final_session_id
            ):
                raise ValueError("Managed lineage final identity change is not authorized")
            self._mutate(
                lambda latest: self.store.rebind_final_native_session_id(
                    lineage_anchor=Path(self.reference.lineage_anchor),
                    launch_id=self.reference.launch_id,
                    expected_session_id=verified_final_session_id,
                    session_id=session_id,
                    expected_generation=latest.generation,
                    expected_record_digest=latest.record_digest,
                )
            )
            return
        self._mutate(
            lambda latest: self.store.bind_final_native_session_id(
                lineage_anchor=Path(self.reference.lineage_anchor),
                launch_id=self.reference.launch_id,
                session_id=session_id,
                expected_generation=latest.generation,
                expected_record_digest=latest.record_digest,
            )
        )

    def close(self, terminal_state: ManagedHeadlessSessionTerminalState) -> None:
        current = self.store.load_reference(self.reference)
        if current.terminal_state is not ManagedHeadlessSessionTerminalState.ACTIVE:
            return
        self._mutate(
            lambda latest: (
                latest
                if latest.terminal_state is not ManagedHeadlessSessionTerminalState.ACTIVE
                else self.store.set_terminal_state(
                    lineage_anchor=Path(self.reference.lineage_anchor),
                    launch_id=self.reference.launch_id,
                    terminal_state=terminal_state,
                    expected_generation=latest.generation,
                    expected_record_digest=latest.record_digest,
                )
            )
        )

    def capture_diagnostic(self) -> NativeShellCaptureDiagnostic:
        """Collect runner markers and build one immutable bounded projection."""

        lineage = self.store.collect_runner_observations(self.reference)
        policy_disabled = any(
            observation.project_policy_disabled for observation in lineage.observations
        )
        observed_direct = any(
            observation.effective_mode is NativeShellCaptureMode.DIRECT
            for observation in lineage.observations
        )
        launch_direct = lineage.decision.mode is NativeShellCaptureMode.DIRECT
        effective_mode = (
            NativeShellCaptureMode.DIRECT
            if launch_direct or observed_direct
            else NativeShellCaptureMode.CAPTURE
        )
        attributions: set[NativeShellCaptureReason] = set()
        if launch_direct:
            attributions.add(NativeShellCaptureReason.LAUNCH_AUTHORIZED_DIRECT)
        if policy_disabled:
            attributions.add(NativeShellCaptureReason.PROJECT_POLICY_DISABLED)
        if not attributions:
            attributions.add(NativeShellCaptureReason.CAPTURE_ENABLED)
        if launch_direct:
            primary_reason = NativeShellCaptureReason.LAUNCH_AUTHORIZED_DIRECT
        elif policy_disabled:
            primary_reason = NativeShellCaptureReason.PROJECT_POLICY_DISABLED
        else:
            primary_reason = NativeShellCaptureReason.CAPTURE_ENABLED
        attempt_id = (
            lineage.observations[-1].attempt_id
            if lineage.observations
            else (lineage.attempt_ids[-1] if lineage.attempt_ids else None)
        )
        return NativeShellCaptureDiagnostic(
            requested_mode=lineage.decision.mode,
            effective_mode=effective_mode,
            primary_reason=primary_reason,
            attributions=tuple(sorted(attributions, key=lambda value: value.value)),
            resolution_reason=lineage.decision.reason,
            lineage_status=lineage.decision.lineage_status,
            launch_id=lineage.launch_id,
            attempt_id=attempt_id,
            dropped_observation_count=lineage.dropped_observation_count,
        )


def capture(
    observer: _ManagedLineageObserver | None,
) -> NativeShellCaptureDiagnostic | None:
    """Return one immutable terminal projection without disrupting execution."""

    if observer is None:
        return None
    try:
        return observer.capture_diagnostic()
    except Exception:
        logger.warning("native_shell_capture_diagnostic_failed", exc_info=True)
        return None


def log_launch(observer: _ManagedLineageObserver | None) -> None:
    """Emit the common launch event with a deterministic diagnostic identity."""

    diagnostic = capture(observer)
    logger.debug(
        "headless_session_launch",
        event_id=(diagnostic.event_id(stage="launch") if diagnostic is not None else None),
        native_shell_capture=(
            diagnostic.to_dict(stage="launch") if diagnostic is not None else None
        ),
    )


def log_exit(
    diagnostic: NativeShellCaptureDiagnostic | None,
    result: SkillResult,
) -> None:
    """Emit the common terminal event for every result-bearing exit path."""

    logger.debug(
        "headless_session_exit",
        success=result.success,
        needs_retry=result.needs_retry,
        subtype=result.subtype,
        session_id=result.session_id,
        event_id=(diagnostic.event_id(stage="exit") if diagnostic is not None else None),
        native_shell_capture=(
            diagnostic.to_dict(stage="exit") if diagnostic is not None else None
        ),
    )


def log_cancelled(
    diagnostic: NativeShellCaptureDiagnostic | None,
) -> None:
    """Emit the terminal event for a cancellation that will be re-raised."""

    log_exit(diagnostic, SkillResult.cancelled())


def should_flush(
    result: SubprocessResult | None,
    skill_result: SkillResult,
    step_name: str,
    diagnostic: NativeShellCaptureDiagnostic | None,
) -> bool:
    """Return whether the session has any durable diagnostic payload."""

    return (
        (result is not None and result.proc_snapshots is not None)
        or not skill_result.success
        or bool(step_name)
        or skill_result.token_usage is not None
        or diagnostic is not None
    )


def _headless_plugin_load_mode(
    backend: CodingAgentBackend,
    *,
    add_dirs: Sequence[ValidatedAddDir] = (),
    requires_generated_home: bool = False,
) -> PluginLoadMode:
    """Resolve how this concrete backend launch obtains its skill tree.

    ``requires_generated_home`` selects a generated home ahead of the
    add-dirs that will materialize into it (e.g. a food-truck managed
    catalog, built only after this mode selects ``GENERATED_HOME``).
    """
    capabilities = backend.capabilities
    if not capabilities.skill_injection_capable:
        return PluginLoadMode.NONE
    if capabilities.plugin_install_capable:
        return PluginLoadMode.EXPLICIT_PLUGIN_DIR
    if add_dirs or requires_generated_home:
        return PluginLoadMode.GENERATED_HOME
    return PluginLoadMode.PROJECTED_HOME


def _build_attempt_spec(
    build_spec: _BuildSpec,
    *,
    binding: PluginLaunchBinding | None,
    provider_extras: Mapping[str, str] | None,
    observer: _ManagedLineageObserver | None,
    managed_attempt_id: str | None = None,
) -> CmdSpec:
    """Build one physical attempt from an already-bound managed identity."""
    if observer is None:
        unmanaged_build = cast(
            Callable[
                [PluginLaunchBinding | None, Mapping[str, str] | None],
                CmdSpec,
            ],
            build_spec,
        )
        return unmanaged_build(binding, provider_extras)
    if managed_attempt_id is None:
        managed_attempt_id = observer.allocate_attempt()
    return build_spec(binding, provider_extras, managed_attempt_id)


class _LineageCallbacks:
    """Keep lineage binding independent from correlation-owned callbacks."""

    def __init__(
        self,
        observer: _ManagedLineageObserver | None,
        downstream: Callable[[str], None] | None,
    ) -> None:
        self._observer = observer
        self._downstream = downstream
        self._session_type: SessionType | None = None

    @property
    def session_type(self) -> SessionType | None:
        return self._session_type

    @property
    def on_candidate(self) -> Callable[[str], None] | None:
        if self._observer is None:
            return self._downstream
        return self._observe_candidate

    @property
    def attempt_kwargs(self) -> dict[str, Any]:
        if self._observer is None:
            return {}
        return {"managed_lineage_observer": self._observer}

    @property
    def launch_kwargs(self) -> dict[str, Any]:
        return {**self.attempt_kwargs, "on_spec_built": self.capture_session_type}

    def capture_session_type(self, spec: CmdSpec) -> None:
        raw_session_type = spec.env.get(SESSION_TYPE_ENV_VAR)
        self._session_type = SessionType(raw_session_type) if raw_session_type else None

    def _observe_candidate(self, session_id: str) -> None:
        assert self._observer is not None
        self._observer.bind_candidate(session_id)
        if self._downstream is not None:
            self._downstream(session_id)

    def bind_final(self, session_id: str) -> None:
        if not session_id:
            return
        if self._observer is not None:
            self._observer.bind_returned_final(session_id)
        if self._downstream is not None:
            self._downstream(session_id)


__all__ = [
    "_BuildSpec",
    "_LineageCallbacks",
    "_ManagedLineageObserver",
    "_build_attempt_spec",
    "capture",
    "_headless_plugin_load_mode",
    "log_cancelled",
    "log_exit",
    "log_launch",
    "should_flush",
]


def _resolve_idle_output_timeout(override: float | None, configured: float) -> float | None:
    """Resolve the shared caller, environment, and configured idle timeout policy."""
    if override is not None:
        raw_idle = override
    else:
        env_idle = os.environ.get("AUTOSKILLIT_IDLE_OUTPUT_TIMEOUT")
        if env_idle is not None:
            try:
                raw_idle = float(env_idle)
            except ValueError:
                logger.warning(
                    "AUTOSKILLIT_IDLE_OUTPUT_TIMEOUT: invalid float — falling back to config",
                    env_value=env_idle,
                    fallback=configured,
                )
                raw_idle = float(configured)
        else:
            raw_idle = float(configured)
    return raw_idle if raw_idle > 0.0 else None


def build_managed_attempt_wiring(
    *,
    child_role: str | None,
    child_attribution_skill: str,
    step_backend: CodingAgentBackend,
    session_id: str | None,
    diagnostic_log_dir: str,
    on_spawn: Callable[[int, int], None] | None,
    on_candidate: Callable[[str], None] | None,
) -> tuple[ManagedAttemptRecorder, Callable[[int, int], None], Callable[[str], None]]:
    """Construct the Step-5 (#4623) recorder plus its on_spawn/on_session_id_resolved
    callback wrappers for one ``_execute_claude_headless`` call.

    A no-op recorder (``log_root=None``) when ``child_role`` is None — ordinary,
    non-managed sessions leave every managed-attempt recording call a no-op.
    """
    recorder = ManagedAttemptRecorder(
        log_root=resolve_log_dir(diagnostic_log_dir) if child_role is not None else None,
        backend=normalize_backend_name(step_backend.name) if child_role is not None else "",
        parent_session_id=session_id or "",
        role=child_role or "",
        attribution_skill=child_attribution_skill,
    )

    def _observe_spawn(pid: int, extra: int) -> None:
        recorder.on_spawn(pid, extra, downstream=on_spawn)

    def _bind_alias(native_session_id: str) -> None:
        recorder.bind_launch_alias(native_session_id, downstream=on_candidate)

    return recorder, _observe_spawn, _bind_alias
