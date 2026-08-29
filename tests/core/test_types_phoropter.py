"""Authoritative phoropter type tests — frozen, exported, accessible."""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from autoskillit.core import (
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


def test_all_guard() -> None:
    # SynthesisStrategy is intentionally absent: defined in _type_enums, not _type_phoropter.
    # It reaches autoskillit.core via a separate re-export path
    # (verified by test_importable_from_gateway).
    from autoskillit.core.types._type_phoropter import __all__ as phoropter_all

    assert set(phoropter_all) == {
        "PhoropterPrescription",
        "ReadingToken",
        "READING_TOKEN_PATTERN",
    }


def test_importable_from_gateway() -> None:
    import autoskillit.core as core

    for name in (
        "PhoropterPrescription",
        "ReadingToken",
        "READING_TOKEN_PATTERN",
        "SynthesisStrategy",
    ):
        assert hasattr(core, name), f"{name} not importable from autoskillit.core"
