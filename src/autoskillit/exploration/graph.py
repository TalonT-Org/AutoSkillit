"""Deterministic graph construction that keeps identities distinct from evidence."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from autoskillit.core import EvidenceRecord, GraphEdge, GraphNode, NodeKey, RelationshipKind


@dataclass(frozen=True, slots=True)
class CanonicalEvidenceGraph:
    """The lossless graph projection of one bounded evidence set."""

    nodes: tuple[GraphNode, ...]
    edges: tuple[GraphEdge, ...]
    conflicts: tuple[str, ...]


def _merge_terms(values: Iterable[tuple[str, ...]]) -> tuple[str, ...]:
    return tuple(sorted({term for value in values for term in value}))


def merge_graph_nodes(nodes: Iterable[GraphNode]) -> tuple[GraphNode, ...]:
    """Merge only equal durable node identities; retain every claim category."""

    grouped: dict[NodeKey, list[GraphNode]] = {}
    for node in nodes:
        grouped.setdefault(node.key, []).append(node)
    merged: list[GraphNode] = []
    for node_key in sorted(grouped):
        observations = grouped[node_key]
        labels = {observation.label for observation in observations}
        conflicts = _merge_terms(observation.conflicts for observation in observations)
        if len(labels) != 1:
            conflicts = tuple(sorted((*conflicts, f"conflicting labels: {sorted(labels)!r}")))
        merged.append(
            GraphNode(
                key=node_key,
                label=sorted(labels)[0],
                facts=_merge_terms(observation.facts for observation in observations),
                inferences=_merge_terms(observation.inferences for observation in observations),
                unknowns=_merge_terms(observation.unknowns for observation in observations),
                conflicts=conflicts,
                evidence_ids=_merge_terms(
                    observation.evidence_ids for observation in observations
                ),
            )
        )
    return tuple(merged)


def merge_graph_edges(edges: Iterable[GraphEdge]) -> tuple[GraphEdge, ...]:
    """Merge equal relationship identities while never deduplicating evidence records."""

    grouped: dict[str, list[GraphEdge]] = {}
    for edge in edges:
        grouped.setdefault(edge.key, []).append(edge)
    merged: list[GraphEdge] = []
    for edge_key in sorted(grouped):
        observations = grouped[edge_key]
        exemplar = observations[0]
        merged.append(
            GraphEdge(
                source=exemplar.source,
                target=exemplar.target,
                relationship=exemplar.relationship,
                facts=_merge_terms(observation.facts for observation in observations),
                inferences=_merge_terms(observation.inferences for observation in observations),
                unknowns=_merge_terms(observation.unknowns for observation in observations),
                conflicts=_merge_terms(observation.conflicts for observation in observations),
                evidence_ids=_merge_terms(
                    observation.evidence_ids for observation in observations
                ),
            )
        )
    return tuple(merged)


def evidence_by_subject(
    evidence: Iterable[EvidenceRecord],
) -> tuple[tuple[object | None, tuple[EvidenceRecord, ...]], ...]:
    """Group evidence for presentation without using it as graph identity."""

    grouped: dict[object | None, list[EvidenceRecord]] = {}
    for record in evidence:
        grouped.setdefault(record.subject, []).append(record)
    return tuple(
        (subject, tuple(sorted(records, key=lambda record: record.evidence_id)))
        for subject, records in sorted(grouped.items(), key=lambda item: repr(item[0]))
    )


def _origin_key(record: EvidenceRecord) -> NodeKey | None:
    """Recover a contained source path from the evidence location when available."""

    location = record.location or record.locator
    if location is None:
        return None
    path, separator, line = location.rpartition(":")
    if not separator or not path or not line.isdecimal():
        return None
    return NodeKey("repository-path", path)


_RELATIONSHIP_BY_SUBJECT_NAMESPACE: dict[str, RelationshipKind] = {
    "python-alias": RelationshipKind.REFERENCES,
    "python-call": RelationshipKind.CALLS,
    "python-declaration": RelationshipKind.DECLARES,
    "python-dynamic-import": RelationshipKind.IMPORTS,
    "python-import": RelationshipKind.IMPORTS,
    "python-nominal-protocol": RelationshipKind.DECLARES,
    "python-protocol": RelationshipKind.DECLARES,
    "python-reexport": RelationshipKind.REFERENCES,
    "python-registry": RelationshipKind.AFFECTS,
    "python-runtime-patch": RelationshipKind.AFFECTS,
    "python-runtime-wiring": RelationshipKind.AFFECTS,
    "python-symbol": RelationshipKind.DEFINES,
    "python-test-consumer": RelationshipKind.AFFECTS,
    "configuration-declaration": RelationshipKind.AFFECTS,
    "coverage-observation": RelationshipKind.AFFECTS,
    "generated-artifact": RelationshipKind.AFFECTS,
    "test-consumer": RelationshipKind.AFFECTS,
}


def _relationships_for_record(
    record: EvidenceRecord, subject: NodeKey
) -> tuple[RelationshipKind, ...]:
    """Classify observed subjects without deriving links from unobserved semantics."""

    relationship = _RELATIONSHIP_BY_SUBJECT_NAMESPACE.get(
        subject.namespace, RelationshipKind.DECLARES
    )
    if record.conflicts and relationship is not RelationshipKind.CONFLICTS_WITH:
        return relationship, RelationshipKind.CONFLICTS_WITH
    return (relationship,)


def build_canonical_evidence_graph(evidence: Iterable[EvidenceRecord]) -> CanonicalEvidenceGraph:
    """Project evidence into merged graph identities without discarding conflicts or fanout."""

    node_observations: list[GraphNode] = []
    edge_observations: list[GraphEdge] = []
    for record in sorted(evidence, key=lambda item: item.evidence_id):
        origin = _origin_key(record)
        subject = record.subject or origin or NodeKey("evidence", record.evidence_id)
        node_observations.append(
            GraphNode(
                key=subject,
                label=subject.value,
                facts=record.facts,
                inferences=record.inferences,
                unknowns=record.unknowns,
                conflicts=record.conflicts,
                evidence_ids=(record.evidence_id,),
            )
        )
        if origin is not None and origin != subject:
            node_observations.append(
                GraphNode(
                    key=origin,
                    label=origin.value,
                    evidence_ids=(record.evidence_id,),
                )
            )
            edge_observations.extend(
                GraphEdge(
                    source=origin,
                    target=subject,
                    relationship=relationship,
                    facts=record.facts,
                    inferences=record.inferences,
                    unknowns=record.unknowns,
                    conflicts=record.conflicts,
                    evidence_ids=(record.evidence_id,),
                )
                for relationship in _relationships_for_record(record, subject)
            )
    nodes = merge_graph_nodes(node_observations)
    edges = merge_graph_edges(edge_observations)
    conflicts = tuple(
        sorted(
            {conflict for node in nodes for conflict in node.conflicts}
            | {conflict for edge in edges for conflict in edge.conflicts}
        )
    )
    return CanonicalEvidenceGraph(nodes=nodes, edges=edges, conflicts=conflicts)
