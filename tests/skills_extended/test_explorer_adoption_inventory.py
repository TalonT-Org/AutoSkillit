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
            "planner-extract-domain-domain-vocabulary",
        ),
        (
            "existing-abstractions",
            "migrated",
            "semantic-code-navigator",
            "planner-extract-domain-existing-abstractions",
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
            "planner-extract-domain-cross-cutting-concerns",
        ),
        (
            "data-flow-patterns",
            "migrated",
            "semantic-code-navigator",
            "planner-extract-domain-data-flow-patterns",
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
            "planner-elaborate-phase-dependency-analysis",
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
            "planner-elaborate-phase-pattern-discovery",
        ),
        (
            "cross-phase-boundaries",
            "migrated",
            "semantic-code-navigator",
            "planner-elaborate-phase-cross-phase-boundaries",
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
            "always",
        ),
        (
            "standard-dependencies",
            "migrated",
            "semantic-code-navigator",
            "investigate-standard-dependencies",
            "always",
        ),
        (
            "standard-consumer-impact",
            "migrated",
            "repository-impact-profiler",
            "investigate-standard-consumer-impact",
            "always",
        ),
        (
            "standard-test-coverage",
            "migrated",
            "repository-impact-profiler",
            "investigate-standard-test-coverage",
            "always",
        ),
        (
            "standard-error-provenance",
            "migrated",
            "semantic-code-navigator",
            "investigate-standard-error-provenance",
            "always",
        ),
        (
            "standard-similar-patterns",
            "migrated",
            "semantic-code-navigator",
            "investigate-standard-similar-patterns",
            "always",
        ),
        (
            "standard-architecture-constraints",
            "migrated",
            "repository-impact-profiler",
            "investigate-standard-architecture-constraints",
            "always",
        ),
        (
            "standard-design-intent-history",
            "migrated",
            "repository-impact-profiler",
            "investigate-standard-design-intent-history",
            "always",
        ),
        (
            "standard-recurrence-history",
            "migrated",
            "repository-impact-profiler",
            "investigate-standard-recurrence-history",
            "always",
        ),
        (
            "deep-code-paths",
            "migrated",
            "semantic-code-navigator",
            "investigate-deep-code-paths",
            "always",
        ),
        (
            "deep-log-history",
            "migrated",
            "repository-impact-profiler",
            "investigate-deep-log-history",
            "always",
        ),
        (
            "deep-dependencies",
            "migrated",
            "semantic-code-navigator",
            "investigate-deep-dependencies",
            "always",
        ),
        (
            "deep-related-components",
            "migrated",
            "repository-impact-profiler",
            "investigate-deep-related-components",
            "always",
        ),
        (
            "deep-code-deepening",
            "migrated",
            "semantic-code-navigator",
            "investigate-deep-code-deepening",
            "always",
        ),
        (
            "deep-candidate-blast-radius",
            "migrated",
            "repository-impact-profiler",
            "investigate-deep-candidate-blast-radius",
            "always",
        ),
        (
            "standard-web-research",
            "retained",
            None,
            "investigate-standard-web-research",
            "always",
        ),
        (
            "standard-design-intent-reasoning",
            "retained",
            None,
            "investigate-standard-design-intent-reasoning",
            "always",
        ),
        (
            "standard-recurrence-reasoning",
            "retained",
            None,
            "investigate-standard-recurrence-reasoning",
            "always",
        ),
        (
            "deep-web-research",
            "retained",
            None,
            "investigate-deep-web-research",
            "always",
        ),
        (
            "deep-design-intent-reasoning",
            "retained",
            None,
            "investigate-deep-design-intent-reasoning",
            "always",
        ),
        (
            "deep-recurrence-reasoning",
            "retained",
            None,
            "investigate-deep-recurrence-reasoning",
            "always",
        ),
        (
            "deep-informed-web-research",
            "retained",
            None,
            "investigate-deep-informed-web-research",
            "always",
        ),
        (
            "deep-design-intent-refresh",
            "retained",
            None,
            "investigate-deep-design-intent-refresh",
            "always",
        ),
        (
            "deep-hypothesis-challenge",
            "retained",
            None,
            "investigate-deep-hypothesis-challenge",
            "always",
        ),
        (
            "deep-solution-generation",
            "retained",
            None,
            "investigate-deep-solution-generation",
            "always",
        ),
        (
            "deep-breakage-reasoning",
            "retained",
            None,
            "investigate-deep-breakage-reasoning",
            "always",
        ),
        (
            "deep-factual-validation",
            "retained",
            None,
            "investigate-deep-factual-validation",
            "always",
        ),
        (
            "deep-recommendation-validation",
            "retained",
            None,
            "investigate-deep-recommendation-validation",
            "always",
        ),
        (
            "deep-gap-validation",
            "retained",
            None,
            "investigate-deep-gap-validation",
            "always",
        ),
    ),
    "scope": (
        (
            "prior-art-codebase",
            "retained",
            None,
            "scope-prior-art-codebase",
            "always",
        ),
        (
            "prior-art-literature",
            "retained",
            None,
            "scope-prior-art-literature",
            "always",
        ),
        ("external-research", "retained", None, "scope-external-research", "always"),
        (
            "domain-context-architecture",
            "retained",
            None,
            "scope-domain-context-architecture",
            "always",
        ),
        (
            "domain-context-domain-knowledge",
            "retained",
            None,
            "scope-domain-context-domain-knowledge",
            "always",
        ),
        (
            "evaluation-framework-software",
            "retained",
            None,
            "scope-evaluation-framework-software",
            "always",
        ),
        (
            "evaluation-framework-domain-assessment",
            "retained",
            None,
            "scope-evaluation-framework-domain-assessment",
            "always",
        ),
        (
            "computational-complexity-local",
            "retained",
            None,
            "scope-computational-complexity-local",
            "always",
        ),
        (
            "computational-complexity-external",
            "retained",
            None,
            "scope-computational-complexity-external",
            "always",
        ),
        (
            "data-availability-repository",
            "retained",
            None,
            "scope-data-availability-repository",
            "always",
        ),
        (
            "data-availability-external",
            "retained",
            None,
            "scope-data-availability-external",
            "always",
        ),
        ("custom-research", "retained", None, "scope-custom-research", "always"),
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
    "c4-container": "4c7887d31e4985ad477addf1bca29090f37f3112e138c60e555953406db1550e",
    "concurrency": "d658d1a536229e9ed1989344e281eeb6ad14f6b725d5bbdf08cc59e8f825146b",
    "data-lineage": "bb5ef95363c401b18e5ee363646ac980a13989846fdf462162f2ba73b5578f00",
    "deployment": "8289193279a3c93bf1c9fe5eaf85bfc63187eb087aebded20a666b14152ca3eb",
    "development": "4000d322e44c3fff6c881e95746a7cfe45868da09fa5f70d70f38a8b9af438c6",
    "error-resilience": "299889750ff1479e90584a514721b4531c60a71563dd56fa36a26a7a853a414b",
    "module-dependency": "05567682369b69529f851ce30a065db5eedb3129fd4a1c275804af520340e134",
    "operational": "e786d263fd36dc6eb50d6714af4374ea5316decaf9afa0b3f9095d7b3f9c7ca4",
    "process-flow": "b0499eb27c252fbf375e8c492663340ade97f17c612eeb8c3d300274a913cb41",
    "repository-access": "8368535ccb2f3fd487bf9dcde31c1d4d2624d6f0dac523410abab2c99cc26497",
    "scenarios": "65104e406718a7f5a69b35e0b1803bd98c984feec0829f9f1f4066e5319248e8",
    "security": "ec63aaa15a281515113f45df23990816d4a71fea8a945798a9f7cd3b0b351f23",
    "state-lifecycle": "710fe5d3b5b2db56f9224e48b8138900fc279b4ba17330d49df4156661127f50",
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

_VISUALIZATION_LENS_SKILLS = (
    "vis-lens-always-on",
    "vis-lens-antipattern",
    "vis-lens-caption-annot",
    "vis-lens-chart-select",
    "vis-lens-color-access",
    "vis-lens-figure-table",
    "vis-lens-methodology-norms",
    "vis-lens-multi-compare",
    "vis-lens-reproducibility",
    "vis-lens-story-arc",
    "vis-lens-temporal",
    "vis-lens-uncertainty",
)

_REACHABLE_VISUALIZATION_LENSES = (
    "vis-lens-always-on",
    "vis-lens-temporal",
    "vis-lens-multi-compare",
    "vis-lens-chart-select",
    "vis-lens-uncertainty",
    "vis-lens-figure-table",
    "vis-lens-methodology-norms",
)

_UNREACHABLE_VISUALIZATION_LENSES = (
    "vis-lens-antipattern",
    "vis-lens-caption-annot",
    "vis-lens-color-access",
    "vis-lens-reproducibility",
    "vis-lens-story-arc",
)

_VISUALIZATION_RECIPE_STEP_PINS = {
    "research.vis_apply",
    "research-design.vis_apply",
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
        "557cc95b34a0a5c0b7f097cdfd95aa997463f978672c6ceb77099dce926bd9c7"
    ),
    "exp-lens-causal-assumptions": (
        "0a80c86cfced5a1a498e1c52ff5d9d7f34fc1f0a7da80b6149e2e8cd82190383"
    ),
    "exp-lens-comparator-construction": (
        "5e020f47bfb5667ecf01b1cf884688c00c31ec12e39a6eee6457fe004bf4bf37"
    ),
    "exp-lens-pipeline-integrity": (
        "a60b756fdfe12f2d1fa56142a9171ec9c878ac8346560824b9ede6036f95709e"
    ),
    "exp-lens-variance-stability": (
        "b17eeb0e5ce6760e7992ac95b1dc34252fda56e0bff8df464f92117bf1fd4621"
    ),
    "exp-lens-fair-comparison": (
        "dd363783aaaaa01f1153e49317b744ea945d631a9646e821183a2b0fe84271dc"
    ),
    "exp-lens-reproducibility-artifacts": (
        "c2fd7d6cbce206a9849113c652717048aafcb9d1f4d6cdd66819a8f3ade09cca"
    ),
    "exp-lens-measurement-validity": (
        "36ae3fb5c87a9e59f8adccbe3ba09db5096f05dd95e48ac18c3e0910154d7320"
    ),
    "exp-lens-sensitivity-robustness": (
        "e67bb315fe21ebcce96957671a7c4d03e16a54dded09eaf311315902f01a9391"
    ),
    "exp-lens-benchmark-representativeness": (
        "cad7643ae5347ac78758419a1938e076ec11dd8a7bc5d99ec0272a455f576bf9"
    ),
    "exp-lens-unit-interference": (
        "550cc401fc95c628b4696b42ecbcb651e64015dce7ceb39714bd01cb99ca4575"
    ),
    "exp-lens-error-budget": ("cc890a9d1aa9bc42db7078777cac30ee383436aa1779ec7ae63f164372cd1347"),
    "exp-lens-severity-testing": (
        "a5d01f4d32f1b802154ae547b903ba4b119e44110532c5c93263e25a98fc288a"
    ),
    "exp-lens-randomization-blocking": (
        "3c0085eed60e00d244cc94c9d765052347c04733ecb7becb85f7d5513338d62d"
    ),
    "exp-lens-validity-threats": (
        "b27165e3d909f5ae3e6d35291a5c55a5ad8e65a2a7bf7bb7d7199d49a349fcd0"
    ),
    "exp-lens-iterative-learning": (
        "05279f937ccc38d6d93ad76f01c72d2e8bd3c898b1aad1c367b3c78fcf25e9be"
    ),
    "exp-lens-exploratory-confirmatory": (
        "78399ce7436727b0eb4163e2347e0e452c278fab94039ba520a802e537a941ab"
    ),
    "exp-lens-governance-risk": (
        "eaac60091afedce9402f8c63a2b1706b5207528a592595898dffbecfcd4a64ef"
    ),
}

_MISSING_CONTEXT_FIELDS_BODY_DIGESTS = {
    "39ecf9415491404a6c1288da80979096fa452d04bd6df835b048ecef6f78620d",
    "5fd56558f7f721791a43d5825ff77ce73960eda3ba27332fc39ae83466a138f7",
}

VisualizationInventoryRow = tuple[str, str, str | None]

_VISUALIZATION_VECTOR_INVENTORY: dict[str, tuple[VisualizationInventoryRow, ...]] = {
    "vis-lens-always-on": (
        ("missing-context-fields", "migrated", "repository-impact-profiler"),
        ("figure-inventory", "migrated", "repository-impact-profiler"),
        ("caller-context", "retained", None),
    ),
    "vis-lens-antipattern": (
        ("missing-context-fields", "migrated", "repository-impact-profiler"),
        ("chart-visualization-clues", "migrated", "semantic-code-navigator"),
        ("caller-context", "retained", None),
    ),
    "vis-lens-caption-annot": (
        ("missing-context-fields", "migrated", "repository-impact-profiler"),
        ("figure-title-inventory", "migrated", "repository-impact-profiler"),
        ("axis-label-unit-evidence", "migrated", "repository-impact-profiler"),
        ("uncertainty-definition-evidence", "migrated", "repository-impact-profiler"),
        ("baseline-sample-disclosure", "migrated", "repository-impact-profiler"),
        ("caller-context", "retained", None),
    ),
    "vis-lens-chart-select": (
        ("missing-context-fields", "migrated", "repository-impact-profiler"),
        ("existing-figure-inventory", "migrated", "repository-impact-profiler"),
        ("data-types-variables", "migrated", "semantic-code-navigator"),
        ("current-chart-choices", "migrated", "semantic-code-navigator"),
        ("encoding-channel-usage", "migrated", "semantic-code-navigator"),
        ("caller-context", "retained", None),
    ),
    "vis-lens-color-access": (
        ("missing-context-fields", "migrated", "repository-impact-profiler"),
        ("palette-colormap-usage", "migrated", "semantic-code-navigator"),
        ("hue-encoding-usage", "migrated", "semantic-code-navigator"),
        ("series-definitions", "migrated", "semantic-code-navigator"),
        ("redundant-encoding-declarations", "migrated", "semantic-code-navigator"),
        ("text-size-parameters", "migrated", "semantic-code-navigator"),
    ),
    "vis-lens-figure-table": (
        ("missing-context-fields", "migrated", "repository-impact-profiler"),
        ("result-slot-inventory", "retained", None),
    ),
    "vis-lens-methodology-norms": (
        ("missing-context-fields", "migrated", "repository-impact-profiler"),
        ("generated-figure-artifacts", "migrated", "repository-impact-profiler"),
        ("figure-generating-code", "migrated", "semantic-code-navigator"),
        ("planned-figure-artifacts", "migrated", "repository-impact-profiler"),
        ("methodology-tradition-resolution", "retained", None),
        ("mandatory-figure-coverage", "retained", None),
    ),
    "vis-lens-multi-compare": (
        ("missing-context-fields", "migrated", "repository-impact-profiler"),
        ("condition-factor-definitions", "migrated", "semantic-code-navigator"),
        ("factorial-structure", "migrated", "semantic-code-navigator"),
        ("series-overlap-assessment", "retained", None),
    ),
    "vis-lens-reproducibility": (
        ("missing-context-fields", "migrated", "repository-impact-profiler"),
        ("data-availability-inventory", "migrated", "repository-impact-profiler"),
        ("preprocessing-parameter-audit", "migrated", "semantic-code-navigator"),
        ("library-version-audit", "migrated", "semantic-code-navigator"),
        ("random-seed-audit", "migrated", "semantic-code-navigator"),
        ("per-figure-code-reference", "migrated", "repository-impact-profiler"),
    ),
    "vis-lens-story-arc": (
        ("missing-context-fields", "migrated", "repository-impact-profiler"),
        ("global-color-map", "migrated", "repository-impact-profiler"),
        ("enumerate-number-figures", "retained", None),
        ("detect-redundant-figures", "retained", None),
        ("map-narrative-dependencies", "retained", None),
    ),
    "vis-lens-temporal": (
        ("missing-context-fields", "migrated", "repository-impact-profiler"),
        ("learning-loss-curves", "migrated", "repository-impact-profiler"),
        ("seed-count", "migrated", "semantic-code-navigator"),
        ("smoothing-calls", "migrated", "semantic-code-navigator"),
        ("x-axis-type", "migrated", "semantic-code-navigator"),
        ("early-stopping", "migrated", "semantic-code-navigator"),
    ),
    "vis-lens-uncertainty": (
        ("missing-context-fields", "migrated", "repository-impact-profiler"),
        ("figures-error-bearing-quantities", "migrated", "repository-impact-profiler"),
        ("seed-count", "migrated", "semantic-code-navigator"),
        (
            "existing-uncertainty-representation",
            "migrated",
            "semantic-code-navigator",
        ),
        ("claims-about-variance", "migrated", "repository-impact-profiler"),
    ),
}

_VISUALIZATION_REVIEW_DIGESTS = {
    "vis-lens-always-on": "b6c4fe3ad42b0017b167dcd47be7fd21355e6354e85584b8614f8f802e7acb57",
    "vis-lens-antipattern": "79bbe3d69e46f54bca6314d83dc7e7452c66e81c8572eeb44394ecaf5860555d",
    "vis-lens-caption-annot": "b10efa30f17d908295f1817877759ec00f7df062d5d04fcf42e1b5eedc37dbd9",
    "vis-lens-chart-select": "3d4bfc9235b0bbab56cc3af617fd2674dd371b6bfc328d5059917113cc5e9511",
    "vis-lens-color-access": "ddf1f0614dacd2115a8b3d22c79a24a7d9fb941dff356681082c62adbff5102e",
    "vis-lens-figure-table": "0323ca2d21fdc49a39c7ed4fe48d5513f897e21e104967ae792b7d83f936c810",
    "vis-lens-methodology-norms": (
        "d8618744ce8649542be534a40d7698b306dcb3dc1807083f4dd327d07e31061c"
    ),
    "vis-lens-multi-compare": "feceb6d0724c90de00b5112a5f5bac6cf3b7e54598c8fb667822f53effc5434a",
    "vis-lens-reproducibility": (
        "d8ba9ffb50b2e315270f8e2cca9ab6323d4ef58f94231997be66eddd0730a829"
    ),
    "vis-lens-story-arc": "5c9e5832916bdc368fcc8faf2b8327ee4ad2cf02023480c34e4452c61035d466",
    "vis-lens-temporal": "6de30267108dd985f46a10c52e8ba7acaca4f9f9f160775ca8574d246c0c1704",
    "vis-lens-uncertainty": "e8c3108dbb5d0ccefa3de5f5b1fd0b3830bc76de391f7d6b53594f25da61738a",
}

_VISUALIZATION_RETAINED_BODY_AUTHORITIES = {
    ("vis-lens-always-on", "caller-context"): ("positional arg 1", "structured context"),
    ("vis-lens-antipattern", "caller-context"): ("positional arg 1", "structured context"),
    ("vis-lens-caption-annot", "caller-context"): ("positional arg 1", "structured context"),
    ("vis-lens-chart-select", "caller-context"): ("positional arg 1", "structured context"),
    ("vis-lens-figure-table", "result-slot-inventory"): (
        "classify it as one of",
        "exact-value query",
    ),
    ("vis-lens-methodology-norms", "methodology-tradition-resolution"): (
        "tradition_slug",
        "out-of-scope tradition",
    ),
    ("vis-lens-methodology-norms", "mandatory-figure-coverage"): (
        "mandatory types",
        "present / partial / absent",
    ),
    ("vis-lens-multi-compare", "series-overlap-assessment"): (
        "assess whether",
        "overlapping confidence bands",
    ),
    ("vis-lens-story-arc", "enumerate-number-figures"): (
        "supplied or pre-existing figure plan",
        "document order",
    ),
    ("vis-lens-story-arc", "detect-redundant-figures"): (
        "parent flags",
        "data and conclusion",
    ),
    ("vis-lens-story-arc", "map-narrative-dependencies"): (
        "parent",
        "ordering judgment",
    ),
}

_PHASE_D_REVIEW_DIGESTS = {
    "investigate": "2042be22a51bf8ac8cc9e95b3dd3db6efe77d06a93957027da983effb2af9eea",
    "scope": "d176850d2fe3fa933c4416fe9ce2c1dc747b0380ad4a91d8dd90029f24f90585",
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

_MISSING_CONTEXT_INVARIANTS = (
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
)
_EXPERIMENT_MISSING_CONTEXT_INVARIANTS = (
    *_MISSING_CONTEXT_INVARIANTS,
    "tests",
    "experiments",
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
            vector.disposition is ExplorationVectorDisposition.MIGRATED,
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


def _assert_lens_vector_contracts(skill_name: str, expected_count: int) -> SkillInfo:
    info = _load_phase_d_skill(skill_name)
    content = info.path.read_text(encoding="utf-8")

    for vector in info.exploration_vectors:
        assert content.count(vector.marker_line) == 1
        if vector.disposition is ExplorationVectorDisposition.MIGRATED:
            for pattern in _RAW_MIGRATED_AGENT_SYNTAX:
                assert pattern.search(vector.body) is None, (
                    skill_name,
                    vector.id,
                    pattern.pattern,
                )
    assert content.count("<!-- /autoskillit:exploration-vector -->") == expected_count
    return info


def _assert_acyclic_task_graph(
    graph: Mapping[str, set[str]],
    expected_count: int,
    family: str,
) -> None:
    assert set[str]().union(*graph.values()) <= set(graph), (
        f"unknown dependency in {family} exploration graph"
    )

    remaining = {task_id: set(dependencies) for task_id, dependencies in graph.items()}
    scheduled: list[str] = []
    while remaining:
        ready = tuple(task_id for task_id, dependencies in remaining.items() if not dependencies)
        assert ready, f"cycle in {family} exploration graph: {remaining}"
        scheduled.extend(ready)
        remaining = {
            task_id: dependencies.difference(ready)
            for task_id, dependencies in remaining.items()
            if task_id not in ready
        }

    assert len(scheduled) == expected_count, family


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
    if skill_name == "planner-extract-domain":
        assert "Use the registered exploration roles for all repository reads" in content


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
        vector.disposition is ExplorationVectorDisposition.MIGRATED for vector in conditional
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
            and vector.role in {"semantic-code-navigator", "repository-impact-profiler"}
        )
        if vector.disposition is ExplorationVectorDisposition.MIGRATED
        else vector.role is None
        for vector in info.exploration_vectors
    )

    content = skill_path.read_text(encoding="utf-8")
    if skill_name == "investigate":
        assert (
            "Spawn all retained subagents through child delegation under the declared "
            "`sonnet` model-class policy"
        ) in content
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

    assert migrated_count == 34


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
            vector.id
            for vector in info.exploration_vectors
            if vector.disposition is ExplorationVectorDisposition.MIGRATED
        )

    assert selector_slugs == _ARCHITECTURE_SELECTOR_SLUGS, (
        "selector table differs from selector fixture"
    )
    assert filesystem_slugs == _ARCHITECTURE_SELECTOR_SLUGS, (
        "filesystem inventory differs from selector fixture"
    )
    assert tuple(_ARCHITECTURE_LENS_INVENTORY) == _ARCHITECTURE_SELECTOR_SLUGS, (
        "reviewed inventory differs from selector fixture"
    )
    assert tuple(actual_native_dispatch_matrix) == _ARCHITECTURE_SELECTOR_SLUGS, (
        "native dispatch matrix differs from selector fixture"
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
    expected = _EXPERIMENT_VECTOR_INVENTORY[skill_name]
    info = _assert_lens_vector_contracts(skill_name, len(expected))

    assert tuple((vector.id, vector.role) for vector in info.exploration_vectors) == expected
    assert tuple(
        vector.id
        for vector in info.exploration_vectors
        if vector.disposition is ExplorationVectorDisposition.MIGRATED
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
    for invariant in _EXPERIMENT_MISSING_CONTEXT_INVARIANTS:
        assert invariant in normalized_missing_body, (skill_name, invariant)

    content = info.path.read_text(encoding="utf-8")
    assert "Retain parent authority over" in content
    if skill_name in {
        "exp-lens-fair-comparison",
        "exp-lens-governance-risk",
        "exp-lens-iterative-learning",
        "exp-lens-measurement-validity",
        "exp-lens-pipeline-integrity",
        "exp-lens-randomization-blocking",
        "exp-lens-reproducibility-artifacts",
        "exp-lens-sensitivity-robustness",
        "exp-lens-severity-testing",
        "exp-lens-unit-interference",
        "exp-lens-validity-threats",
        "exp-lens-variance-stability",
    }:
        assert re.search(
            r"Import or execute target code|must not execute the target",
            content,
        )


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
    _assert_acyclic_task_graph(graph, 108, "experiment")


def test_visualization_family_reachability_pins_and_filter_coverage_are_exact() -> None:
    defaults = load_yaml(pkg_root() / "config" / "defaults.yaml")
    bundled = tuple(name for name in defaults["skills"]["tier2"] if name.startswith("vis-lens-"))
    filesystem = tuple(
        path.parent.name
        for path in sorted((pkg_root() / "skills_extended").glob("vis-lens-*/SKILL.md"))
    )
    recipe_overrides = defaults["agent_backend"]["recipe_overrides"]
    actual_pins = {
        f"{recipe_name}.{step_name}": backend
        for recipe_name, steps in recipe_overrides.items()
        for step_name, backend in steps.items()
        if step_name == "vis_apply"
    }

    assert filesystem == tuple(_VISUALIZATION_VECTOR_INVENTORY)
    assert filesystem == _VISUALIZATION_LENS_SKILLS
    assert len(bundled) == len(_VISUALIZATION_LENS_SKILLS)
    assert set(bundled) == set(_VISUALIZATION_LENS_SKILLS)
    assert set(_REACHABLE_VISUALIZATION_LENSES) < set(_VISUALIZATION_LENS_SKILLS)
    assert set(_UNREACHABLE_VISUALIZATION_LENSES) == (
        set(_VISUALIZATION_LENS_SKILLS) - set(_REACHABLE_VISUALIZATION_LENSES)
    )
    assert actual_pins == {
        "research.vis_apply": "codex",
        "research-design.vis_apply": "codex",
    }
    assert set(actual_pins) == _VISUALIZATION_RECIPE_STEP_PINS

    manifest = load_yaml(pkg_root().parents[1] / ".autoskillit" / "test-filter-manifest.yaml")
    skill_targets = set(manifest["src/autoskillit/skills_extended/*/SKILL.md"])
    assert {"contracts/", "execution/", "skills/", "skills_extended/"} <= skill_targets


@pytest.mark.parametrize("skill_name", _VISUALIZATION_LENS_SKILLS)
def test_visualization_vectors_match_complete_reviewed_inventory(skill_name: str) -> None:
    expected = _VISUALIZATION_VECTOR_INVENTORY[skill_name]
    info = _assert_lens_vector_contracts(skill_name, len(expected))

    assert (
        tuple(
            (vector.id, vector.disposition.value, vector.role)
            for vector in info.exploration_vectors
        )
        == expected
    )
    assert len({vector.id for vector in info.exploration_vectors}) == len(expected)
    assert all(
        vector.profile is RepositoryProfileId.AUTO
        and vector.task.profile is RepositoryProfileId.AUTO
        and vector.task.task_id == f"{skill_name}-{vector.id}"
        and vector.task.frontier_item_id == f"{skill_name}-{vector.id}-frontier"
        and vector.task.depends_on == ()
        and vector.task.scope == (".",)
        and vector.rationale.strip()
        and vector.relationship_classes
        for vector in info.exploration_vectors
    )
    assert all(
        vector.role in {"semantic-code-navigator", "repository-impact-profiler"}
        if vector.disposition is ExplorationVectorDisposition.MIGRATED
        else vector.role is None
        for vector in info.exploration_vectors
    )
    assert _review_digest(info) == _VISUALIZATION_REVIEW_DIGESTS[skill_name]

    missing_fields = next(
        vector for vector in info.exploration_vectors if vector.id == "missing-context-fields"
    )
    assert hashlib.sha256(missing_fields.body.encode()).hexdigest() in (
        _MISSING_CONTEXT_FIELDS_BODY_DIGESTS
    )
    normalized_missing_body = missing_fields.body.lower().replace("-", " ")
    for invariant in _MISSING_CONTEXT_INVARIANTS:
        assert invariant in normalized_missing_body, (skill_name, invariant)

    content = info.path.read_text(encoding="utf-8")
    assert "Retain parent authority over" in content
    assert "Wait for " in content
    if skill_name in {
        "vis-lens-always-on",
        "vis-lens-antipattern",
        "vis-lens-caption-annot",
        "vis-lens-chart-select",
    }:
        assert re.search(
            r"Run exploration leaves in the background|must not execute the target",
            content,
        )
    migrated_count = sum(
        vector.disposition is ExplorationVectorDisposition.MIGRATED
        for vector in info.exploration_vectors
    )
    if migrated_count:
        assert "Dispatch every migrated exploration vector below" in content


def test_visualization_retained_context_and_judgment_authorities_are_exact() -> None:
    actual: dict[tuple[str, str], str] = {}

    for skill_name in _VISUALIZATION_LENS_SKILLS:
        info = _load_phase_d_skill(skill_name)
        actual.update(
            {
                (skill_name, vector.id): vector.body.lower()
                for vector in info.exploration_vectors
                if vector.disposition is ExplorationVectorDisposition.RETAINED
            }
        )

    assert set(actual) == set(_VISUALIZATION_RETAINED_BODY_AUTHORITIES)
    for key, required_phrases in _VISUALIZATION_RETAINED_BODY_AUTHORITIES.items():
        assert all(phrase in actual[key] for phrase in required_phrases), key

    for skill_name in (
        "vis-lens-reproducibility",
        "vis-lens-story-arc",
        "vis-lens-temporal",
        "vis-lens-uncertainty",
    ):
        content = _load_phase_d_skill(skill_name).path.read_text(encoding="utf-8")
        assert (
            "Keep external availability, licensing, and network checks lens-owned "
            "and outside native exploration"
        ) in content


def test_visualization_task_inventory_is_complete_unique_and_acyclic() -> None:
    graph: dict[str, set[str]] = {}
    migrated_count = 0
    retained_count = 0

    for skill_name in _VISUALIZATION_LENS_SKILLS:
        info = _load_phase_d_skill(skill_name)
        for vector in info.exploration_vectors:
            assert vector.task.task_id not in graph
            graph[vector.task.task_id] = set(vector.task.depends_on)
            if vector.disposition is ExplorationVectorDisposition.MIGRATED:
                migrated_count += 1
            else:
                retained_count += 1

    assert len(graph) == 58
    assert migrated_count == 47
    assert retained_count == 11
    _assert_acyclic_task_graph(graph, 58, "visualization")


@pytest.mark.parametrize("slug", _ARCHITECTURE_SELECTOR_SLUGS)
def test_architecture_lens_vectors_match_complete_reviewed_inventory(slug: str) -> None:
    expected = _ARCHITECTURE_LENS_INVENTORY[slug]
    skill_name = f"arch-lens-{slug}"
    info = _assert_lens_vector_contracts(skill_name, len(expected))

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
        for vector in info.exploration_vectors
    )
    assert _review_digest(info) == _ARCHITECTURE_REVIEW_DIGESTS[slug]

    content = info.path.read_text(encoding="utf-8")
    assert "Retain parent authority over" in content
    assert "Detach child delegations instead of joining them" in content
    assert "Start all independent child delegations before awaiting any result" in content
    assert "Wait for every exploration result" in content
    if slug != "module-dependency":
        assert "Dispatch every exploration vector below" in content


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
    _assert_acyclic_task_graph(graph, vector_count, "architecture")


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
        == 34
    )
    assert (
        sum(
            disposition == "retained"
            for vectors in _PHASE_D_INVENTORY.values()
            for _, disposition, _, _, _ in vectors
        )
        == 26
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
    } == {"always"}
    assert {
        disposition
        for vectors in _PHASE_D_INVENTORY.values()
        for _, disposition, _, _, _ in vectors
    } == {"migrated", "retained"}
