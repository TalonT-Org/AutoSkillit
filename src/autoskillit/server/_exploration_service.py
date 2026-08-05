"""Server-owned adapter from typed exploration queries to bounded collectors."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from autoskillit.core import (
    CollectorReport,
    CollectorStatus,
    ContinuationCursor,
    EvidencePage,
    EvidenceRecord,
    ExplorationQuerySpec,
    FrontierItem,
    ProfileActivation,
    RepositoryProfileId,
    RepositorySnapshot,
    get_logger,
)
from autoskillit.exploration import (
    COLLECTOR_PROFILES,
    CollectorLimits,
    CollectorProfile,
    RepositoryProfileActivation,
    build_canonical_evidence_graph,
    capture_repository_snapshot,
    collector_manifest_digest,
    evaluate_completeness,
    page_evidence,
    readiness_waves,
    reclassify_cross_leaf,
    route_frontier,
)
from autoskillit.pipeline import ExplorationContext

logger = get_logger(__name__)


class DefaultExplorationService:
    """Run the profile-scoped, non-executing collector plan for one snapshot."""

    @staticmethod
    def _capture_snapshot_and_activation(
        root: Path,
    ) -> tuple[RepositorySnapshot, RepositoryProfileActivation]:
        """Return only snapshot and profile state validated by the same capture."""
        captured = capture_repository_snapshot(
            root,
            collector_manifest_digest=collector_manifest_digest(),
        )
        snapshot = captured.snapshot
        activation = captured.validated_activation
        if snapshot is None or activation is None or snapshot.stale or snapshot.truncated:
            raise RuntimeError(f"repository snapshot {captured.status}")
        return snapshot, activation

    def capture_snapshot(self, root: Path) -> RepositorySnapshot:
        """Capture one complete snapshot using the current collector manifest."""
        snapshot, _activation = self._capture_snapshot_and_activation(root)
        return snapshot

    @staticmethod
    def _planned_collectors(
        query: ExplorationQuerySpec, activations: tuple[ProfileActivation, ...]
    ) -> tuple[CollectorProfile, ...]:
        applicable = {
            activation.profile
            for activation in activations
            if activation.applicability.value == "applicable"
        }
        selected_profiles = set(query.required_profiles).union(applicable)
        # The language-neutral observation layer supplies bounded repository context for every
        # specialized profile. It is always applicable and its inclusion is deterministic.
        selected_profiles.add(RepositoryProfileId.LANGUAGE_NEUTRAL)
        return tuple(
            sorted(
                (
                    profile
                    for profile in COLLECTOR_PROFILES
                    if profile.profile in selected_profiles
                ),
                key=lambda profile: profile.collector_id,
            )
        )

    @staticmethod
    def _failed_report(
        collector: CollectorProfile, snapshot_digest: str, diagnostic: str
    ) -> CollectorReport:
        return CollectorReport(
            collector_id=collector.collector_id,
            status=CollectorStatus.FAILED,
            snapshot_digest=snapshot_digest,
            diagnostic=diagnostic,
        )

    @staticmethod
    def _inapplicable_report(
        collector: CollectorProfile, snapshot_digest: str, diagnostic: str
    ) -> CollectorReport:
        return CollectorReport(
            collector_id=collector.collector_id,
            status=CollectorStatus.UNSUPPORTED,
            snapshot_digest=snapshot_digest,
            diagnostic=diagnostic,
        )

    @staticmethod
    def _validate_evidence_graph(reports: tuple[CollectorReport, ...]) -> None:
        """Reject projections that reference evidence outside this collector generation."""

        evidence_ids = {record.evidence_id for report in reports for record in report.evidence}
        graph = build_canonical_evidence_graph(
            record for report in reports for record in report.evidence
        )
        graph_evidence_ids = {
            evidence_id for node in graph.nodes for evidence_id in node.evidence_ids
        }
        graph_evidence_ids.update(
            evidence_id for edge in graph.edges for evidence_id in edge.evidence_ids
        )
        if not graph_evidence_ids.issubset(evidence_ids):
            raise RuntimeError(
                "canonical graph references evidence outside the collector generation"
            )

    def _execute(
        self,
        collector: CollectorProfile,
        query: ExplorationQuerySpec,
        *,
        root: Path,
        snapshot_digest: str,
    ) -> CollectorReport:
        try:
            limits = CollectorLimits(max_matches=query.max_results)
            reports = collector.invocation(
                root,
                snapshot_digest,
                query.query,
                query.scope,
                limits,
            )
            uncertainty = ("query is evaluated only by the registered collector method",)
            all_reports = tuple(report for _scope, report in reports)
            statuses = {report.status for report in all_reports}
            if CollectorStatus.FAILED in statuses:
                status = CollectorStatus.FAILED
            elif CollectorStatus.TRUNCATED in statuses:
                status = CollectorStatus.TRUNCATED
            elif CollectorStatus.UNSUPPORTED in statuses:
                status = CollectorStatus.UNSUPPORTED
            elif statuses == {CollectorStatus.EMPTY}:
                status = CollectorStatus.EMPTY
            else:
                status = CollectorStatus.SUCCEEDED
            evidence = tuple(
                sorted(
                    (
                        replace(
                            record,
                            searched_scope=scope,
                            query_uncertainty=tuple(
                                sorted(set((*record.query_uncertainty, *uncertainty)))
                            ),
                        )
                        for scope, report in reports
                        for record in report.evidence
                    ),
                    key=lambda record: record.evidence_id,
                )
            )
            diagnostics = tuple(
                sorted(
                    {report.diagnostic for report in all_reports if report.diagnostic is not None}
                )
            )
            return CollectorReport(
                collector.collector_id,
                status,
                snapshot_digest,
                evidence,
                "; ".join(diagnostics) or None,
            )
        except Exception as exc:
            logger.warning("exploration collector failed", exc_info=True)
            return self._failed_report(
                collector, snapshot_digest, f"collector failure: {type(exc).__name__}"
            )

    def collect(self, query: ExplorationQuerySpec, *, root: Path) -> ExplorationContext:
        snapshot, activation = self._capture_snapshot_and_activation(root)
        collectors = self._planned_collectors(query, activation.activations)
        expected_collectors = tuple(
            collector.collector_id for collector in collectors if collector.required_by_default
        )
        active_profiles = {
            item.profile
            for item in activation.activations
            if item.applicability.value == "applicable"
        }
        reports: list[CollectorReport] = []
        task_query = replace(query, required_profiles=())
        runnable = tuple(
            collector for collector in collectors if collector.profile in active_profiles
        )
        inactive = tuple(
            collector for collector in collectors if collector.profile not in active_profiles
        )
        reports.extend(
            self._inapplicable_report(
                collector,
                snapshot.digest,
                f"required profile {collector.profile.value} is not applicable to the snapshot",
            )
            for collector in inactive
        )
        discovered_frontier = tuple(
            FrontierItem(
                collector.collector_id,
                task_query,
                RepositoryProfileId.LANGUAGE_NEUTRAL,
                scope=query.scope,
            )
            for collector in runnable
        )
        routed_frontier = reclassify_cross_leaf(
            discovered_frontier,
            handoffs={
                collector.collector_id: collector.profile
                for collector in runnable
                if collector.profile is not RepositoryProfileId.LANGUAGE_NEUTRAL
            },
        )
        plan = route_frontier(snapshot, routed_frontier, activation.activations)
        runnable_by_id = {collector.collector_id: collector for collector in runnable}
        for wave in readiness_waves(plan):
            for collector_id in wave.items:
                reports.append(
                    self._execute(
                        runnable_by_id[collector_id],
                        query,
                        root=root,
                        snapshot_digest=snapshot.digest,
                    )
                )
        try:
            publication, _publication_activation = self._capture_snapshot_and_activation(root)
        except RuntimeError as exc:
            raise RuntimeError(
                "exploration publication rejected because repository state changed "
                "or was incomplete"
            ) from exc
        if publication.digest != snapshot.digest:
            raise RuntimeError(
                "exploration publication rejected because repository state changed "
                "or was incomplete"
            )
        completeness = evaluate_completeness(
            expected_collectors,
            reports,
            snapshot_digest=snapshot.digest,
            allowed_collectors=tuple(collector.collector_id for collector in collectors),
        )
        evidence_by_id: dict[str, EvidenceRecord] = {}
        for report in completeness.reports:
            for record in report.evidence:
                existing = evidence_by_id.setdefault(record.evidence_id, record)
                if existing != record:
                    raise RuntimeError("collector evidence identity collision")
        self._validate_evidence_graph(completeness.reports)
        return ExplorationContext(
            query=query,
            snapshot=snapshot,
            evidence=tuple(evidence_by_id[evidence_id] for evidence_id in sorted(evidence_by_id)),
            completeness=completeness,
        )

    def page(
        self,
        context: ExplorationContext,
        *,
        page_size: int,
        cursor: ContinuationCursor | None = None,
    ) -> EvidencePage:
        """Use the canonical page encoder so stale cursors cannot be replayed."""
        return page_evidence(
            context.evidence,
            context.completeness,
            page_size=page_size,
            cursor=cursor,
            query=context.query,
            snapshot=context.snapshot,
        )
