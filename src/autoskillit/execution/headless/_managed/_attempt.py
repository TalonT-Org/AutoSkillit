"""Managed physical-attempt lineage and launch policy."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, cast

from autoskillit.core import (
    CmdSpec,
    CodingAgentBackend,
    ManagedHeadlessSessionKind,
    ManagedHeadlessSessionLineage,
    ManagedHeadlessSessionLineageRef,
    ManagedHeadlessSessionLineageStore,
    ManagedHeadlessSessionTerminalState,
    NativeShellCaptureDecision,
    PluginLaunchBinding,
    PluginLoadMode,
    ValidatedAddDir,
    new_managed_attempt_id,
)
from autoskillit.execution.session import ManagedHeadlessSessionLineageCASMismatch

_BuildSpec = Callable[
    [PluginLaunchBinding | None, Mapping[str, str] | None, str | None],
    CmdSpec,
]


class _ManagedLineageObserver:
    """CAS-safe physical-attempt and native-session observer for one lineage."""

    _MAX_CAS_RETRIES = 8

    def __init__(
        self,
        *,
        store: ManagedHeadlessSessionLineageStore,
        decision: NativeShellCaptureDecision,
        reference: ManagedHeadlessSessionLineageRef,
        backend: str,
        session_kind: ManagedHeadlessSessionKind,
    ) -> None:
        self.store = store
        self.decision = decision
        self.reference = reference
        lineage = store.load_reference(reference)
        if lineage.decision != decision:
            raise ValueError("Managed lineage capture decision mismatch")
        if lineage.backend != backend:
            raise ValueError("Managed lineage backend mismatch")
        if lineage.session_kind is not session_kind:
            raise ValueError("Managed lineage session kind mismatch")

    @classmethod
    def create(
        cls,
        *,
        store: ManagedHeadlessSessionLineageStore,
        decision: NativeShellCaptureDecision | None,
        reference: ManagedHeadlessSessionLineageRef | None,
        backend: str,
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

    def bind_returned_final(self, session_id: str) -> None:
        if not session_id:
            return
        self.bind_candidate(session_id)
        current = self.store.load_reference(self.reference)
        if current.final_native_session_id is not None:
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


def _headless_plugin_load_mode(
    backend: CodingAgentBackend,
    *,
    add_dirs: Sequence[ValidatedAddDir] = (),
) -> PluginLoadMode:
    """Resolve how this concrete backend launch obtains its skill tree."""
    capabilities = backend.capabilities
    if not capabilities.skill_injection_capable:
        return PluginLoadMode.NONE
    if capabilities.plugin_install_capable:
        return PluginLoadMode.EXPLICIT_PLUGIN_DIR
    if add_dirs:
        return PluginLoadMode.GENERATED_HOME
    return PluginLoadMode.PROJECTED_HOME


def _build_attempt_spec(
    build_spec: _BuildSpec,
    *,
    binding: PluginLaunchBinding | None,
    provider_extras: Mapping[str, str] | None,
    observer: _ManagedLineageObserver | None,
) -> CmdSpec:
    """Build one physical attempt, allocating managed identity immediately first."""
    if observer is None:
        unmanaged_build = cast(
            Callable[
                [PluginLaunchBinding | None, Mapping[str, str] | None],
                CmdSpec,
            ],
            build_spec,
        )
        return unmanaged_build(binding, provider_extras)
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
    "_headless_plugin_load_mode",
]
