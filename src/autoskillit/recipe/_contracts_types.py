"""Recipe contract types — dataclasses and regex patterns."""

from __future__ import annotations

import dataclasses
from enum import StrEnum

import regex as re

from autoskillit.core import BoundScalar, PreflightKind

_CONTEXT_REF_RE = re.compile(r"\$\{\{\s*context\.(\w+)\s*\}\}")
INPUT_REF_RE = re.compile(r"\$\{\{\s*inputs\.(\w+)\s*\}\}")
_TEMPLATE_REF_RE = re.compile(r"\$\{\{[^}]+\}\}")
RESULT_CAPTURE_RE = re.compile(r"\$\{\{\s*result\.([\w-]+)\s*\}\}")


@dataclasses.dataclass(frozen=True, slots=True)
class SkillInput:
    name: str
    type: str
    required: bool
    recommended: bool = False
    nullable: bool = True
    unresolved_default: BoundScalar | None = None

    def accepts(self, value: object) -> bool:
        normalized = self.type
        if normalized in {
            "str",
            "string",
            "optional_string",
            "file_path",
            "file_path_list",
            "directory_path",
        }:
            return isinstance(value, str)
        if normalized == "integer":
            return isinstance(value, int) and not isinstance(value, bool)
        if normalized in {"number", "float"}:
            return isinstance(value, int) and not isinstance(value, bool)
        if normalized in {"boolean", "bool"}:
            return isinstance(value, bool)
        return False


@dataclasses.dataclass
class SkillOutput:
    name: str
    type: str
    allowed_values: list[str] = dataclasses.field(default_factory=list)


class AuditOutputMode(StrEnum):
    ATTESTED = "attested"
    STANDALONE = "standalone"


@dataclasses.dataclass(frozen=True, slots=True)
class AuditOutputContract:
    outputs: tuple[SkillOutput, ...]
    expected_output_patterns: tuple[str, ...]
    pattern_examples: tuple[str, ...]


@dataclasses.dataclass(frozen=True, slots=True)
class ResultFieldSpec:
    name: str
    type: str
    required: bool = True


@dataclasses.dataclass
class OutcomeInvariantEntry:
    when: str
    require: str


@dataclasses.dataclass
class SuccessQualifierEntry:
    when: str
    qualifier: str


@dataclasses.dataclass(frozen=True, slots=True)
class AuditAuthorityPublicationSpec:
    output_field: str
    prior_input_field: str


@dataclasses.dataclass
class SkillContract:
    inputs: tuple[SkillInput, ...]
    outputs: list[SkillOutput]
    expected_output_patterns: list[str] = dataclasses.field(default_factory=list)
    pattern_examples: list[str] = dataclasses.field(default_factory=list)
    write_behavior: str | None = None
    write_expected_when: list[str] = dataclasses.field(default_factory=list)
    read_only: bool = False
    scope_discipline: bool = False
    completion_required: bool = False
    result_fields: list[ResultFieldSpec] = dataclasses.field(default_factory=list)
    outcome_invariants: list[OutcomeInvariantEntry] = dataclasses.field(default_factory=list)
    success_qualifiers: list[SuccessQualifierEntry] = dataclasses.field(default_factory=list)
    input_preflight: str | None = None
    audit_authority_publication: AuditAuthorityPublicationSpec | None = None
    audit_output_contracts: dict[AuditOutputMode, AuditOutputContract] = dataclasses.field(
        default_factory=dict
    )
    audit_output_mode: AuditOutputMode | None = None

    def __post_init__(self) -> None:
        if self.input_preflight is None:
            return
        try:
            self.input_preflight = PreflightKind(self.input_preflight).value
        except ValueError as exc:
            raise ValueError(f"unsupported input preflight: {self.input_preflight!r}") from exc


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
