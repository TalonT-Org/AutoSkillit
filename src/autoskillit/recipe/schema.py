"""Pure domain model for pipeline recipes — dataclasses and enums only."""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

from autoskillit.core import RecipeSource

AUTOSKILLIT_VERSION_KEY: Final = "autoskillit_version"


@dataclass
class RecipeIngredient:
    description: str
    required: bool = False
    default: str | None = None

    def __post_init__(self) -> None:
        self.description = self.description.strip().replace("\n", " ")
        if self.default is not None:
            self.default = self.default.strip()


@dataclass
class StepResultCondition:
    """A single conditional route in a predicate-based on_result block.

    when=None means the default/else condition (no guard — always matches).
    Conditions are evaluated in declaration order; first match wins.
    """

    route: str
    when: str | None = None


@dataclass
class StepResultRoute:
    """Multi-way routing based on result fields.

    Two mutually exclusive formats:
    - Legacy (field+routes): field is non-empty, conditions is empty.
      Routes based on an exact match of result.<field> to a known string value.
    - Predicate (conditions): conditions is non-empty, field and routes are empty.
      Conditions are evaluated in order; first matching `when` predicate wins.
      A condition with when=None is the default/else case.
    """

    # Legacy format
    field: str = ""
    routes: dict[str, str] = dataclasses.field(default_factory=dict)
    # Predicate format (mutually exclusive with field+routes)
    conditions: list[StepResultCondition] = dataclasses.field(default_factory=list)


@dataclass
class RecipeStep:
    tool: str | None = None
    action: str | None = None  # Built-in action: "route", "stop", "confirm"
    python: str | None = None
    constant: str | None = None  # Literal output value — no subprocess or MCP call
    with_args: dict[str, str] = field(default_factory=dict)
    on_success: str | None = None
    on_failure: str | None = None
    on_context_limit: str | None = None
    on_result: StepResultRoute | None = None
    retries: int = 3
    on_exhausted: str = "escalate"
    message: str | None = None
    note: str | None = None
    capture: dict[str, str] = field(default_factory=dict)
    capture_list: dict[str, str] = field(default_factory=dict)
    optional: bool = False
    skip_when_false: str | None = None
    model: str | None = None
    description: str = ""


@dataclass
class Recipe:
    name: str
    description: str
    summary: str = ""
    ingredients: dict[str, RecipeIngredient] = field(default_factory=dict)
    steps: dict[str, RecipeStep] = field(default_factory=dict)
    kitchen_rules: list[str] = field(default_factory=list)
    version: str | None = None
    experimental: bool = False

    def __post_init__(self) -> None:
        self.name = self.name.strip()
        self.description = self.description.strip()
        self.summary = self.summary.strip()
        self.kitchen_rules = [rule.strip() for rule in self.kitchen_rules]


@dataclass
class RecipeInfo:
    name: str
    description: str
    source: RecipeSource
    path: Path
    summary: str = ""
    version: str | None = None
    content: str | None = None  # raw YAML text; None when set via parse_recipe_metadata


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
