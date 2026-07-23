"""Backend-neutral skill source identity contracts."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ._type_constants_registries import SKILL_CAPABILITY_REGISTRY
from ._type_enums import SkillSource

__all__ = [
    "SKILL_PROJECTION_VERSION",
    "SkillSourceIdentity",
    "SkillSourceRef",
    "derive_backend_requirements",
]


SKILL_PROJECTION_VERSION = 1


def derive_backend_requirements(uses_capabilities: frozenset[str]) -> frozenset[str]:
    """Return the backend-name constraints implied by a capability set."""
    known = uses_capabilities & SKILL_CAPABILITY_REGISTRY.keys()
    return frozenset().union(
        *(SKILL_CAPABILITY_REGISTRY[capability].required_backends for capability in known)
    )


@dataclass(frozen=True, slots=True)
class SkillSourceIdentity:
    """Path-free logical identity safe to carry beyond source resolution."""

    origin: SkillSource
    logical_name: str
    search_dir: str | None = None
    precedence: int | None = None


@dataclass(frozen=True, slots=True)
class SkillSourceRef:
    """Private source reference selected for a skill machine contract."""

    origin: SkillSource
    logical_name: str
    skill_path: Path
    search_dir: str | None = None
    precedence: int | None = None

    @property
    def identity(self) -> SkillSourceIdentity:
        """Return the path-free identity used by catalogs and projections."""
        return SkillSourceIdentity(
            origin=self.origin,
            logical_name=self.logical_name,
            search_dir=self.search_dir,
            precedence=self.precedence,
        )
