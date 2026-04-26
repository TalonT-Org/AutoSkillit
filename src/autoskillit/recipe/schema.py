"""Pure domain model for pipeline recipes — dataclasses and enums only."""

from __future__ import annotations

import dataclasses
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Final

from autoskillit.core import RecipeSource

AUTOSKILLIT_VERSION_KEY: Final = "autoskillit_version"
RECIPE_VERSION_KEY: Final = "recipe_version"
CAMPAIGN_REF_RE: Final = re.compile(r"\$\{\{\s*campaign\.(\w+)\s*\}\}")


class RecipeKind(StrEnum):
    STANDARD = "standard"
    CAMPAIGN = "campaign"


@dataclass
class RecipeIngredient:
    description: str
    required: bool = False
    default: str | None = None
    hidden: bool = False  # When True, excluded from ingredients table shown to agent

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


# Terminal routing sentinels: valid on_exhausted targets that are not step names.
# "escalate": triggers orchestrator-level escalation (stop-with-escalation).
# "done": terminates the recipe cleanly without escalation.
_TERMINAL_TARGETS: frozenset[str] = frozenset({"done", "escalate"})


@dataclass
class RecipeStep:
    name: str = ""  # Set from the YAML dict key after parsing; enables block member lookup
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
    sub_recipe: str | None = None  # Name of sub-recipe file (no extension)
    gate: str | None = None  # Ingredient name whose value controls lazy loading
    optional_context_refs: list[str] = field(
        default_factory=list
    )  # Context variable names that may be referenced before they are captured (cyclic routes)
    stale_threshold: int | None = None  # None means use global RunSkillConfig.stale_threshold
    idle_output_timeout: int | None = None  # None = use global cfg; 0 = disabled for this step
    block: str | None = None  # Named block anchor this step belongs to (e.g. "pre_queue_gate")


@dataclass(frozen=True)
class RecipeBlock:
    """A named contiguous region of the step routing graph with budget constraints.

    Populated by ``extract_blocks`` in ``recipe/_analysis.py`` during validation.
    Steps declare membership via ``step.block = <name>``.  The block primitive
    enables per-block semantic rules (budget guards, single-producer checks, etc.)
    that individual-step rules cannot express.
    """

    name: str  # e.g. "pre_queue_gate"
    entry: str  # name of the step at the block entry point (no in-block predecessor)
    exit: str  # name of the step at the block exit point (no in-block successor)
    members: tuple[RecipeStep, ...]  # ordered member steps (by YAML declaration order)
    tool_counts: Mapping[str, int]  # {"run_cmd": 1, "check_repo_merge_state": 1, …}
    gh_api_occurrences: int  # total count of "gh api" substrings across all run_cmd cmds


@dataclass
class CampaignDispatch:
    """A single dispatch entry in a campaign recipe."""

    name: str
    recipe: str
    task: str
    ingredients: dict[str, str] = field(
        default_factory=dict
    )  # string-only: YAML pass-through key-value pairs, not structured RecipeIngredient objects
    depends_on: list[str] = field(default_factory=list)
    capture: dict[str, str] = field(default_factory=dict)


@dataclass
class Recipe:
    name: str
    description: str
    summary: str = ""
    ingredients: dict[str, RecipeIngredient] = field(default_factory=dict)
    steps: dict[str, RecipeStep] = field(default_factory=dict)
    kitchen_rules: list[str] = field(default_factory=list)
    version: str | None = None
    recipe_version: str | None = None
    content_hash: str = ""
    composite_hash: str = ""
    experimental: bool = False
    requires_packs: list[str] = field(default_factory=list)
    kind: RecipeKind = RecipeKind.STANDARD
    categories: list[str] = field(default_factory=list)
    dispatches: list[CampaignDispatch] = field(default_factory=list)
    requires_recipe_packs: list[str] = field(default_factory=list)
    allowed_recipes: list[str] = field(default_factory=list)
    continue_on_failure: bool = False
    # Populated by extract_blocks() during load; empty tuple for recipes with no block: anchors.
    blocks: tuple[RecipeBlock, ...] = field(default_factory=tuple)

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
    recipe_version: str | None = None
    content_hash: str = ""
    content: str | None = None  # raw YAML text; None when set via parse_recipe_metadata
    kind: RecipeKind = RecipeKind.STANDARD


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
