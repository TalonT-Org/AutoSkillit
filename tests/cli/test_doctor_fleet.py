"""Tests for fleet doctor checks."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from autoskillit.cli.doctor._doctor_fleet import _check_fleet_state_schema
from autoskillit.fleet.state_types import _SCHEMA_VERSION

pytestmark = [pytest.mark.layer("cli"), pytest.mark.small]


class TestCheckFleetStateSchema:
    def test_check_fleet_state_schema_ok_on_current_version(self, tmp_path: Path) -> None:
        dispatches_dir = tmp_path / "dispatches"
        dispatches_dir.mkdir()
        state_file = dispatches_dir / "campaign_abc.json"
        state_file.write_text(
            json.dumps(
                {
                    "schema_version": _SCHEMA_VERSION,
                    "campaign_id": "abc",
                    "campaign_name": "test",
                    "manifest_path": "/m.yaml",
                    "started_at": 0.0,
                    "dispatches": [],
                }
            ),
            encoding="utf-8",
        )
        result = _check_fleet_state_schema(dispatches_dir)
        assert result.severity.name == "OK"

    def test_check_fleet_state_schema_warns_on_stale_version(self, tmp_path: Path) -> None:
        dispatches_dir = tmp_path / "dispatches"
        dispatches_dir.mkdir()
        state_file = dispatches_dir / "campaign_stale.json"
        state_file.write_text(
            json.dumps(
                {
                    "schema_version": 2,
                    "campaign_id": "stale",
                    "campaign_name": "test",
                    "manifest_path": "/m.yaml",
                    "started_at": 0.0,
                    "dispatches": [],
                }
            ),
            encoding="utf-8",
        )
        result = _check_fleet_state_schema(dispatches_dir)
        assert result.severity.name == "WARNING"
        assert "drift" in result.message.lower()
        assert "stale" in result.message

    def test_check_fleet_state_schema_ok_on_empty_dir(self, tmp_path: Path) -> None:
        dispatches_dir = tmp_path / "dispatches"
        dispatches_dir.mkdir()
        result = _check_fleet_state_schema(dispatches_dir)
        assert result.severity.name == "OK"
