"""T2-P1-A2-WP1: Phoropter family and phase type definitions."""

from __future__ import annotations

import re

import pytest

from autoskillit.core.types._type_enums import SynthesisStrategy
from autoskillit.core.types._type_phoropter import (
    READING_TOKEN_PATTERN,
    CrossDomainAssessment,
    CrossDomainPrescription,
    PhoropterPhaseSkip,
    PhoropterPrescription,
    ReadingToken,
)
from autoskillit.core.types._type_phoropter import (
    __all__ as phoropter_all,
)

pytestmark = [pytest.mark.layer("core"), pytest.mark.small]


def test_all_exports_complete() -> None:
    assert set(phoropter_all) == {
        "PhoropterPrescription",
        "ReadingToken",
        "READING_TOKEN_PATTERN",
        "PhoropterPhaseSkip",
        "CrossDomainPrescription",
        "CrossDomainAssessment",
    }


class TestPhoropterPrescription:
    def test_frozen(self) -> None:
        obj = PhoropterPrescription(selected_lenses="a", lens_context_paths="b")
        with pytest.raises(AttributeError):
            obj.selected_lenses = "x"  # type: ignore[misc]

    def test_failure_mode_default(self) -> None:
        obj = PhoropterPrescription(selected_lenses="a", lens_context_paths="b")
        assert obj.failure_mode == "continue"


class TestReadingToken:
    def test_frozen(self) -> None:
        obj = ReadingToken(output_prefix="p", path_value="/v")
        with pytest.raises(AttributeError):
            obj.output_prefix = "x"  # type: ignore[misc]


class TestReadingTokenPattern:
    def test_matches_absolute_path(self) -> None:
        m = re.match(READING_TOKEN_PATTERN, "diagram_path = /absolute/path/file.svg")
        assert m is not None
        assert m.group("prefix") == "diagram_path"
        assert m.group("path") == "/absolute/path/file.svg"

    def test_rejects_relative_path(self) -> None:
        m = re.match(READING_TOKEN_PATTERN, "key = relative/path")
        assert m is None


class TestPhoropterPhaseSkip:
    def test_frozen(self) -> None:
        obj = PhoropterPhaseSkip(skip_field="f", skip_semantics="skip_when_true")
        with pytest.raises(AttributeError):
            obj.skip_field = "x"  # type: ignore[misc]

    def test_applies_to_default(self) -> None:
        obj = PhoropterPhaseSkip(skip_field="f", skip_semantics="skip_when_false")
        assert obj.applies_to == ""


class TestCrossDomainPrescription:
    def test_frozen(self) -> None:
        obj = CrossDomainPrescription(family_names=("a", "b"))
        with pytest.raises(AttributeError):
            obj.family_names = ("x",)  # type: ignore[misc]

    def test_defaults(self) -> None:
        obj = CrossDomainPrescription(family_names=("a",))
        assert obj.merged_lenses == ""
        assert obj.merge_strategy == "union"


class TestCrossDomainAssessment:
    def test_frozen(self) -> None:
        obj = CrossDomainAssessment(family_names=("a",))
        with pytest.raises(AttributeError):
            obj.family_names = ("x",)  # type: ignore[misc]

    def test_defaults(self) -> None:
        obj = CrossDomainAssessment(family_names=("a",))
        assert obj.synthesis_strategy is SynthesisStrategy.NULL
        assert obj.combined_output == ""


def test_all_dataclasses_have_slots() -> None:
    for cls in (
        PhoropterPrescription,
        ReadingToken,
        PhoropterPhaseSkip,
        CrossDomainPrescription,
        CrossDomainAssessment,
    ):
        assert "__slots__" in vars(cls), f"{cls.__name__} missing __slots__"
