"""Managed physical-attempt identity and native-session binding tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit.core import (
    CmdSpec,
    ManagedHeadlessSessionKind,
    NativeShellCaptureMode,
    resolve_native_shell_capture_decision,
)
from autoskillit.execution.headless._managed import (
    _build_attempt_spec,
    _LineageCallbacks,
    _ManagedLineageObserver,
)
from tests.fakes import FakeManagedHeadlessSessionLineageStore

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]


def _observer(tmp_path: Path):
    anchor = tmp_path / "lineage"
    anchor.mkdir()
    store = FakeManagedHeadlessSessionLineageStore()
    decision = resolve_native_shell_capture_decision(NativeShellCaptureMode.DIRECT)
    lineage = store.create(
        lineage_anchor=anchor,
        launch_id="a" * 32,
        decision=decision,
        backend="codex",
        session_kind=ManagedHeadlessSessionKind.SKILL,
    )
    observer = _ManagedLineageObserver.create(
        store=store,
        decision=decision,
        reference=lineage.reference,
        backend="codex",
        session_kind=ManagedHeadlessSessionKind.SKILL,
    )
    assert observer is not None
    return store, lineage.reference, observer


def test_each_physical_build_gets_a_fresh_preallocated_attempt(tmp_path: Path) -> None:
    store, reference, observer = _observer(tmp_path)
    observed_attempts: list[str] = []

    def build(_binding, _extras, attempt_id):
        current = store.load_reference(reference)
        assert attempt_id in current.attempt_ids
        observed_attempts.append(attempt_id)
        return CmdSpec(cmd=("codex",), env={})

    first = _build_attempt_spec(
        build,
        binding=None,
        provider_extras=None,
        observer=observer,
    )
    second = _build_attempt_spec(
        build,
        binding=None,
        provider_extras={"provider": "fallback"},
        observer=observer,
    )

    assert first.cmd == second.cmd == ("codex",)
    assert len(set(observed_attempts)) == 2
    assert store.load_reference(reference).attempt_ids == tuple(observed_attempts)
    assert observer.reference == reference


def test_candidate_and_final_binding_precede_correlation_callback(tmp_path: Path) -> None:
    store, reference, observer = _observer(tmp_path)

    def reject_correlation(_session_id: str) -> None:
        raise RuntimeError("correlation store unavailable")

    callbacks = _LineageCallbacks(observer, reject_correlation)

    assert callbacks.on_candidate is not None
    with pytest.raises(RuntimeError, match="correlation store unavailable"):
        callbacks.on_candidate("native-session")
    assert store.load_reference(reference).candidate_native_session_ids == ("native-session",)

    with pytest.raises(RuntimeError, match="correlation store unavailable"):
        callbacks.bind_final("native-session")
    assert store.load_reference(reference).final_native_session_id == "native-session"
