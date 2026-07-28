"""Projection publication and process-lifetime ownership are one contract."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
import structlog

from autoskillit.core import (
    ArtifactLease,
    ArtifactLeaseContention,
    PluginArtifactContentionError,
    PluginArtifactValidationError,
    PluginLoadMode,
    RetirementOutcome,
    is_canonical_plugin_artifact_incarnation_id,
    new_plugin_artifact_incarnation_id,
    read_retiring_cache,
)
from autoskillit.execution.backends.claude import ClaudeCodeBackend
from autoskillit.workspace import (
    ProjectedPluginArtifactAuthority,
    project_default_plugin_authority,
    prune_stale_projections,
)
from tests._helpers import _flush_structlog_proxy_caches
from tests.contracts._projection_helpers import session_catalog

pytestmark = [pytest.mark.layer("contracts"), pytest.mark.medium]


def _authority(tmp_path: Path) -> ProjectedPluginArtifactAuthority:
    return project_default_plugin_authority(
        cwd=tmp_path,
        base_branch="main",
        catalog=session_catalog(),
    )


def test_authority_creation_is_lazy(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    authority = _authority(tmp_path)

    assert authority.catalog is not None
    assert not (tmp_path / ".autoskillit").exists()


@pytest.mark.parametrize("invalid", [True, 1.5, 0])
def test_authority_requires_exact_positive_projection_version(
    invalid: object,
) -> None:
    with pytest.raises(ValueError, match="positive integer"):
        project_default_plugin_authority(projection_version=invalid)  # type: ignore[arg-type]


def test_projection_publication_preserves_control_flow_exceptions(
    tmp_path: Path,
    monkeypatch,
) -> None:
    import autoskillit.workspace._projected_artifact.authority as projection

    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    def interrupt_publication(_plan):
        raise KeyboardInterrupt("stop projection publication")

    monkeypatch.setattr(
        projection,
        "_stage_projected_plugin_artifact",
        interrupt_publication,
    )

    with pytest.raises(KeyboardInterrupt, match="stop projection publication"):
        _authority(tmp_path).acquire_launch_binding(
            backend=ClaudeCodeBackend(),
            load_mode=PluginLoadMode.EXPLICIT_PLUGIN_DIR,
        )


def test_projection_staging_cleanup_preserves_primary_error(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from unittest.mock import Mock

    import autoskillit.workspace._projected_artifact.authority as projection

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    plan = _authority(tmp_path)._plan(ClaudeCodeBackend())
    plan.destination.parent.mkdir(parents=True)
    logger = Mock()
    monkeypatch.setattr(projection, "logger", logger)

    def fail_after_manifest_write(path: Path, *_args, **_kwargs) -> None:
        path.write_text("staged")
        raise RuntimeError("primary staging failure")

    original_unlink = Path.unlink

    def fail_staging_manifest_unlink(path: Path, *args, **kwargs) -> None:
        if ".manifest-" in path.name:
            raise OSError("cleanup unlink failure")
        original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(projection, "write_versioned_json", fail_after_manifest_write)
    monkeypatch.setattr(Path, "unlink", fail_staging_manifest_unlink)

    with pytest.raises(RuntimeError, match="primary staging failure"):
        projection._stage_projected_plugin_artifact(plan)

    logger.warning.assert_called_once()
    assert logger.warning.call_args.args == ("projected_plugin_staging_cleanup_failed",)
    assert logger.warning.call_args.kwargs["error"] == "cleanup unlink failure"
    assert ".manifest-" in Path(logger.warning.call_args.kwargs["manifest_path"]).name


def test_binding_owns_exact_v2_incarnation_and_stable_sidecar(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    authority = _authority(tmp_path)
    backend = ClaudeCodeBackend()

    first = authority.acquire_launch_binding(
        backend=backend,
        load_mode=PluginLoadMode.EXPLICIT_PLUGIN_DIR,
    )
    second = authority.acquire_launch_binding(
        backend=backend,
        load_mode=PluginLoadMode.PROJECTED_HOME,
    )
    try:
        assert first.identity == second.identity
        assert first.plugin_dir == first.identity.managed_path
        assert second.plugin_dir == second.identity.managed_path
        assert first.inherited_fds != second.inherited_fds
        assert first.identity.manifest_schema_version == 2
        manifest = json.loads(first.identity.manifest_path.read_text(encoding="utf-8"))
        assert manifest["semantic_key"] == first.identity.semantic_key
        assert manifest["incarnation_id"] == first.identity.incarnation_id
        assert manifest["artifact_digest"] == first.identity.artifact_digest
        assert is_canonical_plugin_artifact_incarnation_id(first.identity.incarnation_id)

        lease_path = (
            first.identity.managed_path.parent
            / ".artifact-leases"
            / f"{first.identity.semantic_key}.lock"
        )
        assert lease_path.is_file()
        assert first.identity.managed_path not in lease_path.parents
        with pytest.raises(ArtifactLeaseContention):
            ArtifactLease.acquire_exclusive(lease_path, blocking=False)
    finally:
        first.close()
        second.close()


def test_projection_lifecycle_events_cover_publication_and_binding(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    _flush_structlog_proxy_caches()
    try:
        with structlog.testing.capture_logs() as logs:
            binding = _authority(tmp_path).acquire_launch_binding(
                backend=ClaudeCodeBackend(),
                load_mode=PluginLoadMode.EXPLICIT_PLUGIN_DIR,
            )
            identity = binding.identity
            binding.close()
    finally:
        _flush_structlog_proxy_caches()

    lifecycle = [entry for entry in logs if entry.get("event") == "plugin_artifact_lifecycle"]
    assert [entry["action"] for entry in lifecycle] == [
        "publish",
        "acquire",
        "release",
    ]
    assert all(entry["outcome"] == "succeeded" for entry in lifecycle)
    assert all(entry["artifact_kind"] == "projection" for entry in lifecycle)
    assert all(entry["semantic_key"] == identity.semantic_key for entry in lifecycle)
    assert all(entry["incarnation"] == identity.incarnation_id for entry in lifecycle)


def test_projection_reclaim_io_failure_stays_queued_for_retry(
    tmp_path: Path,
    monkeypatch,
) -> None:
    import autoskillit.core._plugin_cache as plugin_cache
    from autoskillit.workspace import ProjectedPluginRetirementOwner

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    binding = _authority(tmp_path).acquire_launch_binding(
        backend=ClaudeCodeBackend(),
        load_mode=PluginLoadMode.EXPLICIT_PLUGIN_DIR,
    )
    identity = binding.identity
    binding.close()
    owner = ProjectedPluginRetirementOwner(identity.managed_path.parent)
    deadline = datetime.now(UTC)
    append_result = owner.enqueue_retirement(identity, deadline)
    record = read_retiring_cache().records[0]

    real_rmtree = plugin_cache.shutil.rmtree

    def fail_reclaim(_path):
        raise PermissionError("injected projection reclaim failure")

    monkeypatch.setattr(plugin_cache.shutil, "rmtree", fail_reclaim)

    assert owner.try_reclaim(record, deadline) is RetirementOutcome.DEFERRED_IO_ERROR
    assert append_result.record_id in {
        queued.record_id for queued in read_retiring_cache().records
    }
    monkeypatch.setattr(plugin_cache.shutil, "rmtree", real_rmtree)
    assert owner.try_reclaim(record, deadline) is RetirementOutcome.RECLAIMED
    assert append_result.record_id not in {
        queued.record_id for queued in read_retiring_cache().records
    }


def test_projection_reclaim_preserves_outcome_when_writer_close_fails(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    binding = _authority(tmp_path).acquire_launch_binding(
        backend=ClaudeCodeBackend(),
        load_mode=PluginLoadMode.EXPLICIT_PLUGIN_DIR,
    )
    identity = binding.identity
    binding.close()

    from autoskillit.workspace import ProjectedPluginRetirementOwner

    owner = ProjectedPluginRetirementOwner(identity.managed_path.parent)
    deadline = datetime.now(UTC)
    owner.enqueue_retirement(identity, deadline)
    record = read_retiring_cache().records[0]
    real_close = ArtifactLease.close

    def fail_after_close(lease: ArtifactLease) -> None:
        real_close(lease)
        raise OSError("injected retirement writer close failure")

    monkeypatch.setattr(ArtifactLease, "close", fail_after_close)

    assert owner.try_reclaim(record, deadline) is RetirementOutcome.RECLAIMED


def test_projection_prune_preserves_validation_skip_when_writer_close_fails(
    tmp_path: Path,
    monkeypatch,
) -> None:
    projections_root = tmp_path / "projections"
    (projections_root / "invalid-stale-projection").mkdir(parents=True)
    real_close = ArtifactLease.close

    def fail_after_close(lease: ArtifactLease) -> None:
        real_close(lease)
        raise OSError("injected prune writer close failure")

    monkeypatch.setattr(ArtifactLease, "close", fail_after_close)

    assert prune_stale_projections(projections_root, active_key="active") == 0


def test_corrupt_live_incarnation_is_not_replaced_until_reader_closes(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    authority = _authority(tmp_path)
    backend = ClaudeCodeBackend()
    binding = authority.acquire_launch_binding(
        backend=backend,
        load_mode=PluginLoadMode.EXPLICIT_PLUGIN_DIR,
    )
    old_identity = binding.identity
    probe = old_identity.managed_path / "recipes" / "_lifetime_probe.yaml"
    probe.write_text("corrupt: true\n", encoding="utf-8")
    manifest_bytes = old_identity.manifest_path.read_bytes()

    try:
        with pytest.raises(PluginArtifactContentionError):
            authority.acquire_launch_binding(
                backend=backend,
                load_mode=PluginLoadMode.EXPLICIT_PLUGIN_DIR,
            )
        assert probe.is_file()
        assert old_identity.manifest_path.read_bytes() == manifest_bytes
    finally:
        binding.close()

    with authority.acquire_launch_binding(
        backend=backend,
        load_mode=PluginLoadMode.EXPLICIT_PLUGIN_DIR,
    ) as replacement:
        assert replacement.identity.managed_path == old_identity.managed_path
        assert replacement.identity.incarnation_id != old_identity.incarnation_id
        assert not probe.exists()


def test_mode_only_mutation_invalidates_projection_identity(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    authority = _authority(tmp_path)
    backend = ClaudeCodeBackend()
    binding = authority.acquire_launch_binding(
        backend=backend,
        load_mode=PluginLoadMode.EXPLICIT_PLUGIN_DIR,
    )
    plugin_metadata = binding.identity.managed_path / ".claude-plugin" / "plugin.json"
    plugin_metadata.chmod(plugin_metadata.stat().st_mode ^ 0o100)

    try:
        with pytest.raises(PluginArtifactContentionError):
            authority.acquire_launch_binding(
                backend=backend,
                load_mode=PluginLoadMode.EXPLICIT_PLUGIN_DIR,
            )
    finally:
        binding.close()

    with authority.acquire_launch_binding(
        backend=backend,
        load_mode=PluginLoadMode.EXPLICIT_PLUGIN_DIR,
    ) as replacement:
        assert replacement.identity.incarnation_id != binding.identity.incarnation_id


def test_writer_to_reader_handoff_revalidates_exact_incarnation(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    authority = _authority(tmp_path)
    original_acquire = ArtifactLease.acquire_shared
    acquisitions = 0

    def acquire_shared(cls: type[ArtifactLease], lock_path: Path) -> ArtifactLease:
        nonlocal acquisitions
        del cls
        lease = original_acquire(lock_path)
        acquisitions += 1
        if acquisitions == 2:
            projections = lock_path.parent.parent
            semantic_key = lock_path.stem
            manifest_path = projections / f".{semantic_key}.autoskillit-projection.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["incarnation_id"] = new_plugin_artifact_incarnation_id()
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        return lease

    monkeypatch.setattr(ArtifactLease, "acquire_shared", classmethod(acquire_shared))

    with pytest.raises(PluginArtifactValidationError, match="incarnation changed"):
        authority.acquire_launch_binding(
            backend=ClaudeCodeBackend(),
            load_mode=PluginLoadMode.EXPLICIT_PLUGIN_DIR,
        )

    plan = authority._plan(ClaudeCodeBackend())
    with ArtifactLease.acquire_exclusive(plan.lease_path, blocking=False):
        pass


@pytest.mark.parametrize(
    "load_mode",
    [
        PluginLoadMode.GENERATED_HOME,
        PluginLoadMode.IMPLICIT_INSTALLED,
        PluginLoadMode.NONE,
    ],
)
def test_projected_authority_rejects_incompatible_load_modes(
    tmp_path: Path,
    monkeypatch,
    load_mode: PluginLoadMode,
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    authority = _authority(tmp_path)

    with pytest.raises(ValueError):
        authority.acquire_launch_binding(
            backend=ClaudeCodeBackend(),
            load_mode=load_mode,
        )
    assert not (tmp_path / ".autoskillit").exists()
