"""Declared dispositions for scoped hook policies."""

from __future__ import annotations

from typing import Literal, cast

from ._hooks_defs import ProtectionWaiverDef

_SCOPED_POLICY_EXCLUSIONS: tuple[
    tuple[str, Literal["headless", "interactive"], str, str, str, str | None], ...
] = (
    (
        "guards/ask_user_question_guard.py",
        "interactive",
        "interactive questions",
        "Interactive users may ask questions; only headless workers must not wait.",
        "not-applicable",
        None,
    ),
    (
        "guards/compose_pr_body_guard.py",
        "interactive",
        "direct PR creation",
        "The interactive PR creation guard denies the command before a body can be published.",
        "hook",
        "guards/pr_create_guard.py",
    ),
    (
        "guards/planner_gh_discovery_guard.py",
        "interactive",
        "planner issue discovery",
        "This restriction applies to headless planner workers, not interactive user discovery.",
        "not-applicable",
        None,
    ),
    (
        "guards/test_runner_guard.py",
        "interactive",
        "direct test execution",
        "Interactive users may run tests; the headless workflow requires the configured gate.",
        "not-applicable",
        None,
    ),
    (
        "guards/planner_result_naming_guard.py",
        "interactive",
        "planner result naming",
        "Only headless planner workers publish these machine-named result files.",
        "not-applicable",
        None,
    ),
    (
        "guards/recipe_write_advisor.py",
        "headless",
        "recipe write advice",
        "The advisor is informational for interactive editing, not a headless write prohibition.",
        "not-applicable",
        None,
    ),
    (
        "guards/mcp_health_advisor.py",
        "headless",
        "MCP disconnect advice",
        "The advisory targets interactive reconnect decisions and does not deny headless tools.",
        "not-applicable",
        None,
    ),
    (
        "guards/skill_orchestration_guard.py",
        "interactive",
        "L1 orchestration recursion",
        "Interactive orchestrators are allowed to invoke orchestration tools by design.",
        "not-applicable",
        None,
    ),
    (
        "guards/recipe_read_guard.py",
        "interactive",
        "direct source reads",
        "Interactive users may inspect recipes; the source-read restriction is L1-specific.",
        "not-applicable",
        None,
    ),
    (
        "guards/skill_load_guard.py",
        "interactive",
        "unloaded headless skill tools",
        "The interactive host loads skills explicitly before their instructions apply.",
        "not-applicable",
        None,
    ),
)

_PROTECTION_BACKENDS: tuple[Literal["claude_code", "codex"], ...] = (
    "claude_code",
    "codex",
)

PROTECTION_WAIVERS: tuple[ProtectionWaiverDef, ...] = (
    tuple(
        ProtectionWaiverDef(
            guard_script=script,
            excluded_scope=scope,
            backend=backend,
            risk=risk,
            covering_mechanism=mechanism,
            justification=reason,
            covering_guard_script=delegate,
        )
        for script, scope, risk, reason, mechanism, delegate in _SCOPED_POLICY_EXCLUSIONS
        for backend in _PROTECTION_BACKENDS
    )
    + (
        ProtectionWaiverDef(
            guard_script="guards/background_exec_guard.py",
            excluded_scope="interactive",
            backend="codex",
            risk="managed-route scope exclusion",
            covering_mechanism="not-applicable",
            justification="Managed Codex parent and leaf routes are headless sessions only.",
        ),
        ProtectionWaiverDef(
            guard_script="guards/join_followup_guard.py",
            excluded_scope="interactive",
            backend="codex",
            risk="managed-route scope exclusion",
            covering_mechanism="not-applicable",
            justification="Join follow-up applies only to managed headless parents.",
        ),
        ProtectionWaiverDef(
            guard_script="guards/join_stop_guard.py",
            excluded_scope="interactive",
            backend="codex",
            risk="managed-route scope exclusion",
            covering_mechanism="not-applicable",
            justification="Join stop applies only to managed headless parents.",
        ),
        ProtectionWaiverDef(
            guard_script="guards/write_guard.py",
            excluded_scope="all",
            backend="codex",
            risk="write-prefix guard bypasses Codex",
            covering_mechanism="codex-sandbox",
            justification=(
                "Codex workspace-write and file_changes enforce session writes; "
                "the installation guard still runs."
            ),
        ),
    )
    + tuple(
        ProtectionWaiverDef(
            guard_script="guards/git_ops_guard.py",
            excluded_scope="interactive",
            backend=backend,
            risk="headless destructive-git policy branch is inactive",
            covering_mechanism="not-applicable",
            justification=(
                "Interactive users may choose these Git operations; "
                "the all-session ref preflight remains active."
            ),
        )
        for backend in _PROTECTION_BACKENDS
    )
    + tuple(
        cast(
            tuple[ProtectionWaiverDef, ...],
            (
                ProtectionWaiverDef(
                    guard_script=script,
                    excluded_scope="interactive",
                    backend=cast(Literal["claude_code", "codex"], backend),
                    risk="headless-only session scope",
                    covering_mechanism="not-applicable",
                    justification=(
                        f"{script} denies session-bound operations when invoked "
                        "from a headless worker; interactive users own the "
                        "operation and may resume ownership / dispatch directly."
                    ),
                )
                for script, backend in (
                    ("guards/fleet_dispatch_guard.py", "claude_code"),
                    ("guards/fleet_dispatch_guard.py", "codex"),
                    ("guards/resume_ownership_guard.py", "claude_code"),
                    ("guards/resume_ownership_guard.py", "codex"),
                )
            ),
        )
    )
)
