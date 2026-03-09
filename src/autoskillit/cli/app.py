"""CLI for autoskillit: serve, init, cook, config, skills, recipes, workspace."""

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

from autoskillit.cli._init_helpers import (
    _MARKER_CONTENT,
    _generate_config_yaml,
    _prompt_test_command,
)
from autoskillit.core import ClaudeFlags, _atomic_write, pkg_root
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

    # Phase 1: Early init at INFO (or DEBUG if --verbose) — ensures logging
    # works for config load errors.
    cli_level = _stdlib_logging.DEBUG if verbose else _stdlib_logging.INFO
    configure_logging(
        level=cli_level,
        json_output=not sys.stderr.isatty(),
        stream=sys.stderr,
    )

    project_dir = Path.cwd()
    cfg = load_config(project_dir)

    # Phase 2: Reconfigure if config specifies a different level.
    # min() ensures --verbose OR config DEBUG both enable debug — most verbose wins.
    config_level = getattr(_stdlib_logging, cfg.logging.level.upper(), _stdlib_logging.INFO)
    effective_level = min(config_level, cli_level)
    json_output = (
        cfg.logging.json_output if cfg.logging.json_output is not None else not sys.stderr.isatty()
    )
    if effective_level != cli_level or cfg.logging.json_output is not None:
        configure_logging(
            level=effective_level,
            json_output=json_output,
            stream=sys.stderr,
        )

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


@app.command
def doctor(*, output_json: bool = False, fix: bool = False):
    """Check project setup for common issues.

    Parameters
    ----------
    output_json
        Output results as JSON instead of human-readable text.
    fix
        Auto-remediate fixable errors (e.g. remove stale gate files).
    """
    from autoskillit.cli._doctor import run_doctor
    from autoskillit.server import _get_plugin_dir

    plugin_dir = _get_plugin_dir()
    run_doctor(output_json=output_json, plugin_dir=plugin_dir, fix=fix)


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
    force: Annotated[bool, Parameter(name=["--force", "-f"])] = False,
) -> None:
    """Prune autoskillit-runs/ directories.

    Partitions subdirectories of autoskillit-runs/ into stale (>=5h old)
    and recent (<5h), displays both lists with ages, and requires
    confirmation before deleting stale directories.

    Parameters
    ----------
    dir
        Base directory to search for autoskillit-runs/ (default: parent of CWD).
    force
        Skip the confirmation prompt and delete stale directories immediately.
    """
    from autoskillit.cli._workspace import run_workspace_clean  # deferred: avoids scan overhead

    run_workspace_clean(dir=dir, force=force)


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


@recipes_app.command(name="render")
def recipes_render(name: str | None = None) -> None:
    """Pre-generate flow diagram(s) for recipe(s).

    Parameters
    ----------
    name
        Name of a single recipe to render. Renders all recipes if omitted.
    """
    from autoskillit.core import RecipeSource
    from autoskillit.recipe import find_recipe_by_name, generate_recipe_diagram, list_recipes

    project_dir = Path.cwd()

    def _recipes_dir(info: object) -> Path:
        if getattr(info, "source", None) == RecipeSource.BUILTIN:
            return pkg_root() / "recipes"
        return project_dir / ".autoskillit" / "recipes"

    if name is not None:
        match = find_recipe_by_name(name, project_dir)
        if match is None:
            print(f"Recipe '{name}' not found.", file=sys.stderr)
            sys.exit(1)
        generate_recipe_diagram(match.path, _recipes_dir(match))
        print(f"Rendered: {name}")
    else:
        result = list_recipes(project_dir)
        for info in result.items:
            generate_recipe_diagram(info.path, _recipes_dir(info))
            print(f"Rendered: {info.name}")


def _launch_cook_session(system_prompt: str) -> None:
    """Launch an interactive Claude Code cook session with the given system prompt."""
    if shutil.which("claude") is None:
        print("ERROR: 'claude' command not found on PATH.")
        print("Install Claude Code first: https://docs.anthropic.com/en/docs/claude-code")
        sys.exit(1)
    spec = build_interactive_cmd()
    cmd = spec.cmd + [
        ClaudeFlags.PLUGIN_DIR,
        str(pkg_root()),
        ClaudeFlags.TOOLS,
        "AskUserQuestion",
        ClaudeFlags.APPEND_SYSTEM_PROMPT,
        system_prompt,
    ]
    result = subprocess.run(cmd, env={**os.environ, **spec.env})
    if result.returncode != 0:
        sys.exit(result.returncode)


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
    from autoskillit.cli._prompts import _build_orchestrator_prompt

    if os.environ.get("CLAUDECODE"):
        print("ERROR: 'cook' cannot run inside a Claude Code session.")
        print("Run this command in a regular terminal.")
        sys.exit(1)

    if recipe is None:
        from autoskillit.cli._prompts import (
            _OPEN_KITCHEN_CHOICE,
            _build_open_kitchen_prompt,
            _resolve_recipe_input,
        )

        available = list_recipes(Path.cwd()).items
        if not available:
            print("No recipes found. Run 'autoskillit recipes list' to check.")
            sys.exit(1)
        print("Available recipes:")
        print("  0. Open kitchen (no recipe)")
        for i, r in enumerate(available, 1):
            print(f"  {i}. {r.name}")
        raw = input(f"Select recipe [0-{len(available)}]: ").strip()
        resolved = _resolve_recipe_input(raw, available)
        if resolved is _OPEN_KITCHEN_CHOICE:
            _launch_cook_session(_build_open_kitchen_prompt())
            return
        elif resolved is None:
            print(f"Invalid selection: '{raw}'")
            sys.exit(1)
        else:
            if isinstance(resolved, str):
                raise TypeError(f"Expected RecipeInfo, got str: {resolved!r}")
            recipe = resolved.name

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

    _launch_cook_session(_build_orchestrator_prompt(recipe_yaml))


def main() -> None:
    """Entry point for autoskillit."""
    app()
