"""Stable filesystem leases for artifacts retained by child processes."""

from __future__ import annotations

import errno
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar

try:
    import fcntl
except ImportError:  # pragma: no cover - exercised through the platform guard
    fcntl = None  # type: ignore[assignment]

__all__ = ["ArtifactLease", "ArtifactLeaseContention"]

_ARTIFACT_LEASE_CONSTRUCTION_TOKEN = object()


def _close_preserving_primary(fd: int, primary: BaseException) -> None:
    try:
        os.close(fd)
    except BaseException as cleanup_error:
        primary.add_note(f"Artifact lease descriptor cleanup failed: {cleanup_error!r}")


class ArtifactLeaseContention(RuntimeError):
    """Raised when a nonblocking artifact lease cannot be acquired."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        super().__init__(f"Artifact lease is contended: {self.path}")


@dataclass(slots=True, init=False)
class ArtifactLease:
    """An inherited shared or exclusive lock released only by descriptor close."""

    path: Path
    fd: int | None
    shared: bool

    _CONTENTION_ERRNOS: ClassVar[frozenset[int]] = frozenset({errno.EACCES, errno.EAGAIN})

    def __init__(
        self,
        *,
        path: Path,
        fd: int,
        shared: bool,
        _construction_token: object | None = None,
    ) -> None:
        if _construction_token is not _ARTIFACT_LEASE_CONSTRUCTION_TOKEN:
            raise TypeError("ArtifactLease instances must be created by an acquire method")
        if isinstance(fd, bool) or not isinstance(fd, int) or fd < 0:
            raise ValueError("ArtifactLease fd must be a non-negative integer")
        if type(shared) is not bool:
            raise ValueError("ArtifactLease shared must be a boolean")
        self.path = Path(path)
        self.fd = fd
        self.shared = shared

    @classmethod
    def acquire_shared(cls, lock_path: Path) -> ArtifactLease:
        """Acquire a blocking reader lease backed by a stable regular file."""
        return cls._acquire(Path(lock_path), shared=True, blocking=True)

    @classmethod
    def acquire_exclusive(
        cls,
        lock_path: Path,
        *,
        blocking: bool = False,
    ) -> ArtifactLease:
        """Acquire a writer lease, nonblocking unless explicitly requested."""
        return cls._acquire(Path(lock_path), shared=False, blocking=blocking)

    @classmethod
    def _acquire(
        cls,
        lock_path: Path,
        *,
        shared: bool,
        blocking: bool,
    ) -> ArtifactLease:
        if fcntl is None:
            raise RuntimeError("Artifact leases require a POSIX flock implementation")
        if lock_path.suffix != ".lock":
            raise ValueError(f"Artifact lease path must use the .lock suffix: {lock_path}")

        lock_path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        directory_flags = (
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
        )
        directory_fd = os.open(lock_path.parent, directory_flags)
        fd: int | None = None
        try:
            directory_stat = os.fstat(directory_fd)
            if not stat.S_ISDIR(directory_stat.st_mode):
                raise RuntimeError(f"Artifact lease root is not a directory: {lock_path.parent}")
            os.fchmod(directory_fd, 0o700)

            fd = os.open(
                lock_path.name,
                os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0),
                0o600,
                dir_fd=directory_fd,
            )
            lease_stat = os.fstat(fd)
            if not stat.S_ISREG(lease_stat.st_mode):
                raise RuntimeError(f"Artifact lease is not a regular file: {lock_path}")
            os.fchmod(fd, 0o600)

            operation = fcntl.LOCK_SH if shared else fcntl.LOCK_EX
            if not blocking:
                operation |= fcntl.LOCK_NB
            try:
                fcntl.flock(fd, operation)
            except OSError as exc:
                if not blocking and exc.errno in cls._CONTENTION_ERRNOS:
                    raise ArtifactLeaseContention(lock_path) from exc
                raise
        except BaseException as primary:
            if fd is not None:
                _close_preserving_primary(fd, primary)
            _close_preserving_primary(directory_fd, primary)
            raise
        try:
            os.close(directory_fd)
        except BaseException as directory_close_error:
            if fd is not None:
                _close_preserving_primary(fd, directory_close_error)
            raise
        assert fd is not None
        try:
            return cls(
                path=lock_path,
                fd=fd,
                shared=shared,
                _construction_token=_ARTIFACT_LEASE_CONSTRUCTION_TOKEN,
            )
        except BaseException as construction_error:
            _close_preserving_primary(fd, construction_error)
            raise

    @property
    def inherited_fds(self) -> tuple[int, ...]:
        """Descriptor tuple suitable for ``pass_fds`` while the lease is open."""
        return () if self.fd is None else (self.fd,)

    @property
    def closed(self) -> bool:
        return self.fd is None

    def fileno(self) -> int:
        if self.fd is None:
            raise ValueError("Artifact lease is closed")
        return self.fd

    def close(self) -> None:
        """Release ownership by close only; safe to call repeatedly."""
        fd = self.fd
        if fd is None:
            return
        self.fd = None
        os.close(fd)

    def __enter__(self) -> ArtifactLease:
        return self

    def __exit__(
        self,
        exc_type: object,
        exc: object,
        traceback: object,
    ) -> None:
        self.close()
