"""Canonical hook registry — single source of truth for all hook definitions.

Both hooks.json (plugin manifest) and _hooks.py (settings.json registration)
derive from this registry.
"""

from __future__ import annotations

import hashlib
import json
import re
import shlex
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, NamedTuple

from autoskillit.core import installed_plugin_cache_dir, pkg_root


@dataclass(frozen=True, slots=True)
class HookDef:
    """A single hook group: event type, matcher pattern, and ordered script list."""

    matcher: str = ""
    event_type: Literal["PreToolUse", "PostToolUse", "SessionStart"] = "PreToolUse"
    scripts: list[str] = field(default_factory=list)
    timeout_seconds: int | None = None
    session_scope: Literal["any", "headless_only", "interactive_only"] = "any"
    exempt_skills: frozenset[str] = field(default_factory=frozenset)
    exempt_session_types: frozenset[str] = field(default_factory=frozenset)
    codex_status: Literal["works-as-is", "degraded", "fix-required", "not-applicable"] = (
        "works-as-is"
    )
    mechanism: Literal[
        "deny",
        "additionalContext",
        "output-rewrite",
        "input-rewrite",
        "side-effect",
    ] = "deny"
    enforcement_strength: dict[str, str] = field(default_factory=dict)
    produces_resources: frozenset[str] = field(default_factory=frozenset)
    reclaims_resources: frozenset[str] = field(default_factory=frozenset)
    self_reclaims_resources: frozenset[str] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        if self.event_type != "SessionStart" and not self.matcher:
            raise ValueError(
                f"HookDef with event_type={self.event_type!r} requires a non-empty matcher"
            )
        for field_name in (
            "produces_resources",
            "reclaims_resources",
            "self_reclaims_resources",
        ):
            resources = getattr(self, field_name)
            if not isinstance(resources, frozenset) or any(
                not isinstance(resource, str) or not resource for resource in resources
            ):
                raise ValueError(f"HookDef.{field_name} must be a frozenset of non-empty strings")


@dataclass(frozen=True, slots=True)
class LifecycleContractDef:
    """Static ownership contract for a hook-produced persistent resource."""

    resource: str
    producer_script: str
    backend: Literal["claude_code", "codex"]
    session_scope: Literal["any", "headless_only", "interactive_only"]
    required_owner_roles: frozenset[Literal["same_runner", "session_start"]]

    def __post_init__(self) -> None:
        if not isinstance(self.resource, str) or not self.resource:
            raise ValueError("LifecycleContractDef.resource must be non-empty")
        if not isinstance(self.producer_script, str) or not self.producer_script:
            raise ValueError("LifecycleContractDef.producer_script must be non-empty")
        if self.backend not in ("claude_code", "codex"):
            raise ValueError("LifecycleContractDef.backend is invalid")
        if self.session_scope not in ("any", "headless_only", "interactive_only"):
            raise ValueError("LifecycleContractDef.session_scope is invalid")
        if not isinstance(self.required_owner_roles, frozenset) or not (self.required_owner_roles):
            raise ValueError("LifecycleContractDef.required_owner_roles must be non-empty")
        if not self.required_owner_roles <= {"same_runner", "session_start"}:
            raise ValueError("LifecycleContractDef.required_owner_roles contains an invalid role")


# ---------------------------------------------------------------------------
# Codex Compatibility Table
#
# Status values:
#   works-as-is    — Hook works correctly under Codex without changes.
#   degraded       — Hook runs but with reduced functionality under Codex.
#   fix-required   — Hook needs code changes for Codex compatibility.
#   not-applicable — Hook targets a tool/event that Codex does not support.
#
# Hook (primary script)                  | Status
# ---------------------------------------|----------------
# skill_cmd_guard (+ quota, skill_cmd)   | works-as-is
# remove_clone_guard                     | works-as-is
# open_kitchen_guard                     | works-as-is
# ask_user_question_guard                | not-applicable
# branch_protection_guard (merge)        | works-as-is
# branch_protection_guard (push)         | works-as-is
# unsafe_install_guard                   | works-as-is
# pr_create_guard                        | works-as-is
# compose_pr_body_guard                  | works-as-is
# planner_gh_discovery_guard             | works-as-is
# artifact_download_guard                | works-as-is
# git_ops_guard                          | works-as-is
# test_runner_guard                      | works-as-is
# shell_capture_hook                     | works-as-is (Codex-only input-rewrite, #4286/ADR-0006)
# generated_file_write_guard             | works-as-is
# write_guard                            | works-as-is
# planner_result_naming_guard            | works-as-is
# recipe_write_advisor                   | works-as-is
# grep_pattern_lint_guard               | not-applicable
# mcp_health_advisor                     | degraded
# skill_orchestration_guard              | works-as-is
# background_exec_guard                  | works-as-is
# fleet_dispatch_guard (+ resume_own. + fleet_claim)   | works-as-is
# pretty_output_hook                     | works-as-is
# token_summary_hook (+ quota_post)       | works-as-is
# review_gate_post_hook                  | works-as-is
# resume_gate_post_hook                  | works-as-is
# recipe_confirmed_post_hook             | works-as-is
# quota_guard_state_post_hook            | works-as-is
# lint_after_edit_hook                   | degraded
# skill_load_post_hook                   | not-applicable
# skill_load_guard                       | works-as-is
# review_loop_gate                       | works-as-is
# reset_resume_gate                      | works-as-is
# fabricated_completion_guard            | works-as-is
# capture_lifecycle_hook                 | works-as-is
# session_start_hook                     | works-as-is
# ---------------------------------------------------------------------------

HOOK_REGISTRY: list[HookDef] = [
    HookDef(
        matcher=r".*",
        scripts=["guards/fabricated_completion_guard.py"],
        session_scope="headless_only",
        mechanism="deny",
        enforcement_strength={"claude_code": "soft", "codex": "works-as-is"},
    ),
    HookDef(
        matcher="mcp__.*autoskillit.*__run_skill.*",
        scripts=[
            "guards/skill_cmd_guard.py",
            "guards/quota_guard.py",
            "guards/skill_command_guard.py",
            "guards/ingredient_lock_guard.py",
            "guards/pipeline_step_guard.py",
        ],
        mechanism="deny",
        enforcement_strength={"claude_code": "soft", "codex": "works-as-is"},
    ),
    HookDef(
        matcher="mcp__.*autoskillit.*__remove_clone",
        scripts=["guards/remove_clone_guard.py"],
        mechanism="deny",
        enforcement_strength={"claude_code": "soft", "codex": "works-as-is"},
    ),
    HookDef(
        matcher=r"mcp__.*autoskillit.*__open_kitchen.*",
        scripts=["guards/open_kitchen_guard.py"],
        timeout_seconds=5,
        mechanism="deny",
        enforcement_strength={"claude_code": "soft", "codex": "works-as-is"},
    ),
    HookDef(
        matcher="AskUserQuestion",
        scripts=["guards/ask_user_question_guard.py"],
        timeout_seconds=5,
        session_scope="headless_only",
        codex_status="not-applicable",
        mechanism="deny",
        enforcement_strength={"claude_code": "hard", "codex": "not-applicable"},
    ),
    HookDef(
        matcher=r"mcp__.*autoskillit.*__merge_worktree",
        scripts=["guards/branch_protection_guard.py"],
        mechanism="deny",
        enforcement_strength={"claude_code": "soft", "codex": "works-as-is"},
    ),
    HookDef(
        matcher=r"mcp__.*autoskillit.*__push_to_remote",
        scripts=["guards/branch_protection_guard.py"],
        mechanism="deny",
        enforcement_strength={"claude_code": "soft", "codex": "works-as-is"},
    ),
    HookDef(
        matcher=r"Bash|mcp__.*autoskillit.*__run_cmd",
        scripts=["guards/unsafe_install_guard.py"],
        mechanism="deny",
        enforcement_strength={"claude_code": "soft", "codex": "works-as-is"},
    ),
    HookDef(
        matcher=r"Bash|mcp__.*autoskillit.*__run_cmd",
        scripts=["guards/github_mutation_guard.py"],
        mechanism="deny",
        enforcement_strength={"claude_code": "hard", "codex": "works-as-is"},
    ),
    HookDef(
        matcher=r"Bash|mcp__.*autoskillit.*__run_cmd",
        scripts=["guards/pr_create_guard.py"],
        # Must stay in sync with _EXEMPT_SKILLS in guards/pr_create_guard.py —
        # stdlib-only boundary on hook scripts prevents a shared import.
        exempt_skills=frozenset(
            {
                "compose-pr",
                "compose-research-pr",
                "open-integration-pr",
                "promote-to-main",
                "pipeline-summary",
            }
        ),
        # Must stay in sync with _EXEMPT_SESSION_TYPES in guards/pr_create_guard.py —
        # stdlib-only boundary on hook scripts prevents a shared import.
        exempt_session_types=frozenset({"orchestrator"}),
        mechanism="deny",
        enforcement_strength={"claude_code": "soft", "codex": "works-as-is"},
    ),
    HookDef(
        matcher=r"Bash|mcp__.*autoskillit.*__run_cmd",
        scripts=["guards/compose_pr_body_guard.py"],
        session_scope="headless_only",
        mechanism="deny",
        enforcement_strength={"claude_code": "soft", "codex": "works-as-is"},
    ),
    HookDef(
        matcher=r"Bash|mcp__.*autoskillit.*__run_cmd",
        scripts=["guards/planner_gh_discovery_guard.py"],
        session_scope="headless_only",
        mechanism="deny",
        enforcement_strength={"claude_code": "soft", "codex": "works-as-is"},
    ),
    HookDef(
        matcher=r"Bash|mcp__.*autoskillit.*__run_cmd",
        scripts=["guards/artifact_download_guard.py"],
        mechanism="deny",
        enforcement_strength={"claude_code": "soft", "codex": "works-as-is"},
    ),
    HookDef(
        matcher=r"Bash|mcp__.*autoskillit.*__run_cmd",
        scripts=["guards/git_ops_guard.py"],
        session_scope="headless_only",
        # Must stay in sync with _EXEMPT_SESSION_TYPES in guards/git_ops_guard.py —
        # stdlib-only boundary on hook scripts prevents a shared import.
        exempt_session_types=frozenset({"orchestrator"}),
        mechanism="deny",
        enforcement_strength={"claude_code": "soft", "codex": "works-as-is"},
    ),
    HookDef(
        matcher=r"Bash|mcp__.*autoskillit.*__run_cmd",
        scripts=["guards/test_runner_guard.py"],
        session_scope="headless_only",
        # Must stay in sync with _EXEMPT_SKILLS in guards/test_runner_guard.py —
        # stdlib-only boundary on hook scripts prevents a shared import.
        exempt_skills=frozenset({"implement-experiment"}),
        mechanism="deny",
        enforcement_strength={"claude_code": "soft", "codex": "works-as-is"},
    ),
    HookDef(
        matcher=r"Bash",
        scripts=["shell_capture_hook.py"],
        timeout_seconds=5,
        session_scope="any",
        codex_status="works-as-is",
        mechanism="input-rewrite",
        # In-script Codex gate (#4286 / ADR-0006): exits 0 on non-Codex sessions.
        # Matcher excludes mcp__.*run_cmd — that channel is lossless server-side.
        enforcement_strength={"claude_code": "not-applicable", "codex": "works-as-is"},
        produces_resources=frozenset({"shell-captures"}),
        reclaims_resources=frozenset({"shell-captures"}),
        self_reclaims_resources=frozenset({"shell-captures"}),
    ),
    HookDef(
        matcher=r"Write|Edit",
        scripts=["guards/generated_file_write_guard.py"],
        mechanism="deny",
        enforcement_strength={"claude_code": "hard", "codex": "works-as-is"},
    ),
    HookDef(
        matcher=r"Write|Edit|Bash|mcp__.*autoskillit.*__run_cmd",
        scripts=["guards/write_guard.py"],
        session_scope="headless_only",
        mechanism="deny",
        enforcement_strength={"claude_code": "soft", "codex": "works-as-is"},
    ),
    HookDef(
        matcher=r"Write|Edit",
        scripts=["guards/planner_result_naming_guard.py"],
        session_scope="headless_only",
        mechanism="deny",
        enforcement_strength={"claude_code": "hard", "codex": "works-as-is"},
    ),
    HookDef(
        matcher=r"Write|Edit",
        scripts=["guards/recipe_write_advisor.py"],
        session_scope="interactive_only",
        mechanism="deny",
        enforcement_strength={"claude_code": "hard", "codex": "works-as-is"},
    ),
    HookDef(
        matcher=r"Grep",
        scripts=["guards/grep_pattern_lint_guard.py"],
        codex_status="not-applicable",
        mechanism="deny",
        enforcement_strength={"claude_code": "hard", "codex": "not-applicable"},
    ),
    HookDef(
        matcher=r"Bash|Write|Edit|Read|Glob|Grep",
        scripts=["guards/mcp_health_advisor.py"],
        timeout_seconds=5,
        session_scope="interactive_only",
        codex_status="degraded",
        mechanism="deny",
        enforcement_strength={"claude_code": "hard", "codex": "degraded"},
    ),
    HookDef(
        matcher=r"mcp__.*autoskillit.*__(run_skill|run_cmd|run_python).*",
        scripts=["guards/skill_orchestration_guard.py"],
        session_scope="headless_only",
        mechanism="deny",
        enforcement_strength={"claude_code": "soft", "codex": "works-as-is"},
    ),
    HookDef(
        matcher=r"Bash|mcp__.*autoskillit.*__(run_cmd|run_python)",
        scripts=["guards/recipe_read_guard.py"],
        session_scope="headless_only",
        mechanism="deny",
        enforcement_strength={"claude_code": "soft", "codex": "works-as-is"},
    ),
    HookDef(
        matcher=r"Bash|Agent",
        scripts=["guards/background_exec_guard.py"],
        session_scope="headless_only",
        mechanism="deny",
        enforcement_strength={"claude_code": "hard", "codex": "works-as-is"},
    ),
    HookDef(
        matcher=r"(mcp__.*autoskillit.*__)?dispatch_food_truck",
        scripts=[
            "guards/fleet_dispatch_guard.py",
            "guards/resume_ownership_guard.py",
            "guards/ingredient_lock_guard.py",
            "guards/fleet_claim_guard.py",
        ],
        mechanism="deny",
        enforcement_strength={"claude_code": "soft", "codex": "works-as-is"},
    ),
    HookDef(
        event_type="PostToolUse",
        matcher="mcp__.*autoskillit.*",
        scripts=["formatters/pretty_output_hook.py"],
        mechanism="output-rewrite",
        enforcement_strength={"claude_code": "soft", "codex": "works-as-is"},
    ),
    HookDef(
        event_type="PostToolUse",
        matcher=r"mcp__.*autoskillit.*__run_skill.*",
        scripts=[
            "token_summary_hook.py",
            "quota_post_hook.py",
            "recipe_confirmed_post_hook.py",
        ],
        mechanism="output-rewrite",
        enforcement_strength={"claude_code": "soft", "codex": "works-as-is"},
    ),
    HookDef(
        event_type="PostToolUse",
        matcher=r"mcp__.*autoskillit.*__(disable_quota_guard|close_kitchen).*",
        scripts=["quota_guard_state_post_hook.py"],
        mechanism="output-rewrite",
        enforcement_strength={"claude_code": "soft", "codex": "works-as-is"},
    ),
    HookDef(
        event_type="PostToolUse",
        matcher=r"mcp__.*autoskillit.*__(run_skill|run_python).*",
        scripts=["review_gate_post_hook.py"],
        mechanism="output-rewrite",
        enforcement_strength={"claude_code": "soft", "codex": "works-as-is"},
    ),
    HookDef(
        event_type="PostToolUse",
        matcher=r"(mcp__.*autoskillit.*__)?dispatch_food_truck",
        scripts=["resume_gate_post_hook.py"],
        mechanism="output-rewrite",
        enforcement_strength={"claude_code": "soft", "codex": "works-as-is"},
    ),
    HookDef(
        event_type="PostToolUse",
        matcher=r"Write|Edit",
        scripts=["lint_after_edit_hook.py"],
        session_scope="headless_only",
        codex_status="degraded",
        mechanism="output-rewrite",
        enforcement_strength={"claude_code": "hard", "codex": "degraded"},
    ),
    HookDef(
        event_type="PostToolUse",
        matcher="Skill",
        scripts=["skill_load_post_hook.py"],
        codex_status="not-applicable",
        mechanism="additionalContext",
        enforcement_strength={"claude_code": "hard", "codex": "not-applicable"},
    ),
    HookDef(
        matcher=r"Read|Write|Edit|Bash|Grep|Glob",
        scripts=["guards/skill_load_guard.py"],
        session_scope="headless_only",
        codex_status="works-as-is",
        mechanism="deny",
        enforcement_strength={"claude_code": "hard", "codex": "works-as-is"},
    ),
    HookDef(
        matcher=r"mcp__.*autoskillit.*__(wait_for_ci|enqueue_pr)",
        scripts=["guards/review_loop_gate.py"],
        mechanism="deny",
        enforcement_strength={"claude_code": "soft", "codex": "works-as-is"},
    ),
    HookDef(
        matcher=r"(mcp__.*autoskillit.*__)?reset_dispatch",
        scripts=["guards/reset_resume_gate.py"],
        mechanism="deny",
        enforcement_strength={"claude_code": "soft", "codex": "works-as-is"},
    ),
    HookDef(
        event_type="SessionStart",
        scripts=["capture_lifecycle_hook.py"],
        timeout_seconds=2,
        session_scope="any",
        codex_status="works-as-is",
        mechanism="side-effect",
        enforcement_strength={"claude_code": "soft", "codex": "works-as-is"},
        reclaims_resources=frozenset({"shell-captures"}),
    ),
    HookDef(
        event_type="SessionStart",
        scripts=["session_start_hook.py"],
        session_scope="interactive_only",
        mechanism="additionalContext",
        enforcement_strength={"claude_code": "soft", "codex": "works-as-is"},
    ),
]

LIFECYCLE_CONTRACTS: tuple[LifecycleContractDef, ...] = (
    LifecycleContractDef(
        resource="shell-captures",
        producer_script="shell_capture_hook.py",
        backend="codex",
        session_scope="any",
        required_owner_roles=frozenset({"same_runner", "session_start"}),
    ),
)

HOOKS_DIR: Path = pkg_root() / "hooks"
"""Source hooks used by machine-local settings and development checks."""

PLUGIN_ROOT_TOKEN = "${CLAUDE_PLUGIN_ROOT}"
"""Claude Code's runtime root for the plugin supplying ``hooks.json``."""


RETIRED_SCRIPT_BASENAMES: frozenset[str] = frozenset(
    {
        "quota_check.py",
        "skill_cmd_check.py",
        "quota_post_check.py",
        "pretty_output.py",
        "token_summary_appender.py",
        "session_start_reminder.py",
        "headless_orchestration_guard.py",
        "franchise_dispatch_guard.py",
        "ask_user_question_guard.py",
        "branch_protection_guard.py",
        "fleet_dispatch_guard.py",
        "generated_file_write_guard.py",
        "grep_pattern_lint_guard.py",
        "leaf_orchestration_guard.py",
        "mcp_health_guard.py",
        "open_kitchen_guard.py",
        "planner_gh_discovery_guard.py",
        "pr_create_guard.py",
        "quota_guard.py",
        "recipe_write_advisor.py",
        "remove_clone_guard.py",
        "review_loop_gate.py",
        "skill_cmd_guard.py",
        "skill_command_guard.py",
        "unsafe_install_guard.py",
        "write_guard.py",
        "pretty_output_hook.py",
        "output_budget_guard.py",
        "pipeline_step_post_hook.py",
        # Append any future retired basenames here, atomically with the rename commit.
    }
)

# Basenames of scripts added directly to a subdirectory without ever having a flat path.
# Add the basename here when introducing a new subdir script that was never previously flat,
# so test_moved_scripts_must_be_in_retired does not false-positive on it.
NEW_SUBDIR_BASENAMES: frozenset[str] = frozenset(
    {
        "mcp_health_advisor.py",
        "skill_orchestration_guard.py",
        "skill_load_guard.py",
        "resume_ownership_guard.py",
        "planner_result_naming_guard.py",
        "artifact_download_guard.py",
        "ingredient_lock_guard.py",  # NEW (#3357)
        "background_exec_guard.py",
        "pipeline_step_guard.py",
        "git_ops_guard.py",
        "compose_pr_body_guard.py",
        "test_runner_guard.py",
        "fleet_claim_guard.py",
        "reset_resume_gate.py",
        "recipe_read_guard.py",
        "github_mutation_guard.py",
        "fabricated_completion_guard.py",
    }
)

# Guards that fail-closed for valid-but-unrecognized input, as a defense-in-depth
# measure against privilege escalation (garbage-in still fails open everywhere).
# Mirrored in src/autoskillit/hooks/guards/AGENTS.md and docs/safety/hooks.md —
# tests/docs/test_guard_fail_mode_docs.py and tests/arch/test_fail_closed_guard_contract.py
# enforce both the doc listing and a standing false-positive-corpus obligation
# for every member.
FAIL_CLOSED_GUARD_BASENAMES: frozenset[str] = frozenset(
    {
        "skill_command_guard.py",
        "open_kitchen_guard.py",
        "skill_orchestration_guard.py",
        "background_exec_guard.py",
        "github_mutation_guard.py",
    }
)

# Risky gh CLI subcommand pairs that MUST have PreToolUse guard coverage.
# test_risky_gh_subcommand_coverage.py enforces that every pair here is
# detected by at least one command-inspecting guard registered under a
# Bash|run_cmd matcher. Add new pairs when threat modeling identifies
# risky gh subcommands; the coverage test will fail until a guard exists.
RISKY_GH_SUBCOMMANDS: frozenset[tuple[str, str]] = frozenset(
    {
        ("run", "download"),
        ("release", "download"),
        ("pr", "create"),
    }
)

# Risky raw git CLI operations that MUST have PreToolUse guard coverage.
# test_risky_git_ops_coverage.py enforces that every tuple here is
# detected by at least one command-inspecting guard registered under a
# Bash|run_cmd matcher. Add new tuples when threat modeling identifies
# risky git operations; the coverage test will fail until a guard exists.
RISKY_GIT_OPERATIONS: frozenset[tuple[str, ...]] = frozenset(
    {
        ("commit", "--amend"),
        ("push", "--force"),
        ("push", "-f"),
        ("push", "--force-with-lease"),
        ("reset", "--hard"),
        ("clean", "-f"),
        ("clean", "-fd"),
        ("checkout", "."),
        ("checkout", "--", "."),
    }
)


def hook_applies_to_backend(
    hook_def: HookDef,
    *,
    backend: Literal["claude_code", "codex"],
    session_scope: Literal["headless", "interactive"],
) -> bool:
    """Return whether a hook is reachable for one deployed backend/session scope."""
    if backend not in ("claude_code", "codex"):
        raise ValueError(f"unsupported hook backend: {backend!r}")
    if session_scope not in ("headless", "interactive"):
        raise ValueError(f"unsupported hook session scope: {session_scope!r}")
    match backend:
        case "codex":
            if hook_def.codex_status in {
                "fix-required",
                "not-applicable",
            }:
                return False
            return hook_def.session_scope == "any" or (
                session_scope == "headless"
                and hook_def.session_scope == "headless_only"
                or session_scope == "interactive"
                and hook_def.session_scope == "interactive_only"
            )
        case "claude_code":
            if hook_def.enforcement_strength.get("claude_code") == "not-applicable":
                return False
            return hook_def.session_scope == "any" or (
                session_scope == "headless"
                and hook_def.session_scope == "headless_only"
                or session_scope == "interactive"
                and hook_def.session_scope == "interactive_only"
            )


def _contract_session_scopes(
    contract: LifecycleContractDef,
) -> tuple[Literal["headless", "interactive"], ...]:
    if contract.session_scope == "headless_only":
        return ("headless",)
    if contract.session_scope == "interactive_only":
        return ("interactive",)
    return ("headless", "interactive")


def validate_lifecycle_contracts(
    registry: Sequence[HookDef],
    lifecycle_contracts: Sequence[LifecycleContractDef],
    *,
    backend: Literal["claude_code", "codex"],
) -> None:
    """Fail closed when a deployed producer loses a required cleanup owner."""
    contract_keys = {
        (contract.resource, contract.producer_script, contract.backend)
        for contract in lifecycle_contracts
    }
    for hook_def in registry:
        for resource in hook_def.produces_resources:
            reachable = hook_applies_to_backend(
                hook_def,
                backend=backend,
                session_scope="headless",
            ) or hook_applies_to_backend(
                hook_def,
                backend=backend,
                session_scope="interactive",
            )
            if reachable and not any(
                (resource, producer_script, backend) in contract_keys
                for producer_script in hook_def.scripts
            ):
                raise ValueError(f"persistent resource {resource!r} has no lifecycle contract")

    applicable_contracts = [
        contract for contract in lifecycle_contracts if contract.backend == backend
    ]
    for contract in applicable_contracts:
        producers = [
            hook_def
            for hook_def in registry
            if contract.producer_script in hook_def.scripts
            and contract.resource in hook_def.produces_resources
        ]
        if len(producers) != 1:
            raise ValueError(
                f"lifecycle producer {contract.producer_script!r} for "
                f"{contract.resource!r} must resolve exactly once"
            )
        producer = producers[0]
        if producer.session_scope != contract.session_scope:
            raise ValueError(
                f"lifecycle producer {contract.producer_script!r} scope "
                f"{producer.session_scope!r} does not match contract "
                f"{contract.session_scope!r}"
            )

        for session_scope in _contract_session_scopes(contract):
            if not hook_applies_to_backend(
                producer,
                backend=backend,
                session_scope=session_scope,
            ):
                raise ValueError(
                    f"lifecycle producer {contract.producer_script!r} is not applicable "
                    f"to {backend}/{session_scope}"
                )
            if "same_runner" in contract.required_owner_roles and not (
                contract.resource in producer.reclaims_resources
                and contract.resource in producer.self_reclaims_resources
            ):
                raise ValueError(
                    f"lifecycle resource {contract.resource!r} has no same-runner owner "
                    f"for {backend}/{session_scope}"
                )
            if "session_start" in contract.required_owner_roles:
                session_start_owners = [
                    hook_def
                    for hook_def in registry
                    if hook_def.event_type == "SessionStart"
                    and contract.resource in hook_def.reclaims_resources
                    and hook_applies_to_backend(
                        hook_def,
                        backend=backend,
                        session_scope=session_scope,
                    )
                ]
                if not session_start_owners:
                    raise ValueError(
                        f"lifecycle resource {contract.resource!r} has no SessionStart "
                        f"owner for {backend}/{session_scope}"
                    )


def _canonical_registry_payload(
    registry: Sequence[HookDef],
    retired: frozenset[str],
    lifecycle_contracts: Sequence[LifecycleContractDef],
) -> str:
    registry_rows = sorted(
        [
            {
                "codex_status": h.codex_status,
                "enforcement_strength": dict(sorted(h.enforcement_strength.items())),
                "event_type": h.event_type,
                "exempt_session_types": sorted(h.exempt_session_types),
                "exempt_skills": sorted(h.exempt_skills),
                "matcher": h.matcher,
                "mechanism": h.mechanism,
                "produces_resources": sorted(h.produces_resources),
                "reclaims_resources": sorted(h.reclaims_resources),
                "scripts": list(h.scripts),
                "self_reclaims_resources": sorted(h.self_reclaims_resources),
                "session_scope": h.session_scope,
                "timeout_seconds": h.timeout_seconds,
            }
            for h in registry
        ],
        key=lambda row: (row["event_type"], row["matcher"], tuple(row["scripts"])),  # type: ignore[arg-type]
    )
    lifecycle_rows = sorted(
        [
            {
                "backend": contract.backend,
                "producer_script": contract.producer_script,
                "required_owner_roles": sorted(contract.required_owner_roles),
                "resource": contract.resource,
                "session_scope": contract.session_scope,
            }
            for contract in lifecycle_contracts
        ],
        key=lambda row: (
            row["resource"],
            row["producer_script"],
            row["backend"],
            row["session_scope"],
        ),
    )
    return json.dumps(
        {
            "format_version": 4,
            "lifecycle_contracts": lifecycle_rows,
            "registry": registry_rows,
            "retired": sorted(retired),
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def compute_registry_hash(
    registry: Sequence[HookDef],
    retired: frozenset[str],
    lifecycle_contracts: Sequence[LifecycleContractDef],
) -> str:
    """Compute a stable sha256 over the hook and lifecycle registries."""
    payload = _canonical_registry_payload(registry, retired, lifecycle_contracts)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


HOOK_REGISTRY_HASH: str = compute_registry_hash(
    HOOK_REGISTRY,
    RETIRED_SCRIPT_BASENAMES,
    LIFECYCLE_CONTRACTS,
)


def load_hooks_json_hash(path: Path) -> str | None:
    """Read the _autoskillit_registry_hash from a hooks.json file."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        val = data.get("_autoskillit_registry_hash")
        return str(val) if val else None
    except (OSError, json.JSONDecodeError, AttributeError):
        return None


def _build_hook_entry(hook_def: HookDef, hook_commands: list[dict]) -> dict:
    """Build the per-entry dict for a hook definition.

    SessionStart entries omit the 'matcher' key; all others include it.
    This is the single authoritative formatter for both hooks.json and
    settings.json generation.
    """
    if hook_def.event_type == "SessionStart":
        return {"hooks": hook_commands}
    return {"matcher": hook_def.matcher, "hooks": hook_commands}


def _build_hook_command(
    hooks_dir: Path | None,
    script: str,
    timeout_seconds: int | None,
    *,
    relocatable: bool = False,
) -> dict:
    """Build a single hook command dict using the stable dispatcher format.

    Two explicit modes — no implicit default silently decides the destination:

    - ``relocatable=True`` (hooks.json only): emits the quoted
      ``PLUGIN_ROOT_TOKEN`` form, expanded by Claude Code at hook-invocation
      time against the plugin version that supplied the file. ``hooks_dir``
      is ignored and may be ``None``.
    - ``relocatable=False`` (settings.json only, dev-mode, machine-local):
      bakes the caller-supplied absolute ``hooks_dir``. ``hooks_dir`` is
      required.
    """
    logical_name = script.removesuffix(".py")
    if relocatable:
        command = render_relocatable_hook_command(logical_name)
    else:
        if hooks_dir is None:
            raise ValueError("hooks_dir is required when relocatable=False")
        command = f"python3 -B {hooks_dir / '_dispatch.py'} {logical_name}"
    cmd: dict = {
        "type": "command",
        "command": command,
    }
    if timeout_seconds is not None:
        cmd["timeout"] = timeout_seconds
    return cmd


_LOGICAL_HOOK_COMPONENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*")


def render_relocatable_hook_command(logical_name: str) -> str:
    """Render one validated dispatcher command for a plugin hooks artifact."""
    logical_name = logical_name.removesuffix(".py").strip("/")
    components = logical_name.split("/")
    if not logical_name or any(
        _LOGICAL_HOOK_COMPONENT.fullmatch(component) is None for component in components
    ):
        raise ValueError(f"invalid logical hook name: {logical_name!r}")
    return f'python3 -B "{PLUGIN_ROOT_TOKEN}/hooks/_dispatch.py" {shlex.quote(logical_name)}'


def generate_hooks_json(
    registry: Sequence[HookDef] = HOOK_REGISTRY,
    lifecycle_contracts: Sequence[LifecycleContractDef] = LIFECYCLE_CONTRACTS,
) -> dict:
    """Generate the hooks.json structure from HOOK_REGISTRY using the stable dispatcher.

    Multiple HookDef entries with the same (event_type, matcher) are consolidated
    into a single settings.json entry so Claude Code sees no duplicate matchers.
    """
    validate_lifecycle_contracts(
        registry,
        lifecycle_contracts,
        backend="claude_code",
    )
    # Preserve insertion order; merge scripts from same (event_type, matcher) key.
    groups: dict[tuple[str, str], dict] = {}
    for hook_def in registry:
        key = (hook_def.event_type, hook_def.matcher)
        hook_commands = [
            _build_hook_command(None, script, hook_def.timeout_seconds, relocatable=True)
            for script in hook_def.scripts
        ]
        if key not in groups:
            groups[key] = _build_hook_entry(hook_def, hook_commands)
        else:
            groups[key]["hooks"].extend(hook_commands)

    by_event: dict[str, list] = {}
    for (event_type, _), entry in groups.items():
        by_event.setdefault(event_type, []).append(entry)
    registry_hash = compute_registry_hash(
        registry,
        RETIRED_SCRIPT_BASENAMES,
        lifecycle_contracts,
    )
    return {"hooks": by_event, "_autoskillit_registry_hash": registry_hash}


# ---------------------------------------------------------------------------
# Hook diagnostic utilities — shared between cli/ and server/ (both IL-3).
# Placed here (package root, IL-0-accessible) to avoid IL-3-to-IL-3 peer imports.
# ---------------------------------------------------------------------------


def _claude_settings_path(scope: str, *, cwd: Path) -> Path:
    """Return the Claude settings path for a scope and explicit project cwd.

    Raises:
        ValueError: If ``scope`` is not ``user``, ``project``, or ``local``.
    """
    if scope == "user":
        return Path.home() / ".claude" / "settings.json"
    project_dir = Path(cwd)
    if scope == "project":
        return project_dir / ".claude" / "settings.json"
    if scope == "local":
        return project_dir / ".claude" / "settings.local.json"
    raise ValueError(f"invalid Claude settings scope: {scope!r}")


def iter_all_scope_paths(
    project_root: Path | None = None,
) -> Iterator[tuple[str, Path]]:
    """Yield (scope_label, settings_path) for all Claude Code settings scopes.

    Always yields the user scope. Project and local scopes are yielded only
    when project_root is provided AND the corresponding .claude/ directory exists.
    """
    scope_cwd = Path.cwd() if project_root is None else Path(project_root)
    yield ("user", _claude_settings_path("user", cwd=scope_cwd))
    if project_root is not None:
        claude_dir = scope_cwd / ".claude"
        if claude_dir.is_dir():
            yield ("project", _claude_settings_path("project", cwd=scope_cwd))
            local_path = _claude_settings_path("local", cwd=scope_cwd)
            if local_path.exists():
                yield ("local", local_path)


def _load_settings_data(settings_path: Path) -> dict:
    """Read and parse settings.json; return empty dict on any error."""
    if settings_path.exists():
        try:
            return json.loads(settings_path.read_text())
        except (OSError, json.JSONDecodeError):
            pass
    return {}


def canonical_script_basenames() -> frozenset[str]:
    """Return the set of all known autoskillit hook script basenames."""
    return frozenset(s for h in HOOK_REGISTRY for s in h.scripts)


def _is_own_hook(command: str) -> bool:
    """Check if a hook command belongs to autoskillit (any format)."""
    if "autoskillit" in command:
        return True
    if "_dispatch.py" in command:
        return True
    known = canonical_script_basenames() | RETIRED_SCRIPT_BASENAMES
    bare = {Path(s).name for s in known}
    return any(command.endswith(script) or f"/{script}" in command for script in known | bare)


def _extract_script_basenames(hooks_dict: dict) -> set[str]:
    """Extract autoskillit hook script relative paths from a hooks dict.

    Filters to autoskillit-owned commands only, then normalizes
    to hooks-dir-relative paths for installation-path-agnostic comparison.
    """
    result: set[str] = set()
    for event_entries in hooks_dict.values():
        if not isinstance(event_entries, list):
            continue
        for entry in event_entries:
            for hook in entry.get("hooks", []):
                cmd = hook.get("command", "")
                if not cmd or not _is_own_hook(cmd):
                    continue
                parts = cmd.split()
                if "_dispatch.py" in cmd and len(parts) >= 3:
                    logical_name = parts[-1]
                    result.add(logical_name + ".py")
                else:
                    script_path = Path(parts[-1])
                    bare = script_path.name
                    canonical = canonical_script_basenames()
                    matched = next((c for c in canonical if Path(c).name == bare), bare)
                    result.add(matched)
    return result


class HookDriftResult(NamedTuple):
    """Bidirectional hook drift counts."""

    missing: int  # canonical − deployed (hooks not yet deployed)
    orphaned: int  # deployed − canonical (ghost hooks, fatal ENOENT risk)
    orphaned_cmds: frozenset[str] = frozenset()


def _count_hook_registry_drift(settings_path: Path) -> HookDriftResult:
    """Return bidirectional hook drift counts between canonical and deployed settings.json."""
    deployed_data = _load_settings_data(settings_path)
    canonical_basenames = canonical_script_basenames()
    deployed_basenames = _extract_script_basenames(deployed_data.get("hooks", {}))
    orphaned = deployed_basenames - canonical_basenames
    return HookDriftResult(
        missing=len(canonical_basenames - deployed_basenames),
        orphaned=len(orphaned),
        orphaned_cmds=frozenset(orphaned),
    )


def find_broken_hook_scripts(
    hook_config_path: Path,
    *,
    expansion_root: Path | None = None,
) -> list[str]:
    """Return list of hook commands whose script files do not exist on disk.

    Commands are parsed with ``shlex`` (not bare ``.split()``) so the quoted
    relocatable form (``python3 "${CLAUDE_PLUGIN_ROOT}/hooks/_dispatch.py" name``)
    classifies correctly. A command containing ``PLUGIN_ROOT_TOKEN`` is
    resolved against ``expansion_root`` (the root of the artifact — e.g. the
    plugin-cache incarnation dir — that contains it) before the existence
    check. A token-bearing command with no ``expansion_root`` supplied is
    reported broken (fail-closed, never silently skipped). Commands with a
    plain absolute path (settings.json, dev-mode) are checked as before,
    independent of ``expansion_root``.
    """
    data = _load_settings_data(hook_config_path)
    broken: list[str] = []
    for event_type in ("PreToolUse", "PostToolUse", "SessionStart"):
        for entry in data.get("hooks", {}).get(event_type, []):
            for hook in entry.get("hooks", []):
                cmd = hook.get("command", "")
                if not _is_own_hook(cmd):
                    continue
                try:
                    parts = shlex.split(cmd)
                except ValueError:
                    broken.append(cmd)
                    continue
                has_dispatcher = any(part.endswith("_dispatch.py") for part in parts)
                if len(parts) >= 3 and parts[-2].endswith("_dispatch.py"):
                    script_path_str = parts[-2]
                elif has_dispatcher:
                    broken.append(cmd)
                    continue
                elif len(parts) >= 2:
                    script_path_str = parts[-1]
                else:
                    broken.append(cmd)
                    continue
                if PLUGIN_ROOT_TOKEN in script_path_str:
                    if expansion_root is None:
                        broken.append(cmd)
                        continue
                    script_path_str = script_path_str.replace(
                        PLUGIN_ROOT_TOKEN, str(expansion_root)
                    )
                    expansion_root_resolved = expansion_root.resolve()
                    script_path = Path(script_path_str).resolve()
                    if not script_path.is_relative_to(expansion_root_resolved):
                        broken.append(cmd)
                        continue
                else:
                    script_path = Path(script_path_str)
                if not script_path.is_file():
                    broken.append(cmd)
    return broken


def validate_plugin_cache_hooks(cache_dir: Path | None = None) -> list[str]:
    """Return list of broken hook commands from the plugin cache hooks.json.

    Reads each hooks.json found under cache_dir/<version>/hooks/hooks.json —
    the real installed layout (write site: ``cli/_marketplace.py``,
    ``public_plugin_root / "hooks" / "hooks.json"``) — and checks that every
    autoskillit hook script path exists on disk. Token-bearing commands are
    expanded against ``hooks_json_path.parent.parent``: the ``<version>``
    incarnation directory, which is the plugin root Claude Code binds
    ``${CLAUDE_PLUGIN_ROOT}`` to (it directly contains ``hooks/``, ``agents/``,
    ``.claude-plugin/``, ``skills/``, ``recipes/``, ``assets/``).
    """
    _cache = cache_dir or installed_plugin_cache_dir(Path.home(), "autoskillit")
    broken: list[str] = []
    if not _cache.is_dir():
        return broken
    for hooks_json_path in _cache.glob("*/hooks/hooks.json"):
        broken.extend(
            find_broken_hook_scripts(
                hooks_json_path,
                expansion_root=hooks_json_path.parent.parent,
            )
        )
    return broken
