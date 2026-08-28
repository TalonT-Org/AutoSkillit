"""Focused server-owned managed-leaf authority tests."""

from __future__ import annotations

import pytest

from autoskillit.core import (
    SkillContractError,
    SkillSemanticAdaptationResult,
    SkillSource,
    SkillSourceIdentity,
    WriteBehaviorSpec,
)
from autoskillit.hooks._session_binding import LoadedSkillEntry
from autoskillit.server.tools.tools_execution._managed_leaf import (
    ManagedLeafAssignmentInput,
    bind_managed_leaf,
    classify_managed_leaf_workspace,
    may_retry_managed_leaf,
    plan_managed_leaf_identities,
    project_managed_leaf,
)
from autoskillit.workspace import AgentSkillDocument

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


def test_managed_leaf_planner_and_projection_bind_only_leaf_authority() -> None:
    assignments = (
        ManagedLeafAssignmentInput(
            role="reviewer",
            label="  first review  ",
            runtime_key=" primary ",
            task_prompt="Inspect the first change.",
        ),
        ManagedLeafAssignmentInput(
            role="reviewer",
            label="second review",
            task_prompt="Inspect the second change.",
        ),
    )
    plan = plan_managed_leaf_identities("request-1", assignments)

    assert plan == plan_managed_leaf_identities("request-1", assignments)
    assert plan.assignments[0].label == "first review"
    assert plan.assignments[0].runtime_key == "primary"
    assert plan.assignments[0].assignment_id.startswith(f"{plan.batch_id}:assignment-")
    assert plan.assignments[0].first_run_id.startswith("managed-run-")
    assert plan.assignments[0].generated_home_id.startswith("managed-leaf-")

    adaptation = SkillSemanticAdaptationResult(
        logical_role_mapping={"reviewer": "reviewer"},
        model_effort_policy={"reviewer": ("gpt-5.6-luna", "high")},
    )
    document = AgentSkillDocument(
        content=(
            "Source contract.\n\n"
            "- Use the server-owned managed fixed-batch route to declare, launch, and "
            "join the complete assignment set before parent synthesis.\n"
        ),
        projected_digest="projected-source",
        canonical_digest="canonical-source",
        source_identity=SkillSourceIdentity(SkillSource.BUNDLED, "review-skill"),
        semantic_digest="semantic-source",
        adaptation_digest=adaptation.digest,
    )
    selected_source = LoadedSkillEntry(
        skill_name="review-skill",
        ts="2026-08-28T00:00:00Z",
        join_required=True,
        child_spawn_cardinality={"reviewer": 2},
        semantic_digest="semantic-source",
        adaptation_digest=adaptation.digest,
        projected_digest="projected-source",
        canonical_digest="canonical-source",
        source_artifact_digest="source-artifact",
        source_artifact_incarnation_id="incarnation-1",
        binding_valid=True,
        binding_error=None,
    )

    binding = bind_managed_leaf(
        assignment=plan.assignments[0],
        selected_source=selected_source,
        source_document=document,
        adaptation=adaptation,
        default_model="gpt-5.6-sol",
        write_behavior=WriteBehaviorSpec(mode="conditional"),
        read_only=False,
    )
    leaf = project_managed_leaf(binding, document)

    assert leaf.resume_session_id == ""
    assert leaf.binding.model == "gpt-5.6-luna"
    assert leaf.leaf_projection_artifact_digest != document.projected_digest
    assert (
        leaf.ledger_attempt_evidence["generated_home_id"] == plan.assignments[0].generated_home_id
    )
    assert (
        leaf.ledger_attempt_evidence["leaf_projection_artifact_digest"]
        == leaf.leaf_projection_artifact_digest
    )
    assert "managed fixed-batch route" not in leaf.prompt
    assert "Inspect the first change." in leaf.prompt
    assert document.content != leaf.prompt


def test_managed_leaf_workspace_classification_is_safe_for_effect_retries() -> None:
    read_only = classify_managed_leaf_workspace(
        read_only=True,
        write_behavior=WriteBehaviorSpec(),
    )
    idempotent = classify_managed_leaf_workspace(
        read_only=False,
        write_behavior=WriteBehaviorSpec(
            mode="always",
            external_effect="serialized-idempotent",
        ),
    )
    unknown = classify_managed_leaf_workspace(
        read_only=False,
        write_behavior=WriteBehaviorSpec(
            mode="always",
            external_effect="serialized-unknown-completion",
        ),
    )

    assert read_only.shared_workspace
    assert idempotent.requires_isolated_worktree
    assert may_retry_managed_leaf(idempotent, launched=False, verified_non_execution=False)
    assert may_retry_managed_leaf(idempotent, launched=True, verified_non_execution=True)
    assert not may_retry_managed_leaf(idempotent, launched=True, verified_non_execution=False)
    assert not may_retry_managed_leaf(unknown, launched=False, verified_non_execution=False)
    with pytest.raises(SkillContractError, match="requires a declared write_behavior"):
        classify_managed_leaf_workspace(read_only=False, write_behavior=WriteBehaviorSpec())
