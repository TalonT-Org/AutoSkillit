"""Tests for core/_plugin_cache.py — retiring cache, kitchen registry, and schema version
validation."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from autoskillit.core._plugin_cache import (
    any_kitchen_open,
    append_retiring_entry,
    clear_kitchens_for_pid,
    register_active_kitchen,
    sweep_retiring_cache,
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


# ---------------------------------------------------------------------------
# Schema version validation — stale retiring cache
# ---------------------------------------------------------------------------


class TestRetiringCacheSchemaValidation:
    def setup_method(self):
        from autoskillit.core.io import _reset_schema_drift_logged_for_tests

        _reset_schema_drift_logged_for_tests()

    def test_append_retiring_entry_reads_ignore_stale_version(self, monkeypatch, tmp_path):
        """Stale retiring_cache.json (schema_version=99) must be treated as empty.

        append_retiring_entry reads the existing cache before writing.
        When the file has a stale schema version, it must be discarded and only
        the new entry must be written.
        """
        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
        cache = tmp_path / ".autoskillit" / "retiring_cache.json"
        _make_retiring_file(
            cache, schema_version=99, retiring=[{"version": "old", "path": "/old/path"}]
        )

        # Intercept filesystem writes so we can inspect what gets persisted
        written: list[str] = []

        def spy_write(path, data, schema_version):
            from autoskillit.core.io import write_versioned_json as real

            real(path, data, schema_version)
            written.append(json.loads(Path(path).read_text()))

        monkeypatch.setattr("autoskillit.core._plugin_cache.write_versioned_json", spy_write)

        append_retiring_entry("1.0", "/new/path")

        # The stale entries must not appear in any write
        for w in written:
            versioned_entries = w.get("retiring", [])
            stale = [e for e in versioned_entries if e["version"] == "old"]
            assert len(stale) == 0, "Stale entries must not be carried forward"
        # Only the new entry should be present
        last = written[-1]
        assert last["retiring"] == [
            {
                "version": "1.0",
                "path": "/new/path",
                "retired_at": last["retiring"][0]["retired_at"],
            }
        ]

    def test_sweep_retiring_cache_ignores_stale_version(self, tmp_path):
        """sweep_retiring_cache must treat a stale retiring_cache.json as empty.

        When the file has schema_version != _SCHEMA_VERSION, the function must
        return 0 (nothing to sweep) rather than crashing or sweeping stale data.
        """
        cache = tmp_path / ".autoskillit" / "retiring_cache.json"
        _make_retiring_file(
            cache,
            schema_version=99,
            retiring=[
                {
                    "version": "0.9",
                    "path": str(tmp_path / "old"),
                    "retired_at": "2020-01-01T00:00:00+00:00",
                }
            ],
        )
        # Create the directory so shutil.rmtree doesn't crash
        (tmp_path / "old").mkdir()

        result = sweep_retiring_cache(grace_hours=0)

        assert result == 0, "Must return 0 when file has stale schema version"

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

    def test_any_kitchen_open_returns_false_on_stale_version(self, tmp_path):
        """any_kitchen_open must return False when active_kitchens.json has stale schema version.

        Even if the stale file contains kitchen entries, the stale version causes
        it to be treated as absent, so the function returns False.
        """
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
