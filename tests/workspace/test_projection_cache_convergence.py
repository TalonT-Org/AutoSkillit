"""Projection-cache reconciliation classifies every direct child safely."""

from __future__ import annotations

import json
import os
import uuid
import warnings
from contextlib import nullcontext
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import Mock

import pytest
from structlog.testing import capture_logs

import autoskillit.workspace._projected_artifact._artifact_residue as artifact_residue
import autoskillit.workspace._projection_cache as projection_cache
from autoskillit.core import (
    ArtifactLease,
    ArtifactLeaseContention,
    PluginArtifactUnavailableError,
    managed_home_for,
)
from tests._retention_surface import (
    RECLAIMER_CONVERGENCE_CASES,
    assert_second_pass_is_quiet,
)

pytestmark = [pytest.mark.layer("workspace"), pytest.mark.medium]


_ACTIVE_KEY = "a" * 24
_STALE_KEY = "b" * 24
_UUID = "01234567-89ab-cdef-0123-456789abcdef"


def _filesystem_snapshot(root: Path) -> tuple[tuple[str, str, bytes], ...]:
    """Return a stable, symlink-safe snapshot of one isolated test root."""
    snapshot: list[tuple[str, str, bytes]] = []
    if not root.exists():
        return ()
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            snapshot.append((relative, "symlink", os.readlink(path).encode()))
        elif path.is_dir():
            snapshot.append((relative, "directory", b""))
        else:
            snapshot.append((relative, "file", path.read_bytes()))
    return tuple(snapshot)


def _warning_or_error_events(logs: list[dict[str, object]]) -> list[dict[str, object]]:
    return [entry for entry in logs if entry.get("log_level") in {"warning", "error"}]


def _seed_invalid_projection(root: Path, failure_class: str) -> tuple[Path, Path]:
    candidate = root / _STALE_KEY
    candidate.mkdir(parents=True)
    (candidate / "plugin.json").write_text("{}\n", encoding="utf-8")
    manifest = projection_cache.projected_artifact_manifest_path(candidate)
    valid_manifest: dict[str, object] = {
        "schema_version": projection_cache.PROJECTION_ARTIFACT_MANIFEST_SCHEMA_VERSION,
        "artifact_kind": "projection",
        "projection_version": 2,
        "semantic_key": _STALE_KEY,
        "incarnation_id": uuid.uuid4().hex,
        "artifact_digest": projection_cache.projected_plugin_artifact_digest(candidate),
        "skills": {},
    }
    if failure_class == "missing_manifest":
        pass
    elif failure_class == "malformed_json_manifest":
        manifest.write_text("{not-json", encoding="utf-8")
    elif failure_class == "legacy_schema_v1_manifest":
        manifest.write_text(
            json.dumps({**valid_manifest, "schema_version": 1}),
            encoding="utf-8",
        )
    elif failure_class == "unexpected_manifest_fields":
        manifest.write_text(
            json.dumps({**valid_manifest, "unexpected": True}),
            encoding="utf-8",
        )
    elif failure_class == "content_digest_mismatch":
        manifest.write_text(
            json.dumps({**valid_manifest, "artifact_digest": "0" * 64}),
            encoding="utf-8",
        )
    elif failure_class == "non_regular_file_manifest":
        manifest.mkdir()
    else:  # pragma: no cover - parametrization is the closed caller
        raise AssertionError(f"unknown failure class: {failure_class}")
    return candidate, manifest


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


@pytest.mark.parametrize(
    "failure_class",
    [
        "missing_manifest",
        "malformed_json_manifest",
        "legacy_schema_v1_manifest",
        "unexpected_manifest_fields",
        "content_digest_mismatch",
        "non_regular_file_manifest",
    ],
)
def test_invalid_projection_classes_converge_after_one_prune(
    tmp_path: Path,
    failure_class: str,
) -> None:
    home = managed_home_for(tmp_path)
    root = home.autoskillit_dir / "plugin-projections"
    candidate, manifest = _seed_invalid_projection(root, failure_class)
    target = (
        "src/autoskillit/workspace/_projection_cache.py",
        "prune_stale_projections",
    )
    run_adapter, observe_adapter = RECLAIMER_CONVERGENCE_CASES[target]

    def run() -> object:
        return projection_cache.prune_stale_projections(
            root,
            home=home,
            active_key=_ACTIVE_KEY,
        )

    def observe() -> object:
        return _filesystem_snapshot(root)

    first_result, second_result, first_logs, second_logs = assert_second_pass_is_quiet(
        lambda: run_adapter(run),
        observe=lambda: observe_adapter(observe),
    )

    assert (first_result, second_result) == (0, 0)
    reconciled = [
        entry
        for entry in first_logs
        if entry.get("event") == "projected_plugin_reconcile"
        and entry.get("path") == str(candidate)
    ]
    assert [entry.get("disposition") for entry in reconciled] == ["reconciled"]
    assert not any(
        entry.get("event") == "projected_plugin_prune_validation_failed" for entry in first_logs
    )
    assert not candidate.exists()
    assert not manifest.exists()
    assert not projection_cache.residue_staging_path(candidate).exists()
    assert not any(
        entry.get("event") == "projected_plugin_prune_validation_failed" for entry in second_logs
    )
    assert _warning_or_error_events(second_logs) == []


def test_contended_invalid_projection_reconciles_after_lease_release(tmp_path: Path) -> None:
    home = managed_home_for(tmp_path)
    root = home.autoskillit_dir / "plugin-projections"
    candidate, manifest = _seed_invalid_projection(root, "malformed_json_manifest")
    lease_path = projection_cache.projected_artifact_lease_path(candidate)

    with ArtifactLease.acquire_exclusive(lease_path, blocking=False):
        with capture_logs() as contended_logs:
            assert (
                projection_cache.prune_stale_projections(
                    root,
                    home=home,
                    active_key=_ACTIVE_KEY,
                )
                == 0
            )

    assert candidate.is_dir()
    assert manifest.is_file()
    assert any(
        entry.get("path") == str(candidate) and entry.get("disposition") == "deferred_contended"
        for entry in contended_logs
    )

    with capture_logs() as reconciled_logs:
        assert (
            projection_cache.prune_stale_projections(
                root,
                home=home,
                active_key=_ACTIVE_KEY,
            )
            == 0
        )

    assert any(
        entry.get("path") == str(candidate) and entry.get("disposition") == "reconciled"
        for entry in reconciled_logs
    )
    assert not candidate.exists()
    assert not manifest.exists()


@pytest.mark.parametrize("scenario", ["outside_managed_home", "symlink_escape"])
def test_prune_rejects_candidates_outside_managed_home_without_mutation(
    tmp_path: Path,
    scenario: str,
) -> None:
    home_root = tmp_path / "home"
    home_root.mkdir()
    home = managed_home_for(home_root)
    outside = tmp_path / "outside"
    outside.mkdir()

    if scenario == "outside_managed_home":
        root = outside / "plugin-projections"
        candidate, manifest = _seed_invalid_projection(root, "malformed_json_manifest")
        before = _filesystem_snapshot(outside)
        with capture_logs() as logs:
            assert (
                projection_cache.prune_stale_projections(
                    root,
                    home=home,
                    active_key=_ACTIVE_KEY,
                )
                == 0
            )
        assert _filesystem_snapshot(outside) == before
        assert candidate.is_dir()
        assert manifest.is_file()
        assert any(
            entry.get("path") == str(root) and entry.get("disposition") == "deferred_io_error"
            for entry in logs
        )
        return

    root = home.autoskillit_dir / "plugin-projections"
    root.mkdir(parents=True)
    sentinel = outside / "sentinel.txt"
    sentinel.write_text("do not mutate\n", encoding="utf-8")
    candidate = root / _STALE_KEY
    candidate.symlink_to(sentinel)

    with capture_logs() as logs:
        assert (
            projection_cache.prune_stale_projections(
                root,
                home=home,
                active_key=_ACTIVE_KEY,
            )
            == 0
        )

    after_first = _filesystem_snapshot(root)
    with capture_logs() as second_logs:
        assert (
            projection_cache.prune_stale_projections(
                root,
                home=home,
                active_key=_ACTIVE_KEY,
            )
            == 0
        )

    assert sentinel.read_text(encoding="utf-8") == "do not mutate\n"
    assert candidate.is_symlink()
    assert not projection_cache.residue_staging_path(candidate).exists()
    assert any(
        entry.get("path") == str(candidate) and entry.get("disposition") == "deferred_unmanaged"
        for entry in logs
    )
    assert _warning_or_error_events(second_logs) == []
    assert _filesystem_snapshot(root) == after_first


@pytest.mark.parametrize(
    "failure_seam",
    ["lease", "install_lock", "rename", "rmtree"],
)
def test_quarantine_io_failures_leave_recoverable_shape_and_converge(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_seam: str,
) -> None:
    home = managed_home_for(tmp_path)
    root = home.autoskillit_dir / "plugin-projections"
    candidate, _manifest = _seed_invalid_projection(root, "malformed_json_manifest")
    residue = projection_cache.residue_staging_path(candidate)
    active = root / _ACTIVE_KEY
    active.mkdir()
    active_marker = active / "launch-marker"
    active_marker.write_text("launchable\n", encoding="utf-8")
    attempts = 0

    if failure_seam == "lease":
        real_acquire = projection_cache.ArtifactLease.acquire_exclusive

        def acquire_once(*args: object, **kwargs: object) -> object:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise OSError("injected lease failure")
            return real_acquire(*args, **kwargs)

        monkeypatch.setattr(projection_cache.ArtifactLease, "acquire_exclusive", acquire_once)
    elif failure_seam == "install_lock":
        real_install_lock = projection_cache._InstallLock

        class FailOnceInstallLock:
            def __init__(self, lock_home: object) -> None:
                self._delegate = real_install_lock(lock_home)

            def __enter__(self) -> object:
                nonlocal attempts
                attempts += 1
                if attempts == 1:
                    raise OSError("injected install-lock failure")
                return self._delegate.__enter__()

            def __exit__(self, *args: object) -> object:
                return self._delegate.__exit__(*args)

        monkeypatch.setattr(projection_cache, "_InstallLock", FailOnceInstallLock)
    elif failure_seam == "rename":
        real_rename = artifact_residue.os.rename

        def rename_once(src: Path, dst: Path) -> None:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise OSError("injected rename failure")
            real_rename(src, dst)

        monkeypatch.setattr(artifact_residue.os, "rename", rename_once)
    else:
        real_rmtree = artifact_residue.shutil.rmtree

        def rmtree_once(path: Path) -> None:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise OSError("injected rmtree failure")
            real_rmtree(path)

        monkeypatch.setattr(artifact_residue.shutil, "rmtree", rmtree_once)

    with capture_logs() as first_logs:
        assert (
            projection_cache.prune_stale_projections(
                root,
                home=home,
                active_key=_ACTIVE_KEY,
            )
            == 0
        )

    assert any(entry.get("disposition") == "deferred_io_error" for entry in first_logs)
    assert candidate.exists() or residue.exists()
    assert active_marker.read_text(encoding="utf-8") == "launchable\n"

    with capture_logs() as second_logs:
        assert (
            projection_cache.prune_stale_projections(
                root,
                home=home,
                active_key=_ACTIVE_KEY,
            )
            == 0
        )

    assert not candidate.exists()
    assert not residue.exists()
    assert active_marker.read_text(encoding="utf-8") == "launchable\n"
    assert _warning_or_error_events(second_logs) == []


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
