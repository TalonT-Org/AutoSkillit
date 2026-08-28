"""Phoropter family and phase types. Zero autoskillit imports."""

from __future__ import annotations

from dataclasses import dataclass

from ._type_enums import SynthesisStrategy

__all__ = [
    "PhoropterPrescription",
    "ReadingToken",
    "READING_TOKEN_PATTERN",
    "CrossDomainPrescription",
    "CrossDomainAssessment",
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


@dataclass(frozen=True, slots=True)
class CrossDomainPrescription:
    family_names: tuple[str, ...]
    merged_lenses: str = ""
    merge_strategy: str = "union"


@dataclass(frozen=True, slots=True)
class CrossDomainAssessment:
    family_names: tuple[str, ...]
    synthesis_strategy: SynthesisStrategy = SynthesisStrategy.NULL
    combined_output: str = ""
