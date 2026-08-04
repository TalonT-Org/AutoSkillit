"""Canonical stdlib-only transport contracts for shell capture."""

from __future__ import annotations

import base64
import binascii
import json
import os
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final, Protocol

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
    from autoskillit.hooks._capture._syntax import (
        CAPTURE_ID_RE,
        REFERENCE_RE,
        SHA256_RE,
    )
elif __package__:
    from ._capture import _failure_policy
    from ._capture._syntax import CAPTURE_ID_RE, REFERENCE_RE, SHA256_RE
else:
    from _capture import _failure_policy
    from _capture._syntax import CAPTURE_ID_RE, REFERENCE_RE, SHA256_RE

CaptureFailureReason = _failure_policy.CaptureFailureReason

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
    "parse_capture_failure_v2",
    "parse_capture_failure_v3",
    "parse_capture_v2",
    "render_capture_failure_v2",
    "render_capture_failure_v3",
    "render_capture_v2",
]

CAPTURE_V2_SCHEMA_VERSION = 2
CAPTURE_V2_PRODUCER = "codex_shell_capture"
MAX_CAPTURE_V2_MARKER_BYTES = 2048
MAX_CAPTURE_FAILURE_V2_BYTES = 1024
CAPTURE_FAILURE_V3_SCHEMA_VERSION = 3
CAPTURE_FAILURE_V3_PRODUCER = CAPTURE_V2_PRODUCER
MAX_CAPTURE_FAILURE_V3_BYTES = 1024

CAPTURE_REQUEST_PROTOCOL_VERSION: Final = 1
MANAGED_LINEAGE_REF_SCHEMA_VERSION: Final = 1

NATIVE_SHELL_CAPTURE_MODE_ENV_VAR: Final = "AUTOSKILLIT_NATIVE_SHELL_CAPTURE_MODE"
MANAGED_LAUNCH_ID_ENV_VAR: Final = "AUTOSKILLIT_MANAGED_LAUNCH_ID"
MANAGED_ATTEMPT_ID_ENV_VAR: Final = "AUTOSKILLIT_MANAGED_ATTEMPT_ID"
MANAGED_LINEAGE_DIGEST_ENV_VAR: Final = "AUTOSKILLIT_MANAGED_LINEAGE_DIGEST"
MANAGED_LINEAGE_REF_ENV_VAR: Final = "AUTOSKILLIT_MANAGED_LINEAGE_REF"
PROTECTED_CAPTURE_ENV_VARS: Final = frozenset(
    {
        NATIVE_SHELL_CAPTURE_MODE_ENV_VAR,
        MANAGED_LAUNCH_ID_ENV_VAR,
        MANAGED_ATTEMPT_ID_ENV_VAR,
        MANAGED_LINEAGE_DIGEST_ENV_VAR,
        MANAGED_LINEAGE_REF_ENV_VAR,
    }
)

_CAPTURE_PREFIX = b"[AutoSkillit shell capture v2:"
_FAILURE_PREFIX = b"[AutoSkillit shell capture failure v2:"
_FAILURE_V3_PREFIX = b"[AutoSkillit shell capture failure v3:"
_FRAME_SUFFIX = b"]"
_MAX_COMMAND_BYTES = 64 * 1024
_MAX_SIGNED_VALUE = (1 << 63) - 1
_CAPTURE_ID_RE = CAPTURE_ID_RE
_REFERENCE_RE = REFERENCE_RE
_SHA256_RE = SHA256_RE
_IDENTITY_RE = re.compile(r"^[0-9a-f]{32}$")
_REASON_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
_MAX_PATH_BYTES = 4096
_MAX_LINEAGE_REF_JSON_BYTES = 32 * 1024
_MAX_DECODED_REQUEST_BYTES = 512 * 1024
_MAX_ENCODED_REQUEST_BYTES = ((_MAX_DECODED_REQUEST_BYTES + 2) // 3) * 4
_REQUEST_COMMON_KEYS = frozenset(
    {
        "protocol_version",
        "action",
        "mode",
        "attempt_id",
        "lineage_ref",
        "cwd",
        "capture_id",
    }
)
_REQUEST_RUN_KEYS = _REQUEST_COMMON_KEYS | {"command"}
_REQUEST_REJECT_KEYS = _REQUEST_COMMON_KEYS
_LINEAGE_REF_KEYS = frozenset(
    {
        "schema_version",
        "launch_id",
        "lineage_digest",
        "lineage_anchor",
        "anchor_device",
        "anchor_inode",
    }
)
_VALID_ACTIONS = frozenset({"run", "reject"})
_VALID_MODES = frozenset({"capture", "direct"})
_CAPTURE_KEYS = frozenset(
    {
        "capture_id",
        "capture_status",
        "command_outcome_kind",
        "command_outcome_value",
        "finalized_at_revision",
        "producer",
        "reference",
        "reference_status",
        "schema_version",
        "sha256",
        "shell_returncode",
        "snapshot_status",
        "total_bytes",
        "unavailable_reason",
    }
)
_FAILURE_KEYS = frozenset(
    {
        "detail",
        "producer",
        "schema_version",
        "settlement_returncode",
        "shell_returncode",
        "stage",
        "status",
    }
)
_FAILURE_V3_KEYS = _FAILURE_KEYS | {"reason"}


class CaptureContractError(ValueError):
    """Raised when a V2 capture transport value is invalid or noncanonical."""


@dataclass(frozen=True, slots=True)
class CaptureV2Fields:
    capture_id: str
    finalized_at_revision: int
    total_bytes: int
    sha256: str
    command_outcome_kind: str
    command_outcome_value: int
    shell_returncode: int
    reference_status: str
    reference: str | None
    unavailable_reason: str | None

    def __post_init__(self) -> None:
        _validate_capture_fields(self)

    @property
    def schema_version(self) -> int:
        return CAPTURE_V2_SCHEMA_VERSION

    @property
    def producer(self) -> str:
        return CAPTURE_V2_PRODUCER

    @property
    def capture_status(self) -> str:
        return "complete"

    @property
    def snapshot_status(self) -> str:
        return "verified"


class CaptureV2Renderable(Protocol):
    def capture_v2_fields(self) -> CaptureV2Fields: ...


class _OutcomeKind(Protocol):
    @property
    def value(self) -> str: ...


class _CommandOutcome(Protocol):
    @property
    def kind(self) -> _OutcomeKind: ...

    @property
    def value(self) -> int: ...

    @property
    def shell_returncode(self) -> int: ...


class _CaptureManifest(Protocol):
    @property
    def capture_id(self) -> str: ...

    @property
    def finalized_at_revision(self) -> int: ...

    @property
    def total_bytes(self) -> int: ...

    @property
    def sha256(self) -> str: ...

    @property
    def command_outcome(self) -> _CommandOutcome: ...


class _CaptureSnapshot(Protocol):
    @property
    def manifest(self) -> _CaptureManifest: ...


def capture_v2_fields(
    snapshot: _CaptureSnapshot,
    *,
    reference_status: str,
    reference: str | None,
    unavailable_reason: str | None,
) -> CaptureV2Fields:
    manifest = snapshot.manifest
    return CaptureV2Fields(
        capture_id=manifest.capture_id,
        finalized_at_revision=manifest.finalized_at_revision,
        total_bytes=manifest.total_bytes,
        sha256=manifest.sha256,
        command_outcome_kind=manifest.command_outcome.kind.value,
        command_outcome_value=manifest.command_outcome.value,
        shell_returncode=manifest.command_outcome.shell_returncode,
        reference_status=reference_status,
        reference=reference,
        unavailable_reason=unavailable_reason,
    )


@dataclass(frozen=True, slots=True)
class CaptureFailureV2:
    stage: str
    detail: str
    shell_returncode: int | None
    settlement_returncode: int | None

    def __post_init__(self) -> None:
        _validate_failure(self)


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


def _validate_capture_fields(fields: CaptureV2Fields) -> None:
    if (
        type(fields) is not CaptureV2Fields
        or not isinstance(fields.capture_id, str)
        or _CAPTURE_ID_RE.fullmatch(fields.capture_id) is None
        or not _plain_int(
            fields.finalized_at_revision,
            minimum=1,
            maximum=_MAX_SIGNED_VALUE,
        )
        or not _plain_int(
            fields.total_bytes,
            maximum=_MAX_SIGNED_VALUE,
        )
        or not isinstance(fields.sha256, str)
        or _SHA256_RE.fullmatch(fields.sha256) is None
    ):
        raise CaptureContractError("invalid capture snapshot fields")
    if fields.command_outcome_kind == "exited":
        maximum = 255
        minimum = 0
        shell_returncode = fields.command_outcome_value
    elif fields.command_outcome_kind == "signaled":
        maximum = 127
        minimum = 1
        shell_returncode = 128 + fields.command_outcome_value
    else:
        raise CaptureContractError("invalid capture command outcome")
    if (
        not _plain_int(
            fields.command_outcome_value,
            minimum=minimum,
            maximum=maximum,
        )
        or not _plain_int(fields.shell_returncode, maximum=255)
        or fields.shell_returncode != shell_returncode
    ):
        raise CaptureContractError("invalid capture command outcome")
    if fields.reference_status == "published":
        matched = (
            _REFERENCE_RE.fullmatch(fields.reference)
            if isinstance(fields.reference, str)
            else None
        )
        if (
            matched is None
            or matched.group(1) != fields.capture_id
            or fields.unavailable_reason is not None
        ):
            raise CaptureContractError("invalid published capture reference")
    elif fields.reference_status == "unavailable":
        if (
            fields.reference is not None
            or not isinstance(fields.unavailable_reason, str)
            or _REASON_RE.fullmatch(fields.unavailable_reason) is None
        ):
            raise CaptureContractError("invalid unavailable capture reference")
    else:
        raise CaptureContractError("invalid capture reference status")


def _capture_primitive(fields: CaptureV2Fields) -> dict[str, object]:
    _validate_capture_fields(fields)
    return {
        "capture_id": fields.capture_id,
        "capture_status": fields.capture_status,
        "command_outcome_kind": fields.command_outcome_kind,
        "command_outcome_value": fields.command_outcome_value,
        "finalized_at_revision": fields.finalized_at_revision,
        "producer": fields.producer,
        "reference": fields.reference,
        "reference_status": fields.reference_status,
        "schema_version": fields.schema_version,
        "sha256": fields.sha256,
        "shell_returncode": fields.shell_returncode,
        "snapshot_status": fields.snapshot_status,
        "total_bytes": fields.total_bytes,
        "unavailable_reason": fields.unavailable_reason,
    }


def _canonical_json(value: dict[str, object]) -> bytes:
    try:
        return json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise CaptureContractError("capture transport is not encodable") from exc


def _fields_from_renderable(value: CaptureV2Renderable) -> CaptureV2Fields:
    try:
        fields = value.capture_v2_fields()
    except (AttributeError, TypeError) as exc:
        raise CaptureContractError("capture value is not renderable") from exc
    if type(fields) is not CaptureV2Fields:
        raise CaptureContractError("capture renderer returned invalid fields")
    return fields


def _render_capture_fields(fields: CaptureV2Fields) -> bytes:
    encoded = _CAPTURE_PREFIX + _canonical_json(_capture_primitive(fields)) + _FRAME_SUFFIX
    if len(encoded) > MAX_CAPTURE_V2_MARKER_BYTES:
        raise CaptureContractError("capture marker exceeds bound")
    return encoded


def render_capture_v2(value: CaptureV2Renderable) -> bytes:
    return _render_capture_fields(_fields_from_renderable(value))


def capture_v2_encoded_length(value: CaptureV2Renderable) -> int:
    return len(render_capture_v2(value))


def capture_v2_worst_case_bytes() -> int:
    return MAX_CAPTURE_V2_MARKER_BYTES


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise CaptureContractError("duplicate capture transport field")
        result[key] = value
    return result


def _reject_constant(value: str) -> Any:
    raise CaptureContractError(f"invalid capture transport constant: {value}")


def _decode_frame(value: object, *, prefix: bytes, maximum: int) -> dict[str, object]:
    if (
        type(value) is not bytes
        or len(value) > maximum
        or not value.startswith(prefix)
        or not value.endswith(_FRAME_SUFFIX)
    ):
        raise CaptureContractError("capture transport framing exceeds bound")
    raw = value[len(prefix) : -len(_FRAME_SUFFIX)]
    try:
        decoded = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except CaptureContractError:
        raise
    except (json.JSONDecodeError, UnicodeError, ValueError) as exc:
        raise CaptureContractError("invalid capture transport encoding") from exc
    if not isinstance(decoded, dict):
        raise CaptureContractError("capture transport must be an object")
    return decoded


def _string_field(value: object) -> str:
    if not isinstance(value, str):
        raise CaptureContractError("capture transport string field is invalid")
    return value


def _integer_field(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise CaptureContractError("capture transport integer field is invalid")
    return value


def _optional_string_field(value: object) -> str | None:
    if value is None:
        return None
    return _string_field(value)


def _optional_integer_field(value: object) -> int | None:
    if value is None:
        return None
    return _integer_field(value)


def parse_capture_v2(value: bytes) -> CaptureV2Fields:
    decoded = _decode_frame(
        value,
        prefix=_CAPTURE_PREFIX,
        maximum=MAX_CAPTURE_V2_MARKER_BYTES,
    )
    if set(decoded) != _CAPTURE_KEYS:
        raise CaptureContractError("capture transport fields do not match schema")
    if (
        decoded["schema_version"] != CAPTURE_V2_SCHEMA_VERSION
        or decoded["producer"] != CAPTURE_V2_PRODUCER
        or decoded["capture_status"] != "complete"
        or decoded["snapshot_status"] != "verified"
    ):
        raise CaptureContractError("capture transport status does not match V2")
    fields = CaptureV2Fields(
        capture_id=_string_field(decoded["capture_id"]),
        finalized_at_revision=_integer_field(decoded["finalized_at_revision"]),
        total_bytes=_integer_field(decoded["total_bytes"]),
        sha256=_string_field(decoded["sha256"]),
        command_outcome_kind=_string_field(decoded["command_outcome_kind"]),
        command_outcome_value=_integer_field(decoded["command_outcome_value"]),
        shell_returncode=_integer_field(decoded["shell_returncode"]),
        reference_status=_string_field(decoded["reference_status"]),
        reference=_optional_string_field(decoded["reference"]),
        unavailable_reason=_optional_string_field(decoded["unavailable_reason"]),
    )
    _validate_capture_fields(fields)
    if _render_capture_fields(fields) != value:
        raise CaptureContractError("capture transport is not canonical")
    return fields


def _validate_failure(value: CaptureFailureV2) -> None:
    if (
        type(value) is not CaptureFailureV2
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
        raise CaptureContractError("invalid capture failure transport")


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


def _failure_primitive(value: CaptureFailureV2) -> dict[str, object]:
    _validate_failure(value)
    return {
        "detail": value.detail,
        "producer": CAPTURE_V2_PRODUCER,
        "schema_version": CAPTURE_V2_SCHEMA_VERSION,
        "settlement_returncode": value.settlement_returncode,
        "shell_returncode": value.shell_returncode,
        "stage": value.stage,
        "status": "capture_failed",
    }


def render_capture_failure_v2(value: CaptureFailureV2) -> bytes:
    encoded = _FAILURE_PREFIX + _canonical_json(_failure_primitive(value)) + _FRAME_SUFFIX
    if len(encoded) > MAX_CAPTURE_FAILURE_V2_BYTES:
        raise CaptureContractError("capture failure marker exceeds bound")
    return encoded


def parse_capture_failure_v2(value: bytes) -> CaptureFailureV2:
    decoded = _decode_frame(
        value,
        prefix=_FAILURE_PREFIX,
        maximum=MAX_CAPTURE_FAILURE_V2_BYTES,
    )
    if set(decoded) != _FAILURE_KEYS:
        raise CaptureContractError("capture failure fields do not match schema")
    if (
        decoded["schema_version"] != CAPTURE_V2_SCHEMA_VERSION
        or decoded["producer"] != CAPTURE_V2_PRODUCER
        or decoded["status"] != "capture_failed"
    ):
        raise CaptureContractError("capture failure status does not match V2")
    failure = CaptureFailureV2(
        stage=_string_field(decoded["stage"]),
        detail=_string_field(decoded["detail"]),
        shell_returncode=_optional_integer_field(decoded["shell_returncode"]),
        settlement_returncode=_optional_integer_field(decoded["settlement_returncode"]),
    )
    _validate_failure(failure)
    if render_capture_failure_v2(failure) != value:
        raise CaptureContractError("capture failure transport is not canonical")
    return failure


class CaptureProtocolError(ValueError):
    """The shell-capture request is malformed, non-canonical, or unsupported."""


@dataclass(frozen=True, slots=True)
class CaptureLineageRef:
    """Isolated wire projection of a managed lineage reference."""

    schema_version: int
    launch_id: str
    lineage_digest: str
    lineage_anchor: str
    anchor_device: int
    anchor_inode: int


@dataclass(frozen=True, slots=True)
class CaptureRequest:
    """One closed action request transported to the isolated runner."""

    protocol_version: int
    action: str
    mode: str
    attempt_id: str | None
    lineage_ref: CaptureLineageRef | None
    cwd: str
    capture_id: str
    command: str | None = None


def canonical_json_bytes(value: object) -> bytes:
    """Return the repository's narrow canonical JSON representation."""

    try:
        return json.dumps(
            value,
            ensure_ascii=True,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")
    except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
        raise CaptureProtocolError("request is not canonically serializable") from exc


def encode_capture_request(request: CaptureRequest) -> str:
    """Validate and encode a producer request as standard padded Base64."""

    value = _producer_request_object(request)
    decoded = canonical_json_bytes(value)
    if len(decoded) > _MAX_DECODED_REQUEST_BYTES:
        raise CaptureProtocolError("decoded request exceeds limit")
    encoded = base64.b64encode(decoded).decode("ascii")
    if len(encoded) > _MAX_ENCODED_REQUEST_BYTES:
        raise CaptureProtocolError("encoded request exceeds limit")
    return encoded


def decode_capture_request(encoded: str) -> CaptureRequest:
    """Independently validate and decode one canonical runner request."""

    if (
        not isinstance(encoded, str)
        or not encoded
        or len(encoded) > _MAX_ENCODED_REQUEST_BYTES
        or len(encoded) % 4
    ):
        raise CaptureProtocolError("invalid encoded request")
    try:
        encoded_bytes = encoded.encode("ascii")
        decoded = base64.b64decode(encoded_bytes, validate=True)
    except (UnicodeEncodeError, binascii.Error, ValueError) as exc:
        raise CaptureProtocolError("invalid encoded request") from exc
    if base64.b64encode(decoded) != encoded_bytes:
        raise CaptureProtocolError("non-canonical encoded request")
    if not decoded or len(decoded) > _MAX_DECODED_REQUEST_BYTES:
        raise CaptureProtocolError("decoded request exceeds limit")
    value = _load_canonical_json(decoded, "request")
    return _consumer_request_from_object(value)


def decode_lineage_ref_json(serialized: str) -> CaptureLineageRef:
    """Decode the canonical protected-environment lineage reference."""

    if not isinstance(serialized, str) or not serialized:
        raise CaptureProtocolError("invalid lineage reference")
    try:
        raw = serialized.encode("ascii")
    except UnicodeEncodeError as exc:
        raise CaptureProtocolError("invalid lineage reference") from exc
    if len(raw) > _MAX_LINEAGE_REF_JSON_BYTES:
        raise CaptureProtocolError("lineage reference exceeds limit")
    value = _load_canonical_json(raw, "lineage reference")
    return _consumer_lineage_ref(value)


def _producer_request_object(request: CaptureRequest) -> dict[str, object]:
    if not isinstance(request, CaptureRequest):
        raise CaptureProtocolError("invalid request type")
    _producer_validate_int(
        request.protocol_version,
        CAPTURE_REQUEST_PROTOCOL_VERSION,
        "protocol_version",
    )
    if request.action not in _VALID_ACTIONS:
        raise CaptureProtocolError("invalid action")
    if request.mode not in _VALID_MODES:
        raise CaptureProtocolError("invalid mode")
    lineage_value = (
        None if request.lineage_ref is None else _producer_lineage_ref_object(request.lineage_ref)
    )
    _producer_validate_identity_relation(
        mode=request.mode,
        attempt_id=request.attempt_id,
        lineage_ref=request.lineage_ref,
    )
    _validate_path(request.cwd, "cwd")
    _validate_capture_id(request.capture_id)
    value: dict[str, object] = {
        "protocol_version": request.protocol_version,
        "action": request.action,
        "mode": request.mode,
        "attempt_id": request.attempt_id,
        "lineage_ref": lineage_value,
        "cwd": request.cwd,
        "capture_id": request.capture_id,
    }
    if request.action == "run":
        _validate_command(request.command)
        value["command"] = request.command
    elif request.command is not None:
        raise CaptureProtocolError("reject request cannot contain command")
    return value


def _producer_lineage_ref_object(reference: CaptureLineageRef) -> dict[str, object]:
    if not isinstance(reference, CaptureLineageRef):
        raise CaptureProtocolError("invalid lineage reference type")
    _producer_validate_int(
        reference.schema_version,
        MANAGED_LINEAGE_REF_SCHEMA_VERSION,
        "schema_version",
    )
    _validate_identity(reference.launch_id, "launch_id")
    _validate_digest(reference.lineage_digest)
    _validate_path(reference.lineage_anchor, "lineage_anchor")
    _validate_nonnegative_int(reference.anchor_device, "anchor_device")
    _validate_nonnegative_int(reference.anchor_inode, "anchor_inode")
    return {
        "schema_version": reference.schema_version,
        "launch_id": reference.launch_id,
        "lineage_digest": reference.lineage_digest,
        "lineage_anchor": reference.lineage_anchor,
        "anchor_device": reference.anchor_device,
        "anchor_inode": reference.anchor_inode,
    }


def _consumer_request_from_object(value: object) -> CaptureRequest:
    if not isinstance(value, dict):
        raise CaptureProtocolError("request must be an object")
    action = value.get("action")
    if not isinstance(action, str) or action not in _VALID_ACTIONS:
        raise CaptureProtocolError("invalid action")
    expected_keys = _REQUEST_RUN_KEYS if action == "run" else _REQUEST_REJECT_KEYS
    if set(value) != expected_keys:
        raise CaptureProtocolError("invalid request fields")

    protocol_version = value["protocol_version"]
    _consumer_validate_int(
        protocol_version,
        CAPTURE_REQUEST_PROTOCOL_VERSION,
        "protocol_version",
    )
    mode = value["mode"]
    if not isinstance(mode, str) or mode not in _VALID_MODES:
        raise CaptureProtocolError("invalid mode")

    attempt_id = value["attempt_id"]
    if attempt_id is not None:
        _validate_identity(attempt_id, "attempt_id")
    lineage_value = value["lineage_ref"]
    lineage_ref = None if lineage_value is None else _consumer_lineage_ref(lineage_value)
    _consumer_validate_identity_relation(
        mode=mode,
        attempt_id=attempt_id,
        lineage_ref=lineage_ref,
    )

    cwd = value["cwd"]
    capture_id = value["capture_id"]
    _validate_path(cwd, "cwd")
    _validate_capture_id(capture_id)
    command = value["command"] if action == "run" else None
    if action == "run":
        _validate_command(command)
    return CaptureRequest(
        protocol_version=protocol_version,
        action=action,
        mode=mode,
        attempt_id=attempt_id,
        lineage_ref=lineage_ref,
        cwd=cwd,
        capture_id=capture_id,
        command=command,
    )


def _consumer_lineage_ref(value: object) -> CaptureLineageRef:
    if not isinstance(value, dict) or set(value) != _LINEAGE_REF_KEYS:
        raise CaptureProtocolError("invalid lineage reference fields")
    schema_version = value["schema_version"]
    _consumer_validate_int(
        schema_version,
        MANAGED_LINEAGE_REF_SCHEMA_VERSION,
        "schema_version",
    )
    launch_id = value["launch_id"]
    lineage_digest = value["lineage_digest"]
    lineage_anchor = value["lineage_anchor"]
    anchor_device = value["anchor_device"]
    anchor_inode = value["anchor_inode"]
    _validate_identity(launch_id, "launch_id")
    _validate_digest(lineage_digest)
    _validate_path(lineage_anchor, "lineage_anchor")
    _validate_nonnegative_int(anchor_device, "anchor_device")
    _validate_nonnegative_int(anchor_inode, "anchor_inode")
    return CaptureLineageRef(
        schema_version=schema_version,
        launch_id=launch_id,
        lineage_digest=lineage_digest,
        lineage_anchor=lineage_anchor,
        anchor_device=anchor_device,
        anchor_inode=anchor_inode,
    )


def _load_canonical_json(raw: bytes, label: str) -> object:
    try:
        text = raw.decode("utf-8")
        value = json.loads(text, object_pairs_hook=_object_without_duplicates)
    except (UnicodeDecodeError, ValueError, RecursionError) as exc:
        raise CaptureProtocolError(f"invalid {label}") from exc
    if canonical_json_bytes(value) != raw:
        raise CaptureProtocolError(f"non-canonical {label}")
    return value


def _object_without_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise CaptureProtocolError("duplicate JSON key")
        value[key] = item
    return value


def _producer_validate_identity_relation(
    *,
    mode: str,
    attempt_id: object,
    lineage_ref: object,
) -> None:
    if (attempt_id is None) != (lineage_ref is None):
        raise CaptureProtocolError("attempt and lineage reference must be both null or non-null")
    if attempt_id is not None:
        _validate_identity(attempt_id, "attempt_id")
    if mode == "direct" and lineage_ref is None:
        raise CaptureProtocolError("direct mode requires managed lineage")


def _consumer_validate_identity_relation(
    *,
    mode: str,
    attempt_id: object,
    lineage_ref: object,
) -> None:
    if (attempt_id is None) != (lineage_ref is None):
        raise CaptureProtocolError("invalid attempt/lineage relationship")
    if mode == "direct" and lineage_ref is None:
        raise CaptureProtocolError("invalid direct request authority")


def _producer_validate_int(value: object, expected: int, field_name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value != expected:
        raise CaptureProtocolError(f"invalid {field_name}")


def _consumer_validate_int(value: object, expected: int, field_name: str) -> None:
    if type(value) is not int or value != expected:
        raise CaptureProtocolError(f"unsupported {field_name}")


def _validate_nonnegative_int(value: object, field_name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise CaptureProtocolError(f"invalid {field_name}")


def _validate_identity(value: object, field_name: str) -> None:
    if not isinstance(value, str) or _IDENTITY_RE.fullmatch(value) is None:
        raise CaptureProtocolError(f"invalid {field_name}")


def _validate_digest(value: object) -> None:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise CaptureProtocolError("invalid lineage_digest")


def _validate_capture_id(value: object) -> None:
    if not isinstance(value, str) or _CAPTURE_ID_RE.fullmatch(value) is None:
        raise CaptureProtocolError("invalid capture_id")


def _validate_path(value: object, field_name: str) -> None:
    if not isinstance(value, str) or not value or "\x00" in value or not os.path.isabs(value):
        raise CaptureProtocolError(f"invalid {field_name}")
    try:
        encoded = value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise CaptureProtocolError(f"invalid {field_name}") from exc
    if len(encoded) > _MAX_PATH_BYTES:
        raise CaptureProtocolError(f"{field_name} exceeds limit")


def _validate_command(value: object) -> None:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise CaptureProtocolError("invalid command")
    try:
        encoded = value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise CaptureProtocolError("invalid command") from exc
    if len(encoded) > _MAX_COMMAND_BYTES:
        raise CaptureProtocolError("command exceeds limit")


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
