"""Init command helpers: interactive prompts, config YAML generation, and workspace marker."""

from __future__ import annotations

import json
from pathlib import Path

from autoskillit.core import _atomic_write
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
        except (json.JSONDecodeError, OSError):
            data = {}
    data.setdefault("mcpServers", {})
    data["mcpServers"]["autoskillit"] = {
        "type": "stdio",
        "command": "autoskillit",
        "args": ["serve"],
    }
    _atomic_write(claude_json_path, json.dumps(data, indent=2))


def _print_init_next_steps() -> None:
    print("\n✓ Config created")
    print("✓ MCP server registered in ~/.claude.json")
    print("✓ Hooks registered in settings.json")
    print("\nNext steps:")
    print("  autoskillit chefs-hat   Launch Claude with all skills")
    print("  autoskillit doctor      Check setup health")


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
    _register_mcp_server(_user_claude_json_path())
    _print_init_next_steps()
