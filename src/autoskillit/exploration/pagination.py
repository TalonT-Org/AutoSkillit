"""Digest-bound pagination for stable evidence result sets."""

from __future__ import annotations

import unicodedata
from collections.abc import Iterable, Mapping, Sequence

from autoskillit.core import (
    CompletenessReport,
    ContinuationCursor,
    EvidencePage,
    EvidenceRecord,
    ExplorationQuerySpec,
    RepositorySnapshot,
)

from ._deterministic import CursorValidationError, page_slice, stable_digest
from ._digest import qualified_digest
from .graph import build_canonical_evidence_graph

PAGINATION_DIGEST_DOMAIN = b"autoskillit.exploration-page.v1\0"


def normalize_query(query: str) -> str:
    """Return the stable Unicode/whitespace form used by pagination authority."""
    if not isinstance(query, str) or not query.strip():
        raise ValueError("query must be a non-empty string")
    return " ".join(unicodedata.normalize("NFKC", query).split())


def pagination_identity(
    *,
    query: str,
    ordered_item_identities: Sequence[str],
    snapshot_identity: str,
    profile_identity: str,
    schema_identity: str,
    collector_manifest_digest: str,
) -> str:
    """Bind a page sequence to stable order and every invalidation authority."""
    if len(set(ordered_item_identities)) != len(ordered_item_identities):
        raise ValueError("pagination item identities must be unique")
    payload: Mapping[str, object] = {
        "normalized_query": normalize_query(query),
        "stable_order": list(ordered_item_identities),
        "total_count": len(ordered_item_identities),
        "snapshot_identity": snapshot_identity,
        "profile_identity": profile_identity,
        "schema_identity": schema_identity,
        "collector_manifest_digest": collector_manifest_digest,
    }
    for name, value in payload.items():
        if name not in {"stable_order", "total_count"} and not value:
            raise ValueError(f"pagination authority {name} must be non-empty")
    return qualified_digest(PAGINATION_DIGEST_DOMAIN, payload)


def evidence_result_digest(
    evidence: Iterable[EvidenceRecord],
    completeness: CompletenessReport,
    *,
    query: ExplorationQuerySpec | None = None,
    snapshot: RepositorySnapshot | None = None,
) -> str:
    """Bind every page cursor to evidence, snapshot/provenance, and completeness state."""

    records = tuple(sorted(evidence, key=lambda record: record.evidence_id))
    if len({record.evidence_id for record in records}) != len(records):
        raise ValueError("evidence record identities must be unique in a result set")
    return stable_digest(
        {
            "evidence": [
                [
                    record.evidence_id,
                    record.provenance,
                    record.snapshot_digest,
                    None if record.subject is None else record.subject.digest,
                    list(record.facts),
                    list(record.inferences),
                    list(record.unknowns),
                    list(record.conflicts),
                    record.locator,
                    record.method,
                    record.extractor_version,
                    list(record.searched_scope),
                    record.location,
                    list(record.query_uncertainty),
                ]
                for record in records
            ],
            "complete": completeness.complete,
            "missing": list(completeness.missing_collectors),
            "failed": list(completeness.failed_collectors),
            "authority": _result_authority(query=query, snapshot=snapshot),
        }
    )


def _result_authority(
    *, query: ExplorationQuerySpec | None, snapshot: RepositorySnapshot | None
) -> dict[str, object]:
    """Include all pagination invalidators, including zero-evidence result sets."""

    if query is None:
        query_authority: dict[str, object] = {"normalized_query": None, "query_digest": None}
    else:
        query_authority = {
            "normalized_query": normalize_query(query.query),
            "query_digest": query.digest,
            "required_profiles": [profile.value for profile in query.required_profiles],
            "scope": list(query.scope),
        }
    if snapshot is None:
        snapshot_authority: dict[str, object] = {
            "snapshot_digest": None,
            "profile_activation_digest": None,
            "profile_versions": [],
            "schema_version": None,
            "collector_manifest_digest": None,
            "pagination_identity": None,
        }
    else:
        snapshot_authority = {
            "snapshot_digest": snapshot.digest,
            "profile_activation_digest": snapshot.profile_activation_digest,
            "profile_versions": list(snapshot.profile_versions),
            "schema_version": snapshot.schema_version,
            "collector_manifest_digest": snapshot.collector_manifest_digest,
            "pagination_identity": snapshot.pagination_identity,
        }
    return {"query": query_authority, "snapshot": snapshot_authority}


def page_evidence(
    evidence: Iterable[EvidenceRecord],
    completeness: CompletenessReport,
    *,
    page_size: int,
    cursor: ContinuationCursor | None = None,
    query: ExplorationQuerySpec | None = None,
    snapshot: RepositorySnapshot | None = None,
) -> EvidencePage:
    """Emit only canonical ordering and invalidate cursors on any result change."""

    records = tuple(sorted(evidence, key=lambda record: record.evidence_id))
    authority_digest = stable_digest(_result_authority(query=query, snapshot=snapshot))
    digest = evidence_result_digest(records, completeness, query=query, snapshot=snapshot)
    if cursor is None:
        offset = 0
    elif (
        cursor.result_digest != digest
        or cursor.authority_digest != authority_digest
        or cursor.page_size != page_size
    ):
        raise CursorValidationError("continuation cursor is stale")
    else:
        offset = cursor.offset
    page, next_offset = page_slice(records, offset=offset, page_size=page_size)
    graph = build_canonical_evidence_graph(page)
    next_cursor = (
        None
        if next_offset is None
        else ContinuationCursor(
            result_digest=digest,
            offset=next_offset,
            page_size=page_size,
            authority_digest=authority_digest,
        )
    )
    return EvidencePage(
        evidence=page,
        result_digest=digest,
        completeness=completeness,
        continuation=next_cursor,
        graph_nodes=graph.nodes,
        graph_edges=graph.edges,
        graph_conflicts=graph.conflicts,
    )
