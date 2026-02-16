"""Minimal CLI for automation-mcp: serve, init, config show, skills list/install."""

from __future__ import annotations

import dataclasses
import json
import shutil
import sys
from pathlib import Path


def main() -> None:
    """Entry point: automation-mcp [serve|init|config show|skills list|skills install]."""
    args = sys.argv[1:]

    if not args or args[0] == "serve":
        _serve()
    elif args[0] == "init":
        _init(args[1:])
    elif args[0] == "config" and len(args) > 1 and args[1] == "show":
        _config_show()
    elif args[0] == "skills" and len(args) > 1 and args[1] == "list":
        _skills_list()
    elif args[0] == "skills" and len(args) > 2 and args[1] == "install":
        _skills_install(args[2])
    else:
        print(f"Unknown command: {' '.join(args)}", file=sys.stderr)
        print(
            "Usage: automation-mcp [serve|init|config show|skills list|skills install <name>]",
            file=sys.stderr,
        )
        sys.exit(1)


def _serve() -> None:
    from automation_mcp.server import mcp

    mcp.run()


def _init(args: list[str]) -> None:
    import yaml

    project_dir = Path.cwd()
    config_dir = project_dir / ".automation-mcp"
    config_dir.mkdir(exist_ok=True)
    config_path = config_dir / "config.yaml"

    if config_path.exists() and "--force" not in args:
        print(f"Config already exists: {config_path}")
        print("Use --force to overwrite.")
        return

    template = {
        "version": 1,
        "test_check": {"command": ["pytest", "-v"]},
        "classify_fix": {"path_prefixes": []},
        "reset_executor": {"command": None, "preserve_dirs": [".agent_data", "plans"]},
        "implement_gate": {
            "marker": "Dry-walkthrough verified = TRUE",
            "skill_names": ["/implement-worktree", "/implement-worktree-no-merge"],
        },
        "safety": {
            "playground_guard": True,
            "require_dry_walkthrough": True,
            "test_gate_on_merge": True,
        },
    }
    config_path.write_text(yaml.dump(template, default_flow_style=False, sort_keys=False))
    print(f"Config written to: {config_path}")
    print("Edit the config to match your project, then restart the MCP server.")


def _config_show() -> None:
    from automation_mcp.config import load_config

    cfg = load_config(Path.cwd())
    print(json.dumps(dataclasses.asdict(cfg), indent=2, default=list))


def _skills_list() -> None:
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


def _skills_install(name: str) -> None:
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
