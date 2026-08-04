"""Backend-neutral semantic skill requirement contracts."""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

pytestmark = [pytest.mark.layer("core"), pytest.mark.small]


def test_skill_semantic_taxonomy_is_closed_and_exported() -> None:
    from autoskillit.core import (
        SKILL_MODEL_CLASSES,
        SKILL_REASONING_EFFORTS,
        SkillSemanticOperation,
    )
    from autoskillit.core.types import _type_skill_semantics

    assert {operation.value for operation in SkillSemanticOperation} == {
        "child_spawn",
        "required_concurrency",
        "required_join",
        "required_evidence",
        "child_model_policy",
        "logical_role",
        "sibling_skill_invoke",
        "git_metadata_write",
    }
    assert SKILL_MODEL_CLASSES == frozenset({"haiku", "sonnet", "opus"})
    assert SKILL_REASONING_EFFORTS == frozenset({"medium", "high", "xhigh"})
    assert "SKILL_REASONING_EFFORTS" in _type_skill_semantics.__all__


def test_skill_semantic_plan_is_frozen_slotted_and_derives_operations() -> None:
    from autoskillit.core import (
        ChildModelPolicySpec,
        ChildSpawnSpec,
        ConcurrencySpec,
        EvidenceSpec,
        GitMetadataWriteSpec,
        JoinSpec,
        LogicalRoleSpec,
        SiblingSkillSpec,
        SkillSemanticOperation,
        SkillSemanticPlan,
    )

    plan = SkillSemanticPlan(
        schema_version=1,
        child_spawns=(ChildSpawnSpec(role="implementation-auditor"),),
        concurrency=ConcurrencySpec(required=True),
        join=JoinSpec(required=True),
        evidence=EvidenceSpec(required=True, independent=True),
        child_model_policies=(
            ChildModelPolicySpec(
                role="implementation-auditor",
                model_class="opus",
                reasoning_effort="high",
            ),
        ),
        logical_roles=(
            LogicalRoleSpec(name="implementation-auditor", purpose="audit one plan slice"),
        ),
        sibling_skills=(SiblingSkillSpec(name="investigate"),),
        git_metadata_writes=(GitMetadataWriteSpec(purpose="create the requested commit"),),
    )

    assert plan.operations == frozenset(SkillSemanticOperation)
    assert not hasattr(plan, "__dict__")
    with pytest.raises(FrozenInstanceError):
        plan.schema_version = 2  # type: ignore[misc]


def test_skill_semantic_plan_rejects_incoherent_payloads() -> None:
    from autoskillit.core import (
        ChildModelPolicySpec,
        SkillContractError,
        SkillSemanticPlan,
    )

    with pytest.raises(SkillContractError, match="schema version"):
        SkillSemanticPlan(schema_version=2)
    with pytest.raises(SkillContractError, match="unknown logical role"):
        SkillSemanticPlan(
            schema_version=1,
            child_model_policies=(ChildModelPolicySpec(role="missing", model_class="opus"),),
        )


def test_skill_semantic_adaptation_result_enforces_exact_diagnostic_boundary() -> None:
    from autoskillit.core import (
        SkillSemanticAdaptationResult,
        SkillSemanticOperation,
    )

    supported = SkillSemanticAdaptationResult(
        instruction_fragments=("Launch one child for logical role 'audit'.",),
        logical_role_mapping={"audit": "audit-impl-slice-auditor"},
        sibling_skill_targets={"investigate": "$investigate"},
        model_effort_policy={"audit": ("gpt-5.6-sol", "high")},
    )
    assert supported.unsupported_operation is None
    assert supported.diagnostic is None

    diagnostic = SkillSemanticAdaptationResult.unsupported(
        backend="codex",
        operation=SkillSemanticOperation.GIT_METADATA_WRITE,
    )
    assert diagnostic.instruction_fragments == ()
    assert diagnostic.diagnostic == (
        "backend 'codex' does not support skill semantic operation 'git_metadata_write'"
    )
