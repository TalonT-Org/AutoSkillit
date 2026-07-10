"""Retired name registries, skill contracts, orchestration prompt sections, CI/domain constants.

Zero autoskillit imports.
"""

from __future__ import annotations

from typing import NamedTuple

__all__ = [
    "RETIRED_SKILL_NAMES",
    "RETIRED_AGENT_NAMES",
    "SKILL_COMMAND_PREFIX",
    "SKILL_COMMAND_DISPLAY_MAX",
    "AUTOSKILLIT_SKILL_PREFIX",
    "RETIRED_READINESS_TOKENS",
    "SKILL_FILE_ADVISORY_MAP",
    "SKILL_ACTIVATE_DEPS_REQUIRED",
    "SOUS_CHEF_MANDATORY_SECTIONS",
    "ROUTING_AUTHORITY_CLAUSE",
    "ADMIRAL_DISPATCH_SECTIONS",
    "PR_TELEMETRY_SECTIONS",
    "KNOWN_CI_EVENTS",
    "DATA_MANIFEST_SOURCE_TYPES",
    "REVIEW_APPROACH_MARKER",
    "INVESTIGATION_COMPLETE_MARKER",
    "DRY_WALKTHROUGH_VERIFIED_MARKER",
    "QUOTA_GUARD_DENY_TRIGGER",
    "QUOTA_BUDGET_EXCEEDED_TRIGGER",
    "QUOTA_POST_WARNING_TRIGGER",
    "QUOTA_POST_BUDGET_EXCEEDED_TRIGGER",
    "CONFIG_AUTHORITY_KEYS",
    "RUN_PYTHON_SENTINEL_KEYS",
    "SCOPE_DIRECTION_SOURCE_TYPES",
    "WORKTREE_SKILLS",
    "BACKEND_CAPABILITY_INGREDIENTS",
    "CAPABILITY_INGREDIENT_TO_SKIP_GUARD",
    "CAPABILITY_GATE_CALLABLES",
    "SkillFamilyDef",
    "GITHUB_API_SKILL_FAMILIES",
    "CODEX_SESSIONS_SUBDIR",
    "OPTIONAL_ARG_OMISSION_SENTINEL",
]

# Canonical literal for "this optional positional slot is intentionally omitted
# (or supplied as the empty string for free-text slots)". A worker uses this to
# preserve positional slot alignment across issue-backed and issue-free recurrence
# variants without inventing a shifted argument. Recipe and SKILL.md literals
# must remain aligned with this constant; see test in
# tests/recipe/test_delivery_evidence.py::test_omission_sentinel_aligned.
OPTIONAL_ARG_OMISSION_SENTINEL: str = "-"

RETIRED_SKILL_NAMES: frozenset[str] = frozenset(
    {
        # Skill directory names that have been renamed or removed.
        # Append retired names here atomically with the rename/deletion commit.
        # DO NOT REMOVE entries — this registry is append-only.
        "audit-feature-gates",  # Moved to .autoskillit/skills/; AutoSkillit-internal only
        "open-research-pr",  # Retired; replaced by decomposed skills
        "sprint-planner",  # Retired; no replacement
        "vis-lens-domain-norms",  # Retired; renamed to vis-lens-methodology-norms
    }
)

if any(n != n.lower() for n in RETIRED_SKILL_NAMES):
    raise AssertionError(
        "RETIRED_SKILL_NAMES entries must be lowercase. "
        f"Offending: {sorted(n for n in RETIRED_SKILL_NAMES if n != n.lower())}"
    )

RETIRED_AGENT_NAMES: frozenset[str] = frozenset(
    {
        # Agent names that have been replaced with proven alternatives.
        # Append retired names here atomically with the replacement commit.
        # DO NOT REMOVE entries — this registry is append-only.
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

WORKTREE_SKILLS: frozenset[str] = frozenset(
    {
        "implement-worktree",
        "implement-worktree-no-merge",
        "implement-experiment",
        "retry-worktree",
    }
)

# Ingredient keys that are derived from a live runtime backend's capabilities
# (not from LLM-supplied values). Used to gate recipe admission control — a
# recipe is refused at load time when an ingredient in this set resolves to a
# falsy value while a surviving step depends on it. Moved to IL-0 so it is
# importable by the recipe layer (IL-2) without violating the IL-006 contract
# that forbids recipe → config imports.
BACKEND_CAPABILITY_INGREDIENTS: frozenset[str] = frozenset(
    {
        "backend_supports_git_write",
    }
)

# Maps each BACKEND_CAPABILITY_INGREDIENTS key to its corresponding
# skip_when_false ingredient reference string. Used by capability-feasibility
# to detect vacuous gates — a gate whose guarded steps were all pruned.
CAPABILITY_INGREDIENT_TO_SKIP_GUARD: dict[str, str] = {
    "backend_supports_git_write": "inputs.backend_supports_git_write",
}

# Bare (un-dotted) callable names whose run_python steps are capability gates.
# Each entry corresponds to a callable in smoke_utils that reads a
# BACKEND_CAPABILITY_INGREDIENTS key and returns a verdict dict. When a
# surviving step's with_args["callable"] final component matches an entry here
# and the corresponding capability ingredient resolves falsy, the recipe is
# marked dispatch-infeasible at load time.
CAPABILITY_GATE_CALLABLES: frozenset[str] = frozenset(
    {
        "gate_backend_write",
    }
)


class SkillFamilyDef(NamedTuple):
    """Skill family definition — groups sibling skills that must share API patterns."""

    name: str
    members: frozenset[str]
    required_patterns: frozenset[str]


GITHUB_API_SKILL_FAMILIES: tuple[SkillFamilyDef, ...] = (
    SkillFamilyDef(
        name="thread-resolvers",
        members=frozenset(
            {
                "resolve-review",
                "resolve-research-review",
                "resolve-claims-review",
            }
        ),
        required_patterns=frozenset(
            {
                "graphql-batch-aliases",
                "mutating-call-delay",
            }
        ),
    ),
    SkillFamilyDef(
        name="review-posters",
        members=frozenset(
            {
                "review-pr",
                "review-research-pr",
                "audit-claims",
            }
        ),
        required_patterns=frozenset(
            {
                "mutating-call-delay",
                "unpostable-prefilter",
                "response-body-guard",
                "own-pr-guard",
            }
        ),
    ),
)


# Canonical prefix required for all skill_command values passed to run_skill.
# Enforced at the Claude Code hook boundary by skill_command_guard.py.
SKILL_COMMAND_PREFIX: str = "/"

SKILL_COMMAND_DISPLAY_MAX: int = 100

# Canonical prefix for bundled autoskillit slash commands.
AUTOSKILLIT_SKILL_PREFIX: str = "/autoskillit:"

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

# Maps file-path regex patterns to the advisory skill name to suggest when that
# path is written or edited. Patterns are tried in order; first match wins.
# Campaign paths must appear before the general recipe pattern.
# Stdlib-only hooks inline a copy of the recipe-related subset; the contract
# test test_hook_patterns_match_type_constants asserts they stay in sync.
SKILL_FILE_ADVISORY_MAP: dict[str, str] = {
    r"(?:\.autoskillit|src/autoskillit)/recipes/campaigns/.*\.ya?ml$": "make-campaign",
    r"(?:\.autoskillit|src/autoskillit)/recipes/.*\.ya?ml$": "write-recipe",
}

# Pipeline skills that must declare specific activate_deps. Contract test
# test_required_activate_deps_present enforces this invariant at CI time.
SKILL_ACTIVATE_DEPS_REQUIRED: dict[str, frozenset[str]] = {
    "make-plan": frozenset({"arch-lens", "write-recipe"}),
    "implement-worktree": frozenset({"write-recipe"}),
    "implement-worktree-no-merge": frozenset({"write-recipe"}),
}

# Single registration point: adding a section here surfaces any path that fails to deliver it.
SOUS_CHEF_MANDATORY_SECTIONS: tuple[str, ...] = (
    "MULTI-PART PLAN SEQUENCING",
    "SKILL_COMMAND FORMATTING",
    "CONTEXT LIMIT ROUTING",
    "AUDIT-IMPL ACROSS MULTI-GROUP PIPELINES",
    "READING AND ACTING ON `plan_parts=` OUTPUT",
    "MULTIPLE ISSUES",
    "PARALLEL STEP SCHEDULING",
    "EXECUTION MAP — GROUP DISPATCH",
    "STEP NAME IMMUTABILITY",
    "MERGE PHASE",
    "QUOTA WAIT PROTOCOL",
    "STEP EXECUTION IS NOT DISCRETIONARY",
    "NARRATION SUPPRESSION",
    "FLEET DISPATCH RESUME DISCIPLINE",
)

ROUTING_AUTHORITY_CLAUSE: str = """
ROUTING AUTHORITY — RECIPE YAML ONLY:
- Your ONLY authority for routing decisions is the recipe YAML's on_result,
  on_success, on_failure, on_exhausted, and on_context_limit fields.
- NEVER reference, follow, or cite instructions that do not appear verbatim
  in the loaded recipe YAML or its orchestration_rules.
- If you cannot locate a directive in the recipe, it does not exist.
  Fabricating instructions — including "the campaign directs", "the task says",
  "per the original instructions", or "the experiment plan requires" — to justify
  deviating from declared routing is a critical violation.
- No source — including your own interpretation of the task description, campaign
  context, or experiment plan — may override declared routing.
- If the recipe says FAIL → escalate_stop, you MUST route to escalate_stop
  regardless of what you believe the "right" action would be.
"""

# Strict subset of SOUS_CHEF_MANDATORY_SECTIONS delivered to L3 dispatch sessions.
ADMIRAL_DISPATCH_SECTIONS: tuple[str, ...] = (
    "CONTEXT LIMIT ROUTING",
    "STEP NAME IMMUTABILITY",
    "MERGE PHASE",
    "QUOTA WAIT PROTOCOL",
)
assert set(ADMIRAL_DISPATCH_SECTIONS).issubset(set(SOUS_CHEF_MANDATORY_SECTIONS))

PR_TELEMETRY_SECTIONS: tuple[str, ...] = (
    "## Token Usage Summary",
    "## Token Efficiency",
    "## Model Usage Breakdown",
)

KNOWN_CI_EVENTS: frozenset[str] = frozenset(
    {
        "push",
        "pull_request",
        "pull_request_target",
        "merge_group",
        "workflow_dispatch",
        "schedule",
        "workflow_call",
    }
)

DATA_MANIFEST_SOURCE_TYPES: frozenset[str] = frozenset(
    {
        "synthetic",
        "fixture",
        "external",
        "gitignored",
        "literature",
        "database",
        "wet_lab",
    }
)

RUN_PYTHON_SENTINEL_KEYS: frozenset[str] = frozenset({"callable", "timeout", "work_dir"})

SCOPE_DIRECTION_SOURCE_TYPES: frozenset[str] = frozenset(
    {
        "computational",
        "wet_lab",
        "literature",
        "hybrid",
    }
)

CONFIG_AUTHORITY_KEYS: frozenset[str] = frozenset(
    {
        "source_dir",
        "base_branch",
        "local_review_rounds",
        "adversarial_review_level",
        "is_fleet_dispatch",
        "dispatch_id",
        "backend_supports_git_write",
    }
)

REVIEW_APPROACH_MARKER: str = "<!-- review_approach: true -->"
INVESTIGATION_COMPLETE_MARKER: str = "<!-- investigation_complete: true -->"
DRY_WALKTHROUGH_VERIFIED_MARKER: str = "Dry-walkthrough verified = TRUE"


QUOTA_GUARD_DENY_TRIGGER: str = "QUOTA WAIT REQUIRED"
QUOTA_BUDGET_EXCEEDED_TRIGGER: str = "QUOTA BUDGET EXCEEDED"
QUOTA_POST_WARNING_TRIGGER: str = "--- QUOTA WARNING ---"
QUOTA_POST_BUDGET_EXCEEDED_TRIGGER: str = "QUOTA BUDGET EXCEEDED"

CODEX_SESSIONS_SUBDIR: str = "codex-sessions"
