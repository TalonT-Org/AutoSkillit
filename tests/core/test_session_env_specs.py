"""Unit tests for typed session env specs (FleetSessionEnv)."""

from __future__ import annotations

import pytest

from autoskillit.core import FleetSessionEnv

pytestmark = [pytest.mark.layer("core"), pytest.mark.small]


def test_fleet_session_env_adhoc_to_dict() -> None:
    spec = FleetSessionEnv(
        session_type="fleet",
        fleet_mode="dispatch",
        project_dir="/tmp/my-project",
    )
    d = spec.to_dict()
    assert d["AUTOSKILLIT_SESSION_TYPE"] == "fleet"
    assert d["AUTOSKILLIT_FLEET_MODE"] == "dispatch"
    assert d["AUTOSKILLIT_PROJECT_DIR"] == "/tmp/my-project"
    assert d["AUTOSKILLIT_HEADLESS"] == "0"
    assert "AUTOSKILLIT_CAMPAIGN_ID" not in d
    assert "AUTOSKILLIT_CONTINUE_ON_FAILURE" not in d


def test_fleet_session_env_campaign_to_dict() -> None:
    spec = FleetSessionEnv(
        session_type="fleet",
        fleet_mode="campaign",
        project_dir="/tmp/my-project",
        campaign_id="camp-42",
        campaign_state_path="/tmp/camp/state.json",
        continue_on_failure="true",
    )
    d = spec.to_dict()
    assert d["AUTOSKILLIT_CAMPAIGN_ID"] == "camp-42"
    assert d["AUTOSKILLIT_CAMPAIGN_STATE_PATH"] == "/tmp/camp/state.json"
    assert d["AUTOSKILLIT_CONTINUE_ON_FAILURE"] == "true"


def test_fleet_session_env_missing_required_field_raises() -> None:
    with pytest.raises(TypeError):
        FleetSessionEnv(session_type="fleet", fleet_mode="dispatch")  # type: ignore[call-arg]


def test_fleet_session_env_to_dict_returns_dict_str_str() -> None:
    spec = FleetSessionEnv(
        session_type="fleet",
        fleet_mode="dispatch",
        project_dir="/tmp/test",
    )
    d = spec.to_dict()
    assert isinstance(d, dict)
    assert all(isinstance(k, str) and isinstance(v, str) for k, v in d.items())


def test_fleet_session_env_rejects_invalid_session_type() -> None:
    with pytest.raises(ValueError, match="SessionType"):
        FleetSessionEnv(
            session_type="bogus",
            fleet_mode="dispatch",
            project_dir="/tmp/test",
        )


def test_fleet_session_env_rejects_cli_display_label() -> None:
    with pytest.raises(ValueError, match="SessionType"):
        FleetSessionEnv(
            session_type="order",
            fleet_mode="dispatch",
            project_dir="/tmp/test",
        )
