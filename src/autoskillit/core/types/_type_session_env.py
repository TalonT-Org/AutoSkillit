"""Typed env specs for session launch boundaries."""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["FleetSessionEnv"]


@dataclass(frozen=True, slots=True)
class FleetSessionEnv:
    """Env spec for fleet interactive sessions launched via _launch_fleet_session."""

    session_type: str
    fleet_mode: str
    project_dir: str
    headless: str = "0"
    campaign_id: str = ""
    campaign_state_path: str = ""
    continue_on_failure: str = "false"

    def to_dict(self) -> dict[str, str]:
        d = {
            "AUTOSKILLIT_SESSION_TYPE": self.session_type,
            "AUTOSKILLIT_FLEET_MODE": self.fleet_mode,
            "AUTOSKILLIT_PROJECT_DIR": self.project_dir,
            "AUTOSKILLIT_HEADLESS": self.headless,
        }
        if self.campaign_id:
            d["AUTOSKILLIT_CAMPAIGN_ID"] = self.campaign_id
            d["AUTOSKILLIT_CAMPAIGN_STATE_PATH"] = self.campaign_state_path
            d["AUTOSKILLIT_CONTINUE_ON_FAILURE"] = self.continue_on_failure
        return d
