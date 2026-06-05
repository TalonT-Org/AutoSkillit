"""Tests for _type_tradition_manifest.py — tradition manifest frozen types."""

from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit.core.types._type_enums import SynthesisStrategy
from autoskillit.core.types._type_tradition_manifest import (
    DialingConfig,
    LensEntry,
    TraditionManifest,
)
from autoskillit.core.types._type_tradition_manifest import (
    __all__ as _module_all,
)

pytestmark = [pytest.mark.layer("core"), pytest.mark.small]

_MINIMAL_YAML = """\
name: test-tradition
description: A test tradition
output_type: diagram
step_count: 3
mode_label: Test Mode
context_file_schema: test_schema
default_enabled: true
failure_mode: continue
step_name_prefix: test
"""

_FULL_YAML = """\
name: full-tradition
description: Full tradition with all fields
output_type: assessment
step_count: 5
mode_label: Full Mode
context_file_schema: full_schema
default_enabled: false
failure_mode: abort
step_name_prefix: full
lenses:
  - slug: lens-a
    analytical_mode: structural
    primary_question: "How is it built?"
    tradition: arch
    codification_level: high
    diagram_direction: TB
  - slug: lens-b
    analytical_mode: behavioral
dialing:
  selection_strategy: coverage
  min_lenses: 2
  max_lenses: 5
  always_run:
    - lens-a
  synthesis_strategy: priority_hierarchy
phase_skip:
  skip_field: context.is_silent_type
  skip_semantics: skip_when_true
  applies_to: apply
"""

_REQUIRED_FIELDS = [
    "name",
    "description",
    "output_type",
    "step_count",
    "mode_label",
    "context_file_schema",
    "default_enabled",
    "failure_mode",
    "step_name_prefix",
]


class TestRoundTrip:
    def test_minimal_yaml_round_trip(self, tmp_path: Path) -> None:
        p = tmp_path / "tradition.yaml"
        p.write_text(_MINIMAL_YAML)
        m = TraditionManifest.from_yaml_path(p)
        assert m.name == "test-tradition"
        assert m.description == "A test tradition"
        assert m.output_type == "diagram"
        assert m.step_count == 3
        assert m.mode_label == "Test Mode"
        assert m.context_file_schema == "test_schema"
        assert m.default_enabled is True
        assert m.failure_mode == "continue"
        assert m.step_name_prefix == "test"
        assert m.phase_skip is None
        assert m.lenses == ()
        assert m.dialing == DialingConfig()

    def test_full_yaml_round_trip(self, tmp_path: Path) -> None:
        p = tmp_path / "tradition.yaml"
        p.write_text(_FULL_YAML)
        m = TraditionManifest.from_yaml_path(p)
        assert m.name == "full-tradition"
        assert m.step_count == 5
        assert m.default_enabled is False
        assert len(m.lenses) == 2
        assert m.lenses[0].slug == "lens-a"
        assert m.lenses[0].analytical_mode == "structural"
        assert m.lenses[1].slug == "lens-b"
        assert m.lenses[1].analytical_mode == "behavioral"
        assert m.lenses[1].primary_question == ""
        assert m.dialing.selection_strategy == "coverage"
        assert m.dialing.min_lenses == 2
        assert m.dialing.max_lenses == 5
        assert m.dialing.always_run == ("lens-a",)
        assert m.dialing.synthesis_strategy == SynthesisStrategy.PRIORITY_HIERARCHY
        assert m.phase_skip is not None
        assert m.phase_skip.skip_field == "context.is_silent_type"
        assert m.phase_skip.skip_semantics == "skip_when_true"
        assert m.phase_skip.applies_to == "apply"


class TestFrozen:
    def test_lens_entry_frozen(self) -> None:
        le = LensEntry(slug="a")
        with pytest.raises(AttributeError):
            le.slug = "b"  # type: ignore[misc]

    def test_dialing_config_frozen(self) -> None:
        dc = DialingConfig()
        with pytest.raises(AttributeError):
            dc.min_lenses = 99  # type: ignore[misc]

    def test_tradition_manifest_frozen(self) -> None:
        m = TraditionManifest(
            name="x",
            description="x",
            output_type="x",
            step_count=1,
            mode_label="x",
            context_file_schema="x",
            default_enabled=True,
            failure_mode="x",
            step_name_prefix="x",
        )
        with pytest.raises(AttributeError):
            m.name = "y"  # type: ignore[misc]


class TestDefaults:
    def test_lens_entry_defaults(self) -> None:
        le = LensEntry()
        assert le.slug == ""
        assert le.analytical_mode == ""
        assert le.primary_question == ""
        assert le.tradition == ""
        assert le.codification_level == ""
        assert le.diagram_direction == ""

    def test_dialing_config_defaults(self) -> None:
        dc = DialingConfig()
        assert dc.selection_strategy == ""
        assert dc.min_lenses == 1
        assert dc.max_lenses == 1
        assert dc.always_run == ()
        assert dc.synthesis_strategy == SynthesisStrategy.NULL


class TestMissingFields:
    @pytest.mark.parametrize("missing_field", _REQUIRED_FIELDS)
    def test_missing_required_field_raises_value_error(
        self, tmp_path: Path, missing_field: str
    ) -> None:
        lines = [ln for ln in _MINIMAL_YAML.splitlines() if not ln.startswith(f"{missing_field}:")]
        p = tmp_path / "bad.yaml"
        p.write_text("\n".join(lines) + "\n")
        with pytest.raises(ValueError, match=missing_field):
            TraditionManifest.from_yaml_path(p)


class TestAllGuard:
    def test_module_all(self) -> None:
        assert _module_all == ["TraditionManifest", "LensEntry", "DialingConfig"]


class TestSlots:
    def test_all_dataclasses_have_slots(self) -> None:
        for cls in (LensEntry, DialingConfig, TraditionManifest):
            assert "__slots__" in vars(cls), f"{cls.__name__} missing __slots__"


class TestGatewayReexport:
    def test_importable_from_core_types(self) -> None:
        from autoskillit.core.types import DialingConfig as DC
        from autoskillit.core.types import LensEntry as LE
        from autoskillit.core.types import TraditionManifest as TM

        assert TM is TraditionManifest
        assert LE is LensEntry
        assert DC is DialingConfig
