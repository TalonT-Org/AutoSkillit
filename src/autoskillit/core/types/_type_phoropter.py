"""Frozen value objects for phoropter output parsing.

This module is IL-0 and stdlib-only.  The phoropter family captures the
selected lenses and reading tokens that drive recipe-step lint output, and
its members are consumed by tests asserting on the recipe-binding projection.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = [
    "PhoropterPrescription",
    "READING_TOKEN_PATTERN",
    "ReadingToken",
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
