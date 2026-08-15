from __future__ import annotations

import asyncio
import hashlib
import json
import stat
import time
from collections.abc import Callable
from enum import StrEnum
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

import autoskillit.server as server_module
import autoskillit.server.tools._evidence_reader as reader_module
import autoskillit.server.tools.tools_evidence_reader as handler_module
from autoskillit.core import (
    DIRECT_PREFIX,
    EVIDENCE_READER_AUTHORITY_ENV_VAR,
    EVIDENCE_READER_AUTHORITY_PATH_ENV_VAR,
    EVIDENCE_READER_CAPABILITY_ENV_VAR,
)
from autoskillit.exploration import StableArtifactCapture
from autoskillit.pipeline import ToolContext
from autoskillit.server.tools._evidence_reader import (
    EvidenceReaderError,
    EvidenceReaderInvocation,
    EvidenceReaderLimits,
    EvidenceReaderPage,
    create_evidence_reader_invocation,
    load_evidence_reader_receipts,
    read_evidence_reader_page,
    revoke_evidence_reader_invocation,
)

pytestmark = [pytest.mark.layer("server"), pytest.mark.medium]

_CANONICAL_TOOLS = (
    f"{DIRECT_PREFIX}get_authorized_artifact_page",
    f"{DIRECT_PREFIX}read_authorized_artifact",
)
_BARE_TOOLS = ("get_authorized_artifact_page", "read_authorized_artifact")
_CALL_BINDING = {
    "caller_session_id": "session-1",
    "role": "pr-source-reader",
    "role_definition_digest": "sha256:role-definition",
    "canonical_tool": _CANONICAL_TOOLS[0],
    "bare_tool": _BARE_TOOLS[0],
    "policy": "read-only",
}


class _ErrorCode(StrEnum):
    AUTHORITY_EXPIRED = "authority_expired"
    AUTHORITY_TAMPERED = "authority_tampered"
    AUTHORITY_UNAVAILABLE = "authority_unavailable"
    BROKER_UNAVAILABLE = "evidence_reader_broker_unavailable"
    CALL_BUDGET_EXHAUSTED = "call_budget_exhausted"
    CALL_IN_FLIGHT = "call_in_flight"
    CAPABILITY_INVALID = "capability_invalid"
    CONTENT_NOT_UTF8 = "content_not_utf8"
    CONTINUATION_INVALID = "continuation_invalid"
    DEADLINE_EXCEEDED = "deadline_exceeded"
    OUTPUT_BUDGET_EXHAUSTED = "output_budget_exhausted"
    PAGE_BUDGET_EXHAUSTED = "page_budget_exhausted"
    RECEIPT_LIMIT_INVALID = "receipt_limit_invalid"
    SCOPE_MISMATCH = "scope_mismatch"
    TOOL_NOT_AUTHORIZED = "tool_not_authorized"


class _ReceiptOutcome(StrEnum):
    COMPLETE = "complete"


def _capture(tmp_path: Path, content: bytes = b"alpha\nbeta\ngamma\n") -> StableArtifactCapture:
    repository = tmp_path / "repository"
    repository.mkdir(exist_ok=True)
    content_digest = f"sha256:{hashlib.sha256(content).hexdigest()}"
    return StableArtifactCapture(
        repository_root=repository,
        repository_identity_digest="a" * 64,
        revision="b" * 40,
        artifact_path="evidence.txt",
        content=content,
        content_digest=content_digest,
        size=len(content),
        mode=0o644,
        index_records=(f"100644 {'c' * 40} 0\tevidence.txt",),
        snapshot_digest="sha256:snapshot",
    )


def _create(
    tmp_path: Path,
    *,
    capture: StableArtifactCapture | None = None,
    limits: EvidenceReaderLimits | None = None,
    expires_at: float | None = None,
) -> tuple[ToolContext, EvidenceReaderInvocation]:
    context = cast(ToolContext, SimpleNamespace(temp_dir=tmp_path))
    invocation = create_evidence_reader_invocation(
        context,
        capture or _capture(tmp_path),
        caller_session_id=_CALL_BINDING["caller_session_id"],
        role=_CALL_BINDING["role"],
        role_definition_digest=_CALL_BINDING["role_definition_digest"],
        canonical_tools=_CANONICAL_TOOLS,
        bare_tools=_BARE_TOOLS,
        policy=_CALL_BINDING["policy"],
        expires_at=expires_at or time.time() + 60,
        limits=limits,
    )
    return context, invocation


def _read(
    context: ToolContext,
    invocation: EvidenceReaderInvocation,
    *,
    page_size: int = 5,
    continuation: str | None = None,
    environment: dict[str, str] | None = None,
    binding_overrides: dict[str, str] | None = None,
):
    binding = {**_CALL_BINDING, **(binding_overrides or {})}
    return read_evidence_reader_page(
        context,
        environment or dict(invocation.environment),
        **binding,
        page_size=page_size,
        continuation=continuation,
        deadline=time.monotonic() + 5,
    )


def _assert_error(code: _ErrorCode, operation: Callable[[], object]) -> None:
    with pytest.raises(EvidenceReaderError) as raised:
        operation()
    assert raised.value.code == code


def test_invocations_use_unique_private_directories_and_files(tmp_path: Path) -> None:
    _context, first = _create(tmp_path)
    _context, second = _create(tmp_path)

    assert first.invocation_dir != second.invocation_dir
    assert stat.S_IMODE(first.invocation_dir.parent.stat().st_mode) == 0o700
    for invocation in (first, second):
        assert stat.S_IMODE(invocation.invocation_dir.stat().st_mode) == 0o700
        assert {path.name for path in invocation.invocation_dir.iterdir()} == {
            "authority.json",
            "receipts.json",
        }
        assert all(
            stat.S_IMODE(path.stat().st_mode) == 0o600
            for path in invocation.invocation_dir.iterdir()
        )


@pytest.mark.parametrize(
    ("override", "code"),
    [
        ({"caller_session_id": "other-session"}, _ErrorCode.SCOPE_MISMATCH),
        ({"role": "other-role"}, _ErrorCode.SCOPE_MISMATCH),
        ({"role_definition_digest": "sha256:other-role"}, _ErrorCode.SCOPE_MISMATCH),
        ({"policy": "other-policy"}, _ErrorCode.SCOPE_MISMATCH),
        ({"bare_tool": _BARE_TOOLS[1]}, _ErrorCode.TOOL_NOT_AUTHORIZED),
    ],
)
def test_call_authority_is_bound_to_session_role_policy_and_tool_pair(
    tmp_path: Path,
    override: dict[str, str],
    code: _ErrorCode,
) -> None:
    context, invocation = _create(tmp_path)

    _assert_error(
        code,
        lambda: _read(context, invocation, binding_overrides=override),
    )

    page = _read(context, invocation)
    assert page.snapshot_digest == "sha256:snapshot"


@pytest.mark.parametrize(
    ("tamper", "code"),
    [
        ("authority", _ErrorCode.AUTHORITY_TAMPERED),
        ("receipt", _ErrorCode.AUTHORITY_TAMPERED),
        ("capability", _ErrorCode.CAPABILITY_INVALID),
    ],
)
def test_tampered_authority_receipt_or_capability_fails_closed(
    tmp_path: Path,
    tamper: str,
    code: _ErrorCode,
) -> None:
    context, invocation = _create(tmp_path)
    environment = dict(invocation.environment)
    if tamper == "capability":
        environment[EVIDENCE_READER_CAPABILITY_ENV_VAR] = "forged-capability"
    else:
        filename = "authority.json" if tamper == "authority" else "receipts.json"
        path = invocation.invocation_dir / filename
        path.write_text("{}")
        path.chmod(0o600)

    def operation() -> object:
        if tamper == "receipt":
            return load_evidence_reader_receipts(context, environment)
        return _read(context, invocation, environment=environment)

    _assert_error(code, operation)


def test_expired_authority_replayed_cursor_and_cross_invocation_cursor_are_denied(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context, first = _create(tmp_path)
    first_page = _read(context, first, page_size=5)
    assert first_page.continuation is not None
    _read(context, first, page_size=5, continuation=first_page.continuation)
    _assert_error(
        _ErrorCode.CONTINUATION_INVALID,
        lambda: _read(context, first, page_size=5, continuation=first_page.continuation),
    )

    source_context, source = _create(tmp_path)
    cross_page = _read(source_context, source, page_size=5)
    assert cross_page.continuation is not None
    other_context, second = _create(tmp_path)
    _assert_error(
        _ErrorCode.CONTINUATION_INVALID,
        lambda: _read(
            other_context,
            second,
            page_size=5,
            continuation=cross_page.continuation,
        ),
    )

    monkeypatch.setattr(reader_module.time, "time", lambda: first.expires_at + 1)
    _assert_error(_ErrorCode.AUTHORITY_EXPIRED, lambda: _read(context, first))


def test_utf8_paging_is_byte_and_line_exact_with_opaque_single_use_cursors(
    tmp_path: Path,
) -> None:
    content = "αlpha\nβeta\ngamma\n".encode()
    capture = _capture(tmp_path, content)
    context, invocation = _create(tmp_path, capture=capture)
    (capture.repository_root / capture.artifact_path).write_bytes(b"later repository bytes")
    pages = []
    continuation = None
    while True:
        page = _read(context, invocation, page_size=6, continuation=continuation)
        pages.append(page)
        if page.continuation is None:
            break
        assert len(page.continuation) >= 32
        assert not page.continuation.isdigit()
        continuation = page.continuation

    assert "".join(page.content for page in pages).encode() == content
    assert [page.byte_start for page in pages] == [0, *[page.byte_end for page in pages[:-1]]]
    assert pages[0].line_start == 1
    assert pages[-1].line_end == 3
    assert len({page.citation_id for page in pages}) == len(pages)


@pytest.mark.parametrize(
    ("limits", "expected_code"),
    [
        (
            EvidenceReaderLimits(max_calls=1, max_pages=2),
            _ErrorCode.CALL_BUDGET_EXHAUSTED,
        ),
        (
            EvidenceReaderLimits(max_calls=2, max_pages=1),
            _ErrorCode.PAGE_BUDGET_EXHAUSTED,
        ),
        (
            EvidenceReaderLimits(max_calls=2, max_pages=2, max_output_bytes=5),
            _ErrorCode.OUTPUT_BUDGET_EXHAUSTED,
        ),
    ],
)
def test_reader_denies_exhausted_call_page_and_output_budgets(
    tmp_path: Path,
    limits: EvidenceReaderLimits,
    expected_code: _ErrorCode,
) -> None:
    context, invocation = _create(tmp_path, limits=limits)
    first = _read(context, invocation, page_size=5)

    _assert_error(
        expected_code,
        lambda: _read(context, invocation, page_size=5, continuation=first.continuation),
    )


def test_reader_denies_expired_deadline_and_existing_inflight_lock(tmp_path: Path) -> None:
    context, invocation = _create(tmp_path)
    environment = dict(invocation.environment)
    _assert_error(
        _ErrorCode.DEADLINE_EXCEEDED,
        lambda: read_evidence_reader_page(
            context,
            environment,
            **_CALL_BINDING,
            page_size=5,
            continuation=None,
            deadline=time.monotonic() - 1,
        ),
    )

    lock = invocation.invocation_dir / "call.lock"
    lock.write_text("")
    lock.chmod(0o600)
    _assert_error(_ErrorCode.CALL_IN_FLIGHT, lambda: _read(context, invocation))


def test_invalid_utf8_is_rejected_before_authority_is_created(tmp_path: Path) -> None:
    invalid = _capture(tmp_path, b"valid\xffinvalid")

    _assert_error(_ErrorCode.CONTENT_NOT_UTF8, lambda: _create(tmp_path, capture=invalid))
    assert not (tmp_path / "evidence-readers").exists()


def test_receipt_loader_returns_only_the_bounded_verified_suffix(tmp_path: Path) -> None:
    limits = EvidenceReaderLimits(max_receipts=2)
    context, invocation = _create(tmp_path, limits=limits)
    continuation = None
    for _ in range(3):
        page = _read(context, invocation, page_size=5, continuation=continuation)
        continuation = page.continuation

    receipts = load_evidence_reader_receipts(
        context,
        dict(invocation.environment),
        max_receipts=1,
    )

    assert tuple(receipt.sequence for receipt in receipts) == (3,)
    assert receipts[0].outcome == _ReceiptOutcome.COMPLETE
    _assert_error(
        _ErrorCode.RECEIPT_LIMIT_INVALID,
        lambda: load_evidence_reader_receipts(
            context,
            dict(invocation.environment),
            max_receipts=65,
        ),
    )


def test_revocation_is_synchronous_and_leaves_verified_absence(tmp_path: Path) -> None:
    context, invocation = _create(tmp_path)
    environment = dict(invocation.environment)
    authority_path = Path(environment[EVIDENCE_READER_AUTHORITY_PATH_ENV_VAR])
    authority_digest = environment[EVIDENCE_READER_AUTHORITY_ENV_VAR]

    revoke_evidence_reader_invocation(context, environment)

    assert not invocation.invocation_dir.exists()
    assert not authority_path.exists()
    assert authority_digest
    _assert_error(
        _ErrorCode.AUTHORITY_UNAVAILABLE,
        lambda: load_evidence_reader_receipts(context, environment),
    )


def _bind_handler_authority(
    context: ToolContext,
    invocation: EvidenceReaderInvocation,
    monkeypatch: pytest.MonkeyPatch,
) -> dict[str, str]:
    environment = dict(invocation.environment)
    monkeypatch.setattr(server_module, "_get_ctx", lambda: context)
    monkeypatch.setattr(handler_module, "_require_enabled", lambda: None)
    for name, value in environment.items():
        monkeypatch.setenv(name, value)
    return environment


@pytest.mark.anyio
async def test_brokers_serve_initial_and_continuation_pages_with_exact_citations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    content = "αlpha\nβeta\ngamma\n".encode()
    context, invocation = _create(tmp_path, capture=_capture(tmp_path, content))
    _bind_handler_authority(context, invocation, monkeypatch)

    initial_raw = await handler_module.read_authorized_artifact(page_size=6)
    initial = json.loads(initial_raw)
    assert initial == {
        "status": "ok",
        "content": "αlpha",
        "citation_id": initial["citation_id"],
        "byte_start": 0,
        "byte_end": 6,
        "line_start": 1,
        "line_end": 1,
        "snapshot_digest": "sha256:snapshot",
        "continuation": initial["continuation"],
    }
    assert initial["continuation"]

    continued_raw = await handler_module.get_authorized_artifact_page(
        initial["continuation"],
        page_size=6,
    )
    continued = json.loads(continued_raw)
    assert continued["status"] == "ok"
    assert continued["byte_start"] == initial["byte_end"]
    assert continued["citation_id"] != initial["citation_id"]
    assert continued["snapshot_digest"] == initial["snapshot_digest"]


@pytest.mark.anyio
async def test_brokers_apply_default_explicit_page_sizes_deadlines_and_private_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context, invocation = _create(tmp_path)
    expected_environment = _bind_handler_authority(context, invocation, monkeypatch)
    monkeypatch.setenv("AUTOSKILLIT_UNTRUSTED_REQUEST_OVERRIDE", "attacker-value")
    calls: list[dict[str, object]] = []

    def observe_bound_read(
        observed_context: ToolContext,
        environment: dict[str, str],
        *,
        canonical_tool: str,
        page_size: int,
        continuation: str | None,
        deadline: float,
    ) -> EvidenceReaderPage:
        calls.append(
            {
                "context": observed_context,
                "environment": environment,
                "canonical_tool": canonical_tool,
                "page_size": page_size,
                "continuation": continuation,
                "deadline": deadline,
            }
        )
        return EvidenceReaderPage("page", "citation", "next", 1, 5, 2, 2, "snapshot")

    monkeypatch.setattr(handler_module, "read_bound_evidence_reader_page", observe_bound_read)
    started = time.monotonic()

    initial = json.loads(await handler_module.read_authorized_artifact())
    continued = json.loads(
        await handler_module.get_authorized_artifact_page("opaque", page_size=7)
    )

    assert initial == {
        "status": "ok",
        "content": "page",
        "citation_id": "citation",
        "byte_start": 1,
        "byte_end": 5,
        "line_start": 2,
        "line_end": 2,
        "snapshot_digest": "snapshot",
        "continuation": "next",
    }
    assert continued == initial
    assert [call["page_size"] for call in calls] == [64_000, 7]
    assert [call["continuation"] for call in calls] == [None, "opaque"]
    assert [call["canonical_tool"] for call in calls] == [
        f"{DIRECT_PREFIX}read_authorized_artifact",
        f"{DIRECT_PREFIX}get_authorized_artifact_page",
    ]
    assert all(call["context"] is context for call in calls)
    assert all(call["environment"] == expected_environment for call in calls)
    assert all(
        started < call["deadline"] <= started + handler_module._BROKER_TIMEOUT_SECONDS + 0.1
        for call in calls
    )

    invalid = json.loads(await handler_module.read_authorized_artifact(page_size=0))
    assert invalid == {"status": "error", "code": "page_size_invalid"}
    assert len(calls) == 2


@pytest.mark.parametrize(
    ("failure", "expected_code"),
    [
        ("capability", _ErrorCode.CAPABILITY_INVALID),
        ("tamper", _ErrorCode.AUTHORITY_TAMPERED),
        ("expiry", _ErrorCode.AUTHORITY_EXPIRED),
        ("replay", _ErrorCode.CONTINUATION_INVALID),
        ("budget", _ErrorCode.CALL_BUDGET_EXHAUSTED),
    ],
)
@pytest.mark.anyio
async def test_broker_error_envelopes_do_not_leak_authority_secrets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
    expected_code: _ErrorCode,
) -> None:
    limits = EvidenceReaderLimits(max_calls=1) if failure == "budget" else None
    context, invocation = _create(tmp_path, limits=limits)
    environment = _bind_handler_authority(context, invocation, monkeypatch)
    continuation: str | None = None
    if failure == "capability":
        monkeypatch.setenv(EVIDENCE_READER_CAPABILITY_ENV_VAR, "forged-capability")
    elif failure == "tamper":
        authority = invocation.invocation_dir / "authority.json"
        authority.write_text("{}")
        authority.chmod(0o600)
    elif failure == "expiry":
        monkeypatch.setattr(reader_module.time, "time", lambda: invocation.expires_at + 1)
    else:
        initial = json.loads(await handler_module.read_authorized_artifact(page_size=5))
        continuation = initial["continuation"]
        if failure == "replay":
            assert continuation
            await handler_module.get_authorized_artifact_page(continuation, page_size=5)

    raw = await (
        handler_module.read_authorized_artifact(page_size=5)
        if continuation is None
        else handler_module.get_authorized_artifact_page(continuation, page_size=5)
    )

    assert json.loads(raw) == {"status": "error", "code": expected_code}
    assert environment[EVIDENCE_READER_CAPABILITY_ENV_VAR] not in raw
    assert environment[EVIDENCE_READER_AUTHORITY_PATH_ENV_VAR] not in raw


@pytest.mark.parametrize("failure", ["unexpected", "cancelled"])
@pytest.mark.anyio
async def test_broker_boundary_never_raises_and_shields_cancellation(
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    monkeypatch.setattr(handler_module, "_require_enabled", lambda: None)

    def fail_closed(**_kwargs: object) -> str:
        if failure == "cancelled":
            raise asyncio.CancelledError
        raise RuntimeError("private failure detail")

    monkeypatch.setattr(handler_module, "_serve_page", fail_closed)

    payload = json.loads(await handler_module.read_authorized_artifact())

    if failure == "cancelled":
        assert payload == {"success": False, "error": "cancelled", "subtype": "cancelled"}
    else:
        assert payload == {"status": "error", "code": _ErrorCode.BROKER_UNAVAILABLE}
    assert "private failure detail" not in json.dumps(payload)
