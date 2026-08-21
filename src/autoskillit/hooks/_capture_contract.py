"""Canonical facade for shell-capture transport contracts (stdlib-only).

Owns the V3 failure envelope framing (``CaptureFailureV3``, the four V3
render/parse functions, the V3 wire-format prefixes) and the cross-cutting
``CaptureContractError`` exception.  V2 capture protocol primitives live
in ``_capture._v2_protocol``; request/lineage codecs live in
``_capture._request_lineage``.  This facade re-exports their public names
so existing import sites continue to work unchanged.

``CaptureContractError`` is defined before the cross-module discriminant so
that ``_capture._v2_protocol``'s runtime import of it resolves during the
first-load cycle.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if __package__:
    from ._capture._module_identity import (
        register_module_aliases as _register_packaged_module,
    )

    _register_packaged_module(__name__)
else:
    from _capture._module_identity import (
        register_module_aliases as _register_standalone_module,
    )

    _register_standalone_module(__name__)

if TYPE_CHECKING:
    from autoskillit.hooks._capture import _failure_policy
elif __package__:
    from ._capture import _failure_policy
else:
    from _capture import _failure_policy

CaptureFailureReason = _failure_policy.CaptureFailureReason


class CaptureContractError(ValueError):
    """Raised when a V2 capture transport value is invalid or noncanonical."""

    failure_reason = CaptureFailureReason.LEDGER_INTEGRITY


if TYPE_CHECKING:
    from autoskillit.hooks._capture._request_lineage import (  # noqa: F401
        _CAPTURE_ID_RE,
        _IDENTITY_RE,
        _MAX_DECODED_REQUEST_BYTES,
        _MAX_ENCODED_REQUEST_BYTES,
        _MAX_LINEAGE_REF_JSON_BYTES,
        _MAX_PATH_BYTES,
        CAPTURE_REQUEST_PROTOCOL_VERSION,
        MANAGED_ATTEMPT_ID_ENV_VAR,
        MANAGED_LAUNCH_ID_ENV_VAR,
        MANAGED_LINEAGE_DIGEST_ENV_VAR,
        MANAGED_LINEAGE_REF_ENV_VAR,
        MANAGED_LINEAGE_REF_SCHEMA_VERSION,
        NATIVE_SHELL_CAPTURE_MODE_ENV_VAR,
        PROTECTED_CAPTURE_ENV_VARS,
        CaptureLineageRef,
        CaptureProtocolError,
        CaptureRequest,
        canonical_json_bytes,
        decode_capture_request,
        decode_lineage_ref_json,
        encode_capture_request,
    )
    from autoskillit.hooks._capture._v2_protocol import (  # noqa: F401
        _FRAME_SUFFIX,
        CAPTURE_V2_PRODUCER,
        CAPTURE_V2_SCHEMA_VERSION,
        MAX_CAPTURE_FAILURE_V2_BYTES,
        MAX_CAPTURE_V2_MARKER_BYTES,
        CaptureFailureV2,
        CaptureV2Fields,
        CaptureV2Renderable,
        _canonical_json,
        _decode_frame,
        _optional_integer_field,
        _string_field,
        capture_v2_encoded_length,
        capture_v2_fields,
        capture_v2_worst_case_bytes,
        parse_capture_failure_v2,
        parse_capture_v2,
        render_capture_failure_v2,
        render_capture_v2,
    )
elif __package__:
    from ._capture._request_lineage import (  # noqa: F401
        _CAPTURE_ID_RE,
        _IDENTITY_RE,
        _MAX_DECODED_REQUEST_BYTES,
        _MAX_ENCODED_REQUEST_BYTES,
        _MAX_LINEAGE_REF_JSON_BYTES,
        _MAX_PATH_BYTES,
        CAPTURE_REQUEST_PROTOCOL_VERSION,
        MANAGED_ATTEMPT_ID_ENV_VAR,
        MANAGED_LAUNCH_ID_ENV_VAR,
        MANAGED_LINEAGE_DIGEST_ENV_VAR,
        MANAGED_LINEAGE_REF_ENV_VAR,
        MANAGED_LINEAGE_REF_SCHEMA_VERSION,
        NATIVE_SHELL_CAPTURE_MODE_ENV_VAR,
        PROTECTED_CAPTURE_ENV_VARS,
        CaptureLineageRef,
        CaptureProtocolError,
        CaptureRequest,
        canonical_json_bytes,
        decode_capture_request,
        decode_lineage_ref_json,
        encode_capture_request,
    )
    from ._capture._v2_protocol import (  # noqa: F401
        _FRAME_SUFFIX,
        CAPTURE_V2_PRODUCER,
        CAPTURE_V2_SCHEMA_VERSION,
        MAX_CAPTURE_FAILURE_V2_BYTES,
        MAX_CAPTURE_V2_MARKER_BYTES,
        CaptureFailureV2,
        CaptureV2Fields,
        CaptureV2Renderable,
        _canonical_json,
        _decode_frame,
        _optional_integer_field,
        _string_field,
        capture_v2_encoded_length,
        capture_v2_fields,
        capture_v2_worst_case_bytes,
        parse_capture_failure_v2,
        parse_capture_v2,
        render_capture_failure_v2,
        render_capture_v2,
    )
else:
    from _capture._request_lineage import (  # noqa: F401
        _CAPTURE_ID_RE,
        _IDENTITY_RE,
        _MAX_DECODED_REQUEST_BYTES,
        _MAX_ENCODED_REQUEST_BYTES,
        _MAX_LINEAGE_REF_JSON_BYTES,
        _MAX_PATH_BYTES,
        CAPTURE_REQUEST_PROTOCOL_VERSION,
        MANAGED_ATTEMPT_ID_ENV_VAR,
        MANAGED_LAUNCH_ID_ENV_VAR,
        MANAGED_LINEAGE_DIGEST_ENV_VAR,
        MANAGED_LINEAGE_REF_ENV_VAR,
        MANAGED_LINEAGE_REF_SCHEMA_VERSION,
        NATIVE_SHELL_CAPTURE_MODE_ENV_VAR,
        PROTECTED_CAPTURE_ENV_VARS,
        CaptureLineageRef,
        CaptureProtocolError,
        CaptureRequest,
        canonical_json_bytes,
        decode_capture_request,
        decode_lineage_ref_json,
        encode_capture_request,
    )
    from _capture._v2_protocol import (  # noqa: F401
        _FRAME_SUFFIX,
        CAPTURE_V2_PRODUCER,
        CAPTURE_V2_SCHEMA_VERSION,
        MAX_CAPTURE_FAILURE_V2_BYTES,
        MAX_CAPTURE_V2_MARKER_BYTES,
        CaptureFailureV2,
        CaptureV2Fields,
        CaptureV2Renderable,
        _canonical_json,
        _decode_frame,
        _optional_integer_field,
        _string_field,
        capture_v2_encoded_length,
        capture_v2_fields,
        capture_v2_worst_case_bytes,
        parse_capture_failure_v2,
        parse_capture_v2,
        render_capture_failure_v2,
        render_capture_v2,
    )

__all__ = [
    "CAPTURE_REQUEST_PROTOCOL_VERSION",
    "CAPTURE_V2_PRODUCER",
    "CAPTURE_V2_SCHEMA_VERSION",
    "CAPTURE_FAILURE_V3_PRODUCER",
    "CAPTURE_FAILURE_V3_SCHEMA_VERSION",
    "MANAGED_ATTEMPT_ID_ENV_VAR",
    "MANAGED_LAUNCH_ID_ENV_VAR",
    "MANAGED_LINEAGE_DIGEST_ENV_VAR",
    "MANAGED_LINEAGE_REF_ENV_VAR",
    "MANAGED_LINEAGE_REF_SCHEMA_VERSION",
    "MAX_CAPTURE_FAILURE_V2_BYTES",
    "MAX_CAPTURE_FAILURE_V3_BYTES",
    "MAX_CAPTURE_V2_MARKER_BYTES",
    "NATIVE_SHELL_CAPTURE_MODE_ENV_VAR",
    "PROTECTED_CAPTURE_ENV_VARS",
    "CaptureContractError",
    "CaptureFailureReason",
    "CaptureFailureV2",
    "CaptureFailureV3",
    "CaptureLineageRef",
    "CaptureProtocolError",
    "CaptureRequest",
    "CaptureV2Fields",
    "CaptureV2Renderable",
    "canonical_json_bytes",
    "capture_v2_encoded_length",
    "capture_v2_fields",
    "capture_v2_worst_case_bytes",
    "decode_capture_request",
    "decode_lineage_ref_json",
    "encode_capture_request",
    "parse_capture_degraded_v3",
    "parse_capture_failure_v2",
    "parse_capture_failure_v3",
    "parse_capture_v2",
    "render_capture_degraded_v3",
    "render_capture_failure_v2",
    "render_capture_failure_v3",
    "render_capture_v2",
]

CAPTURE_FAILURE_V3_SCHEMA_VERSION = 3
CAPTURE_FAILURE_V3_PRODUCER = CAPTURE_V2_PRODUCER
MAX_CAPTURE_FAILURE_V3_BYTES = 1024

_FAILURE_V3_PREFIX = b"[AutoSkillit shell capture failure v3:"
_DEGRADED_V3_PREFIX = b"[AutoSkillit shell capture degraded v3:"
_MAX_COMMAND_BYTES = 64 * 1024

_FAILURE_V3_KEYS = frozenset(
    {
        "detail",
        "producer",
        "reason",
        "schema_version",
        "settlement_returncode",
        "shell_returncode",
        "stage",
        "status",
    }
)
_DEGRADED_V3_KEYS = _FAILURE_V3_KEYS


@dataclass(frozen=True, slots=True)
class CaptureFailureV3:
    reason: CaptureFailureReason
    stage: str
    detail: str
    shell_returncode: int | None
    settlement_returncode: int | None

    def __post_init__(self) -> None:
        _validate_failure_v3(self)


def _plain_int(value: object, *, minimum: int = 0, maximum: int) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and minimum <= value <= maximum


def _validate_failure_v3(value: CaptureFailureV3) -> None:
    if (
        type(value) is not CaptureFailureV3
        or type(value.reason) is not CaptureFailureReason
        or not _failure_policy.valid_failure_stage(value.stage)
        or not _failure_policy.valid_failure_detail(value.detail)
        or (
            value.shell_returncode is not None
            and not _plain_int(value.shell_returncode, maximum=255)
        )
        or (
            value.settlement_returncode is not None
            and (
                not isinstance(value.settlement_returncode, int)
                or isinstance(value.settlement_returncode, bool)
                or not -255 <= value.settlement_returncode <= 255
            )
        )
    ):
        raise CaptureContractError("invalid capture failure V3 transport")


def _failure_v3_primitive(value: CaptureFailureV3) -> dict[str, object]:
    _validate_failure_v3(value)
    return {
        "detail": value.detail,
        "producer": CAPTURE_FAILURE_V3_PRODUCER,
        "reason": value.reason.value,
        "schema_version": CAPTURE_FAILURE_V3_SCHEMA_VERSION,
        "settlement_returncode": value.settlement_returncode,
        "shell_returncode": value.shell_returncode,
        "stage": value.stage,
        "status": "capture_failed",
    }


def render_capture_failure_v3(value: CaptureFailureV3) -> bytes:
    encoded = _FAILURE_V3_PREFIX + _canonical_json(_failure_v3_primitive(value)) + _FRAME_SUFFIX
    if len(encoded) > MAX_CAPTURE_FAILURE_V3_BYTES:
        raise CaptureContractError("capture failure V3 marker exceeds bound")
    return encoded


def parse_capture_failure_v3(value: bytes) -> CaptureFailureV3:
    decoded = _decode_frame(
        value,
        prefix=_FAILURE_V3_PREFIX,
        maximum=MAX_CAPTURE_FAILURE_V3_BYTES,
    )
    if set(decoded) != _FAILURE_V3_KEYS:
        raise CaptureContractError("capture failure V3 fields do not match schema")
    if (
        decoded["schema_version"] != CAPTURE_FAILURE_V3_SCHEMA_VERSION
        or decoded["producer"] != CAPTURE_FAILURE_V3_PRODUCER
        or decoded["status"] != "capture_failed"
    ):
        raise CaptureContractError("capture failure status does not match V3")
    try:
        reason = CaptureFailureReason(_string_field(decoded["reason"]))
    except ValueError as exc:
        raise CaptureContractError("capture failure V3 reason is unknown") from exc
    failure = CaptureFailureV3(
        reason=reason,
        stage=_string_field(decoded["stage"]),
        detail=_string_field(decoded["detail"]),
        shell_returncode=_optional_integer_field(decoded["shell_returncode"]),
        settlement_returncode=_optional_integer_field(decoded["settlement_returncode"]),
    )
    if render_capture_failure_v3(failure) != value:
        raise CaptureContractError("capture failure V3 transport is not canonical")
    return failure


def _degraded_v3_primitive(value: CaptureFailureV3) -> dict[str, object]:
    _validate_failure_v3(value)
    return {
        "detail": value.detail,
        "producer": CAPTURE_FAILURE_V3_PRODUCER,
        "reason": value.reason.value,
        "schema_version": CAPTURE_FAILURE_V3_SCHEMA_VERSION,
        "settlement_returncode": value.settlement_returncode,
        "shell_returncode": value.shell_returncode,
        "stage": value.stage,
        "status": "capture_degraded",
    }


def render_capture_degraded_v3(value: CaptureFailureV3) -> bytes:
    """Render a degraded-delivery diagnostic marker — distinct prefix from failure."""
    encoded = _DEGRADED_V3_PREFIX + _canonical_json(_degraded_v3_primitive(value)) + _FRAME_SUFFIX
    if len(encoded) > MAX_CAPTURE_FAILURE_V3_BYTES:
        raise CaptureContractError("capture degraded V3 marker exceeds bound")
    return encoded


def parse_capture_degraded_v3(value: bytes) -> CaptureFailureV3:
    """Parse a degraded-delivery diagnostic marker."""
    decoded = _decode_frame(
        value,
        prefix=_DEGRADED_V3_PREFIX,
        maximum=MAX_CAPTURE_FAILURE_V3_BYTES,
    )
    if set(decoded) != _DEGRADED_V3_KEYS:
        raise CaptureContractError("capture degraded V3 fields do not match schema")
    if (
        decoded["schema_version"] != CAPTURE_FAILURE_V3_SCHEMA_VERSION
        or decoded["producer"] != CAPTURE_FAILURE_V3_PRODUCER
        or decoded["status"] != "capture_degraded"
    ):
        raise CaptureContractError("capture degraded status does not match V3")
    try:
        reason = CaptureFailureReason(_string_field(decoded["reason"]))
    except ValueError as exc:
        raise CaptureContractError("capture degraded V3 reason is unknown") from exc
    failure = CaptureFailureV3(
        reason=reason,
        stage=_string_field(decoded["stage"]),
        detail=_string_field(decoded["detail"]),
        shell_returncode=_optional_integer_field(decoded["shell_returncode"]),
        settlement_returncode=_optional_integer_field(decoded["settlement_returncode"]),
    )
    if render_capture_degraded_v3(failure) != value:
        raise CaptureContractError("capture degraded V3 transport is not canonical")
    return failure
