from __future__ import annotations

import asyncio
import hashlib
import json
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from enum import StrEnum
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import autoskillit.server.tools.tools_evidence_reader as delegate_module
from autoskillit.core import SessionType, agent_definition_digest
from autoskillit.execution import CodexBackend
from autoskillit.execution.evidence_reader import (
    EvidenceCitation,
    EvidenceReaderLaunchError,
    EvidenceReaderLaunchResult,
    EvidenceReaderResultStatus,
)
from autoskillit.exploration import StableArtifactCapture
from autoskillit.server.tools._evidence_reader import (
    EvidenceReaderInvocation,
    create_evidence_reader_invocation,
    read_bound_evidence_reader_page,
)

pytestmark = [pytest.mark.layer("server"), pytest.mark.medium]


class _ErrorCode(StrEnum):
    READER_CLEANUP_FAILED = "reader_cleanup_failed"
    READER_REQUEST_INVALID = "reader_request_invalid"
    READER_ROLE_UNAVAILABLE = "reader_role_unavailable"


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


def _repository(tmp_path: Path, state: str) -> tuple[Path, str, bytes]:
    root = tmp_path / "repository"
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "reader@example.test")
    _git(root, "config", "user.name", "Reader Test")
    tracked = root / "tracked.txt"
    tracked.write_bytes(b"committed\n")
    _git(root, "add", "tracked.txt")
    _git(root, "commit", "-qm", "initial")
    if state == "untracked":
        artifact_path = "untracked.txt"
        expected = b"untracked-current\n"
    else:
        artifact_path = "tracked.txt"
        expected = f"{state}-current\n".encode()
    (root / artifact_path).write_bytes(expected)
    if state == "staged":
        _git(root, "add", artifact_path)
    return root, artifact_path, expected


def _context(tmp_path: Path) -> SimpleNamespace:
    temp_dir = tmp_path / "runtime"
    temp_dir.mkdir()
    return SimpleNamespace(temp_dir=temp_dir)


def _capture_value(tmp_path: Path, content: bytes = b"current\n") -> StableArtifactCapture:
    repository = tmp_path / "repository"
    repository.mkdir(exist_ok=True)
    return StableArtifactCapture(
        repository_root=repository,
        repository_identity_digest="a" * 64,
        revision="b" * 40,
        artifact_path="artifact.txt",
        content=content,
        content_digest=f"sha256:{hashlib.sha256(content).hexdigest()}",
        size=len(content),
        mode=0o644,
        index_records=(),
        snapshot_digest="sha256:snapshot",
    )


def test_static_reader_role_uses_the_single_bundled_definition_and_digest() -> None:
    definition, bare_tools, definition_digest = delegate_module._reader_definition(
        "pr-source-reader"
    )

    assert definition.name == "pr-source-reader"
    assert definition.codex.model == "gpt-5.6-luna"
    assert bare_tools == ("get_authorized_artifact_page", "read_authorized_artifact")
    assert definition_digest == agent_definition_digest(definition)
    with pytest.raises(delegate_module._DelegateError) as ineligible:
        delegate_module._reader_definition("pr-synthesizer")
    assert ineligible.value.code == _ErrorCode.READER_ROLE_UNAVAILABLE


@pytest.mark.parametrize(
    "role_data",
    [
        {},
        {"artifact_path": "file.txt", "requested_fields": []},
        {"artifact_path": "/absolute.txt", "requested_fields": ["field"], "cwd": "/tmp"},
        {"artifact_path": "file.txt", "requested_fields": ["field"], "model": "other"},
        {"artifact_path": "file.txt", "requested_fields": ["field"], "env": {}},
        {"artifact_path": "file.txt", "requested_fields": ["field"], "tools": []},
        {
            "artifact_path": "file.txt",
            "requested_fields": ["field"],
            "caller_session_id": "attacker-session",
        },
        {
            "artifact_path": "file.txt",
            "requested_fields": ["field"],
            "repository_root": "/tmp",
        },
        {"artifact_path": "file.txt", "requested_fields": ["field", "field"]},
        {"artifact_path": "file.txt", "requested_fields": ["bad field"]},
        {"artifact_path": "file.txt", "requested_fields": ["x"] * 33},
    ],
)
def test_delegate_request_schema_rejects_malformed_and_override_inputs(
    role_data: dict[str, object],
) -> None:
    with pytest.raises(delegate_module._DelegateError) as raised:
        delegate_module._role_request("pr-source-reader", role_data)
    assert raised.value.code == _ErrorCode.READER_REQUEST_INVALID


def test_delegate_admission_requires_exact_headless_codex_skill_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = CodexBackend()
    tool_ctx = SimpleNamespace(
        config=SimpleNamespace(agent_backend=SimpleNamespace(backend="codex")),
        backend=backend,
    )
    monkeypatch.setenv("AUTOSKILLIT_HEADLESS", "1")
    monkeypatch.setattr(delegate_module, "session_type", lambda: SessionType.SKILL)

    assert (
        delegate_module._delegate_caller_session(
            SimpleNamespace(session_id="trusted-parent"), tool_ctx
        )
        == "trusted-parent"
    )

    for denied in (
        SimpleNamespace(session_id=""),
        SimpleNamespace(session_id="direct:untrusted"),
    ):
        with pytest.raises(delegate_module._DelegateError, match="caller_session_unavailable"):
            delegate_module._delegate_caller_session(denied, tool_ctx)

    monkeypatch.setattr(delegate_module, "session_type", lambda: SessionType.ORCHESTRATOR)
    with pytest.raises(delegate_module._DelegateError, match="reader_admission_denied"):
        delegate_module._delegate_caller_session(
            SimpleNamespace(session_id="trusted-parent"), tool_ctx
        )


@pytest.mark.parametrize(
    ("code", "status"),
    [
        ("cli_probe_failed", "unsupported"),
        ("result_schema_invalid", "rejected"),
        ("artifact_stale", "rejected"),
        ("process_cleanup_incomplete", "failed"),
        ("deadline_exceeded", "timeout"),
        ("reader_cancelled", "cancelled"),
    ],
)
def test_delegate_errors_use_terminal_domain_outcomes(code: str, status: str) -> None:
    assert json.loads(delegate_module._delegate_error_outcome(code)) == {
        "status": status,
        "code": code,
    }


@pytest.mark.parametrize("state", ["dirty", "staged", "untracked"])
def test_delegate_real_capture_authority_receipt_and_terminal_recapture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    state: str,
) -> None:
    root, artifact_path, expected = _repository(tmp_path, state)
    context = _context(tmp_path)
    common_git = (root / ".git").resolve()
    monkeypatch.setattr(
        delegate_module,
        "_trusted_repository",
        lambda _ctx: (root.resolve(), root.resolve(), common_git),
    )
    monkeypatch.setattr(delegate_module, "_reader_transport", lambda _ctx: {"transport": True})
    monkeypatch.setattr(
        delegate_module,
        "evidence_reader_provider_environment",
        lambda: {"OPENAI_API_KEY": "provider-secret"},
    )
    invocation_dirs: list[Path] = []
    real_create = create_evidence_reader_invocation

    def track_invocation(*args: Any, **kwargs: Any) -> EvidenceReaderInvocation:
        invocation = real_create(*args, **kwargs)
        invocation_dirs.append(invocation.invocation_dir)
        return invocation

    monkeypatch.setattr(delegate_module, "create_evidence_reader_invocation", track_invocation)

    def launch_with_real_receipt(
        definition: Any,
        invocation: EvidenceReaderInvocation,
        **kwargs: Any,
    ) -> EvidenceReaderLaunchResult:
        prompt = json.loads(kwargs["prompt"])
        assert prompt == {
            "artifact_path": artifact_path,
            "requested_fields": ["title", "body"],
        }
        page = read_bound_evidence_reader_page(
            context,
            dict(invocation.environment),
            canonical_tool=definition.reader_tools[1],
            page_size=64_000,
            continuation=None,
            deadline=time.monotonic() + 5,
        )
        payload = {
            "canary": "private",
            "complete": True,
            "truncated": False,
            "observed": page.content,
        }
        citation = EvidenceCitation(
            page.citation_id,
            page.byte_start,
            page.byte_end,
            page.line_start,
            page.line_end,
        )
        return EvidenceReaderLaunchResult(
            EvidenceReaderResultStatus.ANSWERED,
            definition.name,
            kwargs["expected_scope_digest"],
            kwargs["expected_snapshot_digest"],
            "thread-reader",
            (citation,),
            json.dumps(payload),
        )

    monkeypatch.setattr(delegate_module, "launch_evidence_reader", launch_with_real_receipt)

    raw = delegate_module._delegate_sync(
        context,
        caller_session_id="trusted-session",
        role="pr-source-reader",
        artifact_path=artifact_path,
        requested_fields=("title", "body"),
    )

    response = json.loads(raw)
    assert response["status"] == "answered"
    assert response["artifact_path"] == artifact_path
    assert response["result"]["observed"].encode() == expected
    assert "canary" not in response["result"]
    assert invocation_dirs and all(not path.exists() for path in invocation_dirs)


@pytest.mark.parametrize(
    "failure",
    [
        "preflight",
        "spawn",
        "runtime",
        "timeout",
        "cancel",
        "recapture",
        "malformed-result",
        "oversized-result",
        "incomplete-result",
        "receipt-mismatch",
    ],
)
def test_delegate_failures_revoke_authority_and_remove_invocation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    context = _context(tmp_path)
    capture = _capture_value(tmp_path)
    trusted = capture.repository_root.resolve()
    git_dir = tmp_path / "git-dir"
    git_dir.mkdir()
    monkeypatch.setattr(
        delegate_module,
        "_trusted_repository",
        lambda _ctx: (trusted, trusted, git_dir),
    )
    captures = 0

    def capture_for_attempt(*args: Any, **kwargs: Any) -> StableArtifactCapture:
        nonlocal captures
        captures += 1
        if failure == "recapture" and captures > 1:
            raise RuntimeError("terminal mutation")
        return capture

    monkeypatch.setattr(delegate_module, "capture_stable_artifact", capture_for_attempt)
    monkeypatch.setattr(
        delegate_module,
        "evidence_reader_provider_environment",
        lambda: {"OPENAI_API_KEY": "provider-secret"},
    )
    invocation_dirs: list[Path] = []
    real_create = create_evidence_reader_invocation

    def track_invocation(*args: Any, **kwargs: Any) -> EvidenceReaderInvocation:
        invocation = real_create(*args, **kwargs)
        invocation_dirs.append(invocation.invocation_dir)
        return invocation

    monkeypatch.setattr(delegate_module, "create_evidence_reader_invocation", track_invocation)
    if failure == "preflight":
        monkeypatch.setattr(
            delegate_module,
            "_reader_transport",
            lambda _ctx: (_ for _ in ()).throw(delegate_module._DelegateError("preflight")),
        )
    else:
        monkeypatch.setattr(delegate_module, "_reader_transport", lambda _ctx: {})

    class Cancelled(BaseException):
        pass

    def fail_or_return(*args: Any, **kwargs: Any) -> EvidenceReaderLaunchResult:
        if failure == "cancel":
            raise Cancelled()
        if failure in {"spawn", "runtime", "timeout", "oversized-result"}:
            raise EvidenceReaderLaunchError(failure)
        if failure == "malformed-result":
            payload_json = "not-json"
            status = EvidenceReaderResultStatus.ANSWERED
        elif failure == "incomplete-result":
            payload_json = json.dumps({"complete": False, "truncated": False})
            status = EvidenceReaderResultStatus.PARTIAL
        else:
            payload_json = json.dumps({"complete": True, "truncated": False})
            status = EvidenceReaderResultStatus.ANSWERED
        citation = EvidenceCitation("missing-receipt", 0, 1, 1, 1)
        return EvidenceReaderLaunchResult(
            status,
            "pr-source-reader",
            "scope",
            capture.snapshot_digest,
            "thread",
            (citation,),
            payload_json,
        )

    monkeypatch.setattr(delegate_module, "launch_evidence_reader", fail_or_return)

    if failure == "cancel":
        expected_exception: type[BaseException] = Cancelled
    elif failure == "preflight":
        expected_exception = delegate_module._DelegateError
    elif failure == "recapture":
        expected_exception = delegate_module._DelegateError
    elif failure in {"malformed-result", "incomplete-result", "receipt-mismatch"}:
        expected_exception = delegate_module._DelegateError
    else:
        expected_exception = EvidenceReaderLaunchError
    with pytest.raises(expected_exception) as raised:
        delegate_module._delegate_sync(
            context,
            caller_session_id="session",
            role="pr-source-reader",
            artifact_path=capture.artifact_path,
            requested_fields=("field",),
        )
    if failure == "recapture":
        assert getattr(raised.value, "code", None) == "artifact_unsupported"
    assert invocation_dirs and all(not path.exists() for path in invocation_dirs)


def test_cleanup_failure_overrides_otherwise_successful_delegate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context = _context(tmp_path)
    capture = _capture_value(tmp_path)
    trusted = capture.repository_root.resolve()
    git_dir = tmp_path / "git-dir"
    git_dir.mkdir()
    monkeypatch.setattr(
        delegate_module,
        "_trusted_repository",
        lambda _ctx: (trusted, trusted, git_dir),
    )
    monkeypatch.setattr(
        delegate_module, "capture_stable_artifact", lambda *args, **kwargs: capture
    )
    monkeypatch.setattr(delegate_module, "_reader_transport", lambda _ctx: {})
    monkeypatch.setattr(
        delegate_module,
        "evidence_reader_provider_environment",
        lambda: {"OPENAI_API_KEY": "provider-secret"},
    )
    monkeypatch.setattr(
        delegate_module,
        "launch_evidence_reader",
        lambda *args, **kwargs: EvidenceReaderLaunchResult(
            EvidenceReaderResultStatus.ANSWERED,
            "pr-source-reader",
            "scope",
            capture.snapshot_digest,
            "thread",
            (),
            "{}",
        ),
    )
    monkeypatch.setattr(delegate_module, "_validate_child", lambda *args, **kwargs: {"ok": True})
    monkeypatch.setattr(
        delegate_module,
        "revoke_evidence_reader_invocation",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("cleanup failed")),
    )
    monkeypatch.setattr(delegate_module, "stable_artifact_matches", lambda _a, _b: True)

    with pytest.raises(delegate_module._DelegateError) as raised:
        delegate_module._delegate_sync(
            context,
            caller_session_id="session",
            role="pr-source-reader",
            artifact_path=capture.artifact_path,
            requested_fields=("field",),
        )
    assert raised.value.code == _ErrorCode.READER_CLEANUP_FAILED


@pytest.mark.anyio
async def test_concurrent_same_session_delegates_remain_independent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        delegate_module,
        "_delegate_caller_session",
        lambda _ctx, _tool_ctx: "shared-session",
    )
    monkeypatch.setattr(delegate_module, "_get_tool_context", lambda: object())
    barrier = threading.Barrier(2)
    observed: list[tuple[str, str]] = []

    def delegate_independently(
        _ctx: object,
        *,
        caller_session_id: str,
        artifact_path: str,
        **_kwargs: object,
    ) -> str:
        observed.append((caller_session_id, artifact_path))
        barrier.wait(timeout=5)
        return json.dumps({"status": "answered", "artifact_path": artifact_path})

    monkeypatch.setattr(delegate_module, "_delegate_sync", delegate_independently)

    async def request(path: str) -> str:
        return await delegate_module.delegate_evidence_reader(
            "pr-source-reader",
            {"artifact_path": path, "requested_fields": ["field"]},
            ctx=SimpleNamespace(session_id="untrusted-override"),
        )

    first, second = await asyncio.gather(request("first.txt"), request("second.txt"))

    assert {json.loads(first)["artifact_path"], json.loads(second)["artifact_path"]} == {
        "first.txt",
        "second.txt",
    }
    assert {session for session, _artifact in observed} == {"shared-session"}
    assert {artifact for _session, artifact in observed} == {"first.txt", "second.txt"}


def test_concurrent_real_authorities_isolate_stale_and_successful_delegates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "repository"
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "reader@example.test")
    _git(root, "config", "user.name", "Reader Test")
    for name in ("first.txt", "second.txt"):
        (root / name).write_text(f"{name}-current\n")
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "initial")
    context = _context(tmp_path)
    common_git = (root / ".git").resolve()
    monkeypatch.setattr(
        delegate_module,
        "_trusted_repository",
        lambda _ctx: (root.resolve(), root.resolve(), common_git),
    )
    monkeypatch.setattr(delegate_module, "_reader_transport", lambda _ctx: {"transport": True})
    monkeypatch.setattr(
        delegate_module,
        "evidence_reader_provider_environment",
        lambda: {"OPENAI_API_KEY": "provider-secret"},
    )
    invocation_dirs: list[Path] = []
    real_create = create_evidence_reader_invocation

    def track_invocation(*args: Any, **kwargs: Any) -> EvidenceReaderInvocation:
        invocation = real_create(*args, **kwargs)
        invocation_dirs.append(invocation.invocation_dir)
        return invocation

    monkeypatch.setattr(delegate_module, "create_evidence_reader_invocation", track_invocation)
    barrier = threading.Barrier(2)

    def launch_with_real_authority(
        definition: Any,
        invocation: EvidenceReaderInvocation,
        **kwargs: Any,
    ) -> EvidenceReaderLaunchResult:
        artifact_path = json.loads(kwargs["prompt"])["artifact_path"]
        page = read_bound_evidence_reader_page(
            context,
            dict(invocation.environment),
            canonical_tool=definition.reader_tools[1],
            page_size=64_000,
            continuation=None,
            deadline=time.monotonic() + 5,
        )
        barrier.wait(timeout=5)
        if artifact_path == "first.txt":
            (root / artifact_path).write_text("became-stale\n")
        citation = EvidenceCitation(
            page.citation_id,
            page.byte_start,
            page.byte_end,
            page.line_start,
            page.line_end,
        )
        return EvidenceReaderLaunchResult(
            EvidenceReaderResultStatus.ANSWERED,
            definition.name,
            kwargs["expected_scope_digest"],
            kwargs["expected_snapshot_digest"],
            f"thread-{artifact_path}",
            (citation,),
            json.dumps({"canary": "private", "observed": page.content}),
        )

    monkeypatch.setattr(delegate_module, "launch_evidence_reader", launch_with_real_authority)

    def delegate(path: str) -> str:
        try:
            return json.loads(
                delegate_module._delegate_sync(
                    context,
                    caller_session_id="shared-session",
                    role="pr-source-reader",
                    artifact_path=path,
                    requested_fields=("field",),
                )
            )["status"]
        except delegate_module._DelegateError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = {
            path: future.result(timeout=10)
            for path, future in (
                ("first.txt", pool.submit(delegate, "first.txt")),
                ("second.txt", pool.submit(delegate, "second.txt")),
            )
        }

    assert outcomes == {"first.txt": "artifact_stale", "second.txt": "answered"}
    assert len(set(invocation_dirs)) == 2
    assert all(not path.exists() for path in invocation_dirs)
