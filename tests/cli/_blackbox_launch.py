"""Drive a real ``autoskillit`` CLI launch over a PTY and answer its prompt.

Shared by the cook and order black-box launch tests. These are the only tests
that stub nothing — no ``Popen`` patch, no validation-surface patch — so a
pre-spawn check that fires on legitimate state fails here and nowhere else.
"""

from __future__ import annotations

import fcntl
import os
import select
import subprocess
import sys
import termios
import time
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType

from tests.execution._process_group_helpers import _cleanup_owned_process_group

pty: ModuleType | None
if os.name == "posix":
    import pty as _pty

    pty = _pty
else:  # pragma: no cover - the module's tests are skipped on non-POSIX hosts
    pty = None

_MAX_DIAGNOSTIC_BYTES = 256 * 1024
LAUNCH_PROMPT = b"Launch session?"


def _acquire_controlling_terminal() -> None:  # pragma: no cover - runs post-fork
    """Make the child a session leader that owns the PTY as its controlling terminal.

    ``start_new_session=True`` alone detaches the child from the parent's terminal
    without giving it a new one, so any foreground-process-group management in the
    CLI fails with ``ENOTTY`` on ``tcgetpgrp``. That is a harness artifact — a real
    shell always supplies a controlling terminal — and masking it would mean these
    tests never exercise the code path that runs for actual users.
    """
    os.setsid()
    fcntl.ioctl(0, termios.TIOCSCTTY, 0)


@dataclass(frozen=True)
class LaunchOutcome:
    returncode: int | None
    output: str
    prompt_seen: bool


def hermetic_launch_env(
    *,
    project: Path,
    isolated_home: Path,
    state_dir: Path,
    shim_dir: Path,
    temp_dir: Path,
    xdg_roots: dict[str, Path],
    extra: dict[str, str] | None = None,
) -> dict[str, str]:
    """A launch environment that reaches nothing outside the supplied roots."""
    environment = {
        "AUTOSKILLIT_PROJECT_DIR": str(project),
        "AUTOSKILLIT_SKIP_UPDATE_CHECK": "1",
        "AUTOSKILLIT_STATE_DIR": str(state_dir),
        "HOME": str(isolated_home),
        "LC_ALL": "C",
        "NO_COLOR": "1",
        "PATH": os.pathsep.join(
            (str(shim_dir), str(Path(sys.executable).parent), "/usr/bin", "/bin")
        ),
        "PYTHONDONTWRITEBYTECODE": "1",
        "TERM": "dumb",
        "TMPDIR": str(temp_dir),
        "XDG_CACHE_HOME": str(xdg_roots["cache"]),
        "XDG_CONFIG_HOME": str(xdg_roots["config"]),
        "XDG_DATA_HOME": str(xdg_roots["data"]),
        "XDG_STATE_HOME": str(xdg_roots["state"]),
    }
    if extra:
        environment.update(extra)
    return environment


def run_cli_launch(
    args: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    prompt: bytes = LAUNCH_PROMPT,
    timeout_seconds: float = 30.0,
) -> LaunchOutcome:
    """Spawn the real CLI on a PTY, answer ``prompt`` once, and reap the group."""
    assert pty is not None
    master_fd, slave_fd = pty.openpty()
    process: subprocess.Popen[bytes] | None = None
    retained = bytearray()
    prompt_seen = False
    deadline = time.monotonic() + timeout_seconds
    slave_open = True
    master_open = True
    try:
        process = subprocess.Popen(
            [sys.executable, "-c", "from autoskillit.cli import main; main()", *args],
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            cwd=cwd,
            env=env,
            close_fds=True,
            preexec_fn=_acquire_controlling_terminal,
        )
        os.close(slave_fd)
        slave_open = False

        while time.monotonic() < deadline:
            wait_for = min(0.1, max(0.0, deadline - time.monotonic()))
            readable, _, _ = select.select([master_fd], [], [], wait_for)
            if not readable:
                continue
            try:
                chunk = os.read(master_fd, 64 * 1024)
            except OSError:
                break
            if not chunk:
                break
            retained.extend(chunk)
            if len(retained) > _MAX_DIAGNOSTIC_BYTES:
                del retained[:-_MAX_DIAGNOSTIC_BYTES]
            if not prompt_seen and prompt in retained:
                os.write(master_fd, b"\n")
                prompt_seen = True
    finally:
        try:
            if slave_open:
                os.close(slave_fd)
        finally:
            try:
                if process is not None:
                    _cleanup_owned_process_group(process, timeout=5)
            finally:
                if master_open:
                    os.close(master_fd)
                    master_open = False

    return LaunchOutcome(
        returncode=process.returncode if process is not None else None,
        output=bytes(retained).decode("utf-8", errors="replace"),
        prompt_seen=prompt_seen,
    )
