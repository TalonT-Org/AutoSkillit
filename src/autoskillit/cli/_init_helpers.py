"""Init command helpers: interactive prompts, config YAML generation, and workspace marker."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from autoskillit.recipe import list_recipes

if TYPE_CHECKING:
    from autoskillit.recipe.loader import RecipeInfo

_MARKER_CONTENT = """\
# autoskillit workspace - do not delete
# This file authorizes reset_test_dir and reset_workspace to clear this directory.
# Created: {timestamp}
# Tool: autoskillit {version}
"""


# Sentinel returned by _resolve_recipe_input when the user selects option 0.
_OPEN_KITCHEN_CHOICE: str = "__open_kitchen__"


def _resolve_recipe_input(raw: str, available: list[RecipeInfo]) -> RecipeInfo | str | None:
    """Resolve picker raw text to a selection.

    Returns:
        _OPEN_KITCHEN_CHOICE  if raw is "0" (open kitchen, always valid)
        RecipeInfo            if raw is a valid 1-based index or an exact name match
        None                  for empty input, out-of-range numbers, or unknown names
    """
    if not raw:
        return None
    if raw.isdigit():
        n = int(raw)
        if n == 0:
            return _OPEN_KITCHEN_CHOICE
        if 1 <= n <= len(available):
            return available[n - 1]
        return None
    return next((r for r in available if r.name == raw), None)


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
