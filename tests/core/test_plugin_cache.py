"""Tests for core/_plugin_cache.py — retiring cache, kitchen registry, and schema version
validation."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from autoskillit.core import (
    PluginArtifactKind,
    RetiringArtifactRecord,
    RetiringCacheState,
)
from autoskillit.core._plugin_cache import (
    any_kitchen_open,
    append_retiring_record,
    clear_kitchens_for_pid,
    migrate_retiring_cache_v1,
    read_retiring_cache,
    register_active_kitchen,
    unregister_active_kitchen,
)

pytestmark = [pytest.mark.layer("core"), pytest.mark.small]


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

    def test_register_active_kitchen_ignores_stale_version(self, monkeypatch, tmp_path):
        """Stale active_kitchens.json must be overwritten with fresh data.

        register_active_kitchen reads the existing file before writing.
        When the file has a stale schema version, it must be treated as empty
        and the newly registered kitchen must be the only entry.
        """
        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
        kitchens_path = tmp_path / ".autoskillit" / "active_kitchens.json"
        _make_kitchen_file(
            kitchens_path,
            schema_version=99,
            kitchens=[
                {
                    "kitchen_id": "old-kitchen",
                    "pid": 99999,
                    "create_time": None,
                    "project_path": "/old",
                }
            ],
        )

        written: list[str] = []

        def spy_write(path, data, schema_version):
            from autoskillit.core.io import write_versioned_json as real

            real(path, data, schema_version)
            written.append(json.loads(Path(path).read_text()))

        monkeypatch.setattr("autoskillit.core._plugin_cache.write_versioned_json", spy_write)

        register_active_kitchen("new-kitchen", 12345, "/new")

        last = written[-1]
        # Stale kitchen must not be present
        ids = [k["kitchen_id"] for k in last.get("kitchens", [])]
        assert "old-kitchen" not in ids
        # New kitchen must be present
        assert "new-kitchen" in ids

    def test_any_kitchen_open_returns_false_on_stale_version(self, monkeypatch, tmp_path):
        """any_kitchen_open must return False when active_kitchens.json has stale schema version.

        Even if the stale file contains kitchen entries, the stale version causes
        it to be treated as absent, so the function returns False.
        """
        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
        kitchens_path = tmp_path / ".autoskillit" / "active_kitchens.json"
        _make_kitchen_file(
            kitchens_path,
            schema_version=99,
            kitchens=[
                {
                    "kitchen_id": "stale-kitchen",
                    "pid": 99999,
                    "create_time": None,
                    "project_path": "/x",
                }
            ],
        )

        result = any_kitchen_open()

        assert result is False, "Must return False for stale schema version"


class TestActiveKitchensSchemaValidation:
    def setup_method(self):
        from autoskillit.core.io import _reset_schema_drift_logged_for_tests

        _reset_schema_drift_logged_for_tests()

    def test_unregister_active_kitchen_ignores_stale_version(self, monkeypatch, tmp_path):
        """Stale active_kitchens.json must be treated as empty during unregister."""
        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
        kitchens_path = tmp_path / ".autoskillit" / "active_kitchens.json"
        _make_kitchen_file(
            kitchens_path,
            schema_version=99,
            kitchens=[
                {
                    "kitchen_id": "stale-kitchen",
                    "pid": 99999,
                    "create_time": None,
                    "project_path": "/x",
                }
            ],
        )

        written: list[str] = []

        def spy_write(path, data, schema_version):
            from autoskillit.core.io import write_versioned_json as real

            real(path, data, schema_version)
            written.append(json.loads(Path(path).read_text()))

        monkeypatch.setattr("autoskillit.core._plugin_cache.write_versioned_json", spy_write)

        unregister_active_kitchen("stale-kitchen")

        # Must not crash; the stale file is treated as empty so nothing is written
        last = written[-1]
        assert last["kitchens"] == []

    def test_clear_kitchens_for_pid_ignores_stale_version(self, monkeypatch, tmp_path):
        """Stale active_kitchens.json must be treated as empty during clear_kitchens_for_pid."""
        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
        kitchens_path = tmp_path / ".autoskillit" / "active_kitchens.json"
        _make_kitchen_file(
            kitchens_path,
            schema_version=99,
            kitchens=[
                {
                    "kitchen_id": "some-kitchen",
                    "pid": 12345,
                    "create_time": None,
                    "project_path": "/x",
                }
            ],
        )

        written: list[str] = []

        def spy_write(path, data, schema_version):
            from autoskillit.core.io import write_versioned_json as real

            real(path, data, schema_version)
            written.append(json.loads(Path(path).read_text()))

        monkeypatch.setattr("autoskillit.core._plugin_cache.write_versioned_json", spy_write)

        clear_kitchens_for_pid(12345)

        last = written[-1]
        assert last["kitchens"] == []
