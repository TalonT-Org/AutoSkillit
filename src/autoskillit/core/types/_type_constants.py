"""Retired name registries, skill contracts, orchestration prompt sections, CI/domain constants.

Zero autoskillit imports.
"""

from __future__ import annotations

__all__ = [
    "RETIRED_SKILL_NAMES",
    "RETIRED_AGENT_NAMES",
    "SKILL_COMMAND_PREFIX",
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
    "SCOPE_DIRECTION_SOURCE_TYPES",
]

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


# Canonical prefix required for all skill_command values passed to run_skill.
# Enforced at the Claude Code hook boundary by skill_command_guard.py.
SKILL_COMMAND_PREFIX: str = "/"

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

SCOPE_DIRECTION_SOURCE_TYPES: frozenset[str] = frozenset(
    {
        "computational",
        "wet_lab",
        "literature",
        "hybrid",
    }
)
