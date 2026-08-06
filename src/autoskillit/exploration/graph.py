"""Deterministic graph construction that keeps identities distinct from evidence."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from autoskillit.core import EvidenceRecord, GraphEdge, GraphNode, NodeKey, RelationshipKind


class SubjectNamespace(StrEnum):
    """Collector subject namespaces with explicit graph relationship semantics."""

    CONFIGURATION_DECLARATION = "configuration-declaration"
    COVERAGE_OBSERVATION = "coverage-observation"
    GENERATED_ARTIFACT = "generated-artifact"
    PYTHON_ALIAS = "python-alias"
    PYTHON_CALL = "python-call"
    PYTHON_DECLARATION = "python-declaration"
    PYTHON_DYNAMIC_IMPORT = "python-dynamic-import"
    PYTHON_IMPORT = "python-import"
    PYTHON_NOMINAL_PROTOCOL = "python-nominal-protocol"
    PYTHON_PROTOCOL = "python-protocol"
    PYTHON_REEXPORT = "python-reexport"
    PYTHON_REGISTRY = "python-registry"
    PYTHON_RUNTIME_PATCH = "python-runtime-patch"
    PYTHON_RUNTIME_WIRING = "python-runtime-wiring"
    PYTHON_SYMBOL = "python-symbol"
    PYTHON_TEST_CONSUMER = "python-test-consumer"
    TEST_CONSUMER = "test-consumer"


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
) -> tuple[tuple[NodeKey | None, tuple[EvidenceRecord, ...]], ...]:
    """Group evidence for presentation without using it as graph identity."""

    grouped: dict[NodeKey | None, list[EvidenceRecord]] = {}
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


_RELATIONSHIP_BY_SUBJECT_NAMESPACE: Final[dict[SubjectNamespace, RelationshipKind]] = {
    SubjectNamespace.CONFIGURATION_DECLARATION: RelationshipKind.AFFECTS,
    SubjectNamespace.COVERAGE_OBSERVATION: RelationshipKind.AFFECTS,
    SubjectNamespace.GENERATED_ARTIFACT: RelationshipKind.AFFECTS,
    SubjectNamespace.PYTHON_ALIAS: RelationshipKind.REFERENCES,
    SubjectNamespace.PYTHON_CALL: RelationshipKind.CALLS,
    SubjectNamespace.PYTHON_DECLARATION: RelationshipKind.DECLARES,
    SubjectNamespace.PYTHON_DYNAMIC_IMPORT: RelationshipKind.IMPORTS,
    SubjectNamespace.PYTHON_IMPORT: RelationshipKind.IMPORTS,
    SubjectNamespace.PYTHON_NOMINAL_PROTOCOL: RelationshipKind.DECLARES,
    SubjectNamespace.PYTHON_PROTOCOL: RelationshipKind.DECLARES,
    SubjectNamespace.PYTHON_REEXPORT: RelationshipKind.REFERENCES,
    SubjectNamespace.PYTHON_REGISTRY: RelationshipKind.AFFECTS,
    SubjectNamespace.PYTHON_RUNTIME_PATCH: RelationshipKind.AFFECTS,
    SubjectNamespace.PYTHON_RUNTIME_WIRING: RelationshipKind.AFFECTS,
    SubjectNamespace.PYTHON_SYMBOL: RelationshipKind.DEFINES,
    SubjectNamespace.PYTHON_TEST_CONSUMER: RelationshipKind.AFFECTS,
    SubjectNamespace.TEST_CONSUMER: RelationshipKind.AFFECTS,
}


def _relationships_for_record(
    record: EvidenceRecord, subject: NodeKey
) -> tuple[RelationshipKind, ...]:
    """Classify observed subjects without deriving links from unobserved semantics."""

    try:
        namespace = SubjectNamespace(subject.namespace)
    except ValueError:
        relationship = RelationshipKind.DECLARES
    else:
        relationship = _RELATIONSHIP_BY_SUBJECT_NAMESPACE[namespace]
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
