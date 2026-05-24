"""Recipe contract types — dataclasses and regex patterns."""

from __future__ import annotations

import dataclasses

import regex as re

_CONTEXT_REF_RE = re.compile(r"\$\{\{\s*context\.(\w+)\s*\}\}")
INPUT_REF_RE = re.compile(r"\$\{\{\s*inputs\.(\w+)\s*\}\}")
_TEMPLATE_REF_RE = re.compile(r"\$\{\{[^}]+\}\}")
RESULT_CAPTURE_RE = re.compile(r"\$\{\{\s*result\.([\w-]+)\s*\}\}")


@dataclasses.dataclass
class SkillInput:
    name: str
    type: str
    required: bool
    recommended: bool = False
    nullable: bool = True


@dataclasses.dataclass
class SkillOutput:
    name: str
    type: str


@dataclasses.dataclass(frozen=True, slots=True)
class ResultFieldSpec:
    name: str
    type: str
    required: bool = True


@dataclasses.dataclass
class SkillContract:
    inputs: list[SkillInput]
    outputs: list[SkillOutput]
    expected_output_patterns: list[str] = dataclasses.field(default_factory=list)
    pattern_examples: list[str] = dataclasses.field(default_factory=list)
    write_behavior: str | None = None
    write_expected_when: list[str] = dataclasses.field(default_factory=list)
    read_only: bool = False
    result_fields: list[ResultFieldSpec] = dataclasses.field(default_factory=list)


@dataclasses.dataclass(frozen=True, slots=True)
class ToolOutputFieldSpec:
    allowed_values: tuple[str, ...]
    terminal_values: frozenset[str]
    recoverable_values: frozenset[str]


@dataclasses.dataclass(frozen=True, slots=True)
class ToolOutputContractSpec:
    result_field: str
    fields: dict[str, ToolOutputFieldSpec]


@dataclasses.dataclass
class StaleItem:
    skill: str
    reason: str  # "version_mismatch" | "hash_mismatch"
    stored_value: str
    current_value: str


@dataclasses.dataclass
class DataFlowEntry:
    step: str
    available: list[str]
    required: list[str]
    produced: list[str]


@dataclasses.dataclass(frozen=True, slots=True)
class BlockFingerprint:
    """Structural fingerprint for a named recipe block.

    Used by ``check_contract_staleness`` to detect silent composition drift:
    any change to a block's member count, tool usage, gh api call count, or
    capture names produces a fingerprint mismatch (reason='block_composition_drift').
    """

    name: str
    member_count: int
    tool_counts_sorted: tuple[tuple[str, int], ...]  # sorted by tool name for stable comparison
    gh_api_occurrences: int
    capture_names_hash: str  # sha256hex of sorted capture key names across all members
    entry_step: str
    exit_step: str


@dataclasses.dataclass
class RecipeCard:
    recipe_source_hash: str | None
    bundled_manifest_version: str
    skill_hashes: dict[str, str]
    skills: dict[str, SkillContract]
    dataflow: list[DataFlowEntry]
    block_fingerprints: tuple[BlockFingerprint, ...] = dataclasses.field(
        default_factory=tuple  # type: ignore[arg-type]
    )
