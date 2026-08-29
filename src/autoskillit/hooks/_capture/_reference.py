"""Capture reference parsing, hashing, issuance, and publication primitives."""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ._module_identity import register_module_aliases
from ._syntax import REFERENCE_RE

if TYPE_CHECKING:
    from ._snapshot import (
        IssuedCaptureReference,
        VerifiedCaptureSnapshot,
    )

register_module_aliases(__name__)

__all__ = [
    "CaptureReferenceHint",
    "MAX_REFERENCE_TOKEN_BYTES",
    "_bind_finalized_snapshot",
    "_issue_capture_reference",
    "_make_published_reference",
    "_make_unavailable_reference",
    "_reference_context",
    "_reference_hash",
    "parse_capture_reference",
]

MAX_REFERENCE_TOKEN_BYTES = 192


@dataclass(frozen=True, slots=True)
class CaptureReferenceHint:
    capture_id: str
    incarnation: str
    token: str


def parse_capture_reference(token: str) -> CaptureReferenceHint:
    from ._snapshot import CaptureAuthorityError

    if not isinstance(token, str):
        raise CaptureAuthorityError("invalid capture reference")
    try:
        encoded = token.encode("ascii", errors="strict")
    except UnicodeEncodeError as exc:
        raise CaptureAuthorityError("invalid capture reference") from exc
    if len(encoded) > MAX_REFERENCE_TOKEN_BYTES:
        raise CaptureAuthorityError("invalid capture reference")
    matched = REFERENCE_RE.fullmatch(token)
    if matched is None:
        raise CaptureAuthorityError("invalid capture reference")
    return CaptureReferenceHint(
        capture_id=matched.group(1),
        incarnation=matched.group(2),
        token=token,
    )


def _reference_context(
    manifest,  # CaptureFinalManifest
    *,
    reference_expiry: float | None = None,
) -> bytes:
    from ._descriptor import canonical_json

    # Mirror _snapshot._manifest_primitive exactly. All 22 keys must
    # appear in the literal; the conditional overwrite below only
    # adjusts `reference_expiry`. Dropping the key when called without
    # the kwarg silently changes the canonical JSON (interface-mapper
    # finding) and breaks every reader-side _reference_matches call.
    primitive = {
        "schema_version": manifest.schema_version,
        "producer": manifest.producer,
        "capture_id": manifest.capture_id,
        "incarnation": manifest.incarnation,
        "finalized_at_revision": manifest.finalized_at_revision,
        "project_identity": list(manifest.project_identity),
        "root_identity": list(manifest.root_identity),
        "carrier_name": manifest.carrier_name,
        "carrier_identity": list(manifest.carrier_identity),
        "stream_domain": manifest.stream_domain,
        "total_bytes": manifest.total_bytes,
        "sha256": manifest.sha256,
        "inline_length": manifest.inline_length,
        "head_length": manifest.head_length,
        "tail_length": manifest.tail_length,
        "capture_status": manifest.capture_status.value,
        "command_outcome_kind": manifest.command_outcome.kind.value,
        "command_outcome_value": manifest.command_outcome.value,
        "finalized_at": manifest.finalized_at,
        "reference_hash": None,
        "reference_expiry": manifest.reference_expiry,
        "retention_deadline": manifest.retention_deadline,
    }
    if reference_expiry is not None:
        primitive["reference_expiry"] = reference_expiry
    return b"autoskillit:capture-reference:v2\0" + canonical_json(primitive)


def _reference_hash(
    token: str,
    manifest,  # CaptureFinalManifest
    *,
    reference_expiry: float | None = None,
) -> str:
    parse_capture_reference(token)
    digest = hashlib.sha256()
    digest.update(_reference_context(manifest, reference_expiry=reference_expiry))
    digest.update(b"\0")
    digest.update(token.encode("ascii"))
    return digest.hexdigest()


def _issue_capture_reference(
    snapshot,  # VerifiedCaptureSnapshot
    *,
    expiry: float,
) -> tuple[str, str]:
    manifest = snapshot.manifest
    token = f"ascr2:{manifest.capture_id}:{manifest.incarnation}:{secrets.token_hex(32)}"
    return token, _reference_hash(token, manifest, reference_expiry=expiry)


def _bind_finalized_snapshot(
    verified,  # VerifiedCaptureSnapshot
    *,
    reference_token: str | None,
    reference_hash: str | None,
    reference_expiry: float | None,
):
    from ._snapshot import (
        _AUTHORITY_FACTORY_TOKEN as _SNAPSHOT_FACTORY_TOKEN,
    )
    from ._snapshot import (
        CaptureAuthorityError,
        FinalizedCapture,
        IssuedCaptureReference,
        _make_manifest,
        _make_snapshot,
    )

    if type(verified) is not VerifiedCaptureSnapshot:
        raise CaptureAuthorityError("finalization requires a verified snapshot")
    if (reference_token is None) != (reference_hash is None):
        raise CaptureAuthorityError("incomplete issued reference")
    base = verified.manifest
    if reference_token is not None:
        if reference_hash is None:
            raise CaptureAuthorityError("incomplete issued reference")
        if not hmac.compare_digest(
            _reference_hash(
                reference_token,
                base,
                reference_expiry=reference_expiry,
            ),
            reference_hash,
        ):
            raise CaptureAuthorityError("issued reference hash does not match")
    manifest = _make_manifest(
        capture_id=base.capture_id,
        incarnation=base.incarnation,
        finalized_at_revision=base.finalized_at_revision,
        project_identity=base.project_identity,
        root_identity=base.root_identity,
        carrier_name=base.carrier_name,
        carrier_identity=base.carrier_identity,
        measurement=verified.measurement,
        command_outcome=base.command_outcome,
        finalized_at=base.finalized_at,
        reference_hash=reference_hash,
        reference_expiry=reference_expiry,
        retention_deadline=base.retention_deadline,
    )
    snapshot = _make_snapshot(manifest, verified.measurement)
    issuance = (
        None
        if reference_token is None
        else IssuedCaptureReference(
            snapshot=snapshot,
            token=reference_token,
            _factory_token=_SNAPSHOT_FACTORY_TOKEN,
        )
    )
    return FinalizedCapture(
        snapshot=snapshot,
        finalized_at_revision=manifest.finalized_at_revision,
        issuance=issuance,
        _factory_token=_SNAPSHOT_FACTORY_TOKEN,
    )


def _make_published_reference(
    issuance,  # IssuedCaptureReference
):
    from ._snapshot import (
        _AUTHORITY_FACTORY_TOKEN as _SNAPSHOT_FACTORY_TOKEN,
    )
    from ._snapshot import (
        CaptureAuthorityError,
        PublishedCaptureReference,
    )

    if type(issuance) is not IssuedCaptureReference:
        raise CaptureAuthorityError("publication requires an issued reference")
    return PublishedCaptureReference(
        snapshot=issuance.snapshot,
        token=issuance.token,
        _factory_token=_SNAPSHOT_FACTORY_TOKEN,
    )


def _make_unavailable_reference(
    snapshot,  # VerifiedCaptureSnapshot
    reason_code: str,
):
    from ._snapshot import (
        _AUTHORITY_FACTORY_TOKEN as _SNAPSHOT_FACTORY_TOKEN,
    )
    from ._snapshot import (
        UnavailableCaptureReference,
    )

    return UnavailableCaptureReference(
        snapshot=snapshot,
        reason_code=reason_code,
        _factory_token=_SNAPSHOT_FACTORY_TOKEN,
    )
