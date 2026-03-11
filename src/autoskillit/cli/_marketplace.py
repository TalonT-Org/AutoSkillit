"""Marketplace and plugin management commands: install, upgrade."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import autoskillit.cli._hooks as _hooks_mod
from autoskillit.cli._hooks import (
    _evict_stale_autoskillit_hooks,
    sync_hooks_to_settings,
)
from autoskillit.core import _atomic_write, is_git_worktree, pkg_root

_VALID_SCOPES = {"user", "project", "local"}
_MARKETPLACE_NAME = "autoskillit-local"


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

    # Regenerate hooks.json from the canonical registry with absolute paths
    from autoskillit.hooks import generate_hooks_json

    hooks_json_path = pkg_root() / "hooks" / "hooks.json"
    _atomic_write(hooks_json_path, json.dumps(generate_hooks_json(), indent=2) + "\n")

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
    settings_path = _hooks_mod._claude_settings_path(scope)
    _evict_stale_autoskillit_hooks(settings_path)
    sync_hooks_to_settings(settings_path)
    _print_next_steps()


def upgrade():
    """Migrate a project from .autoskillit/scripts/ to .autoskillit/recipes/.

    Renames the directory and rewrites YAML top-level keys:
      inputs: -> ingredients:
      constraints: -> kitchen_rules:

    Idempotent: safe to run multiple times.
    """
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


def _print_next_steps() -> None:
    """Print concise post-install getting started instructions."""
    print("\nAutoskillit ready. Next steps:")
    print("  1. cd to your project directory")
    print("  2. autoskillit init           — create project config + register hooks")
    print(
        "  3. autoskillit cook setup-project  — explore your project and generate tailored recipes"
    )
    print("  4. autoskillit doctor          — verify your setup")
