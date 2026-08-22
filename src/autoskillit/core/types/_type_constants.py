"""Retired name registries, skill contracts, orchestration prompt sections, CI/domain constants.

Zero autoskillit imports; sibling imports remain within the IL-0 type package.

Issue #4735: this module is a re-export facade. The retirement registries
live in ``_type_constants_retirements.py``, the skill-contract remediation
registry in ``_type_constants_skill_contract.py``, and the durable-artifact
writer registry in ``_type_constants_durable_writers.py``. The 11 moved
public names are bound into this module's namespace via wildcard re-export
so direct symbol imports
(``from autoskillit.core.types._type_constants import RETIRED_SKILL_NAMES``)
and direct attribute access resolve the same object identity.

The facade's own ``__all__`` excludes the 11 moved names so the package hub
``__init__.py`` concatenated ``__all__`` has no duplicates. The hub's
wildcard-import chain still surfaces every moved name to
``autoskillit.core.types.*`` and ``autoskillit.core.*`` consumers.
"""

from __future__ import annotations

from hashlib import sha256
from typing import NamedTuple

from ._type_constants_durable_writers import *  # noqa: F401, F403
from ._type_constants_retirements import *  # noqa: F401, F403
from ._type_constants_skill_contract import *  # noqa: F401, F403

__all__ = [
    "OUTPUT_DISCIPLINE_POLICY_VERSION",
    "OUTPUT_DISCIPLINE_BLOCK",
    "OUTPUT_DISCIPLINE_BLOCK_SHA256",
    "OUTPUT_DISCIPLINE_COMBINED_SHA256",
    "OUTPUT_DISCIPLINE_DIGEST",
    "OUTPUT_DISCIPLINE_REQUIRED_SKILLS",
    "SKILL_COMMAND_PREFIX",
    "SKILL_COMMAND_DISPLAY_MAX",
    "AUTOSKILLIT_SKILL_PREFIX",
    "SKILL_FILE_ADVISORY_MAP",
    "SKILL_ACTIVATE_DEPS_REQUIRED",
    "SOUS_CHEF_MANDATORY_SECTIONS",
    "INFRASTRUCTURE_FAULT_OVERRIDE_CLAUSE",
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
    "CALLER_SOVEREIGN_INGREDIENTS",
    "RUN_PYTHON_PATH_LIKE_ARGS",
    "RUN_PYTHON_SENTINEL_KEYS",
    "SCOPE_DIRECTION_SOURCE_TYPES",
    "WORKTREE_SKILLS",
    "SkillFamilyDef",
    "GITHUB_API_SKILL_FAMILIES",
    "CODEX_ACTIVE_VIEWS_SUBDIR",
    "CODEX_ATTEMPT_RECONCILIATIONS_SUBDIR",
    "CODEX_ATTEMPT_RECONCILIATION_TOMBSTONES_SUBDIR",
    "CODEX_ARCHIVED_SESSIONS_SUBDIR",
    "CODEX_SESSIONS_SUBDIR",
    "SESSION_ADD_DIR_SUBDIR",
    "RECIPE_EXECUTION_ATTESTATION_MISSING_MESSAGE",
    "RECIPE_EXECUTION_INACTIVE_MESSAGE",
]


OUTPUT_DISCIPLINE_POLICY_VERSION = 1

OUTPUT_DISCIPLINE_BLOCK = "\n".join(
    (
        "### Output Discipline Policy v1",
        "",
        (
            "- Treat shell and tool output as a bounded resource. Choose the smallest "
            "useful producer and set a byte limit before running it."
        ),
        (
            "- Bound discovery itself: use forms such as "
            "`rg -l PATTERN PATH 2>&1 | head -c N`, where `N` is within the configured "
            "inline-output ceiling, or redirect both descriptors to a project-temp artifact."
        ),
        (
            "- For JSONL, use record-aware search with a per-record limit such as "
            "`rg -M 500`; never rely on a bare line cap because one record may contain "
            "an arbitrarily large payload."
        ),
        (
            "- Route stdout and stderr from every stage of an output-producing pipeline "
            "into the terminal byte cap. Intermediate stderr must not bypass the cap."
        ),
        (
            "- Follow `jq` field extraction with a byte cap. Selecting one field does not "
            "make its contents small."
        ),
        (
            "- Redirect potentially unbounded output to "
            "`{{AUTOSKILLIT_TEMP}}/<skill>/out.txt` with both descriptors captured, then "
            "inspect only bounded searches or byte slices from that artifact."
        ),
        (
            "- Read complete files only when their size is known to be small. Otherwise "
            "locate matches first and read only the bounded relevant region."
        ),
        (
            "- Give every subagent an explicit maximum size for its final report and "
            "request only evidence needed by the parent synthesis."
        ),
        (
            "- Before authorizing each deep-mode batch, reserve enough context for "
            "synthesis, report writing, and validation. Stop gathering and begin "
            "synthesis when another batch would cross that reserve."
        ),
        (
            "- A command's success does not make oversized inline output safe. Preserve "
            "full evidence in project temp and return a bounded summary plus the "
            "artifact path."
        ),
    )
)

OUTPUT_DISCIPLINE_BLOCK_SHA256 = sha256(OUTPUT_DISCIPLINE_BLOCK.encode("utf-8")).hexdigest()

OUTPUT_DISCIPLINE_DIGEST = "\n".join(
    (
        "Output Discipline Policy v1:",
        (
            "- Bound every shell/tool producer by bytes before execution; discovery "
            "output is also bounded."
        ),
        (
            "- Merge stdout and stderr through the final byte cap so neither descriptor "
            "bypasses it."
        ),
        ("- Use record-aware limits such as `rg -M 500` for JSONL; never trust bare line caps."),
        (
            "- Put a byte cap after `jq` field extraction because a selected field may "
            "still be huge."
        ),
        (
            "- Send potentially unbounded output to "
            "`{{AUTOSKILLIT_TEMP}}/<skill>/out.txt`, capturing both descriptors."
        ),
        (
            "- Inspect saved output only with bounded searches or byte slices; directly "
            "read only known-small files."
        ),
        (
            "- Give subagents explicit final-report size targets and request only "
            "synthesis-relevant evidence."
        ),
        (
            "- Before each deep-mode batch, reserve context for synthesis, report "
            "writing, and validation."
        ),
        (
            "- Stop gathering when another batch would cross that reserve; preserve "
            "evidence by artifact path."
        ),
    )
)

# Covers both policy texts (SKILL.md block and runtime-injected digest) so
# editing either one invalidates cache keys derived from the policy content.
OUTPUT_DISCIPLINE_COMBINED_SHA256 = sha256(
    (OUTPUT_DISCIPLINE_BLOCK + "\x00" + OUTPUT_DISCIPLINE_DIGEST).encode("utf-8")
).hexdigest()

OUTPUT_DISCIPLINE_REQUIRED_SKILLS: frozenset[str] = frozenset(
    {"investigate", "rectify", "audit-bugs", "audit-friction"}
)

# Canonical prefix required for all skill_command values passed to run_skill.
# Enforced at the Claude Code hook boundary by skill_command_guard.py.
SKILL_COMMAND_PREFIX: str = "/"

SKILL_COMMAND_DISPLAY_MAX: int = 100

# Canonical prefix for bundled autoskillit slash commands.
AUTOSKILLIT_SKILL_PREFIX: str = "/autoskillit:"

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
    "make-plan": frozenset({"write-recipe"}),
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
    "RUN_SKILL COMPLETION HANDSHAKE",
    "NARRATION SUPPRESSION",
    "FLEET DISPATCH RESUME DISCIPLINE",
)

INFRASTRUCTURE_FAULT_OVERRIDE_CLAUSE: str = """\
INFRASTRUCTURE FAULT OVERRIDE — checked BEFORE on_failure, for run_skill only:
when the result JSON contains "infra_fault_domain": "infrastructure", the
step's on_failure route MUST NOT be followed. The environment faulted, not the
work. Halt the pipeline and report the environment fault instead of routing to
on_failure.
"""

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
    "RUN_SKILL COMPLETION HANDSHAKE",
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

RUN_PYTHON_PATH_LIKE_ARGS: frozenset[str] = frozenset(
    {"output_dir", "workspace", "diagnostics_log_dir", "investigation_path"}
)
RUN_PYTHON_SENTINEL_KEYS: frozenset[str] = frozenset(
    {"callable", "step_name", "timeout", "work_dir"}
)

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
    }
)

# Config-authority keys that are legitimately caller-supplied rather than
# server-resolved (e.g. source_dir is project-identity — the clone URL — supplied
# by the dispatching caller, not injected from local project config). The named
# registry replaces the implicit CONFIG_AUTHORITY_KEYS - {"source_dir"} set-difference
# that was previously scattered across ingredient_defaults.py and test assertions.
CALLER_SOVEREIGN_INGREDIENTS: frozenset[str] = frozenset(
    {
        "source_dir",
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
CODEX_ARCHIVED_SESSIONS_SUBDIR: str = "codex-archived-sessions"
CODEX_ACTIVE_VIEWS_SUBDIR: str = "codex-active-sessions"
CODEX_ATTEMPT_RECONCILIATIONS_SUBDIR: str = "codex-attempt-reconciliations"
CODEX_ATTEMPT_RECONCILIATION_TOMBSTONES_SUBDIR: str = "codex-attempt-reconciliation-tombstones"
SESSION_ADD_DIR_SUBDIR: str = "add-dir"

RECIPE_EXECUTION_ATTESTATION_MISSING_MESSAGE: str = (
    "an active recipe requires recipe_execution_id and invocation_template_digest; "
    "take both from the recipe_execution block of the complete_recipe_initialization "
    "receipt (bounded delivery) or of the open_kitchen response (inline delivery), "
    "using invocation_template_digests[step_name] for this step; structured calls must "
    "initialize skill_inputs from skill_input_shapes[step_name] ordered keys, replace "
    "available values in place, copy only advertised absence_values by key presence "
    'so "", 0, and False remain verbatim, and never delete or invent a key'
)

RECIPE_EXECUTION_INACTIVE_MESSAGE: str = (
    "no recipe execution is installed in this server process, so this call cannot claim "
    "recipe attestation; a reloaded session has no execution — open the kitchen and "
    "complete_recipe_initialization to establish one, or drop the attestation arguments "
    "to run this skill without a recipe"
)


WORKTREE_SKILLS: frozenset[str] = frozenset(
    {
        "implement-worktree",
        "implement-worktree-no-merge",
        "implement-experiment",
        "retry-worktree",
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
                "unpostable-prefilter",
            }
        ),
    ),
)
