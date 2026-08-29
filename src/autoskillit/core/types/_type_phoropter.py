"""Phoropter family and phase types. Zero autoskillit imports."""

from __future__ import annotations

from dataclasses import dataclass

__all__ = [
    "PhoropterPrescription",
    "ReadingToken",
    "READING_TOKEN_PATTERN",
]


@dataclass(frozen=True, slots=True)
class PhoropterPrescription:
    selected_lenses: str
    lens_context_paths: str
    failure_mode: str = "continue"


@dataclass(frozen=True, slots=True)
class ReadingToken:
    output_prefix: str
    path_value: str


READING_TOKEN_PATTERN: str = r"^(?P<prefix>\w+) = (?P<path>/.+)$"
