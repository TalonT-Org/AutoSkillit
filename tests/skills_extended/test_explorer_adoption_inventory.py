"""Reviewed inventories for planner and Phase D exploration-vector adopters."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence

import pytest

from autoskillit.core import (
    ExplorationVectorApplicabilityId,
    ExplorationVectorDef,
    ExplorationVectorDisposition,
    RepositoryProfileId,
    SkillExecutionRole,
    SkillSource,
    pkg_root,
)
from autoskillit.execution.backends.claude import ClaudeCodeBackend
from autoskillit.execution.backends.codex import CodexBackend
from autoskillit.workspace import EffectiveSkillInvocation, SkillInfo
from autoskillit.workspace._projected_artifact.materialization import (
    SkillProjectionContext,
    project_agent_skill_document,
)
from autoskillit.workspace.skills import _skill_info_from_frontmatter

pytestmark = [pytest.mark.small]

InventoryRow = tuple[str, str, str | None, str]
PhaseDInventoryRow = tuple[str, str, str | None, str, str]

_INVENTORY: dict[str, tuple[InventoryRow, ...]] = {
    "planner-analyze": (
        (
            "languages-frameworks",
            "migrated",
            "repository-impact-profiler",
            "planner-analyze-languages-frameworks",
        ),
        (
            "test-infrastructure",
            "migrated",
            "repository-impact-profiler",
            "planner-analyze-test-infrastructure",
        ),
        (
            "architecture-patterns",
            "migrated",
            "semantic-code-navigator",
            "planner-analyze-architecture-patterns",
        ),
        (
            "existing-conventions",
            "migrated",
            "semantic-code-navigator",
            "planner-analyze-existing-conventions",
        ),
        (
            "existing-conventions-impact",
            "migrated",
            "repository-impact-profiler",
            "planner-analyze-existing-conventions-impact",
        ),
    ),
    "planner-extract-domain": (
        (
            "domain-vocabulary",
            "migrated",
            "repository-impact-profiler",
            "planner-extract-domain-vocabulary",
        ),
        (
            "existing-abstractions",
            "migrated",
            "semantic-code-navigator",
            "planner-extract-domain-abstractions",
        ),
        (
            "integration-points",
            "migrated",
            "semantic-code-navigator",
            "planner-extract-domain-integration-points",
        ),
        (
            "integration-consumers",
            "migrated",
            "repository-impact-profiler",
            "planner-extract-domain-integration-consumers",
        ),
        (
            "cross-cutting-concerns",
            "migrated",
            "semantic-code-navigator",
            "planner-extract-domain-cross-cutting",
        ),
        (
            "data-flow-patterns",
            "migrated",
            "semantic-code-navigator",
            "planner-extract-domain-data-flow",
        ),
        (
            "cross-cutting-impact",
            "migrated",
            "repository-impact-profiler",
            "planner-extract-domain-cross-cutting-impact",
        ),
    ),
    "planner-elaborate-phase": (
        (
            "affected-files",
            "migrated",
            "semantic-code-navigator",
            "planner-elaborate-phase-affected-files",
        ),
        (
            "affected-file-impact",
            "migrated",
            "repository-impact-profiler",
            "planner-elaborate-phase-affected-file-impact",
        ),
        (
            "dependency-analysis",
            "migrated",
            "semantic-code-navigator",
            "planner-elaborate-phase-dependencies",
        ),
        (
            "test-coverage",
            "migrated",
            "repository-impact-profiler",
            "planner-elaborate-phase-test-coverage",
        ),
        (
            "pattern-discovery",
            "migrated",
            "repository-impact-profiler",
            "planner-elaborate-phase-patterns",
        ),
        (
            "cross-phase-boundaries",
            "migrated",
            "semantic-code-navigator",
            "planner-elaborate-phase-boundaries",
        ),
    ),
}

_PHASE_D_INVENTORY: dict[str, tuple[PhaseDInventoryRow, ...]] = {
    "investigate": (
        (
            "standard-core-implementation",
            "migrated",
            "semantic-code-navigator",
            "investigate-standard-core-implementation",
            "investigate-standard",
        ),
        (
            "standard-dependencies",
            "migrated",
            "semantic-code-navigator",
            "investigate-standard-dependencies",
            "investigate-standard",
        ),
        (
            "standard-consumer-impact",
            "migrated",
            "repository-impact-profiler",
            "investigate-standard-consumer-impact",
            "investigate-standard",
        ),
        (
            "standard-test-coverage",
            "migrated",
            "repository-impact-profiler",
            "investigate-standard-test-coverage",
            "investigate-standard",
        ),
        (
            "standard-error-provenance",
            "migrated",
            "semantic-code-navigator",
            "investigate-standard-error-provenance",
            "investigate-standard",
        ),
        (
            "standard-similar-patterns",
            "migrated",
            "semantic-code-navigator",
            "investigate-standard-similar-patterns",
            "investigate-standard",
        ),
        (
            "standard-architecture-constraints",
            "migrated",
            "repository-impact-profiler",
            "investigate-standard-architecture-constraints",
            "investigate-standard",
        ),
        (
            "standard-web-research",
            "retained",
            None,
            "investigate-standard-web-research",
            "investigate-standard",
        ),
        (
            "standard-design-intent-history",
            "migrated",
            "repository-impact-profiler",
            "investigate-standard-design-intent-history",
            "investigate-standard",
        ),
        (
            "standard-design-intent-reasoning",
            "retained",
            None,
            "investigate-standard-design-intent-reasoning",
            "investigate-standard",
        ),
        (
            "standard-recurrence-history",
            "migrated",
            "repository-impact-profiler",
            "investigate-standard-recurrence-history",
            "investigate-standard",
        ),
        (
            "standard-recurrence-reasoning",
            "retained",
            None,
            "investigate-standard-recurrence-reasoning",
            "investigate-standard",
        ),
        (
            "deep-code-paths",
            "migrated",
            "semantic-code-navigator",
            "investigate-deep-code-paths",
            "investigate-deep",
        ),
        (
            "deep-log-history",
            "migrated",
            "repository-impact-profiler",
            "investigate-deep-log-history",
            "investigate-deep",
        ),
        (
            "deep-dependencies",
            "migrated",
            "semantic-code-navigator",
            "investigate-deep-dependencies",
            "investigate-deep",
        ),
        (
            "deep-related-components",
            "migrated",
            "repository-impact-profiler",
            "investigate-deep-related-components",
            "investigate-deep",
        ),
        (
            "deep-web-research",
            "retained",
            None,
            "investigate-deep-web-research",
            "investigate-deep",
        ),
        (
            "deep-design-intent-reasoning",
            "retained",
            None,
            "investigate-deep-design-intent-reasoning",
            "investigate-deep",
        ),
        (
            "deep-recurrence-reasoning",
            "retained",
            None,
            "investigate-deep-recurrence-reasoning",
            "investigate-deep",
        ),
        (
            "deep-code-deepening",
            "migrated",
            "semantic-code-navigator",
            "investigate-deep-code-deepening",
            "investigate-deep",
        ),
        (
            "deep-informed-web-research",
            "retained",
            None,
            "investigate-deep-informed-web-research",
            "investigate-deep",
        ),
        (
            "deep-design-intent-refresh",
            "retained",
            None,
            "investigate-deep-design-intent-refresh",
            "investigate-deep",
        ),
        (
            "deep-hypothesis-challenge",
            "retained",
            None,
            "investigate-deep-hypothesis-challenge",
            "investigate-deep",
        ),
        (
            "deep-solution-generation",
            "retained",
            None,
            "investigate-deep-solution-generation",
            "investigate-deep",
        ),
        (
            "deep-candidate-blast-radius",
            "migrated",
            "repository-impact-profiler",
            "investigate-deep-candidate-blast-radius",
            "investigate-deep",
        ),
        (
            "deep-breakage-reasoning",
            "retained",
            None,
            "investigate-deep-breakage-reasoning",
            "investigate-deep",
        ),
        (
            "deep-factual-validation",
            "retained",
            None,
            "investigate-deep-factual-validation",
            "investigate-deep",
        ),
        (
            "deep-recommendation-validation",
            "retained",
            None,
            "investigate-deep-recommendation-validation",
            "investigate-deep",
        ),
        (
            "deep-gap-validation",
            "retained",
            None,
            "investigate-deep-gap-validation",
            "investigate-deep",
        ),
    ),
    "scope": (
        (
            "prior-art-codebase",
            "migrated",
            "repository-impact-profiler",
            "scope-prior-art-codebase",
            "scope-software",
        ),
        (
            "prior-art-literature",
            "retained",
            None,
            "scope-prior-art-literature",
            "scope-non-software",
        ),
        ("external-research", "retained", None, "scope-external-research", "always"),
        (
            "domain-context-architecture",
            "migrated",
            "semantic-code-navigator",
            "scope-domain-context-architecture",
            "scope-software",
        ),
        (
            "domain-context-domain-knowledge",
            "retained",
            None,
            "scope-domain-context-domain-knowledge",
            "scope-non-software",
        ),
        (
            "evaluation-framework-software",
            "migrated",
            "repository-impact-profiler",
            "scope-evaluation-framework-software",
            "scope-software",
        ),
        (
            "evaluation-framework-domain-assessment",
            "retained",
            None,
            "scope-evaluation-framework-domain-assessment",
            "scope-non-software",
        ),
        (
            "computational-complexity-local",
            "migrated",
            "semantic-code-navigator",
            "scope-computational-complexity-local",
            "scope-software",
        ),
        (
            "computational-complexity-external",
            "retained",
            None,
            "scope-computational-complexity-external",
            "scope-software",
        ),
        (
            "data-availability-repository",
            "migrated",
            "repository-impact-profiler",
            "scope-data-availability-repository",
            "scope-software",
        ),
        (
            "data-availability-external",
            "retained",
            None,
            "scope-data-availability-external",
            "always",
        ),
        ("custom-research", "retained", None, "scope-custom-research", "scope-non-software"),
    ),
    "arch-lens-module-dependency": (
        (
            "project-build-config-artifacts",
            "migrated",
            "repository-impact-profiler",
            "arch-lens-module-dependency-project-build-config-artifacts",
            "always",
        ),
        (
            "module-layer-structure",
            "migrated",
            "semantic-code-navigator",
            "arch-lens-module-dependency-module-layer-structure",
            "always",
        ),
        (
            "import-analysis-by-layer",
            "migrated",
            "semantic-code-navigator",
            "arch-lens-module-dependency-import-analysis-by-layer",
            "always",
        ),
        (
            "circular-dependency-detection",
            "migrated",
            "semantic-code-navigator",
            "arch-lens-module-dependency-circular-dependency-detection",
            "always",
        ),
        (
            "high-fan-in-modules",
            "migrated",
            "semantic-code-navigator",
            "arch-lens-module-dependency-high-fan-in-modules",
            "always",
        ),
        (
            "cross-domain-imports",
            "migrated",
            "semantic-code-navigator",
            "arch-lens-module-dependency-cross-domain-imports",
            "always",
        ),
    ),
    "arch-lens-state-lifecycle": (
        (
            "state-schema",
            "migrated",
            "semantic-code-navigator",
            "arch-lens-state-lifecycle-state-schema",
            "always",
        ),
        (
            "field-categories",
            "migrated",
            "semantic-code-navigator",
            "arch-lens-state-lifecycle-field-categories",
            "always",
        ),
        (
            "validation-gates",
            "migrated",
            "semantic-code-navigator",
            "arch-lens-state-lifecycle-validation-gates",
            "always",
        ),
        (
            "resume-detection",
            "migrated",
            "semantic-code-navigator",
            "arch-lens-state-lifecycle-resume-detection",
            "always",
        ),
        (
            "state-updates",
            "migrated",
            "semantic-code-navigator",
            "arch-lens-state-lifecycle-state-updates",
            "always",
        ),
        (
            "contract-enforcement",
            "migrated",
            "semantic-code-navigator",
            "arch-lens-state-lifecycle-contract-enforcement",
            "always",
        ),
    ),
    "arch-lens-development": (
        (
            "project-structure",
            "migrated",
            "semantic-code-navigator",
            "arch-lens-development-project-structure",
            "always",
        ),
        (
            "build-tooling",
            "migrated",
            "repository-impact-profiler",
            "arch-lens-development-build-tooling",
            "always",
        ),
        (
            "linting-formatting",
            "migrated",
            "repository-impact-profiler",
            "arch-lens-development-linting-formatting",
            "always",
        ),
        (
            "type-checking",
            "migrated",
            "repository-impact-profiler",
            "arch-lens-development-type-checking",
            "always",
        ),
        (
            "test-framework",
            "migrated",
            "repository-impact-profiler",
            "arch-lens-development-test-framework",
            "always",
        ),
        (
            "ci-cd",
            "migrated",
            "repository-impact-profiler",
            "arch-lens-development-ci-cd",
            "always",
        ),
        (
            "entry-points",
            "migrated",
            "semantic-code-navigator",
            "arch-lens-development-entry-points",
            "always",
        ),
    ),
}

_ARCHITECTURE_SELECTOR_SLUGS = (
    "c4-container",
    "concurrency",
    "data-lineage",
    "deployment",
    "development",
    "error-resilience",
    "module-dependency",
    "operational",
    "process-flow",
    "repository-access",
    "scenarios",
    "security",
    "state-lifecycle",
)

_ARCHITECTURE_LENS_INVENTORY: dict[str, tuple[PhaseDInventoryRow, ...]] = {
    "c4-container": (
        (
            "application-layer",
            "migrated",
            "semantic-code-navigator",
            "arch-lens-c4-container-application-layer",
            "always",
        ),
        (
            "service-business-logic-layer",
            "migrated",
            "semantic-code-navigator",
            "arch-lens-c4-container-service-business-logic-layer",
            "always",
        ),
        (
            "package-library-layer",
            "migrated",
            "semantic-code-navigator",
            "arch-lens-c4-container-package-library-layer",
            "always",
        ),
        (
            "data-storage-layer",
            "migrated",
            "semantic-code-navigator",
            "arch-lens-c4-container-data-storage-layer",
            "always",
        ),
        (
            "external-integrations",
            "migrated",
            "semantic-code-navigator",
            "arch-lens-c4-container-external-integrations",
            "always",
        ),
    ),
    "concurrency": tuple(
        (
            vector_id,
            "migrated",
            "semantic-code-navigator",
            f"arch-lens-concurrency-{vector_id}",
            "always",
        )
        for vector_id in (
            "concurrency-model",
            "worker-pools",
            "parallel-operations",
            "synchronization-points",
            "state-access",
            "sequential-boundaries",
        )
    ),
    "data-lineage": (
        (
            "data-origins-inputs",
            "migrated",
            "semantic-code-navigator",
            "arch-lens-data-lineage-data-origins-inputs",
            "always",
        ),
        (
            "transformation-stages",
            "migrated",
            "semantic-code-navigator",
            "arch-lens-data-lineage-transformation-stages",
            "always",
        ),
        (
            "format-changes",
            "migrated",
            "semantic-code-navigator",
            "arch-lens-data-lineage-format-changes",
            "always",
        ),
        (
            "storage-destinations",
            "migrated",
            "repository-impact-profiler",
            "arch-lens-data-lineage-storage-destinations",
            "always",
        ),
        (
            "access-patterns",
            "migrated",
            "semantic-code-navigator",
            "arch-lens-data-lineage-access-patterns",
            "always",
        ),
    ),
    "deployment": tuple(
        (
            vector_id,
            "migrated",
            role,
            f"arch-lens-deployment-{vector_id}",
            "always",
        )
        for vector_id, role in (
            ("process-boundaries", "semantic-code-navigator"),
            ("container-docker", "repository-impact-profiler"),
            ("local-storage", "semantic-code-navigator"),
            ("network-services", "semantic-code-navigator"),
            ("external-services", "semantic-code-navigator"),
            ("web-frontend", "repository-impact-profiler"),
        )
    ),
    "development": _PHASE_D_INVENTORY["arch-lens-development"],
    "error-resilience": tuple(
        (
            vector_id,
            "migrated",
            "semantic-code-navigator",
            f"arch-lens-error-resilience-{vector_id}",
            "always",
        )
        for vector_id in (
            "exception-hierarchy",
            "validation-gates",
            "error-detection",
            "recovery-mechanisms",
            "circuit-breakers",
            "error-routing",
        )
    ),
    "module-dependency": _PHASE_D_INVENTORY["arch-lens-module-dependency"],
    "operational": tuple(
        (
            vector_id,
            "migrated",
            role,
            f"arch-lens-operational-{vector_id}",
            "always",
        )
        for vector_id, role in (
            ("cli-entry-points", "semantic-code-navigator"),
            ("configuration", "repository-impact-profiler"),
            ("task-automation", "repository-impact-profiler"),
            ("logging-monitoring", "repository-impact-profiler"),
            ("status-health", "semantic-code-navigator"),
            ("reset-recovery", "semantic-code-navigator"),
        )
    ),
    "process-flow": tuple(
        (
            vector_id,
            "migrated",
            "semantic-code-navigator",
            f"arch-lens-process-flow-{vector_id}",
            "always",
        )
        for vector_id in (
            "state-machines-workflows",
            "entry-points-triggers",
            "decision-points",
            "loop-mechanisms",
            "terminal-states",
        )
    ),
    "repository-access": tuple(
        (
            vector_id,
            "migrated",
            "semantic-code-navigator",
            f"arch-lens-repository-access-{vector_id}",
            "always",
        )
        for vector_id in (
            "repository-classes",
            "entity-models",
            "crud-operations",
            "query-patterns",
            "factory-scoping",
            "format-conversion",
        )
    ),
    "scenarios": tuple(
        (
            vector_id,
            "migrated",
            "semantic-code-navigator",
            f"arch-lens-scenarios-{vector_id}",
            "always",
        )
        for vector_id in (
            "primary-use-cases",
            "happy-path-flows",
            "error-recovery-flows",
            "resume-restart-flows",
            "integration-points",
        )
    ),
    "security": tuple(
        (
            vector_id,
            "migrated",
            role,
            f"arch-lens-security-{vector_id}",
            "always",
        )
        for vector_id, role in (
            ("input-validation", "semantic-code-navigator"),
            ("path-security", "semantic-code-navigator"),
            ("process-boundaries", "semantic-code-navigator"),
            ("authentication-authorization", "semantic-code-navigator"),
            ("secret-management", "repository-impact-profiler"),
            ("file-system-security", "repository-impact-profiler"),
            ("database-isolation", "repository-impact-profiler"),
        )
    ),
    "state-lifecycle": _PHASE_D_INVENTORY["arch-lens-state-lifecycle"],
}

_ARCHITECTURE_REVIEW_DIGESTS = {
    "c4-container": "67a02b5577994ac2a615871ceb633f456536fbfb25ec6c9d6136bcbaf60686c3",
    "concurrency": "08e120b079c45e82416c5f86b11ba4804e25ec9105e2bbe5df52472fd1d362ce",
    "data-lineage": "5b7ac2e32320d3a82bf38d6f12e4277823e2d868c0bc00c8e9acfe6025ef7132",
    "deployment": "8ef06c7e164e495c17d91cb197b8e081c51f010a7a0fb561a5911eec93f6581c",
    "development": "62fce705d8b7292561dc44d5dabec6436334b081cac725cebd999676c4009fc6",
    "error-resilience": "fae9d01f12b2f6e3a9a7091a9f7249a66114c8f834f01e7b5929868f4f47f187",
    "module-dependency": "0906197b52200c9af01aefe7e73b954d14eaf3ff520f108990634256371ebaa1",
    "operational": "1456102221b81b2e3b3df240687342f48adf27f480aa49899717d96ad1db210c",
    "process-flow": "cc5afc815f03c4df1de2b1e5841412856085225b446f4b919489871976fea788",
    "repository-access": "b64369dcfcc8e5e8bb87dc66b784ff02daac7948fa93e9dd6ab551f6408c41f4",
    "scenarios": "2f8d649845a081da5d92807da906721cdac3d47045a45521cc7782ca38e225d8",
    "security": "15b331b3aac8e57a86f50683d570f2a3b206fbce0c418585cd66e2f3400080c6",
    "state-lifecycle": "774a419edb0502863dd0d86d5b072aa2e4fe6079f6addd47d6106c1665e0af36",
}

_ARCHITECTURE_RECIPE_STEP_PINS = {
    "implementation.run_arch_lenses",
    "implementation-groups.run_arch_lenses",
    "remediation.run_arch_lenses",
}

_PHASE_D_REVIEW_DIGESTS = {
    "investigate": "a9b6026ed99b0071006f87fb6629ea782c665cc16f86f93c006a554eac49453a",
    "scope": "c9b43045d40f08e1ddaee38a9a0a6dc2ebad9fc75a0fe9f18a808eda5ee5cd84",
    "arch-lens-module-dependency": (
        "0906197b52200c9af01aefe7e73b954d14eaf3ff520f108990634256371ebaa1"
    ),
    "arch-lens-state-lifecycle": (
        "774a419edb0502863dd0d86d5b072aa2e4fe6079f6addd47d6106c1665e0af36"
    ),
    "arch-lens-development": ("62fce705d8b7292561dc44d5dabec6436334b081cac725cebd999676c4009fc6"),
}

_RAW_MIGRATED_AGENT_SYNTAX = (
    re.compile(r"\bAgent\s*\("),
    re.compile(r"\bTask\s*\("),
    re.compile(r"\bspawn_agent\s*\("),
    re.compile(
        r"""(?ix)\b(?:subagent_type|agent_type)\s*=\s*["']"""
        r"""(?:generic[- ]purpose:)?explore["']"""
    ),
    re.compile(r"(?i)\bgeneric[- ]explore\s+(?:subagent|agent)\b"),
)


def _load_phase_d_skill(skill_name: str) -> SkillInfo:
    skill_path = pkg_root() / "skills_extended" / skill_name / "SKILL.md"
    info = _skill_info_from_frontmatter(
        skill_name,
        SkillSource.BUNDLED_EXTENDED,
        skill_path,
    )
    assert info.invalid_reason is None
    return info


def _review_digest(info: SkillInfo) -> str:
    payload = [
        (
            vector.id,
            vector.rationale,
            [relationship.value for relationship in vector.relationship_classes],
            vector.native_dispatch,
        )
        for vector in info.exploration_vectors
    ]
    encoded = json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _project_phase_d_skill(
    skill: SkillInfo,
    backend: ClaudeCodeBackend | CodexBackend,
    active_applicabilities: frozenset[ExplorationVectorApplicabilityId],
) -> str:
    invocation = EffectiveSkillInvocation(
        root=skill,
        closure=(skill,),
        capability_union=skill.uses_capabilities,
        project_root=pkg_root(),
        execution_role=SkillExecutionRole.SESSION,
    )
    context = SkillProjectionContext(
        cwd=pkg_root(),
        invocation=invocation,
        backend=backend,
        resolved_exploration_profile=RepositoryProfileId.AUTOSKILLIT,
        active_exploration_applicabilities=active_applicabilities,
        parent_sandbox_mode="read-only",
    )
    return project_agent_skill_document(skill, context).content


def _marker_body(content: str, vector: ExplorationVectorDef) -> str:
    return (
        content.split(vector.marker_line, 1)[1]
        .split("<!-- /autoskillit:exploration-vector -->", 1)[0]
        .strip("\n")
    )


@pytest.fixture
def adoption_inventory() -> Mapping[str, Sequence[InventoryRow]]:
    return _INVENTORY


@pytest.mark.parametrize("skill_name", sorted(_INVENTORY))
def test_planner_skill_vectors_match_reviewed_inventory(
    skill_name: str,
    adoption_inventory: Mapping[str, Sequence[InventoryRow]],
) -> None:
    expected = adoption_inventory[skill_name]
    skill_path = pkg_root() / "skills_extended" / skill_name / "SKILL.md"

    info = _skill_info_from_frontmatter(
        skill_name,
        SkillSource.BUNDLED_EXTENDED,
        skill_path,
    )

    assert info.invalid_reason is None
    assert [
        [vector.id, vector.disposition.value, vector.role, vector.task.task_id]
        for vector in info.exploration_vectors
    ] == [list(row) for row in expected]
    assert all(vector.profile.value == "auto" for vector in info.exploration_vectors)
    assert all(vector.task.scope == (".",) for vector in info.exploration_vectors)
    assert all(vector.task.depends_on == () for vector in info.exploration_vectors)
    assert all(vector.body.strip() for vector in info.exploration_vectors)

    content = skill_path.read_text(encoding="utf-8")
    for vector in info.exploration_vectors:
        assert content.count(vector.marker_line) == 1
    assert content.count("<!-- /autoskillit:exploration-vector -->") == len(expected)


def test_dynamic_deep_mode_vectors_have_closed_conditional_applicability() -> None:
    skill_name = "planner-extract-domain"
    skill_path = pkg_root() / "skills_extended" / skill_name / "SKILL.md"
    info = _skill_info_from_frontmatter(
        skill_name,
        SkillSource.BUNDLED_EXTENDED,
        skill_path,
    )

    conditional = tuple(
        vector
        for vector in info.exploration_vectors
        if vector.applicability.value == "planner-extract-domain-deep"
    )

    assert tuple(vector.id for vector in conditional) == (
        "cross-cutting-concerns",
        "data-flow-patterns",
        "cross-cutting-impact",
    )
    assert all(
        vector.disposition is ExplorationVectorDisposition.MIGRATED and vector.native_dispatch
        for vector in conditional
    )
    assert {vector.role for vector in conditional} == {
        "semantic-code-navigator",
        "repository-impact-profiler",
    }


def test_inventory_is_complete_for_owned_planner_adopters(
    adoption_inventory: Mapping[str, Sequence[InventoryRow]],
) -> None:
    inventory = adoption_inventory

    assert set(inventory) == {
        "planner-analyze",
        "planner-extract-domain",
        "planner-elaborate-phase",
    }
    assert sum(len(vectors) for vectors in inventory.values()) == 18
    assert (
        sum(
            disposition == "migrated"
            for vectors in inventory.values()
            for _, disposition, _, _ in vectors
        )
        == 18
    )


@pytest.mark.parametrize("skill_name", sorted(_PHASE_D_INVENTORY))
def test_phase_d_skill_vectors_match_reviewed_inventory(skill_name: str) -> None:
    expected = _PHASE_D_INVENTORY[skill_name]
    skill_path = pkg_root() / "skills_extended" / skill_name / "SKILL.md"
    info = _load_phase_d_skill(skill_name)

    assert [
        (
            vector.id,
            vector.disposition.value,
            vector.role,
            vector.task.task_id,
            vector.applicability.value,
        )
        for vector in info.exploration_vectors
    ] == list(expected)
    assert all(vector.profile.value == "auto" for vector in info.exploration_vectors)
    assert all(vector.task.scope == (".",) for vector in info.exploration_vectors)
    assert all(vector.task.depends_on == () for vector in info.exploration_vectors)
    assert all(vector.body.strip() for vector in info.exploration_vectors)
    assert all(vector.rationale.strip() for vector in info.exploration_vectors)
    assert all(vector.relationship_classes for vector in info.exploration_vectors)
    assert _review_digest(info) == _PHASE_D_REVIEW_DIGESTS[skill_name]
    assert all(
        (
            vector.role is not None
            and vector.native_dispatch
            and vector.role in {"semantic-code-navigator", "repository-impact-profiler"}
        )
        if vector.disposition is ExplorationVectorDisposition.MIGRATED
        else vector.role is None and not vector.native_dispatch
        for vector in info.exploration_vectors
    )

    content = skill_path.read_text(encoding="utf-8")
    for vector in info.exploration_vectors:
        assert content.count(vector.marker_line) == 1
    assert content.count("<!-- /autoskillit:exploration-vector -->") == len(expected)


@pytest.mark.parametrize("skill_name", sorted(_PHASE_D_INVENTORY))
def test_migrated_phase_d_bodies_have_no_raw_agent_authoring_syntax(
    skill_name: str,
) -> None:
    info = _load_phase_d_skill(skill_name)

    for vector in info.exploration_vectors:
        if vector.disposition is not ExplorationVectorDisposition.MIGRATED:
            continue
        for pattern in _RAW_MIGRATED_AGENT_SYNTAX:
            assert pattern.search(vector.body) is None, (skill_name, vector.id, pattern.pattern)


@pytest.mark.parametrize(
    ("backend", "native_prefix"),
    [
        (ClaudeCodeBackend(), 'Agent(subagent_type="autoskillit:'),
        (CodexBackend(), 'spawn_agent(agent_type="'),
    ],
)
def test_all_actual_migrated_phase_d_vectors_render_each_native_backend_form(
    backend: ClaudeCodeBackend | CodexBackend,
    native_prefix: str,
) -> None:
    active = frozenset(ExplorationVectorApplicabilityId)
    migrated_count = 0

    for skill_name in _PHASE_D_INVENTORY:
        skill = _load_phase_d_skill(skill_name)
        projected = _project_phase_d_skill(skill, backend, active)
        for vector in skill.exploration_vectors:
            body = _marker_body(projected, vector)
            if vector.disposition is not ExplorationVectorDisposition.MIGRATED:
                assert body == vector.body, (skill_name, vector.id)
                continue

            migrated_count += 1
            assert vector.profile is RepositoryProfileId.AUTO
            assert f'{native_prefix}{vector.role}"' in body, (skill_name, vector.id)
            assert f"task_id: {vector.task.task_id}" in body, (skill_name, vector.id)
            assert "profile: autoskillit" in body, (skill_name, vector.id)
            assert (
                "relationship_classes: "
                + ",".join(item.value for item in vector.relationship_classes)
                in body
            ), (skill_name, vector.id)
            assert json.dumps(vector.body)[1:-1] in body, (skill_name, vector.id)

    assert migrated_count == 39


@pytest.mark.parametrize("skill_name", sorted(_PHASE_D_INVENTORY))
def test_actual_migrated_phase_d_applicability_controls_native_dispatch(
    skill_name: str,
) -> None:
    skill = _load_phase_d_skill(skill_name)
    migrated = tuple(
        vector
        for vector in skill.exploration_vectors
        if vector.disposition is ExplorationVectorDisposition.MIGRATED
    )
    applicabilities = {vector.applicability for vector in migrated}

    for selected in applicabilities:
        active = frozenset({ExplorationVectorApplicabilityId.ALWAYS, selected})
        projected = _project_phase_d_skill(skill, CodexBackend(), active)
        for vector in migrated:
            body = _marker_body(projected, vector)
            is_active = (
                vector.applicability is ExplorationVectorApplicabilityId.ALWAYS
                or vector.applicability is selected
            )
            if is_active:
                assert f'spawn_agent(agent_type="{vector.role}"' in body, (
                    skill_name,
                    vector.id,
                    selected.value,
                )
            else:
                assert "not applicable to the current invocation" in body, (
                    skill_name,
                    vector.id,
                    selected.value,
                )


def test_architecture_selectors_filesystem_inventory_and_native_matrix_are_exact() -> None:
    prepare_pr = (pkg_root() / "skills_extended" / "prepare-pr" / "SKILL.md").read_text(
        encoding="utf-8"
    )
    selector_table = prepare_pr.split("### Step 5: Select Arch-Lens Slugs", 1)[1].split(
        "**Selection algorithm:**", 1
    )[0]
    selector_slugs = tuple(re.findall(r"^\| ([a-z0-9-]+) \|", selector_table, flags=re.MULTILINE))
    filesystem_slugs = tuple(
        path.parent.name.removeprefix("arch-lens-")
        for path in sorted((pkg_root() / "skills_extended").glob("arch-lens-*/SKILL.md"))
    )
    actual_native_dispatch_matrix: dict[str, tuple[str, ...]] = {}

    for slug in _ARCHITECTURE_SELECTOR_SLUGS:
        info = _load_phase_d_skill(f"arch-lens-{slug}")
        actual_native_dispatch_matrix[slug] = tuple(
            vector.id for vector in info.exploration_vectors if vector.native_dispatch
        )

    assert (
        selector_slugs
        == filesystem_slugs
        == tuple(_ARCHITECTURE_LENS_INVENTORY)
        == tuple(actual_native_dispatch_matrix)
        == _ARCHITECTURE_SELECTOR_SLUGS
    )
    assert actual_native_dispatch_matrix == {
        slug: tuple(row[0] for row in rows) for slug, rows in _ARCHITECTURE_LENS_INVENTORY.items()
    }


@pytest.mark.parametrize("slug", _ARCHITECTURE_SELECTOR_SLUGS)
def test_architecture_lens_vectors_match_complete_reviewed_inventory(slug: str) -> None:
    expected = _ARCHITECTURE_LENS_INVENTORY[slug]
    info = _load_phase_d_skill(f"arch-lens-{slug}")

    assert [
        (
            vector.id,
            vector.disposition.value,
            vector.role,
            vector.task.task_id,
            vector.applicability.value,
        )
        for vector in info.exploration_vectors
    ] == list(expected)
    assert len({vector.id for vector in info.exploration_vectors}) == len(expected)
    assert all(vector.profile is RepositoryProfileId.AUTO for vector in info.exploration_vectors)
    assert all(vector.task.scope == (".",) for vector in info.exploration_vectors)
    assert all(vector.rationale.strip() for vector in info.exploration_vectors)
    assert all(vector.relationship_classes for vector in info.exploration_vectors)
    assert all(
        vector.disposition is ExplorationVectorDisposition.MIGRATED
        and vector.role in {"semantic-code-navigator", "repository-impact-profiler"}
        and vector.native_dispatch
        for vector in info.exploration_vectors
    )
    assert _review_digest(info) == _ARCHITECTURE_REVIEW_DIGESTS[slug]

    content = info.path.read_text(encoding="utf-8")
    for vector in info.exploration_vectors:
        assert content.count(vector.marker_line) == 1
        for pattern in _RAW_MIGRATED_AGENT_SYNTAX:
            assert pattern.search(vector.body) is None, (slug, vector.id, pattern.pattern)
    assert content.count("<!-- /autoskillit:exploration-vector -->") == len(expected)


def test_architecture_lens_task_inventory_is_unique_and_acyclic() -> None:
    graph: dict[str, set[str]] = {}
    vector_count = 0

    for slug in _ARCHITECTURE_SELECTOR_SLUGS:
        info = _load_phase_d_skill(f"arch-lens-{slug}")
        for vector in info.exploration_vectors:
            vector_count += 1
            assert vector.task.task_id not in graph
            graph[vector.task.task_id] = set(vector.task.depends_on)

    assert vector_count == len(graph) == 76
    assert set[str]().union(*graph.values()) <= set(graph)

    remaining = dict(graph)
    scheduled: list[str] = []
    while remaining:
        ready = tuple(task_id for task_id, dependencies in remaining.items() if not dependencies)
        assert ready, f"cycle in architecture exploration graph: {remaining}"
        scheduled.extend(ready)
        remaining = {
            task_id: dependencies.difference(ready)
            for task_id, dependencies in remaining.items()
            if task_id not in ready
        }

    assert len(scheduled) == vector_count


def test_phase_d_inventory_and_architecture_recipe_step_pins_are_explicit() -> None:
    assert set(_PHASE_D_INVENTORY) == {
        "investigate",
        "scope",
        "arch-lens-module-dependency",
        "arch-lens-state-lifecycle",
        "arch-lens-development",
    }
    assert sum(len(vectors) for vectors in _PHASE_D_INVENTORY.values()) == 60
    assert (
        sum(
            disposition == "migrated"
            for vectors in _PHASE_D_INVENTORY.values()
            for _, disposition, _, _, _ in vectors
        )
        == 39
    )
    assert (
        sum(
            disposition == "retained"
            for vectors in _PHASE_D_INVENTORY.values()
            for _, disposition, _, _, _ in vectors
        )
        == 21
    )
    assert _ARCHITECTURE_RECIPE_STEP_PINS == {
        "implementation.run_arch_lenses",
        "implementation-groups.run_arch_lenses",
        "remediation.run_arch_lenses",
    }
    assert {
        applicability
        for vectors in _PHASE_D_INVENTORY.values()
        for _, _, _, _, applicability in vectors
    } == {
        "always",
        "investigate-standard",
        "investigate-deep",
        "scope-software",
        "scope-non-software",
    }
    assert {
        disposition
        for vectors in _PHASE_D_INVENTORY.values()
        for _, disposition, _, _, _ in vectors
    } == {"migrated", "retained"}
