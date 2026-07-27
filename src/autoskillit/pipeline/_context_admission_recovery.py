"""Bounded SQLite recovery reads for the context-admission ledger."""

from __future__ import annotations

import sqlite3
from typing import Any, cast

from autoskillit.core import (
    CONTEXT_ADMISSION_ENCODING_VERSION,
    CONTEXT_ADMISSION_TOP_LEVEL_DISCRIMINATORS,
    ContextAdmissionStorageFailureReason,
    ContextAdmissionValidationError,
    context_admission_envelope_header,
    context_admission_reducer_for_protocol,
    decode_stored_context_admission_envelope,
)


class _LedgerOpenError(RuntimeError):
    def __init__(
        self,
        reason: ContextAdmissionStorageFailureReason,
        reason_code: str,
    ) -> None:
        super().__init__(reason_code)
        self.reason = reason
        self.reason_code = reason_code


class _LedgerReadBudget:
    __slots__ = ("_bytes", "_max_bytes", "_max_rows", "_reason_code", "_rows")

    def __init__(
        self,
        reason_code: str,
        *,
        max_rows: int,
        max_bytes: int,
    ) -> None:
        self._rows = 0
        self._bytes = 0
        self._reason_code = reason_code
        self._max_rows = max_rows
        self._max_bytes = max_bytes

    def consume(self, row: tuple[Any, ...]) -> tuple[Any, ...]:
        self._rows += 1
        self._bytes += sum(
            len(value)
            if isinstance(value, bytes | bytearray | memoryview)
            else len(value.encode("utf-8"))
            if isinstance(value, str)
            else 0
            for value in row
        )
        if self._rows > self._max_rows or self._bytes > self._max_bytes:
            raise _LedgerOpenError(
                ContextAdmissionStorageFailureReason.INTEGRITY,
                self._reason_code,
            )
        return row


def _read_bounded_rows(
    cursor: sqlite3.Cursor,
    budget: _LedgerReadBudget,
) -> tuple[tuple[Any, ...], ...]:
    return tuple(budget.consume(cast(tuple[Any, ...], row)) for row in cursor)


def _preflight_storage_routes(
    connection: sqlite3.Connection,
    read_budget: _LedgerReadBudget,
) -> None:
    queries = (
        "SELECT genesis_envelope FROM streams",
        "SELECT state_envelope FROM streams",
        "SELECT event_envelope FROM journal_events",
        "SELECT decision_envelope FROM journal_events",
        "SELECT effect_envelope FROM effect_outbox",
        "SELECT shadow_envelope FROM shadow_decisions",
    )
    for query in queries:
        for (encoded,) in _read_bounded_rows(connection.execute(query), read_budget):
            encoded_bytes = bytes(encoded)
            encoding_version, protocol_version, discriminator = _envelope_header(encoded_bytes)
            if encoding_version != CONTEXT_ADMISSION_ENCODING_VERSION:
                try:
                    envelope = decode_stored_context_admission_envelope(encoded_bytes)
                except ContextAdmissionValidationError as exc:
                    raise _LedgerOpenError(
                        ContextAdmissionStorageFailureReason.UNSUPPORTED_ENCODING,
                        "unsupported-envelope-encoding",
                    ) from exc
                protocol_version = envelope.protocol_version
                discriminator = envelope.type_discriminator
            if discriminator not in CONTEXT_ADMISSION_TOP_LEVEL_DISCRIMINATORS:
                raise _LedgerOpenError(
                    ContextAdmissionStorageFailureReason.UNSUPPORTED_ENCODING,
                    "unsupported-envelope-discriminator",
                )
            try:
                context_admission_reducer_for_protocol(protocol_version)
            except ContextAdmissionValidationError as exc:
                raise _LedgerOpenError(
                    ContextAdmissionStorageFailureReason.UNSUPPORTED_PROTOCOL,
                    "unsupported-envelope-protocol",
                ) from exc


def _envelope_header(value: bytes) -> tuple[int, int, str]:
    try:
        return context_admission_envelope_header(value)
    except ContextAdmissionValidationError:
        raise _LedgerOpenError(
            ContextAdmissionStorageFailureReason.INTEGRITY,
            "invalid-envelope-header",
        ) from None
