"""Protected Codex attestation and durable receipt-store contracts."""

from __future__ import annotations

import copy
import json
import sqlite3
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

import autoskillit.execution.backends._codex_recipe_delivery as recipe_delivery
from autoskillit.core import (
    RecipeDeliveryAttestation,
    RecipeDeliveryEvidenceDef,
    RecipeDeliveryMode,
    RecipeDeliveryRequest,
    recipe_delivery_request_digest,
    resolve_recipe_delivery_decision,
)
from autoskillit.execution.backends import BACKEND_REGISTRY, CODEX_RECIPE_DELIVERY_BUDGET
from autoskillit.execution.backends._codex_recipe_delivery import (
    _MARKER_MAX_CANDIDATES,
    CodexHostCorrelation,
    CodexOuterBudgetAttestor,
    NullProtectedHostAttestationProvider,
    ProtectedStoreAuthority,
    RecipeDeliveryReceiptLedger,
    _fresh_marker_session_id,
    enumerate_fresh_codex_marker_ids,
    read_rollout_thread_id,
    resolve_unique_codex_host_correlation,
)
from tests.fixtures.codex_recipe_diagnostic import (
    UNSIGNED_TRACE_V1,
    WRITABLE_ROLLOUT_V1,
)
from tests.fixtures.codex_recipe_diagnostic import (
    fixture_path as diagnostic_fixture_path,
)
from tests.fixtures.codex_recipe_protected import (
    PROTECTED_FUNCTIONS_EXEC_V1,
)
from tests.fixtures.codex_recipe_protected import (
    fixture_path as protected_fixture_path,
)

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]

_NOW = 1_800_000_000
_HIGH = 56_750
_FIXTURE_DIGEST = "sha256:" + ("d" * 64)
_PAYLOAD_SHA = "sha256:" + ("a" * 64)


def _protected_record() -> dict[str, object]:
    return json.loads(protected_fixture_path(PROTECTED_FUNCTIONS_EXEC_V1).read_text())


def _request() -> RecipeDeliveryRequest:
    return RecipeDeliveryRequest(
        audience="autoskillit.recipe-delivery",
        delivery_call_id="delivery-001",
        contract_version=1,
        contract_digest=_FIXTURE_DIGEST,
        caller_requested_outer_tokens=_HIGH,
        code_digest="sha256:" + ("b" * 64),
    )


def _evidence() -> RecipeDeliveryEvidenceDef:
    return RecipeDeliveryEvidenceDef(
        identity="protected-test-host-v1",
        host_channel="test-only-process-isolated-host",
        evidence_schema_version=1,
        parser_version=1,
        cli_identity="codex-test-cli",
        selected_limit_derivation="protected_resolved_outer_limit",
        selected_result_token_limit=_HIGH,
        contract_digest=_FIXTURE_DIGEST,
    )


@dataclass(frozen=True, slots=True)
class _FixtureProtectedProvider:
    record: dict[str, object]
    authority: ProtectedStoreAuthority | None = None
    unavailable: bool = False

    def attest(
        self,
        *,
        request: RecipeDeliveryRequest,
        correlation: CodexHostCorrelation,
        now_unix: int,
    ) -> RecipeDeliveryAttestation | None:
        if self.unavailable:
            raise OSError("protected provider unavailable")
        if len(json.dumps(self.record).encode("utf-8")) > 256 * 1024:
            return None
        if (
            self.record.get("schema_version") != 1
            or self.record.get("authenticated") is not True
            or self.record.get("caller_writable") is not False
            or self.record.get("complete") is not True
            or self.record.get("event_sequence") != ["outer_call_selected", "nested_call_started"]
        ):
            return None
        event = self.record.get("event")
        if not isinstance(event, dict):
            return None
        pragma = event.get("pragma")
        if not isinstance(pragma, str) or not pragma.startswith("// @exec: "):
            return None
        try:
            pragma_maximum = json.loads(pragma.removeprefix("// @exec: "))["max_output_tokens"]
        except (json.JSONDecodeError, KeyError, TypeError):
            return None
        required = {
            "audience": request.audience,
            "thread_id": correlation.thread_id,
            "turn_id": "turn-001",
            "outer_call_id": "functions-exec-001",
            "code_mode_cell_id": "cell-001",
            "delivery_call_id": request.delivery_call_id,
            "nested_call_id": request.delivery_call_id,
            "host_observed_requested_outer_tokens": _HIGH,
            "selected_result_token_limit": _HIGH,
            "code_digest": request.code_digest,
            "request_digest": recipe_delivery_request_digest(request),
            "contract_version": request.contract_version,
            "contract_digest": request.contract_digest,
            "parser_version": 1,
            "evidence_version": 1,
            "evidence_identity": "protected-test-host-v1",
        }
        if pragma_maximum != _HIGH or any(
            event.get(key) != value for key, value in required.items()
        ):
            return None
        if event.get("expires_at_unix", 0) < now_unix or not event.get("nonce"):
            return None
        return RecipeDeliveryAttestation(
            audience=request.audience,
            thread_id=correlation.thread_id,
            turn_id=str(event["turn_id"]),
            outer_call_id=str(event["outer_call_id"]),
            code_mode_cell_id=str(event["code_mode_cell_id"]),
            delivery_call_id=request.delivery_call_id,
            host_observed_requested_outer_tokens=_HIGH,
            selected_result_token_limit=_HIGH,
            code_digest=request.code_digest,
            request_digest=str(event["request_digest"]),
            nonce=str(event["nonce"]),
            expires_at_unix=int(event["expires_at_unix"]),
            contract_version=request.contract_version,
            contract_digest=request.contract_digest,
            parser_version=1,
            evidence_version=1,
            evidence_identity="protected-test-host-v1",
        )

    def store_authority(self, *, thread_id: str) -> ProtectedStoreAuthority | None:
        del thread_id
        return self.authority


def _write_marker_and_rollout(project_dir: Path, thread_id: str) -> Path:
    state_dir = project_dir / ".autoskillit" / "temp" / "kitchen_state"
    state_dir.mkdir(parents=True, exist_ok=True)
    opened_at = datetime.fromtimestamp(_NOW - 10, tz=UTC).isoformat()
    (state_dir / f"{thread_id}.json").write_text(
        json.dumps({"session_id": thread_id, "opened_at": opened_at}),
        encoding="utf-8",
    )
    rollout = project_dir / f"rollout-{thread_id}.jsonl"
    rollout.write_text(
        json.dumps({"type": "thread.started", "thread_id": thread_id}) + "\n",
        encoding="utf-8",
    )
    return rollout


def test_marker_validation_uses_one_bounded_file_descriptor(tmp_path: Path) -> None:
    _write_marker_and_rollout(tmp_path, "thread-single-descriptor")
    marker = tmp_path / ".autoskillit" / "temp" / "kitchen_state" / "thread-single-descriptor.json"

    class _GuardedMarkerPath(type(marker)):
        def stat(self, *_args: Any, **_kwargs: Any) -> Any:
            raise AssertionError("marker validation performed a separate stat")

        def is_file(self) -> bool:
            raise AssertionError("marker validation performed a separate file check")

        def read_text(self, *args: Any, **kwargs: Any) -> str:
            raise AssertionError("marker validation performed an unbounded path read")

    assert (
        _fresh_marker_session_id(
            _GuardedMarkerPath(marker),
            now_unix=_NOW,
            ttl_seconds=24 * 60 * 60,
        )
        == "thread-single-descriptor"
    )


def test_oversized_marker_fails_closed_without_unbounded_read(tmp_path: Path) -> None:
    _write_marker_and_rollout(tmp_path, "thread-oversized-marker")
    marker = tmp_path / ".autoskillit" / "temp" / "kitchen_state" / "thread-oversized-marker.json"
    marker.write_bytes(b"x" * ((64 * 1024) + 1))

    assert enumerate_fresh_codex_marker_ids(tmp_path, now_unix=_NOW) == ()


def test_marker_enumeration_fails_closed_above_candidate_ceiling(tmp_path: Path) -> None:
    state_dir = tmp_path / ".autoskillit" / "temp" / "kitchen_state"
    state_dir.mkdir(parents=True)
    opened_at = datetime.fromtimestamp(_NOW - 10, tz=UTC).isoformat()
    for index in range(_MARKER_MAX_CANDIDATES + 1):
        session_id = f"thread-candidate-{index:04d}"
        (state_dir / f"{session_id}.json").write_text(
            json.dumps({"session_id": session_id, "opened_at": opened_at}),
            encoding="utf-8",
        )

    assert enumerate_fresh_codex_marker_ids(tmp_path, now_unix=_NOW) == ()


def _attestor(
    project_dir: Path,
    provider: _FixtureProtectedProvider | NullProtectedHostAttestationProvider,
    rollouts: dict[str, Path],
) -> CodexOuterBudgetAttestor:
    del project_dir
    return CodexOuterBudgetAttestor(
        provider=provider,
        locate_rollout=rollouts.get,
        supported_evidence={_evidence().identity: _evidence()},
    )


def _delivery_mode(result) -> RecipeDeliveryMode:
    caps = replace(
        BACKEND_REGISTRY["codex"]().capabilities,
        protected_recipe_delivery_capable=True,
    )
    decision = resolve_recipe_delivery_decision(
        capabilities=caps,
        required_serialized_tokens=10_001,
        budget=CODEX_RECIPE_DELIVERY_BUDGET._replace(contract_digest=_FIXTURE_DIGEST),
        producer="open_kitchen",
        payload_sha256=_PAYLOAD_SHA,
        request=_request(),
        attestation=result.attestation,
        supported_evidence=result.evidence,
        now_unix=_NOW,
    )
    return decision.mode


def test_valid_protected_fixture_attests_and_selects_high_inline(tmp_path: Path) -> None:
    rollout = _write_marker_and_rollout(tmp_path, "thread-protected-001")
    result = _attestor(
        tmp_path,
        _FixtureProtectedProvider(_protected_record()),
        {"thread-protected-001": rollout},
    ).attest(request=_request(), project_dir=tmp_path, now_unix=_NOW)
    assert result.reason == "attested"
    assert _delivery_mode(result) is RecipeDeliveryMode.ATTESTED_INLINE


@pytest.mark.parametrize(
    ("target", "key", "value"),
    [
        ("root", "complete", False),
        ("root", "event_sequence", ["nested_call_started", "outer_call_selected"]),
        ("event", "thread_id", "wrong-thread"),
        ("event", "turn_id", "wrong-turn"),
        ("event", "outer_call_id", "wrong-call"),
        ("event", "code_mode_cell_id", "wrong-cell"),
        ("event", "nested_call_id", "wrong-nested"),
        ("event", "pragma", "// @exec: not-json"),
        ("event", "pragma", "// @exec: {}"),
        ("event", "pragma", '// @exec: {"max_output_tokens": 10000}'),
        ("event", "pragma", '// @exec: {"max_output_tokens": 56751}'),
        ("event", "host_observed_requested_outer_tokens", None),
        ("event", "host_observed_requested_outer_tokens", 10_000),
        ("event", "host_observed_requested_outer_tokens", _HIGH + 1),
        ("root", "schema_version", 2),
        ("event", "parser_version", 2),
        ("event", "evidence_version", 2),
    ],
)
def test_protected_evidence_negative_matrix_is_envelope(
    tmp_path: Path,
    target: str,
    key: str,
    value: object,
) -> None:
    record = copy.deepcopy(_protected_record())
    container = record if target == "root" else record["event"]
    assert isinstance(container, dict)
    if value is None:
        container.pop(key)
    else:
        container[key] = value
    rollout = _write_marker_and_rollout(tmp_path, "thread-protected-001")
    result = _attestor(
        tmp_path,
        _FixtureProtectedProvider(record),
        {"thread-protected-001": rollout},
    ).attest(request=_request(), project_dir=tmp_path, now_unix=_NOW)
    assert result.attestation is None
    assert _delivery_mode(result) is RecipeDeliveryMode.ENVELOPE


def test_oversized_record_and_unavailable_provider_are_envelope(tmp_path: Path) -> None:
    rollout = _write_marker_and_rollout(tmp_path, "thread-protected-001")
    oversized = _protected_record()
    oversized["padding"] = "x" * (256 * 1024)
    for provider in (
        _FixtureProtectedProvider(oversized),
        _FixtureProtectedProvider(_protected_record(), unavailable=True),
    ):
        result = _attestor(
            tmp_path,
            provider,
            {"thread-protected-001": rollout},
        ).attest(request=_request(), project_dir=tmp_path, now_unix=_NOW)
        assert result.attestation is None
        assert _delivery_mode(result) is RecipeDeliveryMode.ENVELOPE


def test_direct_forged_and_unsigned_diagnostic_records_are_envelope(tmp_path: Path) -> None:
    rollout = _write_marker_and_rollout(tmp_path, "thread-protected-001")
    diagnostic_records = []
    for name in (WRITABLE_ROLLOUT_V1, UNSIGNED_TRACE_V1):
        diagnostic_records.extend(
            json.loads(line)
            for line in diagnostic_fixture_path(name).read_text().splitlines()
            if line.strip()
        )
    for provider in (
        NullProtectedHostAttestationProvider(),
        _FixtureProtectedProvider({"authenticated": False, "caller_writable": True}),
        *(_FixtureProtectedProvider(record) for record in diagnostic_records),
    ):
        result = _attestor(
            tmp_path,
            provider,
            {"thread-protected-001": rollout},
        ).attest(request=_request(), project_dir=tmp_path, now_unix=_NOW)
        assert result.attestation is None
        assert _delivery_mode(result) is RecipeDeliveryMode.ENVELOPE


def test_zero_or_multiple_fresh_markers_are_ambiguous(tmp_path: Path) -> None:
    assert enumerate_fresh_codex_marker_ids(tmp_path, now_unix=_NOW) == ()
    assert (
        resolve_unique_codex_host_correlation(
            tmp_path,
            locate_rollout=lambda _thread_id: None,
            now_unix=_NOW,
        )
        is None
    )

    rollouts = {
        thread_id: _write_marker_and_rollout(tmp_path, thread_id)
        for thread_id in ("thread-concurrent-a", "thread-concurrent-b")
    }
    assert len(enumerate_fresh_codex_marker_ids(tmp_path, now_unix=_NOW)) == 2
    assert (
        resolve_unique_codex_host_correlation(
            tmp_path,
            locate_rollout=rollouts.get,
            now_unix=_NOW,
        )
        is None
    )


def test_rollout_first_record_must_be_complete_ordered_bounded_and_matching(
    tmp_path: Path,
) -> None:
    partial = tmp_path / "partial.jsonl"
    partial.write_text('{"type":"thread.started","thread_id":"thread-1"}')
    reordered = tmp_path / "reordered.jsonl"
    reordered.write_text(
        '{"type":"turn.started"}\n{"type":"thread.started","thread_id":"thread-1"}\n'
    )
    oversized = tmp_path / "oversized.jsonl"
    oversized.write_text(json.dumps({"type": "thread.started", "thread_id": "x" * 100}) + "\n")
    assert read_rollout_thread_id(partial) == ""
    assert read_rollout_thread_id(reordered) == ""
    assert read_rollout_thread_id(oversized, max_record_bytes=64) == ""


def _authority(tmp_path: Path, **changes: object) -> ProtectedStoreAuthority:
    values = {
        "root": tmp_path / "protected-host-store",
        "security_identity": "protected-test-host-v1",
        "local_filesystem": True,
        "caller_writable": False,
        "initialized_by_host": True,
    }
    values.update(changes)
    return ProtectedStoreAuthority(**values)  # type: ignore[arg-type]


def _ledger_attestation(thread_id: str = "thread-ledger-001") -> RecipeDeliveryAttestation:
    return RecipeDeliveryAttestation(
        audience="autoskillit.recipe-delivery",
        thread_id=thread_id,
        turn_id="turn-ledger-001",
        outer_call_id="outer-ledger-001",
        code_mode_cell_id="cell-ledger-001",
        delivery_call_id="delivery-001",
        host_observed_requested_outer_tokens=_HIGH,
        selected_result_token_limit=_HIGH,
        code_digest="sha256:" + ("b" * 64),
        request_digest=recipe_delivery_request_digest(_request()),
        nonce="nonce-ledger-001",
        expires_at_unix=2_000_000_000,
        contract_version=1,
        contract_digest=_FIXTURE_DIGEST,
        parser_version=1,
        evidence_version=1,
        evidence_identity="protected-test-host-v1",
    )


def _reserve(ledger: RecipeDeliveryReceiptLedger, attestation: RecipeDeliveryAttestation):
    return ledger.reserve(
        capabilities=replace(
            BACKEND_REGISTRY["codex"]().capabilities,
            protected_recipe_delivery_capable=True,
        ),
        required_serialized_tokens=10_001,
        budget=CODEX_RECIPE_DELIVERY_BUDGET._replace(contract_digest=_FIXTURE_DIGEST),
        request=_request(),
        attestation=attestation,
        supported_evidence=_evidence(),
        producer="open_kitchen",
        payload_sha256=_PAYLOAD_SHA,
        now_unix=_NOW,
    )


def test_reservation_atomically_consumes_call_and_creates_pending(tmp_path: Path) -> None:
    ledger = RecipeDeliveryReceiptLedger.initialize_protected(_authority(tmp_path))
    result = _reserve(ledger, _ledger_attestation())
    assert result.reason == "reserved"
    assert result.handle is not None
    assert ledger.receipt_status(result.handle.thread_id) == "pending"
    later_call = replace(
        _ledger_attestation(),
        outer_call_id="outer-ledger-002",
        nonce="nonce-ledger-002",
    )
    assert _reserve(ledger, later_call).reason == "receipt_pending"
    with sqlite3.connect(ledger.path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM consumed_calls").fetchone() == (1,)
        assert connection.execute("SELECT COUNT(*) FROM receipts").fetchone() == (1,)


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"audience": "forged-audience"}, "attestation_audience_mismatch"),
        (
            {"contract_digest": "sha256:" + ("0" * 64)},
            "attestation_contract_digest_mismatch",
        ),
        ({"request_digest": "sha256:" + ("0" * 64)}, "request_digest_mismatch"),
        ({"expires_at_unix": _NOW}, "attestation_expired"),
        ({"evidence_identity": "unknown-evidence"}, "unsupported_evidence_identity"),
        ({"selected_result_token_limit": _HIGH - 1}, "host_selected_limit_mismatch"),
    ],
)
def test_reservation_revalidates_attestation_before_consuming_evidence(
    tmp_path: Path,
    changes: dict[str, object],
    reason: str,
) -> None:
    ledger = RecipeDeliveryReceiptLedger.initialize_protected(_authority(tmp_path))

    result = _reserve(ledger, replace(_ledger_attestation(), **changes))

    assert result.handle is None
    assert result.reason == reason
    with sqlite3.connect(ledger.path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM consumed_calls").fetchone() == (0,)
        assert connection.execute("SELECT COUNT(*) FROM receipts").fetchone() == (0,)


def test_interrupted_first_initialization_does_not_publish_invalid_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    authority = _authority(tmp_path)
    real_connect = sqlite3.connect

    class _InterruptedConnection:
        def __init__(self, path: str | Path, **kwargs: Any) -> None:
            self._connection = real_connect(path, **kwargs)

        def execute(self, *args: Any, **kwargs: Any) -> Any:
            return self._connection.execute(*args, **kwargs)

        def executescript(self, _script: str) -> None:
            raise sqlite3.OperationalError("simulated interrupted initialization")

        def close(self) -> None:
            self._connection.close()

    monkeypatch.setattr(recipe_delivery.sqlite3, "connect", _InterruptedConnection)
    with pytest.raises(sqlite3.OperationalError, match="interrupted initialization"):
        RecipeDeliveryReceiptLedger.initialize_protected(authority)

    final_path = authority.root / "codex-recipe-delivery.sqlite3"
    assert not final_path.exists()
    assert list(authority.root.glob("*.tmp")) == []

    monkeypatch.setattr(recipe_delivery.sqlite3, "connect", real_connect)
    ledger = RecipeDeliveryReceiptLedger.initialize_protected(authority)
    reopened = RecipeDeliveryReceiptLedger.open_existing(authority)
    assert reopened is not None
    assert reopened.path == ledger.path


def test_initialization_fails_closed_when_private_permissions_cannot_be_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    authority = _authority(tmp_path)

    def _deny_chmod(_path: Path, _mode: int) -> None:
        raise PermissionError("simulated protected-store chmod failure")

    monkeypatch.setattr(recipe_delivery.os, "chmod", _deny_chmod)
    with pytest.raises(RuntimeError, match="permissions unavailable"):
        RecipeDeliveryReceiptLedger.initialize_protected(authority)

    assert not (authority.root / "codex-recipe-delivery.sqlite3").exists()
    assert list(authority.root.glob("*.tmp")) == []


def test_open_existing_rejects_nonprivate_store_permissions(tmp_path: Path) -> None:
    authority = _authority(tmp_path)
    ledger = RecipeDeliveryReceiptLedger.initialize_protected(authority)
    ledger.path.chmod(0o640)

    assert RecipeDeliveryReceiptLedger.open_existing(authority) is None


def test_owner_checked_commit_and_abort(tmp_path: Path) -> None:
    ledger = RecipeDeliveryReceiptLedger.initialize_protected(_authority(tmp_path))
    first = _reserve(ledger, _ledger_attestation("thread-owner-commit"))
    assert first.handle is not None
    assert ledger.commit(replace(first.handle, owner_token="forged"), now_unix=_NOW + 1) is False
    assert ledger.commit(first.handle, now_unix=_NOW + 1) is True
    assert ledger.receipt_status(first.handle.thread_id) == "committed"
    assert ledger.abort(first.handle) is False
    later_call = replace(
        _ledger_attestation("thread-owner-commit"),
        outer_call_id="outer-owner-002",
        nonce="nonce-owner-002",
    )
    assert _reserve(ledger, later_call).reason == "receipt_committed"

    second = _reserve(ledger, _ledger_attestation("thread-owner-abort"))
    assert second.handle is not None
    assert ledger.abort(replace(second.handle, owner_token="forged")) is False
    assert ledger.abort(second.handle) is True
    assert ledger.receipt_status(second.handle.thread_id) is None


def test_consumed_call_replay_survives_abort_and_store_reopen(tmp_path: Path) -> None:
    authority = _authority(tmp_path)
    ledger = RecipeDeliveryReceiptLedger.initialize_protected(authority)
    attestation = _ledger_attestation("thread-replay")
    first = _reserve(ledger, attestation)
    assert first.handle is not None
    assert ledger.abort(first.handle) is True

    reopened = RecipeDeliveryReceiptLedger.open_existing(authority)
    assert reopened is not None
    replay = _reserve(reopened, attestation)
    assert replay.handle is None
    assert replay.reason == "host_call_replayed"


def test_stale_pending_recovery_preserves_consumed_call(tmp_path: Path) -> None:
    authority = _authority(tmp_path)
    ledger = RecipeDeliveryReceiptLedger.initialize_protected(authority)
    attestation = _ledger_attestation("thread-stale")
    first = _reserve(ledger, attestation)
    assert first.handle is not None
    assert ledger.recover_stale_pending(thread_id=attestation.thread_id, before_unix=_NOW + 1)
    assert ledger.receipt_status(attestation.thread_id) is None
    reopened = RecipeDeliveryReceiptLedger.open_existing(authority)
    assert reopened is not None
    assert _reserve(reopened, attestation).reason == "host_call_replayed"


def test_store_busy_fails_closed_without_partial_reservation(tmp_path: Path) -> None:
    ledger = RecipeDeliveryReceiptLedger.initialize_protected(_authority(tmp_path))
    lock = sqlite3.connect(ledger.path, timeout=0, isolation_level=None)
    try:
        lock.execute("BEGIN IMMEDIATE")
        result = _reserve(ledger, _ledger_attestation("thread-busy"))
    finally:
        lock.execute("ROLLBACK")
        lock.close()
    assert result.handle is None
    assert result.reason == "store_busy"
    assert ledger.receipt_status("thread-busy") is None


@pytest.mark.parametrize(
    "changes",
    [
        {"root": Path("relative-store")},
        {"security_identity": ""},
        {"local_filesystem": False},
        {"caller_writable": True},
        {"initialized_by_host": False},
    ],
)
def test_unprotected_or_nonlocal_store_is_rejected(
    tmp_path: Path,
    changes: dict[str, object],
) -> None:
    authority = _authority(tmp_path, **changes)
    with pytest.raises(ValueError, match="protected local store authority required"):
        RecipeDeliveryReceiptLedger.initialize_protected(authority)
    assert RecipeDeliveryReceiptLedger.open_existing(authority) is None


def test_open_existing_rejects_security_identity_drift(tmp_path: Path) -> None:
    authority = _authority(tmp_path)
    RecipeDeliveryReceiptLedger.initialize_protected(authority)
    changed = replace(authority, security_identity="different-protected-host")
    assert RecipeDeliveryReceiptLedger.open_existing(changed) is None
