"""Tests for core/_plugin_cache.py — retiring cache, kitchen registry, and schema version
validation."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from autoskillit.core import (
    PluginArtifactIdentity,
    PluginArtifactKind,
    PluginArtifactRetirementEngine,
    RetiringArtifactRecord,
    RetiringCacheState,
    due_retiring_records,
)
from autoskillit.core._plugin_cache import (
    KitchenProcessIdentity,
    any_kitchen_open,
    append_retiring_record,
    migrate_retiring_cache_v1,
    read_retiring_cache,
    register_active_kitchen,
    sample_kitchen_process_identity,
    unregister_active_kitchen,
)

pytestmark = [pytest.mark.layer("core"), pytest.mark.small]


def test_kitchen_identity_samples_process_incarnation_once(monkeypatch, tmp_path: Path) -> None:
    calls: list[int] = []

    class Process:
        def __init__(self, pid: int) -> None:
            calls.append(pid)

        def create_time(self) -> float:
            return 123.5

    monkeypatch.setattr("autoskillit.core._plugin_cache.psutil.Process", Process)

    identity = sample_kitchen_process_identity("kitchen", 42, tmp_path)

    assert identity == KitchenProcessIdentity("kitchen", 42, 123.5, str(tmp_path.resolve()))
    assert calls == [42]


def test_repeated_exact_registration_does_not_grow_registry(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    identity = KitchenProcessIdentity("kitchen", 42, 123.5, str(tmp_path))

    register_active_kitchen(identity)
    register_active_kitchen(identity)

    registry = json.loads((tmp_path / ".autoskillit" / "active_kitchens.json").read_text())
    assert len(registry["kitchens"]) == 1


def test_registration_migrates_valid_v1_active_kitchens(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    registry_path = tmp_path / ".autoskillit" / "active_kitchens.json"
    legacy_entry = {
        "kitchen_id": "legacy",
        "pid": 42,
        "create_time": 123.5,
        "project_path": str(tmp_path),
        "opened_at": "2026-08-09T00:00:00+00:00",
    }
    _make_kitchen_file(registry_path, schema_version=1, kitchens=[legacy_entry])

    register_active_kitchen(KitchenProcessIdentity("current", 43, 124.5, str(tmp_path)))

    migrated = json.loads(registry_path.read_text(encoding="utf-8"))
    assert migrated["schema_version"] == 2
    assert [entry["kitchen_id"] for entry in migrated["kitchens"]] == ["legacy", "current"]


def test_scoped_kitchen_lookup_canonicalizes_project_path(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    project = tmp_path / "project"
    project.mkdir()
    project_link = tmp_path / "project-link"
    project_link.symlink_to(project, target_is_directory=True)
    register_active_kitchen(KitchenProcessIdentity("kitchen", 42, 123.5, str(project)))
    monkeypatch.setattr("autoskillit.core._plugin_cache.kitchen_entry_alive", lambda _entry: True)

    assert any_kitchen_open(str(project_link)) is True


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_kitchen_file(path: Path, schema_version: int, kitchens: list[dict]) -> None:
    """Write a valid active_kitchens.json file with the given schema version."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"schema_version": schema_version, "kitchens": kitchens}))


def _make_retiring_file(path: Path, schema_version: int, retiring: list[dict]) -> None:
    """Write a valid retiring_cache.json file with the given schema version."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"schema_version": schema_version, "retiring": retiring}))


def _retiring_record(tmp_path: Path) -> RetiringArtifactRecord:
    retired_at = datetime.now(UTC)
    return RetiringArtifactRecord(
        record_id="record-1",
        artifact_kind=PluginArtifactKind.INSTALLED_PLUGIN,
        semantic_key="plugin:1",
        managed_path=(tmp_path / "managed" / "1").absolute(),
        manifest_path=(tmp_path / "managed" / ".1.manifest.json").absolute(),
        incarnation_id="00000000000040008000000000000001",
        manifest_schema_version=1,
        artifact_digest="a" * 64,
        retired_at=retired_at,
        not_before=retired_at + timedelta(hours=6),
    )


@pytest.mark.parametrize(
    ("field", "invalid"),
    [
        ("record_id", True),
        ("artifact_kind", "installed_plugin"),
        ("semantic_key", True),
        ("manifest_schema_version", True),
        ("manifest_schema_version", 1.5),
        ("schema_version", 2.0),
    ],
)
def test_retiring_record_requires_exact_authority_types(
    tmp_path: Path,
    field: str,
    invalid: object,
) -> None:
    with pytest.raises(ValueError):
        replace(_retiring_record(tmp_path), **{field: invalid})


def test_retirement_deduplication_ignores_regenerated_deadline(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    original = _retiring_record(tmp_path)
    repeated_scan = replace(
        original,
        record_id="record-2",
        retired_at=original.retired_at + timedelta(minutes=5),
        not_before=original.not_before + timedelta(minutes=5),
    )

    assert append_retiring_record(original).created is True
    repeated_result = append_retiring_record(repeated_scan)

    assert repeated_result.created is False
    assert repeated_result.record_id == original.record_id
    assert read_retiring_cache().records == (original,)


@pytest.mark.parametrize(
    ("state_name", "cache_bytes"),
    [
        ("corrupt", b"{not-json"),
        ("unsupported_future", b'{"schema_version":99,"records":[]}'),
        ("legacy_v1", b'{"schema_version":1,"retiring":[]}'),
    ],
)
def test_unsafe_retirement_state_is_not_collapsed_to_empty(
    state_name: str,
    cache_bytes: bytes,
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    cache = tmp_path / ".autoskillit" / "retiring_cache.json"
    cache.parent.mkdir(parents=True)
    cache.write_bytes(cache_bytes)
    record = _retiring_record(tmp_path)
    identity = PluginArtifactIdentity(
        semantic_key=record.semantic_key,
        managed_path=record.managed_path,
        manifest_path=record.manifest_path,
        incarnation_id=record.incarnation_id,
        manifest_schema_version=record.manifest_schema_version,
        artifact_digest=record.artifact_digest,
    )
    engine = PluginArtifactRetirementEngine(
        managed_root=tmp_path / "managed",
        artifact_kind=record.artifact_kind,
        manifest_path=lambda path: path.parent / f".{path.name}.manifest.json",
        lease_path=lambda path: path.parent / f".{path.name}.lease",
        current_identity=lambda _record: identity,
        logger=None,
    )

    with pytest.raises(RuntimeError, match=state_name):
        due_retiring_records(datetime.now(UTC))
    with pytest.raises(RuntimeError, match=state_name):
        engine.cancel_obsolete_retirements(identity)
    assert cache.read_bytes() == cache_bytes


# ---------------------------------------------------------------------------
# Schema version validation — stale retiring cache
# ---------------------------------------------------------------------------


class TestRetiringCacheSchemaValidation:
    def setup_method(self):
        from autoskillit.core.io import _reset_schema_drift_logged_for_tests

        _reset_schema_drift_logged_for_tests()

    def test_future_schema_is_preserved_and_mutation_fails_closed(
        self,
        monkeypatch,
        tmp_path,
    ):
        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
        cache = tmp_path / ".autoskillit" / "retiring_cache.json"
        _make_retiring_file(
            cache, schema_version=99, retiring=[{"version": "old", "path": "/old/path"}]
        )
        before = cache.read_bytes()

        result = read_retiring_cache()

        assert result.state is RetiringCacheState.UNSUPPORTED_FUTURE
        with pytest.raises(RuntimeError, match="unsupported_future"):
            append_retiring_record(_retiring_record(tmp_path))
        assert cache.read_bytes() == before

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("record_id", 123),
            ("managed_path", ["/managed/1"]),
            ("manifest_schema_version", True),
            ("schema_version", float("inf")),
            ("incarnation_id", "not-canonical"),
            ("artifact_digest", "z" * 64),
        ],
    )
    def test_v2_record_fields_require_exact_json_types(
        self,
        monkeypatch,
        tmp_path,
        field,
        value,
    ) -> None:
        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
        append_retiring_record(_retiring_record(tmp_path))
        cache = tmp_path / ".autoskillit" / "retiring_cache.json"
        payload = json.loads(cache.read_text())
        payload["records"][0][field] = value
        cache.write_text(json.dumps(payload))

        result = read_retiring_cache()

        assert result.state is RetiringCacheState.CORRUPT
        assert result.records == ()

    def test_v2_record_rejects_unexpected_fields(
        self,
        monkeypatch,
        tmp_path,
    ) -> None:
        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
        append_retiring_record(_retiring_record(tmp_path))
        cache = tmp_path / ".autoskillit" / "retiring_cache.json"
        payload = json.loads(cache.read_text())
        payload["records"][0]["unexpected_authority"] = "accepted"
        cache.write_text(json.dumps(payload))

        result = read_retiring_cache()

        assert result.state is RetiringCacheState.CORRUPT
        assert result.records == ()

    def test_v2_cache_rejects_duplicate_record_ids(
        self,
        monkeypatch,
        tmp_path,
    ) -> None:
        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
        append_retiring_record(_retiring_record(tmp_path))
        cache = tmp_path / ".autoskillit" / "retiring_cache.json"
        payload = json.loads(cache.read_text())
        payload["records"].append(dict(payload["records"][0]))
        cache.write_text(json.dumps(payload))
        before = cache.read_bytes()

        result = read_retiring_cache()

        assert result.state is RetiringCacheState.CORRUPT
        assert result.records == ()
        assert cache.read_bytes() == before

    @pytest.mark.parametrize(
        "content",
        [
            "{not-json",
            json.dumps(
                {
                    "schema_version": 2,
                    "records": {},
                    "legacy_evidence": [],
                }
            ),
            json.dumps(
                {
                    "schema_version": 2,
                    "records": [],
                    "legacy_evidence": {},
                }
            ),
            json.dumps({"schema_version": 2}),
            json.dumps(
                {
                    "schema_version": 2,
                    "records": [],
                    "legacy_evidence": [],
                    "unexpected_authority": True,
                }
            ),
        ],
    )
    def test_malformed_v2_cache_is_corrupt_and_preserved(
        self,
        monkeypatch,
        tmp_path,
        content,
    ) -> None:
        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
        cache = tmp_path / ".autoskillit" / "retiring_cache.json"
        cache.parent.mkdir(parents=True)
        cache.write_text(content)
        before = cache.read_bytes()

        result = read_retiring_cache()

        assert result.state is RetiringCacheState.CORRUPT
        assert result.records == ()
        assert result.legacy_evidence == ()
        assert cache.read_bytes() == before

    def test_boolean_root_schema_is_corrupt_not_legacy(
        self,
        monkeypatch,
        tmp_path,
    ) -> None:
        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
        cache = tmp_path / ".autoskillit" / "retiring_cache.json"
        _make_retiring_file(cache, schema_version=True, retiring=[])

        result = read_retiring_cache()

        assert result.state is RetiringCacheState.CORRUPT

    def test_legacy_evidence_fields_are_not_coerced(
        self,
        monkeypatch,
        tmp_path,
    ) -> None:
        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
        cache = tmp_path / ".autoskillit" / "retiring_cache.json"
        cache.parent.mkdir(parents=True)
        cache.write_text(
            json.dumps(
                {
                    "schema_version": 2,
                    "records": [],
                    "legacy_evidence": [
                        {
                            "record_id": 123,
                            "version": "0.9",
                            "path": "/managed/0.9",
                            "retired_at": "2020-01-01T00:00:00+00:00",
                            "recognized_kind": None,
                            "rejection_reason": "legacy path",
                        }
                    ],
                }
            )
        )

        result = read_retiring_cache()

        assert result.state is RetiringCacheState.CORRUPT

    def test_explicit_v1_migration_preserves_active_registry(
        self,
        monkeypatch,
        tmp_path,
    ):
        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
        cache = tmp_path / ".autoskillit" / "retiring_cache.json"
        _make_retiring_file(
            cache,
            schema_version=1,
            retiring=[
                {
                    "version": "0.9",
                    "path": str(tmp_path / "managed" / "0.9"),
                    "retired_at": "2020-01-01T00:00:00+00:00",
                }
            ],
        )
        kitchens = tmp_path / ".autoskillit" / "active_kitchens.json"
        _make_kitchen_file(kitchens, schema_version=1, kitchens=[])
        kitchens_before = kitchens.read_bytes()

        result = migrate_retiring_cache_v1(
            {PluginArtifactKind.INSTALLED_PLUGIN: tmp_path / "managed"}
        )

        assert result.state is RetiringCacheState.EXACT_V2
        assert len(result.legacy_evidence) == 1
        assert result.legacy_evidence[0].recognized_kind is PluginArtifactKind.INSTALLED_PLUGIN
        assert result.records == ()
        assert kitchens.read_bytes() == kitchens_before

    def test_register_active_kitchen_preserves_unsafe_registry(self, monkeypatch, tmp_path):
        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
        kitchens_path = tmp_path / ".autoskillit" / "active_kitchens.json"
        kitchens_path.parent.mkdir(parents=True)
        before = b'{"schema_version":99,"kitchens":[]}'
        kitchens_path.write_bytes(before)

        with pytest.raises(ValueError, match="unsupported"):
            register_active_kitchen(
                KitchenProcessIdentity("new-kitchen", 12345, 1.0, str(tmp_path))
            )

        assert kitchens_path.read_bytes() == before

    def test_any_kitchen_open_fails_safe_on_unsafe_registry(self, monkeypatch, tmp_path):
        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
        kitchens_path = tmp_path / ".autoskillit" / "active_kitchens.json"
        kitchens_path.parent.mkdir(parents=True)
        before = b"{not-json"
        kitchens_path.write_bytes(before)

        result = any_kitchen_open()

        assert result is True
        assert kitchens_path.read_bytes() == before


class TestActiveKitchensSchemaValidation:
    def setup_method(self):
        from autoskillit.core.io import _reset_schema_drift_logged_for_tests

        _reset_schema_drift_logged_for_tests()

    def test_unregister_active_kitchen_preserves_unsafe_registry(self, monkeypatch, tmp_path):
        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
        kitchens_path = tmp_path / ".autoskillit" / "active_kitchens.json"
        kitchens_path.parent.mkdir(parents=True)
        before = b'{"schema_version":99,"kitchens":[]}'
        kitchens_path.write_bytes(before)

        with pytest.raises(ValueError, match="unsupported"):
            unregister_active_kitchen(
                KitchenProcessIdentity("stale-kitchen", 12345, 1.0, str(tmp_path))
            )

        assert kitchens_path.read_bytes() == before
