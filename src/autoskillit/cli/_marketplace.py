"""Marketplace and plugin management commands: install, upgrade."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import regex as re

import autoskillit.cli._hooks as _hooks_mod
from autoskillit.cli._init_helpers import (
    _user_claude_json_path,
    evict_direct_mcp_entry,
    validate_public_plugin_projection,
)
from autoskillit.core import (
    DIRECT_INSTALL_CACHE_SUBDIR,
    SkillExecutionRole,
    SkillSource,
    atomic_write,
    get_logger,
    is_git_worktree,
    pkg_root,
)
from autoskillit.hooks import generate_hooks_json
from autoskillit.workspace import (
    DefaultSkillResolver,
    EffectiveSkillCatalog,
    SkillCatalogEntry,
    SkillProjectionContext,
    materialize_sanitized_plugin_root,
)

logger = get_logger(__name__)

_VALID_SCOPES = {"user", "project", "local"}
_MARKETPLACE_NAME = "autoskillit-local"


class _InstallFailed(Exception):
    """A post-preflight install step failed; the caller must roll back."""


def _plugin_cache_dir() -> Path:
    return (
        Path.home() / ".claude" / "plugins" / "cache" / DIRECT_INSTALL_CACHE_SUBDIR / "autoskillit"
    )


def _installed_plugin_root() -> Path:
    from autoskillit.cli._plugin_artifact import current_installed_plugin_root

    return current_installed_plugin_root()


def _clear_plugin_cache(
    *,
    on_retirement_created: Callable[[str], None] | None = None,
) -> tuple[str, ...]:
    """Queue exact old versions and remove their installed_plugins.json reference."""
    from autoskillit import __version__
    from autoskillit.cli._installed_plugins import InstalledPluginsFile
    from autoskillit.cli._plugin_artifact import (
        InstalledPluginArtifactRetirementOwner,
        _read_installed_plugin_identity,
        installed_artifact_lock_path,
    )
    from autoskillit.core import (
        _AUTOSKILLIT_PLUGIN_KEY,
        ArtifactLease,
        PluginArtifactValidationError,
        _InstallLock,
    )

    cache_dir = _plugin_cache_dir()
    owner = InstalledPluginArtifactRetirementOwner(cache_dir)
    with _InstallLock():
        candidates = (
            tuple(
                path
                for path in sorted(cache_dir.iterdir(), key=lambda item: item.name)
                if path.name != __version__
                and not path.name.startswith(".")
                and path.is_dir()
                and not path.is_symlink()
            )
            if cache_dir.is_dir()
            else ()
        )

    created_ids: list[str] = []
    deadline = datetime.now(UTC) + timedelta(hours=6)
    for candidate in candidates:
        reader = ArtifactLease.acquire_shared(installed_artifact_lock_path(candidate))
        try:
            with _InstallLock():
                try:
                    identity = _read_installed_plugin_identity(candidate)
                except PluginArtifactValidationError:
                    continue
                result = owner.enqueue_retirement(identity, deadline)
                if result.created:
                    created_ids.append(result.record_id)
                    if on_retirement_created is not None:
                        on_retirement_created(result.record_id)
        finally:
            reader.close()

    with _InstallLock():
        InstalledPluginsFile().remove(_AUTOSKILLIT_PLUGIN_KEY)
    return tuple(created_ids)


def _assert_not_worktree() -> None:
    """Refuse to install from a git worktree.

    Hoisted out of ``_ensure_marketplace`` into ``install()``'s preflight so it
    runs ahead of every persistent mutation *and* ahead of the ``CLAUDECODE``
    check. Order matters: a worktree install from inside a Claude Code session
    must report the worktree, not print "run these commands in a regular
    terminal" and return — the generic deferral text names the wrong problem.
    """
    pkg_dir = pkg_root()
    if is_git_worktree(pkg_dir):
        raise SystemExit(
            "ERROR: 'autoskillit install' cannot be run when the package\n"
            "is installed from a git worktree.\n\n"
            f"  Detected worktree path: {pkg_dir}\n\n"
            "The marketplace projection would be sourced from this transient path.\n\n"
            "Fix: run 'autoskillit install' from the main project checkout:\n"
            "  cd /path/to/main/repo && autoskillit install"
        )


def _ensure_marketplace() -> Path:
    """Create or update the local marketplace directory."""
    from autoskillit import __version__

    pkg_dir = pkg_root()
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
    atomic_write(
        plugin_dir / "marketplace.json",
        json.JSONEncoder(indent=2).encode(manifest) + "\n",
    )

    public_plugin_root = marketplace_dir / "plugins" / "autoskillit"
    source_infos = tuple(
        skill for skill in DefaultSkillResolver().list_all() if skill.source is SkillSource.BUNDLED
    )
    catalog = EffectiveSkillCatalog(
        skills=tuple(SkillCatalogEntry.from_skill_info(skill) for skill in source_infos),
        execution_role=SkillExecutionRole.SESSION,
    )
    private_manifest = materialize_sanitized_plugin_root(
        pkg_dir,
        public_plugin_root,
        catalog,
        SkillProjectionContext(
            cwd=Path.cwd().resolve(),
            catalog=catalog,
        ),
    )
    atomic_write(
        public_plugin_root / "hooks" / "hooks.json",
        json.JSONEncoder(indent=2).encode(generate_hooks_json()) + "\n",
    )
    validate_public_plugin_projection(
        pkg_dir,
        public_plugin_root,
        private_manifest,
        source_infos,
    )

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


class _InstallSnapshot:
    """Pre-attempt state of every file ``install()`` mutates, for rollback.

    ``install()`` used to retire the live plugin cache before securing its
    replacement and never rolled back, so a failure between those two points
    left ``installed_plugins.json`` pointing at a directory queued for deletion
    — a dangling pointer roughly two hours later, once the sweeper ran. Every
    failure path now restores this snapshot instead.
    """

    def __init__(self) -> None:
        from autoskillit.cli._plugin_artifact import installed_artifact_manifest_path

        self._marketplace_manifest = self._read(_marketplace_manifest_path())
        self._installed_plugins = self._read(_installed_plugins_json_path())
        self._target_root = _installed_plugin_root()
        self._artifact_manifest_path = installed_artifact_manifest_path(self._target_root)
        self._artifact_manifest = self._read(self._artifact_manifest_path)
        self._target_backup: Path | None = None
        self._target_mutation_owned = False
        self._created_retiring_record_ids: set[str] = set()

    @staticmethod
    def _read(path: Path) -> str | None:
        try:
            return path.read_text()
        except OSError:
            return None

    def _restore(self, path: Path, content: str | None) -> None:
        if content is None:
            if path.is_file():
                path.unlink()
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(path, content)

    def stage_target_root(self) -> None:
        """Move the unleased target aside while its exclusive lease is held."""
        if not self._target_root.exists():
            self._target_mutation_owned = True
            return
        backup = self._target_root.parent / (
            f".{self._target_root.name}.autoskillit-install-backup-{uuid.uuid4().hex}"
        )
        os.replace(self._target_root, backup)
        self._target_backup = backup
        self._target_mutation_owned = True
        if self._artifact_manifest_path.is_file() or self._artifact_manifest_path.is_symlink():
            self._artifact_manifest_path.unlink()

    def track_retirement(self, record_id: str) -> None:
        self._created_retiring_record_ids.add(record_id)

    def rollback(self) -> None:
        """Restore the manifest, the registry, and the retiring queue."""
        from autoskillit.core import remove_retiring_records

        if self._target_mutation_owned:
            if self._target_root.is_dir():
                shutil.rmtree(self._target_root)
            elif self._target_root.exists() or self._target_root.is_symlink():
                self._target_root.unlink()
            if self._target_backup is not None and self._target_backup.exists():
                os.replace(self._target_backup, self._target_root)
                self._target_backup = None
            self._target_mutation_owned = False

        self._restore(_marketplace_manifest_path(), self._marketplace_manifest)
        self._restore(_installed_plugins_json_path(), self._installed_plugins)
        self._restore(self._artifact_manifest_path, self._artifact_manifest)
        remove_retiring_records(self._created_retiring_record_ids)

    def commit(self) -> None:
        """Discard the rollback copy after the new incarnation is published."""
        if self._target_backup is not None and self._target_backup.is_dir():
            shutil.rmtree(self._target_backup)
        self._target_backup = None
        self._target_mutation_owned = False


def _marketplace_manifest_path() -> Path:
    return Path.home() / ".autoskillit" / "marketplace" / ".claude-plugin" / "marketplace.json"


def _installed_plugins_json_path() -> Path:
    return Path.home() / ".claude" / "plugins" / "installed_plugins.json"


def install(*, scope: str = "user") -> bool:
    """Install the plugin persistently for Claude Code.

    Sets up a local marketplace and installs the plugin so it loads
    automatically in every Claude Code session (no --plugin-dir needed).

    After updating autoskillit, re-run this command to refresh the cache.

    Transactional: every check that can decline the install runs before the
    first persistent mutation, and every failure afterwards restores the
    pre-attempt state.

    Parameters
    ----------
    scope
        Where to enable: "user" (all projects), "project" (shared via repo),
        or "local" (this project, gitignored).
    """
    # ---- Preflight. No persistent mutation may happen above this line. ----
    # Order is load-bearing: the worktree guard must precede CLAUDECODE so a
    # worktree install inside a Claude Code session names the real problem.
    _assert_not_worktree()

    if scope not in _VALID_SCOPES:
        print(f"Invalid scope: {scope!r}. Must be one of: {', '.join(sorted(_VALID_SCOPES))}")
        sys.exit(1)

    from autoskillit.config import load_config

    cfg = load_config(Path.cwd())

    from autoskillit.execution import get_backend

    backend = get_backend(cfg.agent_backend.backend)
    if not backend.capabilities.plugin_install_capable:
        print(
            f"\nPlugin install requires a plugin_install_capable backend.\n"
            f"Current backend: {cfg.agent_backend.backend!r}\n"
        )
        return False

    marketplace_dir = Path.home() / ".autoskillit" / "marketplace"
    plugin_ref = f"autoskillit@{_MARKETPLACE_NAME}"

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

    # ---- Mutation begins. Everything below rolls back on failure. ----
    from autoskillit.workspace import reconcile_install_artifacts, verify_install_state

    # Repair artifacts a previous release left in a now-retired shape before
    # anything reads them (e.g. the pre-0.10.892 symlinked plugin root).
    for repaired in reconcile_install_artifacts():
        print(f"Repaired legacy install artifact: ~/{repaired}")

    from autoskillit.cli._plugin_artifact import _validate_installed_plugin_destination
    from autoskillit.core import PluginArtifactValidationError

    target_root = _installed_plugin_root()
    try:
        _validate_installed_plugin_destination(target_root)
    except PluginArtifactValidationError as exc:
        print(f"Unsafe installed plugin target: {exc}")
        raise SystemExit(1) from exc

    snapshot = _InstallSnapshot()

    from autoskillit.cli._plugin_artifact import installed_artifact_lock_path
    from autoskillit.core import (
        ArtifactLease,
        ArtifactLeaseContention,
        PluginArtifactPublicationError,
        _InstallLock,
    )

    try:
        marketplace_dir = _ensure_marketplace()
        print(f"Marketplace prepared: {marketplace_dir}")

        _ensure_workspace_ready()

        target_writer = ArtifactLease.acquire_exclusive(
            installed_artifact_lock_path(_installed_plugin_root()),
            blocking=False,
        )
    except ArtifactLeaseContention as exc:
        snapshot.rollback()
        print("Installed plugin is in use by an active session; retry after it exits.")
        raise SystemExit(1) from exc
    except _InstallFailed as exc:
        snapshot.rollback()
        print(str(exc))
        sys.exit(1)
    except BaseException:
        snapshot.rollback()
        raise
    else:
        try:
            with _InstallLock():
                snapshot.stage_target_root()
            _clear_plugin_cache(on_retirement_created=snapshot.track_retirement)

            # Register the marketplace (idempotent)
            result = subprocess.run(
                ["claude", "plugin", "marketplace", "add", str(marketplace_dir)],
                capture_output=True,
                text=True,
                stdin=subprocess.DEVNULL,
                timeout=30,
                pass_fds=(),
            )
            if result.returncode != 0:
                raise _InstallFailed(f"Failed to register marketplace: {result.stderr.strip()}")
            print("Marketplace registered.")

            # Install the plugin
            result = subprocess.run(
                ["claude", "plugin", "install", plugin_ref, "--scope", scope],
                capture_output=True,
                text=True,
                stdin=subprocess.DEVNULL,
                timeout=30,
                pass_fds=(),
            )
            if result.returncode != 0:
                raise _InstallFailed(f"Failed to install plugin: {result.stderr.strip()}")
            from autoskillit import __version__
            from autoskillit.cli._plugin_artifact import (
                installed_plugin_semantic_key,
                publish_installed_plugin_artifact,
            )

            try:
                with _InstallLock():
                    publish_installed_plugin_artifact(
                        _installed_plugin_root(),
                        semantic_key=installed_plugin_semantic_key(
                            plugin_ref,
                            __version__,
                        ),
                        _owned_exclusive_lease=target_writer,
                    )
            except PluginArtifactPublicationError as exc:
                raise _InstallFailed(
                    f"Failed to publish installed plugin identity: {exc}"
                ) from exc
            snapshot.commit()
        except _InstallFailed as exc:
            snapshot.rollback()
            print(str(exc))
            sys.exit(1)
        except BaseException:
            snapshot.rollback()
            raise
        finally:
            target_writer.close()

    print(f"Plugin installed: {plugin_ref} (scope: {scope})")

    # Post-install verification via the single consistency authority. This
    # replaces the narrower hooks-only check: a broken hook path was never the
    # only way an install could land inconsistent.
    for finding in verify_install_state():
        logger.warning("install_state_inconsistent", check=finding.check, message=finding.message)
        print(f"WARNING [{finding.check}]: {finding.message}")
    if evict_direct_mcp_entry(_user_claude_json_path()):
        print("Removed stale direct MCP entry from ~/.claude.json")
    # Evict any stale autoskillit hooks from settings.json. The plugin was just
    # activated and now provides hooks via hooks.json — settings.json must not
    # contain them (dual registration causes every hook to fire twice).
    _hooks_mod._evict_stale_autoskillit_hooks(_hooks_mod._claude_settings_path(scope))
    from autoskillit.cli.update._update_checks import invalidate_fetch_cache

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
