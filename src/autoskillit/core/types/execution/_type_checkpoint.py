"""Session checkpoint for resume progress tracking.

Zero autoskillit imports outside this sub-package. IL-0 type contract.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


@dataclass(frozen=True, slots=True)
class SessionCheckpoint:
    """Resumable progress snapshot written at each completed pipeline item."""

    completed_items: list[str] = field(default_factory=list)
    step_name: str = ""
    progress_pct: float = 0.0
    ts: str = ""
    backend_name: str = ""
    skill_name: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "completed_items": list(self.completed_items),
            "step_name": self.step_name,
            "progress_pct": self.progress_pct,
            "ts": self.ts,
            "backend_name": self.backend_name,
            "skill_name": self.skill_name,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SessionCheckpoint:
        return cls(
            completed_items=list(data.get("completed_items", [])),
            step_name=str(data.get("step_name", "")),
            progress_pct=float(data.get("progress_pct", 0.0)),
            ts=str(data.get("ts", "")),
            backend_name=str(data.get("backend_name", "")),
            skill_name=str(data.get("skill_name", "")),
        )

    @classmethod
    def now(
        cls,
        completed_items: list[str],
        step_name: str = "",
        progress_pct: float = 0.0,
        backend_name: str = "",
        skill_name: str = "",
    ) -> SessionCheckpoint:
        return cls(
            completed_items=completed_items,
            step_name=step_name,
            progress_pct=progress_pct,
            ts=datetime.now(tz=UTC).isoformat(),
            backend_name=backend_name,
            skill_name=skill_name,
        )


def compute_remaining(original_items: Sequence[str], checkpoint: SessionCheckpoint) -> list[str]:
    completed = set(checkpoint.completed_items)
    return [item for item in original_items if item not in completed]


__all__ = [
    "SessionCheckpoint",
    "compute_remaining",
]
