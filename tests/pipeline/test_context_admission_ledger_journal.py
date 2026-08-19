"""Journal tests for the crash-safe context-admission ledger.

Part of the test split for issue #4606.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from pathlib import Path
from types import MappingProxyType

import pytest

import autoskillit.core.types._type_context_admission_persistence as persistence_types
from autoskillit.core import (
    AuthorityUnavailableEvent,
    ContextAdmissionAccountingStatus,
    ContextAdmissionStorageFailureReason,
    ContextAdmissionStorageHealthStatus,
    CoverageState,
)
from autoskillit.pipeline.context_admission_ledger import (
    DefaultContextAdmissionLedger,
)
from tests.fixtures.context_admission import (
    batch,
    event_fields,
    occurrence,
    open_event,
    propose_event,
    reserve_event,
    stream_key,
    uninitialized_state,
)
from tests.pipeline._context_admission_ledger_helpers import (
    _authority,
)

_GOLDEN_JOURNAL = (
    Path(__file__).resolve().parent.parent
    / "fixtures"
    / "context_admission_journals"
    / "protocol_v1_encoding_v1.json"
)

pytestmark = [pytest.mark.layer("pipeline"), pytest.mark.small]


def test_ledger_publication_matches_protocol_v1_golden_journal(tmp_path: Path) -> None:
    fixture = json.loads(_GOLDEN_JOURNAL.read_text(encoding="utf-8"))
    authority = _authority(tmp_path)
    ledger = DefaultContextAdmissionLedger(authority)
    event = AuthorityUnavailableEvent(
        **event_fields(
            uninitialized_state(),
            "event-authority-unavailable",
            "authority-unavailable",
        ),
        reason_code="authoritative-watermark-unavailable",
        authority_state=CoverageState.PARTIAL,
    )

    recorded = ledger.apply(stream_key(), event)
    assert recorded.status is ContextAdmissionAccountingStatus.SEMANTIC_REJECTION
    assert recorded.transition is not None

    connection = sqlite3.connect(authority.database_path)
    try:
        journal = connection.execute(
            """
            SELECT journal_sequence, event_envelope, decision_envelope
            FROM journal_events ORDER BY journal_sequence
            """
        ).fetchall()
        effects = connection.execute(
            """
            SELECT journal_sequence, effect_ordinal, effect_envelope
            FROM effect_outbox ORDER BY journal_sequence, effect_ordinal
            """
        ).fetchall()
        shadows = connection.execute(
            """
            SELECT journal_sequence, shadow_envelope
            FROM shadow_decisions ORDER BY journal_sequence
            """
        ).fetchall()
        final_state = connection.execute("SELECT state_envelope FROM streams").fetchone()
    finally:
        connection.close()

    publication = fixture["publications"][0]
    assert journal == [
        (
            publication["journal_sequence"],
            publication["event_envelope"].encode("utf-8"),
            publication["decision_envelope"].encode("utf-8"),
        )
    ]
    assert effects == []
    assert shadows == [
        (
            publication["journal_sequence"],
            publication["shadow_envelope"].encode("utf-8"),
        )
    ]
    assert final_state == (fixture["final_state_envelope"].encode("utf-8"),)

    reopened = DefaultContextAdmissionLedger(authority)
    assert reopened.recover_all().status is ContextAdmissionStorageHealthStatus.HEALTHY
    assert reopened.replay(stream_key()).state == recorded.transition.next_state


def test_inspection_replays_validly_encoded_publications_after_recovery(
    tmp_path: Path,
) -> None:
    authority = _authority(tmp_path)
    key = stream_key()
    ledger = DefaultContextAdmissionLedger(authority)
    assert ledger.apply(key, open_event()).status is ContextAdmissionAccountingStatus.RECORDED
    connection = sqlite3.connect(authority.database_path)
    try:
        encoded = bytes(
            connection.execute("SELECT decision_envelope FROM journal_events").fetchone()[0]
        )
        envelope = persistence_types.decode_stored_context_admission_envelope(encoded)
        assert isinstance(envelope.payload, persistence_types.AdmissionDecision)
        tampered = replace(envelope.payload, reason_code="validly-encoded-tamper")
        connection.execute(
            "UPDATE journal_events SET decision_envelope = ?",
            (
                persistence_types.encode_stored_context_admission_envelope(
                    persistence_types.make_stored_context_admission_envelope(tampered)
                ),
            ),
        )
        connection.commit()
    finally:
        connection.close()

    inspection = ledger.inspect_stream(key)

    assert inspection.health.status is ContextAdmissionStorageHealthStatus.FAIL_CLOSED
    assert inspection.health.failure_reason is ContextAdmissionStorageFailureReason.REPLAY_MISMATCH
    assert inspection.health.reason_code == "journal-decision-mismatch"


def test_recovery_rejects_journal_event_identity_mismatch(tmp_path: Path) -> None:
    authority = _authority(tmp_path)
    key = stream_key()
    assert (
        DefaultContextAdmissionLedger(authority).apply(key, open_event()).status
        is ContextAdmissionAccountingStatus.RECORDED
    )
    connection = sqlite3.connect(authority.database_path)
    try:
        connection.execute(
            "UPDATE journal_events SET event_id = ?",
            ("damaged-event-id",),
        )
        connection.commit()
    finally:
        connection.close()

    recovered = DefaultContextAdmissionLedger(authority)
    result = recovered.recover_all()

    assert result.status is ContextAdmissionStorageHealthStatus.HEALTHY
    health = recovered.stream_health(key)
    assert health.status is ContextAdmissionStorageHealthStatus.FAIL_CLOSED
    assert health.failure_reason is ContextAdmissionStorageFailureReason.REPLAY_MISMATCH
    assert health.reason_code == "journal-event-identity-mismatch"


def test_recovery_preflight_rejects_unsupported_encoding_without_rewrite(
    tmp_path: Path,
) -> None:
    authority = _authority(tmp_path)
    ledger = DefaultContextAdmissionLedger(authority)
    assert (
        ledger.apply(stream_key(), open_event()).status
        is ContextAdmissionAccountingStatus.RECORDED
    )
    connection = sqlite3.connect(authority.database_path)
    try:
        original = bytes(
            connection.execute("SELECT event_envelope FROM journal_events").fetchone()[0]
        )
        unsupported = original.replace(
            b'"encoding_version":1',
            b'"encoding_version":2',
            1,
        )
        connection.execute(
            "UPDATE journal_events SET event_envelope = ?",
            (unsupported,),
        )
        connection.commit()
    finally:
        connection.close()

    recovered = DefaultContextAdmissionLedger(authority)
    result = recovered.recover_all()

    assert result.status is ContextAdmissionStorageHealthStatus.FAIL_CLOSED
    assert (
        result.store_health.failure_reason
        is ContextAdmissionStorageFailureReason.UNSUPPORTED_ENCODING
    )
    connection = sqlite3.connect(authority.database_path)
    try:
        assert (
            bytes(connection.execute("SELECT event_envelope FROM journal_events").fetchone()[0])
            == unsupported
        )
    finally:
        connection.close()


@pytest.mark.parametrize(
    ("metadata_key", "invalid_value", "expected_reason", "expected_reason_code"),
    [
        (
            "schema_version",
            "2",
            ContextAdmissionStorageFailureReason.UNSUPPORTED_SCHEMA,
            "invalid-schema-version",
        ),
        (
            "encoding_version",
            "2",
            ContextAdmissionStorageFailureReason.UNSUPPORTED_ENCODING,
            "invalid-encoding-version",
        ),
        (
            "protocol_version",
            "2",
            ContextAdmissionStorageFailureReason.UNSUPPORTED_PROTOCOL,
            "invalid-protocol-version",
        ),
        (
            "store_health",
            "fail_closed",
            ContextAdmissionStorageFailureReason.INTEGRITY,
            "invalid-store-health",
        ),
    ],
)
def test_recovery_rejects_invalid_metadata_without_rewrite(
    tmp_path: Path,
    metadata_key: str,
    invalid_value: str,
    expected_reason: ContextAdmissionStorageFailureReason,
    expected_reason_code: str,
) -> None:
    authority = _authority(tmp_path)
    assert (
        DefaultContextAdmissionLedger(authority).recover_all().status
        is ContextAdmissionStorageHealthStatus.HEALTHY
    )
    connection = sqlite3.connect(authority.database_path)
    try:
        connection.execute(
            "UPDATE metadata SET value = ? WHERE key = ?",
            (invalid_value, metadata_key),
        )
        connection.commit()
    finally:
        connection.close()

    result = DefaultContextAdmissionLedger(authority).recover_all()

    assert result.status is ContextAdmissionStorageHealthStatus.FAIL_CLOSED
    assert result.store_health.failure_reason is expected_reason
    assert result.store_health.reason_code == expected_reason_code
    connection = sqlite3.connect(authority.database_path)
    try:
        stored = connection.execute(
            "SELECT value FROM metadata WHERE key = ?",
            (metadata_key,),
        ).fetchone()
        assert stored == (invalid_value,)
    finally:
        connection.close()


def test_recovery_decodes_supported_legacy_encoding_without_rewrite(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    authority = _authority(tmp_path)
    ledger = DefaultContextAdmissionLedger(authority)
    assert (
        ledger.apply(stream_key(), open_event()).status
        is ContextAdmissionAccountingStatus.RECORDED
    )
    envelope_columns = (
        ("streams", "genesis_envelope"),
        ("streams", "state_envelope"),
        ("journal_events", "event_envelope"),
        ("journal_events", "decision_envelope"),
        ("effect_outbox", "effect_envelope"),
        ("shadow_decisions", "shadow_envelope"),
    )
    connection = sqlite3.connect(authority.database_path)
    try:
        for table, column in envelope_columns:
            for row_id, value in connection.execute(f"SELECT rowid, {column} FROM {table}"):
                legacy = bytes(value).replace(
                    b'"encoding_version":1',
                    b'"encoding_version":0',
                    1,
                )
                connection.execute(
                    f"UPDATE {table} SET {column} = ? WHERE rowid = ?",
                    (legacy, row_id),
                )
        connection.commit()
    finally:
        connection.close()
    monkeypatch.setattr(
        persistence_types,
        "CONTEXT_ADMISSION_ENVELOPE_UPCASTERS",
        MappingProxyType(
            {
                (0, 1): lambda value: value.replace(
                    b'"encoding_version":0',
                    b'"encoding_version":1',
                    1,
                )
            }
        ),
    )

    result = DefaultContextAdmissionLedger(authority).recover_all()

    assert result.status is ContextAdmissionStorageHealthStatus.HEALTHY
    connection = sqlite3.connect(authority.database_path)
    try:
        stored = bytes(
            connection.execute("SELECT event_envelope FROM journal_events").fetchone()[0]
        )
        assert b'"encoding_version":0' in stored
    finally:
        connection.close()


def test_reducer_transition_is_published_atomically_with_independent_journal_order(
    tmp_path: Path,
) -> None:
    authority = _authority(tmp_path)
    ledger = DefaultContextAdmissionLedger(authority)
    key = stream_key()

    opened = ledger.apply(key, open_event())
    assert opened.status is ContextAdmissionAccountingStatus.RECORDED
    assert opened.journal_sequence == 1
    assert opened.transition is not None
    occurrence_value = occurrence()
    proposed = ledger.apply(
        key,
        propose_event(opened.transition.next_state, occurrence_value),
    )
    assert proposed.journal_sequence == 2
    assert proposed.transition is not None
    reserved = ledger.reserve(
        key,
        reserve_event(
            proposed.transition.next_state,
            batch(occurrence_value),
            occurrence_value,
            generation_allowance=15,
        ),
    )
    assert reserved.status is ContextAdmissionAccountingStatus.RECORDED
    assert reserved.journal_sequence == 3
    assert reserved.transition is not None
    assert reserved.transition.next_state.admission_sequence.value == 1
    inspection = ledger.inspect_stream(key)
    assert inspection.events[-1].event_id.value == "event-reserve"
    assert inspection.effects[-1] == reserved.transition.effects
    assert inspection.state == reserved.transition.next_state
    input_target, generation_target = inspection.shadows[-1].targets
    assert input_target.proposed_input_count == 10
    assert input_target.generation_allowance is None
    assert input_target.producer_surfaces == (occurrence_value.lineage.producer_surface,)
    assert input_target.turn_ids == (occurrence_value.lineage.turn_id,)
    assert generation_target.proposed_input_count is None
    assert generation_target.generation_allowance == 15
    assert generation_target.exact_input_charge is None
    assert generation_target.exact_output_charge is None

    connection = sqlite3.connect(authority.database_path)
    try:
        assert connection.execute("SELECT COUNT(*) FROM journal_events").fetchone() == (3,)
        assert connection.execute("SELECT COUNT(*) FROM shadow_decisions").fetchone() == (3,)
        stream_row = connection.execute(
            """
            SELECT latest_journal_sequence, aggregate_revision, admission_sequence
            FROM streams
            """
        ).fetchone()
        assert stream_row == (
            3,
            reserved.transition.next_state.aggregate_revision.value,
            1,
        )
    finally:
        connection.close()
