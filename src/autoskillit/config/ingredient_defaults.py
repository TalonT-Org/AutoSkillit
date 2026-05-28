"""Resolve auto-detect ingredient values from the project environment."""

from __future__ import annotations

import os
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import Protocol

from autoskillit.core import DISPATCH_ID_ENV_VAR, FLEET_MENU_TOOLS, get_logger, is_feature_enabled

logger = get_logger(__name__)

_DISPLAY_CATEGORIES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Execution", ("run_cmd", "run_python", "run_skill")),
    ("Testing & Workspace", ("test_check", "reset_test_dir", "classify_fix", "reset_workspace")),
    (
        "Git Operations",
        (
            "merge_worktree",
            "create_unique_branch",
            "create_and_publish_branch",
            "check_pr_mergeable",
            "set_commit_status",
        ),
    ),
    ("Recipes", ("migrate_recipe", "list_recipes", "load_recipe", "validate_recipe")),
    ("Agents", ("unlock_agent_pack",)),
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
            "enrich_issues",
            "claim_issue",
            "release_issue",
            "get_pr_reviews",
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

# Keys from resolve_ingredient_defaults() that the server must inject as authoritative
# overrides, preventing LLM-supplied values from winning. source_dir is excluded because
# it is project-identity (the clone URL) and is legitimately caller-supplied in fleet dispatch.
SERVER_AUTHORITATIVE_INGREDIENTS: frozenset[str] = frozenset(
    {
        "base_branch",
        "local_review_rounds",
        "adversarial_review_level",
        "post_run_diagnostics",
        "is_fleet_dispatch",
        "dispatch_id",
    }
)


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
        resolved["post_run_diagnostics"] = str(cfg.diagnostics.post_run_analysis).lower()
    except Exception:
        logger.warning("resolve_base_branch_failed", exc_info=True)
        resolved["base_branch"] = "main"
        resolved["local_review_rounds"] = "0"
        resolved["adversarial_review_level"] = "auto"
        resolved["post_run_diagnostics"] = "false"

    # Fleet dispatch detection — reads env vars, not config, so must run unconditionally.
    resolved["is_fleet_dispatch"] = "true" if os.environ.get(DISPATCH_ID_ENV_VAR) else "false"
    resolved["dispatch_id"] = os.environ.get(DISPATCH_ID_ENV_VAR, "")

    return resolved


class _HasAuthority(Protocol):
    authority: str | None


def apply_config_authoritative_overrides(
    effective_ingredients: dict[str, str],
    recipe_ingredients: Mapping[str, _HasAuthority],
    project_dir: Path,
) -> dict[str, str]:
    """Prevent LLM-supplied values from winning for config-authoritative keys at fleet dispatch."""
    config_keys = [
        key
        for key, ing in recipe_ingredients.items()
        if getattr(ing, "authority", None) == "config"
    ]
    if not config_keys:
        return dict(effective_ingredients)

    resolved = resolve_ingredient_defaults(project_dir)
    result = dict(effective_ingredients)
    for key in config_keys:
        if key in resolved:
            result[key] = resolved[key]
        else:
            logger.warning(
                "config-authority key %r not found in resolved defaults — "
                "caller-supplied value retained (config-authoritative contract not enforced)",
                key,
            )
    return result


def build_config_authoritative_layer(defaults: dict[str, str]) -> dict[str, str]:
    """Return the server-authoritative ingredient values from a resolved defaults dict."""
    return {k: v for k, v in defaults.items() if k in SERVER_AUTHORITATIVE_INGREDIENTS}
