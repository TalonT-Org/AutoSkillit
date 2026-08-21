"""Canonical capture-request and lineage-ref codecs (stdlib-only).

Owns the producer/consumer codecs that move a shell-capture request and
its lineage reference between the host process and the isolated runner.
"""

from __future__ import annotations

import base64
import binascii
import json
import os
import re
from dataclasses import dataclass
from typing import Final

from . import _syntax
from ._module_identity import register_module_aliases

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

_CAPTURE_ID_RE = _syntax.CAPTURE_ID_RE
_IDENTITY_RE = re.compile(r"^[0-9a-f]{32}$")
_MAX_PATH_BYTES = 4096
_MAX_LINEAGE_REF_JSON_BYTES = 32 * 1024
_MAX_DECODED_REQUEST_BYTES = 512 * 1024
_MAX_ENCODED_REQUEST_BYTES = ((_MAX_DECODED_REQUEST_BYTES + 2) // 3) * 4

register_module_aliases(__name__)

_MAX_COMMAND_BYTES = 64 * 1024
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
    if not isinstance(value, str) or _syntax.SHA256_RE.fullmatch(value) is None:
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
