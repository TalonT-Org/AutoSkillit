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

from autoskillit.core import is_git_worktree, pkg_root

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

        config_path.write_text(_generate_config_yaml(cmd_parts))
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
    _print_next_steps()


def _claude_settings_path(scope: str) -> Path:
    """Return the Claude Code settings.json path for the given scope."""
    if scope == "user":
        return Path.home() / ".claude" / "settings.json"
    return Path.cwd() / ".claude" / "settings.json"


def _register_quota_hook(settings_path: Path) -> None:
    """Idempotently add the quota PreToolUse hook to .claude/settings.json."""
    from autoskillit.core import _atomic_write

    data: dict = {}
    if settings_path.exists():
        try:
            data = json.loads(settings_path.read_text())
        except (OSError, json.JSONDecodeError):
            pass

    hooks = data.setdefault("hooks", {})
    pretooluse: list[dict] = hooks.setdefault("PreToolUse", [])

    MATCHER = "mcp__.*autoskillit.*__run_skill.*"
    COMMAND = "python3 -m autoskillit.hooks.quota_check"
    for entry in pretooluse:
        if entry.get("matcher") == MATCHER or (
            entry.get("hooks", [{}])[0].get("command") == COMMAND
        ):
            return  # already registered

    pretooluse.append(
        {
            "matcher": MATCHER,
            "hooks": [{"type": "command", "command": COMMAND}],
        }
    )
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write(settings_path, json.dumps(data, indent=2))


def _register_remove_clone_guard_hook(settings_path: Path) -> None:
    """Idempotently add the remove_clone_guard PreToolUse hook to .claude/settings.json."""
    from autoskillit.core import _atomic_write

    data: dict = {}
    if settings_path.exists():
        try:
            data = json.loads(settings_path.read_text())
        except (OSError, json.JSONDecodeError):
            pass

    hooks = data.setdefault("hooks", {})
    pretooluse: list[dict] = hooks.setdefault("PreToolUse", [])

    MATCHER = "mcp__.*autoskillit.*__remove_clone"
    COMMAND = "python3 -m autoskillit.hooks.remove_clone_guard"
    for entry in pretooluse:
        if entry.get("matcher") == MATCHER or (
            entry.get("hooks", [{}])[0].get("command") == COMMAND
        ):
            return  # already registered

    pretooluse.append(
        {
            "matcher": MATCHER,
            "hooks": [{"type": "command", "command": COMMAND}],
        }
    )
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write(settings_path, json.dumps(data, indent=2))


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
    (plugin_dir / "marketplace.json").write_text(json.dumps(manifest, indent=2) + "\n")

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
    from autoskillit import server as _server
    from autoskillit.cli._doctor import run_doctor

    plugin_dir = _server._ctx.plugin_dir if _server._ctx is not None else None
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
    marker.write_text(
        _MARKER_CONTENT.format(
            timestamp=datetime.now(UTC).isoformat(),
            version=__version__,
        )
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


def _build_orchestrator_prompt(script_yaml: str) -> str:
    """Build the --append-system-prompt content for a cook session."""
    return f"""\
You are a pipeline orchestrator. Execute the recipe below step-by-step.

FIRST: Type the open_kitchen prompt to activate AutoSkillit MCP tools before \
executing any pipeline steps. The exact prompt name depends on how the server \
was loaded — for example, plugin installs use \
/mcp__plugin_autoskillit_autoskillit__open_kitchen while --plugin-dir loading \
uses a different prefix. Check the available MCP prompts to find the correct \
open_kitchen prompt name.

After opening the kitchen:
1. Present the recipe to the user using the preview format below
2. Prompt for input values using AskUserQuestion
3. Execute the pipeline steps by calling MCP tools directly

Preview format:

    ## {{name}}
    {{description}}

    **Flow:** {{summary}}

    ### Ingredients
    For each ingredient show: name, description, required/optional, default value.
    Distinguish user-supplied ingredients (required=true or meaningful defaults)
    from agent-managed state (default="" or default=null with description
    indicating it is set by a prior step or the agent).

    ### Steps
    For each step show:
    - Step name and tool/action/python discriminator
    - Routing: on_success → X, on_failure → Y
    - If on_result: show field name and each route
    - If optional: true, mark as "[Optional]" and show the note explaining
      the skip condition
    - If retry block exists: retries Nx on {{condition}}, then → {{on_exhausted}}
    - If note exists, show it (notes contain critical agent instructions)
    - If capture exists, show what values are extracted

    ### Kitchen Rules
    If present, list all kitchen_rules strings.
    If absent, note: "No kitchen rules defined"

During pipeline execution, only use AutoSkillit MCP tools:
- Read, Grep, Glob (code investigation) — not used here because investigation
  happens inside headless sessions launched by run_skill/run_skill_retry,
  which have full tool access.
- Edit, Write (code modification) — not used here because all code changes
  are delegated through run_skill/run_skill_retry.
- Bash (shell commands) — not used here; use run_cmd if shell access is needed.
- Task/Explore subagents, WebFetch, WebSearch — not used here; delegate via
  run_skill for any research or multi-step work.

Allowed during pipeline execution:
- AutoSkillit MCP tools (call directly, not via subagents)
- AskUserQuestion (user interaction)
- Steps with `capture:` fields extract values from tool results into a
  pipeline context dict. Use captured values in subsequent steps via
  ${{{{ context.var_name }}}} in `with:` arguments.
- Thread outputs from each step into the next (e.g. worktree_path from
  implement into test_check).

ROUTING RULES — MANDATORY:
- When a tool returns a failure result, you MUST follow the step's on_failure route.
- When a step fails, route to on_failure — do not use Read, Grep, Glob, Edit,
  Write, Bash, or Explore subagents to investigate. The on_failure step (e.g.,
  resolve-failures) has diagnostic access that the orchestrator does not.
- Your ONLY job is to route to the correct next step and pass the
  required arguments. The downstream skill does the actual work.

FAILURE PREDICATES — when to follow on_failure:
- test_check: {{"passed": false}}
- merge_worktree: "error" key present in response
- run_cmd: {{"success": false}}
- run_skill / run_skill_retry: {{"success": false}}
- classify_fix: "error" key present in response

--- RECIPE ---
{script_yaml}
--- END RECIPE ---
"""


@app.command
def cook(recipe: str):
    """Launch an interactive Claude Code session to execute a recipe.

    Starts Claude Code with hard tool restrictions: only AskUserQuestion
    (built-in) and AutoSkillit MCP tools are available. The recipe is
    injected via --append-system-prompt so the session starts ready to
    execute.

    Parameters
    ----------
    recipe
        Name of the recipe (from .autoskillit/recipes/).
    """
    if os.environ.get("CLAUDECODE"):
        print("ERROR: 'cook' cannot run inside a Claude Code session.")
        print("Run this command in a regular terminal.")
        sys.exit(1)

    from autoskillit.core import YAMLError
    from autoskillit.recipe import find_recipe_by_name, validate_recipe
    from autoskillit.recipe import list_recipes as _list_all_recipes_for_cook

    _match = find_recipe_by_name(recipe, Path.cwd())
    if _match is None:
        available = _list_all_recipes_for_cook(Path.cwd()).items
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

    plugin_dir = pkg_root()
    system_prompt = _build_orchestrator_prompt(recipe_yaml)

    cmd = [
        "claude",
        "--plugin-dir",
        str(plugin_dir),
        "--tools",
        "AskUserQuestion",
        "--append-system-prompt",
        system_prompt,
    ]

    result = subprocess.run(cmd)
    if result.returncode != 0:
        sys.exit(result.returncode)


def _print_next_steps() -> None:
    """Print concise post-install getting started instructions."""
    print("\nNext steps:")
    print("  1. cd into your project and run: autoskillit init")
    print("  2. Start Claude Code: claude")
    print("  3. Open the kitchen: /mcp__plugin_autoskillit_autoskillit__open_kitchen")


# --- Init helpers ---


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
