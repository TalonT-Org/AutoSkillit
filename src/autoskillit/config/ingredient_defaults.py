"""Resolve auto-detect ingredient values from the project environment."""

from __future__ import annotations

import os
import subprocess
from collections.abc import Mapping
from pathlib import Path

from autoskillit.core import (
    DISPATCH_ID_ENV_VAR,
    FLEET_MENU_TOOLS,
    SERVER_AUTHORITATIVE_INGREDIENTS,
    get_logger,
    is_feature_enabled,
)

logger = get_logger(__name__)

_DISPLAY_CATEGORIES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "Execution",
        (
            "run_cmd",
            "run_python",
            "run_skill",
            "run_fixed_batch",
            "read_fixed_batch_result",
            "recover_run_skill_result",
            "complete_run_skill_result",
            "delegate_evidence_reader",
        ),
    ),
    (
        "Testing & Workspace",
        ("test_check", "bind_plan_set", "reset_test_dir", "classify_fix", "reset_workspace"),
    ),
    (
        "Git Operations",
        (
            "merge_worktree",
            "create_unique_branch",
            "create_and_publish_branch",
            "check_pr_mergeable",
            "set_commit_status",
            "commit_files",
        ),
    ),
    (
        "Recipes",
        (
            "migrate_recipe",
            "list_recipes",
            "load_recipe",
            "validate_recipe",
            "get_recipe_section",
            "complete_recipe_initialization",
            "write_audit_semantic_result",
            "write_standalone_audit_evidence",
            "write_audit_disposition_bundle",
        ),
    ),
    ("Agents", ("unlock_agent_pack",)),
    (
        "Evidence Readers",
        (
            "read_authorized_artifact",
            "get_authorized_artifact_page",
        ),
    ),
    (
        "Repository Exploration",
        (
            "enable_exploration",
            "submit_exploration_query",
            "get_exploration_page",
            "resume_exploration_context",
        ),
    ),
    (
        "Clone & Remote",
        (
            "clone_repo",
            "remove_clone",
            "push_to_remote",
            "register_clone_status",
            "batch_cleanup_clones",
            "bootstrap_clone",
        ),
    ),
    (
        "GitHub",
        (
            "fetch_github_issue",
            "get_issue_title",
            "report_bug",
            "prepare_issue",
            "claim_issue",
            "release_issue",
            "get_pr_reviews",
            "verify_review_receipt",
            "post_pr_review",
            "bulk_close_issues",
            "claim_and_resolve_issue",
        ),
    ),
    (
        "CI & Automation",
        (
            "wait_for_ci",
            "wait_for_merge_queue",
            "check_repo_merge_state",
            "toggle_auto_merge",
            "enqueue_pr",
            "get_ci_status",
        ),
    ),
    (
        "Telemetry & Diagnostics",
        (
            "read_db",
            "write_telemetry_files",
            "kitchen_status",
            "get_pipeline_report",
            "get_token_summary",
            "get_timing_summary",
            "get_quota_events",
            "analyze_tool_sequences",
            "inspect_session_logs",
            "record_pipeline_step",
        ),
    ),
    ("Fleet", FLEET_MENU_TOOLS),
    (
        "Kitchen",
        (
            "open_kitchen",
            "close_kitchen",
            "disable_quota_guard",
            "reload_session",
            "configure_fleet",
            "configure_order",
            "lock_ingredients",
            "declare_join_batch",
        ),
    ),
)


def iter_display_categories(
    features: dict[str, bool],
    *,
    experimental_enabled: bool = False,
) -> tuple[tuple[str, tuple[str, ...]], ...]:
    return tuple(
        (name, tools)
        for name, tools in _DISPLAY_CATEGORIES
        if name != "Fleet"
        or is_feature_enabled("fleet", features, experimental_enabled=experimental_enabled)
    )


_REMOTE_PRECEDENCE = ("upstream", "origin")

SERVER_AUTHORITATIVE_CONFIG_PATHS: dict[str, str] = {
    "base_branch": "branching.default_base_branch",
    "local_review_rounds": "review.local_review_rounds",
    "adversarial_review_level": "plan.adversarial_review_level",
}

SERVER_AUTHORITATIVE_KEY_HINTS: dict[str, str] = {}

CONFIG_DEFAULT_INGREDIENTS: frozenset[str] = frozenset(
    {"pipeline_health", "auto_provision_exploration"}
)


def build_config_default_layer(defaults: dict[str, str]) -> dict[str, str]:
    """Return config-derived defaults for overridable config-backed ingredients."""
    return {k: v for k, v in defaults.items() if k in CONFIG_DEFAULT_INGREDIENTS}


def resolve_ingredient_defaults(project_dir: Path) -> dict[str, str]:
    """Resolve auto-detect ingredient values from the project environment."""
    from autoskillit.config.settings import load_config

    resolved: dict[str, str] = {}

    try:
        for remote in _REMOTE_PRECEDENCE:
            proc = subprocess.run(
                ["git", "remote", "get-url", remote],
                cwd=str(project_dir),
                capture_output=True,
                text=True,
                timeout=5,
            )
            if proc.returncode == 0 and proc.stdout.strip():
                resolved["source_dir"] = proc.stdout.strip()
                break
    except (OSError, subprocess.TimeoutExpired):
        pass

    try:
        cfg = load_config(project_dir)
        resolved["base_branch"] = cfg.branching.default_base_branch
        resolved["local_review_rounds"] = str(cfg.review.local_review_rounds)
        resolved["adversarial_review_level"] = cfg.plan.adversarial_review_level
        resolved["pipeline_health"] = str(cfg.diagnostics.pipeline_health).lower()
        resolved["auto_provision_exploration"] = str(
            cfg.agent_backend.auto_provision_exploration
        ).lower()
    except Exception:
        logger.warning("resolve_base_branch_failed", exc_info=True)
        resolved["base_branch"] = "main"
        resolved["local_review_rounds"] = "0"
        resolved["adversarial_review_level"] = "auto"
        resolved["pipeline_health"] = "false"
        resolved["auto_provision_exploration"] = "false"

    # Fleet dispatch detection — reads env vars, not config, so must run unconditionally.
    resolved["is_fleet_dispatch"] = "true" if os.environ.get(DISPATCH_ID_ENV_VAR) else "false"
    resolved["dispatch_id"] = os.environ.get(DISPATCH_ID_ENV_VAR, "")

    return resolved


def strip_server_authoritative_overrides(
    effective_ingredients: Mapping[str, str],
) -> tuple[dict[str, str], frozenset[str]]:
    """Remove overrides for names resolved by the serving server."""
    stripped = frozenset(
        key for key in effective_ingredients if key in SERVER_AUTHORITATIVE_INGREDIENTS
    )
    return (
        {key: value for key, value in effective_ingredients.items() if key not in stripped},
        stripped,
    )


def build_config_authoritative_layer(defaults: dict[str, str]) -> dict[str, str]:
    """Return the server-authoritative ingredient values from a resolved defaults dict."""
    return {k: v for k, v in defaults.items() if k in SERVER_AUTHORITATIVE_INGREDIENTS}
