"""Tests for fleet doctor checks."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from autoskillit.cli.doctor._doctor_fleet import _check_fleet_state_schema
from autoskillit.fleet import FLEET_STATE_SCHEMA_VERSION

pytestmark = [pytest.mark.layer("cli"), pytest.mark.small]


class TestCheckFleetStateSchema:
    def test_check_fleet_state_schema_ok_on_current_version(self, tmp_path: Path) -> None:
        dispatches_dir = tmp_path / "dispatches"
        dispatches_dir.mkdir()
        state_file = dispatches_dir / "campaign_abc.json"
        state_file.write_text(
            json.dumps(
                {
                    "schema_version": FLEET_STATE_SCHEMA_VERSION,
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

    def test_check_fleet_state_schema_accepts_prior_supported_version(
        self, tmp_path: Path
    ) -> None:
        dispatches_dir = tmp_path / "dispatches"
        dispatches_dir.mkdir()
        (dispatches_dir / "campaign_prior.json").write_text(
            json.dumps(
                {
                    "schema_version": FLEET_STATE_SCHEMA_VERSION - 1,
                    "campaign_id": "prior",
                    "dispatches": [],
                }
            ),
            encoding="utf-8",
        )

        assert _check_fleet_state_schema(dispatches_dir).severity.name == "OK"

    def test_check_fleet_state_schema_ok_on_empty_dir(self, tmp_path: Path) -> None:
        dispatches_dir = tmp_path / "dispatches"
        dispatches_dir.mkdir()
        result = _check_fleet_state_schema(dispatches_dir)
        assert result.severity.name == "OK"
