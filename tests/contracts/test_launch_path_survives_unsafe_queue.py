"""Real launch paths survive unsafe queues; quarantine preserves newer records."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import structlog

from autoskillit import cli
from autoskillit.core import (
    PluginArtifactIdentity,
    PluginLoadMode,
    RetiringCacheState,
    managed_home,
    new_plugin_artifact_incarnation_id,
    read_retiring_cache,
)
from autoskillit.execution.backends.claude import ClaudeCodeBackend
from autoskillit.workspace import (
    ProjectedPluginArtifactAuthority,
    ProjectedPluginRetirementOwner,
    project_default_plugin_authority,
)
from autoskillit.workspace._projection_cache import projected_artifact_manifest_path
from tests._helpers import _flush_structlog_proxy_caches
from tests.cli.test_installed_plugin_selector_integration import (
    _activate_production_selector,
    _install_cook_harness,
    _RecordingBackend,
)
from tests.contracts._projection_helpers import session_catalog
from tests.fixtures.plugin_artifact_state import (
    PluginArtifactStateKind,
    build_plugin_artifact_state,
)

pytestmark = pytest.mark.medium

_UNSAFE_CACHE_KINDS = (
    "corrupt",
    "unsupported_future",
    "legacy_v1",
    "unknown_artifact_kind",
)


def _authority(tmp_path: Path, *, projection_version: int = 1) -> ProjectedPluginArtifactAuthority:
    return project_default_plugin_authority(
        cwd=tmp_path,
        base_branch="main",
        catalog=session_catalog(),
        projection_version=projection_version,
    )


def _cache_path(home: Path) -> Path:
    return home / ".autoskillit" / "retiring_cache.json"


def _record_payload(
    identity: PluginArtifactIdentity,
    *,
    record_id: str,
    artifact_kind: str = "projection",
) -> dict[str, object]:
    now = datetime.now(UTC)
    return {
        "record_id": record_id,
        "artifact_kind": artifact_kind,
        "semantic_key": identity.semantic_key,
        "managed_path": str(identity.managed_path),
        "manifest_path": str(identity.manifest_path),
        "incarnation_id": identity.incarnation_id,
        "manifest_schema_version": identity.manifest_schema_version,
        "artifact_digest": identity.artifact_digest,
        "retired_at": now.isoformat(),
        "not_before": (now + timedelta(hours=1)).isoformat(),
        "schema_version": 2,
    }


def _v2_payload(
    *,
    records: list[dict[str, object]],
    legacy_evidence: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    return {
        "schema_version": 2,
        "records": records,
        "legacy_evidence": legacy_evidence or [],
    }


def _unsafe_cache_bytes(kind: str, identity: PluginArtifactIdentity) -> bytes:
    if kind == "corrupt":
        return b"{not-json"
    if kind == "unsupported_future":
        return json.dumps({"schema_version": 99, "records": []}).encode()
    if kind == "legacy_v1":
        return json.dumps({"schema_version": 1, "retiring": []}).encode()
    unknown = _record_payload(
        identity,
        record_id="unknown-record",
        artifact_kind="install_root_generation",
    )
    return json.dumps(_v2_payload(records=[unknown]), sort_keys=True).encode()


def _new_identity(root: Path, *, semantic_key: str) -> PluginArtifactIdentity:
    managed_path = (root / semantic_key).absolute()
    return PluginArtifactIdentity(
        semantic_key=semantic_key,
        managed_path=managed_path,
        manifest_path=projected_artifact_manifest_path(managed_path),
        incarnation_id=new_plugin_artifact_incarnation_id(),
        manifest_schema_version=2,
        artifact_digest="a" * 64,
    )


def _published_identity(authority: ProjectedPluginArtifactAuthority) -> PluginArtifactIdentity:
    binding = authority.acquire_launch_binding(
        backend=ClaudeCodeBackend(),
        load_mode=PluginLoadMode.EXPLICIT_PLUGIN_DIR,
    )
    try:
        return binding.identity
    finally:
        binding.close()


@pytest.mark.parametrize("cache_kind", _UNSAFE_CACHE_KINDS)
def test_acquire_launch_binding_survives_every_unsafe_queue_state(
    cache_kind: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    authority = _authority(tmp_path)
    identity = _published_identity(authority)
    cache = _cache_path(tmp_path)
    cache_bytes = _unsafe_cache_bytes(cache_kind, identity)
    cache.write_bytes(cache_bytes)

    binding = authority.acquire_launch_binding(
        backend=ClaudeCodeBackend(),
        load_mode=PluginLoadMode.EXPLICIT_PLUGIN_DIR,
    )
    try:
        assert binding.plugin_dir is not None
        assert binding.plugin_dir.is_dir()
        assert cache.read_bytes() == cache_bytes
    finally:
        binding.close()


@pytest.mark.parametrize("cache_kind", _UNSAFE_CACHE_KINDS)
def test_launch_binding_survives_unsafe_queue_in_prune_as_well_as_cancel(
    cache_kind: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    authority = _authority(tmp_path)
    identity = _published_identity(authority)
    _published_identity(_authority(tmp_path, projection_version=2))
    _cache_path(tmp_path).write_bytes(_unsafe_cache_bytes(cache_kind, identity))

    binding = authority.acquire_launch_binding(
        backend=ClaudeCodeBackend(),
        load_mode=PluginLoadMode.EXPLICIT_PLUGIN_DIR,
    )
    try:
        assert binding.plugin_dir is not None
        assert binding.plugin_dir.is_dir()
    finally:
        binding.close()


def test_launch_binding_survives_an_unreadable_stale_projection_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import autoskillit.workspace._projection_cache as projection_cache

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    authority = _authority(tmp_path)
    _published_identity(authority)
    stale = _published_identity(_authority(tmp_path, projection_version=2))
    real_read = projection_cache.read_versioned_json

    def fail_stale_manifest(path: Path, *args: object, **kwargs: object):
        if Path(path) == stale.manifest_path:
            raise OSError("injected stale sidecar failure")
        return real_read(path, *args, **kwargs)

    monkeypatch.setattr(projection_cache, "read_versioned_json", fail_stale_manifest)
    _flush_structlog_proxy_caches()
    try:
        with structlog.testing.capture_logs() as logs:
            binding = authority.acquire_launch_binding(
                backend=ClaudeCodeBackend(),
                load_mode=PluginLoadMode.EXPLICIT_PLUGIN_DIR,
            )
            binding.close()
    finally:
        _flush_structlog_proxy_caches()

    assert any(
        entry.get("event") == "projected_plugin_prune_identity_unavailable" for entry in logs
    )


def test_unknown_artifact_kind_does_not_condemn_the_sibling_records(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    identity = _published_identity(_authority(tmp_path))
    records = [_record_payload(identity, record_id=f"valid-{index}") for index in range(3)]
    records.append(
        _record_payload(
            identity,
            record_id="unknown",
            artifact_kind="install_root_generation",
        )
    )
    _cache_path(tmp_path).write_text(json.dumps(_v2_payload(records=records)))

    result = read_retiring_cache()

    assert result.state is RetiringCacheState.EXACT_V2
    assert len(result.records) == 3
    assert len(result.quarantined_records) == 1


def test_quarantined_records_round_trip_through_a_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    identity = _published_identity(_authority(tmp_path))
    unknown = _record_payload(
        identity,
        record_id="unknown",
        artifact_kind="install_root_generation",
    )
    _cache_path(tmp_path).write_text(json.dumps(_v2_payload(records=[unknown])))
    owner = ProjectedPluginRetirementOwner(
        identity.managed_path.parent,
        home=managed_home(),
    )

    appended = owner.enqueue_retirement(
        _new_identity(identity.managed_path.parent, semantic_key="new-projection"),
        datetime.now(UTC) + timedelta(hours=1),
    )

    assert appended is not None
    payload = json.loads(_cache_path(tmp_path).read_text())
    assert unknown in payload["records"]


def test_both_quarantine_buckets_survive_a_mutation_independently(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    identity = _published_identity(_authority(tmp_path))
    unknown_record = _record_payload(
        identity,
        record_id="unknown-record",
        artifact_kind="install_root_generation",
    )
    unknown_evidence = {
        "record_id": "unknown-evidence",
        "version": "1.0",
        "path": str(tmp_path / "legacy"),
        "retired_at": datetime.now(UTC).isoformat(),
        "recognized_kind": None,
        "rejection_reason": "legacy path",
        "newer_writer_field": True,
    }
    _cache_path(tmp_path).write_text(
        json.dumps(_v2_payload(records=[unknown_record], legacy_evidence=[unknown_evidence]))
    )
    owner = ProjectedPluginRetirementOwner(
        identity.managed_path.parent,
        home=managed_home(),
    )

    appended = owner.enqueue_retirement(
        _new_identity(identity.managed_path.parent, semantic_key="new-projection"),
        datetime.now(UTC) + timedelta(hours=1),
    )

    assert appended is not None
    payload = json.loads(_cache_path(tmp_path).read_text())
    assert unknown_record in payload["records"]
    assert unknown_evidence in payload["legacy_evidence"]


def test_quarantined_record_id_collision_condemns_the_cache(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    identity = _published_identity(_authority(tmp_path))
    valid = _record_payload(identity, record_id="collision")
    unknown = _record_payload(
        identity,
        record_id="collision",
        artifact_kind="install_root_generation",
    )
    _cache_path(tmp_path).write_text(json.dumps(_v2_payload(records=[valid, unknown])))

    assert read_retiring_cache().state is RetiringCacheState.CORRUPT


def test_cook_command_survives_a_corrupt_queue(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    state = build_plugin_artifact_state(
        tmp_path / "home",
        PluginArtifactStateKind.VALID_CURRENT,
    )
    _activate_production_selector(monkeypatch, state)
    project_dir = state.home / "project"
    backend = _RecordingBackend("claude-code")
    _install_cook_harness(monkeypatch, project_dir)
    monkeypatch.setattr(
        "autoskillit.core.bind_session_owner",
        lambda _project, _launch_id, _owner_pid: True,
    )
    cache = _cache_path(state.home)
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_bytes(b"{not-json")

    cli.cook(backend=backend)
    captured = capsys.readouterr()

    assert len(backend.build_calls) == 2
    assert '"event": "plugin_artifact_lifecycle"' in captured.err
    assert '"outcome": "deferred_unreadable_queue"' in captured.err
