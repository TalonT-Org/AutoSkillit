"""The two-axis runtime session shape and reusable admission scopes."""

from __future__ import annotations

import os
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Final, Literal

from ._type_constants_env import HEADLESS_ENV_VAR
from ._type_enums import SessionType
from ._type_helpers import session_type

__all__ = [
    "ALL_SESSION_SHAPES",
    "SESSION_SCOPE_ANY",
    "SessionScope",
    "SessionShape",
    "hookdef_session_scope",
    "session_shape",
]


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
