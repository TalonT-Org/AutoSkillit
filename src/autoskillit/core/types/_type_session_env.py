"""Typed env specs for session launch boundaries."""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite

__all__ = ["FleetSessionEnv", "select_child_session_deadline"]


def select_child_session_deadline(local_deadline: float, inherited_deadline: str) -> str:
    """Select an inherited positive deadline or the caller's local deadline."""
    try:
        inherited_value = float(inherited_deadline)
        if inherited_deadline and isfinite(inherited_value) and inherited_value > 0:
            return inherited_deadline
    except ValueError:
        pass
    return str(int(local_deadline))


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

    def __post_init__(self) -> None:
        from ._type_enums import SessionType

        try:
            SessionType(self.session_type)
        except ValueError:
            valid = ", ".join(m.value for m in SessionType)
            raise ValueError(
                f"FleetSessionEnv.session_type must be a valid SessionType member, "
                f"got {self.session_type!r}. Valid values: {valid}"
            ) from None

    def to_dict(self) -> dict[str, str]:
        d = {
            "AUTOSKILLIT_SESSION_TYPE": self.session_type,
            "AUTOSKILLIT_FLEET_MODE": self.fleet_mode,
            "AUTOSKILLIT_PROJECT_DIR": self.project_dir,
            "AUTOSKILLIT_HEADLESS": self.headless,
        }
        # Campaign fields (campaign_id, campaign_state_path, continue_on_failure) are
        # only emitted when campaign_id is set — non-campaign sessions have no campaign context.
        if self.campaign_id:
            d["AUTOSKILLIT_CAMPAIGN_ID"] = self.campaign_id
            if self.campaign_state_path:
                d["AUTOSKILLIT_CAMPAIGN_STATE_PATH"] = self.campaign_state_path
            d["AUTOSKILLIT_CONTINUE_ON_FAILURE"] = self.continue_on_failure
        return d
