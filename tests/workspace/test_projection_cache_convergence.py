"""Projection-cache reconciliation classifies every direct child safely."""

from __future__ import annotations

import json
import warnings
from contextlib import nullcontext
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import Mock

import pytest

import autoskillit.workspace._projected_artifact._artifact_residue as artifact_residue
import autoskillit.workspace._projection_cache as projection_cache
from autoskillit.core import (
    ArtifactLeaseContention,
    PluginArtifactUnavailableError,
    managed_home_for,
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
        (
            f".{_STALE_KEY}.autoskillit-projection.json.hook-quarantine-{'0' * 64}",
            projection_cache.ProjectionEntryClass.HOOK_QUARANTINE_SIDECAR,
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
            projection_cache.ProjectionEntryClass.HOOK_QUARANTINE_SIDECAR,
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

    expected = (
        projection_cache.ProjectionReconcileDisposition.DEFERRED_IO_ERROR
        if name.endswith("autoskillit-residue-0123456789abcdef")
        else projection_cache.ProjectionReconcileDisposition.DEFERRED_UNMANAGED
    )
    assert disposition is expected
    owner.lease_path.assert_not_called()
    owner.identity_for_path.assert_not_called()
    owner.enqueue_retirement.assert_not_called()


def test_reconcile_unavailable_projection_identity_does_not_mutate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entry = tmp_path / _STALE_KEY
    entry.mkdir()
    owner = Mock()
    owner.lease_path.return_value = tmp_path / "lease.lock"
    owner.identity_for_path.side_effect = PluginArtifactUnavailableError("identity unavailable")
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

    assert disposition is projection_cache.ProjectionReconcileDisposition.DEFERRED_UNAVAILABLE
    owner.enqueue_retirement.assert_not_called()
    assert entry.is_dir()
    assert not projection_cache.residue_staging_path(entry).exists()


def test_reconcile_resumes_deterministic_residue_after_crash(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "projections"
    root.mkdir()
    managed_path = root / _STALE_KEY
    residue = projection_cache.residue_staging_path(managed_path)
    residue.mkdir()
    manifest = projection_cache.projected_artifact_manifest_path(managed_path)
    manifest.write_text("{}", encoding="utf-8")
    owner = Mock()
    owner._contains.return_value = True
    owner.lease_path.return_value = projection_cache.projected_artifact_lease_path(managed_path)
    owner.manifest_path.return_value = manifest
    monkeypatch.setattr(projection_cache, "_InstallLock", lambda _home: nullcontext())
    monkeypatch.setattr(
        projection_cache.ArtifactLease,
        "acquire_exclusive",
        lambda *_args, **_kwargs: _ClosePreservingWriter(),
    )

    disposition = projection_cache._reconcile_projection_entry(
        residue,
        root=root,
        home=object(),
        owner=owner,
        active_key=_ACTIVE_KEY,
        not_before=datetime.now(UTC),
    )

    assert disposition is projection_cache.ProjectionReconcileDisposition.RESUMED
    assert not residue.exists()
    assert not manifest.exists()
    owner.lease_path.assert_called_once_with(managed_path)


def test_residue_resume_defers_when_the_original_key_lease_is_contended(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "projections"
    root.mkdir()
    managed_path = root / _STALE_KEY
    residue = projection_cache.residue_staging_path(managed_path)
    residue.mkdir()
    manifest = projection_cache.projected_artifact_manifest_path(managed_path)
    manifest.write_text("{}", encoding="utf-8")
    lease_path = projection_cache.projected_artifact_lease_path(managed_path)
    owner = Mock()
    owner._contains.return_value = True
    owner.lease_path.return_value = lease_path
    owner.manifest_path.return_value = manifest
    monkeypatch.setattr(projection_cache, "_InstallLock", lambda _home: nullcontext())

    def contend(path: Path, **_kwargs: object) -> _ClosePreservingWriter:
        assert path == lease_path
        raise ArtifactLeaseContention(path)

    monkeypatch.setattr(
        projection_cache.ArtifactLease,
        "acquire_exclusive",
        contend,
    )

    disposition = projection_cache._reconcile_projection_entry(
        residue,
        root=root,
        home=object(),
        owner=owner,
        active_key=_ACTIVE_KEY,
        not_before=datetime.now(UTC),
    )

    assert disposition is projection_cache.ProjectionReconcileDisposition.DEFERRED_CONTENDED
    assert residue.is_dir()
    assert manifest.is_file()
    owner.lease_path.assert_called_once_with(managed_path)


@pytest.mark.parametrize(
    ("mutation", "expected"),
    [
        ("disappear", projection_cache.ProjectionReconcileDisposition.ALREADY_ABSENT),
        ("symlink", projection_cache.ProjectionReconcileDisposition.DEFERRED_UNMANAGED),
    ],
)
def test_quarantine_refuses_targets_that_change_before_rename(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
    expected: projection_cache.ProjectionReconcileDisposition,
) -> None:
    root = tmp_path / "projections"
    root.mkdir()
    entry = root / _STALE_KEY
    entry.mkdir()
    owner = Mock()
    owner._contains.return_value = True
    owner.manifest_path.return_value = projection_cache.projected_artifact_manifest_path(entry)
    revalidate = projection_cache._revalidate_projection_mutation_target
    calls = 0

    def mutate_after_initial_validation(
        target: Path,
        **kwargs: object,
    ) -> projection_cache.ProjectionReconcileDisposition | None:
        nonlocal calls
        refusal = revalidate(target, **kwargs)
        calls += 1
        if calls == 1:
            target.rmdir()
            if mutation == "symlink":
                outside = tmp_path / "outside"
                outside.mkdir()
                target.symlink_to(outside, target_is_directory=True)
        return refusal

    monkeypatch.setattr(
        projection_cache,
        "_revalidate_projection_mutation_target",
        mutate_after_initial_validation,
    )

    disposition = projection_cache._quarantine_invalid_projection(
        entry,
        root=root,
        owner=owner,
        active_key=_ACTIVE_KEY,
    )

    assert disposition is expected
    assert not projection_cache.residue_staging_path(entry).exists()
    assert calls == 2


def test_residue_resume_retries_after_rmtree_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "projections"
    root.mkdir()
    managed_path = root / _STALE_KEY
    residue = projection_cache.residue_staging_path(managed_path)
    residue.mkdir()
    manifest = projection_cache.projected_artifact_manifest_path(managed_path)
    manifest.write_text("{}", encoding="utf-8")
    owner = Mock()
    owner._contains.return_value = True
    owner.lease_path.return_value = projection_cache.projected_artifact_lease_path(managed_path)
    owner.manifest_path.return_value = manifest
    monkeypatch.setattr(projection_cache, "_InstallLock", lambda _home: nullcontext())
    monkeypatch.setattr(
        projection_cache.ArtifactLease,
        "acquire_exclusive",
        lambda *_args, **_kwargs: _ClosePreservingWriter(),
    )
    rmtree = artifact_residue.shutil.rmtree
    attempts = 0

    def fail_once(path: Path) -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise OSError("rmtree failed")
        rmtree(path)

    monkeypatch.setattr(artifact_residue.shutil, "rmtree", fail_once)

    first = projection_cache._reconcile_projection_entry(
        residue,
        root=root,
        home=object(),
        owner=owner,
        active_key=_ACTIVE_KEY,
        not_before=datetime.now(UTC),
    )
    second = projection_cache._reconcile_projection_entry(
        residue,
        root=root,
        home=object(),
        owner=owner,
        active_key=_ACTIVE_KEY,
        not_before=datetime.now(UTC),
    )

    assert first is projection_cache.ProjectionReconcileDisposition.DEFERRED_IO_ERROR
    assert second is projection_cache.ProjectionReconcileDisposition.RESUMED
    assert attempts == 2
    assert not residue.exists()
    assert not manifest.exists()


def test_schema_drift_is_contained_while_stale_projection_is_reconciled(
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
            home=managed_home_for(tmp_path),
            active_key=_ACTIVE_KEY,
        )

    assert queued == 0
    assert not candidate.exists()
    assert not projection_cache.projected_artifact_manifest_path(candidate).exists()
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
            home=managed_home_for(tmp_path),
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
        lambda *_args, **_kwargs: Mock(managed_root=root),
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
            home=managed_home_for(tmp_path),
            active_key=_ACTIVE_KEY,
        )
        == 1
    )
    assert seen == sorted(name if isinstance(name, str) else name[0] for name in names)


class _ClosePreservingWriter:
    def close_preserving(self) -> None:
        pass
