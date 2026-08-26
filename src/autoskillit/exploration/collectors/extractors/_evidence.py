"""Manifest digest, evidence-record construction, and metadata lookup.

Decomposed from the original ``collectors/extractors.py`` per #4836. ``COLLECTOR_PROFILES``
is consumed here and built from this shard's downstream imports, so the cycle
``_file_search → _evidence → _registry → _file_search`` is broken by
lazy-importing it at call time (see ``_collector_metadata`` and
``collector_manifest_digest``).
"""

from __future__ import annotations

import hashlib
from typing import Final

from autoskillit.core import (
    CollectorReport,
    CollectorStatus,
    EvidenceRecord,
    MethodProvenance,
    NodeKey,
)

from ..._deterministic import canonical_json
from ._records import CollectorProfile

__all__ = [
    "collector_manifest_digest",
    "_collector_metadata",
    "_evidence",
    "_report",
    "_bounded_diagnostic_text",
    "_invalid_rg_json_diagnostic",
    "_OBSERVATION_UNCERTAINTY",
    "_RG_DECODE_DETAIL_MAX_BYTES",
    "_RG_DECODE_RAW_LINE_MAX_BYTES",
    "_RG_DECODE_DIAGNOSTIC_MAX_BYTES",
]

_OBSERVATION_UNCERTAINTY: Final = (
    "collector observations do not establish semantic relationships",
)
_RG_DECODE_DETAIL_MAX_BYTES: Final = 96
_RG_DECODE_RAW_LINE_MAX_BYTES: Final = 160
_RG_DECODE_DIAGNOSTIC_MAX_BYTES: Final = 320


def collector_manifest_digest(
    profiles: tuple[CollectorProfile, ...] | None = None,
) -> str:
    """Return the versioned identity of exactly the collectors this process registers."""

    from ._registry import COLLECTOR_PROFILES  # lazy — see module docstring

    registry = COLLECTOR_PROFILES if profiles is None else profiles
    records = [
        {
            "id": profile.collector_id,
            "invocation": profile.invocation.adapter_id,
            "method": profile.method,
            "profile": profile.profile.value,
            "required_by_default": profile.required_by_default,
            "version": profile.version,
        }
        for profile in sorted(registry, key=lambda item: item.collector_id)
    ]
    if len({record["id"] for record in records}) != len(records):
        raise ValueError("collector manifest contains duplicate collector identifiers")
    encoded = canonical_json(records)
    return hashlib.sha256(
        b"autoskillit.collector-manifest.v2\0" + encoded.encode("ascii")
    ).hexdigest()


def _collector_metadata(collector_id: str) -> tuple[str, str]:
    from ._registry import COLLECTOR_PROFILES  # lazy — see module docstring

    profile = next(
        (profile for profile in COLLECTOR_PROFILES if profile.collector_id == collector_id),
        None,
    )
    if profile is None:
        raise ValueError(f"unknown collector identifier: {collector_id}")
    return profile.method, profile.version


def _report(
    collector_id: str,
    snapshot_digest: str,
    status: CollectorStatus,
    diagnostics: tuple[str, ...] = (),
    evidence: tuple[EvidenceRecord, ...] = (),
) -> CollectorReport:
    return CollectorReport(
        collector_id, status, snapshot_digest, evidence, "; ".join(diagnostics) or None
    )


def _bounded_diagnostic_text(value: str, max_bytes: int) -> str:
    """Return a UTF-8-safe diagnostic fragment within an exact byte ceiling."""

    if max_bytes <= 0:
        return ""
    encoded = value.encode("utf-8", "backslashreplace")
    if len(encoded) <= max_bytes:
        return encoded.decode("utf-8")
    marker = b"..."
    if max_bytes <= len(marker):
        return marker[:max_bytes].decode("ascii")
    return encoded[: max_bytes - len(marker)].decode("utf-8", "ignore") + marker.decode()


def _invalid_rg_json_diagnostic(raw_line: bytes, exc: Exception) -> str:
    detail = _bounded_diagnostic_text(repr(str(exc)), _RG_DECODE_DETAIL_MAX_BYTES)
    raw_repr = _bounded_diagnostic_text(repr(raw_line), _RG_DECODE_RAW_LINE_MAX_BYTES)
    diagnostic = (
        f"invalid rg json output ({type(exc).__name__}: detail={detail}; raw_line={raw_repr})"
    )
    return _bounded_diagnostic_text(diagnostic, _RG_DECODE_DIAGNOSTIC_MAX_BYTES)


def _evidence(
    collector_id: str, snapshot_digest: str, path: str, line: int, excerpt: str
) -> EvidenceRecord:
    claim = excerpt
    location = f"{path}:{line}"
    method, version = _collector_metadata(collector_id)
    digest = hashlib.sha256(claim.encode("utf-8", "surrogateescape")).hexdigest()
    identifier = hashlib.sha256(
        f"{collector_id}\0{method}\0{version}\0{path}\0{line}\0{claim}\0{digest}".encode(
            "utf-8", "surrogateescape"
        )
    ).hexdigest()
    return EvidenceRecord(
        identifier,
        MethodProvenance.COLLECTOR,
        snapshot_digest,
        subject=NodeKey("repository-path", path),
        facts=(claim,),
        locator=location,
        method=method,
        extractor_version=version,
        searched_scope=(path,),
        location=location,
        query_uncertainty=_OBSERVATION_UNCERTAINTY,
    )
