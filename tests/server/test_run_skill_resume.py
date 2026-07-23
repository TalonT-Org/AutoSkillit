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
    from tests.fakes import InMemoryHeadlessExecutor

    executor = InMemoryHeadlessExecutor()
    manager = MagicMock()
    tool_ctx_kitchen_open.executor = executor
    tool_ctx_kitchen_open.session_skill_manager = manager
    monkeypatch.setattr("autoskillit.server._ctx", tool_ctx_kitchen_open)

    result = json.loads(await run_skill("/implement foo", "/tmp", resume_session_id="never-bound"))

    assert result["success"] is False
    assert "cannot resume" in result["result"].lower()
    manager.materialize_invocation.assert_not_called()
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
    tool_ctx_kitchen_open.executor = executor
    tool_ctx_kitchen_open.session_skill_manager = manager
    tool_ctx_kitchen_open.skill_resolver = resolver
    tool_ctx_kitchen_open.output_pattern_resolver = output_resolver
    tool_ctx_kitchen_open.write_expected_resolver = write_resolver
    tool_ctx_kitchen_open.skill_contract_resolver = contract_resolver
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
