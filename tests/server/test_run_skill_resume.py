"""Tests for run_skill resume_session_id parameter threading (T4)."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from autoskillit.core import (
    ManagedHeadlessSessionKind,
    ManagedHeadlessSessionLineage,
    ManagedHeadlessSessionLineageStatus,
    ManagedHeadlessSessionLineageStore,
    NativeShellCaptureMode,
    NativeShellCaptureReason,
    resolve_native_shell_capture_decision,
)
from autoskillit.execution import (
    DefaultManagedHeadlessSessionLineageStore,
    DefaultSkillSessionContractStore,
    get_backend,
)
from autoskillit.server.tools._native_shell_capture import (
    prepare_skill_native_shell_lineage,
    rebind_verified_final_session,
)
from autoskillit.server.tools.tools_execution import run_skill

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


def _seed_skill_lineage(
    store: ManagedHeadlessSessionLineageStore,
    *,
    anchor: Path,
    backend_name: str,
    session_id: str,
    mode: NativeShellCaptureMode = NativeShellCaptureMode.DIRECT,
) -> ManagedHeadlessSessionLineage:
    lineage = store.create(
        lineage_anchor=anchor,
        launch_id="a" * 32,
        decision=resolve_native_shell_capture_decision(mode),
        backend=backend_name,
        session_kind=ManagedHeadlessSessionKind.SKILL,
    )
    return store.bind_final_native_session_id(
        lineage_anchor=anchor,
        launch_id=lineage.launch_id,
        session_id=session_id,
        expected_generation=lineage.generation,
        expected_record_digest=lineage.record_digest,
    )


def _attach_lineage_reference(
    store: DefaultSkillSessionContractStore,
    *,
    session_id: str,
    lineage: ManagedHeadlessSessionLineage,
) -> None:
    entry = store._session_path(session_id)  # noqa: SLF001
    manifest = store._read_manifest(entry)  # noqa: SLF001
    manifest["managed_lineage_ref"] = lineage.reference.to_dict()
    store._write_manifest(entry, manifest)  # noqa: SLF001


def test_verified_changed_resume_rebinds_lineage_index_and_contract_callback(
    tmp_path: Path,
) -> None:
    store = DefaultManagedHeadlessSessionLineageStore()
    lineage = _seed_skill_lineage(
        store,
        anchor=tmp_path,
        backend_name="codex",
        session_id="old-final",
    )
    lineage = store.bind_candidate_native_session_id(
        lineage_anchor=tmp_path,
        launch_id=lineage.launch_id,
        session_id="new-final",
        expected_generation=lineage.generation,
        expected_record_digest=lineage.record_digest,
    )
    backend = MagicMock()
    backend.capabilities.session_dir_persistent = True
    rebound_contracts: list[tuple[str, object]] = []

    rebind_verified_final_session(
        store=store,
        backend=backend,
        reference=lineage.reference,
        is_resume=True,
        requested_session_id="old-final",
        returned_session_id="new-final",
        on_rebind=lambda session_id, reference: rebound_contracts.append((session_id, reference)),
    )

    rebound = store.find_by_final_native_session_id(
        lineage_anchor=tmp_path,
        session_id="new-final",
    )
    assert rebound.final_native_session_id == "new-final"
    with pytest.raises(FileNotFoundError):
        store.find_by_final_native_session_id(
            lineage_anchor=tmp_path,
            session_id="old-final",
        )
    assert rebound_contracts == [("new-final", lineage.reference)]


def test_contract_lifecycle_cleans_provisional_and_failed_bound_state() -> None:
    from unittest.mock import call

    from autoskillit.server.tools.tools_execution import _RunSkillContractLifecycle

    store = MagicMock()
    lifecycle = _RunSkillContractLifecycle(
        store=store,
        correlation_key="provisional",
        bound_session_id="bound",
        retain_bound=False,
        execution_started=True,
    )

    lifecycle.cleanup()

    assert store.mock_calls == [call.discard("provisional"), call.delete("bound")]


@pytest.mark.anyio
async def test_resume_session_id_threaded_to_executor(tool_ctx_kitchen_open, monkeypatch) -> None:
    """resume_session_id flows from run_skill → executor.run()."""
    from tests.conftest import bind_test_skill_resume_contract
    from tests.fakes import InMemoryHeadlessExecutor

    executor = InMemoryHeadlessExecutor()
    tool_ctx_kitchen_open.executor = executor
    bind_test_skill_resume_contract(
        tool_ctx_kitchen_open,
        session_id="sess-123",
        cwd="/tmp",
        resolved_command="/implement foo",
    )
    stored = tool_ctx_kitchen_open.skill_session_contract_store.load("sess-123")
    persisted_launch = stored.contract.launch_contract
    assert persisted_launch is not None
    monkeypatch.setattr("autoskillit.server._ctx", tool_ctx_kitchen_open)

    await run_skill("/implement foo", "/tmp", resume_session_id="sess-123")

    assert len(executor.calls) == 1
    assert executor.calls[0].resume_session_id == "sess-123"
    assert executor.calls[0].resume_launch_contract == persisted_launch
    assert executor.calls[0].backend_authority == persisted_launch.backend_authority


@pytest.mark.anyio
async def test_resume_conflicting_mode_inherits_persisted_lineage(
    tool_ctx_kitchen_open,
    monkeypatch,
    tmp_path: Path,
) -> None:
    """A valid persisted decision wins over a conflicting resume argument."""
    import structlog.testing

    from tests.conftest import bind_test_skill_resume_contract
    from tests.fakes import InMemoryHeadlessExecutor

    session_id = "persisted-direct"
    backend_name = tool_ctx_kitchen_open.backend.name
    lineage = _seed_skill_lineage(
        tool_ctx_kitchen_open.managed_headless_session_lineage_store,
        anchor=tmp_path,
        backend_name=backend_name,
        session_id=session_id,
    )
    bind_test_skill_resume_contract(
        tool_ctx_kitchen_open,
        session_id=session_id,
        cwd=tmp_path,
    )
    _attach_lineage_reference(
        tool_ctx_kitchen_open.skill_session_contract_store,
        session_id=session_id,
        lineage=lineage,
    )
    executor = InMemoryHeadlessExecutor()
    tool_ctx_kitchen_open.executor = executor
    monkeypatch.setattr("autoskillit.server._ctx", tool_ctx_kitchen_open)

    with structlog.testing.capture_logs() as logs:
        result = json.loads(
            await run_skill(
                "/implement",
                str(tmp_path),
                resume_session_id=session_id,
                native_shell_capture_mode="capture",
            )
        )

    assert result["success"] is True
    assert len(executor.calls) == 1
    call = executor.calls[0]
    assert call.native_shell_capture_decision == lineage.decision
    assert call.managed_lineage_ref == lineage.reference
    diagnostic = next(
        entry
        for entry in logs
        if entry.get("event") == "native_shell_capture_resume_override_rejected"
    )
    assert {
        "requested_mode": diagnostic["requested_mode"],
        "inherited_mode": diagnostic["inherited_mode"],
        "reason": diagnostic["reason"],
        "lineage_status": diagnostic["lineage_status"],
        "resume_session_id": diagnostic["resume_session_id"],
    } == {
        "requested_mode": NativeShellCaptureMode.CAPTURE.value,
        "inherited_mode": NativeShellCaptureMode.DIRECT.value,
        "reason": NativeShellCaptureReason.RESUME_OVERRIDE_REJECTED.value,
        "lineage_status": ManagedHeadlessSessionLineageStatus.OVERRIDE_REJECTED.value,
        "resume_session_id": session_id,
    }


@pytest.mark.anyio
async def test_resume_missing_lineage_falls_back_to_capture(
    tool_ctx_kitchen_open,
    monkeypatch,
    tmp_path: Path,
) -> None:
    """Requested direct cannot survive a resume contract with no lineage."""
    import structlog.testing

    from tests.conftest import bind_test_skill_resume_contract
    from tests.fakes import InMemoryHeadlessExecutor

    session_id = "missing-lineage"
    tool_ctx_kitchen_open.backend = get_backend("codex")
    bind_test_skill_resume_contract(
        tool_ctx_kitchen_open,
        session_id=session_id,
        cwd=tmp_path,
    )
    executor = InMemoryHeadlessExecutor()
    tool_ctx_kitchen_open.executor = executor
    monkeypatch.setattr("autoskillit.server._ctx", tool_ctx_kitchen_open)

    with structlog.testing.capture_logs() as logs:
        result = json.loads(
            await run_skill(
                "/implement",
                str(tmp_path),
                resume_session_id=session_id,
                native_shell_capture_mode="direct",
            )
        )

    assert result["success"] is True
    decision = executor.calls[0].native_shell_capture_decision
    assert decision is not None
    assert decision.mode is NativeShellCaptureMode.CAPTURE
    assert decision.reason is NativeShellCaptureReason.INVALID_LINEAGE
    assert decision.lineage_status is ManagedHeadlessSessionLineageStatus.MISSING
    fallback_reference = executor.calls[0].managed_lineage_ref
    assert fallback_reference is not None
    fallback_lineage = tool_ctx_kitchen_open.managed_headless_session_lineage_store.load_reference(
        fallback_reference
    )
    assert fallback_lineage.decision == decision
    assert fallback_lineage.final_native_session_id is None
    diagnostic = next(
        entry
        for entry in logs
        if entry.get("event") == "native_shell_capture_resume_lineage_invalid"
    )
    assert diagnostic["requested_mode"] == NativeShellCaptureMode.DIRECT.value
    assert diagnostic["effective_mode"] == NativeShellCaptureMode.CAPTURE.value
    assert diagnostic["reason"] == NativeShellCaptureReason.INVALID_LINEAGE.value
    assert diagnostic["lineage_status"] == ManagedHeadlessSessionLineageStatus.MISSING.value
    assert diagnostic["resume_session_id"] == session_id


@pytest.mark.parametrize(
    ("error", "expected_status"),
    [
        (FileNotFoundError("missing"), ManagedHeadlessSessionLineageStatus.MISSING),
        (ValueError("bad JSON"), ManagedHeadlessSessionLineageStatus.CORRUPT),
        (
            ValueError("unsupported managed lineage schema"),
            ManagedHeadlessSessionLineageStatus.UNSUPPORTED,
        ),
        (
            ValueError("managed lineage anchor identity mismatch"),
            ManagedHeadlessSessionLineageStatus.IDENTITY_MISMATCH,
        ),
    ],
    ids=("missing", "corrupt", "unsupported", "stored-identity"),
)
def test_resume_lineage_load_failure_falls_back_to_capture(
    tmp_path: Path,
    error: Exception,
    expected_status: ManagedHeadlessSessionLineageStatus,
) -> None:
    """Every untrusted store failure maps to a closed capture decision."""
    import structlog.testing

    lineage_store = DefaultManagedHeadlessSessionLineageStore()
    reference_store = MagicMock(wraps=lineage_store)
    valid = _seed_skill_lineage(
        lineage_store,
        anchor=tmp_path,
        backend_name="codex",
        session_id="native-session",
    )
    reference_store.load_reference.side_effect = error
    backend = MagicMock()
    backend.name = "codex"
    backend.capabilities.session_dir_persistent = True

    with structlog.testing.capture_logs() as logs:
        preparation = prepare_skill_native_shell_lineage(
            store=reference_store,
            backend=backend,
            lineage_anchor=tmp_path,
            stored_reference=valid.reference,
            resume_session_id="native-session",
            requested_mode="direct",
            is_resume=True,
        )

    assert preparation.decision is not None
    assert preparation.decision.mode is NativeShellCaptureMode.CAPTURE
    assert preparation.decision.reason is NativeShellCaptureReason.INVALID_LINEAGE
    assert preparation.decision.lineage_status is expected_status
    assert preparation.reference is not None
    fallback = lineage_store.load_reference(preparation.reference)
    assert fallback.decision == preparation.decision
    assert fallback.final_native_session_id is None
    diagnostic = next(
        entry
        for entry in logs
        if entry.get("event") == "native_shell_capture_resume_lineage_invalid"
    )
    assert diagnostic["lineage_status"] == expected_status.value


@pytest.mark.parametrize(
    ("mismatch", "expected_status"),
    [
        ("session-kind", ManagedHeadlessSessionLineageStatus.UNSUPPORTED),
        ("anchor", ManagedHeadlessSessionLineageStatus.IDENTITY_MISMATCH),
        ("backend", ManagedHeadlessSessionLineageStatus.IDENTITY_MISMATCH),
        ("backend-unavailable", ManagedHeadlessSessionLineageStatus.IDENTITY_MISMATCH),
        ("launch", ManagedHeadlessSessionLineageStatus.LAUNCH_MISMATCH),
        ("dispatch", ManagedHeadlessSessionLineageStatus.DISPATCH_MISMATCH),
        ("native-session", ManagedHeadlessSessionLineageStatus.NATIVE_SESSION_MISMATCH),
        ("candidate-only", ManagedHeadlessSessionLineageStatus.NATIVE_SESSION_MISMATCH),
    ],
)
def test_resume_lineage_identity_mismatch_falls_back_to_capture(
    tmp_path: Path,
    mismatch: str,
    expected_status: ManagedHeadlessSessionLineageStatus,
) -> None:
    """Every resume identity dimension fails safe without blocking execution."""
    import structlog.testing

    actual_store = DefaultManagedHeadlessSessionLineageStore()
    lineage = _seed_skill_lineage(
        actual_store,
        anchor=tmp_path,
        backend_name="codex",
        session_id="native-session",
    )
    reference = lineage.reference
    backend = MagicMock()
    backend.name = "codex"
    backend.capabilities.session_dir_persistent = True
    if mismatch == "session-kind":
        lineage = replace(lineage, session_kind=ManagedHeadlessSessionKind.FOOD_TRUCK)
    elif mismatch == "anchor":
        lineage = replace(lineage, lineage_anchor=str(tmp_path / "other"))
    elif mismatch == "backend":
        lineage = replace(lineage, backend="claude-code")
    elif mismatch == "backend-unavailable":
        backend = None
    elif mismatch == "launch":
        lineage = replace(lineage, launch_id="b" * 32)
    elif mismatch == "dispatch":
        lineage = replace(lineage, dispatch_id="dispatch-id")
    elif mismatch == "native-session":
        lineage = replace(lineage, final_native_session_id="different-session")
    elif mismatch == "candidate-only":
        lineage = replace(lineage, final_native_session_id=None)
    store = MagicMock(wraps=actual_store)
    store.load_reference.return_value = lineage

    with structlog.testing.capture_logs() as logs:
        preparation = prepare_skill_native_shell_lineage(
            store=store,
            backend=backend,
            lineage_anchor=tmp_path,
            stored_reference=reference,
            resume_session_id="native-session",
            requested_mode="direct",
            is_resume=True,
        )

    if backend is None:
        assert preparation.decision is None
        assert preparation.reference is None
    else:
        assert preparation.decision is not None
        assert preparation.decision.mode is NativeShellCaptureMode.CAPTURE
        assert preparation.decision.lineage_status is expected_status
        assert preparation.reference is not None
        fallback = actual_store.load_reference(preparation.reference)
        assert fallback.decision == preparation.decision
        assert fallback.final_native_session_id is None
    diagnostic = next(
        entry
        for entry in logs
        if entry.get("event") == "native_shell_capture_resume_lineage_invalid"
    )
    assert diagnostic["lineage_status"] == expected_status.value


def test_valid_resume_without_override_records_inherited_diagnostic(tmp_path: Path) -> None:
    """A valid resume with no caller mode keeps the persisted decision."""
    import structlog.testing

    actual_store = DefaultManagedHeadlessSessionLineageStore()
    lineage = _seed_skill_lineage(
        actual_store,
        anchor=tmp_path,
        backend_name="codex",
        session_id="native-session",
    )
    store = MagicMock()
    store.load_reference.return_value = lineage
    backend = MagicMock()
    backend.name = "codex"

    with structlog.testing.capture_logs() as logs:
        preparation = prepare_skill_native_shell_lineage(
            store=store,
            backend=backend,
            lineage_anchor=tmp_path,
            stored_reference=lineage.reference,
            resume_session_id="native-session",
            requested_mode="",
            is_resume=True,
        )

    assert preparation.decision == lineage.decision
    assert preparation.reference == lineage.reference
    diagnostic = next(
        entry for entry in logs if entry.get("event") == "native_shell_capture_resume_inherited"
    )
    assert diagnostic["mode"] == NativeShellCaptureMode.DIRECT.value
    assert diagnostic["reason"] == NativeShellCaptureReason.RESUME_INHERITED.value
    assert diagnostic["lineage_status"] == ManagedHeadlessSessionLineageStatus.VALID.value


@pytest.mark.anyio
async def test_resume_skips_skill_command_validation(tool_ctx_kitchen_open, monkeypatch) -> None:
    """When resume_session_id is set, non-slash skill_command is allowed."""
    from tests.conftest import bind_test_skill_resume_contract
    from tests.fakes import InMemoryHeadlessExecutor

    executor = InMemoryHeadlessExecutor()
    tool_ctx_kitchen_open.executor = executor
    bind_test_skill_resume_contract(
        tool_ctx_kitchen_open,
        session_id="sess-123",
        cwd="/tmp",
        resolved_command="/implement foo",
    )
    monkeypatch.setattr("autoskillit.server._ctx", tool_ctx_kitchen_open)

    result = await run_skill(
        "Continue from where you left off",
        "/tmp",
        resume_session_id="sess-123",
    )
    data = json.loads(result)
    assert data["success"] is True  # not rejected by _validate_skill_command
    assert executor.calls[0].skill_command == "/implement foo"


@pytest.mark.anyio
async def test_no_resume_still_validates_skill_command(tool_ctx_kitchen_open, monkeypatch) -> None:
    """Without resume_session_id, non-slash skill_command is still rejected."""
    monkeypatch.setattr("autoskillit.server._ctx", tool_ctx_kitchen_open)

    result = await run_skill("Continue from where you left off", "/tmp")
    data = json.loads(result)
    assert data["success"] is False
    assert (
        "slash" in data.get("error", "").lower()
        or "skill_command" in data.get("result", "").lower()
    )


@pytest.mark.anyio
async def test_resume_rejects_unbound_contract_before_downstream_work(
    tool_ctx_kitchen_open, monkeypatch
) -> None:
    from unittest.mock import AsyncMock, call

    from tests.fakes import InMemoryHeadlessExecutor

    executor = InMemoryHeadlessExecutor()
    manager = MagicMock()
    store = MagicMock()
    store.load.side_effect = FileNotFoundError("unbound")
    notify = AsyncMock()
    audit = MagicMock()
    ingredient_guard = MagicMock(side_effect=AssertionError("fresh ingredient guard ran"))
    dependency_guard = MagicMock(side_effect=AssertionError("fresh dependency guard ran"))
    plan_path_guard = MagicMock(side_effect=AssertionError("fresh plan-path guard ran"))
    tool_ctx_kitchen_open.executor = executor
    tool_ctx_kitchen_open.session_skill_manager = manager
    tool_ctx_kitchen_open.skill_session_contract_store = store
    tool_ctx_kitchen_open.audit = audit
    monkeypatch.setattr("autoskillit.server._ctx", tool_ctx_kitchen_open)
    monkeypatch.setattr("autoskillit.server.tools.tools_execution._notify", notify)
    monkeypatch.setattr(
        "autoskillit.server.tools.tools_execution._check_ingredient_locks",
        ingredient_guard,
    )
    monkeypatch.setattr(
        "autoskillit.server.tools.tools_execution._check_pipeline_deps",
        dependency_guard,
    )
    monkeypatch.setattr(
        "autoskillit.server.tools.tools_execution._check_review_approach_plan_path",
        plan_path_guard,
    )

    result = json.loads(
        await run_skill(
            "/implement foo",
            "/tmp",
            step_name="review-step",
            resume_session_id="never-bound",
        )
    )

    assert result["success"] is False
    assert "cannot resume" in result["result"].lower()
    ingredient_guard.assert_not_called()
    dependency_guard.assert_not_called()
    plan_path_guard.assert_not_called()
    manager.materialize_invocation.assert_not_called()
    assert store.mock_calls == [call.load("never-bound")]
    notify.assert_not_awaited()
    assert audit.mock_calls == []
    assert executor.calls == []


@pytest.mark.anyio
async def test_resume_uses_bound_snapshot_without_current_metadata_or_source_reads(
    tool_ctx_kitchen_open, monkeypatch, tmp_path
) -> None:
    from tests.conftest import bind_test_skill_resume_contract
    from tests.fakes import InMemoryHeadlessExecutor

    executor = InMemoryHeadlessExecutor()
    manager = MagicMock()
    resolver = MagicMock()
    output_resolver = MagicMock(side_effect=AssertionError("current output metadata read"))
    write_resolver = MagicMock(side_effect=AssertionError("current write metadata read"))
    contract_resolver = MagicMock(side_effect=AssertionError("current contract metadata read"))
    closure_write_dir = tmp_path / "closure-output"
    closure_write_resolver = MagicMock(return_value=[closure_write_dir])
    tool_ctx_kitchen_open.executor = executor
    tool_ctx_kitchen_open.session_skill_manager = manager
    tool_ctx_kitchen_open.skill_resolver = resolver
    tool_ctx_kitchen_open.output_pattern_resolver = output_resolver
    tool_ctx_kitchen_open.write_expected_resolver = write_resolver
    tool_ctx_kitchen_open.skill_contract_resolver = contract_resolver
    monkeypatch.setattr(
        "autoskillit.server.tools.tools_execution.resolve_closure_write_dirs",
        closure_write_resolver,
    )
    bind_test_skill_resume_contract(
        tool_ctx_kitchen_open,
        session_id="source-isolated",
        cwd=tmp_path,
        resolved_command="/implement original",
    )
    current_source = (
        tool_ctx_kitchen_open.project_dir / ".claude" / "skills" / "implement" / "SKILL.md"
    )
    current_source.unlink(missing_ok=True)
    monkeypatch.setattr("autoskillit.server._ctx", tool_ctx_kitchen_open)

    result = json.loads(
        await run_skill(
            "continue despite deleted current source",
            str(tmp_path),
            resume_session_id="source-isolated",
        )
    )

    assert result["success"] is True, json.dumps(result, sort_keys=True)
    resolver.resolve_invocation.assert_not_called()
    output_resolver.assert_not_called()
    write_resolver.assert_not_called()
    contract_resolver.assert_not_called()
    manager.materialize_invocation.assert_not_called()
    assert len(executor.calls) == 1
    closure_write_resolver.assert_called_once()
    assert executor.calls[0].write_watch_dirs == (closure_write_dir,)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema_version", 999),
        ("member_roles", {"implement": "orchestrator"}),
        ("capabilities", ["run_skill"]),
        ("canonical_contents", {"implement": "changed canonical source"}),
    ],
)
@pytest.mark.anyio
async def test_resume_rejects_incompatible_bound_contract_before_executor(
    tool_ctx_kitchen_open,
    monkeypatch,
    field: str,
    value: object,
) -> None:
    from autoskillit.execution.session._skill_session_contract_store import _digest_json
    from tests.conftest import bind_test_skill_resume_contract
    from tests.fakes import InMemoryHeadlessExecutor

    executor = InMemoryHeadlessExecutor()
    manager = MagicMock()
    tool_ctx_kitchen_open.executor = executor
    tool_ctx_kitchen_open.session_skill_manager = manager
    bind_test_skill_resume_contract(
        tool_ctx_kitchen_open,
        session_id="incompatible",
        cwd="/tmp",
    )
    store = tool_ctx_kitchen_open.skill_session_contract_store
    entry = store._session_path("incompatible")  # noqa: SLF001
    manifest = store._read_manifest(entry)  # noqa: SLF001
    contract = manifest["contract"]
    if field == "capabilities":
        contract["member_capabilities"]["implement"] = value
        contract["capability_union"] = value
    else:
        contract[field] = value
    manifest["contract_digest"] = _digest_json(contract)
    store._write_manifest(entry, manifest)  # noqa: SLF001
    monkeypatch.setattr("autoskillit.server._ctx", tool_ctx_kitchen_open)

    result = json.loads(await run_skill("/implement", "/tmp", resume_session_id="incompatible"))

    assert result["success"] is False
    assert "cannot resume" in result["result"].lower()
    manager.materialize_invocation.assert_not_called()
    assert executor.calls == []


@pytest.mark.parametrize("needs_retry", [False, True], ids=("terminal", "resumable"))
@pytest.mark.anyio
async def test_fresh_dispatch_binds_only_final_backend_id_and_applies_retention_policy(
    tool_ctx_kitchen_open,
    monkeypatch,
    tmp_path,
    needs_retry: bool,
) -> None:
    from unittest.mock import AsyncMock, call

    from autoskillit.core import RetryReason, SkillResult

    real_manager = tool_ctx_kitchen_open.session_skill_manager
    manager = MagicMock(wraps=real_manager)
    tool_ctx_kitchen_open.session_skill_manager = manager

    real_store = tool_ctx_kitchen_open.skill_session_contract_store
    store = MagicMock(wraps=real_store)
    correlation_keys: list[str] = []

    def _create_provisional(**kwargs) -> str:
        key = real_store.create_provisional(**kwargs)
        correlation_keys.append(key)
        return key

    store.create_provisional.side_effect = _create_provisional
    tool_ctx_kitchen_open.skill_session_contract_store = store

    async def _run_with_provider_fallback(
        _command: str,
        _cwd: str,
        *,
        on_session_id_resolved,
        **_kwargs,
    ) -> SkillResult:
        on_session_id_resolved("provider-attempt-1")
        on_session_id_resolved("provider-attempt-1")
        on_session_id_resolved("provider-attempt-2")
        return SkillResult(
            success=not needs_retry,
            result="retry" if needs_retry else "done",
            session_id="final-backend-session",
            subtype="context_limit" if needs_retry else "success",
            is_error=needs_retry,
            exit_code=1 if needs_retry else 0,
            needs_retry=needs_retry,
            retry_reason=(RetryReason.RESUME if needs_retry else RetryReason.NONE),
            stderr="",
            token_usage=None,
        )

    executor = MagicMock()
    executor.run = AsyncMock(side_effect=_run_with_provider_fallback)
    tool_ctx_kitchen_open.executor = executor
    monkeypatch.setattr("autoskillit.server._ctx", tool_ctx_kitchen_open)

    payload = json.loads(await run_skill("/test work", str(tmp_path)))

    assert payload["session_id"] == "final-backend-session"
    assert len(correlation_keys) == 1
    correlation_key = correlation_keys[0]
    materialization_id = manager.materialize_invocation.call_args.args[0]
    assert len({correlation_key, materialization_id, "final-backend-session"}) == 3
    assert store.observe_candidate.call_args_list == [
        call(correlation_key, "provider-attempt-1"),
        call(correlation_key, "provider-attempt-1"),
        call(correlation_key, "provider-attempt-2"),
    ]
    store.finalize.assert_called_once_with(
        correlation_key,
        "final-backend-session",
    )
    for candidate in ("provider-attempt-1", "provider-attempt-2"):
        with pytest.raises((FileNotFoundError, KeyError)):
            real_store.load(candidate)

    if needs_retry:
        stored = real_store.load("final-backend-session")
        assert stored.raw_session_id == "final-backend-session"
        store.delete.assert_not_called()
    else:
        store.delete.assert_called_once_with("final-backend-session")
        with pytest.raises((FileNotFoundError, KeyError)):
            real_store.load("final-backend-session")
