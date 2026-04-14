"""cook command: interactive skill session launcher."""

from __future__ import annotations

import shutil
import subprocess
import uuid
from collections.abc import Mapping
from pathlib import Path

from autoskillit.cli._terminal import terminal_guard

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
        ),
    ),
    ("Kitchen", ("open_kitchen", "close_kitchen")),
)


def _run_cook_session(
    *,
    cmd: list[str],
    env: Mapping[str, str],
    _first_run: bool,
    initial_prompt: str | None,
    project_dir: Path,
) -> None:
    """Run the cook subprocess and gate mark_onboarded on success."""
    with terminal_guard():
        result = subprocess.run(cmd, env=env)
    if result.returncode == 0:
        if _first_run and initial_prompt is not None:
            from autoskillit.cli._onboarding import mark_onboarded

            mark_onboarded(project_dir)
    else:
        raise SystemExit(result.returncode)


def cook(*, resume: bool = False, session_id: str | None = None) -> None:
    """Launch Claude with all bundled AutoSkillit skills as slash commands."""
    from autoskillit.workspace import (
        DefaultSessionSkillManager,
        SkillsDirectoryProvider,
        resolve_ephemeral_root,
    )

    if not shutil.which("claude"):
        print("'claude' not found on PATH. Install Claude Code to use cook.")
        raise SystemExit(1)

    from autoskillit import __version__
    from autoskillit.cli._ansi import supports_color

    color = supports_color()
    _B = "\x1b[1m" if color else ""
    _C = "\x1b[96m" if color else ""
    _D = "\x1b[2m" if color else ""
    _G = "\x1b[32m" if color else ""
    _Y = "\x1b[33m" if color else ""
    _R = "\x1b[0m" if color else ""

    print(f"{_B}{_C}AUTOSKILLIT {__version__}{_R} {_D}Kitchen open. All tools active.{_R}")
    skip = {"Telemetry & Diagnostics", "Kitchen"}
    for name, tools in _DISPLAY_CATEGORIES:
        if name in skip:
            continue
        tool_list = f"{_D}, {_R}".join(f"{_G}{t}{_R}" for t in tools)
        print(f"  {_Y}{name:>20}{_R}  {tool_list}")
    print()

    from autoskillit.cli._ansi import permissions_warning
    from autoskillit.cli._timed_input import timed_prompt

    print(permissions_warning())
    confirm = timed_prompt(
        "\nLaunch session? [Enter/n]", default="", timeout=120, label="autoskillit cook"
    )
    if confirm.lower() in ("n", "no"):
        return

    from autoskillit.cli._init_helpers import _is_plugin_installed
    from autoskillit.cli._onboarding import is_first_run, run_onboarding_menu
    from autoskillit.config import load_config
    from autoskillit.core import configure_logging, find_latest_session_id, pkg_root
    from autoskillit.execution import build_interactive_cmd
    from autoskillit.execution.commands import _MAX_MCP_OUTPUT_TOKENS_VALUE

    configure_logging()

    resume_session_id: str | None = None
    if resume:
        resume_session_id = session_id or find_latest_session_id()
        if resume_session_id is None:
            print("No previous session found. Starting a fresh session.")

    project_dir = Path.cwd()
    initial_prompt: str | None = None
    _first_run = is_first_run(project_dir)
    if _first_run:
        initial_prompt = run_onboarding_menu(project_dir, color=color)

    session_id_local = uuid.uuid4().hex[:16]
    ephemeral_root = resolve_ephemeral_root()
    session_mgr = DefaultSessionSkillManager(SkillsDirectoryProvider(), ephemeral_root)
    session_mgr.cleanup_stale()
    config = load_config()
    skills_dir = session_mgr.init_session(
        session_id_local, cook_session=True, config=config, project_dir=project_dir
    )

    plugin_dir = None if _is_plugin_installed() else pkg_root()
    spec = build_interactive_cmd(
        plugin_dir=plugin_dir,
        add_dirs=[skills_dir],
        initial_prompt=initial_prompt,
        resume_session_id=resume_session_id,
        env_extras={"MAX_MCP_OUTPUT_TOKENS": _MAX_MCP_OUTPUT_TOKENS_VALUE},
    )
    _run_cook_session(
        cmd=spec.cmd,
        env=spec.env,
        _first_run=_first_run,
        initial_prompt=initial_prompt,
        project_dir=project_dir,
    )
