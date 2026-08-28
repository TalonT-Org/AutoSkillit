from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]

_CODEX_REQUIRED_JOIN_DIAGNOSTIC = (
    "Codex exposes wait-any/mailbox-activity semantics rather than fixed-set fan-in. "
    "Skills declaring join.required=true cannot be honestly realized on this backend "
    "and must be refused at admission."
)


def test_semantic_preflight_returns_real_codex_refusal_diagnostic() -> None:
    from autoskillit.core import JoinSpec, SkillSemanticPlan
    from autoskillit.execution.backends import CodexBackend
    from autoskillit.server.tools._preflight import check_skill_semantic_feasibility

    plan = SkillSemanticPlan(schema_version=1, join=JoinSpec(required=True))

    assert (
        check_skill_semantic_feasibility(plan, CodexBackend()) == _CODEX_REQUIRED_JOIN_DIAGNOSTIC
    )


def test_managed_join_attestation_is_server_issued_and_admits_preflight() -> None:
    from autoskillit.core import JoinSpec, SkillSemanticPlan
    from autoskillit.execution.backends import CodexBackend
    from autoskillit.server._managed_join_attestation import DefaultManagedJoinAttestationAuthority
    from autoskillit.server.tools._preflight import check_skill_semantic_feasibility

    authority = DefaultManagedJoinAttestationAuthority()
    context = authority.issue(
        backend="codex",
        launch_context="direct",
        parent_session_id="parent-1",
        direct_tool_mode=True,
        resolved_model="gpt-5.6-sol",
        fixed_batch_tool_registry_digest="a" * 64,
        hook_registry_digest="b" * 64,
        skill_load_applies=True,
        guards_apply=True,
    )
    plan = SkillSemanticPlan(schema_version=1, join=JoinSpec(required=True))

    assert authority.verify(context, backend="codex", parent_session_id="parent-1") == context
    assert (
        check_skill_semantic_feasibility(
            plan,
            CodexBackend(),
            adaptation_context=context,
        )
        is None
    )
    authority.rotate_activation_epoch()
    assert authority.verify(context, backend="codex", parent_session_id="parent-1") is None


def test_shared_backend_compat_serializes_root_refusal_as_infeasible() -> None:
    from autoskillit.core import JoinSpec, SkillSemanticPlan
    from autoskillit.execution.backends import CodexBackend
    from autoskillit.server.tools._backend_compat import _check_backend_compat

    root = SimpleNamespace(
        semantic_plan=SkillSemanticPlan(schema_version=1, join=JoinSpec(required=True))
    )
    invocation = SimpleNamespace(root=root, closure=(root,))

    serialized = _check_backend_compat(
        skill_command="$audit-tests",
        resolved_command="$audit-tests --deep",
        effective_order_id="order-123",
        target_name="audit-tests",
        skill_info=invocation,
        effective_backend_obj=CodexBackend(),
        skill_resolver=object(),
    )

    assert serialized is not None
    result = json.loads(serialized)
    assert result["success"] is False
    assert result["is_error"] is True
    assert result["subtype"] == "infeasible"
    assert result["needs_retry"] is False
    assert result["retry_reason"] == "none"
    assert result["kill_reason"] == "not_applicable"
    assert result["exit_code"] == -1
    assert result["stderr"] == ""
    assert result["order_id"] == "order-123"
    assert result["result"] == (
        "Skill 'audit-tests' is not feasible on backend 'codex': "
        f"{_CODEX_REQUIRED_JOIN_DIAGNOSTIC} | skill_command='$audit-tests --deep'"
    )


def test_shared_backend_compat_checks_only_supported_root_not_refused_dependency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import autoskillit.server.tools._backend_compat as backend_compat
    from autoskillit.core import (
        BackendCapabilities,
        GitMetadataWriteSpec,
        JoinSpec,
        SkillSemanticAdaptationResult,
        SkillSemanticOperation,
        SkillSemanticPlan,
    )
    from tests.fakes import adapt_test_skill_semantics

    root_plan = SkillSemanticPlan(
        schema_version=1,
        git_metadata_writes=(GitMetadataWriteSpec(purpose="create one commit"),),
    )
    dependency_plan = SkillSemanticPlan(schema_version=1, join=JoinSpec(required=True))
    adapted_plans: list[SkillSemanticPlan] = []

    def adapt(
        plan: SkillSemanticPlan,
        _adaptation_context=None,
    ) -> SkillSemanticAdaptationResult:
        adapted_plans.append(plan)
        if plan is dependency_plan:
            return SkillSemanticAdaptationResult.unsupported(
                backend="test-backend",
                operation=SkillSemanticOperation.REQUIRED_JOIN,
            )
        return adapt_test_skill_semantics(plan)

    backend = SimpleNamespace(
        name="test-backend",
        capabilities=BackendCapabilities(),
        adapt_skill_semantics=adapt,
    )
    root = SimpleNamespace(semantic_plan=root_plan)
    dependency = SimpleNamespace(semantic_plan=dependency_plan)
    invocation = SimpleNamespace(root=root, closure=(root, dependency))
    monkeypatch.setattr(
        backend_compat,
        "_get_fix_required_hook_matchers",
        lambda _applicable_guards: [],
    )

    assert (
        backend_compat._check_backend_compat(
            skill_command="$root",
            resolved_command="$root",
            effective_order_id="order-123",
            target_name="root",
            skill_info=invocation,
            effective_backend_obj=backend,
            skill_resolver=object(),
        )
        is None
    )
    assert adapted_plans == [root_plan]


@pytest.mark.parametrize(
    ("malformation", "expected"),
    [
        (
            "undeclared_refusal",
            "backend 'test-backend' reported unsupported semantic operation "
            "'child_spawn' not declared by the semantic plan",
        ),
        (
            "incomplete_supported",
            "semantic adaptation omitted observable instructions",
        ),
    ],
)
def test_semantic_preflight_propagates_malformed_adapter_result(
    malformation: str,
    expected: str,
) -> None:
    from autoskillit.core import (
        GitMetadataWriteSpec,
        SkillContractError,
        SkillSemanticAdaptationResult,
        SkillSemanticOperation,
        SkillSemanticPlan,
    )
    from autoskillit.server.tools._preflight import check_skill_semantic_feasibility

    plan = SkillSemanticPlan(
        schema_version=1,
        git_metadata_writes=(GitMetadataWriteSpec(purpose="create one commit"),),
    )
    if malformation == "undeclared_refusal":
        result = SkillSemanticAdaptationResult.unsupported(
            backend="test-backend",
            operation=SkillSemanticOperation.CHILD_SPAWN,
        )
    else:
        result = SkillSemanticAdaptationResult()
    backend = SimpleNamespace(
        name="test-backend",
        adapt_skill_semantics=lambda _plan, _adaptation_context=None: result,
    )

    with pytest.raises(SkillContractError, match=f"^{expected}$"):
        check_skill_semantic_feasibility(plan, backend)
