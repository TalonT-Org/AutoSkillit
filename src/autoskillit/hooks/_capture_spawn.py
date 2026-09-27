"""Owned process-group spawn mechanics for the isolated shell runner."""

from __future__ import annotations

import errno
import logging
import os
import subprocess
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING

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
_ANCHOR_SCRIPT = (
    "trap '' HUP INT QUIT USR1 USR2 PIPE ALRM TERM TSTP TTIN TTOU VTALRM PROF XCPU XFSZ; "
    "echo; read -r _; kill -KILL 0"
)


def _spawn_anchor(bash_path: str) -> tuple[subprocess.Popen[bytes], int]:
    open_fds: set[int] = set()
    anchor: subprocess.Popen[bytes] | None = None
    lifeline_write = -1

    def close_fd(fd: int) -> None:
        if fd in open_fds:
            open_fds.remove(fd)
            os.close(fd)

    try:
        lifeline_read, lifeline_write = os.pipe()
        open_fds.update((lifeline_read, lifeline_write))
        ready_read, ready_write = os.pipe()
        open_fds.update((ready_read, ready_write))
        anchor = subprocess.Popen(
            [bash_path, "-c", _ANCHOR_SCRIPT],
            stdin=lifeline_read,
            stdout=ready_write,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            env={},
            cwd="/",
            process_group=0,
            start_new_session=False,
        )
        close_fd(lifeline_read)
        close_fd(ready_write)
        armed = os.read(ready_read, 1) == b"\n"
        close_fd(ready_read)
        if not armed:
            error = CaptureSetupError.unknown(
                "cannot spawn capture shell: owned process anchor did not arm"
            )
            open_fds.remove(lifeline_write)
            _abandon_anchor(anchor, lifeline_write, primary_error=error)
            raise error
        open_fds.remove(lifeline_write)
        return anchor, lifeline_write
    except BaseException as primary_error:
        if anchor is not None and lifeline_write in open_fds:
            open_fds.remove(lifeline_write)
            _abandon_anchor(anchor, lifeline_write, primary_error=primary_error)
        for fd in tuple(open_fds):
            try:
                close_fd(fd)
            except BaseException as cleanup_error:
                logger.error("owned_process_anchor_pipe_cleanup_failed", exc_info=True)
                _capture_process._add_cleanup_failure_note(
                    primary_error,
                    "owned process anchor pipe cleanup also failed",
                    cleanup_error,
                )
        raise


def _abandon_anchor(
    anchor: subprocess.Popen[bytes],
    lifeline_fd: int,
    *,
    primary_error: BaseException,
) -> None:
    try:
        os.close(lifeline_fd)
    except BaseException as cleanup_error:
        logger.error("owned_process_anchor_lifeline_close_failed", exc_info=True)
        _capture_process._add_cleanup_failure_note(
            primary_error,
            "owned process anchor cleanup also failed",
            cleanup_error,
        )
    try:
        anchor.wait(timeout=_capture_process._KILL_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        try:
            anchor.kill()
        except BaseException as cleanup_error:
            logger.error("owned_process_anchor_kill_failed", exc_info=True)
            _capture_process._add_cleanup_failure_note(
                primary_error,
                "owned process anchor cleanup also failed",
                cleanup_error,
            )
        try:
            anchor.wait(timeout=_capture_process._KILL_TIMEOUT_SECONDS)
        except BaseException as cleanup_error:
            logger.error("owned_process_anchor_reap_failed", exc_info=True)
            _capture_process._add_cleanup_failure_note(
                primary_error,
                "owned process anchor cleanup also failed",
                cleanup_error,
            )
    except BaseException as cleanup_error:
        logger.error("owned_process_anchor_reap_failed", exc_info=True)
        _capture_process._add_cleanup_failure_note(
            primary_error,
            "owned process anchor cleanup also failed",
            cleanup_error,
        )


def _popen_leader(
    argv: Sequence[str],
    *,
    env: Mapping[str, str],
    capture_output: bool,
    pgid: int,
) -> subprocess.Popen[bytes]:
    return subprocess.Popen(
        list(argv),
        stdout=subprocess.PIPE if capture_output else None,
        stderr=subprocess.STDOUT if capture_output else None,
        stdin=subprocess.DEVNULL if capture_output else None,
        close_fds=True,
        env=dict(env),
        process_group=pgid,
        start_new_session=False,
    )


def spawn_owned_process(
    argv: Sequence[str],
    *,
    cwd_fd: int,
    env: Mapping[str, str],
    capture_output: bool,
) -> _capture_process.OwnedProcessGroup:
    """Spawn one child in the process group held by a runner-owned anchor."""

    _capture_process._require_posix_process_ownership()
    anchor, lifeline_fd = _spawn_anchor(_capture_process._resolve_bash())
    original_cwd_fd: int | None = None
    process: subprocess.Popen[bytes] | None = None
    restore_error: OSError | None = None
    try:
        original_cwd_fd = os.open(".", os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        os.fchdir(cwd_fd)
        process = _popen_leader(
            list(argv),
            env=env,
            capture_output=capture_output,
            pgid=anchor.pid,
        )
    except BaseException as primary_error:
        if process is None:
            _abandon_anchor(anchor, lifeline_fd, primary_error=primary_error)
        raise
    finally:
        if original_cwd_fd is not None:
            try:
                os.fchdir(original_cwd_fd)
            except OSError as exc:
                restore_error = exc
            finally:
                os.close(original_cwd_fd)

    if process is None:
        error = _capture_process.OwnedProcessError("owned process did not start")
        _abandon_anchor(anchor, lifeline_fd, primary_error=error)
        raise error
    if restore_error is not None:
        error = _capture_process.OwnedProcessError("cannot restore runner cwd")
        try:
            _capture_process._settle_failed_capture(
                _finish_owned_spawn(
                    process,
                    anchor=anchor,
                    lifeline_fd=lifeline_fd,
                    inherit_terminal=not capture_output,
                ),
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
    return _finish_owned_spawn(
        process,
        anchor=anchor,
        lifeline_fd=lifeline_fd,
        inherit_terminal=not capture_output,
    )


def _finish_owned_spawn(
    process: subprocess.Popen[bytes],
    *,
    anchor: subprocess.Popen[bytes],
    lifeline_fd: int,
    inherit_terminal: bool,
) -> _capture_process.OwnedProcessGroup:
    """Finish ownership setup for a process atomically spawned by this module."""

    pgid = anchor.pid
    identity_error: BaseException | None = None
    try:
        anchor_exit = _capture_process._poll_leader_without_reaping(anchor, pgid)
        valid_anchor = (
            pgid > 1
            and anchor_exit is None
            and anchor.returncode is None
            and os.getpgid(anchor.pid) == pgid
        )
        valid_leader = process.returncode is None and os.getpgid(process.pid) == pgid
    except (OSError, _capture_process.OwnedProcessError) as exc:
        identity_error = exc
        valid_anchor = False
        valid_leader = False
    if not valid_anchor or not valid_leader:
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
        _abandon_anchor(anchor, lifeline_fd, primary_error=error)
        raise error from identity_error

    owner = _capture_process.OwnedProcessGroup(
        process=process,
        pgid=pgid,
        anchor=anchor,
        _lifeline_fd=lifeline_fd,
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
    anchor: subprocess.Popen[bytes] | None = None
    lifeline_fd = -1
    restore_error: OSError | None = None
    try:
        anchor, lifeline_fd = _spawn_anchor(bash_path)
        os.fchdir(inherited_cwd_fd)
        process = _popen_leader(
            [bash_path, "-c", command],
            env=_scrubbed_user_environment(),
            capture_output=capture_output,
            pgid=anchor.pid,
        )
    except OSError as exc:
        if exc.errno == errno.E2BIG:
            mapped = CaptureSetupError.from_os_error(
                exc,
                "capture shell spawn rejected: argument/environment exceeds system limit",
            )
        else:
            mapped = CaptureSetupError.from_os_error(exc, "cannot spawn capture shell")
        if anchor is not None and process is None:
            _abandon_anchor(anchor, lifeline_fd, primary_error=mapped)
        raise mapped from exc
    except BaseException as primary_error:
        if anchor is not None and process is None:
            _abandon_anchor(anchor, lifeline_fd, primary_error=primary_error)
        raise
    finally:
        try:
            os.fchdir(inherited_cwd_fd)
        except OSError as exc:
            restore_error = exc
        os.close(inherited_cwd_fd)

    assert process is not None and anchor is not None
    if restore_error is not None:
        _capture_process._settle_failed_capture(
            _finish_owned_spawn(
                process,
                anchor=anchor,
                lifeline_fd=lifeline_fd,
                inherit_terminal=not capture_output,
            )
        )
        raise CaptureSetupError.from_os_error(
            restore_error, "cannot restore runner cwd"
        ) from restore_error
    return _finish_owned_spawn(
        process,
        anchor=anchor,
        lifeline_fd=lifeline_fd,
        inherit_terminal=not capture_output,
    )


if TYPE_CHECKING:
    from autoskillit.hooks import _capture_process
elif __package__:
    from . import _capture_process
else:
    import _capture_process

logger: logging.Logger = _capture_process.logger  # type: ignore[has-type]

if __package__:
    from ._capture import _module_identity
else:
    from _capture import _module_identity as _standalone_module_identity

    _module_identity = _standalone_module_identity
_module_identity.register_module_aliases(__name__)
