"""Tests for deterministic exploration scheduling and pagination primitives."""

from __future__ import annotations

from dataclasses import dataclass, replace

import pytest

from autoskillit.core import (
    CollectorReport,
    CollectorStatus,
    EvidenceRecord,
    ExplorationApplicability,
    ExplorationQuerySpec,
    FrontierItem,
    MethodProvenance,
    NodeKey,
    ProfileActivation,
    RelationshipKind,
    RepositoryIdentity,
    RepositoryProfileId,
    RepositorySnapshot,
)
from autoskillit.exploration._deterministic import (
    CursorValidationError,
    DeterministicGraphError,
    page_slice,
    stable_group,
    stable_kahn_waves,
    validate_cursor_payload,
)
from autoskillit.exploration._digest import qualified_digest
from autoskillit.exploration.completeness import evaluate_completeness
from autoskillit.exploration.graph import build_canonical_evidence_graph
from autoskillit.exploration.pagination import normalize_query, page_evidence
from autoskillit.exploration.router import readiness_waves, reclassify_cross_leaf, route_frontier

pytestmark = [
    pytest.mark.layer("exploration"),
    pytest.mark.feature("exploration"),
    pytest.mark.small,
]


@dataclass(frozen=True)
class _Work:
    key: str
    dependencies: tuple[str, ...] = ()
    scopes: tuple[str, ...] = ()


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_qualified_digest_rejects_non_finite_floats(value: float) -> None:
    with pytest.raises(ValueError, match="canonically serializable"):
        qualified_digest(b"test-domain\0", {"value": value})


@pytest.mark.parametrize(
    ("repository", "revision", "message"),
    [("", "revision", "repository"), ("repository", "", "revision")],
)
def test_repository_identity_rejects_empty_digest_anchors(
    repository: str, revision: str, message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        RepositoryIdentity(repository, revision)


def test_repository_identity_accepts_production_fallback_digest_anchors() -> None:
    identity = RepositoryIdentity("local-repository", "unborn")

    assert identity.digest == RepositoryIdentity("local-repository", "unborn").digest
    assert len(identity.digest) == 64
    assert all(character in "0123456789abcdef" for character in identity.digest)


def test_stable_kahn_waves_respect_dependencies_and_scope_conflicts() -> None:
    work = (
        _Work("source"),
        _Work("alpha", ("source",), ("shared",)),
        _Work("beta", ("source",), ("shared",)),
        _Work("gamma", ("source",), ("other",)),
        _Work("final", ("alpha", "beta", "gamma")),
    )

    waves = stable_kahn_waves(
        work,
        key=lambda item: item.key,
        dependencies=lambda item: item.dependencies,
        scope=lambda item: item.scopes,
    )

    assert [wave.items for wave in waves] == [
        ("source",),
        ("alpha", "gamma"),
        ("beta",),
        ("final",),
    ]


@pytest.mark.parametrize(
    ("work", "message"),
    [
        ((_Work("one", ("missing",)),), "unknown dependencies"),
        ((_Work("one", ("two",)), _Work("two", ("one",))), "dependency cycle"),
    ],
)
def test_stable_kahn_waves_are_strictly_closed_world(
    work: tuple[_Work, ...], message: str
) -> None:
    with pytest.raises(DeterministicGraphError, match=message):
        stable_kahn_waves(
            work,
            key=lambda item: item.key,
            dependencies=lambda item: item.dependencies,
            scope=lambda item: item.scopes,
        )


def test_stable_group_preserves_each_observation_under_its_identity() -> None:
    values = (("node-b", 3), ("node-a", 2), ("node-a", 1))

    grouped = stable_group(values, identity=lambda value: value[0], order=lambda value: value[1])

    assert grouped == (("node-a", (("node-a", 1), ("node-a", 2))), ("node-b", (("node-b", 3),)))


def test_cursor_validation_is_digest_bound_and_page_bounds_are_exact() -> None:
    digest = "a" * 64
    assert (
        validate_cursor_payload(
            {"digest": digest, "offset": 2, "page_size": 2},
            expected_digest=digest,
            expected_page_size=2,
        )
        == 2
    )
    assert page_slice(("a", "b", "c"), offset=2, page_size=2) == (("c",), None)

    with pytest.raises(CursorValidationError, match="stale"):
        validate_cursor_payload(
            {"digest": "b" * 64, "offset": 0, "page_size": 2},
            expected_digest=digest,
            expected_page_size=2,
        )


def test_router_reclassifies_then_schedules_scope_disjoint_work() -> None:
    snapshot = RepositorySnapshot(RepositoryIdentity("repo", "rev"), "tree", "collectors")
    query = ExplorationQuerySpec("find impact")
    items = reclassify_cross_leaf(
        (
            FrontierItem(
                "semantic", query, RepositoryProfileId.LANGUAGE_NEUTRAL, scope=("src/a",)
            ),
            FrontierItem("impact", query, RepositoryProfileId.LANGUAGE_NEUTRAL, scope=("src/b",)),
        ),
        handoffs={"impact": RepositoryProfileId.AUTOSKILLIT},
    )
    plan = route_frontier(
        snapshot,
        items,
        (
            ProfileActivation(
                RepositoryProfileId.LANGUAGE_NEUTRAL, ExplorationApplicability.APPLICABLE, "all"
            ),
            ProfileActivation(
                RepositoryProfileId.AUTOSKILLIT, ExplorationApplicability.APPLICABLE, "overlay"
            ),
        ),
    )

    assert [wave.items for wave in readiness_waves(plan)] == [("impact", "semantic")]
    assert plan.tasks[0].profile is RepositoryProfileId.AUTOSKILLIT


def test_router_rejects_unknown_handoffs_and_profile_boundaries() -> None:
    snapshot = RepositorySnapshot(RepositoryIdentity("repo", "rev"), "tree", "collectors")
    query = ExplorationQuerySpec(
        "find impact",
        required_profiles=(RepositoryProfileId.GENERIC_PYTHON,),
    )
    item = FrontierItem("impact", query, RepositoryProfileId.LANGUAGE_NEUTRAL)
    applicable = ProfileActivation(
        RepositoryProfileId.LANGUAGE_NEUTRAL,
        ExplorationApplicability.APPLICABLE,
        "all",
    )

    with pytest.raises(DeterministicGraphError, match="unknown frontier items"):
        reclassify_cross_leaf((item,), handoffs={"missing": RepositoryProfileId.AUTOSKILLIT})
    with pytest.raises(DeterministicGraphError, match="outside query scope"):
        route_frontier(snapshot, (item,), (applicable,))

    unrestricted = replace(item, query=ExplorationQuerySpec("find impact"))
    with pytest.raises(DeterministicGraphError, match="not applicable"):
        route_frontier(snapshot, (unrestricted,), ())
    with pytest.raises(DeterministicGraphError, match="not applicable"):
        route_frontier(
            snapshot,
            (unrestricted,),
            (
                replace(
                    applicable,
                    applicability=ExplorationApplicability.NOT_APPLICABLE,
                ),
            ),
        )
    with pytest.raises(DeterministicGraphError, match="ambiguous"):
        route_frontier(snapshot, (unrestricted,), (applicable, applicable))


def test_completeness_and_pagination_are_closed_world_and_digest_bound() -> None:
    report = CollectorReport("symbols", CollectorStatus.SUCCEEDED, "snapshot")
    completeness = evaluate_completeness(("symbols",), (report,), snapshot_digest="snapshot")
    evidence = (
        EvidenceRecord("e2", MethodProvenance.COLLECTOR, "snapshot"),
        EvidenceRecord("e1", MethodProvenance.COLLECTOR, "snapshot"),
    )

    first = page_evidence(evidence, completeness, page_size=1)
    assert [record.evidence_id for record in first.evidence] == ["e1"]
    assert first.continuation is not None
    second = page_evidence(evidence, completeness, page_size=1, cursor=first.continuation)
    assert [record.evidence_id for record in second.evidence] == ["e2"]

    incomplete = evaluate_completeness(
        ("symbols", "imports"), (report,), snapshot_digest="snapshot"
    )
    assert not incomplete.complete
    with pytest.raises(CursorValidationError, match="stale"):
        page_evidence(evidence, incomplete, page_size=1, cursor=first.continuation)


def test_completeness_rejects_closed_world_contract_violations() -> None:
    succeeded = CollectorReport("symbols", CollectorStatus.SUCCEEDED, "snapshot")
    with pytest.raises(ValueError, match="required collectors must be allowed"):
        evaluate_completeness(
            ("symbols",),
            (succeeded,),
            snapshot_digest="snapshot",
            allowed_collectors=("imports",),
        )
    with pytest.raises(ValueError, match="reports must have unique"):
        evaluate_completeness(
            ("symbols",),
            (succeeded, succeeded),
            snapshot_digest="snapshot",
        )
    with pytest.raises(ValueError, match="unexpected collector reports"):
        evaluate_completeness(
            ("symbols",),
            (succeeded, CollectorReport("imports", CollectorStatus.EMPTY, "snapshot")),
            snapshot_digest="snapshot",
        )
    with pytest.raises(ValueError, match="different repository snapshot"):
        evaluate_completeness(
            ("symbols",),
            (replace(succeeded, snapshot_digest="other"),),
            snapshot_digest="snapshot",
        )


def test_completeness_classifies_empty_as_complete_and_failed_as_incomplete() -> None:
    empty = evaluate_completeness(
        ("symbols",),
        (CollectorReport("symbols", CollectorStatus.EMPTY, "snapshot"),),
        snapshot_digest="snapshot",
    )
    failed = evaluate_completeness(
        ("symbols",),
        (CollectorReport("symbols", CollectorStatus.FAILED, "snapshot"),),
        snapshot_digest="snapshot",
    )

    assert empty.complete
    assert failed.failed_collectors == ("symbols",)


def test_normalize_query_applies_nfkc_and_whitespace_canonicalization() -> None:
    assert normalize_query("  Ａ\tquery\n") == "A query"
    with pytest.raises(ValueError, match="non-empty"):
        normalize_query("   ")


@pytest.mark.parametrize(
    ("query", "snapshot_field", "snapshot_value"),
    [
        (ExplorationQuerySpec("other"), None, None),
        (ExplorationQuerySpec("needle"), "tree_digest", "tree-b"),
        (ExplorationQuerySpec("needle"), "profile_activation_digest", "profiles-b"),
        (ExplorationQuerySpec("needle"), "profile_versions", (("generic-python", "2"),)),
        (ExplorationQuerySpec("needle"), "schema_version", "schema-b"),
        (ExplorationQuerySpec("needle"), "collector_manifest_digest", "manifest-b"),
        (ExplorationQuerySpec("needle"), "pagination_identity", "pagination-b"),
    ],
)
def test_live_cursor_invalidates_on_every_result_authority(
    query: ExplorationQuerySpec,
    snapshot_field: str | None,
    snapshot_value: object,
) -> None:
    snapshot = RepositorySnapshot(
        RepositoryIdentity("repo", "revision"),
        "tree-a",
        "manifest-a",
        profile_activation_digest="profiles-a",
        profile_versions=(("generic-python", "1"),),
        schema_version="schema-a",
        pagination_identity="pagination-a",
    )
    report = CollectorReport("symbols", CollectorStatus.SUCCEEDED, snapshot.digest)
    completeness = evaluate_completeness(
        ("symbols",),
        (report,),
        snapshot_digest=snapshot.digest,
    )
    evidence = (
        EvidenceRecord("e1", MethodProvenance.COLLECTOR, snapshot.digest),
        EvidenceRecord("e2", MethodProvenance.COLLECTOR, snapshot.digest),
    )
    first = page_evidence(
        evidence,
        completeness,
        page_size=1,
        query=ExplorationQuerySpec("needle"),
        snapshot=snapshot,
    )
    assert first.continuation is not None
    changed_snapshot = (
        snapshot
        if snapshot_field is None
        else replace(snapshot, **{snapshot_field: snapshot_value})
    )

    with pytest.raises(CursorValidationError, match="stale"):
        page_evidence(
            evidence,
            completeness,
            page_size=1,
            cursor=first.continuation,
            query=query,
            snapshot=changed_snapshot,
        )


def test_canonical_evidence_graph_preserves_contradictions_and_high_fanout() -> None:
    records = tuple(
        EvidenceRecord(
            f"evidence-{index}",
            MethodProvenance.COLLECTOR,
            "snapshot",
            subject=NodeKey("python-symbol", f"module.py:{index}:symbol_{index}"),
            facts=(f"symbol_{index}",),
            conflicts=("contradictory observation",) if index == 0 else (),
            location=f"module.py:{index + 1}",
        )
        for index in range(128)
    ) + (
        EvidenceRecord(
            "evidence-contradiction",
            MethodProvenance.COLLECTOR,
            "snapshot",
            subject=NodeKey("python-symbol", "module.py:0:symbol_0"),
            facts=("alternate symbol_0",),
            conflicts=("contradictory observation",),
            location="module.py:1",
        ),
    )

    graph = build_canonical_evidence_graph(records)
    source = NodeKey("repository-path", "module.py")
    first_symbol = NodeKey("python-symbol", "module.py:0:symbol_0")

    assert (
        len(
            [
                edge
                for edge in graph.edges
                if edge.source == source
                and edge.relationship is not RelationshipKind.CONFLICTS_WITH
            ]
        )
        == 128
    )
    assert (
        len(
            [
                edge
                for edge in graph.edges
                if edge.source == source and edge.relationship is RelationshipKind.CONFLICTS_WITH
            ]
        )
        == 1
    )
    assert next(node for node in graph.nodes if node.key == first_symbol).facts == (
        "alternate symbol_0",
        "symbol_0",
    )
    assert graph.conflicts == ("contradictory observation",)


def test_canonical_evidence_graph_emits_every_declared_relationship_from_observations() -> None:
    source = "matrix.py"
    records = (
        EvidenceRecord(
            "call-first",
            MethodProvenance.COLLECTOR,
            "snapshot",
            subject=NodeKey("python-call", "matrix.py:1:handler"),
            facts=("call handler",),
            location=f"{source}:1",
        ),
        EvidenceRecord(
            "call-second",
            MethodProvenance.COLLECTOR,
            "snapshot",
            subject=NodeKey("python-call", "matrix.py:1:handler"),
            inferences=("handler may dispatch",),
            unknowns=("handler target is unresolved",),
            location=f"{source}:1",
        ),
        EvidenceRecord(
            "configuration",
            MethodProvenance.COLLECTOR,
            "snapshot",
            subject=NodeKey("configuration-declaration", "pyproject.toml"),
            facts=("configuration observed",),
            location=f"{source}:2",
        ),
        EvidenceRecord(
            "definition",
            MethodProvenance.COLLECTOR,
            "snapshot",
            subject=NodeKey("python-symbol", "matrix.py:3:handler"),
            conflicts=("handler declarations disagree",),
            location=f"{source}:3",
        ),
        EvidenceRecord(
            "declaration",
            MethodProvenance.COLLECTOR,
            "snapshot",
            subject=NodeKey("python-protocol", "matrix.py:4:Handler"),
            location=f"{source}:4",
        ),
        EvidenceRecord(
            "import",
            MethodProvenance.COLLECTOR,
            "snapshot",
            subject=NodeKey("python-import", "package.handler"),
            location=f"{source}:5",
        ),
        EvidenceRecord(
            "reference",
            MethodProvenance.COLLECTOR,
            "snapshot",
            subject=NodeKey("python-alias", "matrix.py:alias"),
            location=f"{source}:6",
        ),
    )

    graph = build_canonical_evidence_graph(records)
    reversed_graph = build_canonical_evidence_graph(reversed(records))

    assert {edge.relationship for edge in graph.edges} == set(RelationshipKind)
    assert graph.edges == reversed_graph.edges
    call_edge = next(edge for edge in graph.edges if edge.relationship is RelationshipKind.CALLS)
    assert call_edge.evidence_ids == ("call-first", "call-second")
    assert call_edge.unknowns == ("handler target is unresolved",)
    conflict_edge = next(
        edge for edge in graph.edges if edge.relationship is RelationshipKind.CONFLICTS_WITH
    )
    assert conflict_edge.evidence_ids == ("definition",)
    assert graph.conflicts == ("handler declarations disagree",)


def test_empty_result_identity_binds_query_and_snapshot_authority() -> None:
    snapshot = RepositorySnapshot(
        RepositoryIdentity("repo", "revision"),
        "tree",
        "manifest-a",
        profile_activation_digest="profiles-a",
        schema_version="schema-a",
    )
    completeness = evaluate_completeness((), (), snapshot_digest=snapshot.digest)

    first = page_evidence(
        (),
        completeness,
        page_size=1,
        query=ExplorationQuerySpec("needle"),
        snapshot=snapshot,
    )
    query_changed = page_evidence(
        (),
        completeness,
        page_size=1,
        query=ExplorationQuerySpec("other"),
        snapshot=snapshot,
    )
    manifest_changed = page_evidence(
        (),
        completeness,
        page_size=1,
        query=ExplorationQuerySpec("needle"),
        snapshot=replace(snapshot, collector_manifest_digest="manifest-b"),
    )

    assert not first.evidence
    assert first.result_digest != query_changed.result_digest
    assert first.result_digest != manifest_changed.result_digest
