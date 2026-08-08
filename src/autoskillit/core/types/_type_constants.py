"""Retired name registries, skill contracts, orchestration prompt sections, CI/domain constants.

Zero autoskillit imports; sibling imports remain within the IL-0 type package.
"""

from __future__ import annotations

from collections.abc import Mapping
from hashlib import sha256
from types import MappingProxyType
from typing import Literal, NamedTuple

from ._type_enums import RemediationAction, SkillInvalidityKind
from ._type_skill_semantics import SKILL_SEMANTIC_SCHEMA_VERSION

__all__ = [
    "OUTPUT_DISCIPLINE_POLICY_VERSION",
    "OUTPUT_DISCIPLINE_BLOCK",
    "OUTPUT_DISCIPLINE_BLOCK_SHA256",
    "OUTPUT_DISCIPLINE_COMBINED_SHA256",
    "OUTPUT_DISCIPLINE_DIGEST",
    "OUTPUT_DISCIPLINE_REQUIRED_SKILLS",
    "RETIRED_SKILL_NAMES",
    "RETIRED_AGENT_NAMES",
    "RETIRED_INSTALL_ARTIFACT_SHAPES",
    "RetiredArtifactShape",
    "SkillContractRemediationDef",
    "SKILL_CONTRACT_REMEDIATIONS",
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
    "SkillFamilyDef",
    "GITHUB_API_SKILL_FAMILIES",
    "CODEX_ACTIVE_VIEWS_SUBDIR",
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


class SkillContractRemediationDef(NamedTuple):
    """One SkillInvalidityKind's forcing-function remediation declaration.

    Modeled on ``RetiredArtifactShape``: a new validation cannot ship without
    registering how pre-existing artifacts that now fail it are handled.
    ``DETERMINISTIC`` kinds must be handled by ``SkillMigrationAdapter``;
    ``ADVISORY`` kinds only ever surface ``hint`` to an operator.
    """

    kind: SkillInvalidityKind
    introduced_in: str
    action: RemediationAction
    hint: str


# Append-only, exactly like RETIRED_INSTALL_ARTIFACT_SHAPES: every member of
# SkillInvalidityKind must have an entry here, enforced by a guard test in
# tests/contracts/. The resolver (workspace/skills.py) renders `hint` into
# SkillExclusion records, composition-root warnings, and doctor findings;
# migration/engine.py's SkillMigrationAdapter renders every DETERMINISTIC
# entry into an actual frontmatter rewrite.
_SKILL_CONTRACT_REMEDIATION_DEFS = (
    SkillContractRemediationDef(
        kind=SkillInvalidityKind.FRONTMATTER_PARSE,
        introduced_in="0.10.929",
        action=RemediationAction.ADVISORY,
        hint="fix the YAML frontmatter parse error named in the detail message",
    ),
    SkillContractRemediationDef(
        kind=SkillInvalidityKind.FIELD_SHAPE,
        introduced_in="0.10.929",
        action=RemediationAction.ADVISORY,
        hint=("change the offending frontmatter field to a YAML list, e.g. 'categories: [tag]'"),
    ),
    SkillContractRemediationDef(
        kind=SkillInvalidityKind.EXPLORATION_CONTRACT_INVALID,
        introduced_in="0.10.931",
        action=RemediationAction.ADVISORY,
        hint=(
            "move exploration vectors to a valid exploration.yaml sidecar and ensure "
            "its declarations match the SKILL.md exploration-vector markers"
        ),
    ),
    SkillContractRemediationDef(
        kind=SkillInvalidityKind.RESERVED_FIELD,
        introduced_in="0.10.929",
        action=RemediationAction.ADVISORY,
        hint=(
            "remove 'canonical_content'/'canonical_digest' from frontmatter — "
            "these are source-derived and must not be supplied"
        ),
    ),
    SkillContractRemediationDef(
        kind=SkillInvalidityKind.UNKNOWN_CAPABILITY,
        introduced_in="0.10.929",
        action=RemediationAction.ADVISORY,
        hint=(
            "remove the unrecognized capability name from 'uses_capabilities:', or "
            "move the skill to an execution role permitted to declare it"
        ),
    ),
    SkillContractRemediationDef(
        kind=SkillInvalidityKind.UNDECLARED_CAPABILITY,
        introduced_in="0.10.929",
        action=RemediationAction.DETERMINISTIC,
        hint=("add the missing capability name(s) to 'uses_capabilities:' in the frontmatter"),
    ),
    SkillContractRemediationDef(
        kind=SkillInvalidityKind.SEMANTIC_UNDECLARED_TOKENS,
        introduced_in="0.10.929",
        action=RemediationAction.DETERMINISTIC,
        hint=(
            "add a 'semantic_version'/'semantic_requirements' declaration covering "
            "the detected portable-execution tokens"
        ),
    ),
    SkillContractRemediationDef(
        kind=SkillInvalidityKind.SEMANTIC_MISSING_VERSION,
        introduced_in="0.10.929",
        action=RemediationAction.DETERMINISTIC,
        hint=(
            f"add 'semantic_version: {SKILL_SEMANTIC_SCHEMA_VERSION}' alongside the "
            "existing semantic_requirements"
        ),
    ),
    SkillContractRemediationDef(
        kind=SkillInvalidityKind.SEMANTIC_VERSION_MISMATCH,
        introduced_in="0.10.929",
        action=RemediationAction.ADVISORY,
        hint=(
            "update semantic_requirements to the current schema and bump "
            f"semantic_version to {SKILL_SEMANTIC_SCHEMA_VERSION}"
        ),
    ),
    SkillContractRemediationDef(
        kind=SkillInvalidityKind.SEMANTIC_PLAN_INVALID,
        introduced_in="0.10.929",
        action=RemediationAction.ADVISORY,
        hint="fix the malformed semantic_requirements mapping named in the detail message",
    ),
)
SKILL_CONTRACT_REMEDIATIONS: Mapping[SkillInvalidityKind, SkillContractRemediationDef] = (
    MappingProxyType(
        {definition.kind: definition for definition in _SKILL_CONTRACT_REMEDIATION_DEFS}
    )
)

if len(SKILL_CONTRACT_REMEDIATIONS) != len(_SKILL_CONTRACT_REMEDIATION_DEFS):
    raise AssertionError("Skill contract remediation definitions must have unique kinds")

_UNREGISTERED_INVALIDITY_KINDS = sorted(
    set(SkillInvalidityKind) - set(SKILL_CONTRACT_REMEDIATIONS)
)
if _UNREGISTERED_INVALIDITY_KINDS:
    raise AssertionError(
        "Every SkillInvalidityKind must have a SKILL_CONTRACT_REMEDIATIONS entry. "
        f"Missing: {_UNREGISTERED_INVALIDITY_KINDS}"
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
SESSION_ADD_DIR_SUBDIR: str = "add-dir"

RECIPE_EXECUTION_ATTESTATION_MISSING_MESSAGE: str = (
    "an active recipe requires recipe_execution_id and invocation_template_digest; "
    "take both from the recipe_execution block of the complete_recipe_initialization "
    "receipt (bounded delivery) or of the open_kitchen response (inline delivery), "
    "using invocation_template_digests[step_name] for this step"
)

RECIPE_EXECUTION_INACTIVE_MESSAGE: str = (
    "no recipe execution is installed in this server process, so this call cannot claim "
    "recipe attestation; a reloaded session has no execution — open the kitchen and "
    "complete_recipe_initialization to establish one, or drop the attestation arguments "
    "to run this skill without a recipe"
)
