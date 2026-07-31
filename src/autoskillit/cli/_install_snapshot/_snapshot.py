"""Exact filesystem snapshot and restoration machinery for plugin installation."""

from __future__ import annotations

import filecmp
import os
import shutil
import stat
import uuid
from pathlib import Path

from autoskillit.cli._init_helpers import _user_claude_json_path
from autoskillit.core import (
    _installed_plugins_path,
    get_logger,
    installed_plugin_artifact_lease_path,
    installed_plugin_artifact_manifest_path,
    installed_plugin_artifact_root,
    installed_plugin_cache_dir,
)

logger = get_logger(__name__)

_FETCH_CACHE_FILE = "github_fetch_cache.json"


def _plugin_cache_dir() -> Path:
    return installed_plugin_cache_dir(Path.home(), "autoskillit")


def _fetch_cache_path(home: Path) -> Path:
    return home / ".autoskillit" / _FETCH_CACHE_FILE


def _installed_plugin_root(version: str | None = None) -> Path:
    if version is None:
        from autoskillit import __version__

        version = __version__
    return installed_plugin_artifact_root(Path.home(), "autoskillit", version)


def _installed_plugins_json_path() -> Path:
    return _installed_plugins_path()


class _InstallSnapshot:
    """Staged filesystem image of every shared surface mutated by install."""

    def __init__(
        self,
        *,
        target_root: Path | None = None,
        settings_path: Path | None = None,
        workspace_cwd: Path | None = None,
    ) -> None:
        home = Path.home()
        self._target_root = target_root or _installed_plugin_root()
        self._artifact_manifest_path = installed_plugin_artifact_manifest_path(self._target_root)
        self._lease_path = installed_plugin_artifact_lease_path(self._target_root)
        settings = settings_path or home / ".claude" / "settings.json"
        self._workspace_temp_dir: Path | None = None
        self._workspace_temp_shape: str | None = None
        paths = [
            home / ".autoskillit" / "marketplace",
            home / ".claude" / "plugins" / "known_marketplaces.json",
            _installed_plugins_json_path(),
            self._target_root,
            self._artifact_manifest_path,
            self._lease_path,
            home / ".autoskillit" / "retiring_cache.json",
            _user_claude_json_path(),
            settings,
            _fetch_cache_path(home),
        ]
        if workspace_cwd is not None:
            project_state = Path(workspace_cwd) / ".autoskillit"
            self._workspace_temp_dir = project_state / "temp"
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
    def _restore_workspace_temp_shape(cls, path: Path, shape: str) -> None:
        current_shape = cls._shape(path)
        if current_shape == shape:
            return
        if shape == "missing" and current_shape == "directory":
            path.rmdir()
        elif shape == "directory" and current_shape == "missing":
            path.mkdir()
        else:
            raise OSError(f"workspace temp {path} has shape {current_shape}, expected {shape}")
        restored_shape = cls._shape(path)
        if restored_shape != shape:
            raise OSError(
                f"restored workspace temp {path} has shape {restored_shape}, expected {shape}"
            )

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
    def _restore_owned_lease_entry(
        cls,
        path: Path,
        shape: str,
        backup: Path | None,
        lease_fd: int,
    ) -> None:
        lease_stat = os.fstat(lease_fd)
        path_stat = path.lstat()
        if (
            not stat.S_ISREG(lease_stat.st_mode)
            or not stat.S_ISREG(path_stat.st_mode)
            or (lease_stat.st_dev, lease_stat.st_ino) != (path_stat.st_dev, path_stat.st_ino)
        ):
            raise OSError(f"owned lease descriptor no longer names {path}")

        if shape == "missing":
            # All shared-surface restoration is complete before this unlink. The
            # descriptor keeps the original inode locked until the caller exits
            # its lease context, while the lexical pre-state becomes absent.
            cls._remove(path)
            if cls._shape(path) != "missing":
                raise OSError(f"new lease sidecar remains after rollback: {path}")
            return
        if shape != "file" or backup is None or cls._shape(backup) != "file":
            raise OSError(f"staged lease backup for {path} is not a regular file")

        os.lseek(lease_fd, 0, os.SEEK_SET)
        os.ftruncate(lease_fd, 0)
        with backup.open("rb") as source:
            while chunk := source.read(1024 * 1024):
                remaining = memoryview(chunk)
                while remaining:
                    written = os.write(lease_fd, remaining)
                    if written <= 0:
                        raise OSError(f"could not restore staged lease bytes for {path}")
                    remaining = remaining[written:]
        os.fchmod(lease_fd, stat.S_IMODE(backup.lstat().st_mode))
        os.fsync(lease_fd)
        if not cls._matches_staged_state(path, shape, backup):
            raise OSError(f"restored lease sidecar {path} does not match its staged prestate")

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
        if self._workspace_temp_dir is not None:
            self._workspace_temp_shape = self._shape(self._workspace_temp_dir)
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
            self._workspace_temp_shape = None
            raise

    def rollback(self, *, owned_lease_fd: int | None = None) -> tuple[str, ...]:
        """Best-effort exact restoration; return every rollback diagnostic."""
        if not self._staged or self._committed:
            return ()
        diagnostics: list[str] = []
        lease_entry: tuple[Path, str, Path | None] | None = None
        for path, shape, backup in reversed(self._entries):
            if owned_lease_fd is not None and path == self._lease_path:
                lease_entry = (path, shape, backup)
                continue
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
        if self._workspace_temp_dir is not None and self._workspace_temp_shape is not None:
            path = self._workspace_temp_dir
            shape = self._workspace_temp_shape
            try:
                self._restore_workspace_temp_shape(path, shape)
            except BaseException as exc:
                logger.warning(
                    "install_snapshot_workspace_temp_restore_failed",
                    path=str(path),
                    exc_info=True,
                )
                diagnostics.append(f"rollback failed for {path}: {exc}")
                try:
                    residual_shape = self._shape(path)
                except BaseException as residual_exc:
                    logger.warning(
                        "install_snapshot_workspace_temp_shape_inspection_failed",
                        path=str(path),
                        exc_info=True,
                    )
                    residual_shape = f"unreadable ({residual_exc})"
                diagnostics.append(
                    f"residual state for {path}: expected {shape}, observed {residual_shape}"
                )
        if owned_lease_fd is not None:
            if lease_entry is None:
                diagnostics.append(
                    f"rollback failed for {self._lease_path}: staged lease entry is missing"
                )
            elif diagnostics:
                diagnostics.append(
                    f"rollback deferred for {self._lease_path}: "
                    "an earlier restoration failure left the owned lease sidecar in place"
                )
            else:
                path, shape, backup = lease_entry
                try:
                    self._restore_owned_lease_entry(
                        path,
                        shape,
                        backup,
                        owned_lease_fd,
                    )
                except BaseException as exc:
                    logger.warning(
                        "install_snapshot_lease_restore_failed",
                        path=str(path),
                        exc_info=True,
                    )
                    diagnostics.append(f"rollback failed for {path}: {exc}")
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
        self._workspace_temp_shape = None
        self._staged = False
        return tuple(diagnostics)

    def discard(self) -> None:
        """Discard a mutation-free snapshot after lease contention."""
        if not self._staged or self._committed:
            return
        self._remove(self._stage_dir)
        self._entries.clear()
        self._workspace_temp_shape = None
        self._staged = False

    def commit(self) -> None:
        """Discard staged state only after every required postcondition passes."""
        if not self._staged:
            raise RuntimeError("install snapshot was not staged")
        self._committed = True
        self._entries.clear()
        self._workspace_temp_shape = None
        self._staged = False
        try:
            shutil.rmtree(self._stage_dir)
        except Exception:
            logger.warning(
                "install_snapshot_commit_cleanup_failed",
                stage_dir=str(self._stage_dir),
                exc_info=True,
            )
