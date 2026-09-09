"""Owned process-group spawn mechanics for the isolated shell runner."""

from __future__ import annotations

import errno
import logging
import os
import subprocess
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from autoskillit.hooks._capture._authority import (
        _DIRECTORY_FLAGS,
        CaptureSetupError,
    )
    from autoskillit.hooks._capture_contract import PROTECTED_CAPTURE_ENV_VARS
elif __package__:
    from ._capture._authority import _DIRECTORY_FLAGS, CaptureSetupError
    from ._capture_contract import PROTECTED_CAPTURE_ENV_VARS
else:
    from _capture._authority import _DIRECTORY_FLAGS, CaptureSetupError
    from _capture_contract import PROTECTED_CAPTURE_ENV_VARS

_TRUSTED_BASH_CANDIDATES = ("/bin/bash", "/usr/bin/bash")


def spawn_owned_process(
    argv: Sequence[str],
    *,
    cwd_fd: int,
    env: Mapping[str, str],
    capture_output: bool,
) -> _capture_process.OwnedProcessGroup:
    """Spawn one child in a fresh process group beneath a descriptor cwd."""

    _capture_process._require_posix_process_ownership()
    original_cwd_fd = os.open(".", os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    process: subprocess.Popen[bytes] | None = None
    restore_error: OSError | None = None
    try:
        os.fchdir(cwd_fd)
        process = subprocess.Popen(
            list(argv),
            stdout=subprocess.PIPE if capture_output else None,
            stderr=subprocess.STDOUT if capture_output else None,
            close_fds=True,
            env=dict(env),
            process_group=0,
            start_new_session=False,
        )
    finally:
        try:
            os.fchdir(original_cwd_fd)
        except OSError as exc:
            restore_error = exc
        finally:
            os.close(original_cwd_fd)

    if process is None:
        raise _capture_process.OwnedProcessError("owned process did not start")
    if restore_error is not None:
        error = _capture_process.OwnedProcessError("cannot restore runner cwd")
        try:
            _capture_process._settle_failed_capture(
                _finish_owned_spawn(process, inherit_terminal=not capture_output),
                primary_error=error,
            )
        except BaseException as cleanup_error:
            logger.error("owned_process_cwd_restore_cleanup_failed", exc_info=True)
            _capture_process._add_cleanup_failure_note(
                error,
                "owned process cwd-restore cleanup also failed",
                cleanup_error,
            )
        raise error from restore_error
    return _finish_owned_spawn(process, inherit_terminal=not capture_output)


def _finish_owned_spawn(
    process: subprocess.Popen[bytes],
    *,
    inherit_terminal: bool,
) -> _capture_process.OwnedProcessGroup:
    """Finish ownership setup for a process atomically spawned by this module."""

    pgid = process.pid
    identity_error: OSError | None = None
    try:
        valid_leader = pgid > 1 and process.returncode is None and os.getpgid(pgid) == pgid
    except OSError as exc:
        identity_error = exc
        valid_leader = False
    if not valid_leader:
        error = _capture_process.OwnedProcessError("unsafe owned process group identity")
        try:
            process.kill()
        except BaseException as cleanup_error:
            logger.error("owned_process_identity_kill_failed", exc_info=True)
            _capture_process._add_cleanup_failure_note(
                error,
                "owned process identity cleanup kill also failed",
                cleanup_error,
            )
        try:
            process.wait(timeout=_capture_process._KILL_TIMEOUT_SECONDS)
        except BaseException as cleanup_error:
            logger.error("owned_process_identity_reap_failed", exc_info=True)
            _capture_process._add_cleanup_failure_note(
                error,
                "owned process identity cleanup reap also failed",
                cleanup_error,
            )
        raise error from identity_error

    owner = _capture_process.OwnedProcessGroup(
        process=process,
        pgid=pgid,
        _spawn_token=_capture_process._OWNED_PROCESS_SPAWN_TOKEN,
    )
    try:
        owner._previous_handlers = _capture_process._install_signal_forwarding(owner)
        if inherit_terminal:
            terminal = _capture_process._take_foreground_process_group(pgid)
            if terminal is not None:
                owner._terminal_fd, owner._previous_foreground_pgid = terminal
    except BaseException:
        logger.error("owned_process_adoption_failed", exc_info=True)
        owner.settle()
        raise
    return owner


def _wrap_user_command(command: str) -> str:
    separator = "" if command.endswith("\n") else "\n"
    return f"(\ntrap '__as_user_ec=$?; wait; exit \"$__as_user_ec\"' EXIT\n{command}{separator})"


def _scrubbed_user_environment() -> dict[str, str]:
    environment = dict(os.environ)
    for name in PROTECTED_CAPTURE_ENV_VARS:
        environment.pop(name, None)
    # Unconditional, not conditional-forward: an *unset* DBUS_SESSION_BUS_ADDRESS is what
    # triggers libdbus autolaunch, so this must be set even when the host has none.
    # Duplicated from core._claude_env.resolve_dbus_session_bus_address so standalone
    # hook imports do not depend on autoskillit.core, matching the _hook_settings.py
    # duplication precedent.
    environment["DBUS_SESSION_BUS_ADDRESS"] = os.environ.get("DBUS_SESSION_BUS_ADDRESS") or (
        "disabled:"
    )
    return environment


def _spawn_bash(
    bash_path: str,
    command: str,
    *,
    capture_output: bool,
) -> _capture_process.OwnedProcessGroup:
    try:
        inherited_cwd_fd = os.open(
            ".",
            _DIRECTORY_FLAGS & ~getattr(os, "O_NOFOLLOW", 0),
        )
    except OSError as exc:
        raise CaptureSetupError.from_os_error(exc, "cannot preserve runner cwd") from exc

    process: subprocess.Popen[bytes] | None = None
    restore_error: OSError | None = None
    try:
        os.fchdir(inherited_cwd_fd)
        process = subprocess.Popen(
            [bash_path, "-c", _wrap_user_command(command)],
            stdout=subprocess.PIPE if capture_output else None,
            stderr=subprocess.STDOUT if capture_output else None,
            close_fds=True,
            env=_scrubbed_user_environment(),
            process_group=0,
            start_new_session=False,
        )
    except OSError as exc:
        if exc.errno == errno.E2BIG:
            raise CaptureSetupError.from_os_error(
                exc,
                "capture shell spawn rejected: argument/environment exceeds system limit",
            ) from exc
        raise CaptureSetupError.from_os_error(exc, "cannot spawn capture shell") from exc
    finally:
        try:
            os.fchdir(inherited_cwd_fd)
        except OSError as exc:
            restore_error = exc
        os.close(inherited_cwd_fd)

    if restore_error is not None:
        if process is not None:
            _capture_process._settle_failed_capture(
                _finish_owned_spawn(process, inherit_terminal=not capture_output)
            )
        raise CaptureSetupError.from_os_error(
            restore_error, "cannot restore runner cwd"
        ) from restore_error
    if process is None:
        raise CaptureSetupError.unknown("capture shell did not start")
    return _finish_owned_spawn(process, inherit_terminal=not capture_output)


if TYPE_CHECKING:
    from autoskillit.hooks import _capture_process
elif __package__:
    from . import _capture_process
else:
    import _capture_process

logger = cast(logging.Logger, getattr(_capture_process, "logger"))

if __package__:
    from ._capture import _module_identity
else:
    from _capture import _module_identity as _standalone_module_identity

    _module_identity = _standalone_module_identity
_module_identity.register_module_aliases(__name__)
