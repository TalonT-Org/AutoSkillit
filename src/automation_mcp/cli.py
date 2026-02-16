"""CLI for automation-mcp: serve, init, config, skills, workflows, update."""

from __future__ import annotations

import dataclasses
import json
import shutil
import sys
from pathlib import Path

from cyclopts import App

app = App(
    name="automation-mcp",
    help="MCP server for orchestrating automated workflows with Claude Code.",
)

config_app = App(name="config", help="Configuration commands.")
skills_app = App(name="skills", help="Skill management.")
workflows_app = App(name="workflows", help="Workflow management.")

app.command(config_app)
app.command(skills_app)
app.command(workflows_app)


@app.default
def serve():
    """Start the MCP server (default command)."""
    from automation_mcp.server import mcp

    mcp.run()


@app.command(name="serve")
def serve_explicit():
    """Start the MCP server."""
    serve()


@app.command
def init(*, quick: bool = False, force: bool = False):
    """Initialize automation-mcp for a project.

    Parameters
    ----------
    quick
        Minimal questions: test command and base branch only.
    force
        Overwrite existing config without prompting.
    """
    import yaml

    project_dir = Path.cwd()
    config_dir = project_dir / ".automation-mcp"
    config_dir.mkdir(exist_ok=True)
    config_path = config_dir / "config.yaml"

    if config_path.exists() and not force:
        print(f"Config already exists: {config_path}")
        print("Use --force to overwrite.")
        return

    if quick:
        answers = _quick_init()
    else:
        answers = _interactive_init()

    config_path.write_text(yaml.dump(answers, default_flow_style=False, sort_keys=False))
    print(f"Config written to: {config_path}")


@app.command
def update():
    """Refresh built-in workflows, preserving customized ones."""
    from automation_mcp.workflow_loader import builtin_workflows_dir

    project_wf_dir = Path.cwd() / ".automation-mcp" / "workflows"
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


@config_app.command(name="show")
def config_show():
    """Show resolved configuration as JSON."""
    from automation_mcp.config import load_config

    cfg = load_config(Path.cwd())
    print(json.dumps(dataclasses.asdict(cfg), indent=2, default=list))


@skills_app.command(name="list")
def skills_list():
    """List available skills with their resolution source."""
    from automation_mcp.config import load_config
    from automation_mcp.skill_resolver import SkillResolver

    cfg = load_config(Path.cwd())
    resolver = SkillResolver(Path.cwd(), cfg)
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


@skills_app.command(name="install")
def skills_install(name: str):
    """Install a bundled skill to the project's .claude/skills/ directory."""
    from automation_mcp.skill_resolver import bundled_skills_dir

    src = bundled_skills_dir() / name / "SKILL.md"
    if not src.is_file():
        print(f"No bundled skill named '{name}'.", file=sys.stderr)
        sys.exit(1)

    dest_dir = Path.cwd() / ".claude" / "skills" / name
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / "SKILL.md"
    shutil.copy2(src, dest)
    print(f"Installed '{name}' to {dest}")


@workflows_app.command(name="list")
def workflows_list():
    """List available workflows with sources."""
    from automation_mcp.workflow_loader import list_workflows

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
    from automation_mcp.workflow_loader import list_workflows

    workflows = list_workflows(Path.cwd())
    match = next((w for w in workflows if w.name == name), None)
    if match is None:
        print(f"No workflow named '{name}'.", file=sys.stderr)
        sys.exit(1)
    print(match.path.read_text())


# --- Init helpers ---


def _quick_init() -> dict:
    test_cmd = _prompt("Test command", "pytest -v")
    return {
        "version": 1,
        "test_check": {"command": test_cmd.split()},
        "safety": {
            "playground_guard": True,
            "require_dry_walkthrough": True,
            "test_gate_on_merge": True,
        },
    }


def _interactive_init() -> dict:
    project_type = _choose(
        "Project type",
        ["Python (pytest)", "TypeScript", "Go", "Custom"],
    )
    test_defaults = {
        "Python (pytest)": "pytest -v",
        "TypeScript": "npm test",
        "Go": "go test ./...",
        "Custom": "",
    }
    test_cmd = _prompt("Test command", test_defaults.get(project_type, ""))

    config: dict = {
        "version": 1,
        "test_check": {"command": test_cmd.split()},
    }

    uses_planner = _confirm("Do you use a planner/executor system?", default=False)
    if uses_planner:
        prefixes = _prompt("Planner path prefixes (comma-separated)", "")
        config["classify_fix"] = {
            "path_prefixes": [p.strip() for p in prefixes.split(",") if p.strip()]
        }
        reset_cmd = _prompt("Executor reset command (blank for none)", "")
        config["reset_executor"] = {
            "command": reset_cmd.split() if reset_cmd else None,
            "preserve_dirs": [".agent_data", "plans"],
        }

    config["safety"] = {
        "playground_guard": True,
        "require_dry_walkthrough": True,
        "test_gate_on_merge": True,
    }

    return config


def _prompt(message: str, default: str) -> str:
    try:
        from InquirerPy import inquirer

        return inquirer.text(message=message, default=default).execute()
    except ImportError:
        suffix = f" [{default}]" if default else ""
        answer = input(f"{message}{suffix}: ").strip()
        return answer if answer else default


def _choose(message: str, choices: list[str]) -> str:
    try:
        from InquirerPy import inquirer

        return inquirer.select(message=message, choices=choices).execute()
    except ImportError:
        print(f"{message}:")
        for i, c in enumerate(choices, 1):
            print(f"  {i}. {c}")
        while True:
            raw = input("Choice [1]: ").strip()
            idx = int(raw) - 1 if raw else 0
            if 0 <= idx < len(choices):
                return choices[idx]


def _confirm(message: str, *, default: bool = True) -> bool:
    try:
        from InquirerPy import inquirer

        return inquirer.confirm(message=message, default=default).execute()
    except ImportError:
        suffix = " [Y/n]" if default else " [y/N]"
        answer = input(f"{message}{suffix}: ").strip().lower()
        if not answer:
            return default
        return answer in ("y", "yes")


def main() -> None:
    """Entry point for automation-mcp."""
    app()
