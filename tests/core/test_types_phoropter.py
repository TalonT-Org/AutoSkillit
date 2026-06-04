"""Authoritative phoropter type tests — frozen, exported, accessible."""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from autoskillit.core import (
    CrossDomainAssessment,
    CrossDomainPrescription,
    PhoropterPhaseSkip,
    PhoropterPrescription,
    SynthesisStrategy,
)

pytestmark = [pytest.mark.layer("core"), pytest.mark.small]


class TestPhoropterPrescription:
    def test_frozen_rejects_mutation(self) -> None:
        obj = PhoropterPrescription(selected_lenses="a", lens_context_paths="b")
        with pytest.raises(FrozenInstanceError):
            obj.selected_lenses = "x"  # type: ignore[misc]

    def test_failure_mode_default_is_continue(self) -> None:
        obj = PhoropterPrescription(selected_lenses="a", lens_context_paths="b")
        assert obj.failure_mode == "continue"


class TestSynthesisStrategy:
    def test_exhaustive_members(self) -> None:
        assert set(SynthesisStrategy) == {
            SynthesisStrategy.NULL,
            SynthesisStrategy.PRIORITY_HIERARCHY,
            SynthesisStrategy.ELECTRE_III,
            SynthesisStrategy.DEX,
            SynthesisStrategy.CUSTOM,
        }

    def test_str_enum_equality(self) -> None:
        assert SynthesisStrategy.NULL == "null"


class TestPhoropterPhaseSkip:
    def test_skip_when_true_accepted(self) -> None:
        obj = PhoropterPhaseSkip(skip_field="ctx.x", skip_semantics="skip_when_true")
        assert obj.skip_semantics == "skip_when_true"

    def test_skip_when_false_accepted(self) -> None:
        obj = PhoropterPhaseSkip(skip_field="ctx.x", skip_semantics="skip_when_false")
        assert obj.skip_semantics == "skip_when_false"

    def test_frozen(self) -> None:
        obj = PhoropterPhaseSkip(skip_field="f", skip_semantics="skip_when_true")
        with pytest.raises(FrozenInstanceError):
            obj.skip_field = "x"  # type: ignore[misc]

    def test_applies_to_defaults_to_empty_string(self) -> None:
        obj = PhoropterPhaseSkip(skip_field="context.x", skip_semantics="skip_when_true")
        assert obj.applies_to == ""


class TestCrossdomainStubs:
    def test_cross_domain_prescription_instantiable(self) -> None:
        obj = CrossDomainPrescription(family_names=("a", "b"))
        assert obj.family_names == ("a", "b")

    def test_cross_domain_assessment_instantiable(self) -> None:
        obj = CrossDomainAssessment(family_names=("a",))
        assert obj.family_names == ("a",)

    def test_cross_domain_prescription_frozen(self) -> None:
        obj = CrossDomainPrescription(family_names=("a",))
        with pytest.raises(FrozenInstanceError):
            obj.family_names = ("x",)  # type: ignore[misc]

    def test_cross_domain_assessment_frozen(self) -> None:
        obj = CrossDomainAssessment(family_names=("a",))
        with pytest.raises(FrozenInstanceError):
            obj.family_names = ("x",)  # type: ignore[misc]


def test_all_guard() -> None:
    from autoskillit.core.types._type_phoropter import __all__ as phoropter_all

    assert set(phoropter_all) == {
        "PhoropterPrescription",
        "ReadingToken",
        "READING_TOKEN_PATTERN",
        "PhoropterPhaseSkip",
        "CrossDomainPrescription",
        "CrossDomainAssessment",
    }


def test_importable_from_gateway() -> None:
    import autoskillit.core as core

    for name in (
        "PhoropterPrescription",
        "ReadingToken",
        "READING_TOKEN_PATTERN",
        "PhoropterPhaseSkip",
        "CrossDomainPrescription",
        "CrossDomainAssessment",
        "SynthesisStrategy",
    ):
        assert hasattr(core, name), f"{name} not importable from autoskillit.core"
