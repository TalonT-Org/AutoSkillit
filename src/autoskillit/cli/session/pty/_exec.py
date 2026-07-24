"""Minimal POSIX exec-side launcher for an already-created PTY session."""

from __future__ import annotations

import fcntl
import os
import sys
import termios
from collections.abc import Mapping, Sequence
from typing import NoReturn

_MODULE_NAME = "autoskillit.cli.session.pty._exec"


def launcher_argv(
    slave_fd: int,
    command: Sequence[str],
    *,
    lease_fds: Sequence[int] = (),
) -> tuple[str, ...]:
    """Build the interpreter command used by the parent-side process owner."""
    _validate_slave_fd(slave_fd)
    normalized = _validate_command(command)
    normalized_leases = _validate_lease_fds(lease_fds, slave_fd=slave_fd)
    return (
        sys.executable,
        "-m",
        _MODULE_NAME,
        str(slave_fd),
        *(str(fd) for fd in sorted(normalized_leases)),
        "--",
        *normalized,
    )


def exec_in_pty(
    slave_fd: int,
    command: Sequence[str],
    environment: Mapping[str, str],
    *,
    lease_fds: Sequence[int] = (),
) -> NoReturn:
    """Attach the slave as controlling terminal, duplicate stdio, and exec."""
    _validate_slave_fd(slave_fd)
    normalized_command = _validate_command(command)
    normalized_environment = _validate_environment(environment)
    normalized_leases = _validate_lease_fds(lease_fds, slave_fd=slave_fd)

    tiocsctty = getattr(termios, "TIOCSCTTY", None)
    if tiocsctty is None:
        raise RuntimeError("This platform does not provide TIOCSCTTY")
    fcntl.ioctl(slave_fd, tiocsctty, 0)
    for standard_fd in (0, 1, 2):
        if slave_fd != standard_fd:
            os.dup2(slave_fd, standard_fd, inheritable=True)
        else:
            os.set_inheritable(standard_fd, True)
    if slave_fd > 2 and slave_fd not in normalized_leases:
        os.close(slave_fd)
    for lease_fd in normalized_leases:
        os.set_inheritable(lease_fd, True)
    os.execvpe(
        normalized_command[0],
        list(normalized_command),
        normalized_environment,
    )
    raise AssertionError("os.execvpe unexpectedly returned")


def main(argv: Sequence[str] | None = None) -> NoReturn:
    """Parse the deliberately small launcher protocol and exec the target."""
    arguments = tuple(sys.argv[1:] if argv is None else argv)
    try:
        separator = arguments.index("--")
    except ValueError as exc:
        raise SystemExit("usage: _exec SLAVE_FD [LEASE_FD ...] -- COMMAND [ARG ...]") from exc
    if separator < 1 or separator + 1 >= len(arguments):
        raise SystemExit("usage: _exec SLAVE_FD [LEASE_FD ...] -- COMMAND [ARG ...]")
    try:
        slave_fd = int(arguments[0], 10)
        lease_fds = tuple(int(value, 10) for value in arguments[1:separator])
    except ValueError as exc:
        raise SystemExit("SLAVE_FD and LEASE_FD values must be decimal integers") from exc
    exec_in_pty(
        slave_fd,
        arguments[separator + 1 :],
        os.environ,
        lease_fds=lease_fds,
    )


def _validate_slave_fd(slave_fd: int) -> None:
    if isinstance(slave_fd, bool) or not isinstance(slave_fd, int) or slave_fd < 0:
        raise ValueError("slave_fd must be a non-negative integer")
    os.fstat(slave_fd)


def _validate_command(command: Sequence[str]) -> tuple[str, ...]:
    normalized = tuple(command)
    if not normalized or any(
        not isinstance(item, str) or not item or "\0" in item for item in normalized
    ):
        raise ValueError("command must contain non-empty NUL-free strings")
    return normalized


def _validate_environment(environment: Mapping[str, str]) -> dict[str, str]:
    normalized: dict[str, str] = {}
    for key, value in environment.items():
        if (
            not isinstance(key, str)
            or not key
            or "=" in key
            or "\0" in key
            or not isinstance(value, str)
            or "\0" in value
        ):
            raise ValueError("environment must contain valid string keys and values")
        normalized[key] = value
    return normalized


def _validate_lease_fds(
    lease_fds: Sequence[int],
    *,
    slave_fd: int,
) -> frozenset[int]:
    normalized: set[int] = set()
    for fd in lease_fds:
        if isinstance(fd, bool) or not isinstance(fd, int) or fd < 3:
            raise ValueError("lease descriptors must be integers greater than two")
        os.fstat(fd)
        if fd != slave_fd:
            normalized.add(fd)
    return frozenset(normalized)


if __name__ == "__main__":
    main()


__all__ = ["exec_in_pty", "launcher_argv", "main"]
