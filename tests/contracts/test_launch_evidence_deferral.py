"""Session-invariant admission separates launch-evidence deferrals from refusals.

``refusal_awaits_launch_evidence`` must agree with every backend's two-valued
admission: refused without evidence, admitted with managed-join evidence.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

from autoskillit.core import (
    ChildModelPolicySpec,
    ChildSpawnSpec,
    ConcurrencySpec,
    EvidenceSpec,
    GitMetadataWriteSpec,
    JoinSpec,
    LaunchEvidenceDeferral,
    LogicalRoleSpec,
    PluginLaunchBinding,
    PluginLoadMode,
    SiblingSkillSpec,
    SkillSemanticAdaptationResult,
    SkillSemanticOperation,
    SkillSemanticPlan,
    adapt_session_invariant,
    refusal_awaits_launch_evidence,
)
from autoskillit.execution.backends import CodexBackend, all_backends
from autoskillit.execution.backends._codex_discovery import (
    CODEX_MANAGED_HOME_ROUTE,
    select_interactive_discovery_route,
)
from autoskillit.execution.headless._managed._attempt import _headless_plugin_load_mode
from tests.fakes import make_managed_codex_context

pytestmark = [pytest.mark.layer("contracts"), pytest.mark.small]

_ROLE = LogicalRoleSpec(name="reviewer", purpose="review one concern")

_PLAN_BY_OPERATION: dict[SkillSemanticOperation, SkillSemanticPlan] = {
    SkillSemanticOperation.CHILD_SPAWN: SkillSemanticPlan(
        schema_version=1,
        child_spawns=(ChildSpawnSpec(role="reviewer", count=1),),
        logical_roles=(_ROLE,),
    ),
    SkillSemanticOperation.REQUIRED_CONCURRENCY: SkillSemanticPlan(
        schema_version=1,
        concurrency=ConcurrencySpec(required=True),
    ),
    SkillSemanticOperation.REQUIRED_JOIN: SkillSemanticPlan(
        schema_version=1,
        join=JoinSpec(required=True),
    ),
    SkillSemanticOperation.REQUIRED_EVIDENCE: SkillSemanticPlan(
        schema_version=1,
        evidence=EvidenceSpec(required=True, independent=True),
    ),
    SkillSemanticOperation.CHILD_MODEL_POLICY: SkillSemanticPlan(
        schema_version=1,
        child_model_policies=(
            ChildModelPolicySpec(role="reviewer", model_class="opus", reasoning_effort="high"),
        ),
        logical_roles=(_ROLE,),
    ),
    SkillSemanticOperation.LOGICAL_ROLE: SkillSemanticPlan(
        schema_version=1,
        logical_roles=(_ROLE,),
    ),
    SkillSemanticOperation.SIBLING_SKILL_INVOKE: SkillSemanticPlan(
        schema_version=1,
        sibling_skills=(SiblingSkillSpec(name="investigate"),),
    ),
    SkillSemanticOperation.GIT_METADATA_WRITE: SkillSemanticPlan(
        schema_version=1,
        git_metadata_writes=(GitMetadataWriteSpec(purpose="create a commit"),),
    ),
}


def test_every_operation_has_a_single_operation_plan() -> None:
    assert set(_PLAN_BY_OPERATION) == set(SkillSemanticOperation)
    for operation, plan in _PLAN_BY_OPERATION.items():
        assert operation in plan.operations


@pytest.mark.parametrize("operation", list(SkillSemanticOperation), ids=lambda op: op.value)
def test_deferral_predicate_matches_every_backend_admission(
    operation: SkillSemanticOperation,
) -> None:
    plan = _PLAN_BY_OPERATION[operation]
    ctx = make_managed_codex_context("deferral-contract")
    for backend in all_backends():
        refused = backend.adapt_skill_semantics(plan, None).unsupported_operation
        admitted_with_evidence = (
            backend.capabilities.managed_fixed_batch_route_capable
            and backend.adapt_skill_semantics(plan, ctx).unsupported_operation is None
        )
        awaits = refusal_awaits_launch_evidence(operation, backend.capabilities)
        assert awaits == (refused is operation and admitted_with_evidence), (
            f"{backend.name}: refusal_awaits_launch_evidence disagrees with the backend's "
            f"admission of {operation.value!r}"
        )
        result = adapt_session_invariant(plan, backend)
        deferred = refused is not None and refusal_awaits_launch_evidence(
            refused, backend.capabilities
        )
        if deferred:
            assert isinstance(result, LaunchEvidenceDeferral), backend.name
            assert result.operation is refused
            assert result.diagnostic
        else:
            assert isinstance(result, SkillSemanticAdaptationResult), backend.name
            assert result.unsupported_operation is refused


def test_evidence_dependent_backends_always_have_a_generated_home() -> None:
    for backend in all_backends():
        if refusal_awaits_launch_evidence(
            SkillSemanticOperation.REQUIRED_JOIN, backend.capabilities
        ):
            assert backend.capabilities.session_dir_persistent, backend.name


def test_generated_home_selects_the_managed_discovery_route(tmp_path: Path) -> None:
    binding = cast(
        PluginLaunchBinding,
        SimpleNamespace(load_mode=PluginLoadMode.PROJECTED_HOME),
    )
    assert (
        select_interactive_discovery_route(generated_home=tmp_path, plugin_binding=binding)
        is CODEX_MANAGED_HOME_ROUTE
    )


def test_headless_codex_generated_home_load_mode() -> None:
    assert (
        _headless_plugin_load_mode(CodexBackend(), requires_generated_home=True)
        is PluginLoadMode.GENERATED_HOME
    )
