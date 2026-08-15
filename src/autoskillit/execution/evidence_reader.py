"""Sterile one-shot Codex launcher for an authorized evidence-reader role."""

from __future__ import annotations

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
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, cast

from autoskillit.core import (
    EVIDENCE_READER_AUTHORITY_ENV_VAR,
    EVIDENCE_READER_AUTHORITY_PATH_ENV_VAR,
    EVIDENCE_READER_CAPABILITY_ENV_VAR,
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
from autoskillit.execution.backends.codex import (
    _codex_exec_base,
    _validate_codex_mcp_inventory,
)
from autoskillit.execution.process._process_kill import spawn_owned_process

_EVIDENCE_ENV = frozenset(
    {
        EVIDENCE_READER_AUTHORITY_ENV_VAR,
        EVIDENCE_READER_CAPABILITY_ENV_VAR,
        EVIDENCE_READER_AUTHORITY_PATH_ENV_VAR,
    }
)
_PROVIDER_ENV = frozenset(
    {
        "ALL_PROXY",
        "CODEX_API_KEY",
        "CODEX_BASE_URL",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "NO_PROXY",
        "OPENAI_API_KEY",
        "OPENAI_BASE_URL",
        "OPENAI_ORGANIZATION",
        "OPENAI_ORG_ID",
        "OPENAI_PROJECT_ID",
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
_TRANSPORT_KEYS = frozenset(
    {"command", "args", "env_vars", "startup_timeout_sec", "tool_timeout_sec"}
)
_STREAM_CHUNK = 64 * 1024
_CATALOG_LIMIT = 2_000_000
_STDERR_LIMIT = 64 * 1024
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


def evidence_reader_provider_environment() -> dict[str, str]:
    """Return only positive provider authentication and transport variables."""

    return {name: os.environ[name] for name in _PROVIDER_ENV if os.environ.get(name)}


def evidence_reader_mcp_transport(config_path: Path) -> dict[str, object]:
    """Project the current local broker transport with only reader authority env."""

    transport = _canonical_explorer_mcp_transport(config_path)
    projected = {key: value for key, value in transport.items() if key in _TRANSPORT_KEYS}
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


@dataclass(frozen=True, slots=True)
class EvidenceCitation:
    citation_id: str
    start_byte: int
    end_byte: int
    start_line: int
    end_line: int


@dataclass(frozen=True, slots=True)
class EvidenceReaderLaunchResult:
    status: str
    role: str
    authorized_scope: str
    snapshot_digest: str
    thread_id: str
    citations: tuple[EvidenceCitation, ...]
    payload_json: str


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
    directory = Path(tempfile.mkdtemp(prefix=f"autoskillit-reader-{label}-"))
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


def _render_config(
    definition: AgentDef,
    transport: Mapping[str, object],
    tools: tuple[str, ...],
    catalog_path: Path,
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
        f"model_catalog_json = {_format_toml_value(str(catalog_path))}",
        f"instructions = {_format_toml_value(instructions)}",
        f"developer_instructions = {_format_toml_value(instructions)}",
        "[features]",
        *(
            f"{feature} = false"
            for feature in definition.codex.disabled_features
            if feature != "shell_tool"
        ),
        "[agents]",
        "enabled = false",
        *_render_direct_role_mcp_lines(transport, tools),
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
                descriptor = key.fileobj if isinstance(key.fileobj, int) else key.fileobj.fileno()
                try:
                    chunk = os.read(descriptor, _STREAM_CHUNK)
                except OSError:
                    chunk = b""
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
    if result.returncode != 0 or _validate_codex_mcp_inventory(result.stdout, config):
        raise EvidenceReaderLaunchError("mcp_probe_failed")


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
        "Operate only through the authorized evidence broker tools. Never invoke commands, "
        "file operations, delegation, web search, permissions, or any unlisted tool. Return "
        "exactly one compact JSON object matching the role's Completion shape, adding the "
        f'top-level field "canary":{json.dumps(canary)}. The authorized_scope must be '
        f"{json.dumps(scope)}, snapshot must be {json.dumps(snapshot)}, role must be "
        f"{json.dumps(definition.name)}, and child_identity.thread_id must be your actual "
        f"Codex thread ID. Task: {prompt}"
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
    allowed_tool_names = frozenset((*allowed_tools, *definition.reader_tools))
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
                if raw["type"] == "item.completed":
                    observed.update(_observed_citations(item))
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
    result_bytes = result_messages[0].encode("utf-8")
    if len(result_bytes) > max_result_bytes:
        raise EvidenceReaderLaunchError("result_limit_exceeded")
    try:
        payload = json.loads(result_bytes)
    except json.JSONDecodeError as exc:
        raise EvidenceReaderLaunchError("result_schema_invalid") from exc
    if not isinstance(payload, dict) or set(payload) != _RESULT_KEYS:
        raise EvidenceReaderLaunchError("result_schema_invalid")
    child = payload.get("child_identity")
    if (
        payload.get("canary") != canary
        or payload.get("role") != definition.name
        or payload.get("authorized_scope") != scope
        or payload.get("snapshot") != snapshot
        or not isinstance(child, dict)
        or child != {"thread_id": thread_ids[0]}
        or payload.get("status") not in {"answered", "partial", "blocked"}
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
        citation_id = evidence.get("citation_id")
        location = evidence.get("location")
        if not isinstance(citation_id, str) or not isinstance(location, dict):
            raise EvidenceReaderLaunchError("citation_invalid")
        fields = tuple(
            location.get(name) for name in ("start_byte", "end_byte", "start_line", "end_line")
        )
        if (
            evidence.get("representation") not in {"literal", "summary"}
            or not isinstance(evidence.get("field"), str)
            or not isinstance(evidence.get("value"), str)
            or not all(isinstance(field, int) and not isinstance(field, bool) for field in fields)
        ):
            raise EvidenceReaderLaunchError("citation_invalid")
        location_fields = cast(tuple[int, int, int, int], fields)
        if (
            citation_id not in observed
            or observed[citation_id] != location_fields
            or location_fields[0] < 0
            or location_fields[1] < location_fields[0]
            or location_fields[2] < 1
            or location_fields[3] < location_fields[2]
        ):
            raise EvidenceReaderLaunchError("citation_invalid")
        citations.append(EvidenceCitation(citation_id, *location_fields))
    if len({citation.citation_id for citation in citations}) != len(citations):
        raise EvidenceReaderLaunchError("citation_invalid")
    if any(
        not isinstance(gap, dict)
        or set(gap) != {"field", "reason"}
        or not isinstance(gap.get("field"), str)
        or not isinstance(gap.get("reason"), str)
        for gap in payload["coverage_gaps"]
    ):
        raise EvidenceReaderLaunchError("result_schema_invalid")
    payload_json = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return EvidenceReaderLaunchResult(
        status=payload["status"],
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
    repository_root: Path,
    worktree_root: Path,
    common_git_dir: Path,
    expected_scope_digest: str,
    expected_snapshot_digest: str,
    deadline: float,
    max_stream_bytes: int = 2_000_000,
    max_result_bytes: int = 256_000,
) -> EvidenceReaderLaunchResult:
    """Launch, validate, and completely remove one sterile Codex reader session."""

    if not isinstance(definition, AgentDef) or not definition.reader_tools:
        raise EvidenceReaderLaunchError("reader_role_invalid")
    try:
        tools = canonical_reader_tools_to_bare(definition.reader_tools)
    except ValueError as exc:
        raise EvidenceReaderLaunchError("reader_role_invalid") from exc
    strings = (prompt, expected_scope_digest, expected_snapshot_digest)
    if any(type(value) is not str or not value for value in strings):
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
    if not (_AUTH_KEYS & set(provider)):
        raise EvidenceReaderLaunchError("provider_auth_missing")
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
    home = _isolated_directory(excluded, "home")
    cwd: Path | None = None
    try:
        cwd = _isolated_directory((*excluded, home), "cwd")
        environment = {
            **provider,
            **evidence_env,
            "HOME": str(home),
            "CODEX_HOME": str(home),
            "CODEX_SQLITE_HOME": str(home),
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
        }
        catalog = _probe_catalog(
            codex,
            definition,
            cwd=cwd,
            environment=environment,
            deadline=deadline,
        )
        catalog_path = home / "models.json"
        _write_private(catalog_path, catalog)
        config = _render_config(definition, transport, tools, catalog_path)
        try:
            tomllib.loads(config)
        except tomllib.TOMLDecodeError as exc:
            raise EvidenceReaderLaunchError("config_invalid") from exc
        config_path = home / "config.toml"
        _write_private(config_path, config)
        config_bytes = config.encode("utf-8")
        _probe_mcp(
            codex,
            config_bytes,
            cwd=cwd,
            environment=environment,
            deadline=deadline,
        )
        canary = secrets.token_urlsafe(24)
        command = _codex_exec_base(
            sandbox="read-only",
            extra_overrides=("sandbox_workspace_write.network_access=false",),
        )
        command[0] = codex
        if definition.codex.model is not None:
            command.extend(("--model", definition.codex.model))
        command.append(
            _prompt(
                definition,
                prompt,
                canary=canary,
                scope=expected_scope_digest,
                snapshot=expected_snapshot_digest,
            )
        )
        output = _run_bounded(
            command,
            cwd=cwd,
            environment=environment,
            deadline=deadline,
            stdout_limit=max_stream_bytes,
        )
        if output.returncode != 0 or output.stderr:
            raise EvidenceReaderLaunchError("codex_execution_failed")
        result = _validate_stream(
            output.stdout,
            definition=definition,
            allowed_tools=tools,
            canary=canary,
            scope=expected_scope_digest,
            snapshot=expected_snapshot_digest,
            max_result_bytes=max_result_bytes,
        )
        _require_empty_cwd(cwd)
        return result
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
