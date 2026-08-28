"""Retirement and UNAFFECTED-skill registries.

Zero autoskillit imports. Stdlib-only sibling imports remain within the IL-0
types package. The ``disposition`` field on ``RetiredArtifactShape`` requires
``Literal`` at class-definition time (named-tuple field evaluation), so
``Literal`` is imported alongside ``NamedTuple``.

Issue #4735: extracted from ``_type_constants.py`` to keep the facade under
the enforced 750-line budget (``test_warning_zone_files_under_750_lines``).
``RETIRED_READINESS_TOKENS`` is structurally a retirement registry and is
grouped here.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import Literal, NamedTuple

__all__ = [
    "KNOWN_UNAFFECTED_SKILL_IDS",
    "KNOWN_BLOCKED_SKILL_IDS",
    "KNOWN_DEGRADED_SKILL_IDS",
    "RETIRED_SKILL_NAMES",
    "RETIRED_SKILL_RESOURCE_IDS",
    "RETIRED_AGENT_NAMES",
    "RETIRED_INTAKE_RULE_IDS",
    "RetiredArtifactShape",
    "RETIRED_INSTALL_ARTIFACT_SHAPES",
    "RETIRED_READINESS_TOKENS",
]


RETIRED_SKILL_NAMES: frozenset[str] = frozenset(
    {
        # Skill directory names that have been renamed or removed.
        # Append retired names here atomically with the rename/deletion commit.
        # DO NOT REMOVE entries — this registry is append-only.
        "audit-feature-gates",  # Moved to .autoskillit/skills/; AutoSkillit-internal only
        "enrich-issues",  # Retired; auto-generated requirements backfill removed (#4477)
        "open-research-pr",  # Retired; replaced by decomposed skills
        "sprint-planner",  # Retired; no replacement
        "vis-lens-domain-norms",  # Retired; renamed to vis-lens-methodology-norms
    }
)

RETIRED_SKILL_RESOURCE_IDS: frozenset[str] = frozenset()

if any(n != n.lower() for n in RETIRED_SKILL_NAMES):
    raise AssertionError(
        "RETIRED_SKILL_NAMES entries must be lowercase. "
        f"Offending: {sorted(n for n in RETIRED_SKILL_NAMES if n != n.lower())}"
    )
_non_lowercase_retired_resource_ids = sorted(
    resource_id for resource_id in RETIRED_SKILL_RESOURCE_IDS if resource_id != resource_id.lower()
)
if _non_lowercase_retired_resource_ids:
    raise AssertionError(
        "RETIRED_SKILL_RESOURCE_IDS entries must be lowercase. "
        f"Offending: {_non_lowercase_retired_resource_ids}"
    )

# UNAFFECTED-skill registry for issue #4684 AC6 ("No new failure mode is
# introduced for the unaffected skills"). A skill is UNAFFECTED iff its
# SKILL.md carries no `<!-- autoskillit:exploration-vector id="..." -->`
# marker — the same predicate tests/contracts/test_unaffected_skill_registry.py's
# _discover_unaffected_skills() applies live against the current skills/ and
# skills_extended/ trees. When a skill's UNAFFECTED status changes (gains a
# marker, is retired, or a new skill is added), update this registry in the
# same PR, citing the tracking issue.
KNOWN_UNAFFECTED_SKILL_IDS: frozenset[str] = frozenset(
    {
        "analyze-pipeline-health",
        "analyze-prs",
        "apply-review-dimensions",
        "audit-arch",
        "audit-bugs",
        "audit-claims",
        "audit-cohesion",
        "audit-defense-standards",
        "audit-friction",
        "audit-impl",
        "audit-review-decisions",
        "audit-tests",
        "build-execution-map",
        "bundle-local-report",
        "classify-experiment-type",
        "close-kitchen",
        "collapse-issues",
        "compose-pr",
        "compose-research-pr",
        "design-guards",
        "diagnose-ci",
        "download-data",
        "dry-walkthrough",
        "elaborate-phase",
        "file-audit-issues",
        "generate-report",
        "implement-experiment",
        "implement-worktree",
        "implement-worktree-no-merge",
        "issue-splitter",
        "make-arch-diag",
        "make-campaign",
        "make-experiment-diag",
        "make-groups",
        "make-plan",
        "make-req",
        "merge-pr",
        "mermaid",
        "migrate-recipes",
        "open-integration-pr",
        "open-kitchen",
        "phoropter-null-synthesis",
        "phoropter-priority-synthesis",
        "pipeline-summary",
        "plan-experiment",
        "plan-visualization",
        "planner-assess-review-approach",
        "planner-consolidate-wps",
        "planner-elaborate-assignments",
        "planner-elaborate-wps",
        "planner-generate-phases",
        "planner-reconcile-deps",
        "planner-refine",
        "planner-refine-assignments",
        "planner-refine-phases",
        "planner-refine-wps",
        "planner-validate-task-alignment",
        "prepare-issue",
        "prepare-pr",
        "prepare-research-pr",
        "process-issues",
        "promote-to-main",
        "rectify",
        "reload-session",
        "report-bug",
        "resolve-claims-review",
        "resolve-design-review",
        "resolve-failures",
        "resolve-merge-conflicts",
        "resolve-research-review",
        "resolve-review",
        "retry-worktree",
        "review-approach",
        "review-design",
        "review-pr",
        "review-research-pr",
        "run-experiment",
        "select-directions",
        "select-vis-lenses",
        "setup-environment",
        "setup-project",
        "smoke-task",
        "sous-chef",
        "stage-data",
        "synthesize-vis-plan",
        "triage-issues",
        "troubleshoot-experiment",
        "validate-audit",
        "validate-review-decisions",
        "validate-test-audit",
        "verify-diag",
        "write-recipe",
    }
)

# Skill-impact matrix registries for issue #4684 AC7. A skill is BLOCKED iff its
# SKILL.md carries the exploration-vector marker AND declares `for_each:
# exploration_vectors` in its frontmatter (a child_spawns entry that iterates
# per-vector); DEGRADED iff it carries the marker but not that frontmatter
# field. Both predicates mirror tests/contracts/test_skill_impact_matrix_registry.py's
# live discovery over the same skills/ and skills_extended/ trees used by
# KNOWN_UNAFFECTED_SKILL_IDS above. The three buckets partition the same
# population: |BLOCKED| + |DEGRADED| + |UNAFFECTED| == the combined skill count.
# Update all three registries together in the same PR when a skill's bucket changes.
KNOWN_BLOCKED_SKILL_IDS: frozenset[str] = frozenset(
    {
        "arch-lens-c4-container",
        "arch-lens-concurrency",
        "arch-lens-data-lineage",
        "arch-lens-deployment",
        "arch-lens-development",
        "arch-lens-error-resilience",
        "arch-lens-module-dependency",
        "arch-lens-operational",
        "arch-lens-process-flow",
        "arch-lens-repository-access",
        "arch-lens-scenarios",
        "arch-lens-security",
        "arch-lens-state-lifecycle",
    }
)

KNOWN_DEGRADED_SKILL_IDS: frozenset[str] = frozenset(
    {
        "audit-docs",
        "exp-lens-benchmark-representativeness",
        "exp-lens-causal-assumptions",
        "exp-lens-comparator-construction",
        "exp-lens-error-budget",
        "exp-lens-estimand-clarity",
        "exp-lens-exploratory-confirmatory",
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
        "investigate",
        "planner-analyze",
        "planner-elaborate-phase",
        "planner-extract-domain",
        "scope",
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
    }
)

RETIRED_AGENT_NAMES: frozenset[str] = frozenset(
    {
        # Agent names that have been replaced with proven alternatives.
        # Append retired names here atomically with the replacement commit.
        # DO NOT REMOVE entries — this registry is append-only.
        "pipeline-health-scanner",
        "plan-assumption-challenger",
        "plan-completeness-auditor",
        "plan-contract-verifier",
        "plan-registry-wire-tracer",
    }
)

if any(n != n.lower() for n in RETIRED_AGENT_NAMES):
    raise AssertionError(
        "RETIRED_AGENT_NAMES entries must be lowercase. "
        f"Offending: {sorted(n for n in RETIRED_AGENT_NAMES if n != n.lower())}"
    )

RETIRED_INTAKE_RULE_IDS: frozenset[str] = frozenset(
    {
        # Intake-rule ids that have been removed from CODEX_INTAKE_RULES.
        # Append retired ids here atomically with the removal commit.
        # DO NOT REMOVE entries — this registry is append-only.
        # Removed #4487; harness injection made the re-read redundant.
        "instruction-file-completeness",
    }
)

if any(n != n.lower() for n in RETIRED_INTAKE_RULE_IDS):
    raise AssertionError(
        "RETIRED_INTAKE_RULE_IDS entries must be lowercase. "
        f"Offending: {sorted(n for n in RETIRED_INTAKE_RULE_IDS if n != n.lower())}"
    )


class RetiredArtifactShape(NamedTuple):
    """One on-disk install artifact whose *shape* was retired by a release.

    ``~/.autoskillit/`` and ``~/.claude/plugins/`` persist across years of
    versions, so changing the shape of an artifact we write there (symlink to
    real directory, file to directory, …) strands every pre-existing install.
    Declaring the retirement here is what gives the reconciler something to
    repair and the guard test something to enforce.

    ``disposition`` controls how the reconciler handles the retired shape:

    - ``"delete"`` — unconditional removal (the original behavior).
    - ``"retire_via_engine"`` — enqueue into the retirement engine with
      the standard grace window and per-path lease gating, so a live
      session's inherited shared-lease fd is never invalidated.
    """

    shape: str
    retired_in: str
    reason: str
    disposition: Literal["delete", "retire_via_engine"] = "delete"


# Artifact key -> the shape that was retired. Keys are ``Path.home()``-relative
# POSIX strings and are resolved against ``Path.home()`` at use time, so the
# registry works unchanged under the ``monkeypatch.setattr(Path, "home", ...)``
# pattern the test suite depends on. Absolute keys are rejected by a guard test.
#
# Append-only, exactly like RETIRED_SKILL_NAMES / RETIRED_AGENT_NAMES: adding an
# entry here is the *forcing function* that makes an artifact-shape change
# mergeable. The reconciler in workspace/_install_state.py consumes this at
# runtime — it must handle every entry, and nothing outside it.
RETIRED_INSTALL_ARTIFACT_SHAPES: Mapping[str, RetiredArtifactShape] = MappingProxyType(
    {
        ".autoskillit/marketplace/plugins/autoskillit": RetiredArtifactShape(
            shape="symlink",
            retired_in="0.10.892",
            reason=(
                "The public marketplace plugin root was a symlink to pkg_root() before "
                "0.10.892 and is a materialized directory after it. A leftover symlink "
                "makes the projection's containment check resolve the destination onto "
                "its own source root."
            ),
        ),
        ".claude/plugins/cache/autoskillit-local/autoskillit": RetiredArtifactShape(
            shape="directory",
            retired_in="0.10.933",
            reason=(
                "The Claude-cache installed plugin artifact was replaced by generation-keyed "
                "publication under ~/.autoskillit/plugin-generations/. Live sessions may "
                "hold inherited shared-lease fds on version subdirectories, so the store "
                "is routed through the retirement engine rather than deleted immediately."
            ),
            disposition="retire_via_engine",
        ),
        # ".autoskillit/plugin-projections" is deliberately NOT registered here yet.
        # It is still the live store `ProjectedPluginArtifactAuthority`
        # (workspace/_projected_artifact/authority.py) publishes to and binds cook
        # and headless sessions from on every launch (PROJECTED_HOME/
        # EXPLICIT_PLUGIN_DIR). Registering it as retired before that authority's
        # dual-store logic collapses onto the generation store (tracked separately)
        # would make verify_install_state() flag a healthy, actively-served store
        # as broken on every machine that has ever run `autoskillit cook`.
    }
)

_ABSOLUTE_ARTIFACT_KEYS = sorted(k for k in RETIRED_INSTALL_ARTIFACT_SHAPES if k.startswith("/"))
if _ABSOLUTE_ARTIFACT_KEYS:
    raise AssertionError(
        "RETIRED_INSTALL_ARTIFACT_SHAPES keys must be Path.home()-relative. "
        f"Offending: {_ABSOLUTE_ARTIFACT_KEYS}"
    )


# Log message tokens that were once used as subprocess readiness sync primitives
# and have since been retired. Any logger call using these tokens as its first
# positional argument is a structural anti-pattern — the lifespan's try: block
# and the anyio signal receiver replaced them with a filesystem sentinel.
# Consumed by test_lifespan_readiness_structural.py (AST Assertion C).
RETIRED_READINESS_TOKENS: frozenset[str] = frozenset(
    {
        "lifespan_started",
        "sigterm_handler_ready",
    }
)
