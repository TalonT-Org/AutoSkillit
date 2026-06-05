"""Tradition manifest types. Zero autoskillit imports (IL-0)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar

import yaml

from ._type_enums import SynthesisStrategy
from ._type_phoropter import PhoropterPhaseSkip

try:
    from yaml import CSafeLoader as _Loader
except ImportError:
    _Loader = yaml.SafeLoader  # type: ignore[misc,assignment]

__all__ = ["TraditionManifest", "LensEntry", "DialingConfig"]


@dataclass(frozen=True, slots=True)
class LensEntry:
    slug: str = ""
    analytical_mode: str = ""
    primary_question: str = ""
    tradition: str = ""
    codification_level: str = ""
    diagram_direction: str = ""


@dataclass(frozen=True, slots=True)
class DialingConfig:
    selection_strategy: str = ""
    min_lenses: int = 1
    max_lenses: int = 1
    always_run: tuple[str, ...] = ()
    synthesis_strategy: SynthesisStrategy = SynthesisStrategy.NULL


@dataclass(frozen=True, slots=True)
class TraditionManifest:
    name: str
    description: str
    output_type: str
    step_count: int
    mode_label: str
    context_file_schema: str
    default_enabled: bool
    failure_mode: str
    step_name_prefix: str
    phase_skip: PhoropterPhaseSkip | None = None
    lenses: tuple[LensEntry, ...] = ()
    dialing: DialingConfig = field(default_factory=DialingConfig)

    _REQUIRED_KEYS: ClassVar[frozenset[str]] = frozenset(
        {
            "name",
            "description",
            "output_type",
            "step_count",
            "mode_label",
            "context_file_schema",
            "default_enabled",
            "failure_mode",
            "step_name_prefix",
        }
    )

    @classmethod
    def from_yaml_path(cls, path: Path) -> TraditionManifest:
        with open(path, "rb") as fh:
            raw = yaml.load(fh, Loader=_Loader)
        if not isinstance(raw, dict):
            raise ValueError(f"Expected a YAML mapping, got {type(raw).__name__}")
        missing = cls._REQUIRED_KEYS - raw.keys()
        if missing:
            raise ValueError(f"Missing required field(s): {', '.join(sorted(missing))}")

        lenses_raw = raw.get("lenses") or ()
        lenses = tuple(
            LensEntry(**{k: v for k, v in entry.items() if k in LensEntry.__dataclass_fields__})
            for entry in lenses_raw
        )

        dialing_raw = raw.get("dialing")
        if dialing_raw and isinstance(dialing_raw, dict):
            synthesis = dialing_raw.get("synthesis_strategy")
            always = dialing_raw.get("always_run")
            dialing = DialingConfig(
                selection_strategy=dialing_raw.get("selection_strategy", ""),
                min_lenses=dialing_raw.get("min_lenses", 1),
                max_lenses=dialing_raw.get("max_lenses", 1),
                always_run=tuple(always) if always else (),
                synthesis_strategy=SynthesisStrategy(synthesis)
                if synthesis
                else SynthesisStrategy.NULL,
            )
        else:
            dialing = DialingConfig()

        skip_raw = raw.get("phase_skip")
        if skip_raw and isinstance(skip_raw, dict):
            phase_skip = PhoropterPhaseSkip(
                **{
                    k: v
                    for k, v in skip_raw.items()
                    if k in PhoropterPhaseSkip.__dataclass_fields__
                }
            )
        else:
            phase_skip = None

        return cls(
            name=raw["name"],
            description=raw["description"],
            output_type=raw["output_type"],
            step_count=raw["step_count"],
            mode_label=raw["mode_label"],
            context_file_schema=raw["context_file_schema"],
            default_enabled=raw["default_enabled"],
            failure_mode=raw["failure_mode"],
            step_name_prefix=raw["step_name_prefix"],
            phase_skip=phase_skip,
            lenses=lenses,
            dialing=dialing,
        )
