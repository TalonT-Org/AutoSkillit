"""Canonical shell-capture snapshot and runner-request contract tests."""

from __future__ import annotations

import base64
import json
from dataclasses import replace

import pytest

import autoskillit.hooks._capture_contract as contract
from autoskillit.core import (
    AUTOSKILLIT_PRIVATE_ENV_VARS,
    CODEX_MCP_ENV_FORWARD_VARS,
    MANAGED_ATTEMPT_ID_ENV_VAR,
    MANAGED_HEADLESS_SESSION_LINEAGE_SCHEMA_VERSION,
    MANAGED_LAUNCH_ID_ENV_VAR,
    MANAGED_LINEAGE_DIGEST_ENV_VAR,
    MANAGED_LINEAGE_REF_ENV_VAR,
    NATIVE_SHELL_CAPTURE_MODE_ENV_VAR,
    ManagedHeadlessSessionLineageRef,
    NativeShellCaptureMode,
)
from autoskillit.hooks._capture_contract import (
    CAPTURE_REQUEST_PROTOCOL_VERSION,
    CAPTURE_FAILURE_V3_PRODUCER,
    CAPTURE_FAILURE_V3_SCHEMA_VERSION,
    CAPTURE_V2_PRODUCER,
    CAPTURE_V2_SCHEMA_VERSION,
    MAX_CAPTURE_FAILURE_V2_BYTES,
    MAX_CAPTURE_FAILURE_V3_BYTES,
    MAX_CAPTURE_V2_MARKER_BYTES,
    CaptureContractError,
    CaptureFailureReason,
    CaptureFailureV2,
    CaptureFailureV3,
    CaptureLineageRef,
    CaptureProtocolError,
    CaptureRequest,
    CaptureV2Fields,
    capture_v2_encoded_length,
    capture_v2_worst_case_bytes,
    decode_capture_request,
    decode_lineage_ref_json,
    encode_capture_request,
    parse_capture_failure_v2,
    parse_capture_failure_v3,
    parse_capture_v2,
    render_capture_failure_v2,
    render_capture_failure_v3,
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
    assert parsed.capture_status == "complete"
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


_ATTEMPT_ID = "a" * 32
_LAUNCH_ID = "b" * 32


def _lineage_ref(**changes: object) -> CaptureLineageRef:
    values = {
        "schema_version": 1,
        "launch_id": _LAUNCH_ID,
        "lineage_digest": _DIGEST,
        "lineage_anchor": "/lineage/anchor",
        "anchor_device": 12,
        "anchor_inode": 34,
    }
    values.update(changes)
    return CaptureLineageRef(**values)  # type: ignore[arg-type]


def _request(**changes: object) -> CaptureRequest:
    values = {
        "protocol_version": CAPTURE_REQUEST_PROTOCOL_VERSION,
        "action": "run",
        "mode": "capture",
        "attempt_id": _ATTEMPT_ID,
        "lineage_ref": _lineage_ref(),
        "cwd": "/command/cwd",
        "capture_id": _CAPTURE_ID,
        "command": "printf hello",
    }
    values.update(changes)
    return CaptureRequest(**values)  # type: ignore[arg-type]


def _request_object(request: CaptureRequest | None = None) -> dict[str, object]:
    encoded = encode_capture_request(_request() if request is None else request)
    return json.loads(base64.b64decode(encoded, validate=True))


def _canonical_wire(value: object) -> str:
    raw = json.dumps(
        value,
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")
    return base64.b64encode(raw).decode("ascii")


@pytest.mark.parametrize(
    "capture_request",
    [
        _request(),
        _request(mode="direct"),
        _request(
            mode="capture",
            attempt_id=None,
            lineage_ref=None,
        ),
        _request(
            action="reject",
            command=None,
        ),
    ],
)
def test_request_round_trip_is_canonical_and_padded(
    capture_request: CaptureRequest,
) -> None:
    encoded = encode_capture_request(capture_request)

    assert len(encoded) % 4 == 0
    assert base64.b64encode(base64.b64decode(encoded, validate=True)).decode() == encoded
    assert decode_capture_request(encoded) == capture_request
    decoded = base64.b64decode(encoded, validate=True)
    assert decoded == contract.canonical_json_bytes(json.loads(decoded))


def test_wire_contract_matches_core_closed_values() -> None:
    assert {mode.value for mode in NativeShellCaptureMode} == {"capture", "direct"}
    assert (
        contract.MANAGED_LINEAGE_REF_SCHEMA_VERSION
        == MANAGED_HEADLESS_SESSION_LINEAGE_SCHEMA_VERSION
    )
    core_ref = ManagedHeadlessSessionLineageRef(
        launch_id=_LAUNCH_ID,
        lineage_digest=_DIGEST,
        lineage_anchor="/lineage/anchor",
        anchor_device=12,
        anchor_inode=34,
    )
    assert core_ref.to_dict() == {
        "schema_version": 1,
        "launch_id": _LAUNCH_ID,
        "lineage_digest": _DIGEST,
        "lineage_anchor": "/lineage/anchor",
        "anchor_device": 12,
        "anchor_inode": 34,
    }
    core_protected = {
        NATIVE_SHELL_CAPTURE_MODE_ENV_VAR,
        MANAGED_LAUNCH_ID_ENV_VAR,
        MANAGED_ATTEMPT_ID_ENV_VAR,
        MANAGED_LINEAGE_DIGEST_ENV_VAR,
        MANAGED_LINEAGE_REF_ENV_VAR,
    }
    assert contract.PROTECTED_CAPTURE_ENV_VARS == core_protected
    assert core_protected <= AUTOSKILLIT_PRIVATE_ENV_VARS
    assert core_protected.isdisjoint(CODEX_MCP_ENV_FORWARD_VARS)


def test_producer_and_consumer_validation_paths_are_independent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    encoded = encode_capture_request(_request())
    monkeypatch.setattr(
        contract,
        "_producer_request_object",
        lambda _request: (_ for _ in ()).throw(AssertionError("producer called")),
    )
    assert decode_capture_request(encoded) == _request()


def test_consumer_validation_is_not_used_by_producer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        contract,
        "_consumer_request_from_object",
        lambda _value: (_ for _ in ()).throw(AssertionError("consumer called")),
    )
    assert encode_capture_request(_request())


@pytest.mark.parametrize(
    "capture_request",
    [
        _request(protocol_version=True),
        _request(protocol_version=2),
        _request(action="execute"),
        _request(mode="DIRECT"),
        _request(attempt_id="x" * 32),
        _request(attempt_id=None),
        _request(lineage_ref=None),
        _request(mode="direct", attempt_id=None, lineage_ref=None),
        _request(cwd="relative"),
        _request(cwd="/bad\x00cwd"),
        _request(cwd="/" + "x" * (contract._MAX_PATH_BYTES + 1)),
        _request(capture_id="0" * 15),
        _request(capture_id="g" * 16),
        _request(command=None),
        _request(command=""),
        _request(command="bad\x00command"),
        _request(command="x" * (contract._MAX_COMMAND_BYTES + 1)),
        _request(action="reject", command="inappropriate"),
    ],
)
def test_producer_rejects_invalid_request(capture_request: CaptureRequest) -> None:
    with pytest.raises(CaptureProtocolError):
        encode_capture_request(capture_request)


@pytest.mark.parametrize(
    "reference",
    [
        _lineage_ref(schema_version=True),
        _lineage_ref(schema_version=2),
        _lineage_ref(launch_id="x" * 32),
        _lineage_ref(lineage_digest="d" * 63),
        _lineage_ref(lineage_anchor="relative"),
        _lineage_ref(lineage_anchor="/bad\x00anchor"),
        _lineage_ref(anchor_device=True),
        _lineage_ref(anchor_device=-1),
        _lineage_ref(anchor_inode=True),
        _lineage_ref(anchor_inode=-1),
    ],
)
def test_producer_rejects_invalid_lineage_reference(
    reference: CaptureLineageRef,
) -> None:
    with pytest.raises(CaptureProtocolError):
        encode_capture_request(_request(lineage_ref=reference))


@pytest.mark.parametrize(
    "encoded",
    [
        "",
        "not-base64!",
        "YQ",
        "YQ==\n",
        "e31=",
        "A" * (contract._MAX_ENCODED_REQUEST_BYTES + 4),
    ],
)
def test_consumer_rejects_malformed_or_oversized_base64(encoded: str) -> None:
    with pytest.raises(CaptureProtocolError):
        decode_capture_request(encoded)


def test_consumer_bounds_decoded_bytes_independently() -> None:
    raw = b" " * (contract._MAX_DECODED_REQUEST_BYTES + 1)
    encoded = base64.b64encode(raw).decode("ascii")
    assert len(encoded) <= contract._MAX_ENCODED_REQUEST_BYTES

    with pytest.raises(CaptureProtocolError, match="decoded request exceeds limit"):
        decode_capture_request(encoded)


@pytest.mark.parametrize(
    "raw",
    [
        b'{"action":"reject","action":"run"}',
        (
            b'{"action":"reject","attempt_id":null,"capture_id":"0123456789abcdef",'
            b'"cwd":"/cwd","lineage_ref":{"schema_version":1,"schema_version":1},'
            b'"mode":"capture","protocol_version":1}'
        ),
        b'{ "action": "reject" }',
        b'{"z":1,"a":2}',
        b"[]",
        b"\xff",
    ],
)
def test_consumer_rejects_duplicate_or_noncanonical_json(raw: bytes) -> None:
    with pytest.raises(CaptureProtocolError):
        decode_capture_request(base64.b64encode(raw).decode("ascii"))


@pytest.mark.parametrize(
    "raw",
    [
        ("[" * 1100 + "0" + "]" * 1100).encode(),
        b'{"protocol_version":' + (b"9" * 5000) + b"}",
    ],
)
def test_consumer_contains_json_parser_resource_errors(raw: bytes) -> None:
    with pytest.raises(CaptureProtocolError):
        decode_capture_request(base64.b64encode(raw).decode("ascii"))


@pytest.mark.parametrize(
    ("mutation", "value"),
    [
        ("missing", "command"),
        ("unknown", "extra"),
        ("unsupported_version", 2),
        ("bool_version", True),
        ("invalid_action", "execute"),
        ("invalid_mode", "DIRECT"),
        ("bool_device", True),
        ("action_inappropriate", "command"),
    ],
)
def test_consumer_rejects_closed_schema_violations(
    mutation: str,
    value: object,
) -> None:
    request = _request_object()
    if mutation == "missing":
        request.pop(str(value))
    elif mutation == "unknown":
        request[str(value)] = 1
    elif mutation in {"unsupported_version", "bool_version"}:
        request["protocol_version"] = value
    elif mutation == "invalid_action":
        request["action"] = value
    elif mutation == "invalid_mode":
        request["mode"] = value
    elif mutation == "bool_device":
        assert isinstance(request["lineage_ref"], dict)
        request["lineage_ref"]["anchor_device"] = value
    elif mutation == "action_inappropriate":
        request["action"] = "reject"

    with pytest.raises(CaptureProtocolError):
        decode_capture_request(_canonical_wire(request))


@pytest.mark.parametrize(
    "changes",
    (
        {"command_outcome_value": True},
        {"shell_returncode": 143},
        {"reference": "0" * 64},
        {"unavailable_reason": "UNEXPECTED"},
    ),
)
def test_capture_fields_reject_inconsistent_construction(
    changes: dict[str, object],
) -> None:
    with pytest.raises(CaptureContractError):
        replace(_published_fields(), **changes)


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
        replace(failure, detail="x" * 241)


@pytest.mark.parametrize(
    "changes",
    [
        {"attempt_id": None},
        {"lineage_ref": None},
        {"mode": "direct", "attempt_id": None, "lineage_ref": None},
        {"attempt_id": "z" * 32},
        {"cwd": "relative"},
        {"capture_id": "f" * 15},
        {"command": ""},
    ],
)
def test_consumer_rejects_invalid_values_independently(
    changes: dict[str, object],
) -> None:
    request = _request_object()
    request.update(changes)
    with pytest.raises(CaptureProtocolError):
        decode_capture_request(_canonical_wire(request))


@pytest.mark.parametrize(
    ("field_name", "invalid_value"),
    [
        ("schema_version", True),
        ("schema_version", 2),
        ("launch_id", "x" * 32),
        ("lineage_digest", "d" * 63),
        ("lineage_anchor", "relative"),
        ("lineage_anchor", "/bad\x00anchor"),
        ("anchor_device", True),
        ("anchor_device", -1),
        ("anchor_inode", True),
        ("anchor_inode", -1),
    ],
)
def test_consumer_rejects_invalid_lineage_values_independently(
    field_name: str,
    invalid_value: object,
) -> None:
    request = _request_object()
    lineage_ref = request["lineage_ref"]
    assert isinstance(lineage_ref, dict)
    lineage_ref[field_name] = invalid_value

    with pytest.raises(CaptureProtocolError):
        decode_capture_request(_canonical_wire(request))


@pytest.mark.parametrize("mutation", ["missing", "unknown"])
def test_consumer_rejects_non_exact_lineage_schema(mutation: str) -> None:
    request = _request_object()
    lineage_ref = request["lineage_ref"]
    assert isinstance(lineage_ref, dict)
    if mutation == "missing":
        lineage_ref.pop("lineage_digest")
    else:
        lineage_ref["unknown"] = "field"

    with pytest.raises(CaptureProtocolError):
        decode_capture_request(_canonical_wire(request))


def test_capture_is_the_only_mode_allowed_with_null_lineage() -> None:
    capture_request = _request(
        mode="capture",
        attempt_id=None,
        lineage_ref=None,
    )
    assert decode_capture_request(encode_capture_request(capture_request)) == capture_request

    with pytest.raises(CaptureProtocolError):
        encode_capture_request(
            replace(
                capture_request,
                mode="direct",
            )
        )


def test_protected_lineage_reference_requires_canonical_exact_json() -> None:
    serialized = contract.canonical_json_bytes(
        {
            "schema_version": 1,
            "launch_id": _LAUNCH_ID,
            "lineage_digest": _DIGEST,
            "lineage_anchor": "/lineage/anchor",
            "anchor_device": 12,
            "anchor_inode": 34,
        }
    ).decode("ascii")
    assert decode_lineage_ref_json(serialized) == _lineage_ref()

    with pytest.raises(CaptureProtocolError):
        decode_lineage_ref_json(" " + serialized)
    with pytest.raises(CaptureProtocolError):
        decode_lineage_ref_json(
            serialized.replace('"schema_version":1', '"schema_version":1,"schema_version":1')
        )
    with pytest.raises(CaptureProtocolError):
        decode_lineage_ref_json(" " * (contract._MAX_LINEAGE_REF_JSON_BYTES + 1))


@pytest.mark.parametrize("reason", tuple(CaptureFailureReason))
def test_v3_failure_frame_has_closed_reason_and_is_canonical(
    reason: CaptureFailureReason,
) -> None:
    failure = CaptureFailureV3(
        reason=reason,
        stage="capture_setup",
        detail="capture setup failed",
        shell_returncode=None,
        settlement_returncode=None,
    )

    encoded = render_capture_failure_v3(failure)
    decoded = json.loads(encoded.removeprefix(b"[AutoSkillit shell capture failure v3:")[:-1])

    assert parse_capture_failure_v3(encoded) == failure
    assert decoded == {
        "detail": "capture setup failed",
        "producer": CAPTURE_FAILURE_V3_PRODUCER,
        "reason": reason.value,
        "schema_version": CAPTURE_FAILURE_V3_SCHEMA_VERSION,
        "settlement_returncode": None,
        "shell_returncode": None,
        "stage": "capture_setup",
        "status": "capture_failed",
    }
    assert len(encoded) <= MAX_CAPTURE_FAILURE_V3_BYTES
    with pytest.raises(CaptureContractError):
        parse_capture_failure_v2(encoded)


@pytest.mark.parametrize(
    "mutation",
    (
        lambda value: value.replace(b'"reason":"UNKNOWN_SETUP"', b'"reason":"UNKNOWN"'),
        lambda value: value.replace(b'"schema_version":3', b'"schema_version":2'),
        lambda value: value.replace(b'"reason"', b'"extra":true,"reason"'),
        lambda value: value.replace(b":{", b":{ ", 1),
    ),
)
def test_v3_failure_parser_rejects_unknown_wrong_extra_and_noncanonical(mutation) -> None:
    encoded = render_capture_failure_v3(
        CaptureFailureV3(
            reason=CaptureFailureReason.UNKNOWN_SETUP,
            stage="capture_setup",
            detail="capture setup failed",
            shell_returncode=None,
            settlement_returncode=None,
        )
    )

    with pytest.raises(CaptureContractError):
        parse_capture_failure_v3(mutation(encoded))

    with pytest.raises(CaptureContractError, match="bound"):
        parse_capture_failure_v3(b"x" * (MAX_CAPTURE_FAILURE_V3_BYTES + 1))
