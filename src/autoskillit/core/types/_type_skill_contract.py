"""Backend-neutral skill source identity contracts."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ._type_constants_registries import SKILL_CAPABILITY_REGISTRY
from ._type_enums import SkillSource

__all__ = ["SkillSourceRef", "derive_backend_requirements"]


def derive_backend_requirements(uses_capabilities: frozenset[str]) -> frozenset[str]:
    """Return the backend-name constraints implied by a capability set."""
    known = uses_capabilities & SKILL_CAPABILITY_REGISTRY.keys()
    return frozenset().union(
        *(SKILL_CAPABILITY_REGISTRY[capability].required_backends for capability in known)
    )


@dataclass(frozen=True, slots=True)
class SkillSourceRef:
    """Exact logical source selected for a skill machine contract."""

    origin: SkillSource
    logical_name: str
    skill_path: Path
    search_dir: str | None = None
    precedence: int | None = None
