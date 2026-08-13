"""Black-box coverage for the real ``autoskillit order`` launch boundary."""

from __future__ import annotations

import os
import select
import subprocess
import time
from pathlib import Path
from types import ModuleType

import pytest

from autoskillit.core import atomic_write
from tests.execution._process_group_helpers import _cleanup_owned_process_group

pty: ModuleType | None
if os.name == "posix":
    import pty as _pty

    pty = _pty
else:  # pragma: no cover - the module's test is skipped on non-POSIX hosts
    pty = None

pytestmark = [pytest.mark.layer("cli"), pytest.mark.medium]

_MAX_DIAGNOSTIC_BYTES = 256 * 1024
_PROMPT = b"Launch session?"


def _filesystem_snapshot(path: Path) -> tuple[object, ...]:
    """Capture a metadata tree without following links or reading host data."""
    try:
        root_stat = path.lstat()
    except FileNotFoundError:
        return ("missing",)
    if path.is_symlink():
        return ("symlink", os.readlink(path), root_stat.st_mtime_ns)
    if not path.is_dir():
        return ("file", root_stat.st_size, root_stat.st_mtime_ns, root_stat.st_mode)

    entries: list[tuple[object, ...]] = []
    for root, directories, files in os.walk(path, followlinks=False):
        root_path = Path(root)
        directories.sort()
        files.sort()
        for name in [*directories, *files]:
            entry = root_path / name
            try:
                entry_stat = entry.lstat()
            except FileNotFoundError:
                entries.append((str(entry.relative_to(path)), "vanished"))
                continue
            kind = "symlink" if entry.is_symlink() else "dir" if entry.is_dir() else "file"
            entries.append(
                (
                    str(entry.relative_to(path)),
                    kind,
                    entry_stat.st_size,
                    entry_stat.st_mtime_ns,
                    entry_stat.st_mode,
                )
            )
    return ("directory", root_stat.st_mtime_ns, tuple(entries))


def _host_paths() -> tuple[Path, ...]:
    home = Path.home()
    defaults = (
        home / ".claude.json",
        home / ".claude",
        home / ".autoskillit",
        home / ".config" / "autoskillit",
        home / ".cache" / "autoskillit",
        home / ".local" / "share" / "autoskillit",
        home / ".local" / "state" / "autoskillit",
    )
    configured = tuple(
        Path(value) / "autoskillit"
        for key in ("XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME")
        if (value := os.environ.get(key))
    )
    return tuple(dict.fromkeys((*defaults, *configured)))


def _git_status(worktree: Path) -> bytes:
    result = subprocess.run(
        ["git", "status", "--porcelain=v1", "--untracked-files=all"],
        cwd=worktree,
        check=True,
        capture_output=True,
    )
    return result.stdout


def _write_fixture(project: Path, isolated_home: Path, shim_dir: Path) -> None:
    atomic_write(
        project / ".autoskillit" / "recipes" / "launch-probe.yaml",
        """name: launch-probe
description: Hermetic interactive launch probe
summary: done
kitchen_rules:
  - Stop after the launch probe completes.
steps:
  done:
    action: stop
    message: Launch probe complete
""",
    )
    atomic_write(
        project / ".autoskillit" / "config.yaml",
        """agent_backend:
  backend: claude-code
workspace:
  temp_dir: .autoskillit/temp
""",
    )
    atomic_write(
        isolated_home / ".claude" / "plugins" / "installed_plugins.json",
        '{"version": 2, "plugins": {}}\n',
    )
    shim = shim_dir / "claude"
    atomic_write(
        shim,
        """#!/bin/sh
if [ "${1-}" = "--version" ]; then
  printf '%s\n' '2.1.220 (Claude Code)'
  exit 0
fi
marker="$AUTOSKILLIT_STATE_DIR/claude-launch-argv.txt"
temporary="${marker}.tmp.$$"
printf '%s\n' "$@" > "$temporary"
mv "$temporary" "$marker"
exit 0
""",
    )
    shim.chmod(0o755)


@pytest.mark.skipif(os.name != "posix", reason="POSIX PTY launch coverage")
def test_order_launches_real_cli_without_host_side_effects(tmp_path: Path) -> None:
    worktree = Path(__file__).resolve().parents[2]
    autoskillit = worktree / ".venv" / "bin" / "autoskillit"
    project = tmp_path / "project"
    isolated_home = tmp_path / "home"
    state_dir = tmp_path / "state"
    shim_dir = tmp_path / "bin"
    temp_dir = tmp_path / "tmp"
    xdg_config = tmp_path / "xdg-config"
    xdg_cache = tmp_path / "xdg-cache"
    xdg_data = tmp_path / "xdg-data"
    xdg_state = tmp_path / "xdg-state"
    for directory in (
        project,
        isolated_home,
        state_dir,
        shim_dir,
        temp_dir,
        xdg_config,
        xdg_cache,
        xdg_data,
        xdg_state,
    ):
        directory.mkdir(parents=True)
    _write_fixture(project, isolated_home, shim_dir)

    environment = {
        "AUTOSKILLIT_PROJECT_DIR": str(project),
        "AUTOSKILLIT_SKIP_UPDATE_CHECK": "1",
        "AUTOSKILLIT_STATE_DIR": str(state_dir),
        "HOME": str(isolated_home),
        "LC_ALL": "C",
        "NO_COLOR": "1",
        "PATH": os.pathsep.join(
            (str(shim_dir), str(worktree / ".venv" / "bin"), "/usr/bin", "/bin")
        ),
        "PYTHONDONTWRITEBYTECODE": "1",
        "TERM": "dumb",
        "TMPDIR": str(temp_dir),
        "XDG_CACHE_HOME": str(xdg_cache),
        "XDG_CONFIG_HOME": str(xdg_config),
        "XDG_DATA_HOME": str(xdg_data),
        "XDG_STATE_HOME": str(xdg_state),
    }
    host_before = {path: _filesystem_snapshot(path) for path in _host_paths()}
    status_before = _git_status(worktree)

    assert pty is not None
    master_fd, slave_fd = pty.openpty()
    process: subprocess.Popen[bytes] | None = None
    retained = bytearray()
    prompt_seen = False
    deadline = time.monotonic() + 30
    slave_open = True
    master_open = True
    try:
        process = subprocess.Popen(
            [str(autoskillit), "order", "launch-probe"],
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            cwd=project,
            env=environment,
            close_fds=True,
            start_new_session=True,
        )
        os.close(slave_fd)
        slave_open = False

        while time.monotonic() < deadline:
            timeout = min(0.1, max(0.0, deadline - time.monotonic()))
            readable, _, _ = select.select([master_fd], [], [], timeout)
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
            if not prompt_seen and _PROMPT in retained:
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

    output = bytes(retained).decode("utf-8", errors="replace")
    assert prompt_seen, output
    assert process is not None and process.returncode == 0, output
    assert (state_dir / "claude-launch-argv.txt").is_file(), output
    expected_artifacts = (
        project / ".autoskillit" / "temp",
        isolated_home / ".autoskillit" / "plugin-projections",
        isolated_home / ".claude" / "plugins" / "installed_plugins.json",
        state_dir / "claude-launch-argv.txt",
    )
    allowed_roots = (project.resolve(), isolated_home.resolve(), state_dir.resolve())
    for artifact in expected_artifacts:
        assert artifact.exists(), (artifact, output)
        assert any(artifact.resolve().is_relative_to(root) for root in allowed_roots)

    assert _git_status(worktree) == status_before
    assert {path: _filesystem_snapshot(path) for path in _host_paths()} == host_before
