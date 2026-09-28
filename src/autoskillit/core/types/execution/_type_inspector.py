"""Health Inspector types. Zero autoskillit imports outside this sub-package."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

__all__ = [
    "InspectorCallback",
    "InspectorEvidence",
    "InspectorVerdict",
]


@dataclass(frozen=True, slots=True)
class InspectorEvidence:
    idle_seconds: float
    stdout_path: str
    jsonl_lines: tuple[str, ...]
    cpu_trend: tuple[float, ...] = ()
    rss_trend: tuple[float, ...] = ()
    connection_summary: str = ""
    execution_marker_present: bool = False
    dispatch_context: str = ""


@dataclass(frozen=True, slots=True)
class InspectorVerdict:
    action: str
    reasoning: str
    confidence: str
    elapsed_seconds: float


InspectorCallback = Callable[[InspectorEvidence], Awaitable[InspectorVerdict]]
