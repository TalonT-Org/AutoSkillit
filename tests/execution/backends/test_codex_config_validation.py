"""Interprocess serialization and exact-input Codex validation contracts."""

from __future__ import annotations

import json
import multiprocessing
import os
import tomllib
from pathlib import Path
from typing import Any

import pytest

pytestmark = [pytest.mark.layer("execution"), pytest.mark.medium]


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

    config_bytes = (
        b'[mcp_servers.autoskillit]\ncommand = "autoskillit"\nargs = []\nenv_vars = []\n'
    )
    transport: dict[str, object] = {
        "type": "stdio",
        "command": "autoskillit",
        "args": [],
        "env_vars": [],
    }
    transport[field] = value
    stdout = json.dumps({"servers": [{"name": "autoskillit", "transport": transport}]}).encode()

    errors = _validate_codex_mcp_inventory(stdout, config_bytes)

    assert any(f"{field} are not an array of strings" in error for error in errors)


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

    assert backend.ensure_pre_launch(session_dir=generated_home) == []

    source_bytes = source_config.read_bytes()
    assert (generated_home / "config.toml").read_bytes() == source_bytes
    data = tomllib.loads(source_bytes.decode("utf-8"))
    assert data["foreign"] == {"owner": "user"}
    assert "autoskillit" in data["mcp_servers"]
    assert data["hooks"]
    assert not (ambient_home / "config.toml").exists()


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
