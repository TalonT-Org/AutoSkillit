"""Resolve auto-detect ingredient values from the project environment."""

from __future__ import annotations

import subprocess
from pathlib import Path

from autoskillit.core import FLEET_MENU_TOOLS, get_logger, is_feature_enabled

logger = get_logger(__name__)

_DISPLAY_CATEGORIES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Execution", ("run_cmd", "run_python", "run_skill")),
    ("Testing & Workspace", ("test_check", "reset_test_dir", "classify_fix", "reset_workspace")),
    (
        "Git Operations",
        ("merge_worktree", "create_unique_branch", "check_pr_mergeable", "set_commit_status"),
    ),
    ("Recipes", ("migrate_recipe", "list_recipes", "load_recipe", "validate_recipe")),
    (
        "Clone & Remote",
        (
            "clone_repo",
            "remove_clone",
            "push_to_remote",
            "register_clone_status",
            "batch_cleanup_clones",
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
    ("Kitchen", ("open_kitchen", "close_kitchen", "disable_quota_guard", "reload_session")),
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
    except Exception:
        logger.warning("resolve_base_branch_failed", exc_info=True)
        resolved["base_branch"] = "main"

    return resolved
