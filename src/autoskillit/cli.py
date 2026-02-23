"""CLI for autoskillit: serve, init, install, orchestrate, config, skills, workflows, update."""

from __future__ import annotations

import dataclasses
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC
from enum import StrEnum
from pathlib import Path

from cyclopts import App


class Severity(StrEnum):
    OK = "ok"
    WARNING = "warning"
    ERROR = "error"


@dataclass
class DoctorResult:
    severity: Severity
    check: str
    message: str


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

    print("\nReady! Start Claude Code and enable tools:")
    print("  claude")
    if _is_plugin_installed():
        print("  /mcp__plugin_autoskillit_autoskillit__enable_tools")
    else:
        print("  /mcp__autoskillit__enable_tools")


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
    _print_next_steps()


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


def _is_plugin_installed() -> bool:
    """Check if autoskillit is installed as a Claude Code plugin."""
    settings_path = Path.home() / ".claude" / "settings.json"
    if not settings_path.is_file():
        return False
    try:
        data = json.loads(settings_path.read_text())
        enabled = data.get("enabledPlugins", {})
        return any(key.startswith("autoskillit@") for key in enabled if enabled[key])
    except (json.JSONDecodeError, AttributeError):
        return False


@app.command
def doctor(*, output_json: bool = False):
    """Check project setup for common issues.

    Parameters
    ----------
    output_json
        Output results as JSON instead of human-readable text.
    """
    results: list[DoctorResult] = []

    # Check 1: Stale MCP servers — dead binaries or nonexistent paths
    stale_servers: list[str] = []
    has_standalone_autoskillit = False
    claude_json = Path.home() / ".claude.json"
    if claude_json.is_file():
        data = json.loads(claude_json.read_text())
        servers = data.get("mcpServers", {})
        for name, entry in servers.items():
            if name == "autoskillit":
                has_standalone_autoskillit = True
                continue
            cmd = entry.get("command", "")
            if not cmd:
                continue
            cmd_path = Path(cmd)
            if cmd_path.is_absolute() and not cmd_path.exists():
                stale_servers.append(
                    f"MCP server '{name}' has dead command path: {cmd}. "
                    f"Remove with: claude mcp remove --scope user {name}"
                )
            elif not cmd_path.is_absolute() and shutil.which(cmd) is None:
                stale_servers.append(
                    f"MCP server '{name}' command not found: {cmd}. "
                    f"Remove with: claude mcp remove --scope user {name}"
                )
    if stale_servers:
        for msg in stale_servers:
            results.append(DoctorResult(Severity.ERROR, "stale_mcp_servers", msg))
    else:
        results.append(
            DoctorResult(Severity.OK, "stale_mcp_servers", "No stale MCP servers detected")
        )

    # Check 1b: Duplicate autoskillit — standalone entry alongside plugin install
    plugin_installed = _is_plugin_installed()
    if has_standalone_autoskillit and plugin_installed:
        results.append(
            DoctorResult(
                Severity.ERROR,
                "duplicate_mcp_server",
                "Standalone 'autoskillit' MCP server in ~/.claude.json duplicates "
                "the plugin registration. This spawns two server processes per session. "
                "Remove with: claude mcp remove autoskillit",
            )
        )
    elif has_standalone_autoskillit and not plugin_installed:
        results.append(
            DoctorResult(
                Severity.WARNING,
                "duplicate_mcp_server",
                "Standalone 'autoskillit' MCP server found in ~/.claude.json. "
                "Consider using 'autoskillit install' for persistent plugin registration instead.",
            )
        )
    else:
        results.append(
            DoctorResult(Severity.OK, "duplicate_mcp_server", "No duplicate MCP registrations")
        )

    # Check 2: Plugin metadata exists in package
    pkg_dir = Path(__file__).parent
    if not (pkg_dir / ".claude-plugin" / "plugin.json").is_file():
        results.append(
            DoctorResult(
                Severity.ERROR,
                "plugin_metadata",
                "Plugin metadata missing. Reinstall autoskillit.",
            )
        )
    else:
        results.append(DoctorResult(Severity.OK, "plugin_metadata", "Plugin metadata exists"))

    # Check 3: autoskillit command on PATH
    if shutil.which("autoskillit") is None:
        results.append(
            DoctorResult(
                Severity.WARNING,
                "autoskillit_on_path",
                "'autoskillit' command not found on PATH.",
            )
        )
    else:
        results.append(
            DoctorResult(Severity.OK, "autoskillit_on_path", "autoskillit command found on PATH")
        )

    # Check 4: Config exists
    if not (Path.cwd() / ".autoskillit" / "config.yaml").is_file():
        results.append(
            DoctorResult(
                Severity.WARNING,
                "project_config",
                "No project config found. Run: autoskillit init",
            )
        )
    else:
        results.append(DoctorResult(Severity.OK, "project_config", "Project config exists"))

    # Check 5: Version consistency — plugin.json vs package version
    from autoskillit.server import _version_info

    info = _version_info()
    if info["plugin_json_version"] is None:
        results.append(
            DoctorResult(
                Severity.ERROR,
                "version_consistency",
                "Cannot verify version consistency: plugin.json not found. "
                "Reinstall: autoskillit install",
            )
        )
    elif not info["match"]:
        results.append(
            DoctorResult(
                Severity.ERROR,
                "version_consistency",
                f"Package version is {info['package_version']} but plugin.json "
                f"reports {info['plugin_json_version']}. "
                f"Update plugin.json or reinstall: autoskillit install",
            )
        )
    else:
        results.append(
            DoctorResult(
                Severity.OK,
                "version_consistency",
                f"Version {info['package_version']} consistent across package and plugin.json",
            )
        )

    # Check 6: Marketplace symlink freshness
    pkg_version = info["package_version"]
    marketplace_link = Path.home() / ".autoskillit" / "marketplace" / "plugins" / "autoskillit"
    if marketplace_link.is_symlink():
        target = marketplace_link.resolve()
        if not target.is_dir():
            results.append(
                DoctorResult(
                    Severity.ERROR,
                    "marketplace_freshness",
                    f"Marketplace symlink points to missing directory: {target}. "
                    f"Re-run: autoskillit install",
                )
            )
        else:
            mkt_json = marketplace_link.parent.parent / ".claude-plugin" / "marketplace.json"
            if mkt_json.is_file():
                mkt_data = json.loads(mkt_json.read_text())
                plugins = mkt_data.get("plugins", [])
                mkt_version = plugins[0].get("version", "") if plugins else ""
                if mkt_version != pkg_version:
                    results.append(
                        DoctorResult(
                            Severity.WARNING,
                            "marketplace_freshness",
                            f"Marketplace manifest version ({mkt_version}) "
                            f"differs from installed version ({pkg_version}). "
                            f"Re-run: autoskillit install",
                        )
                    )
                else:
                    results.append(
                        DoctorResult(
                            Severity.OK,
                            "marketplace_freshness",
                            "Marketplace manifest version matches installed version",
                        )
                    )
    elif (Path.home() / ".autoskillit" / "marketplace").is_dir():
        results.append(
            DoctorResult(
                Severity.WARNING,
                "marketplace_freshness",
                "Marketplace symlink missing. Re-run: autoskillit install",
            )
        )

    # Output
    if output_json:
        print(
            json.dumps(
                {
                    "results": [
                        {"severity": r.severity, "check": r.check, "message": r.message}
                        for r in results
                    ]
                },
                indent=2,
            )
        )
    else:
        has_problems = any(r.severity != Severity.OK for r in results)
        if has_problems:
            for r in results:
                if r.severity != Severity.OK:
                    print(f"{r.severity.upper()}: {r.message}")
        else:
            for r in results:
                print(f"{r.severity}: {r.message}")


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

    workflows = list_workflows(Path.cwd()).items
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

    workflows = list_workflows(Path.cwd()).items
    match = next((w for w in workflows if w.name == name), None)
    if match is None:
        print(f"No workflow named '{name}'.", file=sys.stderr)
        sys.exit(1)
    print(match.path.read_text())


def _build_orchestrator_prompt(script_yaml: str) -> str:
    """Build the --append-system-prompt content for an orchestrate session."""
    return f"""\
You are a pipeline orchestrator. Execute the pipeline script below step-by-step.

FIRST: Type the enable_tools prompt to activate AutoSkillit MCP tools before \
executing any pipeline steps. The exact prompt name depends on how the server \
was loaded — for example, plugin installs use \
/mcp__plugin_autoskillit_autoskillit__enable_tools while --plugin-dir loading \
uses a different prefix. Check the available MCP prompts to find the correct \
enable_tools prompt name.

After enabling tools:
1. Present the script to the user using the preview format below
2. Prompt for input values using AskUserQuestion
3. Execute the pipeline steps by calling MCP tools directly

Preview format:

    ## {{name}}
    {{description}}

    **Flow:** {{summary}}

    ### Inputs
    For each input show: name, description, required/optional, default value.
    Distinguish user-supplied inputs (required=true or meaningful defaults)
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

    ### Constraints
    If present, list all constraint strings.
    If absent, note: "No constraints defined"

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
  assess-and-merge) has diagnostic access that the orchestrator does not.
- Your ONLY job is to route to the correct next step and pass the
  required arguments. The downstream skill does the actual work.

FAILURE PREDICATES — when to follow on_failure:
- test_check: {{"passed": false}}
- merge_worktree: "error" key present in response
- run_cmd: {{"success": false}}
- run_skill / run_skill_retry: {{"success": false}}
- classify_fix: "error" key present in response

--- PIPELINE SCRIPT ---
{script_yaml}
--- END PIPELINE SCRIPT ---
"""


@app.command
def orchestrate(script: str):
    """Launch an interactive Claude Code session to execute a pipeline script.

    Starts Claude Code with hard tool restrictions: only AskUserQuestion
    (built-in) and AutoSkillit MCP tools are available. The script is
    injected via --append-system-prompt so the session starts ready to
    execute.

    Parameters
    ----------
    script
        Name of the pipeline script (from .autoskillit/scripts/).
    """
    if os.environ.get("CLAUDECODE"):
        print("ERROR: 'orchestrate' cannot run inside a Claude Code session.")
        print("Run this command in a regular terminal.")
        sys.exit(1)

    from autoskillit.script_loader import list_scripts, load_script

    script_yaml = load_script(Path.cwd(), script)
    if script_yaml is None:
        available = list_scripts(Path.cwd()).items
        print(f"Script not found: '{script}'")
        if available:
            print("Available scripts:")
            for s in available:
                print(f"  - {s.name}")
        else:
            print("No scripts found in .autoskillit/scripts/")
        sys.exit(1)

    # Validate script before launching session
    import yaml

    from autoskillit.workflow_loader import _parse_workflow, validate_workflow

    try:
        data = yaml.safe_load(script_yaml)
    except yaml.YAMLError as exc:
        print(f"Script YAML parse error: {exc}")
        sys.exit(1)

    if not isinstance(data, dict):
        print("Script must contain a YAML mapping.")
        sys.exit(1)

    wf = _parse_workflow(data)
    errors = validate_workflow(wf)
    if errors:
        print(f"Script '{script}' failed validation:")
        for err in errors:
            print(f"  - {err}")
        sys.exit(1)

    if shutil.which("claude") is None:
        print("ERROR: 'claude' command not found on PATH.")
        print("Install Claude Code first: https://docs.anthropic.com/en/docs/claude-code")
        sys.exit(1)

    plugin_dir = Path(__file__).parent
    system_prompt = _build_orchestrator_prompt(script_yaml)

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
    print("  3. Enable tools: /mcp__plugin_autoskillit_autoskillit__enable_tools")


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
#   completion_marker: "%%AUTOSKILLIT_COMPLETE%%"
#
# run_skill_retry:
#   timeout: 7200
#   heartbeat_marker: '"type":"result"'
#   stale_threshold: 1200
#   completion_marker: "%%AUTOSKILLIT_COMPLETE%%"
"""


def main() -> None:
    """Entry point for autoskillit."""
    app()
