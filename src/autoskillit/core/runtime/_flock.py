"""Bounded POSIX advisory-lock acquisition primitives."""

from __future__ import annotations

import errno
import math
import random
import time
from pathlib import Path

try:
    import fcntl
except ImportError:  # pragma: no cover - exercised through the platform guard
    fcntl = None  # type: ignore[assignment]

__all__ = ["acquire_flock_with_timeout"]

_CONTENTION_ERRNOS = frozenset({errno.EACCES, errno.EAGAIN})
_RETRY_MIN_SECONDS = 0.01
_RETRY_MAX_SECONDS = 0.05


def _validate_flock_timeout(timeout: float) -> float:
    """Validate and normalize a finite, nonnegative flock timeout."""
    if (
        isinstance(timeout, bool)
        or not isinstance(timeout, (int, float))
        or not math.isfinite(timeout)
        or timeout < 0
    ):
        raise ValueError("timeout must be a finite non-negative number")
    return float(timeout)


def acquire_flock_with_timeout(
    fd: int,
    *,
    operation: int,
    timeout: float,
    path: Path,
) -> None:
    """Acquire *operation* without blocking the process beyond its deadline."""
    if fcntl is None:
        raise RuntimeError("POSIX flock is unavailable on this platform")

    validated_timeout = _validate_flock_timeout(timeout)
    deadline = time.monotonic() + validated_timeout
    nonblocking_operation = operation | fcntl.LOCK_NB
    while True:
        try:
            fcntl.flock(fd, nonblocking_operation)
            return
        except OSError as exc:
            if exc.errno not in _CONTENTION_ERRNOS:
                raise
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(
                    errno.ETIMEDOUT,
                    "Timed out acquiring flock",
                    str(Path(path)),
                ) from exc
            time.sleep(min(remaining, random.uniform(_RETRY_MIN_SECONDS, _RETRY_MAX_SECONDS)))
