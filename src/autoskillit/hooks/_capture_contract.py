"""Canonical stdlib-only transport contract for shell capture."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

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
    from autoskillit.hooks import _capture_failure_policy
    from autoskillit.hooks._capture._syntax import (
        CAPTURE_ID_RE,
        REFERENCE_RE,
        SHA256_RE,
    )
elif __package__:
    from . import _capture_failure_policy
    from ._capture._syntax import CAPTURE_ID_RE, REFERENCE_RE, SHA256_RE
else:
    import _capture_failure_policy
    from _capture._syntax import CAPTURE_ID_RE, REFERENCE_RE, SHA256_RE

__all__ = [
    "CAPTURE_V2_PRODUCER",
    "CAPTURE_V2_SCHEMA_VERSION",
    "MAX_CAPTURE_FAILURE_V2_BYTES",
    "MAX_CAPTURE_V2_MARKER_BYTES",
    "CaptureContractError",
    "CaptureFailureV2",
    "CaptureV2Fields",
    "CaptureV2Renderable",
    "capture_v2_encoded_length",
    "capture_v2_fields",
    "capture_v2_worst_case_bytes",
    "parse_capture_failure_v2",
    "parse_capture_v2",
    "render_capture_failure_v2",
    "render_capture_v2",
]

CAPTURE_V2_SCHEMA_VERSION = 2
CAPTURE_V2_PRODUCER = "codex_shell_capture"
MAX_CAPTURE_V2_MARKER_BYTES = 2048
MAX_CAPTURE_FAILURE_V2_BYTES = 1024

_CAPTURE_PREFIX = b"[AutoSkillit shell capture v2:"
_FAILURE_PREFIX = b"[AutoSkillit shell capture failure v2:"
_FRAME_SUFFIX = b"]"
_MAX_COMMAND_BYTES = 64 * 1024
_MAX_SIGNED_VALUE = (1 << 63) - 1
_CAPTURE_ID_RE = CAPTURE_ID_RE
_REFERENCE_RE = REFERENCE_RE
_SHA256_RE = SHA256_RE
_REASON_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
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
        or not _capture_failure_policy.valid_failure_stage(value.stage)
        or not _capture_failure_policy.valid_failure_detail(value.detail)
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


def _render_failure(value: CaptureFailureV2) -> bytes:
    encoded = _FAILURE_PREFIX + _canonical_json(_failure_primitive(value)) + _FRAME_SUFFIX
    if len(encoded) > MAX_CAPTURE_FAILURE_V2_BYTES:
        raise CaptureContractError("capture failure marker exceeds bound")
    return encoded


def render_capture_failure_v2(value: CaptureFailureV2) -> bytes:
    return _render_failure(value)


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
    if _render_failure(failure) != value:
        raise CaptureContractError("capture failure transport is not canonical")
    return failure
