"""Tests for run_skill resume_session_id parameter threading (T4)."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from autoskillit.server.tools.tools_execution import run_skill

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


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
    monkeypatch.setattr("autoskillit.server._ctx", tool_ctx_kitchen_open)

    await run_skill("/implement foo", "/tmp", resume_session_id="sess-123")

    assert len(executor.calls) == 1
    assert executor.calls[0].resume_session_id == "sess-123"


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

    assert result["success"] is True
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
