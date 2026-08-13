"""Real Claude/Codex registration-path lifecycle checks for MCP stdio EOF."""

from __future__ import annotations

import contextlib
import importlib
import json
import os
import select
import shutil
import struct
import subprocess
import sys
import threading
import time
from pathlib import Path

import psutil
import pytest

from autoskillit.cli._init_helpers import _register_mcp_server
from autoskillit.execution.backends import ensure_codex_mcp_registered

fcntl = importlib.import_module("fcntl") if sys.platform == "linux" else None
pty = importlib.import_module("pty") if sys.platform == "linux" else None
termios = importlib.import_module("termios") if sys.platform == "linux" else None

pytestmark = [
    pytest.mark.layer("cli"),
    pytest.mark.large,
    pytest.mark.skipif(sys.platform != "linux", reason="distinct process groups require Linux"),
]


def _wait_for_registered_daemon(launch_id: str, timeout: float) -> psutil.Process | None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for process in psutil.process_iter(["pid"]):
            try:
                if process.environ().get("AUTOSKILLIT_LAUNCH_ID") == launch_id:
                    command = process.cmdline()
                    if any(Path(arg).name == "autoskillit" for arg in command) or any(
                        command[index : index + 2] == ["-m", "autoskillit"]
                        for index in range(len(command) - 1)
                    ):
                        return process
            except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess):
                continue
        time.sleep(0.05)
    return None


def _matching_launch_processes(launch_id: str) -> list[psutil.Process]:
    matches: list[psutil.Process] = []
    for process in psutil.process_iter(["pid"]):
        try:
            if process.environ().get("AUTOSKILLIT_LAUNCH_ID") == launch_id:
                matches.append(process)
        except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess):
            continue
    return matches


def _wait_dead(process: psutil.Process, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if not process.is_running() or process.status() == psutil.STATUS_ZOMBIE:
                return True
        except psutil.NoSuchProcess:
            return True
        time.sleep(0.05)
    return False


def _drain_pty(master_fd: int, stop: threading.Event, diagnostics: bytearray) -> None:
    responded: set[str] = set()
    while not stop.is_set():
        try:
            readable, _, _ = select.select([master_fd], [], [], 0.05)
            if not readable:
                continue
            chunk = os.read(master_fd, 4096)
            if not chunk:
                return
            diagnostics.extend(chunk)
            del diagnostics[:-8192]
            if b"\x1b[6n" in chunk:
                os.write(master_fd, b"\x1b[1;1R")
            if b"\x1b]10;?\x1b\\" in chunk:
                os.write(master_fd, b"\x1b]10;rgb:ffff/ffff/ffff\x1b\\")
            if b"\x1b]11;?\x1b\\" in chunk:
                os.write(master_fd, b"\x1b]11;rgb:0000/0000/0000\x1b\\")
            if b"Choose" in chunk and b"text" in chunk and "theme" not in responded:
                responded.add("theme")
                os.write(master_fd, b"\r")
            if (
                b"trust" in chunk
                and (b"folder" in chunk or b"directory" in chunk)
                and "trust" not in responded
            ):
                responded.add("trust")
                os.write(master_fd, b"\r")
            if b"Hooks need review" in chunk and "hooks" not in responded:
                responded.add("hooks")
                os.write(master_fd, b"\x1b[B\x1b[B\r")
        except OSError:
            return


def _terminate(process: psutil.Process | None) -> None:
    if process is None:
        return
    with contextlib.suppress(psutil.NoSuchProcess, psutil.AccessDenied, psutil.TimeoutExpired):
        process.terminate()
        process.wait(timeout=2)
        return
    with contextlib.suppress(psutil.NoSuchProcess, psutil.AccessDenied, psutil.TimeoutExpired):
        process.kill()
        process.wait(timeout=2)


def test_terminate_kills_process_after_terminate_timeout() -> None:
    class StubbornProcess:
        pid = 123

        def __init__(self) -> None:
            self.terminated = False
            self.killed = False

        def terminate(self) -> None:
            self.terminated = True

        def kill(self) -> None:
            self.killed = True

        def wait(self, timeout: float) -> int:
            if not self.killed:
                raise psutil.TimeoutExpired(timeout, pid=self.pid)
            return 0

    process = StubbornProcess()

    _terminate(process)  # type: ignore[arg-type]

    assert process.terminated is True
    assert process.killed is True


@pytest.mark.parametrize("backend_name", ["claude", "codex"])
def test_real_backend_client_death_closes_registered_mcp_stdio(
    backend_name: str, tmp_path: Path
) -> None:
    """A real client exit closes its registered MCP pipes across a separate PGID."""
    import pwd

    assert fcntl is not None and pty is not None and termios is not None

    binary = shutil.which(backend_name)
    if binary is None:
        pytest.skip(f"{backend_name} client is not installed")

    project = tmp_path / "project"
    project.mkdir()
    state_root = project
    launch_id = f"{os.getpid():016x}"[-16:]
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    wrapper = bin_dir / "autoskillit"
    wrapper.write_text(
        "#!/bin/sh\nexec setsid " + str(Path(sys.executable)) + " -m autoskillit\n",
        encoding="utf-8",
    )
    wrapper.chmod(0o755)

    client_home = tmp_path / "home"
    client_home.mkdir()
    source_home = Path(pwd.getpwuid(os.getuid()).pw_dir)
    source_claude_state = source_home / ".claude.json"
    if source_claude_state.is_file():
        shutil.copy2(source_claude_state, client_home / ".claude.json")
    isolated_claude = client_home / ".claude"
    isolated_claude.mkdir()
    (isolated_claude / "settings.json").write_text(
        json.dumps({"theme": "dark", "skipDangerousModePermissionPrompt": True}),
        encoding="utf-8",
    )
    source_credentials = source_home / ".claude" / ".credentials.json"
    if source_credentials.is_file():
        shutil.copy2(source_credentials, isolated_claude / ".credentials.json")
    env = {
        **os.environ,
        "HOME": str(client_home),
        "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
        "AUTOSKILLIT_LAUNCH_ID": launch_id,
        "AUTOSKILLIT_STATE_ROOT": str(state_root),
        "CLAUDE_CONFIG_DIR": str(isolated_claude),
        "NO_COLOR": "1",
        "TERM": "xterm-256color",
    }
    if backend_name == "claude":
        env.pop("CLAUDE_CONFIG_DIR", None)
        config_path = tmp_path / "claude-mcp.json"
        _register_mcp_server(config_path)
        command = [
            binary,
            "--mcp-config",
            str(config_path),
            "--strict-mcp-config",
            "--dangerously-skip-permissions",
        ]
    else:
        codex_home = client_home / ".codex"
        config_path = codex_home / "config.toml"
        ensure_codex_mcp_registered(config_path=config_path)
        for name in ("auth.json", "installation_id"):
            source = source_home / ".codex" / name
            if source.exists():
                shutil.copy2(source, codex_home / name)
        env["CODEX_HOME"] = str(codex_home)
        mcp_args = [str(Path(sys.executable)), "-m", "autoskillit"]
        command = [
            binary,
            "--no-alt-screen",
            "--dangerously-bypass-approvals-and-sandbox",
            "-c",
            f"mcp_servers.autoskillit.command={json.dumps('setsid')}",
            "-c",
            f"mcp_servers.autoskillit.args={json.dumps(mcp_args)}",
            "-c",
            "mcp_servers.autoskillit.env_vars="
            + json.dumps(["AUTOSKILLIT_LAUNCH_ID", "AUTOSKILLIT_STATE_ROOT"]),
        ]

    master_fd, slave_fd = pty.openpty()
    fcntl.ioctl(slave_fd, termios.TIOCSWINSZ, struct.pack("HHHH", 24, 100, 0, 0))
    diagnostics = bytearray()
    stop_drain = threading.Event()
    drain = threading.Thread(
        target=_drain_pty,
        args=(master_fd, stop_drain, diagnostics),
        daemon=True,
    )
    client: subprocess.Popen[bytes] | None = None
    daemon: psutil.Process | None = None
    client_tree: list[psutil.Process] = []
    try:
        client = subprocess.Popen(
            command,
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            cwd=project,
            env=env,
            start_new_session=True,
        )
        os.close(slave_fd)
        slave_fd = -1
        drain.start()
        daemon = _wait_for_registered_daemon(launch_id, timeout=20)
        assert daemon is not None, diagnostics.decode(errors="replace")
        assert os.getpgid(daemon.pid) != os.getpgid(client.pid)
        with contextlib.suppress(psutil.NoSuchProcess):
            client_tree = psutil.Process(client.pid).children(recursive=True)

        client.terminate()
        try:
            client.wait(timeout=8)
        except subprocess.TimeoutExpired:
            client.kill()
            client.wait(timeout=3)
        os.close(master_fd)
        master_fd = -1

        assert _wait_dead(daemon, timeout=10), diagnostics.decode(errors="replace")
    finally:
        stop_drain.set()
        if slave_fd >= 0:
            with contextlib.suppress(OSError):
                os.close(slave_fd)
        if master_fd >= 0:
            with contextlib.suppress(OSError):
                os.close(master_fd)
        if client is not None and client.poll() is None:
            client.kill()
            with contextlib.suppress(subprocess.TimeoutExpired):
                client.wait(timeout=3)
        _terminate(daemon)
        for process in reversed(client_tree):
            _terminate(process)
        for process in _matching_launch_processes(launch_id):
            _terminate(process)
