"""Projection-cache reconciliation classifies every direct child safely."""

from __future__ import annotations

import json
import warnings
from contextlib import nullcontext
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import Mock

import pytest

import autoskillit.workspace._projection_cache as projection_cache
from autoskillit.core import (
    PluginArtifactUnavailableError,
    PluginArtifactValidationError,
)

pytestmark = [pytest.mark.layer("workspace"), pytest.mark.medium]


_ACTIVE_KEY = "a" * 24
_STALE_KEY = "b" * 24
_UUID = "01234567-89ab-cdef-0123-456789abcdef"


def _entry_names() -> tuple[tuple[str, projection_cache.ProjectionEntryClass], ...]:
    return (
        (_ACTIVE_KEY, projection_cache.ProjectionEntryClass.ACTIVE_ROOT),
        (_STALE_KEY, projection_cache.ProjectionEntryClass.PROJECTION_ROOT),
        (
            f".{_STALE_KEY}.autoskillit-projection.json",
            projection_cache.ProjectionEntryClass.IDENTITY_SIDECAR,
        ),
        (".artifact-leases", projection_cache.ProjectionEntryClass.LEASE_DIRECTORY),
        (
            f".{_STALE_KEY}.plugin-{_UUID}",
            projection_cache.ProjectionEntryClass.PUBLICATION_STAGING_ROOT,
        ),
        (
            f".{_STALE_KEY}.manifest-{_UUID}.json",
            projection_cache.ProjectionEntryClass.PUBLICATION_STAGING_MANIFEST,
        ),
        (
            f".{_STALE_KEY}.autoskillit-retiring-0123456789abcdef",
            projection_cache.ProjectionEntryClass.RETIREMENT_STAGING_ROOT,
        ),
        (
            f".{_STALE_KEY}.autoskillit-residue-0123456789abcdef",
            projection_cache.ProjectionEntryClass.RESIDUE_STAGING_ROOT,
        ),
    )


@pytest.mark.parametrize(("name", "expected"), _entry_names())
def test_classify_projection_entry_covers_every_known_direct_child(
    tmp_path: Path,
    name: str,
    expected: projection_cache.ProjectionEntryClass,
) -> None:
    assert (
        projection_cache.classify_projection_entry(tmp_path / name, active_key=_ACTIVE_KEY)
        is expected
    )


def test_classifier_examples_exhaust_the_entry_class_enum() -> None:
    assert {expected for _name, expected in _entry_names()} == set(
        projection_cache.ProjectionEntryClass
    )


@pytest.mark.parametrize(
    "name",
    [
        name
        for name, entry_class in _entry_names()
        if entry_class
        in {
            projection_cache.ProjectionEntryClass.IDENTITY_SIDECAR,
            projection_cache.ProjectionEntryClass.LEASE_DIRECTORY,
            projection_cache.ProjectionEntryClass.PUBLICATION_STAGING_ROOT,
            projection_cache.ProjectionEntryClass.PUBLICATION_STAGING_MANIFEST,
            projection_cache.ProjectionEntryClass.RETIREMENT_STAGING_ROOT,
            projection_cache.ProjectionEntryClass.RESIDUE_STAGING_ROOT,
        }
    ],
)
def test_reconcile_never_enqueues_protected_lease_or_staging_entries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    name: str,
) -> None:
    entry = tmp_path / name
    owner = Mock()
    monkeypatch.setattr(projection_cache, "_InstallLock", lambda _home: nullcontext())

    disposition = projection_cache._reconcile_projection_entry(
        entry,
        root=tmp_path,
        home=object(),
        owner=owner,
        active_key=_ACTIVE_KEY,
        not_before=datetime.now(UTC),
    )

    assert disposition is projection_cache.ProjectionReconcileDisposition.DEFERRED_UNMANAGED
    owner.lease_path.assert_not_called()
    owner.identity_for_path.assert_not_called()
    owner.enqueue_retirement.assert_not_called()


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (
            PluginArtifactValidationError("bad identity"),
            projection_cache.ProjectionReconcileDisposition.DEFERRED_INVALID_IDENTITY,
        ),
        (
            PluginArtifactUnavailableError("identity unavailable"),
            projection_cache.ProjectionReconcileDisposition.DEFERRED_UNAVAILABLE,
        ),
    ],
)
def test_reconcile_defers_invalid_or_unavailable_projection_identities(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
    expected: projection_cache.ProjectionReconcileDisposition,
) -> None:
    entry = tmp_path / _STALE_KEY
    entry.mkdir()
    owner = Mock()
    owner.lease_path.return_value = tmp_path / "lease.lock"
    owner.identity_for_path.side_effect = error
    monkeypatch.setattr(projection_cache, "_InstallLock", lambda _home: nullcontext())
    monkeypatch.setattr(
        projection_cache.ArtifactLease,
        "acquire_exclusive",
        lambda *_args, **_kwargs: _ClosePreservingWriter(),
    )

    disposition = projection_cache._reconcile_projection_entry(
        entry,
        root=tmp_path,
        home=object(),
        owner=owner,
        active_key=_ACTIVE_KEY,
        not_before=datetime.now(UTC),
    )

    assert disposition is expected
    owner.enqueue_retirement.assert_not_called()


def test_schema_drift_is_contained_while_stale_projection_stays_deferred(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "projections"
    root.mkdir()
    candidate = root / _STALE_KEY
    candidate.mkdir()
    projection_cache.projected_artifact_manifest_path(candidate).write_text(
        json.dumps({"schema_version": 1}),
        encoding="utf-8",
    )
    monkeypatch.setattr(projection_cache, "_InstallLock", lambda _home: nullcontext())
    monkeypatch.setattr(
        projection_cache.ArtifactLease,
        "acquire_exclusive",
        lambda *_args, **_kwargs: _ClosePreservingWriter(),
    )

    with warnings.catch_warnings(record=True) as captured:
        warnings.simplefilter("always")
        queued = projection_cache.prune_stale_projections(
            root,
            home=object(),
            active_key=_ACTIVE_KEY,
        )

    assert queued == 0
    assert candidate.is_dir()
    assert not any("schema_drift" in str(warning.message) for warning in captured)


def test_prune_contains_classifier_value_errors_and_reports_the_disposition(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "projections"
    root.mkdir()
    entry = root / "unexpected-entry"
    entry.mkdir()
    logger = Mock()
    monkeypatch.setattr(projection_cache, "_InstallLock", lambda _home: nullcontext())
    monkeypatch.setattr(projection_cache, "logger", logger)

    assert (
        projection_cache.prune_stale_projections(
            root,
            home=object(),
            active_key=_ACTIVE_KEY,
        )
        == 0
    )

    logger.warning.assert_called_once_with(
        "projected_plugin_reconcile",
        path=str(entry),
        entry_class="unclassified",
        disposition="deferred_unclassified",
    )


def test_prune_enumerates_every_direct_child_and_counts_only_new_queue_entries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "projections"
    root.mkdir()
    names = ("unclassified", *_entry_names())
    for item in names:
        name = item if isinstance(item, str) else item[0]
        (root / name).mkdir()

    seen: list[str] = []

    def reconcile(
        entry: Path,
        **_kwargs: object,
    ) -> projection_cache.ProjectionReconcileDisposition:
        seen.append(entry.name)
        if entry.name == _STALE_KEY:
            return projection_cache.ProjectionReconcileDisposition.QUEUED_FOR_RETIREMENT
        return projection_cache.ProjectionReconcileDisposition.DEFERRED_UNMANAGED

    monkeypatch.setattr(projection_cache, "_InstallLock", lambda _home: nullcontext())
    monkeypatch.setattr(
        projection_cache,
        "ProjectedPluginRetirementOwner",
        lambda *_args, **_kwargs: object(),
    )
    monkeypatch.setattr(projection_cache, "_reconcile_projection_entry", reconcile)
    monkeypatch.setattr(
        projection_cache,
        "_log_projection_reconcile",
        lambda *_args, **_kwargs: None,
    )

    assert (
        projection_cache.prune_stale_projections(
            root,
            home=object(),
            active_key=_ACTIVE_KEY,
        )
        == 1
    )
    assert seen == sorted(name if isinstance(name, str) else name[0] for name in names)


class _ClosePreservingWriter:
    def close_preserving(self) -> None:
        pass
