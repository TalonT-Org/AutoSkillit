"""Codex startup / validation probes — bounded subprocess + inventory + cache.

Owns the bounded Codex probe runner (`_run_bounded_codex_probe`),
the MCP-inventory validator, the tool-flag validator, and the
shared validation cache. Every helper is either stateless or
guarded by the shared module-level lock; the cache and the lock
are the module-level state.
"""

from __future__ import annotations

import hashlib
import json
import os
import selectors
import subprocess
import threading
import time
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from autoskillit.core import CODEX_COOK_RESERVED_ENV_VARS, get_logger
from autoskillit.execution.backends._codex_cmd_builders import CodexFlags
from autoskillit.execution.backends._codex_config import _format_toml_value

logger = get_logger(__name__)


_CODEX_PROBE_TIMEOUT_SECONDS = 15.0
_CODEX_PROBE_STREAM_LIMIT = 64 * 1024
_CODEX_VALIDATION_CACHE_LIMIT = 128
_CODEX_VALIDATION_CACHE: dict[str, None] = {}
_CODEX_VALIDATION_CACHE_GUARD = threading.Lock()


@dataclass(frozen=True, slots=True)
class _BoundedProbeResult:
    returncode: int | None
    stdout: bytes
    stderr: bytes
    failure: str | None = None
    cleanup_incomplete: bool = False


def _terminate_probe(owner: object) -> None:
    from autoskillit.execution.process._process_kill import OwnedProcessGroup

    if not isinstance(owner, OwnedProcessGroup):
        raise TypeError("Codex probe cleanup requires its spawn-bound owner")
    try:
        owner.settle_evidence(timeout=2)
    finally:
        for stream in (owner.process.stdout, owner.process.stderr):
            if stream is not None:
                stream.close()


def _run_bounded_codex_probe(
    command: tuple[str, ...],
    *,
    env: Mapping[str, str],
    cwd: str,
) -> _BoundedProbeResult:
    try:
        from autoskillit.execution.process._process_kill import spawn_owned_process
        from autoskillit.execution.process._process_tether import TetherSpec

        owner = spawn_owned_process(
            command,
            cwd=cwd,
            env=dict(env),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
            tether=TetherSpec(origin="codex_probe", ceiling_seconds=3600.0),
        )
        process = owner.process
    except OSError as exc:
        return _BoundedProbeResult(
            returncode=None,
            stdout=b"",
            stderr=b"",
            failure=f"binary unavailable ({type(exc).__name__})",
        )

    selector: selectors.BaseSelector | None = None
    output = {"stdout": bytearray(), "stderr": bytearray()}
    deadline = time.monotonic() + _CODEX_PROBE_TIMEOUT_SECONDS
    try:
        assert process.stdout is not None
        assert process.stderr is not None
        selector_factory = selectors.DefaultSelector
        selector = selector_factory()
        selector.register(process.stdout, selectors.EVENT_READ, "stdout")
        selector.register(process.stderr, selectors.EVENT_READ, "stderr")
        while selector.get_map() or owner.observe_exit() is None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                _terminate_probe(owner)
                return _BoundedProbeResult(
                    returncode=None,
                    stdout=bytes(output["stdout"]),
                    stderr=bytes(output["stderr"]),
                    failure="timed out",
                )
            if not selector.get_map():
                time.sleep(min(0.01, remaining))
                continue
            events = selector.select(timeout=min(0.1, remaining))
            for key, _ in events:
                stream_name = key.data
                try:
                    file_descriptor = (
                        key.fileobj if isinstance(key.fileobj, int) else key.fileobj.fileno()
                    )
                    chunk = os.read(file_descriptor, 8192)
                except OSError:
                    chunk = b""
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                target = output[stream_name]
                target.extend(chunk)
                if len(target) > _CODEX_PROBE_STREAM_LIMIT:
                    del target[_CODEX_PROBE_STREAM_LIMIT:]
                    _terminate_probe(owner)
                    return _BoundedProbeResult(
                        returncode=None,
                        stdout=bytes(output["stdout"]),
                        stderr=bytes(output["stderr"]),
                        failure=f"{stream_name} exceeded {_CODEX_PROBE_STREAM_LIMIT} bytes",
                    )
        returncode, cleanup_result = owner.settle_evidence(
            timeout=max(0.0, deadline - time.monotonic())
        )
        returncode = returncode if returncode is not None else -1
    except subprocess.TimeoutExpired:
        _terminate_probe(owner)
        return _BoundedProbeResult(
            returncode=None,
            stdout=bytes(output["stdout"]),
            stderr=bytes(output["stderr"]),
            failure="timed out while reaping",
        )
    except BaseException as exc:
        # settle_evidence() never raises, so any BaseException here is unrelated to cleanup.
        if process.returncode is None:
            try:
                _terminate_probe(owner)
            except BaseException as cleanup_exc:
                logger.error("codex_probe_cleanup_failed", exc_info=True)
                exc.add_note(f"Codex probe cleanup failed: {type(cleanup_exc).__name__}")
        raise
    finally:
        if selector is not None:
            selector.close()
        for stream in (process.stdout, process.stderr):
            if stream is not None:
                stream.close()
    return _BoundedProbeResult(
        returncode=returncode,
        stdout=bytes(output["stdout"]),
        stderr=bytes(output["stderr"]),
        cleanup_incomplete=not cleanup_result.complete,
    )


def _probe_diagnostic(result: _BoundedProbeResult) -> str:
    """Return bounded, non-content diagnostics safe for configs containing secrets."""
    stdout_digest = hashlib.sha256(result.stdout).hexdigest()[:16]
    stderr_digest = hashlib.sha256(result.stderr).hexdigest()[:16]
    return (
        f"stdout_bytes={len(result.stdout)} stdout_sha256={stdout_digest} "
        f"stderr_bytes={len(result.stderr)} stderr_sha256={stderr_digest}"
    )


def _mcp_inventory_entries(document: Any) -> list[dict[str, Any]] | None:
    if isinstance(document, list):
        return [entry for entry in document if isinstance(entry, dict)]
    if not isinstance(document, dict):
        return None
    for key in ("servers", "mcp_servers"):
        value = document.get(key)
        if isinstance(value, list):
            return [entry for entry in value if isinstance(entry, dict)]
        if isinstance(value, dict):
            return [
                {"name": name, **entry}
                for name, entry in value.items()
                if isinstance(name, str) and isinstance(entry, dict)
            ]
    return None


def _string_array(value: Any) -> list[str] | None:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        return None
    return value


def _validate_codex_mcp_inventory(stdout: bytes, config_bytes: bytes) -> list[str]:
    try:
        document = json.loads(stdout.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return ["Codex MCP validation returned malformed JSON"]
    try:
        config = tomllib.loads(config_bytes.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError):
        return ["Final Codex config bytes are not valid UTF-8 TOML"]

    expected = config.get("mcp_servers", {}).get("autoskillit")
    if not isinstance(expected, dict):
        return ["Final Codex config is missing mcp_servers.autoskillit"]
    entries = _mcp_inventory_entries(document)
    if entries is None:
        return ["Codex MCP validation JSON has no server inventory"]
    matches = [entry for entry in entries if entry.get("name") == "autoskillit"]
    if len(matches) != 1:
        return [
            "Codex MCP validation expected exactly one enabled autoskillit server; "
            f"found {len(matches)}"
        ]
    actual = matches[0]
    if actual.get("enabled") is False:
        return ["Codex MCP validation reports autoskillit as disabled"]
    transport = actual.get("transport")
    if not isinstance(transport, dict):
        transport = actual
    errors: list[str] = []
    if transport.get("type", "stdio") != "stdio":
        errors.append("Codex MCP autoskillit transport is not stdio")
    if transport.get("command") != expected.get("command"):
        errors.append("Codex MCP autoskillit command does not match final config")
    expected_args = _string_array(expected.get("args", []))
    actual_args = _string_array(transport.get("args", []))
    if expected_args is None:
        errors.append("Final Codex config autoskillit args are not an array of strings")
    if actual_args is None:
        errors.append("Codex MCP autoskillit args are not an array of strings")
    elif expected_args is not None and actual_args != expected_args:
        errors.append("Codex MCP autoskillit args do not match final config")
    expected_env_vars = _string_array(expected.get("env_vars", []))
    actual_env_vars = _string_array(transport.get("env_vars", []))
    if expected_env_vars is None:
        errors.append("Final Codex config autoskillit env_vars are not an array of strings")
    if actual_env_vars is None:
        errors.append("Codex MCP autoskillit env_vars are not an array of strings")
    elif expected_env_vars is not None and set(actual_env_vars) != set(expected_env_vars):
        errors.append("Codex MCP autoskillit env_vars do not match final config")
    for key in ("startup_timeout_sec", "tool_timeout_sec"):
        if key in expected and actual.get(key) != expected[key]:
            errors.append(f"Codex MCP autoskillit {key} does not match final config")
    return errors


def _validation_digest(
    command: tuple[str, ...],
    *,
    env: Mapping[str, str],
    cwd: str,
    config_bytes: bytes,
) -> str:
    digest = hashlib.sha256()
    for value in command:
        digest.update(value.encode("utf-8"))
        digest.update(b"\0")
    for key, value in sorted(env.items()):
        digest.update(key.encode("utf-8"))
        digest.update(b"=")
        digest.update(value.encode("utf-8"))
        digest.update(b"\0")
    digest.update(cwd.encode("utf-8"))
    digest.update(b"\0")
    digest.update(config_bytes)
    return digest.hexdigest()


def _is_cached_validation(digest: str) -> bool:
    with _CODEX_VALIDATION_CACHE_GUARD:
        if digest not in _CODEX_VALIDATION_CACHE:
            return False
        _CODEX_VALIDATION_CACHE[digest] = _CODEX_VALIDATION_CACHE.pop(digest)
        return True


def _cache_validation(digest: str) -> None:
    with _CODEX_VALIDATION_CACHE_GUARD:
        _CODEX_VALIDATION_CACHE.pop(digest, None)
        _CODEX_VALIDATION_CACHE[digest] = None
        while len(_CODEX_VALIDATION_CACHE) > _CODEX_VALIDATION_CACHE_LIMIT:
            del _CODEX_VALIDATION_CACHE[next(iter(_CODEX_VALIDATION_CACHE))]


def _validate_mcp_probe(
    command: tuple[str, ...],
    *,
    env: Mapping[str, str],
    cwd: str,
    config_bytes: bytes,
) -> list[str]:
    digest = _validation_digest(command, env=env, cwd=cwd, config_bytes=config_bytes)
    if _is_cached_validation(digest):
        return []
    result = _run_bounded_codex_probe(command, env=env, cwd=cwd)
    if result.failure is not None:
        return [f"Codex MCP validation {result.failure}; {_probe_diagnostic(result)}"]
    if result.cleanup_incomplete:
        logger.warning("codex_probe_cleanup_incomplete", returncode=result.returncode)
    if result.returncode != 0:
        return [
            f"Codex MCP validation exited with status {result.returncode}; "
            f"{_probe_diagnostic(result)}"
        ]
    errors = _validate_codex_mcp_inventory(result.stdout, config_bytes)
    if errors:
        diagnostic = _probe_diagnostic(result)
        return [f"{error}; {diagnostic}" for error in errors]
    _cache_validation(digest)
    return []


def _validate_global_codex_home(
    source_codex_home: Path,
    *,
    config_path: Path,
) -> list[str]:
    try:
        config_bytes = config_path.read_bytes()
    except OSError as exc:
        return [f"Failed to read final Codex config: {type(exc).__name__}: {exc}"]
    sqlite_override = f"sqlite_home={_format_toml_value(str(source_codex_home))}"
    command = (
        "codex",
        CodexFlags.CONFIG_OVERRIDE,
        sqlite_override,
        "mcp",
        "list",
        CodexFlags.JSON,
    )
    env = dict(os.environ)
    for key in CODEX_COOK_RESERVED_ENV_VARS:
        env[key] = str(source_codex_home)
    return _validate_mcp_probe(
        command,
        env=env,
        cwd=str(source_codex_home),
        config_bytes=config_bytes,
    )


def _validate_inert_rollout_paths(
    generated_home: Path,
) -> tuple[list[str], tuple[tuple[str, str, int, int], ...]]:
    errors: list[str] = []
    fingerprint: list[tuple[str, str, int, int]] = []
    for name in ("sessions", "archived_sessions"):
        public_path = generated_home / name
        if not public_path.is_symlink():
            errors.append(f"{public_path} must be an inert pre-view symlink")
            continue
        try:
            target = public_path.resolve(strict=True)
            stat = target.stat()
        except OSError as exc:
            errors.append(f"{public_path} has an invalid target: {type(exc).__name__}: {exc}")
            continue
        if not target.is_relative_to(generated_home):
            errors.append(f"{public_path} escapes the generated home")
            continue
        if not target.is_dir():
            errors.append(f"{public_path} target is not a directory")
            continue
        try:
            entries = list(target.iterdir())
        except OSError as exc:
            errors.append(f"{public_path} target is unreadable: {type(exc).__name__}: {exc}")
            continue
        if entries:
            errors.append(f"{public_path} inert target is not empty")
        fingerprint.append((name, os.readlink(public_path), stat.st_dev, stat.st_ino))
    return errors, tuple(fingerprint)
