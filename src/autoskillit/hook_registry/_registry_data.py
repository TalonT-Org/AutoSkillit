"""Canonical registry tables: HOOK_REGISTRY, LIFECYCLE_CONTRACTS, retirement sets.

The pre-decomposition ``hook_registry.py`` carried these tables at module
scope. The decomposition keeps the literal data identical — every entry is
moved verbatim, including the per-HookDef "Must stay in sync with … stdlib-only
boundary" comments (now superseded by ``_hook_constants`` imports in Step B3).

After Step B3, ``exempt_skills`` for the three guard HookDefs is sourced from
``EXEMPT_SKILLS_BY_GUARD`` (single source of truth in ``_hook_constants``),
and ``exempt_session_types`` for ``pr_create_guard`` is sourced from
``EXEMPT_SESSION_TYPES_BY_GUARD``. The carve-out for ``git_ops_guard`` keeps
its ``exempt_session_types=frozenset()`` inline (the orchestrator bypass is
script-local; see ``test_git_ops_guard_orchestrator_exemption_is_phase_local``).
"""

from __future__ import annotations

from pathlib import Path

from autoskillit.core import pkg_root

from ._hooks_defs import HookDef, LifecycleContractDef

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
        "exploration_request_identity_guard.py",
        "join_claim_guard.py",  # NEW (#4575, #4520)
        "join_settle_guard.py",  # NEW (#4575, #4520)
        "join_stop_guard.py",  # NEW (#4575, #4520)
        "join_followup_guard.py",  # NEW (#4575, #4520)
        "resource_exhaustion_guard.py",  # NEW (#4678 rectify)
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
        "exploration_request_identity_guard.py",
        "git_ops_guard.py",
        "pr_create_guard.py",
        "unsafe_install_guard.py",
    }
)

# Risky gh CLI subcommand pairs that MUST have PreToolUse guard coverage.
# test_risky_gh_subcommand_coverage.py enforces that every pair here is
# detected by at least one command-inspecting guard registered under a
# Bash|run_cmd matcher. Add new pairs when threat modeling identifies
# risky gh subcommands; the coverage test will fail until a guard exists.
#
# Re-exported from autoskillit.hooks._hook_constants (the canonical authority)
# via ._risky_operations for backwards compatibility with every existing
# consumer that imports ``RISKY_GH_SUBCOMMANDS`` from
# ``autoskillit.hook_registry``.

# Risky raw git CLI operations that MUST have PreToolUse guard coverage.
# test_risky_git_ops_coverage.py enforces that every tuple here is
# detected by at least one command-inspecting guard registered under a
# Bash|run_cmd matcher. Add new tuples when threat modeling identifies
# risky git operations; the coverage test will fail until a guard exists.
#
# Re-exported from autoskillit.hooks._hook_constants (the canonical authority)
# via ._risky_operations.


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
# resource_exhaustion_guard              | works-as-is
# test_runner_guard                      | works-as-is
# shell_capture_hook                     | works-as-is (Codex-only input-rewrite, #4286/ADR-0006)
# exploration_request_identity_guard     | not-applicable
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


def _build_hook_registry() -> list[HookDef]:
    """Construct the canonical HOOK_REGISTRY list.

    Imported lazily at call time so that importing ``_registry_data`` does
    not transitively trigger ``autoskillit.hooks.__init__.py`` (the cycle
    that arises because ``autoskillit.hooks.__init__`` imports
    ``HOOK_REGISTRY`` from this package). The hook_registry package
    ``__init__.py`` calls this factory at the end of its import sequence,
    after every other module-level binding is in place.
    """
    from autoskillit.hooks import (
        EXEMPT_SESSION_TYPES_BY_GUARD,
        EXEMPT_SKILLS_BY_GUARD,
    )

    return [
        HookDef(
            matcher=r".*",
            scripts=["guards/fabricated_completion_guard.py"],
            session_scope="any",
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
            exempt_skills=EXEMPT_SKILLS_BY_GUARD["pr_create_guard"],
            exempt_session_types=EXEMPT_SESSION_TYPES_BY_GUARD["pr_create_guard"],
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
            exempt_skills=EXEMPT_SKILLS_BY_GUARD["git_ops_guard"],
            mechanism="deny",
            enforcement_strength={"claude_code": "soft", "codex": "works-as-is"},
        ),
        HookDef(
            matcher=r"Bash|mcp__.*autoskillit.*__run_cmd",
            scripts=["guards/resource_exhaustion_guard.py"],
            session_scope="any",
            mechanism="deny",
            enforcement_strength={"claude_code": "soft", "codex": "works-as-is"},
        ),
        HookDef(
            matcher=r"Bash|mcp__.*autoskillit.*__run_cmd",
            scripts=["guards/test_runner_guard.py"],
            session_scope="headless_only",
            exempt_skills=EXEMPT_SKILLS_BY_GUARD["test_runner_guard"],
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
            matcher=(
                r"mcp__.*autoskillit.*__(enable_exploration|submit_exploration_query|"
                r"get_exploration_page|resume_exploration_context)$"
            ),
            scripts=["guards/exploration_request_identity_guard.py"],
            timeout_seconds=5,
            session_scope="interactive_only",
            codex_status="not-applicable",
            mechanism="input-rewrite",
            enforcement_strength={"claude_code": "hard", "codex": "not-applicable"},
            produces_resources=frozenset({"exploration-request-records"}),
            reclaims_resources=frozenset({"exploration-request-records"}),
            self_reclaims_resources=frozenset({"exploration-request-records"}),
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
            matcher=r"Bash|Agent|ScheduleWakeup",
            scripts=["guards/background_exec_guard.py"],
            session_scope="any",
            mechanism="deny",
            enforcement_strength={"claude_code": "hard", "codex": "works-as-is"},
        ),
        HookDef(
            matcher="Agent",
            scripts=["guards/join_claim_guard.py"],
            session_scope="any",
            codex_status="not-applicable",
            mechanism="deny",
            enforcement_strength={"claude_code": "hard", "codex": "not-applicable"},
        ),
        HookDef(
            matcher="",
            scripts=["guards/join_followup_guard.py"],
            session_scope="any",
            codex_status="not-applicable",
            mechanism="deny",
            enforcement_strength={"claude_code": "hard", "codex": "not-applicable"},
        ),
        HookDef(
            matcher="Agent",
            event_type="PostToolUse",
            scripts=["guards/join_settle_guard.py"],
            session_scope="any",
            codex_status="not-applicable",
            mechanism="side-effect",
            enforcement_strength={"claude_code": "hard", "codex": "not-applicable"},
        ),
        HookDef(
            matcher="Agent",
            event_type="PostToolUseFailure",
            scripts=["guards/join_settle_guard.py"],
            session_scope="any",
            codex_status="not-applicable",
            mechanism="side-effect",
            enforcement_strength={"claude_code": "hard", "codex": "not-applicable"},
        ),
        HookDef(
            matcher="",
            event_type="Stop",
            scripts=["guards/join_stop_guard.py"],
            session_scope="any",
            codex_status="not-applicable",
            mechanism="deny",
            enforcement_strength={"claude_code": "hard", "codex": "not-applicable"},
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


# Populated by the hook_registry package __init__.py via _build_hook_registry()
# after every other import is resolved (the actual list construction is
# deferred to break the cycle through autoskillit.hooks.__init__.py).
HOOK_REGISTRY: list[HookDef] = []

LIFECYCLE_CONTRACTS: tuple[LifecycleContractDef, ...] = (
    LifecycleContractDef(
        resource="shell-captures",
        producer_script="shell_capture_hook.py",
        backend="codex",
        session_scope="any",
        required_owner_roles=frozenset({"same_runner", "session_start"}),
    ),
    LifecycleContractDef(
        resource="exploration-request-records",
        producer_script="guards/exploration_request_identity_guard.py",
        backend="claude_code",
        session_scope="interactive_only",
        required_owner_roles=frozenset({"same_runner"}),
    ),
)
