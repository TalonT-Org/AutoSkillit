"""The two-axis runtime session shape and reusable admission scopes."""

from __future__ import annotations

import os
from collections.abc import Iterable
from dataclasses import dataclass
from math import isfinite
from typing import Final, Literal

from ._type_constants_env import HEADLESS_ENV_VAR
from ._type_enums import SessionType
from ._type_helpers import session_type

__all__ = [
    "ALL_SESSION_SHAPES",
    "SESSION_SCOPE_ANY",
    "SessionScope",
    "SessionShape",
    "FleetSessionEnv",
    "hookdef_session_scope",
    "session_shape",
    "select_child_session_deadline",
]


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
    """Environment spec for an interactive fleet session launch."""

    session_type: str
    fleet_mode: str
    project_dir: str
    headless: str = "0"
    campaign_id: str = ""
    campaign_state_path: str = ""
    continue_on_failure: str = "false"

    def __post_init__(self) -> None:
        try:
            SessionType(self.session_type)
        except ValueError:
            valid = ", ".join(member.value for member in SessionType)
            raise ValueError(
                f"FleetSessionEnv.session_type must be a valid SessionType member, "
                f"got {self.session_type!r}. Valid values: {valid}"
            ) from None

    def to_dict(self) -> dict[str, str]:
        values = {
            "AUTOSKILLIT_SESSION_TYPE": self.session_type,
            "AUTOSKILLIT_FLEET_MODE": self.fleet_mode,
            "AUTOSKILLIT_PROJECT_DIR": self.project_dir,
            "AUTOSKILLIT_HEADLESS": self.headless,
        }
        if self.campaign_id:
            values["AUTOSKILLIT_CAMPAIGN_ID"] = self.campaign_id
            if self.campaign_state_path:
                values["AUTOSKILLIT_CAMPAIGN_STATE_PATH"] = self.campaign_state_path
            values["AUTOSKILLIT_CONTINUE_ON_FAILURE"] = self.continue_on_failure
        return values


@dataclass(frozen=True, slots=True)
class SessionShape:
    """The headless flag and orchestration tier of the current session."""

    headless: bool
    tier: SessionType

    @property
    def interactive(self) -> bool:
        """Whether this is an interactive session."""
        return not self.headless

    @property
    def label(self) -> str:
        """Return the stable human-readable form used in refusal messages."""
        return f"{'headless' if self.headless else 'interactive'}/{self.tier.value}"


ALL_SESSION_SHAPES: Final = frozenset(
    SessionShape(headless=headless, tier=tier)
    for headless in (False, True)
    for tier in SessionType
)


@dataclass(frozen=True, slots=True)
class SessionScope:
    """An explicitly admitted subset of all runtime session shapes."""

    admitted: frozenset[SessionShape]

    def admits(self, shape: SessionShape) -> bool:
        """Return whether ``shape`` is admitted by this scope."""
        return shape in self.admitted

    def __or__(self, other: object) -> SessionScope:
        """Return the union of two admission scopes."""
        if not isinstance(other, SessionScope):
            return NotImplemented
        return SessionScope(self.admitted | other.admitted)

    @classmethod
    def of(
        cls,
        *,
        headless: Literal["any", "headless_only", "interactive_only"] = "any",
        tiers: Iterable[SessionType] = (),
        exempt_tiers: Iterable[SessionType] = (),
    ) -> SessionScope:
        """Build a scope from the admitted axes and explicitly exempted tiers."""
        if headless not in {"any", "headless_only", "interactive_only"}:
            msg = f"Unknown session scope: {headless!r}"
            raise ValueError(msg)
        tier_set = frozenset(tiers)
        exempt_tier_set = frozenset(exempt_tiers)
        return cls(
            frozenset(
                shape
                for shape in ALL_SESSION_SHAPES
                if (headless == "any" or shape.headless == (headless == "headless_only"))
                and (not tier_set or shape.tier in tier_set)
                and shape.tier not in exempt_tier_set
            )
        )


SESSION_SCOPE_ANY: Final = SessionScope(ALL_SESSION_SHAPES)


def session_shape() -> SessionShape:
    """Resolve the current shape through the canonical tier parser."""
    return SessionShape(
        headless=os.environ.get(HEADLESS_ENV_VAR) == "1",
        tier=session_type(),
    )


def hookdef_session_scope(
    session_scope: str, exempt_session_types: frozenset[str]
) -> SessionScope:
    """Convert the two HookDef admission fields into their core scope."""
    return SessionScope.of(
        headless=session_scope,  # type: ignore[arg-type]
        exempt_tiers=(SessionType(value) for value in exempt_session_types),
    )
