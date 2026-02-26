"""Pure domain model for pipeline recipes — dataclasses and enums only."""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

from autoskillit.types import RecipeSource

AUTOSKILLIT_VERSION_KEY: Final = "autoskillit_version"


@dataclass
class RecipeIngredient:
    description: str
    required: bool = False
    default: str | None = None


@dataclass
class StepRetry:
    max_attempts: int = 3
    on: str | None = None
    on_exhausted: str = "escalate"


@dataclass
class StepResultRoute:
    """Multi-way routing based on a named field in a tool's JSON response."""

    field: str
    routes: dict[str, str] = dataclasses.field(default_factory=dict)


@dataclass
class RecipeStep:
    tool: str | None = None
    action: str | None = None
    python: str | None = None
    with_args: dict[str, str] = field(default_factory=dict)
    on_success: str | None = None
    on_failure: str | None = None
    on_result: StepResultRoute | None = None
    retry: StepRetry | None = None
    message: str | None = None
    note: str | None = None
    capture: dict[str, str] = field(default_factory=dict)
    optional: bool = False
    model: str | None = None


@dataclass
class Recipe:
    name: str
    description: str
    summary: str = ""
    ingredients: dict[str, RecipeIngredient] = field(default_factory=dict)
    steps: dict[str, RecipeStep] = field(default_factory=dict)
    kitchen_rules: list[str] = field(default_factory=list)
    version: str | None = None


@dataclass
class RecipeInfo:
    name: str
    description: str
    source: RecipeSource
    path: Path
    summary: str = ""
    version: str | None = None


@dataclass
class DataFlowWarning:
    """A non-blocking quality finding about pipeline data flow."""

    code: str  # DEAD_OUTPUT, IMPLICIT_HANDOFF
    step_name: str  # Step where the issue originates
    field: str  # Capture key or tool name
    message: str  # Human-readable explanation


@dataclass
class DataFlowReport:
    """Quality analysis of pipeline data flow (non-blocking)."""

    warnings: list[DataFlowWarning] = field(default_factory=list)
    summary: str = ""
