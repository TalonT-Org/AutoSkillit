"""Prune-safety unit tests for prune_stale_kitchen_state."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest

from autoskillit.server.tools.tools_kitchen import prune_stale_kitchen_state
from tests.server._helpers import _write_registry

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


def test_malformed_tracker_json_is_preserved(monkeypatch, tmp_path):
    """Unreadable authority cannot be retired as though it were stale."""
    tracker_dir = tmp_path / ".autoskillit" / "temp" / "pipeline_tracker"
    tracker_dir.mkdir(parents=True)
    bad_file = tracker_dir / "K1.json"
    bad_file.write_text("{not valid json")

    _write_registry(monkeypatch, tmp_path, [])

    prune_stale_kitchen_state(tmp_path, "K2")

    assert bad_file.read_text() == "{not valid json"


def test_wrong_shape_tracker_is_preserved(monkeypatch, tmp_path):
    """Wrong-shape authority remains available for explicit repair."""
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

    assert json.loads(tracker_file.read_text()) == tracker_data


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
                "dependencies": {},
            }
        )
    )

    def _raise():
        raise OSError("boom")

    monkeypatch.setattr("autoskillit.core.pipeline_tracker.read_active_kitchens_registry", _raise)

    prune_stale_kitchen_state(tmp_path, "K2")

    assert (tracker_dir / "K1.json").exists()


def test_invalid_tracker_candidate_is_logged(monkeypatch, tmp_path):
    tracker_dir = tmp_path / ".autoskillit" / "temp" / "pipeline_tracker"
    tracker_dir.mkdir(parents=True)
    invalid_tracker = tracker_dir / "invalid\\name.json"
    invalid_tracker.write_text("{}")
    mock_logger = MagicMock()
    monkeypatch.setattr("autoskillit.server.tools.tools_kitchen.logger", mock_logger)

    prune_stale_kitchen_state(tmp_path, "K2")

    mock_logger.warning.assert_called_once_with(
        "invalid_stale_tracker_candidate",
        path=str(invalid_tracker),
        error="target_order_id must be path-safe",
    )
