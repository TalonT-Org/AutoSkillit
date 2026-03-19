"""Init command helpers: interactive prompts, config YAML generation, and workspace marker."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from autoskillit.core import YAMLError, atomic_write, dump_yaml_str, load_yaml
from autoskillit.recipe import list_recipes

_MARKER_CONTENT = """\
# autoskillit workspace - do not delete
# This file authorizes reset_test_dir and reset_workspace to clear this directory.
# Created: {timestamp}
# Tool: autoskillit {version}
"""


def _prompt_recipe_choice() -> str:
    available = list_recipes(Path.cwd()).items
    if not available:
        print("No recipes found. Run 'autoskillit recipes list' to check.")
        raise SystemExit(1)
    print("Available recipes:")
    for i, r in enumerate(available, 1):
        print(f"  {i}. {r.name}")
    return input("Recipe name: ").strip()


def _prompt_test_command() -> list[str]:
    default = "task test-all"
    answer = input(f"Test command [{default}]: ").strip()
    return (answer if answer else default).split()


def _detect_github_repo() -> str | None:
    """Try to detect owner/repo from the git remote URL."""
    from autoskillit.core import parse_github_repo

    try:
        result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            return parse_github_repo(result.stdout.strip())
    except (OSError, subprocess.TimeoutExpired):
        pass
    return None


def _prompt_github_repo() -> str | None:
    """Prompt the user for their GitHub repository, auto-detecting from git remote."""
    from autoskillit.core import parse_github_repo

    detected = _detect_github_repo()
    default_display = f" [{detected}]" if detected else ""

    print(f"\nGitHub repository{default_display}:")
    print("  Accepts: owner/repo, https://github.com/owner/repo, or a git remote URL.")
    print("  Used for issue management, PR creation, and CI status checks.")
    print("  Leave blank" + (f" to use {detected}" if detected else " to skip") + ".")
    value = input(f"Repository{default_display}: ").strip()

    if not value:
        return detected

    # Accept full URLs — parse_github_repo normalises them to owner/repo
    parsed = parse_github_repo(value)
    if parsed:
        return parsed

    # Accept bare owner/repo if it looks valid
    if "/" in value and not value.startswith("http"):
        return value

    print(f"  Warning: '{value}' doesn't look like owner/repo — using as-is.")
    return value


def _create_secrets_template(project_dir: Path) -> None:
    """Create .autoskillit/.secrets.yaml with a placeholder for github.token."""
    autoskillit_dir = project_dir / ".autoskillit"
    autoskillit_dir.mkdir(exist_ok=True)
    secrets_path = autoskillit_dir / ".secrets.yaml"
    if secrets_path.exists():
        return  # Never overwrite existing secrets
    atomic_write(
        secrets_path,
        "# AutoSkillit secrets — never commit this file\n"
        "# This file is already listed in .gitignore\n\n"
        "# GitHub authentication (choose one):\n"
        "#   Option 1 (recommended): Run 'gh auth login' — the gh CLI handles auth\n"
        "#     for all MCP tool commands (issues, PRs, CI status).\n"
        "#   Option 2: Set a token below — used by background watchers (CI, merge queue)\n"
        "#     that poll the GitHub API directly via httpx.\n"
        "#   If gh is authenticated, the token below is optional.\n"
        "github:\n"
        "  token: ''  # Optional — only needed if gh auth is unavailable\n",
    )
    # Check if gh is already authenticated
    gh_authed = False
    try:
        result = subprocess.run(
            ["gh", "auth", "status"],
            capture_output=True,
            timeout=5,
        )
        gh_authed = result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        pass

    if gh_authed:
        print("GitHub CLI is authenticated — no additional token setup needed.")
    else:
        print(
            f"Created {secrets_path}\n"
            "  Run 'gh auth login' to authenticate, or add a token to the file above."
        )


def _is_plugin_installed() -> bool:
    """Return True if autoskillit is installed as a Claude plugin.

    Returns False when claude CLI is not on PATH, times out, or is otherwise unavailable.
    """
    try:
        result = subprocess.run(
            ["claude", "plugin", "list"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result.returncode == 0 and "autoskillit" in result.stdout
    except FileNotFoundError:
        return False  # claude CLI not on PATH
    except (subprocess.TimeoutExpired, OSError):
        return False  # CLI unavailable or timed out


def _generate_config_yaml(test_command: list[str]) -> str:
    """Generate config YAML with active settings and commented advanced sections."""
    cmd_str = json.dumps(test_command)
    return f"""\
test_check:
  command: {cmd_str}
  # timeout: 600

safety:
  reset_guard_marker: ".autoskillit-workspace"
  require_dry_walkthrough: true
  test_gate_on_merge: true

# --- Advanced settings (uncomment and configure as needed) ---
#
# classify_fix:
#   path_prefixes: []
#
# reset_workspace:
#   command: null
#   preserve_dirs: []
#
# implement_gate:
#   marker: "Dry-walkthrough verified = TRUE"
#   skill_names: ["/autoskillit:implement-worktree", "/autoskillit:implement-worktree-no-merge"]
#
# run_skill:
#   timeout: 7200
#   stale_threshold: 1200
#   completion_marker: "%%ORDER_UP%%"
"""


def _user_claude_json_path() -> Path:
    """Return path to ~/.claude.json (user-scoped MCP server config)."""
    return Path.home() / ".claude.json"


def _register_mcp_server(claude_json_path: Path) -> None:
    """Write autoskillit MCP server entry to claude.json (idempotent)."""
    data: dict = {}
    if claude_json_path.exists():
        try:
            data = json.loads(claude_json_path.read_text())
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"{claude_json_path} contains invalid JSON. "
                f"Fix or remove it before running 'autoskillit init'. Error: {exc}"
            ) from exc
        except OSError as exc:
            raise OSError(f"{claude_json_path} could not be read: {exc}") from exc
    data.setdefault("mcpServers", {})
    data["mcpServers"]["autoskillit"] = {
        "type": "stdio",
        "command": "autoskillit",
        "args": [],
    }
    atomic_write(claude_json_path, json.dumps(data, indent=2))


def _print_next_steps() -> None:
    print("\nAutoskillit ready. Next steps:")
    print("  1. cd to your project directory")
    print("  2. autoskillit init           — create project config + register hooks")
    print(
        "  3. autoskillit cook setup-project  — explore your project and generate tailored recipes"
    )
    print("  4. autoskillit doctor          — verify your setup")


def _register_all(scope: str, project_dir: Path) -> None:
    """Ensure project temp dir, register hooks and MCP server, print next steps."""
    from autoskillit.cli._hooks import (
        _claude_settings_path,
        _evict_stale_autoskillit_hooks,
        sync_hooks_to_settings,
    )
    from autoskillit.core import ensure_project_temp

    ensure_project_temp(project_dir)
    settings_path = _claude_settings_path(scope)
    _evict_stale_autoskillit_hooks(settings_path)
    sync_hooks_to_settings(settings_path)

    # Prompt for github.default_repo if running interactively
    if sys.stdin.isatty():
        github_repo = _prompt_github_repo()
        if github_repo:
            config_path = project_dir / ".autoskillit" / "config.yaml"
            if config_path.exists():
                try:
                    config_data = load_yaml(config_path) or {}
                    if not config_data.get("github", {}).get("default_repo"):
                        config_data.setdefault("github", {})["default_repo"] = github_repo
                        atomic_write(
                            config_path,
                            dump_yaml_str(
                                config_data, default_flow_style=False, allow_unicode=True
                            ),
                        )
                except (OSError, YAMLError) as exc:
                    print(f"Warning: could not write github.default_repo to config: {exc}")
            # Write even if config doesn't exist yet — create a minimal one
            else:
                autoskillit_dir = project_dir / ".autoskillit"
                autoskillit_dir.mkdir(exist_ok=True)
                atomic_write(config_path, f"github:\n  default_repo: '{github_repo}'\n")

    _create_secrets_template(project_dir)

    if _is_plugin_installed():
        print("autoskillit is already registered as a Claude plugin — skipping mcpServers entry.")
    else:
        _register_mcp_server(_user_claude_json_path())

    _print_next_steps()
