"""Canonical V2 shell-capture transport contract tests."""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from autoskillit.hooks._capture_contract import (
    CAPTURE_V2_PRODUCER,
    CAPTURE_V2_SCHEMA_VERSION,
    MAX_CAPTURE_FAILURE_V2_BYTES,
    MAX_CAPTURE_V2_MARKER_BYTES,
    CaptureContractError,
    CaptureFailureV2,
    CaptureV2Fields,
    capture_v2_encoded_length,
    capture_v2_worst_case_bytes,
    parse_capture_failure_v2,
    parse_capture_v2,
    render_capture_failure_v2,
    render_capture_v2,
)

pytestmark = [pytest.mark.layer("hooks"), pytest.mark.medium]

_CAPTURE_ID = "0123456789abcdef"
_INCARNATION = "fedcba9876543210fedcba9876543210"
_TOKEN = f"ascr2:{_CAPTURE_ID}:{_INCARNATION}:{'a' * 64}"
_DIGEST = "b" * 64


class _Renderable:
    def __init__(self, fields: CaptureV2Fields) -> None:
        self._fields = fields

    def capture_v2_fields(self) -> CaptureV2Fields:
        return self._fields


def _published_fields() -> CaptureV2Fields:
    return CaptureV2Fields(
        capture_id=_CAPTURE_ID,
        finalized_at_revision=4,
        total_bytes=12001,
        sha256=_DIGEST,
        command_outcome_kind="exited",
        command_outcome_value=0,
        shell_returncode=0,
        reference_status="published",
        reference=_TOKEN,
        unavailable_reason=None,
    )


def test_published_marker_is_canonical_and_round_trips() -> None:
    fields = _published_fields()
    encoded = render_capture_v2(_Renderable(fields))
    parsed = parse_capture_v2(encoded)

    assert parsed == fields
    assert parsed.schema_version == CAPTURE_V2_SCHEMA_VERSION
    assert parsed.producer == CAPTURE_V2_PRODUCER
    assert parsed.capture_status == "finalized"
    assert parsed.snapshot_status == "verified"
    assert encoded.startswith(b"[AutoSkillit shell capture v2:{")
    assert encoded.endswith(b"}]")
    assert b"complete=true" not in encoded
    assert f"shell_{_CAPTURE_ID}.log".encode() not in encoded
    assert b".log" not in encoded
    assert capture_v2_encoded_length(_Renderable(fields)) == len(encoded)
    assert len(encoded) <= capture_v2_worst_case_bytes()
    assert capture_v2_worst_case_bytes() == MAX_CAPTURE_V2_MARKER_BYTES


def test_unavailable_and_signaled_marker_round_trip() -> None:
    fields = replace(
        _published_fields(),
        command_outcome_kind="signaled",
        command_outcome_value=15,
        shell_returncode=143,
        reference_status="unavailable",
        reference=None,
        unavailable_reason="PUBLICATION_BINDING_UNAVAILABLE",
    )

    assert parse_capture_v2(render_capture_v2(_Renderable(fields))) == fields


@pytest.mark.parametrize(
    "mutate",
    (
        lambda value: value[:-1],
        lambda value: value + b"x",
        lambda value: value.replace(b'"producer"', b'"extra":true,"producer"', 1),
        lambda value: value.replace(
            b'"producer"',
            b'"schema_version":2,"producer"',
            1,
        ),
        lambda value: value.replace(b":{", b":{ ", 1),
    ),
)
def test_capture_parser_rejects_truncated_extra_duplicate_and_noncanonical(
    mutate,
) -> None:
    encoded = render_capture_v2(_Renderable(_published_fields()))

    with pytest.raises(CaptureContractError):
        parse_capture_v2(mutate(encoded))


def test_capture_parser_rejects_oversized_and_wrong_status_fields() -> None:
    with pytest.raises(CaptureContractError, match="bound"):
        parse_capture_v2(b"x" * (MAX_CAPTURE_V2_MARKER_BYTES + 1))

    encoded = render_capture_v2(_Renderable(_published_fields()))
    payload = json.loads(encoded.removeprefix(b"[AutoSkillit shell capture v2:")[:-1])
    payload["snapshot_status"] = "complete"
    forged = (
        b"[AutoSkillit shell capture v2:"
        + json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        + b"]"
    )
    with pytest.raises(CaptureContractError):
        parse_capture_v2(forged)


@pytest.mark.parametrize(
    "fields",
    (
        replace(_published_fields(), command_outcome_value=True),
        replace(_published_fields(), shell_returncode=143),
        replace(_published_fields(), reference="0" * 64),
        replace(_published_fields(), unavailable_reason="UNEXPECTED"),
    ),
)
def test_renderer_rejects_inconsistent_typed_fields(fields: CaptureV2Fields) -> None:
    with pytest.raises(CaptureContractError):
        render_capture_v2(_Renderable(fields))


def test_typed_failure_frame_is_bounded_distinct_and_strict() -> None:
    failure = CaptureFailureV2(
        stage="capture_delivery",
        detail="stdout flush failed",
        shell_returncode=7,
        settlement_returncode=None,
    )
    encoded = render_capture_failure_v2(failure)

    assert len(encoded) <= MAX_CAPTURE_FAILURE_V2_BYTES
    assert parse_capture_failure_v2(encoded) == failure
    with pytest.raises(CaptureContractError):
        parse_capture_v2(encoded)
    with pytest.raises(CaptureContractError):
        parse_capture_failure_v2(encoded.replace(b":{", b":{ ", 1))
    with pytest.raises(CaptureContractError):
        render_capture_failure_v2(replace(failure, detail="x" * 241))
