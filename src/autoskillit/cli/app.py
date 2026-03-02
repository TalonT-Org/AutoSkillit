"""CLI for autoskillit: serve, init, install, cook, config, skills, recipes, update."""

from __future__ import annotations

import dataclasses
import json
import os
import shutil
import subprocess
import sys
from datetime import UTC
from pathlib import Path
from typing import Annotated

from cyclopts import App, Parameter

from autoskillit.core import _atomic_write, is_git_worktree, pkg_root
from autoskillit.execution import build_interactive_cmd
from autoskillit.recipe import list_recipes

app = App(
    name="autoskillit",
    help="MCP server for executing recipes with Claude Code.",
)

config_app = App(name="config", help="Configuration commands.")
skills_app = App(name="skills", help="Skill management.")
recipes_app = App(name="recipes", help="Recipe management.")
workspace_app = App(name="workspace", help="Workspace management.")

app.command(config_app)
app.command(skills_app)
app.command(recipes_app)
app.command(workspace_app)


@app.default
def serve(*, verbose: Annotated[bool, Parameter(name=["--verbose", "-v"])] = False):
    """Start the MCP server (default command)."""
    import logging as _stdlib_logging

    from autoskillit.config import load_config
    from autoskillit.core import configure_logging, get_logger
    from autoskillit.server import _initialize, make_context, mcp

    configure_logging(
        level=_stdlib_logging.DEBUG if verbose else _stdlib_logging.INFO,
        json_output=not sys.stderr.isatty(),
        stream=sys.stderr,
    )

    project_dir = Path.cwd()
    cfg = load_config(project_dir)
    project_path = project_dir / ".autoskillit" / "config.yaml"
    user_path = Path.home() / ".autoskillit" / "config.yaml"
    resolved_path: str | None = (
        str(project_path)
        if project_path.is_file()
        else str(user_path)
        if user_path.is_file()
        else None
    )
    get_logger(__name__).info(
        "serve_startup",
        config_path=resolved_path,
        test_check_command=cfg.test_check.command,
    )

    plugin_dir = str(pkg_root())
    ctx = make_context(cfg, plugin_dir=plugin_dir)
    _initialize(ctx)
    mcp.run()


@app.command
def init(
    *,
    force: bool = False,
    test_command: str | None = None,
):
    """Initialize autoskillit for a project.

    Creates .autoskillit/config.yaml. Bundled skills are served automatically
    via the MCP server — no installation needed.

    Parameters
    ----------
    force
        Overwrite existing config without prompting.
    test_command
        Test command string for non-interactive init (e.g. "pytest -v").
    """
    from autoskillit.core import ensure_project_temp

    project_dir = Path.cwd()
    config_dir = project_dir / ".autoskillit"
    config_dir.mkdir(exist_ok=True)
    config_path = config_dir / "config.yaml"

    if config_path.exists() and not force:
        print(f"Config already exists: {config_path}")
        print("Use --force to overwrite.")
    else:
        if test_command is not None:
            cmd_parts = test_command.split()
        else:
            cmd_parts = _prompt_test_command()

        _atomic_write(config_path, _generate_config_yaml(cmd_parts))
        print(f"Config written to: {config_path}")

    ensure_project_temp(project_dir)

    print("\nReady! Start Claude Code and open the kitchen:")
    print("  claude")
    from autoskillit.cli._doctor import _is_plugin_installed

    if _is_plugin_installed():
        print("  /mcp__plugin_autoskillit_autoskillit__open_kitchen")
    else:
        print("  /mcp__autoskillit__open_kitchen")


_VALID_SCOPES = {"user", "project", "local"}
_MARKETPLACE_NAME = "autoskillit-local"


@app.command
def install(*, scope: str = "user"):
    """Install the plugin persistently for Claude Code.

    Sets up a local marketplace and installs the plugin so it loads
    automatically in every Claude Code session (no --plugin-dir needed).

    After updating autoskillit, re-run this command to refresh the cache.

    Parameters
    ----------
    scope
        Where to enable: "user" (all projects), "project" (shared via repo),
        or "local" (this project, gitignored).
    """
    if scope not in _VALID_SCOPES:
        print(f"Invalid scope: {scope!r}. Must be one of: {', '.join(sorted(_VALID_SCOPES))}")
        sys.exit(1)

    marketplace_dir = _ensure_marketplace()
    plugin_ref = f"autoskillit@{_MARKETPLACE_NAME}"
    print(f"Marketplace prepared: {marketplace_dir}")

    # Cannot run `claude plugin` commands from inside a Claude Code session
    if os.environ.get("CLAUDECODE"):
        print("\nRun these commands in a regular terminal to complete installation:")
        print(f"  claude plugin marketplace add {marketplace_dir}")
        print(f"  claude plugin install {plugin_ref} --scope {scope}")
        print("\nThen run: autoskillit init (in your project directory)")
        return

    if shutil.which("claude") is None:
        print("\nERROR: 'claude' command not found on PATH.")
        print("Install Claude Code, then run:")
        print(f"  claude plugin marketplace add {marketplace_dir}")
        print(f"  claude plugin install {plugin_ref} --scope {scope}")
        print("\nThen run: autoskillit init (in your project directory)")
        sys.exit(1)

    _clear_plugin_cache()

    # Register the marketplace (idempotent)
    result = subprocess.run(
        ["claude", "plugin", "marketplace", "add", str(marketplace_dir)],
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        timeout=30,
    )
    if result.returncode != 0:
        print(f"Failed to register marketplace: {result.stderr.strip()}")
        sys.exit(1)
    print("Marketplace registered.")

    # Install the plugin
    result = subprocess.run(
        ["claude", "plugin", "install", plugin_ref, "--scope", scope],
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        timeout=30,
    )
    if result.returncode != 0:
        print(f"Failed to install plugin: {result.stderr.strip()}")
        sys.exit(1)

    print(f"Plugin installed: {plugin_ref} (scope: {scope})")
    settings_path = _claude_settings_path(scope)
    _register_quota_hook(settings_path)
    _register_remove_clone_guard_hook(settings_path)
    _register_skill_command_guard_hook(settings_path)
    _print_next_steps()


from autoskillit.cli._hooks import (
    _claude_settings_path,
    _register_quota_hook,
    _register_remove_clone_guard_hook,
    _register_skill_command_guard_hook,
)


def _clear_plugin_cache() -> None:
    """Remove the cached plugin snapshot and installed_plugins.json entry.

    Claude Code caches a snapshot of the plugin at install time, keyed by
    version. When the version changes, it orphans the old cache but does not
    automatically create the new one until a second install is run. Clearing
    the cache beforehand ensures a single ``autoskillit install`` is always
    sufficient.
    """
    cache_dir = Path.home() / ".claude" / "plugins" / "cache" / _MARKETPLACE_NAME / "autoskillit"
    if cache_dir.is_dir():
        shutil.rmtree(cache_dir)

    installed_json = Path.home() / ".claude" / "plugins" / "installed_plugins.json"
    if installed_json.exists():
        try:
            data = json.loads(installed_json.read_text())
            plugin_ref = f"autoskillit@{_MARKETPLACE_NAME}"
            if plugin_ref in data:
                del data[plugin_ref]
                _atomic_write(installed_json, json.dumps(data, indent=2))
        except (OSError, json.JSONDecodeError):
            pass  # non-fatal — install will proceed regardless


def _ensure_marketplace() -> Path:
    """Create or update the local marketplace directory."""
    from autoskillit import __version__

    pkg_dir = pkg_root()

    # Guard: refuse to create the symlink when the package is installed
    # from a git worktree. The symlink target must outlive the Python
    # process that creates it — transient worktree paths will break it.
    if is_git_worktree(pkg_dir):
        raise SystemExit(
            "ERROR: 'autoskillit install' cannot be run when the package\n"
            "is installed from a git worktree.\n\n"
            f"  Detected worktree path: {pkg_dir}\n\n"
            "The marketplace symlink would point to this transient path and\n"
            "break when the worktree is deleted.\n\n"
            "Fix: run 'autoskillit install' from the main project checkout:\n"
            "  cd /path/to/main/repo && autoskillit install"
        )

    marketplace_dir = Path.home() / ".autoskillit" / "marketplace"
    plugin_dir = marketplace_dir / ".claude-plugin"
    plugin_dir.mkdir(parents=True, exist_ok=True)

    # Write marketplace manifest
    manifest = {
        "name": _MARKETPLACE_NAME,
        "owner": {"name": "autoskillit"},
        "plugins": [
            {
                "name": "autoskillit",
                "source": "./plugins/autoskillit",
                "description": "Orchestrated skill-driven workflows"
                " using Claude Code headless sessions",
                "version": __version__,
            }
        ],
    }
    _atomic_write(plugin_dir / "marketplace.json", json.dumps(manifest, indent=2) + "\n")

    # Symlink to the live package directory
    link_path = marketplace_dir / "plugins" / "autoskillit"
    link_path.parent.mkdir(parents=True, exist_ok=True)
    if link_path.is_symlink() or link_path.exists():
        link_path.unlink()
    link_path.symlink_to(pkg_dir)

    return marketplace_dir


@app.command
def upgrade():
    """Migrate a project from .autoskillit/scripts/ to .autoskillit/recipes/.

    Renames the directory and rewrites YAML top-level keys:
      inputs: -> ingredients:
      constraints: -> kitchen_rules:

    Idempotent: safe to run multiple times.
    """
    import re

    from autoskillit.core import _atomic_write

    project_dir = Path.cwd()
    scripts_dir = project_dir / ".autoskillit" / "scripts"
    recipes_dir = project_dir / ".autoskillit" / "recipes"

    if not scripts_dir.exists():
        print("Nothing to do — .autoskillit/scripts/ not found.")
        return

    if recipes_dir.exists():
        print("Nothing to do — .autoskillit/recipes/ already present.")
        return

    scripts_dir.rename(recipes_dir)

    changed = 0
    for yaml_file in sorted(recipes_dir.rglob("*.yaml")):
        text = yaml_file.read_text()
        new_text = re.sub(r"^inputs:", "ingredients:", text, flags=re.MULTILINE)
        new_text = re.sub(r"^constraints:", "kitchen_rules:", new_text, flags=re.MULTILINE)
        if new_text != text:
            _atomic_write(yaml_file, new_text)
            changed += 1

    print(f"Upgraded: directory renamed, {changed} file(s) updated.")


@app.command
def doctor(*, output_json: bool = False):
    """Check project setup for common issues.

    Parameters
    ----------
    output_json
        Output results as JSON instead of human-readable text.
    """
    from autoskillit.cli._doctor import run_doctor
    from autoskillit.server import _state as _server_state

    plugin_dir = _server_state._ctx.plugin_dir if _server_state._ctx is not None else None
    run_doctor(output_json=output_json, plugin_dir=plugin_dir)


@app.command
def migrate(*, check: bool = False):
    """Report outdated recipes and their available migrations.

    Parameters
    ----------
    check
        Exit with code 1 if any recipes need migration (useful for CI).
    """
    from autoskillit import __version__
    from autoskillit.core import RecipeSource
    from autoskillit.migration import applicable_migrations
    from autoskillit.recipe import list_recipes as _list_all_recipes

    project_dir = Path.cwd()
    scripts_dir = project_dir / ".autoskillit" / "scripts"
    recipes_dir = project_dir / ".autoskillit" / "recipes"
    if scripts_dir.exists() and not recipes_dir.exists():
        print("Project not upgraded — run 'autoskillit upgrade' first.")
        return

    all_result = _list_all_recipes(project_dir)
    project_items = [r for r in all_result.items if r.source == RecipeSource.PROJECT]

    if not project_items:
        print("No recipes found in .autoskillit/recipes/")
        return

    pending = []
    for recipe in project_items:
        applicable = applicable_migrations(recipe.version, __version__)
        if applicable:
            pending.append((recipe, applicable))

    if not pending:
        print(f"All {len(project_items)} recipe(s) are at version {__version__}.")
        return

    print(f"{len(pending)} recipe(s) need migration:\n")
    for recipe, migrations in pending:
        current = recipe.version or "(no version)"
        target = migrations[-1].to_version
        total_changes = sum(len(m.changes) for m in migrations)
        print(f"  {recipe.name}: {current} -> {target} ({total_changes} change(s))")
        for mig in migrations:
            for change in mig.changes:
                print(f"    - {change.description}")

    if check:
        raise SystemExit(1)

    print(
        "\nRecipes are auto-migrated when loaded. "
        "Use `--check` in CI to gate on pending migrations."
    )


@app.command
def quota_status() -> None:
    """Check current 5-hour quota utilization. Exits 0 always; outputs JSON."""
    import asyncio

    from autoskillit.config import load_config
    from autoskillit.execution import check_and_sleep_if_needed

    config = load_config(Path.cwd())
    result = asyncio.run(check_and_sleep_if_needed(config.quota_guard))
    print(json.dumps(result))


@config_app.command(name="show")
def config_show():
    """Show resolved configuration as JSON."""
    from autoskillit.config import load_config

    cfg = load_config(Path.cwd())
    print(json.dumps(dataclasses.asdict(cfg), indent=2, default=list))


@skills_app.command(name="list")
def skills_list():
    """List bundled skills provided by the plugin."""
    from autoskillit.workspace import SkillResolver

    resolver = SkillResolver()
    skills = resolver.list_all()

    if not skills:
        print("No skills found.")
        return

    name_w = max(len(s.name) for s in skills)
    src_w = max(len(s.source) for s in skills)
    print(f"{'NAME':<{name_w}}  {'SOURCE':<{src_w}}  PATH")
    print(f"{'-' * name_w}  {'-' * src_w}  {'-' * 4}")
    for s in skills:
        print(f"{s.name:<{name_w}}  {s.source:<{src_w}}  {s.path}")


_MARKER_CONTENT = """\
# autoskillit workspace - do not delete
# This file authorizes reset_test_dir and reset_workspace to clear this directory.
# Created: {timestamp}
# Tool: autoskillit {version}
"""


@workspace_app.command(name="init")
def workspace_init(path: str):
    """Create a prep station directory with the reset guard marker.

    The directory must not exist or must be empty (or contain only the marker).

    Parameters
    ----------
    path
        Path to the prep station directory to initialize.
    """
    from datetime import datetime

    from autoskillit import __version__
    from autoskillit.config import load_config

    target = Path(path).resolve()
    cfg = load_config(Path.cwd())
    marker_name = cfg.safety.reset_guard_marker

    if target.is_dir():
        contents = [f for f in target.iterdir() if f.name != marker_name]
        if contents:
            print(f"Directory is not empty: {target}", file=sys.stderr)
            print("prep-station init only works on empty or new directories.", file=sys.stderr)
            sys.exit(1)

    target.mkdir(parents=True, exist_ok=True)
    marker = target / marker_name
    _atomic_write(
        marker,
        _MARKER_CONTENT.format(
            timestamp=datetime.now(UTC).isoformat(),
            version=__version__,
        ),
    )
    print(f"Prep station initialized: {target}")
    print(f"Reset guard marker created: {marker}")


@workspace_app.command(name="clean")
def workspace_clean(
    *,
    dir: Annotated[str | None, Parameter(name=["--dir"])] = None,
) -> None:
    """Prune autoskillit-runs/ directories.

    Removes all subdirectories of autoskillit-runs/ under the given path.
    Defaults to the parent of the current working directory.

    Parameters
    ----------
    dir
        Base directory to search for autoskillit-runs/ (default: parent of CWD).
    """
    base = Path(dir).resolve() if dir else Path.cwd().parent
    runs_dir = base / "autoskillit-runs"

    if not runs_dir.is_dir():
        print(f"No autoskillit-runs/ directory found under: {base}")
        return

    count = 0
    errors = 0
    for entry in sorted(runs_dir.iterdir()):
        if entry.is_dir():
            try:
                shutil.rmtree(entry)
                print(f"Removed: {entry}")
                count += 1
            except OSError as exc:
                print(f"Failed to remove {entry}: {exc}", file=sys.stderr)
                errors += 1

    if count == 0 and errors == 0:
        print(f"Nothing to clean in {runs_dir}")
    else:
        suffix = "ies" if count != 1 else "y"
        err_note = f" ({errors} error(s))" if errors else ""
        print(f"\nCleaned {count} director{suffix}{err_note}")


@recipes_app.command(name="list")
def recipes_list():
    """List available recipes with sources."""
    from autoskillit.recipe import list_recipes

    recipes = list_recipes(Path.cwd()).items
    if not recipes:
        print("No recipes found.")
        return

    name_w = max(len(r.name) for r in recipes)
    src_w = max(len(r.source) for r in recipes)
    print(f"{'NAME':<{name_w}}  {'SOURCE':<{src_w}}  DESCRIPTION")
    print(f"{'-' * name_w}  {'-' * src_w}  {'-' * 11}")
    for r in recipes:
        print(f"{r.name:<{name_w}}  {r.source:<{src_w}}  {r.description}")


@recipes_app.command(name="show")
def recipes_show(name: str):
    """Print the YAML content of a named recipe."""
    from autoskillit.recipe import find_recipe_by_name

    match = find_recipe_by_name(name, Path.cwd())
    if match is None:
        print(f"No recipe named '{name}'.", file=sys.stderr)
        sys.exit(1)
    print(match.path.read_text())



@app.command
def cook(recipe: str | None = None):
    """Launch an interactive Claude Code session to execute a recipe.

    Starts Claude Code with hard tool restrictions: only AskUserQuestion
    (built-in) and AutoSkillit MCP tools are available. The recipe is
    injected via --append-system-prompt so the session starts ready to
    execute.

    Parameters
    ----------
    recipe
        Name of the recipe (from .autoskillit/recipes/). Prompts if omitted.
    """
    if os.environ.get("CLAUDECODE"):
        print("ERROR: 'cook' cannot run inside a Claude Code session.")
        print("Run this command in a regular terminal.")
        sys.exit(1)

    if recipe is None:
        recipe = _prompt_recipe_choice()

    from autoskillit.core import YAMLError
    from autoskillit.recipe import find_recipe_by_name, validate_recipe

    _match = find_recipe_by_name(recipe, Path.cwd())
    if _match is None:
        available = list_recipes(Path.cwd()).items
        print(f"Recipe not found: '{recipe}'")
        if available:
            print("Available recipes:")
            for r in available:
                print(f"  - {r.name}")
        else:
            print("No recipes found")
        sys.exit(1)
    recipe_yaml = _match.path.read_text()

    # Validate recipe before launching session
    from autoskillit.recipe import load_recipe as _load_for_cook

    try:
        parsed = _load_for_cook(_match.path)
    except YAMLError as exc:
        print(f"Recipe YAML parse error: {exc}")
        sys.exit(1)
    except ValueError as exc:
        print(f"Recipe structure error: {exc}")
        sys.exit(1)

    errors = validate_recipe(parsed)
    if errors:
        print(f"Recipe '{recipe}' failed validation:")
        for err in errors:
            print(f"  - {err}")
        sys.exit(1)

    if shutil.which("claude") is None:
        print("ERROR: 'claude' command not found on PATH.")
        print("Install Claude Code first: https://docs.anthropic.com/en/docs/claude-code")
        sys.exit(1)

    from autoskillit.cli._prompts import _build_orchestrator_prompt

    plugin_dir = pkg_root()
    system_prompt = _build_orchestrator_prompt(recipe_yaml)

    spec = build_interactive_cmd()
    cmd = spec.cmd + [
        "--plugin-dir",
        str(plugin_dir),
        "--tools",
        "AskUserQuestion",
        "--append-system-prompt",
        system_prompt,
    ]

    result = subprocess.run(cmd, env={**os.environ, **spec.env})
    if result.returncode != 0:
        sys.exit(result.returncode)


def _print_next_steps() -> None:
    """Print concise post-install getting started instructions."""
    print("\nNext steps:")
    print("  1. cd into your project and run: autoskillit init")
    print("  2. Start Claude Code: claude")
    print("  3. Open the kitchen: /mcp__plugin_autoskillit_autoskillit__open_kitchen")


# --- Init helpers ---


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
#   timeout: 3600
#   heartbeat_marker: '"type":"result"'
#   stale_threshold: 1200
#   completion_marker: "%%ORDER_UP%%"
#
# run_skill_retry:
#   timeout: 7200
#   stale_threshold: 1200
"""


def main() -> None:
    """Entry point for autoskillit."""
    app()
