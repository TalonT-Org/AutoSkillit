"""Projection publication and process-lifetime ownership are one contract."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from autoskillit.core import (
    ArtifactLease,
    ArtifactLeaseContention,
    PluginArtifactContentionError,
    PluginArtifactValidationError,
    PluginLoadMode,
)
from autoskillit.execution.backends.claude import ClaudeCodeBackend
from autoskillit.workspace import (
    ProjectedPluginArtifactAuthority,
    project_default_plugin_authority,
)
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
            manifest["incarnation_id"] = "00000000-0000-0000-0000-000000000000"
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
    [PluginLoadMode.GENERATED_HOME, PluginLoadMode.NONE],
)
def test_authority_rejects_non_artifact_modes(
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
