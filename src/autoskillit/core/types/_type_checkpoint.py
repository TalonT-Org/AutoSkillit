"""Session checkpoint for resume progress tracking.

Zero autoskillit imports outside this sub-package. IL-0 type contract.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime


@dataclass(frozen=True)
class SessionCheckpoint:
    completed_items: list[str] = field(default_factory=list)
    step_name: str = ""
    progress_pct: float = 0.0
    ts: str = ""

    def to_dict(self) -> dict:  # type: ignore[type-arg]
        return {
            "completed_items": list(self.completed_items),
            "step_name": self.step_name,
            "progress_pct": self.progress_pct,
            "ts": self.ts,
        }

    @classmethod
    def from_dict(cls, data: dict) -> SessionCheckpoint:  # type: ignore[type-arg]
        return cls(
            completed_items=list(data.get("completed_items", [])),
            step_name=str(data.get("step_name", "")),
            progress_pct=float(data.get("progress_pct", 0.0)),
            ts=str(data.get("ts", "")),
        )

    @classmethod
    def now(
        cls, completed_items: list[str], step_name: str = "", progress_pct: float = 0.0
    ) -> SessionCheckpoint:
        return cls(
            completed_items=completed_items,
            step_name=step_name,
            progress_pct=progress_pct,
            ts=datetime.now(tz=UTC).isoformat(),
        )


def compute_remaining(original_items: Sequence[str], checkpoint: SessionCheckpoint) -> list[str]:
    completed = set(checkpoint.completed_items)
    return [item for item in original_items if item not in completed]


__all__ = [
    "SessionCheckpoint",
    "compute_remaining",
]
