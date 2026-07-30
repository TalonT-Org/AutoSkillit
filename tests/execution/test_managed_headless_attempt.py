"""Managed physical-attempt identity and native-session binding tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit.core import (
    CmdSpec,
    ManagedHeadlessSessionKind,
    ManagedHeadlessSessionLineageStatus,
    NativeShellCaptureDecision,
    NativeShellCaptureMode,
    NativeShellCaptureReason,
    RetryReason,
    SkillResult,
    resolve_native_shell_capture_decision,
)
from autoskillit.execution.headless import DefaultHeadlessExecutor
from autoskillit.execution.headless._managed import (
    _build_attempt_spec,
    _LineageCallbacks,
    _ManagedLineageObserver,
)
from tests.fakes import FakeManagedHeadlessSessionLineageStore

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]


def _observer(
    tmp_path: Path,
    *,
    backend: str = "codex",
    session_kind: ManagedHeadlessSessionKind = ManagedHeadlessSessionKind.SKILL,
):
    anchor = tmp_path / "lineage"
    anchor.mkdir()
    store = FakeManagedHeadlessSessionLineageStore()
    decision = resolve_native_shell_capture_decision(NativeShellCaptureMode.DIRECT)
    lineage = store.create(
        lineage_anchor=anchor,
        launch_id="a" * 32,
        decision=decision,
        backend=backend,
        session_kind=session_kind,
    )
    observer = _ManagedLineageObserver.create(
        store=store,
        decision=decision,
        reference=lineage.reference,
        backend=backend,
        session_kind=session_kind,
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


def test_resumed_codex_final_binding_transfers_verified_ownership(tmp_path: Path) -> None:
    store, reference, _ = _observer(tmp_path)
    lineage = store.load_reference(reference)
    lineage = store.bind_final_native_session_id(
        lineage_anchor=Path(reference.lineage_anchor),
        launch_id=reference.launch_id,
        session_id="old-final",
        expected_generation=lineage.generation,
        expected_record_digest=lineage.record_digest,
    )
    observer = _ManagedLineageObserver.create(
        store=store,
        decision=lineage.decision,
        reference=reference,
        backend="codex",
        session_kind=ManagedHeadlessSessionKind.SKILL,
    )
    assert observer is not None

    observer.bind_returned_final("new-final")

    rebound = store.load_reference(reference)
    assert rebound.final_native_session_id == "new-final"
    assert rebound.candidate_native_session_ids == ("old-final", "new-final")
    assert (
        store.find_by_final_native_session_id(
            lineage_anchor=Path(reference.lineage_anchor),
            session_id="new-final",
        )
        == rebound
    )
    with pytest.raises(KeyError):
        store.find_by_final_native_session_id(
            lineage_anchor=Path(reference.lineage_anchor),
            session_id="old-final",
        )


def test_changed_final_binding_rejects_non_codex_lineage(tmp_path: Path) -> None:
    store, reference, _ = _observer(tmp_path, backend="claude-code")
    lineage = store.load_reference(reference)
    lineage = store.bind_final_native_session_id(
        lineage_anchor=Path(reference.lineage_anchor),
        launch_id=reference.launch_id,
        session_id="old-final",
        expected_generation=lineage.generation,
        expected_record_digest=lineage.record_digest,
    )
    observer = _ManagedLineageObserver.create(
        store=store,
        decision=lineage.decision,
        reference=reference,
        backend="claude-code",
        session_kind=ManagedHeadlessSessionKind.SKILL,
    )
    assert observer is not None

    with pytest.raises(ValueError, match="not authorized"):
        observer.bind_returned_final("new-final")

    assert store.load_reference(reference).final_native_session_id == "old-final"


@pytest.mark.anyio
async def test_default_executor_accepts_managed_invalid_lineage_capture_fallback(
    minimal_ctx,
    monkeypatch,
    tmp_path: Path,
) -> None:
    from tests.execution.conftest import _mock_backend

    backend = _mock_backend(session_dir_persistent=True)
    backend.name = "codex"
    minimal_ctx.backend = backend
    store = FakeManagedHeadlessSessionLineageStore()
    minimal_ctx.managed_headless_session_lineage_store = store
    decision = NativeShellCaptureDecision(
        mode=NativeShellCaptureMode.CAPTURE,
        reason=NativeShellCaptureReason.INVALID_LINEAGE,
        lineage_status=ManagedHeadlessSessionLineageStatus.MISSING,
    )
    lineage = store.create(
        lineage_anchor=tmp_path,
        launch_id="b" * 32,
        decision=decision,
        backend="codex",
        session_kind=ManagedHeadlessSessionKind.SKILL,
    )

    async def _fake_execute(*_args, **kwargs) -> SkillResult:
        observer = kwargs["managed_lineage_observer"]
        assert observer is not None
        observer.allocate_attempt()
        observer.bind_returned_final("capture-fallback-final")
        return SkillResult(
            success=True,
            result="done",
            session_id="capture-fallback-final",
            subtype="success",
            is_error=False,
            exit_code=0,
            needs_retry=False,
            retry_reason=RetryReason.NONE,
            stderr="",
        )

    monkeypatch.setattr(
        "autoskillit.execution.headless._execute_claude_headless",
        _fake_execute,
    )

    result = await DefaultHeadlessExecutor(minimal_ctx).run(
        "/implement",
        str(tmp_path),
        resume_session_id="untrusted-resume",
        native_shell_capture_decision=decision,
        managed_lineage_ref=lineage.reference,
    )

    assert result.success is True
    persisted = store.load_reference(lineage.reference)
    assert persisted.decision == decision
    assert persisted.attempt_ids
    assert persisted.final_native_session_id == "capture-fallback-final"


@pytest.mark.anyio
async def test_default_executor_rebinds_changed_resumed_codex_final(
    minimal_ctx,
    monkeypatch,
    tmp_path: Path,
) -> None:
    from tests.execution.conftest import _mock_backend

    backend = _mock_backend(session_dir_persistent=True)
    backend.name = "codex"
    minimal_ctx.backend = backend
    store = FakeManagedHeadlessSessionLineageStore()
    minimal_ctx.managed_headless_session_lineage_store = store
    decision = resolve_native_shell_capture_decision(NativeShellCaptureMode.DIRECT)
    lineage = store.create(
        lineage_anchor=tmp_path,
        launch_id="c" * 32,
        decision=decision,
        backend="codex",
        session_kind=ManagedHeadlessSessionKind.SKILL,
    )
    lineage = store.bind_final_native_session_id(
        lineage_anchor=tmp_path,
        launch_id=lineage.launch_id,
        session_id="old-final",
        expected_generation=lineage.generation,
        expected_record_digest=lineage.record_digest,
    )

    async def _fake_execute(*_args, **kwargs) -> SkillResult:
        observer = kwargs["managed_lineage_observer"]
        assert observer is not None
        observer.bind_returned_final("new-final")
        return SkillResult(
            success=True,
            result="done",
            session_id="new-final",
            subtype="success",
            is_error=False,
            exit_code=0,
            needs_retry=False,
            retry_reason=RetryReason.NONE,
            stderr="",
        )

    monkeypatch.setattr(
        "autoskillit.execution.headless._execute_claude_headless",
        _fake_execute,
    )

    result = await DefaultHeadlessExecutor(minimal_ctx).run(
        "/implement",
        str(tmp_path),
        resume_session_id="old-final",
        native_shell_capture_decision=decision,
        managed_lineage_ref=lineage.reference,
    )

    assert result.session_id == "new-final"
    assert store.load_reference(lineage.reference).final_native_session_id == "new-final"
    with pytest.raises(KeyError):
        store.find_by_final_native_session_id(
            lineage_anchor=tmp_path,
            session_id="old-final",
        )
