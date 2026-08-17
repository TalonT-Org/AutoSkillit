"""Interprocess serialization and exact-input Codex validation contracts."""

from __future__ import annotations

import json
import multiprocessing
import os
import signal
import sys
import tomllib
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from autoskillit.core import PreLaunchReadiness

pytestmark = [pytest.mark.layer("execution"), pytest.mark.medium]

_VALID_CONFIG_BYTES = (
    b'[mcp_servers.autoskillit]\ncommand = "autoskillit"\nargs = []\nenv_vars = []\n'
)
_VALID_INVENTORY_BYTES = json.dumps(
    {
        "servers": [
            {
                "name": "autoskillit",
                "enabled": True,
                "transport": {
                    "type": "stdio",
                    "command": "autoskillit",
                    "args": [],
                    "env_vars": [],
                },
            }
        ]
    }
).encode()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("args", "--flag"),
        ("args", [1]),
        ("env_vars", {"TOKEN": "secret"}),
        ("env_vars", [None]),
    ],
)
def test_mcp_inventory_rejects_non_string_array_fields(
    field: str,
    value: object,
) -> None:
    from autoskillit.execution.backends.codex import _validate_codex_mcp_inventory

    transport: dict[str, object] = {
        "type": "stdio",
        "command": "autoskillit",
        "args": [],
        "env_vars": [],
    }
    transport[field] = value
    stdout = json.dumps({"servers": [{"name": "autoskillit", "transport": transport}]}).encode()

    errors = _validate_codex_mcp_inventory(stdout, _VALID_CONFIG_BYTES)

    assert any(f"{field} are not an array of strings" in error for error in errors)


def test_bounded_codex_probe_captures_success(tmp_path: Path) -> None:
    from autoskillit.execution.backends.codex import _run_bounded_codex_probe

    result = _run_bounded_codex_probe(
        (sys.executable, "-c", "import os; os.write(1, b'probe-ok')"),
        env=os.environ,
        cwd=str(tmp_path),
    )

    assert result.returncode == 0
    assert result.stdout == b"probe-ok"
    assert result.stderr == b""
    assert result.failure is None


def test_bounded_codex_probe_times_out_and_reaps(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from autoskillit.execution.backends import codex

    monkeypatch.setattr(codex, "_CODEX_PROBE_TIMEOUT_SECONDS", 0.05)

    result = codex._run_bounded_codex_probe(
        (sys.executable, "-c", "import time; time.sleep(60)"),
        env=os.environ,
        cwd=str(tmp_path),
    )

    assert result.returncode is None
    assert result.failure == "timed out"


def test_bounded_codex_probe_owns_and_kills_process_group(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from autoskillit.execution.backends import codex

    original_popen = codex.subprocess.Popen
    original_killpg = codex.os.killpg
    processes: list[codex.subprocess.Popen[bytes]] = []
    group_signals: list[tuple[int, signal.Signals]] = []

    def recording_popen(*args: object, **kwargs: Any) -> codex.subprocess.Popen[bytes]:
        assert kwargs["start_new_session"] is True
        process = original_popen(*args, **kwargs)
        processes.append(process)
        return process

    def recording_killpg(pgid: int, sig: signal.Signals) -> None:
        group_signals.append((pgid, sig))
        original_killpg(pgid, sig)

    monkeypatch.setattr(codex, "_CODEX_PROBE_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr(codex.subprocess, "Popen", recording_popen)
    monkeypatch.setattr(codex.os, "killpg", recording_killpg)

    result = codex._run_bounded_codex_probe(
        (sys.executable, "-c", "import time; time.sleep(60)"),
        env=os.environ,
        cwd=str(tmp_path),
    )

    assert result.failure == "timed out"
    assert group_signals
    assert {pgid for pgid, _sig in group_signals} == {processes[0].pid}
    assert group_signals[0][1] is signal.SIGTERM
    assert processes[0].poll() is not None


def test_bounded_codex_probe_enforces_stream_limit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from autoskillit.execution.backends import codex

    monkeypatch.setattr(codex, "_CODEX_PROBE_STREAM_LIMIT", 128)

    result = codex._run_bounded_codex_probe(
        (sys.executable, "-c", "import os; os.write(1, b'x' * 4096)"),
        env=os.environ,
        cwd=str(tmp_path),
    )

    assert result.returncode is None
    assert result.failure == "stdout exceeded 128 bytes"
    assert len(result.stdout) == 128


def test_run_bounded_codex_probe_returns_success_with_diagnostic_on_incomplete_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from autoskillit.core import ProcessCleanupResult
    from autoskillit.execution.backends import codex
    from autoskillit.execution.process._process_kill import OwnedProcessGroup

    original_cleanup = OwnedProcessGroup.cleanup

    def incomplete_cleanup(
        self: OwnedProcessGroup, timeout: float = 2.0
    ) -> tuple[int | None, ProcessCleanupResult]:
        returncode, result = original_cleanup(self, timeout)
        return returncode, ProcessCleanupResult(
            root_pid=result.root_pid,
            process_identities=result.process_identities,
            terminated_pids=result.terminated_pids,
            survivor_pids=result.survivor_pids,
            access_denied_pids=(999,),
            observation_complete=result.observation_complete,
        )

    monkeypatch.setattr(OwnedProcessGroup, "cleanup", incomplete_cleanup)

    result = codex._run_bounded_codex_probe(
        (sys.executable, "-c", "import os; os.write(1, b'probe-ok')"),
        env=os.environ,
        cwd=str(tmp_path),
    )

    assert result.failure is None
    assert result.cleanup_incomplete is True
    assert result.returncode == 0
    assert result.stdout == b"probe-ok"
    assert result.stderr == b""


@pytest.mark.parametrize(
    ("program", "expected_error"),
    [
        ("raise SystemExit(7)", "exited with status 7"),
        ("import os; os.write(1, b'not-json')", "returned malformed JSON"),
    ],
)
def test_mcp_probe_normalizes_process_and_output_failures(
    program: str,
    expected_error: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from autoskillit.execution.backends import codex

    monkeypatch.setattr(codex, "_CODEX_VALIDATION_CACHE", {})

    errors = codex._validate_mcp_probe(
        (sys.executable, "-c", program),
        env=os.environ,
        cwd=str(tmp_path),
        config_bytes=_VALID_CONFIG_BYTES,
    )

    assert len(errors) == 1
    assert expected_error in errors[0]


def test_mcp_probe_caches_successful_validation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from autoskillit.execution.backends import codex

    calls = 0

    def run_probe(*_args: object, **_kwargs: object) -> codex._BoundedProbeResult:
        nonlocal calls
        calls += 1
        return codex._BoundedProbeResult(
            returncode=0,
            stdout=_VALID_INVENTORY_BYTES,
            stderr=b"",
        )

    monkeypatch.setattr(codex, "_CODEX_VALIDATION_CACHE", {})
    monkeypatch.setattr(codex, "_run_bounded_codex_probe", run_probe)
    command = ("codex", "mcp", "list", "--json")

    assert (
        codex._validate_mcp_probe(
            command,
            env=os.environ,
            cwd=str(tmp_path),
            config_bytes=_VALID_CONFIG_BYTES,
        )
        == []
    )
    assert (
        codex._validate_mcp_probe(
            command,
            env=os.environ,
            cwd=str(tmp_path),
            config_bytes=_VALID_CONFIG_BYTES,
        )
        == []
    )
    assert calls == 1


def test_validate_mcp_probe_returns_clean_result_when_cleanup_incomplete_but_probe_succeeded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from autoskillit.execution.backends import codex

    def run_probe(*_args: object, **_kwargs: object) -> codex._BoundedProbeResult:
        return codex._BoundedProbeResult(
            returncode=0,
            stdout=_VALID_INVENTORY_BYTES,
            stderr=b"",
            cleanup_incomplete=True,
        )

    monkeypatch.setattr(codex, "_CODEX_VALIDATION_CACHE", {})
    monkeypatch.setattr(codex, "_run_bounded_codex_probe", run_probe)

    errors = codex._validate_mcp_probe(
        ("codex", "mcp", "list", "--json"),
        env=os.environ,
        cwd=str(tmp_path),
        config_bytes=_VALID_CONFIG_BYTES,
    )

    assert errors == []


def test_real_interactive_validator_reaches_successful_native_probe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from autoskillit.execution.backends import codex

    generated_home = tmp_path / "generated-home"
    generated_home.mkdir()
    (generated_home / "config.toml").write_bytes(_VALID_CONFIG_BYTES)
    for name in ("sessions", "archived_sessions"):
        target = generated_home / f".inert-{name}"
        target.mkdir()
        (generated_home / name).symlink_to(target)
    source_home = tmp_path / "source-home"
    source_home.mkdir()
    backend = codex.CodexBackend(source_codex_home=source_home)
    spec = replace(
        backend.build_interactive_cmd(generated_home=generated_home),
        cwd=str(tmp_path),
    )
    commands: list[tuple[str, ...]] = []

    def run_probe(
        command: tuple[str, ...],
        **_kwargs: object,
    ) -> codex._BoundedProbeResult:
        commands.append(command)
        return codex._BoundedProbeResult(
            returncode=0,
            stdout=_VALID_INVENTORY_BYTES,
            stderr=b"",
        )

    monkeypatch.setattr(codex, "_CODEX_VALIDATION_CACHE", {})
    monkeypatch.setattr(codex, "_run_bounded_codex_probe", run_probe)

    assert backend.validate_interactive_invocation(spec) == []
    assert len(commands) == 1
    assert commands[0][-3:] == ("mcp", "list", codex.CodexFlags.JSON)


def _config_writer(
    operation: str,
    config_path: str,
    isolated_home: str,
    ready: Any,
    start: Any,
    result: Any,
) -> None:
    os.environ["HOME"] = isolated_home
    os.environ["XDG_DATA_HOME"] = str(Path(isolated_home) / "xdg")
    ready.put(operation)
    if not start.wait(timeout=10):
        result.put((operation, "start timeout"))
        return
    try:
        if operation == "mcp":
            from autoskillit.execution.backends import ensure_codex_mcp_registered

            ensure_codex_mcp_registered(config_path=Path(config_path))
        else:
            from autoskillit.cli._hooks_codex import sync_hooks_to_codex_config

            sync_hooks_to_codex_config(
                config_path=Path(config_path),
                hook_config_format="toml_nested",
            )
    except BaseException as exc:
        result.put((operation, f"{type(exc).__name__}: {exc}"))
    else:
        result.put((operation, "ok"))


def _stop_and_reap(processes: list[multiprocessing.Process]) -> None:
    for process in processes:
        process.join(timeout=10)
    for process in processes:
        if process.is_alive():
            process.terminate()
            process.join(timeout=5)
        if process.is_alive():
            process.kill()
            process.join(timeout=5)


def test_cook_and_init_config_writers_preserve_the_union_under_one_canonical_lock(
    tmp_path: Path,
) -> None:
    race_root = tmp_path / "config-race"
    config_path = race_root / "codex-home" / "config.toml"
    config_path.parent.mkdir(parents=True)
    config_path.write_text('[foreign]\nowner = "user"\n', encoding="utf-8")
    noncanonical_path = config_path.parent / ".." / "codex-home" / "config.toml"
    child_home = race_root / "child-home"
    child_home.mkdir()
    ctx = multiprocessing.get_context("spawn")
    ready = ctx.Queue()
    result = ctx.Queue()
    start = ctx.Event()
    processes = [
        ctx.Process(
            target=_config_writer,
            args=("mcp", str(config_path), str(child_home), ready, start, result),
        ),
        ctx.Process(
            target=_config_writer,
            args=("hooks", str(noncanonical_path), str(child_home), ready, start, result),
        ),
    ]

    try:
        for process in processes:
            process.start()
        assert {ready.get(timeout=10), ready.get(timeout=10)} == {"mcp", "hooks"}
        start.set()
        outcomes = {result.get(timeout=15), result.get(timeout=15)}
        assert outcomes == {("mcp", "ok"), ("hooks", "ok")}
    finally:
        start.set()
        _stop_and_reap(processes)
        ready.close()
        result.close()
        ready.join_thread()
        result.join_thread()

    assert all(not process.is_alive() for process in processes)
    data = tomllib.loads(config_path.read_text(encoding="utf-8"))
    assert data["foreign"] == {"owner": "user"}
    assert "autoskillit" in data["mcp_servers"]
    assert data["hooks"]
    assert not (child_home / ".codex" / "config.toml").exists()


def test_generated_home_snapshot_is_the_exact_post_mcp_and_hook_transaction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from autoskillit.execution.backends.codex import CodexBackend

    source_home = tmp_path / "source-home"
    source_home.mkdir()
    source_config = source_home / "config.toml"
    source_config.write_text('[foreign]\nowner = "user"\n', encoding="utf-8")
    generated_home = tmp_path / "generated-home"
    generated_home.mkdir()
    ambient_home = tmp_path / "ambient-home"
    ambient_home.mkdir()
    backend = CodexBackend(source_codex_home=source_home)

    monkeypatch.setenv("CODEX_HOME", str(ambient_home))
    monkeypatch.setattr(Path, "home", staticmethod(lambda: ambient_home))

    assert backend.ensure_pre_launch(session_dir=generated_home) == PreLaunchReadiness((), {})

    source_bytes = source_config.read_bytes()
    assert (generated_home / "config.toml").read_bytes() == source_bytes
    data = tomllib.loads(source_bytes.decode("utf-8"))
    assert data["foreign"] == {"owner": "user"}
    assert "autoskillit" in data["mcp_servers"]
    assert data["hooks"]
    assert not (ambient_home / "config.toml").exists()


def test_interactive_cmd_rejects_environment_changed_after_binding(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from autoskillit.core import resolve_executable_launch_binding
    from autoskillit.execution.backends.codex import CodexBackend

    source_home = tmp_path / "source-home"
    source_home.mkdir()
    executable = tmp_path / "codex"
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    executable.chmod(0o755)
    backend = CodexBackend(source_codex_home=source_home)
    extras = {"PATH": str(tmp_path)}
    candidate = backend.build_interactive_cmd(env_extras=extras)
    binding = resolve_executable_launch_binding(
        binary_name="codex",
        environment=candidate.env,
        cwd=tmp_path,
    )

    monkeypatch.setenv("AUTOSKILLIT_CODEX_GUARD_MUTATION", "changed")

    with pytest.raises(
        ValueError,
        match="interactive environment changed after executable binding",
    ):
        backend.build_interactive_cmd(executable=binding, env_extras=extras)


def test_config_lock_is_non_reentrant_for_the_same_canonical_path(tmp_path: Path) -> None:
    from autoskillit.execution.backends._codex_config_lock import CodexConfigLock

    config_path = tmp_path / "codex-home" / "config.toml"
    alias = config_path.parent / ".." / "codex-home" / "config.toml"

    with CodexConfigLock(config_path):
        with pytest.raises(RuntimeError, match="non-reentrant|already owns"):
            with CodexConfigLock(alias):
                pytest.fail("same-process nested acquisition must fail before entry")


def test_config_lock_rejects_symlink_sidecar_without_truncating_target(
    tmp_path: Path,
) -> None:
    from autoskillit.execution.backends._codex_config_lock import CodexConfigLock

    codex_home = tmp_path / "codex-home"
    codex_home.mkdir()
    config_path = codex_home / "config.toml"
    victim = tmp_path / "victim.txt"
    victim.write_text("preserve me", encoding="utf-8")
    lock_path = codex_home / ".config.toml.autoskillit.lock"
    lock_path.symlink_to(victim)

    with pytest.raises(OSError):
        CodexConfigLock(config_path).acquire()

    assert victim.read_text(encoding="utf-8") == "preserve me"
