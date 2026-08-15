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
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol, cast

from autoskillit.core import (
    DIRECT_PREFIX,
    EVIDENCE_READER_AUTHORITY_ENV_VAR,
    EVIDENCE_READER_AUTHORITY_PATH_ENV_VAR,
    EVIDENCE_READER_ENV_FORWARD_VARS,
    AgentDef,
    agent_definition_digest,
    atomic_write,
    canonical_reader_tools_to_bare,
)
from autoskillit.execution.backends._claude_prompt import codex_discipline_suffix
from autoskillit.execution.backends._codex.explorer_projection import (
    _canonical_explorer_mcp_transport,
    _render_direct_role_mcp_lines,
)
from autoskillit.execution.backends._codex_catalog import project_codex_catalog
from autoskillit.execution.backends._codex_config import _format_toml_value
from autoskillit.execution.backends._codex_parse import CodexStreamParser
from autoskillit.execution.backends._probe_cache import (
    ProbeResult,
    read_probe_cache,
    write_probe_cache,
)
from autoskillit.execution.backends.codex import _validate_codex_mcp_inventory
from autoskillit.execution.process._process_kill import spawn_owned_process

_EVIDENCE_ENV = EVIDENCE_READER_ENV_FORWARD_VARS
_PROVIDER_ENV = frozenset(
    {
        "ALL_PROXY",
        "CODEX_API_KEY",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "NO_PROXY",
        "OPENAI_API_KEY",
        "REQUESTS_CA_BUNDLE",
        "SSL_CERT_DIR",
        "SSL_CERT_FILE",
        "all_proxy",
        "http_proxy",
        "https_proxy",
        "no_proxy",
    }
)
_AUTH_KEYS = frozenset({"CODEX_API_KEY", "OPENAI_API_KEY"})
_SUPPORTED_CODEX_CLI_VERSION = "codex-cli 0.147.0"
_AUTH_FILE_LIMIT = 64 * 1024
_TRANSPORT_KEYS = frozenset(
    {"command", "args", "env_vars", "startup_timeout_sec", "tool_timeout_sec"}
)
_STREAM_CHUNK = 64 * 1024
_CATALOG_LIMIT = 2_000_000
_STDERR_LIMIT = 64 * 1024
_CODEX_STDIN_NOTICE = b"Reading additional input from stdin...\n"
_MAX_STREAM_BYTES = 2_000_000
_MAX_RESULT_BYTES = 256_000
_MAX_PROMPT_BYTES = 64_000
_RESULT_KEYS = frozenset(
    {
        "canary",
        "status",
        "role",
        "authorized_scope",
        "snapshot",
        "evidence",
        "coverage_gaps",
        "complete",
        "truncated",
        "stop_reason",
        "child_identity",
    }
)
_OUTPUT_SCHEMA_NAME = "evidence-reader-result.schema.json"
_PROBE_SCHEMA_NAME = "evidence-reader-probe.schema.json"
_PROBE_CACHE_NAME = "codex-evidence-reader-probe-cache.json"
_PROBE_POLICY = "codex-evidence-reader-v1"


@dataclass(frozen=True, slots=True)
class EvidenceReaderAuthSelection:
    forced_login_method: str
    environment: tuple[tuple[str, str], ...]
    credential_text: str | None
    source_digest: str


@dataclass(frozen=True, slots=True)
class EvidenceReaderConformanceEvidence:
    cli_version: str
    auth_method: str
    auth_source_digest: str
    role_definition_digest: str
    authority_digest: str
    config_digest: str
    catalog_digest: str
    output_schema_digest: str
    transport_digest: str
    command_digest: str
    observation_scope: tuple[str, ...]


def evidence_reader_provider_environment() -> dict[str, str]:
    """Return only positive provider authentication and transport variables."""

    return {name: os.environ[name] for name in _PROVIDER_ENV if os.environ.get(name)}


def _read_chatgpt_credential(path: Path) -> str:
    descriptor = -1
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or stat.S_IMODE(metadata.st_mode) & 0o077
            or not 1 <= metadata.st_size <= _AUTH_FILE_LIMIT
        ):
            raise EvidenceReaderLaunchError("provider_auth_invalid")
        chunks: list[bytes] = []
        remaining = _AUTH_FILE_LIMIT + 1
        while remaining:
            chunk = os.read(descriptor, remaining)
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
        after = os.fstat(descriptor)
    except OSError as exc:
        raise EvidenceReaderLaunchError("provider_auth_invalid") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if len(raw) != metadata.st_size or (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mtime_ns,
        metadata.st_size,
    ) != (after.st_dev, after.st_ino, after.st_mtime_ns, after.st_size):
        raise EvidenceReaderLaunchError("provider_auth_invalid")
    try:
        text = raw.decode("utf-8", errors="strict")
        payload = json.loads(text)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise EvidenceReaderLaunchError("provider_auth_invalid") from exc
    tokens = payload.get("tokens") if isinstance(payload, dict) else None
    if (
        not isinstance(payload, dict)
        or payload.get("auth_mode") != "chatgpt"
        or not isinstance(tokens, dict)
        or not isinstance(tokens.get("access_token"), str)
        or not tokens["access_token"]
        or not isinstance(tokens.get("account_id"), str)
        or not tokens["account_id"]
    ):
        raise EvidenceReaderLaunchError("provider_auth_invalid")
    return text


def _select_authentication(
    provider: Mapping[str, str], credential_file: Path | None
) -> EvidenceReaderAuthSelection:
    auth_keys = tuple(sorted(_AUTH_KEYS & set(provider)))
    credential_present = False
    if credential_file is not None:
        try:
            credential_present = credential_file.exists() or credential_file.is_symlink()
        except OSError as exc:
            raise EvidenceReaderLaunchError("provider_auth_invalid") from exc
    if len(auth_keys) + int(credential_present) != 1:
        code = (
            "provider_auth_missing"
            if not auth_keys and not credential_present
            else "provider_auth_ambiguous"
        )
        raise EvidenceReaderLaunchError(code)
    if auth_keys:
        selected = auth_keys[0]
        return EvidenceReaderAuthSelection(
            forced_login_method="api",
            environment=tuple(sorted(provider.items())),
            credential_text=None,
            source_digest="sha256:"
            + hashlib.sha256(f"api:{selected}".encode("ascii")).hexdigest(),
        )
    assert credential_file is not None
    text = _read_chatgpt_credential(credential_file)
    return EvidenceReaderAuthSelection(
        forced_login_method="chatgpt",
        environment=tuple(sorted(provider.items())),
        credential_text=text,
        source_digest="sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest(),
    )


def evidence_reader_mcp_transport(config_path: Path) -> dict[str, object]:
    """Project the current local broker transport with only reader authority env."""

    transport = _canonical_explorer_mcp_transport(config_path)
    projected = {key: value for key, value in transport.items() if key in _TRANSPORT_KEYS}
    command = projected.get("command")
    resolved_command = shutil.which(command) if isinstance(command, str) else None
    if resolved_command is None:
        raise ValueError("evidence reader broker command is unavailable")
    try:
        executable = Path(resolved_command).resolve(strict=True)
        mode = executable.stat().st_mode
    except OSError as exc:
        raise ValueError("evidence reader broker command is unavailable") from exc
    if not stat.S_ISREG(mode) or not os.access(executable, os.X_OK):
        raise ValueError("evidence reader broker command is unavailable")
    projected["command"] = str(executable)
    projected["env_vars"] = sorted(_EVIDENCE_ENV)
    return projected


class EvidenceReaderInvocationLike(Protocol):
    @property
    def invocation_dir(self) -> Path: ...

    @property
    def environment(self) -> tuple[tuple[str, str], ...]: ...

    @property
    def expires_at(self) -> float: ...


class EvidenceReaderLaunchError(RuntimeError):
    """A fail-closed sterile-launch rejection."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class EvidenceReaderResultStatus(StrEnum):
    ANSWERED = "answered"
    PARTIAL = "partial"
    BLOCKED = "blocked"


@dataclass(frozen=True, slots=True)
class EvidenceCitation:
    citation_id: str
    byte_start: int
    byte_end: int
    line_start: int
    line_end: int


@dataclass(frozen=True, slots=True)
class EvidenceReaderLaunchResult:
    status: EvidenceReaderResultStatus
    role: str
    authorized_scope: str
    snapshot_digest: str
    thread_id: str
    citations: tuple[EvidenceCitation, ...]
    payload_json: str
    conformance: EvidenceReaderConformanceEvidence | None = None


@dataclass(frozen=True, slots=True)
class _ProcessOutput:
    returncode: int
    stdout: bytes
    stderr: bytes


def _positive_mapping(
    values: Mapping[str, str], allowed: frozenset[str], code: str
) -> dict[str, str]:
    try:
        normalized = dict(values)
    except (TypeError, ValueError) as exc:
        raise EvidenceReaderLaunchError(code) from exc
    if set(normalized) - allowed or any(
        type(value) is not str or not value or len(value.encode("utf-8")) > 64_000
        for value in normalized.values()
    ):
        raise EvidenceReaderLaunchError(code)
    return normalized


def _invocation_environment(invocation: EvidenceReaderInvocationLike) -> dict[str, str]:
    try:
        environment = dict(invocation.environment)
    except (TypeError, ValueError) as exc:
        raise EvidenceReaderLaunchError("invocation_invalid") from exc
    if set(environment) != _EVIDENCE_ENV or any(
        type(value) is not str or not value for value in environment.values()
    ):
        raise EvidenceReaderLaunchError("invocation_invalid")
    authority_path = Path(environment[EVIDENCE_READER_AUTHORITY_PATH_ENV_VAR])
    try:
        invocation_root = Path(invocation.invocation_dir).resolve(strict=True)
        authority = authority_path.resolve(strict=True)
    except (OSError, TypeError, ValueError) as exc:
        raise EvidenceReaderLaunchError("invocation_invalid") from exc
    if authority.parent != invocation_root or not authority_path.is_absolute():
        raise EvidenceReaderLaunchError("invocation_invalid")
    expires_at = invocation.expires_at
    if (
        not isinstance(expires_at, (int, float))
        or isinstance(expires_at, bool)
        or not math.isfinite(expires_at)
        or expires_at <= time.time()
    ):
        raise EvidenceReaderLaunchError("invocation_expired")
    return environment


def _transport(transport: Mapping[str, object]) -> dict[str, object]:
    try:
        normalized = dict(transport)
    except (TypeError, ValueError) as exc:
        raise EvidenceReaderLaunchError("transport_invalid") from exc
    if set(normalized) - _TRANSPORT_KEYS or set(normalized) < {"command", "env_vars"}:
        raise EvidenceReaderLaunchError("transport_invalid")
    command = normalized.get("command")
    args = normalized.get("args", [])
    env_vars = normalized.get("env_vars")
    if (
        type(command) is not str
        or not command
        or not Path(command).is_absolute()
        or not isinstance(args, list)
        or any(type(value) is not str or not value for value in args)
        or len(args) > 64
        or sum(len(value.encode("utf-8")) for value in args) > 64_000
        or not isinstance(env_vars, list)
        or frozenset(env_vars) != _EVIDENCE_ENV
        or len(env_vars) != len(_EVIDENCE_ENV)
    ):
        raise EvidenceReaderLaunchError("transport_invalid")
    for key in ("startup_timeout_sec", "tool_timeout_sec"):
        value = normalized.get(key)
        if key in normalized and (
            not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0
        ):
            raise EvidenceReaderLaunchError("transport_invalid")
    normalized["args"] = args
    normalized["env_vars"] = sorted(_EVIDENCE_ENV)
    return normalized


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


def _result_output_schema() -> bytes:
    location = {
        "type": "object",
        "additionalProperties": False,
        "required": ["start_byte", "end_byte", "start_line", "end_line"],
        "properties": {
            name: {"type": "integer", "minimum": minimum}
            for name, minimum in (
                ("start_byte", 0),
                ("end_byte", 0),
                ("start_line", 1),
                ("end_line", 1),
            )
        },
    }
    schema = {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "type": "object",
        "additionalProperties": False,
        "required": sorted(_RESULT_KEYS),
        "properties": {
            "canary": {"type": "string", "minLength": 1},
            "status": {"enum": [status.value for status in EvidenceReaderResultStatus]},
            "role": {"type": "string", "minLength": 1},
            "authorized_scope": {"type": "string", "minLength": 1},
            "snapshot": {"type": "string", "minLength": 1},
            "evidence": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": [
                        "field",
                        "value",
                        "representation",
                        "citation_id",
                        "location",
                    ],
                    "properties": {
                        "field": {"type": "string", "minLength": 1},
                        "value": {"type": "string"},
                        "representation": {"enum": ["literal", "summary"]},
                        "citation_id": {"type": "string", "minLength": 1},
                        "location": location,
                    },
                },
            },
            "coverage_gaps": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["field", "reason"],
                    "properties": {
                        "field": {"type": "string", "minLength": 1},
                        "reason": {"type": "string", "minLength": 1},
                    },
                },
            },
            "complete": {"type": "boolean"},
            "truncated": {"type": "boolean"},
            "stop_reason": {"type": "string", "minLength": 1},
            "child_identity": {
                "type": "object",
                "additionalProperties": False,
                "required": ["thread_id"],
                "properties": {"thread_id": {"type": "string", "minLength": 1}},
            },
        },
    }
    return json.dumps(schema, sort_keys=True, separators=(",", ":")).encode("ascii")


def _probe_output_schema() -> bytes:
    schema = {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "type": "object",
        "additionalProperties": False,
        "required": ["probe"],
        "properties": {"probe": {"const": "ok", "type": "string"}},
    }
    return json.dumps(schema, sort_keys=True, separators=(",", ":")).encode("ascii")


def _render_config(
    definition: AgentDef,
    transport: Mapping[str, object],
    tools: tuple[str, ...],
    catalog_path: Path,
    auth: EvidenceReaderAuthSelection,
    shell_environment_names: tuple[str, ...],
    mcp_cwd: Path,
) -> str:
    digest = agent_definition_digest(definition)
    instructions = (
        f"{definition.body}\n\nAutoSkillit agent definition digest: {digest}\n\n"
        f"{codex_discipline_suffix()}"
    )
    lines = [
        f"model = {_format_toml_value(definition.codex.model)}",
        f"model_reasoning_effort = {_format_toml_value(definition.codex.reasoning_effort)}",
        'approval_policy = "never"',
        'sandbox_mode = "read-only"',
        'web_search = "disabled"',
        f"forced_login_method = {_format_toml_value(auth.forced_login_method)}",
        "project_root_markers = []",
        f"model_catalog_json = {_format_toml_value(str(catalog_path))}",
        f"instructions = {_format_toml_value(instructions)}",
        f"developer_instructions = {_format_toml_value(instructions)}",
        "[shell_environment_policy]",
        'inherit = "none"',
        f"include_only = {_format_toml_value(list(shell_environment_names))}",
        "[sandbox_workspace_write]",
        "network_access = false",
        "[features]",
        *(f"{feature} = false" for feature in definition.codex.disabled_features),
        "[agents]",
        "enabled = false",
        *_render_direct_role_mcp_lines(transport, tools),
        f"cwd = {_format_toml_value(str(mcp_cwd))}",
    ]
    return "\n".join(lines) + "\n"


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
        while selector.get_map() or owner.observe_exit() is None:
            remaining = _deadline_remaining(deadline)
            if not selector.get_map():
                time.sleep(min(0.01, remaining))
                continue
            for key, _ in selector.select(min(0.1, remaining)):
                descriptor = (
                    key.fileobj if isinstance(key.fileobj, int) else key.fileobj.fileno()
                )
                chunk = os.read(descriptor, _STREAM_CHUNK)
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                limit = stdout_limit if key.data == "stdout" else _STDERR_LIMIT
                if len(output[key.data]) + len(chunk) > limit:
                    raise EvidenceReaderLaunchError("stream_limit_exceeded")
                output[key.data].extend(chunk)
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


def _probe_agent_message(output: bytes) -> None:
    messages: list[dict[str, Any]] = []
    try:
        lines = output.decode("utf-8", errors="strict").splitlines()
        for line in lines:
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


def _prompt(
    definition: AgentDef,
    prompt: str,
    *,
    canary: str,
    scope: str,
    snapshot: str,
) -> str:
    return (
        f"{definition.body}\n\n"
        f"Your first MCP call must be {DIRECT_PREFIX}read_authorized_artifact. Use "
        f"{DIRECT_PREFIX}get_authorized_artifact_page only when its continuation is non-null. "
        "Do not list MCP "
        "resources, templates, or tools. Operate only through those authorized evidence broker "
        "tools. Never invoke commands, "
        "file operations, delegation, web search, permissions, or any unlisted tool. For every "
        "evidence item, copy the broker's citation_id and all four byte/line location values "
        "exactly; do not adjust them per field. Return exactly one compact JSON object matching "
        "the role's Completion shape, adding the "
        f'top-level field "canary":{json.dumps(canary)}. The authorized_scope must be '
        f"{json.dumps(scope)}, snapshot must be {json.dumps(snapshot)}, role must be "
        f"{json.dumps(definition.name)}. Set child_identity.thread_id to any non-empty "
        "placeholder; "
        f"the launcher replaces it with the observed Codex thread identity. Task: {prompt}"
    )


def _json_object(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str) or len(value) > 1_000_000:
        return None
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError:
        return None
    return decoded if isinstance(decoded, dict) else None


def _observed_citations(value: Any) -> dict[str, tuple[int, int, int, int]]:
    found: dict[str, tuple[int, int, int, int]] = {}

    def visit(item: Any) -> None:
        decoded = _json_object(item)
        if decoded is not None and decoded is not item:
            visit(decoded)
            return
        if isinstance(item, dict):
            citation_id = item.get("citation_id")
            location = item.get("location")
            if isinstance(citation_id, str):
                source = location if isinstance(location, dict) else item
                names = (
                    ("start_byte", "end_byte", "start_line", "end_line")
                    if isinstance(location, dict)
                    else ("byte_start", "byte_end", "line_start", "line_end")
                )
                fields = tuple(source.get(name) for name in names)
                if all(isinstance(field, int) and not isinstance(field, bool) for field in fields):
                    normalized_fields = cast(tuple[int, int, int, int], fields)
                    if citation_id in found and found[citation_id] != normalized_fields:
                        raise EvidenceReaderLaunchError("citation_invalid")
                    found[citation_id] = normalized_fields
            for child in item.values():
                visit(child)
        elif isinstance(item, list):
            for child in item:
                visit(child)

    visit(value)
    return found


def _validate_stream(
    output: bytes,
    *,
    definition: AgentDef,
    allowed_tools: tuple[str, ...],
    canary: str,
    scope: str,
    snapshot: str,
    requested_fields: tuple[str, ...],
    max_result_bytes: int,
) -> EvidenceReaderLaunchResult:
    try:
        text = output.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise EvidenceReaderLaunchError("stream_invalid") from exc
    parser = CodexStreamParser()
    thread_ids: list[str] = []
    terminal = 0
    result_messages: list[str] = []
    observed: dict[str, tuple[int, int, int, int]] = {}
    started_mcp_calls: dict[str, str] = {}
    completed_mcp_call_ids: set[str] = set()
    completed_mcp_tools: list[str] = []
    successful_mcp_tools: list[str] = []
    definition_bare_tools = canonical_reader_tools_to_bare(definition.reader_tools)
    tool_aliases = {
        **dict(zip(definition.reader_tools, definition_bare_tools, strict=True)),
        **{tool: tool for tool in allowed_tools},
    }
    allowed_tool_names = frozenset(tool_aliases)
    allowed_events = {
        "thread.started",
        "turn.started",
        "item.started",
        "item.updated",
        "item.completed",
        "turn.completed",
    }
    allowed_items = {"reasoning", "todo_list", "mcp_tool_call", "agent_message", "message"}
    for raw_line in text.splitlines():
        try:
            raw = json.loads(raw_line)
        except json.JSONDecodeError as exc:
            raise EvidenceReaderLaunchError("stream_invalid") from exc
        if not isinstance(raw, dict) or raw.get("type") not in allowed_events:
            raise EvidenceReaderLaunchError("stream_shape_forbidden")
        event = parser.parse_line(raw_line)
        if event is None:
            raise EvidenceReaderLaunchError("stream_shape_forbidden")
        if raw["type"] == "thread.started":
            thread_id = raw.get("thread_id")
            if not isinstance(thread_id, str) or not thread_id:
                raise EvidenceReaderLaunchError("child_identity_invalid")
            thread_ids.append(thread_id)
        elif raw["type"] == "turn.completed":
            terminal += 1
        elif raw["type"] in {"item.started", "item.updated", "item.completed"}:
            item = raw.get("item")
            if not isinstance(item, dict) or item.get("type") not in allowed_items:
                raise EvidenceReaderLaunchError("forbidden_operation")
            if item["type"] == "mcp_tool_call":
                tool = item.get("tool_name", item.get("name", item.get("tool")))
                if tool not in allowed_tool_names:
                    raise EvidenceReaderLaunchError("tool_not_authorized")
                normalized_tool = tool_aliases[cast(str, tool)]
                call_id = item.get("id")
                if call_id is not None and (not isinstance(call_id, str) or not call_id):
                    raise EvidenceReaderLaunchError("stream_shape_forbidden")
                if raw["type"] == "item.started" and isinstance(call_id, str):
                    if call_id in started_mcp_calls:
                        raise EvidenceReaderLaunchError("stream_shape_forbidden")
                    started_mcp_calls[call_id] = normalized_tool
                if raw["type"] == "item.completed":
                    if isinstance(call_id, str) and call_id in completed_mcp_call_ids:
                        raise EvidenceReaderLaunchError("stream_shape_forbidden")
                    if (
                        item.get("status") not in {None, "completed", "success"}
                        or item.get("error") not in {None, ""}
                        or (
                            isinstance(call_id, str)
                            and started_mcp_calls.get(call_id, normalized_tool) != normalized_tool
                        )
                    ):
                        raise EvidenceReaderLaunchError("mcp_call_failed")
                    if isinstance(call_id, str):
                        completed_mcp_call_ids.add(call_id)
                    completed_mcp_tools.append(normalized_tool)
                    call_citations = _observed_citations(item)
                    if call_citations:
                        successful_mcp_tools.append(normalized_tool)
                    for citation_id, location in call_citations.items():
                        if citation_id in observed and observed[citation_id] != location:
                            raise EvidenceReaderLaunchError("citation_invalid")
                        observed[citation_id] = location
            elif item["type"] == "agent_message" and raw["type"] == "item.completed":
                if isinstance(item.get("text"), str):
                    result_messages.append(item["text"])
            elif item["type"] == "message" and raw["type"] == "item.completed":
                blocks = item.get("content")
                if not isinstance(blocks, list):
                    raise EvidenceReaderLaunchError("stream_shape_forbidden")
                result_messages.extend(
                    block["text"]
                    for block in blocks
                    if isinstance(block, dict)
                    and block.get("type") == "text"
                    and isinstance(block.get("text"), str)
                )
    if parser.ndjson_unknown_event_count or parser.ndjson_unknown_item_count:
        raise EvidenceReaderLaunchError("stream_shape_forbidden")
    if len(thread_ids) != 1 or terminal != 1 or len(result_messages) != 1:
        raise EvidenceReaderLaunchError("terminal_result_invalid")
    initial_seen = False
    page_started = False
    sequence_invalid = not completed_mcp_tools
    for tool in successful_mcp_tools:
        if tool == "read_authorized_artifact":
            sequence_invalid = sequence_invalid or page_started
            initial_seen = True
        elif tool == "get_authorized_artifact_page":
            sequence_invalid = sequence_invalid or not initial_seen
            page_started = True
    if set(started_mcp_calls) - completed_mcp_call_ids or sequence_invalid:
        raise EvidenceReaderLaunchError("mcp_call_sequence_invalid")
    result_bytes = result_messages[0].encode("utf-8")
    if len(result_bytes) > max_result_bytes:
        raise EvidenceReaderLaunchError("result_limit_exceeded")
    try:
        payload = json.loads(result_bytes)
    except json.JSONDecodeError as exc:
        raise EvidenceReaderLaunchError("result_schema_invalid") from exc
    if not isinstance(payload, dict) or set(payload) != _RESULT_KEYS:
        raise EvidenceReaderLaunchError("result_schema_invalid")
    raw_status = payload.get("status")
    if not isinstance(raw_status, str):
        raise EvidenceReaderLaunchError("result_schema_invalid")
    try:
        status = EvidenceReaderResultStatus(raw_status)
    except (TypeError, ValueError) as exc:
        raise EvidenceReaderLaunchError("result_schema_invalid") from exc
    child = payload.get("child_identity")
    if (
        payload.get("canary") != canary
        or payload.get("role") != definition.name
        or payload.get("authorized_scope") != scope
        or payload.get("snapshot") != snapshot
        or not isinstance(child, dict)
        or set(child) != {"thread_id"}
        or not isinstance(child.get("thread_id"), str)
        or not child["thread_id"]
        or type(payload.get("complete")) is not bool
        or type(payload.get("truncated")) is not bool
        or not isinstance(payload.get("stop_reason"), str)
        or not payload["stop_reason"]
        or not isinstance(payload.get("evidence"), list)
        or not isinstance(payload.get("coverage_gaps"), list)
    ):
        raise EvidenceReaderLaunchError("result_schema_invalid")
    citations: list[EvidenceCitation] = []
    for evidence in payload["evidence"]:
        if not isinstance(evidence, dict) or set(evidence) != {
            "field",
            "value",
            "representation",
            "citation_id",
            "location",
        }:
            raise EvidenceReaderLaunchError("result_schema_invalid")
        raw_citation_id = evidence.get("citation_id")
        raw_location = evidence.get("location")
        if (
            not isinstance(raw_citation_id, str)
            or not raw_citation_id
            or not isinstance(raw_location, dict)
        ):
            raise EvidenceReaderLaunchError("citation_invalid")
        fields = tuple(
            raw_location.get(name) for name in ("start_byte", "end_byte", "start_line", "end_line")
        )
        if (
            evidence.get("representation") not in {"literal", "summary"}
            or not isinstance(evidence.get("field"), str)
            or not evidence["field"]
            or not isinstance(evidence.get("value"), str)
            or not all(isinstance(field, int) and not isinstance(field, bool) for field in fields)
        ):
            raise EvidenceReaderLaunchError("citation_invalid")
        location_fields = cast(tuple[int, int, int, int], fields)
        receipt_location = observed.get(raw_citation_id)
        if (
            receipt_location is None
            or location_fields[0] < 0
            or location_fields[1] < location_fields[0]
            or location_fields[2] < 1
            or location_fields[3] < location_fields[2]
            or location_fields[0] < receipt_location[0]
            or location_fields[1] > receipt_location[1]
            or location_fields[2] < receipt_location[2]
            or location_fields[3] > receipt_location[3]
        ):
            raise EvidenceReaderLaunchError("citation_invalid")
        citations.append(EvidenceCitation(raw_citation_id, *location_fields))
    if any(
        not isinstance(gap, dict)
        or set(gap) != {"field", "reason"}
        or not isinstance(gap.get("field"), str)
        or not gap["field"]
        or not isinstance(gap.get("reason"), str)
        or not gap["reason"]
        for gap in payload["coverage_gaps"]
    ):
        raise EvidenceReaderLaunchError("result_schema_invalid")
    evidence_fields = tuple(item["field"] for item in payload["evidence"])
    gap_fields = tuple(item["field"] for item in payload["coverage_gaps"])
    if (
        len(requested_fields) != len(set(requested_fields))
        or len(evidence_fields) != len(set(evidence_fields))
        or len(gap_fields) != len(set(gap_fields))
        or set(evidence_fields) & set(gap_fields)
        or set(evidence_fields) | set(gap_fields) != set(requested_fields)
    ):
        raise EvidenceReaderLaunchError("result_partition_invalid")
    complete = payload["complete"]
    truncated = payload["truncated"]
    stop_reason = payload["stop_reason"]
    state_valid = {
        EvidenceReaderResultStatus.ANSWERED: (
            bool(evidence_fields)
            and not gap_fields
            and complete
            and not truncated
            and stop_reason == "requested fields covered"
        ),
        EvidenceReaderResultStatus.PARTIAL: (
            bool(evidence_fields)
            and bool(gap_fields)
            and complete is not truncated
            and stop_reason in {"artifact exhausted", "concrete blocker"}
        ),
        EvidenceReaderResultStatus.BLOCKED: (
            not evidence_fields
            and bool(gap_fields)
            and complete
            and not truncated
            and stop_reason == "concrete blocker"
        ),
    }
    if not state_valid[status]:
        raise EvidenceReaderLaunchError("result_state_invalid")
    payload["child_identity"] = {"thread_id": thread_ids[0]}
    payload_json = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return EvidenceReaderLaunchResult(
        status=status,
        role=definition.name,
        authorized_scope=scope,
        snapshot_digest=snapshot,
        thread_id=thread_ids[0],
        citations=tuple(citations),
        payload_json=payload_json,
    )


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
    if credential_file is not None:
        try:
            credential_parent = credential_file.parent.resolve(strict=True)
        except OSError as exc:
            if credential_file.exists() or credential_file.is_symlink():
                raise EvidenceReaderLaunchError("provider_auth_invalid") from exc
        else:
            if any(_overlaps(credential_parent, root) for root in excluded):
                raise EvidenceReaderLaunchError("provider_auth_invalid")
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
        probe_cache_key = _reader_probe_cache_key(
            definition,
            auth,
            config=config_bytes,
            catalog=catalog,
            output_schema=output_schema,
            transport=transport,
        )
        probe_cache_path = invocation.invocation_dir.parent / _PROBE_CACHE_NAME
        cached_probe = read_probe_cache(
            probe_cache_path,
            cli_version,
            _PROBE_POLICY,
            cache_key=probe_cache_key,
        )
        if cached_probe is None or not cached_probe.passed:
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
            observation_scope=(
                "generated_config",
                "configured_mcp_transport",
                "supported_cli_and_catalog_shape",
                "output_schema_behavioral_canary",
                "observed_runtime_calls",
                "not_exhaustive_native_tool_inventory",
            ),
        )
        return replace(result, conformance=conformance)
    finally:
        cleanup_error: EvidenceReaderLaunchError | None = None
        if cwd is not None:
            try:
                _remove_directory(cwd)
            except EvidenceReaderLaunchError as exc:
                cleanup_error = exc
        try:
            _remove_directory(home)
        except EvidenceReaderLaunchError as exc:
            cleanup_error = exc
        if cleanup_error is not None:
            raise cleanup_error
