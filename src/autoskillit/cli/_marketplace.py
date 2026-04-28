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
    sweep_all_scopes_for_orphans,
    sync_hooks_to_settings,
)
from autoskillit.cli._init_helpers import _user_claude_json_path, evict_direct_mcp_entry
from autoskillit.core import atomic_write, is_git_worktree, pkg_root
from autoskillit.hooks import generate_hooks_json

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
        from autoskillit import __version__ as _new_version
        from autoskillit.core import _retire_old_versions

        _retire_old_versions(cache_dir, _new_version)
    else:
        from autoskillit.core import sweep_retiring_cache

        sweep_retiring_cache()

    from autoskillit.cli._installed_plugins import InstalledPluginsFile

    try:
        InstalledPluginsFile().remove(f"autoskillit@{_MARKETPLACE_NAME}")
    except OSError:
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
    atomic_write(plugin_dir / "marketplace.json", json.dumps(manifest, indent=2) + "\n")

    # Symlink to the live package directory
    link_path = marketplace_dir / "plugins" / "autoskillit"
    link_path.parent.mkdir(parents=True, exist_ok=True)
    if link_path.is_symlink() or link_path.exists():
        link_path.unlink()
    link_path.symlink_to(pkg_dir)

    return marketplace_dir


def _ensure_workspace_ready() -> None:
    """Repair project workspace state that install() is responsible for.

    Called after the CLAUDECODE guard — only when the actual install proceeds.
    Idempotent: safe to call on any project state.
    """
    from autoskillit.core import ensure_project_temp

    project_dir = Path.cwd()
    # Repair .autoskillit/.gitignore and ensure temp/ exists
    if (project_dir / ".autoskillit").is_dir():
        ensure_project_temp(project_dir)

    # Migrate legacy .autoskillit/scripts/ to .autoskillit/recipes/ if present
    if (project_dir / ".autoskillit" / "scripts").exists():
        try:
            upgrade()
        except OSError as exc:
            print(f"Warning: migration upgrade() failed (non-fatal): {exc}")


def install(*, scope: str = "user") -> bool:
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
        return False  # deferred: user must complete manually in a regular terminal

    if shutil.which("claude") is None:
        print("\nERROR: 'claude' command not found on PATH.")
        print("Install Claude Code, then run:")
        print(f"  claude plugin marketplace add {marketplace_dir}")
        print(f"  claude plugin install {plugin_ref} --scope {scope}")
        print("\nThen run: autoskillit init (in your project directory)")
        sys.exit(1)

    _ensure_workspace_ready()

    from autoskillit.core import _InstallLock

    with _InstallLock():
        from autoskillit.core import any_kitchen_open

        if any_kitchen_open(project_path=str(Path.cwd())):
            print("Kitchen open for this project — skipping plugin cache clear.")
        else:
            _clear_plugin_cache()

        # Regenerate hooks.json from the canonical registry with absolute paths
        hooks_json_path = pkg_root() / "hooks" / "hooks.json"
        atomic_write(hooks_json_path, json.dumps(generate_hooks_json(), indent=2) + "\n")

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
    if evict_direct_mcp_entry(_user_claude_json_path()):
        print("Removed stale direct MCP entry from ~/.claude.json")
    # Cross-scope sweep: evict orphaned autoskillit hooks from ALL scopes before
    # writing canonical entries to the target scope.
    sweep_all_scopes_for_orphans(Path.cwd())
    settings_path = _hooks_mod._claude_settings_path(scope)
    sync_hooks_to_settings(settings_path)
    from autoskillit.cli._update_checks import invalidate_fetch_cache

    invalidate_fetch_cache(Path.home())
    return True


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
            atomic_write(yaml_file, new_text)
            changed += 1

    print(f"Upgraded: directory renamed, {changed} file(s) updated.")
