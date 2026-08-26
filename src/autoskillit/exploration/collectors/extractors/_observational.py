"""Observational collectors — relabel existing reports under typed subject namespaces.

Decomposed from the original ``collectors/extractors.py`` per #4836. Each
``collect_*`` wrapper delegates to ``_relabel``, which restamps a captured
report's evidence IDs and (when applicable) subject namespace.
"""

from __future__ import annotations

import hashlib
import json
import tomllib
from dataclasses import replace
from pathlib import Path

from autoskillit.core import (
    CollectorReport,
    CollectorStatus,
    MethodProvenance,
    NodeKey,
)

from ...graph import SubjectNamespace
from .._bounded import (
    CollectorLimits,
    CollectorSafetyError,
    read_contained_file,
)
from ._evidence import _collector_metadata, _evidence, _report
from ._file_search import collect_artifact

__all__ = [
    "collect_architecture",
    "collect_autoskillit_registry",
    "collect_autoskillit_toml",
    "collect_coverage_observation",
    "collect_generated_artifact",
    "collect_observational_artifact",
    "collect_python_stub",
    "collect_test_map_observation",
    "collect_unsupported",
    "_relabel",
]


def collect_unsupported(
    root: Path,
    snapshot_digest: str,
    limits: CollectorLimits,
    *,
    collector_id: str,
) -> CollectorReport:
    del root, limits
    return _report(
        collector_id,
        snapshot_digest,
        CollectorStatus.UNSUPPORTED,
        ("native capability is not available in the collector runtime",),
    )


def collect_autoskillit_toml(
    root: Path, snapshot_digest: str, path: str, limits: CollectorLimits
) -> CollectorReport:
    collector_id = "autoskillit-manifest"
    try:
        data = tomllib.loads(read_contained_file(root, path, limits).decode("utf-8"))
    except (CollectorSafetyError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        return _report(collector_id, snapshot_digest, CollectorStatus.FAILED, (str(exc),))
    excerpt = json.dumps(data, sort_keys=True, default=str)[: limits.max_output_bytes]
    return _report(
        collector_id,
        snapshot_digest,
        CollectorStatus.SUCCEEDED,
        evidence=(
            replace(
                _evidence(
                    collector_id,
                    snapshot_digest,
                    path,
                    1,
                    excerpt,
                ),
                subject=NodeKey(SubjectNamespace.CONFIGURATION_DECLARATION.value, path),
            ),
        ),
    )


def collect_observational_artifact(
    root: Path, snapshot_digest: str, path: str, limits: CollectorLimits
) -> CollectorReport:
    """Read coverage and test-map artifacts without interpreting them as execution truth."""

    return collect_artifact(root, snapshot_digest, path, limits)


def _relabel(
    report: CollectorReport,
    collector_id: str,
    *,
    subject_namespace: SubjectNamespace | None = None,
) -> CollectorReport:
    method, version = _collector_metadata(collector_id)
    evidence = tuple(
        replace(
            record,
            evidence_id=hashlib.sha256(
                f"{collector_id}\0{record.evidence_id}".encode()
            ).hexdigest(),
            provenance=MethodProvenance.COLLECTOR,
            method=method,
            extractor_version=version,
            subject=(
                NodeKey(subject_namespace.value, record.subject.value)
                if subject_namespace is not None and record.subject is not None
                else record.subject
            ),
        )
        for record in report.evidence
    )
    return replace(report, collector_id=collector_id, evidence=evidence)


def collect_autoskillit_registry(
    root: Path, snapshot_digest: str, scope: str, limits: CollectorLimits
) -> CollectorReport:
    # Lazy import: ``collect_python_ast`` is the only caller of stdlib ``ast`` in
    # this shard, and most observational collects do not exercise it.
    from ._python_ast import collect_python_ast

    return _relabel(
        collect_python_ast(root, snapshot_digest, scope, limits), "autoskillit-registry"
    )


def collect_architecture(
    root: Path, snapshot_digest: str, path: str, limits: CollectorLimits
) -> CollectorReport:
    return _relabel(
        collect_artifact(root, snapshot_digest, path, limits), "autoskillit-architecture"
    )


def collect_python_stub(
    root: Path, snapshot_digest: str, path: str, limits: CollectorLimits
) -> CollectorReport:
    if not path.endswith(".pyi"):
        return _report(
            "python-stub",
            snapshot_digest,
            CollectorStatus.FAILED,
            ("python stub path must end in .pyi",),
        )
    return _relabel(collect_artifact(root, snapshot_digest, path, limits), "python-stub")


def collect_generated_artifact(
    root: Path, snapshot_digest: str, path: str, limits: CollectorLimits
) -> CollectorReport:
    return _relabel(
        collect_artifact(root, snapshot_digest, path, limits),
        "generated-artifact",
        subject_namespace=SubjectNamespace.GENERATED_ARTIFACT,
    )


def collect_coverage_observation(
    root: Path, snapshot_digest: str, path: str, limits: CollectorLimits
) -> CollectorReport:
    return _relabel(
        collect_observational_artifact(root, snapshot_digest, path, limits),
        "coverage-observation",
        subject_namespace=SubjectNamespace.COVERAGE_OBSERVATION,
    )


def collect_test_map_observation(
    root: Path, snapshot_digest: str, path: str, limits: CollectorLimits
) -> CollectorReport:
    return _relabel(
        collect_observational_artifact(root, snapshot_digest, path, limits),
        "test-map-observation",
        subject_namespace=SubjectNamespace.TEST_CONSUMER,
    )
