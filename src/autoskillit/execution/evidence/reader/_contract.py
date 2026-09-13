"""Static sterile-reader contracts: authentication, projection, config, and schemas."""

from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import stat
import time
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Protocol

from autoskillit.core import (
    EVIDENCE_READER_AUTHORITY_PATH_ENV_VAR,
    EVIDENCE_READER_ENV_FORWARD_VARS,
    AgentDef,
    agent_definition_digest,
)
from autoskillit.execution.backends._claude_prompt import codex_discipline_suffix
from autoskillit.execution.backends._codex.explorer_projection import (
    _canonical_explorer_mcp_transport,
    _render_direct_role_mcp_lines,
)
from autoskillit.execution.backends._codex_config import _format_toml_value

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
_AUTH_FILE_LIMIT = 64 * 1024
_TRANSPORT_KEYS = frozenset(
    {"command", "args", "env_vars", "startup_timeout_sec", "tool_timeout_sec"}
)
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
CITATION_LOCATION_KEYS = ("byte_start", "byte_end", "line_start", "line_end")
_PROBE_SCHEMA_NAME = "evidence-reader-probe.schema.json"


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


def _positive_mapping(
    values: Mapping[str, str], allowed: frozenset[str], code: str
) -> dict[str, str]:
    normalized = dict(values)
    if set(normalized) - allowed or any(
        type(value) is not str or not value or len(value.encode("utf-8")) > 64_000
        for value in normalized.values()
    ):
        raise EvidenceReaderLaunchError(code)
    return normalized


def _invocation_environment(invocation: EvidenceReaderInvocationLike) -> dict[str, str]:
    environment = dict(invocation.environment)
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
    normalized = dict(transport)
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


def _result_output_schema() -> bytes:
    location = {
        "type": "object",
        "additionalProperties": False,
        "required": ["byte_start", "byte_end", "line_start", "line_end"],
        "properties": {
            name: {"type": "integer", "minimum": minimum}
            for name, minimum in (
                ("byte_start", 0),
                ("byte_end", 0),
                ("line_start", 1),
                ("line_end", 1),
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
