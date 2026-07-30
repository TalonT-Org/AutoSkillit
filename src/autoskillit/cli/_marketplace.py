"""Marketplace and plugin management commands: install, upgrade."""

from __future__ import annotations

import filecmp
import importlib.metadata
import json
import os
import shutil
import stat
import subprocess
import uuid
from collections.abc import Callable, Mapping, Sequence
from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from pathlib import Path

import regex as re

import autoskillit.cli._hooks as _hooks_mod
from autoskillit.cli._init_helpers import (
    _user_claude_json_path,
    evict_direct_mcp_entry,
    validate_public_plugin_projection,
)
from autoskillit.cli._install_contract import (
    InstallFailureKind,
    InstallMode,
    InstallOutcome,
    InstallRequest,
    InstallResult,
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
    """An operational install step failed; the caller must compensate."""

    def __init__(self, kind: InstallFailureKind, message: str) -> None:
        self.kind = kind
        super().__init__(message)


def _plugin_cache_dir() -> Path:
    return (
        Path.home() / ".claude" / "plugins" / "cache" / DIRECT_INSTALL_CACHE_SUBDIR / "autoskillit"
    )


def _installed_plugin_root(version: str | None = None) -> Path:
    if version is None:
        from autoskillit.cli._plugin_artifact import current_installed_plugin_root

        return current_installed_plugin_root()
    return _plugin_cache_dir() / version


def _clear_plugin_cache(
    *,
    on_retirement_created: Callable[[str], None] | None = None,
    current_version: str | None = None,
    _lock_owned: bool = False,
) -> tuple[str, ...]:
    """Queue exact old versions and remove their installed_plugins.json reference."""
    if current_version is None:
        from autoskillit import __version__

        current_version = __version__
    from autoskillit.cli._installed_plugins import InstalledPluginsFile
    from autoskillit.cli._plugin_artifact import (
        InstalledPluginArtifactRetirementOwner,
        _read_installed_plugin_identity,
        default_plugin_retirement_coordinator,
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
    lock_scope = nullcontext() if _lock_owned else _InstallLock()
    with lock_scope:
        default_plugin_retirement_coordinator().migrate_legacy_cache()
        candidates = (
            tuple(
                path
                for path in sorted(cache_dir.iterdir(), key=lambda item: item.name)
                if path.name != current_version
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
            with reader:
                try:
                    identity = _read_installed_plugin_identity(candidate)
                except PluginArtifactValidationError:
                    continue
                result = owner.enqueue_retirement(
                    identity,
                    deadline,
                    on_persisted=on_retirement_created,
                )
                if result.created:
                    created_ids.append(result.record_id)

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
        raise RuntimeError(
            "ERROR: 'autoskillit install' cannot be run when the package\n"
            "is installed from a git worktree.\n\n"
            f"  Detected worktree path: {pkg_dir}\n\n"
            "The marketplace projection would be sourced from this transient path.\n\n"
            "Fix: run 'autoskillit install' from the main project checkout:\n"
            "  cd /path/to/main/repo && autoskillit install"
        )


def _ensure_marketplace(
    *,
    cwd: Path | None = None,
    version: str | None = None,
) -> Path:
    """Create or update the local marketplace directory."""
    if version is None:
        from autoskillit import __version__

        version = __version__
    projection_cwd = Path.cwd().resolve() if cwd is None else Path(cwd)

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
                "version": version,
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
            cwd=projection_cwd,
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


def _ensure_workspace_ready(*, cwd: Path | None = None) -> None:
    """Repair project workspace state that install() is responsible for.

    Called after the CLAUDECODE guard — only when the actual install proceeds.
    Idempotent: safe to call on any project state.
    """
    from autoskillit.core import ensure_project_temp

    project_dir = Path.cwd() if cwd is None else Path(cwd)
    # Repair .autoskillit/.gitignore and ensure temp/ exists
    if (project_dir / ".autoskillit").is_dir():
        ensure_project_temp(project_dir)

    # Migrate legacy .autoskillit/scripts/ to .autoskillit/recipes/ if present
    if (project_dir / ".autoskillit" / "scripts").exists():
        try:
            upgrade(project_dir=project_dir)
        except OSError as exc:
            print(f"Warning: migration upgrade() failed (non-fatal): {exc}")


class _InstallSnapshot:
    """Staged filesystem image of every shared surface mutated by install."""

    def __init__(
        self,
        *,
        target_root: Path | None = None,
        settings_path: Path | None = None,
        workspace_cwd: Path | None = None,
    ) -> None:
        from autoskillit.cli._plugin_artifact import installed_artifact_manifest_path
        from autoskillit.cli.update._update_checks_fetch import _fetch_cache_path

        home = Path.home()
        self._target_root = target_root or _installed_plugin_root()
        self._artifact_manifest_path = installed_artifact_manifest_path(self._target_root)
        settings = settings_path or home / ".claude" / "settings.json"
        paths = [
            home / ".autoskillit" / "marketplace",
            home / ".claude" / "plugins" / "known_marketplaces.json",
            _installed_plugins_json_path(),
            self._target_root,
            self._artifact_manifest_path,
            home / ".autoskillit" / "retiring_cache.json",
            _user_claude_json_path(),
            settings,
            _fetch_cache_path(home),
        ]
        if workspace_cwd is not None:
            project_state = Path(workspace_cwd) / ".autoskillit"
            paths.extend(
                (
                    project_state / ".gitignore",
                    project_state / "temp" / ".gitignore",
                )
            )
            if (project_state / "scripts").exists():
                paths.extend((project_state / "scripts", project_state / "recipes"))
        self._paths = tuple(dict.fromkeys(paths))
        self._stage_dir = home / ".autoskillit" / (f".install-transaction-{uuid.uuid4().hex}")
        self._entries: list[tuple[Path, str, Path | None]] = []
        self._staged = False
        self._committed = False

    @staticmethod
    def _shape(path: Path) -> str:
        try:
            mode = path.lstat().st_mode
        except FileNotFoundError:
            return "missing"
        if stat.S_ISLNK(mode):
            return "symlink"
        if stat.S_ISREG(mode):
            return "file"
        if stat.S_ISDIR(mode):
            return "directory"
        raise OSError(f"unsupported install snapshot artifact shape at {path}")

    @staticmethod
    def _remove(path: Path) -> None:
        shape = _InstallSnapshot._shape(path)
        if shape == "missing":
            return
        if shape == "directory":
            shutil.rmtree(path)
        else:
            path.unlink()

    @classmethod
    def _restore_entry(
        cls,
        path: Path,
        shape: str,
        backup: Path | None,
    ) -> None:
        if shape != "missing":
            if backup is None:
                raise OSError(f"staged backup is missing for {path}")
            backup_shape = cls._shape(backup)
            if backup_shape != shape:
                raise OSError(
                    f"staged backup for {path} has shape {backup_shape}, expected {shape}"
                )
        cls._remove(path)
        if shape == "missing":
            return
        assert backup is not None
        path.parent.mkdir(parents=True, exist_ok=True)
        if shape == "directory":
            shutil.copytree(backup, path, symlinks=True)
        elif shape == "symlink":
            path.symlink_to(os.readlink(backup))
        else:
            shutil.copy2(backup, path, follow_symlinks=False)
        restored_shape = cls._shape(path)
        if restored_shape != shape:
            raise OSError(f"restored {path} has shape {restored_shape}, expected {shape}")
        if not cls._matches_staged_state(path, shape, backup):
            raise OSError(f"restored {path} does not match its staged prestate")

    @classmethod
    def _matches_staged_state(
        cls,
        path: Path,
        shape: str,
        backup: Path | None,
    ) -> bool:
        if shape == "missing":
            return cls._shape(path) == "missing"
        if backup is None or cls._shape(path) != shape or cls._shape(backup) != shape:
            return False
        if stat.S_IMODE(path.lstat().st_mode) != stat.S_IMODE(backup.lstat().st_mode):
            return False
        if shape == "symlink":
            return os.readlink(path) == os.readlink(backup)
        if shape == "file":
            return filecmp.cmp(path, backup, shallow=False)

        current_entries = {entry.relative_to(path) for entry in path.rglob("*")}
        backup_entries = {entry.relative_to(backup) for entry in backup.rglob("*")}
        if current_entries != backup_entries:
            return False
        for relative in current_entries:
            current_entry = path / relative
            backup_entry = backup / relative
            entry_shape = cls._shape(current_entry)
            if entry_shape != cls._shape(backup_entry):
                return False
            if stat.S_IMODE(current_entry.lstat().st_mode) != stat.S_IMODE(
                backup_entry.lstat().st_mode
            ):
                return False
            if entry_shape == "symlink" and os.readlink(current_entry) != os.readlink(
                backup_entry
            ):
                return False
            if entry_shape == "file" and not filecmp.cmp(
                current_entry,
                backup_entry,
                shallow=False,
            ):
                return False
        return True

    def stage(self) -> None:
        """Copy every covered surface before reconciliation or mutation."""
        if self._staged:
            return
        self._stage_dir.mkdir(parents=True, mode=0o700)
        try:
            for index, path in enumerate(self._paths):
                shape = self._shape(path)
                backup: Path | None = None
                if shape != "missing":
                    backup = self._stage_dir / str(index)
                    if shape == "directory":
                        shutil.copytree(path, backup, symlinks=True)
                    elif shape == "symlink":
                        backup.symlink_to(os.readlink(path))
                    else:
                        shutil.copy2(path, backup, follow_symlinks=False)
                self._entries.append((path, shape, backup))
            self._staged = True
        except BaseException:
            logger.warning(
                "install_snapshot_stage_failed",
                stage_dir=str(self._stage_dir),
                exc_info=True,
            )
            shutil.rmtree(self._stage_dir, ignore_errors=True)
            self._entries.clear()
            raise

    def track_retirement(self, _record_id: str) -> None:
        """Retirement state is covered by the exact staged cache file."""

    def rollback(self) -> tuple[str, ...]:
        """Best-effort exact restoration; return every rollback diagnostic."""
        if not self._staged or self._committed:
            return ()
        diagnostics: list[str] = []
        for path, shape, backup in reversed(self._entries):
            try:
                self._restore_entry(path, shape, backup)
            except BaseException as exc:
                logger.warning(
                    "install_snapshot_restore_failed",
                    path=str(path),
                    exc_info=True,
                )
                diagnostics.append(f"rollback failed for {path}: {exc}")
                try:
                    residual_shape = self._shape(path)
                except BaseException as residual_exc:
                    logger.warning(
                        "install_snapshot_residual_shape_inspection_failed",
                        path=str(path),
                        exc_info=True,
                    )
                    residual_shape = f"unreadable ({residual_exc})"
                try:
                    residual_matches = self._matches_staged_state(path, shape, backup)
                except BaseException:
                    logger.warning(
                        "install_snapshot_residual_comparison_failed",
                        path=str(path),
                        exc_info=True,
                    )
                    residual_matches = False
                comparison = (
                    "matches staged prestate"
                    if residual_matches
                    else "differs from staged prestate"
                )
                diagnostics.append(
                    f"residual state for {path}: expected {shape}, "
                    f"observed {residual_shape}; {comparison}"
                )
        if diagnostics:
            diagnostics.append(f"recovery evidence preserved at {self._stage_dir}")
            return tuple(diagnostics)
        try:
            shutil.rmtree(self._stage_dir)
        except FileNotFoundError:
            pass
        except BaseException as exc:
            logger.warning(
                "install_snapshot_cleanup_failed",
                stage_dir=str(self._stage_dir),
                exc_info=True,
            )
            diagnostics.append(f"rollback staging cleanup failed: {exc}")
            if self._shape(self._stage_dir) != "missing":
                diagnostics.append(f"recovery evidence preserved at {self._stage_dir}")
            return tuple(diagnostics)
        self._entries.clear()
        self._staged = False
        return tuple(diagnostics)

    def commit(self) -> None:
        """Discard staged state only after every required postcondition passes."""
        if not self._staged:
            raise RuntimeError("install snapshot was not staged")
        shutil.rmtree(self._stage_dir)
        self._committed = True
        self._entries.clear()
        self._staged = False


def _marketplace_manifest_path() -> Path:
    return Path.home() / ".autoskillit" / "marketplace" / ".claude-plugin" / "marketplace.json"


def _installed_plugins_json_path() -> Path:
    return Path.home() / ".claude" / "plugins" / "installed_plugins.json"


def _settings_path(scope: str, cwd: Path) -> Path:
    if scope == "user":
        return Path.home() / ".claude" / "settings.json"
    return cwd / ".claude" / "settings.json"


def _claude_on_path(env: Mapping[str, str]) -> bool:
    """Resolve Claude exactly once against the sealed PATH."""
    return shutil.which("claude", path=env.get("PATH")) is not None


def _validate_transaction_target(target: Path, expected_version: str) -> None:
    """Validate an explicit-version target without consulting cached package state."""
    expected_parent = _plugin_cache_dir()
    if (
        not target.is_absolute()
        or target != expected_parent / expected_version
        or target.is_symlink()
        or (target.exists() and not target.is_dir())
    ):
        raise RuntimeError(f"Unsafe installed plugin target: {target}")
    resolved_parent = expected_parent.resolve(strict=False)
    if target.resolve(strict=False).parent != resolved_parent:
        raise RuntimeError(f"Unsafe installed plugin target escapes managed cache: {target}")


def _run_claude_admin(
    argv: Sequence[str],
    *,
    env: Mapping[str, str],
    cwd: Path,
) -> subprocess.CompletedProcess[str]:
    """Run one Claude administrative command with an explicit sealed context."""
    if not argv:
        raise ValueError("Claude administrative argv must not be empty")
    return subprocess.run(
        tuple(argv),
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        timeout=30,
        pass_fds=(),
        shell=False,
        env=dict(env),
        cwd=cwd,
    )


def _typed_result(
    outcome: InstallOutcome,
    *,
    failure_kind: InstallFailureKind | None = None,
    verified_identity: str | None = None,
    findings: tuple[str, ...] = (),
) -> InstallResult:
    for finding in findings:
        print(finding)
    return InstallResult(
        outcome=outcome,
        failure_kind=failure_kind,
        verified_identity=verified_identity,
        findings=findings,
    )


def _compensated_result(
    snapshot: _InstallSnapshot,
    primary: _InstallFailed,
) -> InstallResult:
    rollback_findings = snapshot.rollback()
    primary_finding = f"{primary.kind.value} failure: {primary}"
    if rollback_findings:
        return _typed_result(
            InstallOutcome.RECOVERY_REQUIRED,
            failure_kind=InstallFailureKind.ROLLBACK,
            findings=(primary_finding, *rollback_findings),
        )
    return _typed_result(
        InstallOutcome.FAILED,
        failure_kind=primary.kind,
        findings=(primary_finding, "compensation completed"),
    )


def _read_json_object(path: Path, *, purpose: str) -> dict:
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except OSError as exc:
        raise _InstallFailed(
            InstallFailureKind.POSTCONDITION,
            f"Could not verify {purpose} at {path}: {exc}",
        ) from exc
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise _InstallFailed(
            InstallFailureKind.POSTCONDITION,
            f"Could not verify {purpose} at {path}: invalid JSON ({exc})",
        ) from exc
    if not isinstance(data, dict):
        raise _InstallFailed(
            InstallFailureKind.POSTCONDITION,
            f"Could not verify {purpose} at {path}: expected a JSON object",
        )
    return data


def _verify_cleanup(settings_path: Path, fetch_cache_path: Path) -> None:
    direct = _read_json_object(
        _user_claude_json_path(),
        purpose="direct MCP registration eviction",
    )
    servers = direct.get("mcpServers", {})
    if isinstance(servers, dict) and "autoskillit" in servers:
        raise _InstallFailed(
            InstallFailureKind.POSTCONDITION,
            "Stale direct MCP registration remains after eviction",
        )

    settings = _read_json_object(settings_path, purpose="Claude hook eviction")
    hooks = settings.get("hooks", {})
    if isinstance(hooks, dict):
        for entries in hooks.values():
            if not isinstance(entries, list):
                continue
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                for hook in entry.get("hooks", ()):
                    if isinstance(hook, dict) and _hooks_mod._is_autoskillit_hook_command(
                        str(hook.get("command", ""))
                    ):
                        raise _InstallFailed(
                            InstallFailureKind.POSTCONDITION,
                            f"Stale AutoSkillit hook remains in {settings_path}",
                        )
    try:
        fetch_cache_path.lstat()
    except FileNotFoundError:
        return
    except OSError as exc:
        raise _InstallFailed(
            InstallFailureKind.POSTCONDITION,
            f"Could not verify fetch-cache invalidation: {exc}",
        ) from exc
    raise _InstallFailed(
        InstallFailureKind.POSTCONDITION,
        f"Fetch cache remains after invalidation: {fetch_cache_path}",
    )


def install(
    *,
    request: InstallRequest | None = None,
    scope: str = "user",
    child_env: Mapping[str, str] | None = None,
    child_cwd: Path | None = None,
) -> InstallResult:
    """Install the Claude plugin as one typed, compensating transaction."""
    ambient_env = dict(os.environ)
    ambient_cwd = Path.cwd().resolve()
    from autoskillit import __version__

    install_request = request or InstallRequest(
        scope=scope,
        mode=InstallMode.DIRECT,
        require_registered_plugin=True,
        expected_version=__version__,
    )
    effective_scope = install_request.scope
    if (
        install_request.mode is InstallMode.MAINTENANCE_UPDATE
        and not install_request.require_registered_plugin
    ):
        return _typed_result(
            InstallOutcome.NOT_REQUIRED,
            findings=("Claude plugin publication is not required for this maintenance update",),
        )

    try:
        if effective_scope not in _VALID_SCOPES:
            raise RuntimeError(
                f"Invalid scope: {effective_scope!r}. Must be one of: "
                f"{', '.join(sorted(_VALID_SCOPES))}"
            )
        if install_request.mode is InstallMode.MAINTENANCE_UPDATE:
            if child_env is None or child_cwd is None:
                raise RuntimeError("Maintenance install requires a sealed child_env and child_cwd")
            operation_env = dict(child_env)
            operation_cwd = Path(child_cwd)
            if not operation_cwd.is_absolute():
                raise RuntimeError("Maintenance child_cwd must be absolute")
            expected_version = install_request.expected_version
            if expected_version is None:
                raise RuntimeError("Maintenance install requires expected_version")
            distribution_version = importlib.metadata.version("autoskillit")
            if distribution_version != expected_version:
                raise RuntimeError(
                    "Maintenance install expected distribution version "
                    f"{expected_version}, observed {distribution_version}"
                )
        else:
            operation_env = dict(ambient_env if child_env is None else child_env)
            operation_cwd = Path(ambient_cwd if child_cwd is None else child_cwd).resolve()
            expected_version = install_request.expected_version or __version__

        if not operation_cwd.is_dir():
            raise RuntimeError(f"Install child_cwd is not a directory: {operation_cwd}")
        if not all(
            isinstance(key, str) and isinstance(value, str) for key, value in operation_env.items()
        ):
            raise RuntimeError("Install child_env must contain only string keys and values")

        _assert_not_worktree()
        if install_request.mode is InstallMode.DIRECT:
            from autoskillit.config import load_config
            from autoskillit.execution import get_backend

            cfg = load_config(operation_cwd)
            backend = get_backend(cfg.agent_backend.backend)
            if not backend.capabilities.plugin_install_capable:
                return _typed_result(
                    InstallOutcome.DECLINED,
                    findings=(
                        "Plugin install requires a plugin_install_capable backend; "
                        f"current backend is {cfg.agent_backend.backend!r}",
                    ),
                )

        marketplace_dir = Path.home() / ".autoskillit" / "marketplace"
        plugin_ref = f"autoskillit@{_MARKETPLACE_NAME}"
        if operation_env.get("CLAUDECODE"):
            return _typed_result(
                InstallOutcome.DEFERRED,
                findings=(
                    "Run the Claude plugin marketplace and install commands in a regular terminal",
                ),
            )
        if not _claude_on_path(operation_env):
            raise RuntimeError("'claude' command not found in the sealed PATH")

        target_root = _installed_plugin_root(expected_version)
        _validate_transaction_target(target_root, expected_version)
    except (OSError, RuntimeError, ValueError, importlib.metadata.PackageNotFoundError) as exc:
        return _typed_result(
            InstallOutcome.FAILED,
            failure_kind=InstallFailureKind.PREFLIGHT,
            findings=(f"preflight failure: {exc}",),
        )

    from autoskillit.cli._plugin_artifact import (
        installed_artifact_lock_path,
        installed_plugin_semantic_key,
        publish_installed_plugin_artifact,
    )
    from autoskillit.core import (
        ArtifactLease,
        ArtifactLeaseContention,
        PluginArtifactPublicationError,
        _InstallLock,
    )
    from autoskillit.workspace import (
        InstallStateSpec,
        reconcile_install_artifacts,
        verify_installed_plugin_artifact,
    )

    settings_path = _settings_path(effective_scope, operation_cwd)
    try:
        with _InstallLock():
            try:
                target_writer = ArtifactLease.acquire_exclusive(
                    installed_artifact_lock_path(target_root),
                    blocking=False,
                )
            except ArtifactLeaseContention:
                return _typed_result(
                    InstallOutcome.DEFERRED,
                    findings=(
                        "Installed plugin is in use by an active session; retry after it exits",
                    ),
                )
            with target_writer:
                snapshot = _InstallSnapshot(
                    target_root=target_root,
                    settings_path=settings_path,
                    workspace_cwd=(
                        operation_cwd if install_request.mode is InstallMode.DIRECT else None
                    ),
                )
                try:
                    snapshot.stage()
                except (OSError, RuntimeError, ValueError) as exc:
                    return _typed_result(
                        InstallOutcome.FAILED,
                        failure_kind=InstallFailureKind.PREFLIGHT,
                        findings=(f"preflight snapshot failure: {exc}",),
                    )

                try:
                    for repaired in reconcile_install_artifacts():
                        print(f"Repaired legacy install artifact: ~/{repaired}")
                    marketplace_dir = _ensure_marketplace(
                        cwd=operation_cwd,
                        version=expected_version,
                    )
                    if install_request.mode is InstallMode.DIRECT:
                        _ensure_workspace_ready(cwd=operation_cwd)
                    _clear_plugin_cache(
                        on_retirement_created=snapshot.track_retirement,
                        current_version=expected_version,
                        _lock_owned=True,
                    )

                    try:
                        result = _run_claude_admin(
                            (
                                "claude",
                                "plugin",
                                "marketplace",
                                "add",
                                str(marketplace_dir),
                            ),
                            env=operation_env,
                            cwd=operation_cwd,
                        )
                    except (OSError, subprocess.SubprocessError) as exc:
                        raise _InstallFailed(
                            InstallFailureKind.CHILD,
                            f"Failed to register marketplace: {exc}",
                        ) from exc
                    if result.returncode != 0:
                        raise _InstallFailed(
                            InstallFailureKind.CHILD,
                            f"Failed to register marketplace: {result.stderr.strip()}",
                        )

                    try:
                        result = _run_claude_admin(
                            (
                                "claude",
                                "plugin",
                                "install",
                                plugin_ref,
                                "--scope",
                                effective_scope,
                            ),
                            env=operation_env,
                            cwd=operation_cwd,
                        )
                    except (OSError, subprocess.SubprocessError) as exc:
                        raise _InstallFailed(
                            InstallFailureKind.CHILD,
                            f"Failed to install plugin: {exc}",
                        ) from exc
                    if result.returncode != 0:
                        raise _InstallFailed(
                            InstallFailureKind.CHILD,
                            f"Failed to install plugin: {result.stderr.strip()}",
                        )

                    try:
                        publish_installed_plugin_artifact(
                            target_root,
                            semantic_key=installed_plugin_semantic_key(
                                plugin_ref,
                                expected_version,
                            ),
                            _owned_exclusive_lease=target_writer,
                        )
                    except PluginArtifactPublicationError as exc:
                        raise _InstallFailed(
                            InstallFailureKind.POSTCONDITION,
                            f"Failed to publish installed plugin identity: {exc}",
                        ) from exc

                    if evict_direct_mcp_entry(_user_claude_json_path()):
                        print("Removed stale direct MCP entry from ~/.claude.json")
                    _hooks_mod._evict_stale_autoskillit_hooks(settings_path)
                    from autoskillit.cli.update._update_checks import invalidate_fetch_cache
                    from autoskillit.cli.update._update_checks_fetch import (
                        _fetch_cache_path,
                    )

                    invalidate_fetch_cache(Path.home())
                    _verify_cleanup(settings_path, _fetch_cache_path(Path.home()))

                    verification = verify_installed_plugin_artifact(
                        InstallStateSpec(
                            home=Path.home(),
                            plugin_ref=plugin_ref,
                            expected_version=expected_version,
                            require_registered_plugin=(install_request.require_registered_plugin),
                            require_shared_lease=False,
                            supplied_lease=target_writer,
                        )
                    )
                    if verification.identity is None or verification.findings:
                        messages = "; ".join(
                            f"{finding.check}: {finding.message}"
                            for finding in verification.findings
                        )
                        raise _InstallFailed(
                            InstallFailureKind.POSTCONDITION,
                            "Installed plugin exact verification failed"
                            + (f": {messages}" if messages else ""),
                        )
                    verified_identity = verification.identity.incarnation_id
                    snapshot.commit()
                except _InstallFailed as exc:
                    return _compensated_result(snapshot, exc)
                except Exception as exc:
                    logger.warning(
                        "install_transaction_unexpected_failure",
                        failure=str(exc),
                        exc_info=True,
                    )
                    return _compensated_result(
                        snapshot,
                        _InstallFailed(
                            InstallFailureKind.POSTCONDITION,
                            f"Install transaction failed: {exc}",
                        ),
                    )
    except (OSError, RuntimeError, ValueError) as exc:
        return _typed_result(
            InstallOutcome.FAILED,
            failure_kind=InstallFailureKind.PREFLIGHT,
            findings=(f"install lock failure: {exc}",),
        )

    print(f"Plugin installed: {plugin_ref} (scope: {effective_scope})")
    return InstallResult(
        outcome=InstallOutcome.COMPLETED,
        verified_identity=verified_identity,
    )


def upgrade(*, project_dir: Path | None = None):
    """Migrate a project from .autoskillit/scripts/ to .autoskillit/recipes/.

    Renames the directory and rewrites YAML top-level keys:
      inputs: -> ingredients:
      constraints: -> kitchen_rules:

    Idempotent: safe to run multiple times.
    """
    project_dir = Path.cwd() if project_dir is None else Path(project_dir)
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
