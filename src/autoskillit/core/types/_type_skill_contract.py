"""Backend-neutral skill source identity contracts."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ._type_enums import SkillSource

__all__ = ["SkillSourceRef"]


@dataclass(frozen=True, slots=True)
class SkillSourceRef:
    """Exact logical source selected for a skill machine contract."""

    origin: SkillSource
    logical_name: str
    skill_path: Path
    search_dir: str | None = None
    precedence: int | None = None
