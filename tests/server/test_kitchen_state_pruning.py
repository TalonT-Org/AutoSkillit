"""Prune-safety unit tests for prune_stale_kitchen_state."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from autoskillit.server.tools.tools_kitchen import prune_stale_kitchen_state

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


def _write_registry(monkeypatch, tmp_path, entries):
    from autoskillit.core._plugin_cache import write_versioned_json

    registry_path = tmp_path / "active_kitchens.json"
    monkeypatch.setattr(
        "autoskillit.core._plugin_cache._active_kitchens_path",
        lambda: registry_path,
    )
    monkeypatch.setattr(
        "autoskillit.core._plugin_cache._active_kitchens_lock",
        lambda: tmp_path / "active_kitchens.lock",
    )
    write_versioned_json(registry_path, {"kitchens": entries}, schema_version=1)
    return registry_path


def test_malformed_tracker_json_reaped(monkeypatch, tmp_path):
    """A tracker file containing invalid JSON must be deleted, not raise."""
    tracker_dir = tmp_path / ".autoskillit" / "temp" / "pipeline_tracker"
    tracker_dir.mkdir(parents=True)
    bad_file = tracker_dir / "K1.json"
    bad_file.write_text("{not valid json")

    _write_registry(monkeypatch, tmp_path, [])

    prune_stale_kitchen_state(tmp_path, "K2")

    assert not bad_file.exists()


def test_tracker_missing_kitchen_id_treated_as_orphan(monkeypatch, tmp_path):
    """A tracker with no kitchen_id field is an orphan; past grace window it is reaped."""
    tracker_dir = tmp_path / ".autoskillit" / "temp" / "pipeline_tracker"
    tracker_dir.mkdir(parents=True)
    tracker_data = {
        "steps": {},
        "initialized_at": "2020-01-01T00:00:00+00:00",
    }
    tracker_file = tracker_dir / "K1.json"
    tracker_file.write_text(json.dumps(tracker_data))

    _write_registry(monkeypatch, tmp_path, [])

    prune_stale_kitchen_state(tmp_path, "K2")

    assert not tracker_file.exists()


def test_pruner_does_not_raise(monkeypatch, tmp_path):
    """Registry read failures must be swallowed — pruning is best-effort."""
    tracker_dir = tmp_path / ".autoskillit" / "temp" / "pipeline_tracker"
    tracker_dir.mkdir(parents=True)
    (tracker_dir / "K1.json").write_text(
        json.dumps(
            {
                "kitchen_id": "K1",
                "initialized_at": datetime.now(UTC).isoformat(),
                "steps": {},
            }
        )
    )

    def _raise():
        raise OSError("boom")

    monkeypatch.setattr("autoskillit.core._plugin_cache._active_kitchens_path", _raise)

    prune_stale_kitchen_state(tmp_path, "K2")
