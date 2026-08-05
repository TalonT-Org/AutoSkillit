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
from autoskillit.core.io import load_yaml
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

_EXPERIMENT_LENS_SKILLS = (
    "exp-lens-estimand-clarity",
    "exp-lens-causal-assumptions",
    "exp-lens-comparator-construction",
    "exp-lens-pipeline-integrity",
    "exp-lens-variance-stability",
    "exp-lens-fair-comparison",
    "exp-lens-reproducibility-artifacts",
    "exp-lens-measurement-validity",
    "exp-lens-sensitivity-robustness",
    "exp-lens-benchmark-representativeness",
    "exp-lens-unit-interference",
    "exp-lens-error-budget",
    "exp-lens-severity-testing",
    "exp-lens-randomization-blocking",
    "exp-lens-validity-threats",
    "exp-lens-iterative-learning",
    "exp-lens-exploratory-confirmatory",
    "exp-lens-governance-risk",
)

_PREPARE_RESEARCH_PR_LENS_SUBSET = (
    "exp-lens-fair-comparison",
    "exp-lens-estimand-clarity",
    "exp-lens-causal-assumptions",
    "exp-lens-iterative-learning",
    "exp-lens-sensitivity-robustness",
    "exp-lens-exploratory-confirmatory",
    "exp-lens-validity-threats",
    "exp-lens-severity-testing",
)

_EXPERIMENT_RECIPE_STEP_PINS = {
    "research.run_experiment_lenses",
    "research-review.run_experiment_lenses",
}

ExperimentInventoryRow = tuple[str, str]


def _experiment_inventory(
    *step_one: ExperimentInventoryRow,
) -> tuple[ExperimentInventoryRow, ...]:
    return (("missing-context-fields", "repository-impact-profiler"), *step_one)


_EXPERIMENT_VECTOR_INVENTORY: dict[str, tuple[ExperimentInventoryRow, ...]] = {
    "exp-lens-estimand-clarity": _experiment_inventory(
        ("stated-claims-hypotheses", "repository-impact-profiler"),
        ("treatment-definition", "semantic-code-navigator"),
        ("outcome-definition", "semantic-code-navigator"),
        ("population-scope", "repository-impact-profiler"),
        ("complication-handling", "semantic-code-navigator"),
    ),
    "exp-lens-causal-assumptions": _experiment_inventory(
        ("treatment-outcome-definition", "semantic-code-navigator"),
        ("confounding-pathways", "repository-impact-profiler"),
        ("mediator-mechanism-variables", "semantic-code-navigator"),
        ("collider-selection-variables", "semantic-code-navigator"),
        ("randomization-assignment", "semantic-code-navigator"),
    ),
    "exp-lens-comparator-construction": _experiment_inventory(
        ("baseline-control-definitions", "repository-impact-profiler"),
        ("implementation-parity", "repository-impact-profiler"),
        ("version-environment-match", "repository-impact-profiler"),
        ("tuning-protocol-symmetry", "repository-impact-profiler"),
        ("temporal-baseline-drift", "repository-impact-profiler"),
    ),
    "exp-lens-pipeline-integrity": _experiment_inventory(
        ("data-loading-sources", "semantic-code-navigator"),
        ("preprocessing-transforms", "semantic-code-navigator"),
        ("split-logic", "semantic-code-navigator"),
        ("feature-engineering", "semantic-code-navigator"),
        ("model-training-evaluation", "semantic-code-navigator"),
    ),
    "exp-lens-variance-stability": _experiment_inventory(
        ("random-seed-management", "semantic-code-navigator"),
        ("nondeterminism-sources", "semantic-code-navigator"),
        ("multiple-run-protocol", "semantic-code-navigator"),
        ("variance-reporting", "repository-impact-profiler"),
        ("signal-to-noise-assessment", "repository-impact-profiler"),
    ),
    "exp-lens-fair-comparison": _experiment_inventory(
        ("compute-resource-allocation", "repository-impact-profiler"),
        ("tuning-protocol-per-method", "semantic-code-navigator"),
        ("data-access-preprocessing", "semantic-code-navigator"),
        ("engineering-effort-indicators", "semantic-code-navigator"),
        ("reporting-completeness", "repository-impact-profiler"),
    ),
    "exp-lens-reproducibility-artifacts": _experiment_inventory(
        ("environment-dependencies", "repository-impact-profiler"),
        ("data-provenance", "repository-impact-profiler"),
        ("execution-entry-points", "semantic-code-navigator"),
        ("random-seed-determinism", "semantic-code-navigator"),
        ("output-artifacts-logging", "repository-impact-profiler"),
    ),
    "exp-lens-measurement-validity": _experiment_inventory(
        ("metric-definitions", "semantic-code-navigator"),
        ("intended-interpretations", "repository-impact-profiler"),
        ("metric-computation-details", "semantic-code-navigator"),
        ("alternative-metrics-considered", "repository-impact-profiler"),
        ("construct-metric-gap", "repository-impact-profiler"),
    ),
    "exp-lens-sensitivity-robustness": _experiment_inventory(
        ("analytic-choices-made", "semantic-code-navigator"),
        ("ablation-coverage", "semantic-code-navigator"),
        ("preprocessing-variations", "semantic-code-navigator"),
        ("hyperparameter-sensitivity", "repository-impact-profiler"),
        ("distribution-environment-variations", "repository-impact-profiler"),
    ),
    "exp-lens-benchmark-representativeness": _experiment_inventory(
        ("benchmark-dataset-inventory", "repository-impact-profiler"),
        ("task-scenario-coverage", "repository-impact-profiler"),
        ("metric-coverage", "repository-impact-profiler"),
        ("claimed-generalization-scope", "repository-impact-profiler"),
        ("distribution-characteristics", "repository-impact-profiler"),
    ),
    "exp-lens-unit-interference": _experiment_inventory(
        ("unit-definition", "semantic-code-navigator"),
        ("cluster-group-structure", "semantic-code-navigator"),
        ("shared-resources", "repository-impact-profiler"),
        ("network-social-connections", "semantic-code-navigator"),
        ("treatment-assignment-boundary", "semantic-code-navigator"),
    ),
    "exp-lens-error-budget": _experiment_inventory(
        ("sample-size-power", "repository-impact-profiler"),
        ("multiple-comparisons", "semantic-code-navigator"),
        ("sequential-analysis", "semantic-code-navigator"),
        ("decision-thresholds", "repository-impact-profiler"),
        ("effect-size-context", "repository-impact-profiler"),
    ),
    "exp-lens-severity-testing": _experiment_inventory(
        ("positive-results-claimed", "repository-impact-profiler"),
        ("negative-controls-sanity-checks", "repository-impact-profiler"),
        ("adversarial-conditions", "repository-impact-profiler"),
        ("alternative-explanations-tested", "repository-impact-profiler"),
        ("prediction-specificity", "repository-impact-profiler"),
    ),
    "exp-lens-randomization-blocking": _experiment_inventory(
        ("assignment-mechanism", "semantic-code-navigator"),
        ("blocking-stratification", "semantic-code-navigator"),
        ("replication-structure", "repository-impact-profiler"),
        ("order-timing-effects", "semantic-code-navigator"),
        ("exclusion-attrition", "semantic-code-navigator"),
    ),
    "exp-lens-validity-threats": _experiment_inventory(
        ("temporal-changes-history", "repository-impact-profiler"),
        ("instrumentation-changes", "repository-impact-profiler"),
        ("selection-filtering", "semantic-code-navigator"),
        ("co-interventions", "repository-impact-profiler"),
        ("treatment-diffusion", "semantic-code-navigator"),
    ),
    "exp-lens-iterative-learning": _experiment_inventory(
        ("factor-space", "repository-impact-profiler"),
        ("interaction-structure", "repository-impact-profiler"),
        ("cost-resource-model", "repository-impact-profiler"),
        ("sequential-decision-logic", "semantic-code-navigator"),
        ("learning-objectives", "repository-impact-profiler"),
    ),
    "exp-lens-exploratory-confirmatory": _experiment_inventory(
        ("pre-specified-plans", "repository-impact-profiler"),
        ("analytic-flexibility", "semantic-code-navigator"),
        ("selective-reporting-signals", "repository-impact-profiler"),
        ("post-hoc-rationalization", "repository-impact-profiler"),
        ("exploration-confirmation-separation", "repository-impact-profiler"),
    ),
    "exp-lens-governance-risk": _experiment_inventory(
        ("intended-use-deployment-context", "repository-impact-profiler"),
        ("subgroup-fairness-analysis", "repository-impact-profiler"),
        ("harm-risk-metrics", "semantic-code-navigator"),
        ("monitoring-feedback-plans", "semantic-code-navigator"),
        ("limitation-disclosure", "repository-impact-profiler"),
    ),
}

_EXPERIMENT_REVIEW_DIGESTS = {
    "exp-lens-estimand-clarity": (
        "adea2c13317bd089d66bde08d23999afd92a15a3a63e5fc3de2f7fef7ef408fd"
    ),
    "exp-lens-causal-assumptions": (
        "0ffa8a302a004551e3f3d1047032b0884ffd338957b66e4e0cff02c89f009cbc"
    ),
    "exp-lens-comparator-construction": (
        "d7a2491f6f289957419cc43017c467c0ee6d69038f4f0c4792aaded536e62e06"
    ),
    "exp-lens-pipeline-integrity": (
        "0da452196ade9066331166675e9ee13c684e508dfd8a52ef078e01a534daa6d2"
    ),
    "exp-lens-variance-stability": (
        "53f5cc17a866441d9c32a885af036c352b2609730ab7446e0522c14fc6d3caf1"
    ),
    "exp-lens-fair-comparison": (
        "66885994e8a08d1fbede4982b72bf05abab31c88229e7c3aeac2710dd8b3ce86"
    ),
    "exp-lens-reproducibility-artifacts": (
        "fa4604b518ad6614bb0e184aa72c340913e4d6579ece0f71f47c518c8bf05666"
    ),
    "exp-lens-measurement-validity": (
        "ce31d75424c90b7b33e7f079a10cbab196494f402329abc4fa32e0da34760c5c"
    ),
    "exp-lens-sensitivity-robustness": (
        "3a39f90bac84fe19fd44dfadde19a0235ee1e90f2f07aa094d88ab820f26fe2d"
    ),
    "exp-lens-benchmark-representativeness": (
        "4846390e2073a2bb0c467c501f6421040f1b33a30d3977b43c396f3a73b35af6"
    ),
    "exp-lens-unit-interference": (
        "2d4aa7606833bb73185c25a96edf7a0fc3d5658868f1cb50ea21a30f0953e5f6"
    ),
    "exp-lens-error-budget": ("60cd3e77ee5f2f1195dd7a52b08f8cc73a691f47864d9675ada0dae31a52e7ee"),
    "exp-lens-severity-testing": (
        "10ca2b28a3855d694b5b639a85f1889c80c8f2bff0cd9864245422602409128c"
    ),
    "exp-lens-randomization-blocking": (
        "dc404626d7852d7eebecac31173aaf6778dbbc118649c5154e3ea17b3bdb610b"
    ),
    "exp-lens-validity-threats": (
        "0f875fa4a12f07161536d8551c1aaac080aff0cab1d36259b767baef74b5a2fa"
    ),
    "exp-lens-iterative-learning": (
        "174878db7f640ae29590f177d433f17d55b1b8f53161ada51f67bdff90a93034"
    ),
    "exp-lens-exploratory-confirmatory": (
        "56aebdd702018d3c19f7edf8379a05d9d4ff7b781f64c5254d0f236a43c0dfa2"
    ),
    "exp-lens-governance-risk": (
        "9fe55c185e3bb7dfd2c81f0061e4160081a8886e529c67aaba07c63afe553a3e"
    ),
}

_MISSING_CONTEXT_FIELDS_BODY_DIGESTS = {
    "39ecf9415491404a6c1288da80979096fa452d04bd6df835b048ecef6f78620d",
    "5fd56558f7f721791a43d5825ff77ce73960eda3ba27332fc39ae83466a138f7",
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


def test_experiment_lens_bundled_alias_and_prepare_research_families_are_exact() -> None:
    defaults = load_yaml(pkg_root() / "config" / "defaults.yaml")
    bundled = tuple(name for name in defaults["skills"]["tier2"] if name.startswith("exp-lens-"))
    make_experiment = (
        pkg_root() / "skills_extended" / "make-experiment-diag" / "SKILL.md"
    ).read_text(encoding="utf-8")
    make_frontmatter = load_yaml(make_experiment.split("---", 2)[1])
    siblings = tuple(
        item["name"]
        for item in make_frontmatter["semantic_requirements"]["sibling_skills"]
        if item["name"].startswith("exp-lens-")
    )
    alias_table = make_experiment.split("## Alias Table", 1)[1].split("## Related Skills", 1)[0]
    aliases = tuple(re.findall(r"^\| [a-z-]+ \| (exp-lens-[a-z-]+) \|", alias_table, re.MULTILINE))
    filesystem = {
        path.parent.name for path in (pkg_root() / "skills_extended").glob("exp-lens-*/SKILL.md")
    }

    prepare_research = (
        pkg_root() / "skills_extended" / "prepare-research-pr" / "SKILL.md"
    ).read_text(encoding="utf-8")
    selection_table = prepare_research.split("## Lens Selection Table", 1)[1].split(
        "## Experiment Status Badges", 1
    )[0]
    prepare_outputs = tuple(dict.fromkeys(re.findall(r"exp-lens-[a-z-]+", selection_table)))

    assert bundled == aliases == _EXPERIMENT_LENS_SKILLS
    assert tuple(_EXPERIMENT_VECTOR_INVENTORY) == _EXPERIMENT_LENS_SKILLS
    assert siblings == tuple(sorted(_EXPERIMENT_LENS_SKILLS))
    assert filesystem == set(_EXPERIMENT_LENS_SKILLS)
    assert prepare_outputs == _PREPARE_RESEARCH_PR_LENS_SUBSET
    assert set(prepare_outputs) < set(_EXPERIMENT_LENS_SKILLS)
    assert _EXPERIMENT_RECIPE_STEP_PINS == {
        "research.run_experiment_lenses",
        "research-review.run_experiment_lenses",
    }

    manifest = load_yaml(pkg_root().parents[1] / ".autoskillit" / "test-filter-manifest.yaml")
    skill_targets = set(manifest["src/autoskillit/skills_extended/*/SKILL.md"])
    assert {"skills_extended/", "execution/", "skills/"} <= skill_targets


@pytest.mark.parametrize("skill_name", _EXPERIMENT_LENS_SKILLS)
def test_phase_f_experiment_vectors_match_complete_reviewed_inventory(
    skill_name: str,
) -> None:
    info = _load_phase_d_skill(skill_name)
    expected = _EXPERIMENT_VECTOR_INVENTORY[skill_name]

    assert tuple((vector.id, vector.role) for vector in info.exploration_vectors) == expected
    assert tuple(
        vector.id for vector in info.exploration_vectors if vector.native_dispatch
    ) == tuple(vector_id for vector_id, _ in expected)
    assert len({vector.id for vector in info.exploration_vectors}) == len(expected) == 6
    assert all(
        vector.disposition is ExplorationVectorDisposition.MIGRATED
        and vector.applicability is ExplorationVectorApplicabilityId.ALWAYS
        and vector.profile is RepositoryProfileId.AUTO
        and vector.task.profile is RepositoryProfileId.AUTO
        and vector.task.task_id == f"{skill_name}-{vector.id}"
        and vector.task.frontier_item_id == f"{skill_name}-{vector.id}-frontier"
        and vector.task.depends_on == ()
        and vector.task.scope == (".",)
        and vector.native_dispatch
        and vector.rationale.strip()
        and vector.relationship_classes
        for vector in info.exploration_vectors
    )
    assert _review_digest(info) == _EXPERIMENT_REVIEW_DIGESTS[skill_name]

    missing_fields = info.exploration_vectors[0]
    assert missing_fields.id == "missing-context-fields"
    assert missing_fields.role == "repository-impact-profiler"
    assert hashlib.sha256(missing_fields.body.encode()).hexdigest() in (
        _MISSING_CONTEXT_FIELDS_BODY_DIGESTS
    )
    normalized_missing_body = missing_fields.body.lower().replace("-", " ")
    for invariant in (
        "absent",
        "never rediscover",
        "override",
        "not applicable",
        "search",
        "unavailable",
        "unrelated",
        "without widening scope",
        "inferring meaning",
        "importing or executing target code",
        "tests",
        "experiments",
    ):
        assert invariant in normalized_missing_body, (skill_name, invariant)

    content = info.path.read_text(encoding="utf-8")
    for vector in info.exploration_vectors:
        assert content.count(vector.marker_line) == 1
        for pattern in _RAW_MIGRATED_AGENT_SYNTAX:
            assert pattern.search(vector.body) is None, (
                skill_name,
                vector.id,
                pattern.pattern,
            )
    assert content.count("<!-- /autoskillit:exploration-vector -->") == len(expected)


def test_phase_f_experiment_inventory_is_complete_unique_and_acyclic() -> None:
    graph: dict[str, set[str]] = {}
    step_zero_count = 0
    step_one_count = 0

    for skill_name in _EXPERIMENT_LENS_SKILLS:
        info = _load_phase_d_skill(skill_name)
        for vector in info.exploration_vectors:
            assert vector.task.task_id not in graph
            graph[vector.task.task_id] = set(vector.task.depends_on)
            if vector.id == "missing-context-fields":
                step_zero_count += 1
            else:
                step_one_count += 1

    assert step_zero_count == 18
    assert step_one_count == 90
    assert len(graph) == 108
    assert set[str]().union(*graph.values()) <= set(graph)

    remaining = dict(graph)
    scheduled: list[str] = []
    while remaining:
        ready = tuple(task_id for task_id, dependencies in remaining.items() if not dependencies)
        assert ready, f"cycle in experiment exploration graph: {remaining}"
        scheduled.extend(ready)
        remaining = {
            task_id: dependencies.difference(ready)
            for task_id, dependencies in remaining.items()
            if task_id not in ready
        }

    assert len(scheduled) == 108


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
