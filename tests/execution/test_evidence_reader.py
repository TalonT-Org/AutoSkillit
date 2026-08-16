from __future__ import annotations

import json
import os
import stat
import time
from collections.abc import Callable
from enum import StrEnum
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import autoskillit.execution.evidence_reader as launcher
from autoskillit.core import (
    EVIDENCE_READER_AUTHORITY_ENV_VAR,
    EVIDENCE_READER_AUTHORITY_PATH_ENV_VAR,
    EVIDENCE_READER_CAPABILITY_ENV_VAR,
    AgentDef,
    load_bundled_agent_definitions,
)
from autoskillit.execution.evidence_reader import (
    EvidenceReaderLaunchError,
    EvidenceReaderResultStatus,
    evidence_reader_mcp_transport,
    launch_evidence_reader,
)

pytestmark = [pytest.mark.layer("execution"), pytest.mark.medium]

_CANARY = "reader-canary"
_SCOPE = "sha256:authorized-scope"
_SNAPSHOT = "sha256:authorized-snapshot"
_THREAD = "thread-reader"
_CITATION = "sha256:citation"
_EVIDENCE_ENV = {
    EVIDENCE_READER_AUTHORITY_ENV_VAR: "sha256:authority",
    EVIDENCE_READER_CAPABILITY_ENV_VAR: "capability",
    EVIDENCE_READER_AUTHORITY_PATH_ENV_VAR: "",
}


class _ErrorCode(StrEnum):
    CLEANUP_INCOMPLETE = "cleanup_incomplete"
    CODEX_UNAVAILABLE = "codex_unavailable"
    FORBIDDEN_OPERATION = "forbidden_operation"
    ISOLATION_INVALID = "isolation_invalid"
    PROCESS_CLEANUP_INCOMPLETE = "process_cleanup_incomplete"
    RESULT_LIMIT_EXCEEDED = "result_limit_exceeded"
    RESULT_SCHEMA_INVALID = "result_schema_invalid"
    STREAM_INVALID = "stream_invalid"
    STREAM_SHAPE_FORBIDDEN = "stream_shape_forbidden"
    TERMINAL_RESULT_INVALID = "terminal_result_invalid"
    TOOL_NOT_AUTHORIZED = "tool_not_authorized"


def _definition() -> AgentDef:
    return next(
        definition
        for definition in load_bundled_agent_definitions()
        if definition.name == "pr-source-reader"
    )


def _invocation(tmp_path: Path) -> SimpleNamespace:
    invocation_dir = tmp_path / "invocation"
    invocation_dir.mkdir(exist_ok=True)
    authority = invocation_dir / "authority.json"
    authority.write_text("{}")
    environment = {**_EVIDENCE_ENV, EVIDENCE_READER_AUTHORITY_PATH_ENV_VAR: str(authority)}
    return SimpleNamespace(
        invocation_dir=invocation_dir,
        environment=tuple(sorted(environment.items())),
        expires_at=time.time() + 60,
    )


def _roots(tmp_path: Path) -> tuple[Path, Path, Path]:
    roots = tuple(tmp_path / name for name in ("repository", "worktree", "common-git"))
    for root in roots:
        root.mkdir(exist_ok=True)
    return roots


def _transport() -> dict[str, object]:
    return {
        "command": "/usr/bin/evidence-broker",
        "args": ["serve"],
        "env_vars": sorted(_EVIDENCE_ENV),
        "startup_timeout_sec": 2,
        "tool_timeout_sec": 2,
    }


def test_mcp_transport_resolves_configured_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable = tmp_path / "autoskillit"
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    executable.chmod(0o755)
    config = tmp_path / "config.toml"
    config.write_text(
        '[mcp_servers.autoskillit]\ncommand = "autoskillit"\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(launcher.shutil, "which", lambda command: str(executable))

    transport = evidence_reader_mcp_transport(config)

    assert transport == {
        "command": str(executable.resolve()),
        "env_vars": sorted(_EVIDENCE_ENV),
    }


def _payload(*, canary: str = _CANARY, scope: str = _SCOPE) -> dict[str, Any]:
    return {
        "canary": canary,
        "status": "answered",
        "role": "pr-source-reader",
        "authorized_scope": scope,
        "snapshot": _SNAPSHOT,
        "evidence": [
            {
                "field": "field",
                "value": "value",
                "representation": "literal",
                "citation_id": _CITATION,
                "location": {
                    "byte_start": 0,
                    "byte_end": 5,
                    "line_start": 1,
                    "line_end": 1,
                },
            }
        ],
        "coverage_gaps": [],
        "complete": True,
        "truncated": False,
        "stop_reason": "requested fields covered",
        "child_identity": {"thread_id": _THREAD},
    }


def _stream(
    *,
    payload: dict[str, Any] | None = None,
    tool_name: str = "read_authorized_artifact",
) -> bytes:
    citation = {
        "citation_id": _CITATION,
        "location": {
            "byte_start": 0,
            "byte_end": 5,
            "line_start": 1,
            "line_end": 1,
        },
    }
    events = [
        {"type": "thread.started", "thread_id": _THREAD},
        {
            "type": "item.completed",
            "item": {
                "type": "mcp_tool_call",
                "tool_name": tool_name,
                "result": citation,
            },
        },
        {
            "type": "item.completed",
            "item": {
                "type": "agent_message",
                "text": json.dumps(payload or _payload()),
            },
        },
        {"type": "turn.completed", "usage": {}},
    ]
    return ("\n".join(json.dumps(event) for event in events) + "\n").encode()


def _private_tempdirs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> list[Path]:
    created: list[Path] = []

    def make_directory(*, prefix: str, dir: Path | None = None) -> str:
        del dir
        path = tmp_path / "isolated" / f"{prefix}{len(created)}"
        path.mkdir(parents=True)
        created.append(path)
        return str(path)

    monkeypatch.setattr(launcher.tempfile, "mkdtemp", make_directory)
    return created


def _launch_kwargs(tmp_path: Path) -> dict[str, Any]:
    repository, worktree, common_git = _roots(tmp_path)
    return {
        "prompt": "Extract the requested field",
        "mcp_transport": _transport(),
        "provider_env": {"OPENAI_API_KEY": "provider-secret"},
        "credential_file": None,
        "requested_fields": ("field",),
        "repository_root": repository,
        "worktree_root": worktree,
        "common_git_dir": common_git,
        "expected_scope_digest": _SCOPE,
        "expected_snapshot_digest": _SNAPSHOT,
        "deadline": time.monotonic() + 30,
    }


def _assert_error(code: _ErrorCode, operation: Callable[[], object]) -> None:
    with pytest.raises(EvidenceReaderLaunchError) as raised:
        operation()
    assert raised.value.code == code


def _launch_with_observation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[dict[str, Any], Any, list[Path]]:
    """Run launch_evidence_reader with mocked probes and return (observed, result, created)."""
    definition = _definition()
    invocation = _invocation(tmp_path)
    kwargs = _launch_kwargs(tmp_path)
    repository = kwargs["repository_root"]
    assert isinstance(repository, Path)
    for relative in (".env", ".codex/config.toml", ".codex/skills/skill.md", "agents/a.md"):
        source = repository / relative
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text("source-controlled")
    created = _private_tempdirs(tmp_path, monkeypatch)
    observed: dict[str, Any] = {}
    monkeypatch.setattr(launcher.shutil, "which", lambda _name: "/usr/bin/codex")
    monkeypatch.setattr(launcher.secrets, "token_urlsafe", lambda _size: _CANARY)

    def probe_catalog(
        _codex: str,
        _definition: AgentDef,
        *,
        cwd: Path,
        environment: dict[str, str],
        deadline: float,
    ) -> bytes:
        observed["cwd"] = cwd
        observed["cwd_mode"] = stat.S_IMODE(cwd.stat().st_mode)
        observed["environment"] = dict(environment)
        assert deadline > time.monotonic()
        return b"{}"

    def probe_mcp(
        _codex: str,
        config: bytes,
        *,
        cwd: Path,
        environment: dict[str, str],
        deadline: float,
    ) -> None:
        observed["config"] = config.decode()
        assert cwd == observed["cwd"]
        assert environment == observed["environment"]
        assert deadline > time.monotonic()

    def run_final(
        command: tuple[str, ...] | list[str],
        *,
        cwd: Path,
        environment: dict[str, str],
        deadline: float,
        stdout_limit: int,
    ) -> launcher._ProcessOutput:
        observed["command"] = tuple(command)
        observed["cwd_files"] = tuple(cwd.rglob("*"))
        observed["home_files"] = {
            path.relative_to(Path(environment["HOME"])).as_posix(): stat.S_IMODE(
                path.stat().st_mode
            )
            for path in Path(environment["HOME"]).rglob("*")
        }
        assert cwd == observed["cwd"]
        assert deadline > time.monotonic()
        assert stdout_limit == 2_000_000
        return launcher._ProcessOutput(0, _stream(), b"")

    monkeypatch.setattr(launcher, "_probe_catalog", probe_catalog)
    monkeypatch.setattr(launcher, "_probe_mcp", probe_mcp)
    monkeypatch.setattr(launcher, "_probe_conformance", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        launcher,
        "_probe_cli_version",
        lambda *args, **kwargs: "codex-cli 0.147.0",
    )
    monkeypatch.setattr(launcher, "_run_bounded", run_final)
    result = launch_evidence_reader(definition, invocation, **kwargs)
    return observed, result, created


def test_launch_filters_environment_to_sterile_minimum(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    observed, _, _ = _launch_with_observation(tmp_path, monkeypatch)
    environment = observed["environment"]
    expected_environment = {
        "OPENAI_API_KEY",
        *_EVIDENCE_ENV,
        "HOME",
        "CODEX_HOME",
        "CODEX_SQLITE_HOME",
        "LANG",
        "LC_ALL",
    }
    assert set(environment) == expected_environment
    assert environment["HOME"] == environment["CODEX_HOME"] == environment["CODEX_SQLITE_HOME"]


def test_launch_uses_sterile_cwd_with_0o700_permissions_and_empty_contents(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    observed, _, _ = _launch_with_observation(tmp_path, monkeypatch)
    assert observed["cwd_mode"] == 0o700
    assert observed["cwd_files"] == ()


def test_launch_writes_sterile_0o600_home_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    observed, _, _ = _launch_with_observation(tmp_path, monkeypatch)
    assert observed["home_files"] == {
        "config.toml": 0o600,
        "evidence-reader-probe.schema.json": 0o600,
        "evidence-reader-result.schema.json": 0o600,
        "models.json": 0o600,
    }


_REQUIRED_CONFIG_FRAGMENTS = (
    'approval_policy = "never"',
    'sandbox_mode = "read-only"',
    'forced_login_method = "api"',
    'inherit = "none"',
    "project_root_markers = []",
    "[agents]\nenabled = false",
    "shell_tool = false",
)
_READER_TOOLS_IN_CONFIG = ("get_authorized_artifact_page", "read_authorized_artifact")
_DANGEROUS_TOOLS_NOT_IN_CONFIG = ("run_cmd", "read_file")
_REQUIRED_COMMAND_FLAGS = (
    "--strict-config",
    "--ignore-rules",
    "--ephemeral",
    "--skip-git-repo-check",
    "--output-schema",
    "-C",
    "exec",
    "--json",
)
_DANGEROUS_FLAGS_NOT_IN_COMMAND = ("--add-dir", "--dangerously-bypass-hook-trust")


def _assert_all_in(haystack: str | tuple[str, ...], needles: tuple[str, ...]) -> None:
    missing = [n for n in needles if n not in haystack]
    assert not missing, f"missing from haystack: {missing}"


def _assert_none_in(haystack: str | tuple[str, ...], needles: tuple[str, ...]) -> None:
    present = [n for n in needles if n in haystack]
    assert not present, f"unexpectedly present in haystack: {present}"


def test_launch_renders_sterile_codex_config_with_required_content_and_excluded_tools(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    observed, _, _ = _launch_with_observation(tmp_path, monkeypatch)
    config = observed["config"]
    _assert_all_in(config, _REQUIRED_CONFIG_FRAGMENTS)
    _assert_all_in(config, _READER_TOOLS_IN_CONFIG)
    _assert_none_in(config, _DANGEROUS_TOOLS_NOT_IN_CONFIG)


def test_launch_builds_codex_command_with_required_flags_and_excluded_flags(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    observed, _, _ = _launch_with_observation(tmp_path, monkeypatch)
    command = observed["command"]
    assert command[0] == "/usr/bin/codex"
    _assert_all_in(command, _REQUIRED_COMMAND_FLAGS)
    _assert_none_in(command, _DANGEROUS_FLAGS_NOT_IN_COMMAND)
    prompt = command[-1]
    _assert_all_in(prompt, (_CANARY, _SCOPE, _SNAPSHOT))


def test_launch_propagates_thread_id_and_citations_to_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, result, _ = _launch_with_observation(tmp_path, monkeypatch)
    assert result.thread_id == _THREAD
    assert result.citations[0].citation_id == _CITATION


def test_launch_removes_sterile_tempdirs_after_completion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, _, created = _launch_with_observation(tmp_path, monkeypatch)
    assert all(not path.exists() for path in created)


def test_launch_preserves_cwd_and_environment_across_probes_and_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cross-call invariant: cwd and environment seen by probe_catalog must equal
    those seen by probe_mcp and run_bounded. Captured by the helper's probe_mcp
    and run_final mocks (which assert cwd == observed['cwd'] and environment ==
    observed['environment']). Make one explicit call here to lock the invariant."""
    observed, _, _ = _launch_with_observation(tmp_path, monkeypatch)
    assert "cwd" in observed
    assert "environment" in observed
    forward = observed["environment"]
    assert "HOME" in forward
    assert forward["HOME"] == str(observed["cwd"].parent) or forward["HOME"].startswith("/")


def test_launch_rejects_isolated_cwd_overlap_with_authorized_roots(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    definition = _definition()
    invocation = _invocation(tmp_path)
    kwargs = _launch_kwargs(tmp_path)
    worktree = kwargs["worktree_root"]
    assert isinstance(worktree, Path)
    home = tmp_path / "safe-home"
    calls = 0

    def overlapping_directory(*, prefix: str, dir: Path | None = None) -> str:
        del dir
        nonlocal calls
        calls += 1
        path = home if calls == 1 else worktree
        path.mkdir(exist_ok=True)
        return str(path)

    monkeypatch.setattr(launcher.tempfile, "mkdtemp", overlapping_directory)
    monkeypatch.setattr(launcher.shutil, "which", lambda _name: "/usr/bin/codex")

    _assert_error(
        _ErrorCode.ISOLATION_INVALID,
        lambda: launch_evidence_reader(definition, invocation, **kwargs),
    )
    assert not home.exists()


@pytest.mark.parametrize(
    ("output", "code", "max_result_bytes"),
    [
        (b"not-json\n", _ErrorCode.STREAM_INVALID, 256_000),
        (
            b'{"type":"unexpected.event"}\n',
            _ErrorCode.STREAM_SHAPE_FORBIDDEN,
            256_000,
        ),
        (
            _stream(tool_name="run_cmd"),
            _ErrorCode.TOOL_NOT_AUTHORIZED,
            256_000,
        ),
        (
            _stream(payload=_payload(scope="sha256:wrong")),
            _ErrorCode.RESULT_SCHEMA_INVALID,
            256_000,
        ),
        (_stream(), _ErrorCode.RESULT_LIMIT_EXCEEDED, 10),
        (
            b'{"type":"thread.started","thread_id":"thread-reader"}\n',
            _ErrorCode.TERMINAL_RESULT_INVALID,
            256_000,
        ),
    ],
)
def test_stream_validation_rejects_malformed_unknown_unauthorized_oversized_incomplete_or_unbound(
    output: bytes,
    code: _ErrorCode,
    max_result_bytes: int,
) -> None:
    _assert_error(
        code,
        lambda: launcher._validate_stream(
            output,
            definition=_definition(),
            allowed_tools=("get_authorized_artifact_page", "read_authorized_artifact"),
            canary=_CANARY,
            scope=_SCOPE,
            snapshot=_SNAPSHOT,
            requested_fields=("field",),
            max_result_bytes=max_result_bytes,
        ),
    )


@pytest.mark.parametrize("item_type", ["command_execution", "file_change"])
def test_stream_validation_rejects_commands_and_file_changes(item_type: str) -> None:
    events = [
        {"type": "thread.started", "thread_id": _THREAD},
        {"type": "item.completed", "item": {"type": item_type}},
        {"type": "turn.completed", "usage": {}},
    ]
    output = ("\n".join(json.dumps(event) for event in events) + "\n").encode()

    _assert_error(
        _ErrorCode.FORBIDDEN_OPERATION,
        lambda: launcher._validate_stream(
            output,
            definition=_definition(),
            allowed_tools=("get_authorized_artifact_page", "read_authorized_artifact"),
            canary=_CANARY,
            scope=_SCOPE,
            snapshot=_SNAPSHOT,
            requested_fields=("field",),
            max_result_bytes=256_000,
        ),
    )


@pytest.mark.parametrize(
    ("status", "evidence", "gaps", "complete", "truncated", "stop_reason", "fields"),
    [
        (
            "partial",
            _payload()["evidence"],
            [{"field": "missing", "reason": "artifact exhausted"}],
            True,
            False,
            "artifact exhausted",
            ("field", "missing"),
        ),
        (
            "blocked",
            [],
            [{"field": "field", "reason": "broker unavailable"}],
            True,
            False,
            "concrete blocker",
            ("field",),
        ),
    ],
)
def test_stream_validation_accepts_exact_partial_and_blocked_partitions(
    status: str,
    evidence: list[dict[str, Any]],
    gaps: list[dict[str, str]],
    complete: bool,
    truncated: bool,
    stop_reason: str,
    fields: tuple[str, ...],
) -> None:
    payload = _payload()
    payload.update(
        status=status,
        evidence=evidence,
        coverage_gaps=gaps,
        complete=complete,
        truncated=truncated,
        stop_reason=stop_reason,
    )
    result = launcher._validate_stream(
        _stream(payload=payload),
        definition=_definition(),
        allowed_tools=("get_authorized_artifact_page", "read_authorized_artifact"),
        canary=_CANARY,
        scope=_SCOPE,
        snapshot=_SNAPSHOT,
        requested_fields=fields,
        max_result_bytes=256_000,
    )
    assert result.status.value == status


def test_stream_validation_rejects_unissued_receipt_even_with_one_observed_receipt() -> None:
    payload = _payload()
    payload["evidence"] = [
        {
            **payload["evidence"][0],
            "field": field,
            "citation_id": citation_id,
            "location": {
                "byte_start": start,
                "byte_end": end,
                "line_start": 1,
                "line_end": 1,
            },
        }
        for field, citation_id, start, end in (
            ("first", _CITATION, 0, 5),
            ("second", "model-copy-error", 1, 4),
        )
    ]

    with pytest.raises(EvidenceReaderLaunchError, match="citation_invalid"):
        launcher._validate_stream(
            _stream(payload=payload),
            definition=_definition(),
            allowed_tools=("get_authorized_artifact_page", "read_authorized_artifact"),
            canary=_CANARY,
            scope=_SCOPE,
            snapshot=_SNAPSHOT,
            requested_fields=("first", "second"),
            max_result_bytes=256_000,
        )


@pytest.mark.parametrize(
    ("limit", "value"),
    [
        ("max_stream_bytes", True),
        ("max_stream_bytes", 0),
        ("max_stream_bytes", launcher._MAX_STREAM_BYTES + 1),
        ("max_result_bytes", True),
        ("max_result_bytes", 0),
        ("max_result_bytes", launcher._MAX_RESULT_BYTES + 1),
    ],
)
def test_launch_limits_reject_invalid_int_values(
    tmp_path: Path, limit: str, value: object
) -> None:
    kwargs = _launch_kwargs(tmp_path)
    kwargs[limit] = value  # type: ignore[assignment]
    with pytest.raises(EvidenceReaderLaunchError) as raised:
        launch_evidence_reader(_definition(), _invocation(tmp_path), **kwargs)
    assert raised.value.code == "launch_limits_invalid"


@pytest.mark.parametrize(
    "mutate",
    [
        lambda payload: payload["coverage_gaps"].append(
            {"field": "field", "reason": "duplicate partition"}
        ),
        lambda payload: payload.update(status="partial"),
        lambda payload: payload.update(truncated=True),
        lambda payload: payload.update(stop_reason="concrete blocker"),
    ],
)
def test_stream_validation_rejects_inconsistent_semantic_states(
    mutate: Callable[[dict[str, object]], object],
) -> None:
    payload = _payload()
    mutate(payload)
    with pytest.raises(EvidenceReaderLaunchError) as raised:
        launcher._validate_stream(
            _stream(payload=payload),
            definition=_definition(),
            allowed_tools=("get_authorized_artifact_page", "read_authorized_artifact"),
            canary=_CANARY,
            scope=_SCOPE,
            snapshot=_SNAPSHOT,
            requested_fields=("field",),
            max_result_bytes=256_000,
        )
    assert raised.value.code in {"result_partition_invalid", "result_state_invalid"}


def test_stream_validation_requires_initial_read_before_continuation() -> None:
    with pytest.raises(EvidenceReaderLaunchError) as raised:
        launcher._validate_stream(
            _stream(tool_name="get_authorized_artifact_page"),
            definition=_definition(),
            allowed_tools=("get_authorized_artifact_page", "read_authorized_artifact"),
            canary=_CANARY,
            scope=_SCOPE,
            snapshot=_SNAPSHOT,
            requested_fields=("field",),
            max_result_bytes=256_000,
        )
    assert raised.value.code == "mcp_call_sequence_invalid"


def test_stream_validation_allows_recovery_after_failed_continuation() -> None:
    citation = {
        "citation_id": _CITATION,
        "location": {"byte_start": 0, "byte_end": 5, "line_start": 1, "line_end": 1},
    }

    def call(tool: str, result: dict[str, Any]) -> dict[str, Any]:
        return {
            "type": "item.completed",
            "item": {"type": "mcp_tool_call", "tool_name": tool, "result": result},
        }

    events = [
        {"type": "thread.started", "thread_id": _THREAD},
        call("read_authorized_artifact", citation),
        call(
            "get_authorized_artifact_page",
            {"status": "error", "code": "continuation_invalid"},
        ),
        call("read_authorized_artifact", citation),
        {
            "type": "item.completed",
            "item": {"type": "agent_message", "text": json.dumps(_payload())},
        },
        {"type": "turn.completed", "usage": {}},
    ]

    result = launcher._validate_stream(
        ("\n".join(json.dumps(event) for event in events) + "\n").encode(),
        definition=_definition(),
        allowed_tools=("get_authorized_artifact_page", "read_authorized_artifact"),
        canary=_CANARY,
        scope=_SCOPE,
        snapshot=_SNAPSHOT,
        requested_fields=("field",),
        max_result_bytes=256_000,
    )

    assert result.status is EvidenceReaderResultStatus.ANSWERED


def test_stream_validation_rejects_conflicting_citation_observations() -> None:
    events = [
        {"type": "thread.started", "thread_id": _THREAD},
        {
            "type": "item.completed",
            "item": {
                "type": "mcp_tool_call",
                "tool_name": "read_authorized_artifact",
                "result": {
                    "citation_id": _CITATION,
                    "location": {
                        "byte_start": 0,
                        "byte_end": 5,
                        "line_start": 1,
                        "line_end": 1,
                    },
                },
            },
        },
        {
            "type": "item.completed",
            "item": {
                "type": "mcp_tool_call",
                "tool_name": "get_authorized_artifact_page",
                "result": {
                    "citation_id": _CITATION,
                    "location": {
                        "byte_start": 1,
                        "byte_end": 5,
                        "line_start": 1,
                        "line_end": 1,
                    },
                },
            },
        },
        {
            "type": "item.completed",
            "item": {"type": "agent_message", "text": json.dumps(_payload())},
        },
        {"type": "turn.completed", "usage": {}},
    ]
    with pytest.raises(EvidenceReaderLaunchError) as raised:
        launcher._validate_stream(
            ("\n".join(json.dumps(event) for event in events) + "\n").encode(),
            definition=_definition(),
            allowed_tools=("get_authorized_artifact_page", "read_authorized_artifact"),
            canary=_CANARY,
            scope=_SCOPE,
            snapshot=_SNAPSHOT,
            requested_fields=("field",),
            max_result_bytes=256_000,
        )
    assert raised.value.code == "citation_invalid"


@pytest.mark.parametrize("failure", ["preflight", "spawn", "timeout", "cancel"])
def test_launch_failure_paths_remove_sterile_home_and_cwd(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    definition = _definition()
    invocation = _invocation(tmp_path)
    kwargs = _launch_kwargs(tmp_path)
    created = _private_tempdirs(tmp_path, monkeypatch)
    monkeypatch.setattr(launcher.shutil, "which", lambda _name: "/usr/bin/codex")

    class Cancelled(BaseException):
        pass

    def fail_launch(*args: object, **kwargs: object) -> bytes:
        if failure == "cancel":
            raise Cancelled()
        code = "deadline_exceeded" if failure == "timeout" else "codex_unavailable"
        raise EvidenceReaderLaunchError(code)

    if failure == "preflight":
        monkeypatch.setattr(launcher, "_probe_catalog", fail_launch)
    else:
        monkeypatch.setattr(launcher, "_probe_catalog", lambda *args, **kwargs: b"{}")
        monkeypatch.setattr(launcher, "_probe_mcp", lambda *args, **kwargs: None)
        monkeypatch.setattr(launcher, "_probe_conformance", lambda *args, **kwargs: None)
        monkeypatch.setattr(launcher, "_run_bounded", fail_launch)

    expected = Cancelled if failure == "cancel" else EvidenceReaderLaunchError
    with pytest.raises(expected):
        launch_evidence_reader(definition, invocation, **kwargs)
    assert len(created) == 2
    assert all(not path.exists() for path in created)


def test_launch_fails_closed_when_sterile_home_removal_is_incomplete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    definition = _definition()
    invocation = _invocation(tmp_path)
    kwargs = _launch_kwargs(tmp_path)
    created = _private_tempdirs(tmp_path, monkeypatch)
    monkeypatch.setattr(launcher.shutil, "which", lambda _name: "/usr/bin/codex")
    monkeypatch.setattr(launcher, "_probe_catalog", lambda *args, **kwargs: b"{}")
    monkeypatch.setattr(launcher, "_probe_mcp", lambda *args, **kwargs: None)
    monkeypatch.setattr(launcher, "_probe_conformance", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        launcher,
        "_run_bounded",
        lambda *args, **kwargs: launcher._ProcessOutput(0, _stream(), b""),
    )
    original_remove = launcher._remove_directory

    def incomplete_home_removal(path: Path) -> None:
        if path == created[0]:
            raise EvidenceReaderLaunchError("cleanup_incomplete")
        original_remove(path)

    monkeypatch.setattr(launcher, "_remove_directory", incomplete_home_removal)

    _assert_error(
        _ErrorCode.CLEANUP_INCOMPLETE,
        lambda: launch_evidence_reader(definition, invocation, **kwargs),
    )
    assert not created[1].exists()


@pytest.mark.parametrize("failure", ["timeout", "cancel"])
def test_bounded_process_timeout_and_cancellation_settle_owned_process(
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    stdout_read, stdout_write = os.pipe()
    stderr_read, stderr_write = os.pipe()
    try:
        os.close(stdout_write)
        os.close(stderr_write)
        stdout_file = os.fdopen(stdout_read, "rb", buffering=0)
        stderr_file = os.fdopen(stderr_read, "rb", buffering=0)
    except BaseException:
        os.close(stdout_read)
        os.close(stderr_read)
        raise
    process = SimpleNamespace(stdout=stdout_file, stderr=stderr_file)

    class Cancelled(BaseException):
        pass

    failure_exception: BaseException = (
        EvidenceReaderLaunchError("deadline_exceeded") if failure == "timeout" else Cancelled()
    )
    owner = SimpleNamespace(process=process, preserved=None)
    owner.observe_exit = lambda: None

    def settle_preserving(exc: BaseException, *, timeout: float) -> SimpleNamespace:
        owner.preserved = exc
        assert timeout >= 0
        return SimpleNamespace(complete=True)

    owner.settle_preserving = settle_preserving
    monkeypatch.setattr(launcher, "spawn_owned_process", lambda *args, **kwargs: owner)

    def interrupt_deadline(_deadline: float) -> float:
        raise failure_exception

    monkeypatch.setattr(launcher, "_deadline_remaining", interrupt_deadline)

    with pytest.raises(type(failure_exception)):
        launcher._run_bounded(
            ("/usr/bin/codex", "exec"),
            cwd=Path.cwd(),
            environment={},
            deadline=time.monotonic() + 5,
            stdout_limit=100,
        )
    try:
        assert owner.preserved is failure_exception
    finally:
        stdout_file.close()
        stderr_file.close()


def test_bounded_process_fails_when_owned_process_cleanup_is_incomplete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stdout_read, stdout_write = os.pipe()
    stderr_read, stderr_write = os.pipe()
    os.close(stdout_write)
    os.close(stderr_write)
    stdout_file = os.fdopen(stdout_read, "rb", buffering=0)
    stderr_file = os.fdopen(stderr_read, "rb", buffering=0)
    try:
        process = SimpleNamespace(stdout=stdout_file, stderr=stderr_file)
        owner = SimpleNamespace(process=process, observe_exit=lambda: None)
        owner.settle_preserving = lambda exc, timeout=2.0: SimpleNamespace(complete=False)
        monkeypatch.setattr(launcher, "spawn_owned_process", lambda *args, **kwargs: owner)

        def expire(_deadline: float) -> float:
            raise EvidenceReaderLaunchError("deadline_exceeded")

        monkeypatch.setattr(launcher, "_deadline_remaining", expire)

        _assert_error(
            _ErrorCode.PROCESS_CLEANUP_INCOMPLETE,
            lambda: launcher._run_bounded(
                ("/usr/bin/codex", "exec"),
                cwd=Path.cwd(),
                environment={},
                deadline=time.monotonic() + 5,
                stdout_limit=100,
            ),
        )
    finally:
        stdout_file.close()
        stderr_file.close()
