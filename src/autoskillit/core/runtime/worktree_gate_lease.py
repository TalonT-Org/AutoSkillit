"""Process-tree-lived exclusion for test gates on one canonical worktree.

The lease uses the existing POSIX ``fcntl.flock`` authority on a local filesystem.
It is not a cross-host network-filesystem lease and has no Windows fallback.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path

from ..io import read_versioned_json, write_versioned_json
from ..paths import default_log_dir
from ._linux_proc import read_boot_id, read_starttime_ticks
from .artifact_lease import ArtifactLease, ArtifactLeaseContention

__all__ = ["WorktreeGateContention", "WorktreeGateLease"]


def _write_gate_holder_manifest(path: Path, holder: dict[str, object]) -> None:
    write_versioned_json(path, holder, schema_version=1)


def _read_holder_diagnostic(path: Path) -> str:
    holder = read_versioned_json(path, expected_version=1)
    if holder is None:
        return "holder diagnostics unavailable"
    return ", ".join(
        f"{key}={holder.get(key)!r}"
        for key in ("pid", "starttime_ticks", "boot_id", "invocation_id")
    )


class WorktreeGateContention(RuntimeError):
    """Raised when another process tree owns a worktree's test gate."""

    def __init__(self, worktree: Path, diagnostic_path: Path) -> None:
        self.worktree = worktree
        self.diagnostic_path = diagnostic_path
        holder = _read_holder_diagnostic(diagnostic_path)
        super().__init__(f"Test gate already active for {worktree}; recorded holder: {holder}")


@dataclass(slots=True, init=False)
class WorktreeGateLease:
    """Exclusive gate lease whose descriptor may be inherited by child processes."""

    worktree: Path
    diagnostic_path: Path
    _lease: ArtifactLease

    def __init__(
        self,
        *,
        worktree: Path,
        diagnostic_path: Path,
        lease: ArtifactLease,
    ) -> None:
        self.worktree = worktree
        self.diagnostic_path = diagnostic_path
        self._lease = lease

    @classmethod
    def acquire(cls, cwd: str | os.PathLike[str], *, invocation_id: str) -> WorktreeGateLease:
        canonical = Path(os.path.realpath(os.fspath(cwd)))
        digest = hashlib.sha256(os.fsencode(canonical)).hexdigest()
        lease_root = default_log_dir() / "gate-leases"
        lock_path = lease_root / f"{digest}.lock"
        diagnostic_path = lease_root / f"{digest}.json"
        try:
            lease = ArtifactLease.acquire_exclusive(lock_path, timeout=0.0)
        except ArtifactLeaseContention as exc:
            raise WorktreeGateContention(canonical, diagnostic_path) from exc

        try:
            _write_gate_holder_manifest(
                diagnostic_path,
                {
                    "boot_id": read_boot_id(),
                    "invocation_id": invocation_id,
                    "pid": os.getpid(),
                    "starttime_ticks": read_starttime_ticks(os.getpid()),
                },
            )
        except BaseException:
            lease.close()
            raise
        return cls(worktree=canonical, diagnostic_path=diagnostic_path, lease=lease)

    @property
    def inherited_fds(self) -> tuple[int, ...]:
        return self._lease.inherited_fds

    def close(self) -> None:
        self._lease.close()
