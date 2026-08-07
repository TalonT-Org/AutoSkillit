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
    "vis-lens-always-on": "bb631b4089215cc7862fae6510375196ba2f6d5f07eca09e40d7def638dddb16",
    "vis-lens-antipattern": "6bc948219ec78e9b9b51048fe596b86fe0220015852347e405b6b5ea0ea5a7e0",
    "vis-lens-caption-annot": "b041b62130b211ec819a78827796eacf349bbcdceabf45ba4ba92222654164c2",
    "vis-lens-chart-select": "47a5e8aafcf32ce6508cf6899016e07b162091cc7e7d1c162e58621dec5e20ce",
    "vis-lens-color-access": "e94e7f559fd2975abd3a9fc484e063c6be55636f16919dc2c745448f367a95e5",
    "vis-lens-figure-table": "1d69193874c4c77eb323e0ee9cde662aa841425ef8222975b9dad536f50bab4d",
    "vis-lens-methodology-norms": (
        "d387d876ceeca25d3a7baeec87e39e3da635683c5586b013daf012ac0553dba7"
    ),
    "vis-lens-multi-compare": "e6ee5c06f6c401b929aab13ec28643f620c937b682673158b3f5601fa335ccfd",
    "vis-lens-reproducibility": (
        "a077c788bc34dc29715c90da8b123650a05b85c2307b6d56a9520e02036fff73"
    ),
    "vis-lens-story-arc": "7ad24361119770d790a7c98d730a44e1d84c48af047905feb8045296eea468e1",
    "vis-lens-temporal": "9d2a14ae7389896827acd6a67a98189efb81cf3607a57f3795cdcb7ad58a0578",
    "vis-lens-uncertainty": "3d45a8f0f6013a244f94521351dc37a29ed4375b4a0ee78921050c27dfc8f0e8",
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
    assert not info.invalidities, info.invalidities
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

    assert not info.invalidities, info.invalidities
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
    if migrated_count and skill_name not in {
        "vis-lens-reproducibility",
        "vis-lens-story-arc",
        "vis-lens-temporal",
        "vis-lens-uncertainty",
    }:
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


def test_migration_completeness_census_total_vector_count() -> None:
    """No skill carries the retired `exploration_vectors` frontmatter key, and
    every `exploration.yaml` sidecar under `skills/` and `skills_extended/`
    sums to the reviewed migration census: 264 migrated + 37 retained = 301."""
    skill_roots = (pkg_root() / "skills", pkg_root() / "skills_extended")
    skill_md_paths = tuple(
        sorted(path for root in skill_roots for path in root.glob("*/SKILL.md"))
    )
    assert skill_md_paths

    migrated_count = 0
    retained_count = 0
    sidecar_count = 0

    for skill_md_path in skill_md_paths:
        frontmatter_text = skill_md_path.read_text(encoding="utf-8").split("---", 2)[1]
        frontmatter = load_yaml(frontmatter_text)
        assert isinstance(frontmatter, dict)
        assert "exploration_vectors" not in frontmatter, skill_md_path

        source = (
            SkillSource.BUNDLED
            if skill_md_path.parents[1].name == "skills"
            else SkillSource.BUNDLED_EXTENDED
        )
        info = _skill_info_from_frontmatter(skill_md_path.parent.name, source, skill_md_path)
        assert not info.invalidities, (skill_md_path, info.invalidities)

        if (skill_md_path.parent / "exploration.yaml").is_file():
            sidecar_count += 1
        for vector in info.exploration_vectors:
            if vector.disposition is ExplorationVectorDisposition.MIGRATED:
                migrated_count += 1
            elif vector.disposition is ExplorationVectorDisposition.RETAINED:
                retained_count += 1
            else:
                pytest.fail(f"unexpected disposition {vector.disposition} in {skill_md_path}")

    assert sidecar_count > 0
    assert migrated_count == 264
    assert retained_count == 37
    assert migrated_count + retained_count == 301
