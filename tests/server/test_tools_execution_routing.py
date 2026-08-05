"""Tests for run_skill routing, executor delegation, and session skill management."""

from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit.server.tools.tools_execution import run_skill

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


@pytest.mark.anyio
async def test_tools_execution_routes_through_executor(tool_ctx_kitchen_open, monkeypatch) -> None:
    """run_skill routes through ctx.executor.run(), not run_headless_core directly."""
    from tests.fakes import InMemoryHeadlessExecutor

    executor = InMemoryHeadlessExecutor()
    tool_ctx_kitchen_open.executor = executor
    monkeypatch.setattr("autoskillit.server._ctx", tool_ctx_kitchen_open)

    await run_skill("/test skill", "/tmp")
    assert len(executor.calls) == 1
    assert executor.calls[0].skill_command == "/test skill"
    assert executor.calls[0].cwd == str(Path("/tmp").resolve())


@pytest.mark.anyio
async def test_standalone_audit_uses_only_standalone_contract(
    tool_ctx_kitchen_open,
    monkeypatch,
    tmp_path,
) -> None:
    import json
    from unittest.mock import MagicMock

    from tests.fakes import InMemoryHeadlessExecutor

    executor = InMemoryHeadlessExecutor()
    materializer = MagicMock()
    tool_ctx_kitchen_open.executor = executor
    tool_ctx_kitchen_open.audit_authority_materializer = materializer
    monkeypatch.setattr("autoskillit.server._ctx", tool_ctx_kitchen_open)

    response = json.loads(
        await run_skill(
            "/autoskillit:audit-impl audit this plan",
            str(tmp_path),
        )
    )

    call = executor.calls[0]
    assert call.skill_contract.audit_output_mode.value == "standalone"
    assert {output.name for output in call.skill_contract.outputs} == {
        "audit_status",
        "standalone_evidence_path",
        "content_digest",
    }
    assert '"audit_output_mode":"standalone"' in call.skill_command
    assert "audit_semantic_submission" not in call.skill_command
    materializer.materialize.assert_not_called()
    assert response["audit_verdict"] is None
    assert response["audit_cycle_path"] is None


@pytest.mark.anyio
async def test_run_skill_passes_validated_add_dirs(tool_ctx_kitchen_open, monkeypatch) -> None:
    """run_skill passes ValidatedAddDir instances (not raw strings) as add_dirs."""
    from autoskillit.core import ValidatedAddDir
    from tests.fakes import InMemoryHeadlessExecutor

    executor = InMemoryHeadlessExecutor()
    tool_ctx_kitchen_open.executor = executor
    monkeypatch.setattr("autoskillit.server._ctx", tool_ctx_kitchen_open)

    await run_skill("/test skill", "/tmp")
    # All add_dirs must be ValidatedAddDir instances
    assert len(executor.calls[0].add_dirs) >= 1
    assert all(isinstance(d, ValidatedAddDir) for d in executor.calls[0].add_dirs)
    # Must not include raw skills_extended/ path
    from autoskillit.workspace.skills import bundled_skills_extended_dir

    skills_ext = str(bundled_skills_extended_dir())
    add_dir_paths = [d.path for d in executor.calls[0].add_dirs]
    assert skills_ext not in add_dir_paths


@pytest.mark.anyio
async def test_run_skill_materializes_exact_invocation(
    tool_ctx_kitchen_open, monkeypatch, tmp_path
) -> None:
    """run_skill routes the resolved invocation through session materialization."""
    from unittest.mock import MagicMock

    real_ssm = tool_ctx_kitchen_open.session_skill_manager
    mock_ssm = MagicMock(wraps=real_ssm)
    tool_ctx_kitchen_open.session_skill_manager = mock_ssm

    from tests.fakes import InMemoryHeadlessExecutor

    executor = InMemoryHeadlessExecutor()
    tool_ctx_kitchen_open.executor = executor
    monkeypatch.setattr("autoskillit.server._ctx", tool_ctx_kitchen_open)

    await run_skill("/test skill", "/tmp")

    mock_ssm.materialize_invocation.assert_called_once()
    invocation = mock_ssm.materialize_invocation.call_args.args[1]
    assert invocation.root.name == "test"
    assert executor.calls[0].add_dirs


@pytest.mark.anyio
async def test_run_skill_materializes_resolved_dependency_closure(
    tool_ctx_kitchen_open, monkeypatch, tmp_path
) -> None:
    """The materialized invocation carries the resolver's exact dependency closure."""
    from unittest.mock import MagicMock

    real_ssm = tool_ctx_kitchen_open.session_skill_manager
    mock_ssm = MagicMock(wraps=real_ssm)
    tool_ctx_kitchen_open.session_skill_manager = mock_ssm

    from tests.fakes import InMemoryHeadlessExecutor

    tool_ctx_kitchen_open.executor = InMemoryHeadlessExecutor()
    monkeypatch.setattr("autoskillit.server._ctx", tool_ctx_kitchen_open)

    await run_skill("/autoskillit:investigate issue", "/tmp")

    mock_ssm.materialize_invocation.assert_called_once()
    invocation = mock_ssm.materialize_invocation.call_args.args[1]
    assert invocation.root.name == "investigate"
    assert {member.name for member in invocation.closure} == {"investigate"}


@pytest.mark.anyio
async def test_run_skill_result_includes_order_id_when_passed(
    tool_ctx_kitchen_open, monkeypatch
) -> None:
    """run_skill injects order_id into the result JSON when order_id is non-empty."""
    import json as _json

    from tests.fakes import InMemoryHeadlessExecutor

    tool_ctx_kitchen_open.executor = InMemoryHeadlessExecutor()
    monkeypatch.setattr("autoskillit.server._ctx", tool_ctx_kitchen_open)

    result_json = await run_skill("/test skill", "/tmp", order_id="issue-185")
    data = _json.loads(result_json)
    assert data.get("order_id") == "issue-185"


@pytest.mark.anyio
async def test_run_skill_result_order_id_empty_string_when_not_passed(
    tool_ctx_kitchen_open, monkeypatch
) -> None:
    """run_skill emits order_id as empty string in result JSON when none provided."""
    import json as _json

    from tests.fakes import InMemoryHeadlessExecutor

    tool_ctx_kitchen_open.executor = InMemoryHeadlessExecutor()
    monkeypatch.setattr("autoskillit.server._ctx", tool_ctx_kitchen_open)

    result_json = await run_skill("/test skill", "/tmp")  # no order_id
    data = _json.loads(result_json)
    assert data.get("order_id") == ""


@pytest.mark.anyio
async def test_run_skill_materializes_exact_resolver_closure(
    tool_ctx_kitchen_open, monkeypatch
) -> None:
    """run_skill forwards the resolver's immutable invocation to materialization."""
    from unittest.mock import MagicMock

    from tests.fakes import InMemoryHeadlessExecutor

    real_ssm = tool_ctx_kitchen_open.session_skill_manager
    mock_ssm = MagicMock(wraps=real_ssm)
    tool_ctx_kitchen_open.session_skill_manager = mock_ssm

    tool_ctx_kitchen_open.executor = InMemoryHeadlessExecutor()
    monkeypatch.setattr("autoskillit.server._ctx", tool_ctx_kitchen_open)

    await run_skill("/autoskillit:investigate the bug", "/tmp")

    mock_ssm.materialize_invocation.assert_called_once()
    invocation = mock_ssm.materialize_invocation.call_args.args[1]
    assert invocation.root.name == "investigate"
    assert {member.name for member in invocation.closure} == {"investigate"}


@pytest.mark.anyio
async def test_run_skill_without_injected_resolver_uses_default(
    tool_ctx_kitchen_open, monkeypatch
) -> None:
    """Production dispatch constructs its effective resolver when DI omits one."""
    from unittest.mock import MagicMock

    from tests.fakes import InMemoryHeadlessExecutor

    mock_ssm = MagicMock(wraps=tool_ctx_kitchen_open.session_skill_manager)
    tool_ctx_kitchen_open.session_skill_manager = mock_ssm
    tool_ctx_kitchen_open.skill_resolver = None

    executor = InMemoryHeadlessExecutor()
    tool_ctx_kitchen_open.executor = executor
    monkeypatch.setattr("autoskillit.server._ctx", tool_ctx_kitchen_open)

    await run_skill("/autoskillit:investigate the bug", "/tmp")

    mock_ssm.materialize_invocation.assert_called_once()
    assert mock_ssm.materialize_invocation.call_args.args[1].root.name == "investigate"
    assert executor.calls


@pytest.mark.anyio
async def test_run_skill_make_plan_passes_exact_retained_closure(
    tool_ctx_kitchen_open, monkeypatch
) -> None:
    """End-to-end: /make-plan materializes its exact retained activation closure."""
    from unittest.mock import MagicMock

    from tests.fakes import InMemoryHeadlessExecutor

    real_ssm = tool_ctx_kitchen_open.session_skill_manager
    mock_ssm = MagicMock(wraps=real_ssm)
    tool_ctx_kitchen_open.session_skill_manager = mock_ssm

    tool_ctx_kitchen_open.executor = InMemoryHeadlessExecutor()
    monkeypatch.setattr("autoskillit.server._ctx", tool_ctx_kitchen_open)

    await run_skill("/autoskillit:make-plan refactor", "/tmp")

    invocation = mock_ssm.materialize_invocation.call_args.args[1]
    assert {member.name for member in invocation.closure} == {"make-plan", "write-recipe"}


@pytest.mark.anyio
async def test_run_skill_passes_idle_output_timeout(tool_ctx_kitchen_open, monkeypatch) -> None:
    """run_skill passes idle_output_timeout (as float) to executor.run()."""
    from tests.fakes import InMemoryHeadlessExecutor

    executor = InMemoryHeadlessExecutor()
    tool_ctx_kitchen_open.executor = executor
    monkeypatch.setattr("autoskillit.server._ctx", tool_ctx_kitchen_open)

    await run_skill("/test skill", "/tmp", idle_output_timeout=120)
    assert executor.calls[0].idle_output_timeout == 120.0  # int→float conversion


@pytest.mark.anyio
async def test_run_skill_idle_output_timeout_defaults_to_none(
    tool_ctx_kitchen_open, monkeypatch
) -> None:
    """run_skill passes None to executor.run() when idle_output_timeout is not set."""
    from tests.fakes import InMemoryHeadlessExecutor

    executor = InMemoryHeadlessExecutor()
    tool_ctx_kitchen_open.executor = executor
    monkeypatch.setattr("autoskillit.server._ctx", tool_ctx_kitchen_open)

    await run_skill("/test skill", "/tmp")
    assert executor.calls[0].idle_output_timeout is None


@pytest.mark.anyio
async def test_run_skill_passes_backend_to_projection_context(
    tool_ctx_kitchen_open, monkeypatch
) -> None:
    """run_skill forwards the resolved global backend to materialization."""
    from unittest.mock import MagicMock

    from autoskillit.execution.backends import get_backend
    from tests.fakes import InMemoryHeadlessExecutor

    real_ssm = tool_ctx_kitchen_open.session_skill_manager
    mock_ssm = MagicMock(wraps=real_ssm)
    tool_ctx_kitchen_open.session_skill_manager = mock_ssm

    fake_backend = get_backend("claude-code")
    tool_ctx_kitchen_open.backend = fake_backend

    tool_ctx_kitchen_open.executor = InMemoryHeadlessExecutor()
    monkeypatch.setattr("autoskillit.server._ctx", tool_ctx_kitchen_open)

    await run_skill("/test skill", "/tmp")

    mock_ssm.materialize_invocation.assert_called_once()
    projection_context = mock_ssm.materialize_invocation.call_args.args[2]
    assert projection_context.backend.name == fake_backend.name


class TestOutputDirParameter:
    """output_dir parameter plumbing from run_skill to executor."""

    def test_run_skill_has_output_dir_parameter(self) -> None:
        """run_skill() accepts output_dir parameter."""
        import inspect

        sig = inspect.signature(run_skill)
        assert "output_dir" in sig.parameters
        param = sig.parameters["output_dir"]
        assert param.default == ""

    @pytest.mark.anyio
    async def test_run_skill_forwards_output_dir_to_write_watch_dirs(
        self, tool_ctx_kitchen_open, monkeypatch, tmp_path
    ) -> None:
        """output_dir is resolved and forwarded to executor.run() as write_watch_dirs."""
        from pathlib import Path

        from tests.fakes import InMemoryHeadlessExecutor

        executor = InMemoryHeadlessExecutor()
        tool_ctx_kitchen_open.executor = executor
        monkeypatch.setattr("autoskillit.server._ctx", tool_ctx_kitchen_open)

        output_dir = str(tmp_path / "output")
        await run_skill("/test skill", str(tmp_path), output_dir=output_dir)

        assert len(executor.calls) == 1
        assert Path(output_dir) in executor.calls[0].write_watch_dirs


@pytest.mark.anyio
async def test_run_skill_injects_provider_extras_when_feature_enabled(
    tool_ctx_kitchen_open, monkeypatch, tmp_path
) -> None:
    """run_skill records provider_extras and profile_name when feature is enabled."""
    from tests.fakes import InMemoryHeadlessExecutor

    executor = InMemoryHeadlessExecutor()
    tool_ctx_kitchen_open.executor = executor
    monkeypatch.setattr("autoskillit.server._ctx", tool_ctx_kitchen_open)
    _feat = "autoskillit.server.tools.tools_execution.is_feature_enabled"
    monkeypatch.setattr(_feat, lambda *a, **kw: True)
    monkeypatch.setattr(
        "autoskillit.server._guards._resolve_provider_profile",
        lambda *a, **kw: ("vertex", {"ANTHROPIC_API_KEY": "test-key-xyz"}),
    )

    await run_skill("/autoskillit:probe", str(tmp_path))

    assert executor.calls[0].provider_extras == {"ANTHROPIC_API_KEY": "test-key-xyz"}
    assert executor.calls[0].profile_name == "vertex"
    assert executor.calls[0].provider_name == "vertex"


@pytest.mark.anyio
async def test_run_skill_provider_extras_none_when_feature_disabled(
    tool_ctx_kitchen_open, monkeypatch, tmp_path
) -> None:
    """run_skill records None provider_extras and empty profile_name when feature is disabled."""
    from tests.fakes import InMemoryHeadlessExecutor

    executor = InMemoryHeadlessExecutor()
    tool_ctx_kitchen_open.executor = executor
    monkeypatch.setattr("autoskillit.server._ctx", tool_ctx_kitchen_open)
    _feat = "autoskillit.server.tools.tools_execution.is_feature_enabled"
    monkeypatch.setattr(_feat, lambda *a, **kw: False)

    await run_skill("/autoskillit:probe", str(tmp_path))

    assert executor.calls[0].provider_extras is None
    assert executor.calls[0].profile_name == ""
    assert executor.calls[0].provider_name == ""


@pytest.mark.anyio
async def test_run_skill_provider_extras_none_when_default_profile(
    tool_ctx_kitchen_open, monkeypatch, tmp_path
) -> None:
    """run_skill records None provider_extras when profile resolves to default anthropic."""
    from tests.fakes import InMemoryHeadlessExecutor

    executor = InMemoryHeadlessExecutor()
    tool_ctx_kitchen_open.executor = executor
    monkeypatch.setattr("autoskillit.server._ctx", tool_ctx_kitchen_open)
    _feat = "autoskillit.server.tools.tools_execution.is_feature_enabled"
    monkeypatch.setattr(_feat, lambda *a, **kw: True)
    monkeypatch.setattr(
        "autoskillit.server._guards._resolve_provider_profile",
        lambda *a, **kw: ("anthropic", {}),
    )

    await run_skill("/autoskillit:probe", str(tmp_path))

    assert executor.calls[0].provider_extras is None
    assert executor.calls[0].profile_name == ""
    assert executor.calls[0].provider_name == ""


@pytest.mark.anyio
async def test_run_skill_calls_cleanup_session_after_execution(
    tool_ctx_kitchen_open, monkeypatch, tmp_path
):
    """cleanup_session is called with the session_id after executor.run completes."""
    from unittest.mock import MagicMock

    from autoskillit.core import ValidatedAddDir
    from tests.fakes import InMemoryHeadlessExecutor

    cleanup_calls: list[str] = []
    mock_ssm = MagicMock()
    mock_ssm.init_session.return_value = ValidatedAddDir(path=str(tmp_path))
    mock_ssm.compute_skill_closure.return_value = None
    mock_ssm.cleanup_session.side_effect = lambda sid: cleanup_calls.append(sid) or True
    tool_ctx_kitchen_open.session_skill_manager = mock_ssm
    executor = InMemoryHeadlessExecutor()
    tool_ctx_kitchen_open.executor = executor
    monkeypatch.setattr("autoskillit.server._ctx", tool_ctx_kitchen_open)

    await run_skill("/autoskillit:test-skill", str(tmp_path))

    assert len(cleanup_calls) == 1
    assert cleanup_calls[0].startswith("headless-")


@pytest.mark.anyio
async def test_run_skill_cleans_up_on_skill_md_not_found(
    tool_ctx_kitchen_open, monkeypatch, tmp_path
):
    """Early return when SKILL.md not found still triggers session cleanup."""
    import json
    from unittest.mock import MagicMock

    from autoskillit.core import ValidatedAddDir

    cleanup_calls: list[str] = []
    session_dir = tmp_path / "empty-session"
    session_dir.mkdir()
    mock_ssm = MagicMock()
    mock_ssm.materialize_invocation.return_value = ValidatedAddDir(path=str(session_dir))
    mock_ssm.cleanup_session.side_effect = lambda sid: cleanup_calls.append(sid) or True
    tool_ctx_kitchen_open.session_skill_manager = mock_ssm
    monkeypatch.setattr("autoskillit.server._ctx", tool_ctx_kitchen_open)

    result = json.loads(await run_skill("/autoskillit:target-skill", str(tmp_path)))

    assert len(cleanup_calls) == 1
    assert not result.get("success", True)


@pytest.mark.anyio
async def test_run_skill_rejects_fabricated_skill_name(
    tool_ctx_kitchen_open, monkeypatch, tmp_path
) -> None:
    """run_skill crashes before session creation for a skill not in any discovery source."""
    import json
    from unittest.mock import MagicMock

    from autoskillit.core import ValidatedAddDir

    mock_ssm = MagicMock()
    mock_ssm.init_session.return_value = ValidatedAddDir(path=str(tmp_path))
    tool_ctx_kitchen_open.session_skill_manager = mock_ssm

    mock_resolver = MagicMock()
    mock_resolver.resolve.return_value = None  # Skill not in any source
    tool_ctx_kitchen_open.skill_resolver = mock_resolver
    monkeypatch.setattr("autoskillit.server._ctx", tool_ctx_kitchen_open)

    result = json.loads(await run_skill("/autoskillit:this-skill-does-not-exist", str(tmp_path)))

    assert result.get("subtype") == "crashed"
    assert not result.get("success", True)
    mock_ssm.init_session.assert_not_called()


@pytest.mark.anyio
async def test_run_skill_empty_closure_not_expanded_to_unrestricted(
    tool_ctx_kitchen_open, monkeypatch, tmp_path
) -> None:
    """An empty frozenset from compute_skill_closure is rejected, not expanded to allow_only=None.

    Before the fix, the empty frozenset collapsed to None (unrestricted); after the fix,
    run_skill returns a crash result and init_session is never called.
    """
    import json
    from unittest.mock import MagicMock

    from autoskillit.core import ValidatedAddDir

    mock_ssm = MagicMock()
    mock_ssm.init_session.return_value = ValidatedAddDir(path=str(tmp_path))
    mock_ssm.compute_skill_closure.return_value = frozenset()  # Empty — simulates unknown skill
    tool_ctx_kitchen_open.session_skill_manager = mock_ssm

    mock_resolver = MagicMock()
    mock_resolver.resolve.return_value = MagicMock(source=MagicMock(value="bundled_extended"))
    tool_ctx_kitchen_open.skill_resolver = mock_resolver
    monkeypatch.setattr("autoskillit.server._ctx", tool_ctx_kitchen_open)

    result = json.loads(await run_skill("/autoskillit:some-skill", str(tmp_path)))

    assert result.get("subtype") == "crashed"
    assert not result.get("success", True)
    mock_ssm.init_session.assert_not_called()


@pytest.mark.anyio
async def test_run_skill_cleans_up_on_materialization_failure(
    tool_ctx_kitchen_open, monkeypatch, tmp_path
):
    """Partial materialization failure still triggers cleanup for the session_id."""
    from unittest.mock import MagicMock

    from tests.fakes import InMemoryHeadlessExecutor

    cleanup_calls: list[str] = []
    mock_ssm = MagicMock()
    mock_ssm.materialize_invocation.side_effect = OSError("disk full")
    mock_ssm.cleanup_session.side_effect = lambda sid: cleanup_calls.append(sid) or True
    tool_ctx_kitchen_open.session_skill_manager = mock_ssm
    executor = InMemoryHeadlessExecutor()
    tool_ctx_kitchen_open.executor = executor
    monkeypatch.setattr("autoskillit.server._ctx", tool_ctx_kitchen_open)

    await run_skill("/autoskillit:test-skill", str(tmp_path))

    assert len(cleanup_calls) == 1
    assert cleanup_calls[0].startswith("headless-")


@pytest.mark.anyio
async def test_run_skill_succeeds_when_cleanup_session_raises(
    tool_ctx_kitchen_open, monkeypatch, tmp_path
):
    """cleanup_session failure is swallowed — run_skill still returns the result."""
    import json
    from unittest.mock import MagicMock

    from tests.fakes import InMemoryHeadlessExecutor

    real_ssm = tool_ctx_kitchen_open.session_skill_manager
    mock_ssm = MagicMock(wraps=real_ssm)
    mock_ssm.cleanup_session.side_effect = PermissionError("locked")
    tool_ctx_kitchen_open.session_skill_manager = mock_ssm
    executor = InMemoryHeadlessExecutor()
    tool_ctx_kitchen_open.executor = executor
    monkeypatch.setattr("autoskillit.server._ctx", tool_ctx_kitchen_open)

    result = json.loads(await run_skill("/autoskillit:test-skill", str(tmp_path)))

    mock_ssm.cleanup_session.assert_called_once()
    assert result.get("success") is True


@pytest.mark.anyio
async def test_run_skill_passes_inspector_eligible_when_fleet_dispatch(
    tool_ctx_kitchen_open, monkeypatch
) -> None:
    """When DISPATCH_ID_ENV_VAR is set and fleet has inspector_model, run_skill passes params."""

    from autoskillit.core import DISPATCH_ID_ENV_VAR
    from tests.fakes import InMemoryHeadlessExecutor

    monkeypatch.setenv(DISPATCH_ID_ENV_VAR, "test-dispatch-id-123")

    from autoskillit.config import AutomationConfig

    cfg = AutomationConfig()
    cfg.fleet.inspector_model = "claude-haiku-4-5-20251001"
    tool_ctx_kitchen_open.config = cfg

    executor = InMemoryHeadlessExecutor()
    tool_ctx_kitchen_open.executor = executor
    monkeypatch.setattr("autoskillit.server._ctx", tool_ctx_kitchen_open)

    await run_skill("/test skill", "/tmp")

    assert len(executor.calls) == 1
    assert executor.calls[0].inspector_eligible is True
    assert executor.calls[0].inspector_model == "claude-haiku-4-5-20251001"


@pytest.mark.anyio
async def test_run_skill_no_inspector_outside_dispatch(tool_ctx_kitchen_open, monkeypatch) -> None:
    """When DISPATCH_ID_ENV_VAR is absent, inspector_eligible=False."""
    from tests.fakes import InMemoryHeadlessExecutor

    monkeypatch.delenv("AUTOSKILLIT_DISPATCH_ID", raising=False)

    executor = InMemoryHeadlessExecutor()
    tool_ctx_kitchen_open.executor = executor
    monkeypatch.setattr("autoskillit.server._ctx", tool_ctx_kitchen_open)

    await run_skill("/test skill", "/tmp")

    assert len(executor.calls) == 1
    assert executor.calls[0].inspector_eligible is False
    assert executor.calls[0].inspector_model == ""


@pytest.mark.anyio
@pytest.mark.parametrize("session_type", ["skill", "fleet"])
async def test_run_skill_exact_role_denial_precedes_all_downstream_work(
    tool_ctx_kitchen_open, monkeypatch, tmp_path, session_type
) -> None:
    """L1 and L3 are denied before resolution, materialization, or execution."""
    from unittest.mock import AsyncMock, MagicMock

    from tests.fakes import InMemoryHeadlessExecutor

    monkeypatch.setenv("AUTOSKILLIT_HEADLESS", "1")
    monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", session_type)
    resolver = MagicMock()
    manager = MagicMock()
    contract_store = MagicMock()
    audit = MagicMock()
    token_log = MagicMock()
    timing_log = MagicMock()
    notify = AsyncMock()
    executor = InMemoryHeadlessExecutor()
    tool_ctx_kitchen_open.skill_resolver = resolver
    tool_ctx_kitchen_open.session_skill_manager = manager
    tool_ctx_kitchen_open.skill_session_contract_store = contract_store
    tool_ctx_kitchen_open.audit = audit
    tool_ctx_kitchen_open.token_log = token_log
    tool_ctx_kitchen_open.timing_log = timing_log
    tool_ctx_kitchen_open.executor = executor
    monkeypatch.setattr("autoskillit.server._ctx", tool_ctx_kitchen_open)
    monkeypatch.setattr(
        "autoskillit.server.tools.tools_execution._notify",
        notify,
    )

    result = await run_skill("/autoskillit:root work", str(tmp_path))

    assert __import__("json").loads(result)["subtype"] == "headless_error"
    resolver.resolve_effective.assert_not_called()
    resolver.resolve_invocation.assert_not_called()
    manager.init_session.assert_not_called()
    manager.activate_skill_deps.assert_not_called()
    manager.materialize_invocation.assert_not_called()
    assert contract_store.mock_calls == []
    assert audit.mock_calls == []
    assert token_log.mock_calls == []
    assert timing_log.mock_calls == []
    notify.assert_not_awaited()
    assert executor.calls == []


@pytest.mark.anyio
async def test_invalid_orchestrator_root_rejects_before_all_downstream_work(
    tool_ctx_kitchen_open, monkeypatch, tmp_path
) -> None:
    """An L2 caller cannot materialize an ORCHESTRATOR skill as an L1 root."""
    import json
    from unittest.mock import AsyncMock, MagicMock

    from autoskillit.workspace import DefaultSkillResolver
    from tests.fakes import InMemoryHeadlessExecutor

    skill_md = tmp_path / ".claude" / "skills" / "invalid-root" / "SKILL.md"
    skill_md.parent.mkdir(parents=True)
    skill_md.write_text(
        "---\n"
        "name: invalid-root\n"
        "description: Invalid L1 root.\n"
        "uses_capabilities: [run_skill]\n"
        "execution_role: orchestrator\n"
        "---\n"
        'Call run_skill("/test child").\n'
    )
    monkeypatch.setenv("AUTOSKILLIT_HEADLESS", "1")
    monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "orchestrator")
    notify = AsyncMock()
    manager = MagicMock()
    contract_store = MagicMock()
    audit = MagicMock()
    executor = InMemoryHeadlessExecutor()
    tool_ctx_kitchen_open.project_dir = tmp_path
    tool_ctx_kitchen_open.skill_resolver = DefaultSkillResolver()
    tool_ctx_kitchen_open.session_skill_manager = manager
    tool_ctx_kitchen_open.skill_session_contract_store = contract_store
    tool_ctx_kitchen_open.audit = audit
    tool_ctx_kitchen_open.executor = executor
    monkeypatch.setattr("autoskillit.server._ctx", tool_ctx_kitchen_open)
    monkeypatch.setattr("autoskillit.server.tools.tools_execution._notify", notify)

    result = json.loads(await run_skill("/invalid-root", str(tmp_path)))

    assert result["success"] is False
    assert "orchestrator" in result["result"].lower()
    notify.assert_not_awaited()
    manager.init_session.assert_not_called()
    manager.activate_skill_deps.assert_not_called()
    manager.materialize_invocation.assert_not_called()
    assert contract_store.mock_calls == []
    assert audit.mock_calls == []
    assert executor.calls == []


@pytest.mark.anyio
@pytest.mark.parametrize("backend_name", ["claude-code", "codex"])
async def test_process_issues_l2_parent_executes_session_child_on_each_backend(
    tool_ctx_kitchen_open,
    monkeypatch,
    tmp_path,
    backend_name: str,
) -> None:
    """The process-issues L2 role is the authorized L2→L1 run_skill edge."""
    import json
    from unittest.mock import MagicMock

    from autoskillit.core import CodingAgentBackend, SkillExecutionRole
    from autoskillit.execution.backends import get_backend
    from autoskillit.workspace import (
        DefaultSessionSkillManager,
        DefaultSkillResolver,
        SkillsDirectoryProvider,
    )
    from tests.fakes import InMemoryHeadlessExecutor

    resolver = DefaultSkillResolver()
    parent = resolver.resolve_invocation(
        "process-issues",
        tool_ctx_kitchen_open.project_dir,
        SkillExecutionRole.ORCHESTRATOR,
    )
    assert parent.root.execution_role is SkillExecutionRole.ORCHESTRATOR
    assert "run_skill" in parent.capability_union

    concrete_backend = get_backend(backend_name)
    backend = MagicMock(spec=CodingAgentBackend)
    backend.name = concrete_backend.name
    backend.capabilities = concrete_backend.capabilities
    backend.conventions = concrete_backend.conventions
    backend.ensure_pre_launch.return_value = []
    backend.validate_session_layout.return_value = []
    backend.session_locator.return_value.project_log_dir.return_value = None
    manager = MagicMock(
        wraps=DefaultSessionSkillManager(
            SkillsDirectoryProvider(),
            ephemeral_root=tmp_path / "ephemeral-sessions",
            persistent_roots={"codex": tmp_path / "persistent-sessions"},
        )
    )
    executor = InMemoryHeadlessExecutor()
    tool_ctx_kitchen_open.skill_resolver = resolver
    tool_ctx_kitchen_open.session_skill_manager = manager
    tool_ctx_kitchen_open.executor = executor
    tool_ctx_kitchen_open.backend = backend
    monkeypatch.setenv("AUTOSKILLIT_HEADLESS", "1")
    monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "orchestrator")
    monkeypatch.setattr("autoskillit.server._ctx", tool_ctx_kitchen_open)

    result = json.loads(await run_skill("/test child", str(tmp_path)))

    assert result["success"] is True, result.get("result")
    child = manager.materialize_invocation.call_args.args[1]
    assert child.execution_role is SkillExecutionRole.SESSION
    assert child.root.execution_role is SkillExecutionRole.SESSION
    assert len(executor.calls) == 1


@pytest.mark.anyio
async def test_fresh_dispatch_without_injected_resolver_fails_before_writes(
    tool_ctx_kitchen_open, monkeypatch, tmp_path
) -> None:
    """The production resolver rejects an unknown skill before any writes."""
    from unittest.mock import MagicMock

    from tests.fakes import InMemoryHeadlessExecutor

    monkeypatch.setenv("AUTOSKILLIT_HEADLESS", "1")
    monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "orchestrator")
    executor = InMemoryHeadlessExecutor()
    tool_ctx_kitchen_open.skill_resolver = None
    manager = MagicMock()
    tool_ctx_kitchen_open.session_skill_manager = manager
    tool_ctx_kitchen_open.executor = executor
    monkeypatch.setattr("autoskillit.server._ctx", tool_ctx_kitchen_open)

    result = await run_skill("/autoskillit:not-installed work", str(tmp_path))

    payload = __import__("json").loads(result)
    assert payload["success"] is False
    assert "was not found in any effective source" in payload["result"]
    manager.materialize_invocation.assert_not_called()
    assert executor.calls == []
