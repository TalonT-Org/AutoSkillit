"""Closed-world completeness evaluation for collector evidence."""

from __future__ import annotations

from collections.abc import Iterable

from autoskillit.core import CollectorReport, CollectorStatus, CompletenessReport


def evaluate_completeness(
    expected_collectors: Iterable[str],
    reports: Iterable[CollectorReport],
    *,
    snapshot_digest: str,
    allowed_collectors: Iterable[str] | None = None,
) -> CompletenessReport:
    """Require one successful or explicitly-empty report for every expected collector."""

    expected_items = tuple(expected_collectors)
    expected = tuple(sorted(set(expected_items)))
    if len(expected) != len(expected_items):
        raise ValueError("expected collector identifiers must be unique")
    allowed_items = expected_items if allowed_collectors is None else tuple(allowed_collectors)
    allowed = set(allowed_items)
    if len(allowed) != len(allowed_items):
        raise ValueError("allowed collector identifiers must be unique")
    if not set(expected).issubset(allowed):
        raise ValueError("required collectors must be allowed")
    report_list = tuple(reports)
    report_ids = [report.collector_id for report in report_list]
    if len(set(report_ids)) != len(report_ids):
        raise ValueError("collector reports must have unique identifiers")
    unexpected = set(report_ids).difference(allowed)
    if unexpected:
        raise ValueError(f"unexpected collector reports: {sorted(unexpected)!r}")
    if any(report.snapshot_digest != snapshot_digest for report in report_list):
        raise ValueError("collector report has a different repository snapshot")
    by_id = {report.collector_id: report for report in report_list}
    missing = tuple(collector for collector in expected if collector not in by_id)
    failed = tuple(
        collector
        for collector in expected
        if collector in by_id
        and by_id[collector].status not in {CollectorStatus.SUCCEEDED, CollectorStatus.EMPTY}
    )
    return CompletenessReport(
        expected_collectors=expected,
        reports=tuple(sorted(report_list, key=lambda report: report.collector_id)),
        complete=not missing and not failed,
        missing_collectors=missing,
        failed_collectors=failed,
    )
