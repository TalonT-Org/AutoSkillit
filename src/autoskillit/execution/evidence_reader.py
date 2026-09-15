"""Sterile one-shot Codex launcher for an authorized evidence-reader role."""

from __future__ import annotations

import hashlib
import json
import math
import os
import secrets
import selectors
import shutil
import stat
import subprocess
import tempfile
import time
import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from autoskillit.core import (
    EVIDENCE_READER_AUTHORITY_ENV_VAR,
    AgentDef,
    agent_definition_digest,
    atomic_write,
    canonical_reader_tools_to_bare,
)
from autoskillit.execution.backends._codex_catalog import project_codex_catalog
from autoskillit.execution.backends._codex_probes import _validate_codex_mcp_inventory
from autoskillit.execution.backends._probe_cache import (
    ProbeResult,
    read_probe_cache,
    write_probe_cache,
)
from autoskillit.execution.evidence.reader._contract import (
    _OUTPUT_SCHEMA_NAME as _OUTPUT_SCHEMA_NAME,
)
from autoskillit.execution.evidence.reader._contract import (
    _PROBE_SCHEMA_NAME as _PROBE_SCHEMA_NAME,
)
from autoskillit.execution.evidence.reader._contract import (
    _PROVIDER_ENV as _PROVIDER_ENV,
)
from autoskillit.execution.evidence.reader._contract import (
    _RESULT_KEYS as _RESULT_KEYS,
)
from autoskillit.execution.evidence.reader._contract import (
    EvidenceCitation as EvidenceCitation,
)
from autoskillit.execution.evidence.reader._contract import (
    EvidenceReaderAuthSelection as EvidenceReaderAuthSelection,
)
from autoskillit.execution.evidence.reader._contract import (
    EvidenceReaderConformanceEvidence as EvidenceReaderConformanceEvidence,
)
from autoskillit.execution.evidence.reader._contract import (
    EvidenceReaderInvocationLike as EvidenceReaderInvocationLike,
)
from autoskillit.execution.evidence.reader._contract import (
    EvidenceReaderLaunchError as EvidenceReaderLaunchError,
)
from autoskillit.execution.evidence.reader._contract import (
    EvidenceReaderLaunchResult as EvidenceReaderLaunchResult,
)
from autoskillit.execution.evidence.reader._contract import (
    EvidenceReaderResultStatus as EvidenceReaderResultStatus,
)
from autoskillit.execution.evidence.reader._contract import (
    _invocation_environment as _invocation_environment,
)
from autoskillit.execution.evidence.reader._contract import (
    _positive_mapping as _positive_mapping,
)
from autoskillit.execution.evidence.reader._contract import (
    _probe_output_schema as _probe_output_schema,
)
from autoskillit.execution.evidence.reader._contract import (
    _render_config as _render_config,
)
from autoskillit.execution.evidence.reader._contract import (
    _result_output_schema as _result_output_schema,
)
from autoskillit.execution.evidence.reader._contract import (
    _select_authentication as _select_authentication,
)
from autoskillit.execution.evidence.reader._contract import (
    _transport as _transport,
)
from autoskillit.execution.evidence.reader._contract import (
    evidence_reader_mcp_transport as evidence_reader_mcp_transport,
)
from autoskillit.execution.evidence.reader._contract import (
    evidence_reader_provider_environment as evidence_reader_provider_environment,
)
from autoskillit.execution.evidence.reader._protocol import (
    _prompt as _prompt,
)
from autoskillit.execution.evidence.reader._protocol import (
    _validate_stream as _validate_stream,
)
from autoskillit.execution.process._lifecycle.owned_group import (
    OwnedProcessGroup,
    spawn_owned_process,
)
from autoskillit.execution.process._process_tether import TetherSpec

_SUPPORTED_CODEX_CLI_VERSION = "codex-cli 0.147.0"
_STREAM_CHUNK = 64 * 1024
_CATALOG_LIMIT = 2_000_000
_STDERR_LIMIT = 64 * 1024
_CODEX_STDIN_NOTICE = b"Reading additional input from stdin...\n"
_MAX_STREAM_BYTES = 2_000_000
_MAX_RESULT_BYTES = 256_000
_MAX_PROMPT_BYTES = 64_000
_OBSERVATION_SCOPE_LABELS: tuple[str, ...] = (
    "generated_config",
    "configured_mcp_transport",
    "supported_cli_and_catalog_shape",
    "output_schema_behavioral_canary",
    "observed_runtime_calls",
    "not_exhaustive_native_tool_inventory",
)
_PROBE_CACHE_NAME = "codex-evidence-reader-probe-cache.json"
_PROBE_POLICY = "codex-evidence-reader-v1"


@dataclass(frozen=True, slots=True)
class _ProcessOutput:
    returncode: int
    stdout: bytes
    stderr: bytes


def _real_root(path: Path, label: str) -> Path:
    try:
        mode = path.lstat().st_mode
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise EvidenceReaderLaunchError(f"{label}_invalid") from exc
    if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
        raise EvidenceReaderLaunchError(f"{label}_invalid")
    return resolved


def _overlaps(first: Path, second: Path) -> bool:
    return first == second or first in second.parents or second in first.parents


def _isolated_directory(excluded: tuple[Path, ...], label: str) -> Path:
    stable_temp_root = Path("/var/tmp")
    directory = Path(
        tempfile.mkdtemp(
            prefix=f"autoskillit-reader-{label}-",
            dir=stable_temp_root if stable_temp_root.is_dir() else None,
        )
    )
    resolved = directory.resolve(strict=True)
    if any(_overlaps(resolved, root) for root in excluded):
        if all(resolved != root and resolved not in root.parents for root in excluded):
            shutil.rmtree(resolved)
        raise EvidenceReaderLaunchError("isolation_invalid")
    directory.chmod(0o700)
    return resolved


def _remove_directory(path: Path) -> None:
    try:
        mode = path.lstat().st_mode
        if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
            raise EvidenceReaderLaunchError("cleanup_incomplete")
        shutil.rmtree(path)
    except FileNotFoundError:
        return
    except OSError as exc:
        raise EvidenceReaderLaunchError("cleanup_incomplete") from exc
    if os.path.lexists(path):
        raise EvidenceReaderLaunchError("cleanup_incomplete")


def _require_empty_cwd(path: Path) -> None:
    try:
        with os.scandir(path) as entries:
            if next(entries, None) is not None:
                raise EvidenceReaderLaunchError("cwd_modified")
    except OSError as exc:
        raise EvidenceReaderLaunchError("cwd_observation_incomplete") from exc


def _write_private(path: Path, content: str | bytes) -> None:
    if isinstance(content, bytes):
        text = content.decode("ascii", errors="strict")
    else:
        text = content
    atomic_write(path, text)
    path.chmod(0o600)


def _deadline_remaining(deadline: float) -> float:
    if (
        not isinstance(deadline, (int, float))
        or isinstance(deadline, bool)
        or not math.isfinite(deadline)
    ):
        raise EvidenceReaderLaunchError("deadline_invalid")
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise EvidenceReaderLaunchError("deadline_exceeded")
    return remaining


def _drain_bounded_output(
    selector: selectors.BaseSelector,
    owner: OwnedProcessGroup,
    output: dict[str, bytearray],
    deadline: float,
    stdout_limit: int,
) -> None:
    while selector.get_map() or owner.observe_exit() is None:
        remaining = _deadline_remaining(deadline)
        if not selector.get_map():
            time.sleep(min(0.01, remaining))
            continue
        for key, _ in selector.select(min(0.1, remaining)):
            descriptor = key.fileobj if isinstance(key.fileobj, int) else key.fileobj.fileno()
            chunk = os.read(descriptor, _STREAM_CHUNK)
            if not chunk:
                selector.unregister(key.fileobj)
                continue
            limit = stdout_limit if key.data == "stdout" else _STDERR_LIMIT
            if len(output[key.data]) + len(chunk) > limit:
                raise EvidenceReaderLaunchError("stream_limit_exceeded")
            output[key.data].extend(chunk)


def _run_bounded(
    command: Sequence[str],
    *,
    cwd: Path,
    environment: Mapping[str, str],
    deadline: float,
    stdout_limit: int,
) -> _ProcessOutput:
    try:
        owner = spawn_owned_process(
            tuple(command),
            cwd=cwd,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
            tether=TetherSpec(origin="evidence_reader", ceiling_seconds=3600.0),
        )
    except OSError as exc:
        raise EvidenceReaderLaunchError("codex_unavailable") from exc
    output = {"stdout": bytearray(), "stderr": bytearray()}
    selector_factory = selectors.DefaultSelector
    selector = selector_factory()
    try:
        assert owner.process.stdout is not None and owner.process.stderr is not None
        selector.register(owner.process.stdout, selectors.EVENT_READ, "stdout")
        selector.register(owner.process.stderr, selectors.EVENT_READ, "stderr")
        _drain_bounded_output(selector, owner, output, deadline, stdout_limit)
        returncode, cleanup = owner.settle(timeout=min(2.0, _deadline_remaining(deadline)))
        if not cleanup.complete:
            raise EvidenceReaderLaunchError("process_cleanup_incomplete")
        return _ProcessOutput(returncode, bytes(output["stdout"]), bytes(output["stderr"]))
    except BaseException as exc:
        cleanup = owner.settle_preserving(
            exc, timeout=min(2.0, max(0.0, deadline - time.monotonic()))
        )
        if not cleanup.complete:
            raise EvidenceReaderLaunchError("process_cleanup_incomplete") from exc
        raise
    finally:
        selector.close()
        for stream in (owner.process.stdout, owner.process.stderr):
            if stream is not None:
                stream.close()


def _probe_catalog(
    codex: str,
    definition: AgentDef,
    *,
    cwd: Path,
    environment: Mapping[str, str],
    deadline: float,
) -> bytes:
    result = _run_bounded(
        (codex, "debug", "models", "--bundled"),
        cwd=cwd,
        environment=environment,
        deadline=deadline,
        stdout_limit=_CATALOG_LIMIT,
    )
    if result.returncode != 0 or result.stderr:
        raise EvidenceReaderLaunchError("catalog_probe_failed")
    try:
        projection = project_codex_catalog(
            result.stdout,
            expected_model=str(definition.codex.model),
            expected_reasoning_effort=str(definition.codex.reasoning_effort),
        )
    except ValueError as exc:
        raise EvidenceReaderLaunchError("catalog_invalid") from exc
    return projection.canonical_projected_bytes


def _probe_mcp(
    codex: str,
    config: bytes,
    *,
    cwd: Path,
    environment: Mapping[str, str],
    deadline: float,
) -> None:
    result = _run_bounded(
        (codex, "mcp", "list", "--json"),
        cwd=cwd,
        environment=environment,
        deadline=deadline,
        stdout_limit=256_000,
    )
    if (
        result.returncode != 0
        or result.stderr
        or _validate_codex_mcp_inventory(result.stdout, config)
    ):
        raise EvidenceReaderLaunchError("mcp_probe_failed")


def _codex_command(
    codex: str,
    definition: AgentDef,
    *,
    cwd: Path,
    output_schema_path: Path,
    prompt: str,
) -> list[str]:
    command = [
        codex,
        "exec",
        "--strict-config",
        "--ignore-rules",
        "--ephemeral",
        "--skip-git-repo-check",
        "--sandbox",
        "read-only",
        "--output-schema",
        str(output_schema_path),
        "--json",
        "-C",
        str(cwd),
        "-c",
        "project_root_markers=[]",
        "-c",
        "sandbox_workspace_write.network_access=false",
        "-c",
        "features.image_generation=false",
    ]
    if definition.codex.model is not None:
        command.extend(("--model", definition.codex.model))
    command.append(prompt)
    return command


def _decode_probe_event(line: str) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    event = json.loads(line)
    if not isinstance(event, dict) or event.get("type") not in {
        "thread.started",
        "turn.started",
        "item.started",
        "item.updated",
        "item.completed",
        "turn.completed",
    }:
        raise ValueError
    item = event.get("item")
    if (
        event.get("type") == "item.completed"
        and isinstance(item, dict)
        and item.get("type") == "agent_message"
        and isinstance(item.get("text"), str)
    ):
        decoded = json.loads(item["text"])
        if isinstance(decoded, dict):
            messages.append(decoded)
    elif (
        event.get("type") == "item.completed"
        and isinstance(item, dict)
        and item.get("type") == "message"
        and isinstance(item.get("content"), list)
    ):
        for block in item["content"]:
            if (
                isinstance(block, dict)
                and block.get("type") == "text"
                and isinstance(block.get("text"), str)
            ):
                decoded = json.loads(block["text"])
                if isinstance(decoded, dict):
                    messages.append(decoded)
    return messages


def _probe_agent_message(output: bytes) -> None:
    messages: list[dict[str, Any]] = []
    try:
        lines = output.decode("utf-8", errors="strict").splitlines()
        for line in lines:
            messages.extend(_decode_probe_event(line))
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise EvidenceReaderLaunchError("output_schema_probe_failed") from exc
    if messages != [{"probe": "ok"}]:
        raise EvidenceReaderLaunchError("output_schema_probe_failed")


def _probe_conformance(
    codex: str,
    definition: AgentDef,
    auth: EvidenceReaderAuthSelection,
    *,
    cwd: Path,
    environment: Mapping[str, str],
    probe_schema_path: Path,
    deadline: float,
) -> None:
    help_result = _run_bounded(
        (codex, "exec", "--help"),
        cwd=cwd,
        environment=environment,
        deadline=deadline,
        stdout_limit=64_000,
    )
    required_flags = (
        b"--strict-config",
        b"--ignore-rules",
        b"--ephemeral",
        b"--skip-git-repo-check",
        b"--output-schema",
        b"--json",
        b"--cd",
    )
    if (
        help_result.returncode != 0
        or help_result.stderr
        or any(flag not in help_result.stdout for flag in required_flags)
    ):
        raise EvidenceReaderLaunchError("cli_probe_failed")
    auth_result = _run_bounded(
        (codex, "login", "status"),
        cwd=cwd,
        environment=environment,
        deadline=deadline,
        stdout_limit=4_096,
    )
    if auth_result.stdout and auth_result.stderr:
        raise EvidenceReaderLaunchError("auth_probe_failed")
    auth_output = (auth_result.stdout or auth_result.stderr).strip()
    auth_matches = (
        auth_output == b"Logged in using ChatGPT"
        if auth.forced_login_method == "chatgpt"
        else auth_output.startswith(b"Logged in using an API key - ")
        and len(auth_output) > len(b"Logged in using an API key - ")
    )
    if auth_result.returncode != 0 or not auth_matches:
        raise EvidenceReaderLaunchError("auth_probe_failed")
    probe = _run_bounded(
        _codex_command(
            codex,
            definition,
            cwd=cwd,
            output_schema_path=probe_schema_path,
            prompt='Return exactly {"probe":"ok"}. Do not call any tool.',
        ),
        cwd=cwd,
        environment=environment,
        deadline=deadline,
        stdout_limit=256_000,
    )
    if probe.returncode != 0 or probe.stderr not in {b"", _CODEX_STDIN_NOTICE}:
        raise EvidenceReaderLaunchError("output_schema_probe_failed")
    _probe_agent_message(probe.stdout)


def _probe_cli_version(
    codex: str,
    *,
    cwd: Path,
    environment: Mapping[str, str],
    deadline: float,
) -> str:
    result = _run_bounded(
        (codex, "--version"),
        cwd=cwd,
        environment=environment,
        deadline=deadline,
        stdout_limit=4_096,
    )
    try:
        version = result.stdout.decode("utf-8", errors="strict").strip()
    except UnicodeDecodeError as exc:
        raise EvidenceReaderLaunchError("cli_probe_failed") from exc
    if result.returncode != 0 or result.stderr or version != _SUPPORTED_CODEX_CLI_VERSION:
        raise EvidenceReaderLaunchError("cli_probe_failed")
    return version


def _reader_probe_cache_key(
    definition: AgentDef,
    auth: EvidenceReaderAuthSelection,
    *,
    config: bytes,
    catalog: bytes,
    output_schema: bytes,
    transport: Mapping[str, object],
) -> str:
    payload = {
        "auth_method": auth.forced_login_method,
        "auth_source_digest": auth.source_digest,
        "catalog_digest": hashlib.sha256(catalog).hexdigest(),
        "config_digest": hashlib.sha256(config).hexdigest(),
        "definition_digest": agent_definition_digest(definition),
        "model": definition.codex.model,
        "policy": "read-only",
        "reasoning_effort": definition.codex.reasoning_effort,
        "schema_digest": hashlib.sha256(output_schema).hexdigest(),
        "transport": transport,
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _prepare_launch_request(
    definition: AgentDef,
    *,
    prompt: str,
    expected_scope_digest: str,
    expected_snapshot_digest: str,
    requested_fields: tuple[str, ...],
    max_stream_bytes: int,
    max_result_bytes: int,
) -> tuple[str, ...]:
    if not isinstance(definition, AgentDef) or not definition.reader_tools:
        raise EvidenceReaderLaunchError("reader_role_invalid")
    try:
        tools = canonical_reader_tools_to_bare(definition.reader_tools)
    except ValueError as exc:
        raise EvidenceReaderLaunchError("reader_role_invalid") from exc
    strings = (prompt, expected_scope_digest, expected_snapshot_digest, *requested_fields)
    if any(type(value) is not str or not value for value in strings):
        raise EvidenceReaderLaunchError("launch_scope_invalid")
    if not requested_fields or len(requested_fields) != len(set(requested_fields)):
        raise EvidenceReaderLaunchError("launch_scope_invalid")
    if len(prompt.encode("utf-8")) > _MAX_PROMPT_BYTES:
        raise EvidenceReaderLaunchError("launch_scope_invalid")
    if (
        not isinstance(max_stream_bytes, int)
        or isinstance(max_stream_bytes, bool)
        or not 1 <= max_stream_bytes <= _MAX_STREAM_BYTES
        or not isinstance(max_result_bytes, int)
        or isinstance(max_result_bytes, bool)
        or not 1 <= max_result_bytes <= _MAX_RESULT_BYTES
    ):
        raise EvidenceReaderLaunchError("launch_limits_invalid")
    return tools


def _validate_credential_parent(credential_file: Path | None, excluded: tuple[Path, ...]) -> None:
    if credential_file is None:
        return
    try:
        credential_parent = credential_file.parent.resolve(strict=True)
    except OSError as exc:
        if credential_file.exists() or credential_file.is_symlink():
            raise EvidenceReaderLaunchError("provider_auth_invalid") from exc
    else:
        if any(_overlaps(credential_parent, root) for root in excluded):
            raise EvidenceReaderLaunchError("provider_auth_invalid")


def _run_and_cache_reader_probes(
    codex: str,
    definition: AgentDef,
    auth: EvidenceReaderAuthSelection,
    *,
    config_bytes: bytes,
    catalog: bytes,
    output_schema: bytes,
    transport: Mapping[str, object],
    cwd: Path,
    environment: Mapping[str, str],
    probe_schema_path: Path,
    deadline: float,
    cli_version: str,
    probe_cache_path: Path,
) -> None:
    probe_cache_key = _reader_probe_cache_key(
        definition,
        auth,
        config=config_bytes,
        catalog=catalog,
        output_schema=output_schema,
        transport=transport,
    )
    cached_probe = read_probe_cache(
        probe_cache_path,
        cli_version,
        _PROBE_POLICY,
        cache_key=probe_cache_key,
    )
    if cached_probe is not None and cached_probe.passed:
        return
    try:
        _probe_mcp(
            codex,
            config_bytes,
            cwd=cwd,
            environment=environment,
            deadline=deadline,
        )
        _probe_conformance(
            codex,
            definition,
            auth,
            cwd=cwd,
            environment=environment,
            probe_schema_path=probe_schema_path,
            deadline=deadline,
        )
    except EvidenceReaderLaunchError as exc:
        write_probe_cache(
            probe_cache_path,
            ProbeResult(
                cli_version=cli_version,
                policy_identity=_PROBE_POLICY,
                passed=False,
                failure_detail=exc.code,
                probe_timestamp=datetime.now(UTC).isoformat(),
                cache_key=probe_cache_key,
            ),
        )
        raise
    write_probe_cache(
        probe_cache_path,
        ProbeResult(
            cli_version=cli_version,
            policy_identity=_PROBE_POLICY,
            passed=True,
            failure_detail=None,
            probe_timestamp=datetime.now(UTC).isoformat(),
            cache_key=probe_cache_key,
        ),
    )


def _cleanup_reader_directories(cwd: Path | None, home: Path) -> None:
    cleanup_errors: list[EvidenceReaderLaunchError] = []
    if cwd is not None:
        try:
            _remove_directory(cwd)
        except EvidenceReaderLaunchError as exc:
            cleanup_errors.append(exc)
    try:
        _remove_directory(home)
    except EvidenceReaderLaunchError as exc:
        cleanup_errors.append(exc)
    if cleanup_errors:
        primary = cleanup_errors[0]
        for follow_on in cleanup_errors[1:]:
            primary.__cause__ = follow_on
        raise primary


def launch_evidence_reader(
    definition: AgentDef,
    invocation: EvidenceReaderInvocationLike,
    *,
    prompt: str,
    mcp_transport: Mapping[str, object],
    provider_env: Mapping[str, str],
    credential_file: Path | None,
    requested_fields: tuple[str, ...],
    repository_root: Path,
    worktree_root: Path,
    common_git_dir: Path,
    expected_scope_digest: str,
    expected_snapshot_digest: str,
    deadline: float,
    max_stream_bytes: int = _MAX_STREAM_BYTES,
    max_result_bytes: int = _MAX_RESULT_BYTES,
) -> EvidenceReaderLaunchResult:
    """Launch, validate, and completely remove one sterile Codex reader session."""

    tools = _prepare_launch_request(
        definition,
        prompt=prompt,
        expected_scope_digest=expected_scope_digest,
        expected_snapshot_digest=expected_snapshot_digest,
        requested_fields=requested_fields,
        max_stream_bytes=max_stream_bytes,
        max_result_bytes=max_result_bytes,
    )
    _deadline_remaining(deadline)
    evidence_env = _invocation_environment(invocation)
    provider = _positive_mapping(provider_env, _PROVIDER_ENV, "provider_env_invalid")
    transport = _transport(mcp_transport)
    excluded = tuple(
        dict.fromkeys(
            (
                _real_root(repository_root, "repository_root"),
                _real_root(worktree_root, "worktree_root"),
                _real_root(common_git_dir, "common_git_dir"),
            )
        )
    )
    codex = shutil.which("codex")
    if codex is None:
        raise EvidenceReaderLaunchError("codex_unavailable")
    _validate_credential_parent(credential_file, excluded)
    auth = _select_authentication(provider, credential_file)
    home = _isolated_directory(excluded, "home")
    cwd: Path | None = None
    try:
        cwd = _isolated_directory((*excluded, home), "cwd")
        environment = {
            **dict(auth.environment),
            **evidence_env,
            "HOME": str(home),
            "CODEX_HOME": str(home),
            "CODEX_SQLITE_HOME": str(home),
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
        }
        if auth.credential_text is not None:
            _write_private(home / "auth.json", auth.credential_text)
        catalog = _probe_catalog(
            codex,
            definition,
            cwd=cwd,
            environment=environment,
            deadline=deadline,
        )
        catalog_path = home / "models.json"
        _write_private(catalog_path, catalog)
        config = _render_config(
            definition,
            transport,
            tools,
            catalog_path,
            auth,
            tuple(sorted(environment)),
            home,
        )
        try:
            tomllib.loads(config)
        except tomllib.TOMLDecodeError as exc:
            raise EvidenceReaderLaunchError("config_invalid") from exc
        config_path = home / "config.toml"
        _write_private(config_path, config)
        config_bytes = config.encode("utf-8")
        output_schema = _result_output_schema()
        output_schema_path = home / _OUTPUT_SCHEMA_NAME
        _write_private(output_schema_path, output_schema)
        probe_schema_path = home / _PROBE_SCHEMA_NAME
        _write_private(probe_schema_path, _probe_output_schema())
        cli_version = _probe_cli_version(
            codex, cwd=cwd, environment=environment, deadline=deadline
        )
        probe_cache_path = invocation.invocation_dir.parent / _PROBE_CACHE_NAME
        _run_and_cache_reader_probes(
            codex,
            definition,
            auth,
            config_bytes=config_bytes,
            catalog=catalog,
            output_schema=output_schema,
            transport=transport,
            cwd=cwd,
            environment=environment,
            probe_schema_path=probe_schema_path,
            deadline=deadline,
            cli_version=cli_version,
            probe_cache_path=probe_cache_path,
        )
        canary = secrets.token_urlsafe(24)
        command = _codex_command(
            codex,
            definition,
            cwd=cwd,
            output_schema_path=output_schema_path,
            prompt=_prompt(
                definition,
                prompt,
                canary=canary,
                scope=expected_scope_digest,
                snapshot=expected_snapshot_digest,
            ),
        )
        output = _run_bounded(
            command,
            cwd=cwd,
            environment=environment,
            deadline=deadline,
            stdout_limit=max_stream_bytes,
        )
        if output.returncode != 0 or output.stderr not in {b"", _CODEX_STDIN_NOTICE}:
            raise EvidenceReaderLaunchError("codex_execution_failed")
        result = _validate_stream(
            output.stdout,
            definition=definition,
            allowed_tools=tools,
            canary=canary,
            scope=expected_scope_digest,
            snapshot=expected_snapshot_digest,
            requested_fields=requested_fields,
            max_result_bytes=max_result_bytes,
        )
        _require_empty_cwd(cwd)
        conformance = EvidenceReaderConformanceEvidence(
            cli_version=cli_version,
            auth_method=auth.forced_login_method,
            auth_source_digest=auth.source_digest,
            role_definition_digest=agent_definition_digest(definition),
            authority_digest=evidence_env[EVIDENCE_READER_AUTHORITY_ENV_VAR],
            config_digest="sha256:" + hashlib.sha256(config_bytes).hexdigest(),
            catalog_digest="sha256:" + hashlib.sha256(catalog).hexdigest(),
            output_schema_digest="sha256:" + hashlib.sha256(output_schema).hexdigest(),
            transport_digest="sha256:"
            + hashlib.sha256(
                json.dumps(transport, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest(),
            command_digest="sha256:"
            + hashlib.sha256(
                json.dumps(command, separators=(",", ":")).encode("utf-8")
            ).hexdigest(),
            observation_scope=_OBSERVATION_SCOPE_LABELS,
        )
        return replace(result, conformance=conformance)
    finally:
        _cleanup_reader_directories(cwd, home)
