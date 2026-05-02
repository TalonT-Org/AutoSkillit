"""CLI for autoskillit: serve, init, cook, order, config, skills, recipes, workspace."""

from __future__ import annotations

import dataclasses
import json
import os
import sys
from datetime import UTC
from pathlib import Path
from typing import Annotated

import anyio
from cyclopts import App, Parameter

from autoskillit.cli._cook import cook as cook_interactive
from autoskillit.cli._features import features_app
from autoskillit.cli._fleet import fleet_app
from autoskillit.cli._init_helpers import (
    _MARKER_CONTENT,
    _check_secret_scanning,
    _generate_config_yaml,
    _log_secret_scan_bypass,
    _prompt_test_command,
    _register_all,
)
from autoskillit.cli._order import _recipes_dir_for, order
from autoskillit.cli._serve_guard import serve_with_signal_guard
from autoskillit.cli._sessions import sessions_app
from autoskillit.core import (
    RecipeSource,
    atomic_write,
)

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
app.command(fleet_app)
app.command(features_app)
app.command(sessions_app)
app.command(order)


class CliError(Exception):
    """Raised by CLI helpers to signal a user-facing error that should abort the command."""


@app.default
def serve(*, verbose: Annotated[bool, Parameter(name=["--verbose", "-v"])] = False):
    """Start the MCP server (default command)."""
    import logging as _stdlib_logging

    from autoskillit.config import load_config
    from autoskillit.core import configure_logging, get_logger

    # Phase 1: Early init at INFO (or DEBUG if --verbose) — ensures logging
    # works for config load errors.  MUST run before importing
    # autoskillit.server so that module-level loggers resolve to stderr.
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

    # Import server AFTER logging is configured so module-level loggers
    # resolve to stderr+JSON, not stdout+ConsoleRenderer (structlog default).
    from autoskillit.server import _initialize, make_context, mcp

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
        test_check_command=cfg.test_check.commands or cfg.test_check.command,
    )

    # Inject config-derived protected branches so hook scripts read consistent values.
    # Guard: skip when the list is empty so hook scripts never receive "" and
    # accidentally split it into [""] instead of [].
    if cfg.safety.protected_branches:
        os.environ.setdefault(
            "AUTOSKILLIT_PROTECTED_BRANCHES",
            ",".join(cfg.safety.protected_branches),
        )

    ctx = make_context(cfg)
    _initialize(ctx)

    try:
        anyio.run(serve_with_signal_guard, mcp)
    except KeyboardInterrupt:
        pass  # Ctrl+C before anyio loop starts — rare during heavy import phase


@app.command
def init(
    *,
    force: bool = False,
    test_command: str | None = None,
    scope: Annotated[str, Parameter(help="Registration scope: user or project")] = "user",
):
    """Initialize autoskillit for a project.

    Creates .autoskillit/config.yaml, registers the MCP server in ~/.claude.json,
    and registers hooks in settings.json.

    Parameters
    ----------
    force
        Overwrite existing config without prompting.
    test_command
        Test command string for non-interactive init (e.g. "pytest -v").
    scope
        Registration scope for hooks: "user" or "project".
    """
    if scope not in ("user", "project"):
        raise SystemExit(f"Error: --scope must be 'user' or 'project', got '{scope}'")
    project_dir = Path.cwd()
    config_dir = project_dir / ".autoskillit"
    config_dir.mkdir(exist_ok=True)
    config_path = config_dir / "config.yaml"

    gate = _check_secret_scanning(project_dir)
    if not gate.passed:
        raise SystemExit(1)

    if config_path.exists() and not force:
        print(f"  Config already exists: {config_path}")
        print("  Use --force to overwrite.")
        if gate.bypass_accepted:
            _log_secret_scan_bypass(project_dir)
    else:
        if test_command is not None:
            cmd_parts = test_command.split()
        else:
            cmd_parts = _prompt_test_command()

        atomic_write(config_path, _generate_config_yaml(cmd_parts))
        if gate.bypass_accepted:
            _log_secret_scan_bypass(project_dir)
        onboarded_marker = config_dir / ".onboarded"
        onboarded_marker.unlink(missing_ok=True)

    try:
        _register_all(scope, project_dir)
    except CliError as exc:
        print(f"\n  ERROR: {exc}")
        raise SystemExit(1) from None


@app.command
def install(
    *,
    scope: Annotated[str, Parameter(help="Registration scope: user, project, or local")] = "user",
):
    """Install the plugin for Claude Code and refresh the cache."""
    from autoskillit.cli._init_helpers import _print_next_steps
    from autoskillit.cli._marketplace import install as _install

    completed = _install(scope=scope)
    if completed:
        _print_next_steps(context="install")


@app.command
def upgrade() -> None:
    """Migrate project from .autoskillit/scripts/ format to .autoskillit/recipes/ format."""
    from autoskillit.cli._marketplace import upgrade as _upgrade

    _upgrade()


@app.command
def update() -> None:
    """Upgrade autoskillit to the latest version on your install's branch."""
    from autoskillit.cli._update import run_update_command

    run_update_command()


@app.command
def doctor(*, output_json: bool = False):
    """Check project setup for common issues."""
    from autoskillit.cli._doctor import run_doctor

    run_doctor(output_json=output_json)


@app.command
def migrate(*, check: bool = False):
    """Report outdated recipes and their available migrations.

    Parameters
    ----------
    check
        Exit with code 1 if any recipes need migration (useful for CI).
    """
    from autoskillit import __version__
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
    """Check quota utilization across all rate-limit windows. Exits 0 always; outputs JSON."""
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
    from autoskillit.workspace import DefaultSkillResolver

    resolver = DefaultSkillResolver()
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
    atomic_write(
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
    import asyncio

    from autoskillit.cli._workspace import run_workspace_clean  # deferred: avoids scan overhead

    asyncio.run(run_workspace_clean(dir=dir, force=force))


@recipes_app.command(name="list")
def recipes_list():
    """List available recipes with sources."""
    from autoskillit.cli._cook import _print_recipes_list

    _print_recipes_list()


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
    """Show pre-rendered diagram. Diagrams are generated by /render-recipe."""
    from autoskillit.recipe import find_recipe_by_name, list_recipes, load_recipe_diagram

    if name is None:
        for info in list_recipes(Path.cwd()).items:
            print(f"  {info.name}")
        return
    match = find_recipe_by_name(name, Path.cwd())
    if match is None:
        print(f"Recipe '{name}' not found.", file=sys.stderr)
        sys.exit(1)
    diagram = load_recipe_diagram(name, _recipes_dir_for(match))
    print(diagram if diagram else f"No diagram. Run /render-recipe {name}")


@app.command(name="cook", alias="c")
def _cook_cmd(session_id: str | None = None, *, resume: bool = False) -> None:
    """Launch an interactive Claude session with all skills and kitchen tools.

    Use --resume to restore a previous session. Pass a session ID to target a
    specific one, or omit for Claude Code's interactive picker.
    """
    cook_interactive(resume=resume or (session_id is not None), session_id=session_id)


def main() -> None:
    """Entry point for autoskillit."""
    _first_arg = sys.argv[1] if len(sys.argv) > 1 else "serve"
    if _first_arg != "serve":
        from autoskillit.cli._init_helpers import _user_claude_json_path, evict_direct_mcp_entry

        evict_direct_mcp_entry(_user_claude_json_path())

        from autoskillit.cli._update_checks import run_update_checks

        run_update_checks(command=_first_arg)
    app()
