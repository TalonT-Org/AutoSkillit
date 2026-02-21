"""CLI for autoskillit: serve, init, install, config, skills, workflows, update."""

from __future__ import annotations

import dataclasses
import json
import os
import shutil
import subprocess
import sys
from datetime import UTC
from pathlib import Path

from cyclopts import App

app = App(
    name="autoskillit",
    help="MCP server for orchestrating automated workflows with Claude Code.",
)

config_app = App(name="config", help="Configuration commands.")
skills_app = App(name="skills", help="Skill management.")
workflows_app = App(name="workflows", help="Workflow management.")
workspace_app = App(name="workspace", help="Workspace management.")

app.command(config_app)
app.command(skills_app)
app.command(workflows_app)
app.command(workspace_app)


@app.default
def serve():
    """Start the MCP server (default command)."""
    from autoskillit.server import mcp

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

    plugin_dir = Path(__file__).parent
    print(f"\nPlugin directory: {plugin_dir}")
    print(f"Load with: claude --plugin-dir {plugin_dir}")


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
        return

    if shutil.which("claude") is None:
        print("\nERROR: 'claude' command not found on PATH.")
        print("Install Claude Code, then run:")
        print(f"  claude plugin marketplace add {marketplace_dir}")
        print(f"  claude plugin install {plugin_ref} --scope {scope}")
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
    print("\nUsage: just run 'claude' — skills and MCP tools load automatically.")
    print("Enable tools with: /mcp__autoskillit__enable_tools")


def _ensure_marketplace() -> Path:
    """Create or update the local marketplace directory."""
    from autoskillit import __version__

    pkg_dir = Path(__file__).parent
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
def update():
    """Refresh built-in workflows, preserving customized ones."""
    from autoskillit.workflow_loader import builtin_workflows_dir

    project_wf_dir = Path.cwd() / ".autoskillit" / "workflows"
    if not project_wf_dir.is_dir():
        print("No project workflows directory found. Nothing to update.")
        return

    builtin_dir = builtin_workflows_dir()
    updated = []
    skipped = []

    for builtin_file in sorted(builtin_dir.glob("*.yaml")):
        project_file = project_wf_dir / builtin_file.name
        if not project_file.exists():
            shutil.copy2(builtin_file, project_file)
            updated.append(builtin_file.stem)
        elif project_file.read_text() == builtin_file.read_text():
            shutil.copy2(builtin_file, project_file)
            updated.append(builtin_file.stem)
        else:
            skipped.append(builtin_file.stem)

    if updated:
        print(f"Updated: {', '.join(updated)}")
    if skipped:
        print(f"Skipped (customized): {', '.join(skipped)}")
    if not updated and not skipped:
        print("No built-in workflows found.")


@app.command
def doctor(*, output_json: bool = False):
    """Check project setup for common issues.

    Parameters
    ----------
    output_json
        Output results as JSON instead of human-readable text.
    """
    warnings: list[str] = []

    # Check 1: Stale MCP servers — dead binaries or nonexistent paths
    claude_json = Path.home() / ".claude.json"
    if claude_json.is_file():
        data = json.loads(claude_json.read_text())
        servers = data.get("mcpServers", {})
        for name, entry in servers.items():
            if name == "autoskillit":
                continue
            cmd = entry.get("command", "")
            if not cmd:
                continue
            cmd_path = Path(cmd)
            if cmd_path.is_absolute() and not cmd_path.exists():
                warnings.append(
                    f"WARNING: MCP server '{name}' has dead command path: {cmd}. "
                    f"Remove with: claude mcp remove --scope user {name}"
                )
            elif not cmd_path.is_absolute() and shutil.which(cmd) is None:
                warnings.append(
                    f"WARNING: MCP server '{name}' command not found: {cmd}. "
                    f"Remove with: claude mcp remove --scope user {name}"
                )

    # Check 2: Plugin metadata exists in package
    pkg_dir = Path(__file__).parent
    if not (pkg_dir / ".claude-plugin" / "plugin.json").is_file():
        warnings.append("WARNING: Plugin metadata missing. Reinstall autoskillit.")

    # Check 3: autoskillit command on PATH
    if shutil.which("autoskillit") is None:
        warnings.append("WARNING: 'autoskillit' command not found on PATH.")

    # Check 4: Config exists
    if not (Path.cwd() / ".autoskillit" / "config.yaml").is_file():
        warnings.append("WARNING: No project config found. Run: autoskillit init")

    # Output
    if output_json:
        print(json.dumps({"warnings": warnings}, indent=2))
    elif warnings:
        for w in warnings:
            print(w)
    else:
        print("All checks passed.")


@config_app.command(name="show")
def config_show():
    """Show resolved configuration as JSON."""
    from autoskillit.config import load_config

    cfg = load_config(Path.cwd())
    print(json.dumps(dataclasses.asdict(cfg), indent=2, default=list))


@skills_app.command(name="list")
def skills_list():
    """List bundled skills provided by the plugin."""
    from autoskillit.skill_resolver import SkillResolver

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
    """Create a workspace directory with the reset guard marker.

    The directory must not exist or must be empty (or contain only the marker).

    Parameters
    ----------
    path
        Path to the workspace directory to initialize.
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
            print("workspace init only works on empty or new directories.", file=sys.stderr)
            sys.exit(1)

    target.mkdir(parents=True, exist_ok=True)
    marker = target / marker_name
    marker.write_text(
        _MARKER_CONTENT.format(
            timestamp=datetime.now(UTC).isoformat(),
            version=__version__,
        )
    )
    print(f"Workspace initialized: {target}")
    print(f"Reset guard marker created: {marker}")


@workflows_app.command(name="list")
def workflows_list():
    """List available workflows with sources."""
    from autoskillit.workflow_loader import list_workflows

    workflows = list_workflows(Path.cwd())
    if not workflows:
        print("No workflows found.")
        return

    name_w = max(len(w.name) for w in workflows)
    src_w = max(len(w.source) for w in workflows)
    print(f"{'NAME':<{name_w}}  {'SOURCE':<{src_w}}  DESCRIPTION")
    print(f"{'-' * name_w}  {'-' * src_w}  {'-' * 11}")
    for w in workflows:
        print(f"{w.name:<{name_w}}  {w.source:<{src_w}}  {w.description}")


@workflows_app.command(name="show")
def workflows_show(name: str):
    """Print the YAML content of a named workflow."""
    from autoskillit.workflow_loader import list_workflows

    workflows = list_workflows(Path.cwd())
    match = next((w for w in workflows if w.name == name), None)
    if match is None:
        print(f"No workflow named '{name}'.", file=sys.stderr)
        sys.exit(1)
    print(match.path.read_text())


# --- Init helpers ---


def _prompt_test_command() -> list[str]:
    default = "pytest -v"
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
"""


def main() -> None:
    """Entry point for autoskillit."""
    app()
