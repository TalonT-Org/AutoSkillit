"""Minimal descriptor authority shared by shell-capture producer and cleanup owners.

This stdlib-only module opens and retains the project and capture-root
directories. It deliberately excludes command execution, policy, replay, and
subprocess dependencies so cleanup-only hooks remain independently loadable.
"""

from __future__ import annotations

import importlib
import os
import stat
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import InitVar, dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from ._module_identity import register_module_aliases

register_module_aliases(__name__)

_HOOKS_DIR = str(Path(__file__).resolve().parent.parent)
if _HOOKS_DIR not in sys.path:
    sys.path.insert(0, _HOOKS_DIR)

if TYPE_CHECKING:
    from autoskillit.hooks._capture_lifecycle import (
        CaptureLifecycleError,
        CaptureLifecycleStore,
    )
else:
    _capture_lifecycle = importlib.import_module(
        f"{__package__.rpartition('.')[0]}._capture_lifecycle"
        if __package__.startswith("autoskillit.")
        else "_capture_lifecycle"
    )
    CaptureLifecycleError = _capture_lifecycle.CaptureLifecycleError
    CaptureLifecycleStore = _capture_lifecycle.CaptureLifecycleStore

__all__ = [
    "CAPTURE_PATH_COMPONENTS",
    "CaptureLifecycleError",
    "CaptureRoot",
    "CaptureSetupError",
    "CaptureStoreAbsentError",
    "FileIdentity",
    "ProjectAnchor",
    "open_capture_lifecycle",
    "open_capture_root",
    "open_project_anchor",
]

CAPTURE_PATH_COMPONENTS = (".autoskillit", "temp", "shell_capture")

_AUTHORITY_FACTORY_TOKEN = object()
_UNTRUSTED_WRITE_BITS = stat.S_IWGRP | stat.S_IWOTH
_DIRECTORY_FLAGS = (
    os.O_RDONLY
    | getattr(os, "O_DIRECTORY", 0)
    | getattr(os, "O_CLOEXEC", 0)
    | getattr(os, "O_NOFOLLOW", 0)
)
_READ_FLAGS = (
    os.O_RDONLY
    | getattr(os, "O_CLOEXEC", 0)
    | getattr(os, "O_NOFOLLOW", 0)
    | getattr(os, "O_NONBLOCK", 0)
)


class CaptureSetupError(RuntimeError):
    """Raised when the descriptor-anchored capture authority cannot be established."""


class CaptureStoreAbsentError(CaptureSetupError):
    """Raised when a cleanup-only open finds no existing capture store."""


@dataclass(frozen=True, slots=True)
class FileIdentity:
    device: int
    inode: int

    @classmethod
    def from_stat(cls, value: os.stat_result) -> FileIdentity:
        return cls(device=value.st_dev, inode=value.st_ino)


@dataclass(frozen=True, slots=True)
class ProjectAnchor:
    """Opened project directory and its post-open physical-path hint."""

    fd: int
    identity: FileIdentity
    supplied_path: str
    physical_path: Path
    _factory_token: InitVar[object | None] = None

    def __post_init__(self, _factory_token: object | None) -> None:
        if _factory_token is not _AUTHORITY_FACTORY_TOKEN:
            raise CaptureSetupError("ProjectAnchor must be created by open_project_anchor")

    def close(self) -> None:
        if self.fd >= 0:
            os.close(self.fd)
            object.__setattr__(self, "fd", -1)


@dataclass(frozen=True, slots=True)
class CaptureRoot:
    """Opened capture-root chain retained for the capture lifetime."""

    autoskillit_fd: int
    temp_fd: int
    fd: int
    autoskillit_identity: FileIdentity
    temp_identity: FileIdentity
    identity: FileIdentity
    _factory_token: InitVar[object | None] = None

    def __post_init__(self, _factory_token: object | None) -> None:
        if _factory_token is not _AUTHORITY_FACTORY_TOKEN:
            raise CaptureSetupError("CaptureRoot must be created by open_capture_root")

    def close(self) -> None:
        for field_name in ("fd", "temp_fd", "autoskillit_fd"):
            fd = getattr(self, field_name)
            if fd >= 0:
                os.close(fd)
                object.__setattr__(self, field_name, -1)


def _require_capabilities() -> None:
    required_dir_fd = (os.link, os.mkdir, os.open, os.stat, os.unlink)
    required_flags = (
        "O_CLOEXEC",
        "O_CREAT",
        "O_DIRECTORY",
        "O_EXCL",
        "O_NOFOLLOW",
        "O_NONBLOCK",
    )
    if (
        any(getattr(os, flag, 0) == 0 for flag in required_flags)
        or not hasattr(os, "fchdir")
        or not hasattr(os, "fstat")
        or not hasattr(os, "pread")
        or any(function not in os.supports_dir_fd for function in required_dir_fd)
        or os.stat not in getattr(os, "supports_follow_symlinks", ())
        or os.listdir not in getattr(os, "supports_fd", ())
    ):
        raise CaptureSetupError("required descriptor-relative filesystem primitives unavailable")


def _identity(fd: int) -> FileIdentity:
    return FileIdentity.from_stat(os.fstat(fd))


def _same_identity(fd: int, expected: FileIdentity) -> bool:
    return _identity(fd) == expected


def _open_directory_component(parent_fd: int, name: str, *, create: bool) -> int:
    try:
        fd = os.open(name, _DIRECTORY_FLAGS, dir_fd=parent_fd)
    except FileNotFoundError:
        if not create:
            raise CaptureStoreAbsentError(f"missing capture path component: {name}") from None
        try:
            os.mkdir(name, mode=0o700, dir_fd=parent_fd)
        except FileExistsError:
            pass
        except OSError as exc:
            raise CaptureSetupError(f"cannot create capture path component: {name}") from exc
        try:
            fd = os.open(name, _DIRECTORY_FLAGS, dir_fd=parent_fd)
        except OSError as exc:
            raise CaptureSetupError(f"cannot open created capture component: {name}") from exc
    except OSError as exc:
        raise CaptureSetupError(f"unsafe capture path component: {name}") from exc

    try:
        value = os.fstat(fd)
        if not stat.S_ISDIR(value.st_mode):
            raise CaptureSetupError(f"capture path component is not a directory: {name}")
        if value.st_uid != os.geteuid() or value.st_mode & _UNTRUSTED_WRITE_BITS:
            raise CaptureSetupError(f"capture path component has unsafe ownership or mode: {name}")
    except BaseException:
        os.close(fd)
        raise
    return fd


def open_project_anchor(cwd: str) -> ProjectAnchor:
    """Open the supplied cwd first; a symlink in the supplied spelling is allowed."""

    _require_capabilities()
    if not isinstance(cwd, str) or not cwd or not os.path.isabs(cwd) or "\x00" in cwd:
        raise CaptureSetupError("cwd must be a non-empty absolute path")
    try:
        fd = os.open(cwd, _DIRECTORY_FLAGS & ~getattr(os, "O_NOFOLLOW", 0))
    except OSError as exc:
        raise CaptureSetupError("cannot open project anchor") from exc
    try:
        anchor_stat = os.fstat(fd)
        if not stat.S_ISDIR(anchor_stat.st_mode):
            raise CaptureSetupError("project anchor is not a directory")
        physical_path = Path(os.path.realpath(cwd))
        return ProjectAnchor(
            fd=fd,
            identity=FileIdentity.from_stat(anchor_stat),
            supplied_path=cwd,
            physical_path=physical_path,
            _factory_token=_AUTHORITY_FACTORY_TOKEN,
        )
    except BaseException:
        os.close(fd)
        raise


def open_capture_root(anchor: ProjectAnchor, *, create: bool) -> CaptureRoot:
    """Open the capture-root chain relative to ``anchor`` without following symlinks."""

    opened: list[int] = []
    try:
        autoskillit_fd = _open_directory_component(
            anchor.fd, CAPTURE_PATH_COMPONENTS[0], create=create
        )
        opened.append(autoskillit_fd)
        temp_fd = _open_directory_component(
            autoskillit_fd, CAPTURE_PATH_COMPONENTS[1], create=create
        )
        opened.append(temp_fd)
        capture_fd = _open_directory_component(
            temp_fd,
            CAPTURE_PATH_COMPONENTS[2],
            create=create,
        )
        opened.append(capture_fd)
        return CaptureRoot(
            autoskillit_fd=autoskillit_fd,
            temp_fd=temp_fd,
            fd=capture_fd,
            autoskillit_identity=_identity(autoskillit_fd),
            temp_identity=_identity(temp_fd),
            identity=_identity(capture_fd),
            _factory_token=_AUTHORITY_FACTORY_TOKEN,
        )
    except BaseException:
        for fd in reversed(opened):
            os.close(fd)
        raise


@contextmanager
def open_capture_lifecycle(
    requested_cwd: str,
    *,
    create: bool = False,
) -> Iterator[CaptureLifecycleStore]:
    """Open a lifecycle store from a validated payload cwd."""

    anchor = open_project_anchor(requested_cwd)
    root: CaptureRoot | None = None
    try:
        root = open_capture_root(anchor, create=create)
        yield CaptureLifecycleStore.from_open_authorities(anchor, root)
    finally:
        try:
            if root is not None:
                root.close()
        finally:
            anchor.close()
