"""Backend-neutral semantic skill requirement contracts."""

from __future__ import annotations

import hashlib
from dataclasses import FrozenInstanceError, replace

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
    assert SKILL_REASONING_EFFORTS == frozenset({"medium", "high"})
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
        child_spawns=(ChildSpawnSpec(role="implementation-auditor", count=1),),
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


def test_child_spawn_cardinality_is_explicit_and_canonical() -> None:
    from autoskillit.core import (
        ChildSpawnSpec,
        LogicalRoleSpec,
        SkillContractError,
        SkillSemanticPlan,
    )

    dynamic = ChildSpawnSpec(role="researcher", for_each="research_topics")
    logical_roles = (LogicalRoleSpec(name="researcher", purpose="research one topic"),)
    plan = SkillSemanticPlan(
        schema_version=1, child_spawns=(dynamic,), logical_roles=logical_roles
    )

    assert dynamic.count is None
    assert plan.canonical_payload["child_spawns"] == (
        {"role": "researcher", "for_each": "research_topics"},
    )
    fixed = SkillSemanticPlan(
        schema_version=1,
        child_spawns=(ChildSpawnSpec(role="researcher", count=1),),
        logical_roles=logical_roles,
    )
    assert fixed.canonical_payload["child_spawns"] == ({"role": "researcher", "count": 1},)
    assert "for_each" not in fixed.canonical_payload["child_spawns"][0]
    with pytest.raises(SkillContractError, match="for_each must be a non-empty"):
        ChildSpawnSpec(role="researcher", for_each=" ")
    assert not hasattr(plan, "__dict__")
    with pytest.raises(FrozenInstanceError):
        plan.schema_version = 2  # type: ignore[misc]


@pytest.mark.parametrize("count", [True, 1.5, "1", 0, -1])
def test_child_spawn_rejects_non_positive_integer_counts(count: object) -> None:
    from autoskillit.core import ChildSpawnCardinalityError, ChildSpawnSpec

    with pytest.raises(ChildSpawnCardinalityError, match="positive integer"):
        ChildSpawnSpec(role="researcher", count=count)  # type: ignore[arg-type]


def test_child_spawn_rejects_missing_or_competing_authorities() -> None:
    from autoskillit.core import ChildSpawnCardinalityError, ChildSpawnSpec

    with pytest.raises(ChildSpawnCardinalityError, match="exactly one"):
        ChildSpawnSpec(role="researcher")
    with pytest.raises(ChildSpawnCardinalityError, match="exactly one"):
        ChildSpawnSpec(role="researcher", count=1, for_each="research_topics")


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


def test_managed_join_adaptation_context_is_immutable_and_digestible() -> None:
    from autoskillit.core import (
        BackendCapabilities,
        JoinSpec,
        SemanticAdaptationContext,
        SkillContractError,
        SkillSemanticPlan,
        required_join_is_unsupported,
    )
    from tests.fakes import make_managed_codex_context

    context = make_managed_codex_context("parent-1")
    attestation = context.managed_join_attestation
    assert attestation is not None

    assert context.admits_managed_join_for("codex")
    assert not context.admits_managed_join_for("claude")
    required_join = SkillSemanticPlan(schema_version=1, join=JoinSpec(required=True))
    capabilities = BackendCapabilities(fixed_set_join_capable=False)
    assert not required_join_is_unsupported(required_join, capabilities, "codex", context)
    assert required_join_is_unsupported(required_join, capabilities, "claude-code", context)
    assert context.digest == SemanticAdaptationContext(managed_join_attestation=attestation).digest
    with pytest.raises(FrozenInstanceError):
        context.managed_join_attestation = None  # type: ignore[misc]

    catalog = b'{"models":[]}'
    catalog_attestation = replace(
        attestation,
        codex_catalog_digest=hashlib.sha256(catalog).hexdigest(),
    )
    complete = SemanticAdaptationContext(
        managed_join_attestation=catalog_attestation,
        managed_codex_catalog=catalog,
    )
    assert complete.managed_codex_catalog == catalog
    assert (
        complete.canonical_payload
        == SemanticAdaptationContext(
            managed_join_attestation=catalog_attestation
        ).canonical_payload
    )
    with pytest.raises(SkillContractError, match="does not match its attestation"):
        SemanticAdaptationContext(
            managed_join_attestation=catalog_attestation,
            managed_codex_catalog=b"tampered",
        )
