"""Recipe contract types — dataclasses and regex patterns."""

from __future__ import annotations

import dataclasses
from enum import StrEnum

import regex as re

from autoskillit.core import (
    CANONICAL_INTEGER_DIGITS,
    EXTERNAL_EFFECT_CHOICES,
    VALID_EXTERNAL_EFFECTS,
    BoundScalar,
    BoundValue,
    BoundValueOrigin,
    PreflightKind,
    is_canonical_integer_text,
)

_CONTEXT_REF_RE = re.compile(r"\$\{\{\s*context\.([A-Za-z_]\w*)\s*\}\}")
INPUT_REF_RE = re.compile(r"\$\{\{\s*inputs\.([A-Za-z_]\w*)\s*\}\}")
_TEMPLATE_REF_RE = re.compile(r"\$\{\{[^}]+\}\}")
RESULT_CAPTURE_RE = re.compile(r"\$\{\{\s*result\.([\w-]+)\s*\}\}")


_TEXT_INPUT_TYPES = frozenset({"str", "string", "optional_string"})
_PATH_INPUT_TYPES = frozenset({"file_path", "file_path_list", "directory_path"})
_EXACT_REFERENCE_ORIGINS = frozenset({BoundValueOrigin.CONTEXT, BoundValueOrigin.RECIPE_INPUT})


@dataclasses.dataclass(frozen=True, slots=True)
class SkillInput:
    name: str
    type: str
    required: bool
    recommended: bool = False
    nullable: bool = True
    absence_value: BoundScalar | None = None

    def __post_init__(self) -> None:
        if not self.has_absence_value:
            return
        value = self.absence_value
        if self.required:
            raise ValueError(f"required input {self.name!r} cannot declare absence_value")
        if type(value) not in (str, int, bool):
            raise ValueError("absence_value must be a strict scalar")
        if not self.accepts(value):
            raise ValueError(f"skill input {self.name!r} does not accept absence_value {value!r}")

    @property
    def has_absence_value(self) -> bool:
        return self.absence_value is not None

    def accepts(self, value: object) -> bool:
        normalized = self.type
        if normalized in _TEXT_INPUT_TYPES | _PATH_INPUT_TYPES:
            return isinstance(value, str)
        if normalized == "integer":
            return isinstance(value, int) and not isinstance(value, bool)
        if normalized in {"number", "float"}:
            return isinstance(value, int) and not isinstance(value, bool)
        if normalized in {"boolean", "bool"}:
            return isinstance(value, bool)
        return False

    def admit_runtime_wire(self, value: BoundScalar, bound: BoundValue) -> BoundScalar | None:
        """Return the bound value, or None when the declared type rejects its wire form."""
        if (
            bound.origin is BoundValueOrigin.DISPATCH_ITEM
            and isinstance(value, str)
            and value.strip() == str(bound.declared_value).strip()
        ):
            return None
        if self.accepts(value):
            return value
        if bound.origin not in _EXACT_REFERENCE_ORIGINS:
            return None
        if self.type == "integer":
            if isinstance(value, str) and is_canonical_integer_text(value):
                return int(value)
            return None
        if self.type not in _TEXT_INPUT_TYPES or not isinstance(value, int):
            return None
        if isinstance(value, bool):
            text = "true" if value else "false"
        elif abs(value) < 10**CANONICAL_INTEGER_DIGITS:
            text = str(value)
        else:
            return None
        return None if text == bound.absence_value else text

    def wire_mismatch_message(self, value: BoundScalar, bound: BoundValue) -> str:
        if (
            bound.origin is BoundValueOrigin.DISPATCH_ITEM
            and isinstance(value, str)
            and value.strip() == str(bound.declared_value).strip()
        ):
            producer = ", ".join(
                f"{namespace}.{name}"
                for namespace, dependencies in (
                    ("context", bound.context_dependencies),
                    ("inputs", bound.input_dependencies),
                )
                for name in dependencies
            )
            return (
                f"per-item skill input {self.name!r} received its unsubstituted placeholder "
                f"{value!r}; substitute one item from the step's dispatch_items {producer}"
            )
        message = (
            f"runtime skill input {self.name!r} expects {self.type!r}; "
            f"received {type(value).__name__}"
        )
        if self.type == "integer":
            message += "; send a JSON integer"
            if bound.origin in _EXACT_REFERENCE_ORIGINS:
                message += (
                    " or canonical ASCII decimal text"
                    f" (e.g. '31'; at most {CANONICAL_INTEGER_DIGITS} digits)"
                )
        elif self.type in _TEXT_INPUT_TYPES:
            message += "; send a JSON string"
        return message


@dataclasses.dataclass
class SkillOutput:
    name: str
    type: str
    allowed_values: list[str] = dataclasses.field(default_factory=list)

    def __post_init__(self) -> None:
        if self.type == "dispositions" and self.name != "finding_disposition":
            raise ValueError("dispositions output type is reserved for 'finding_disposition'")


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
    external_effect: str = "none"
    read_only: bool = False
    scope_discipline: bool = False
    completion_required: bool = False
    result_fields: list[ResultFieldSpec] = dataclasses.field(default_factory=list)
    outcome_invariants: list[OutcomeInvariantEntry] = dataclasses.field(default_factory=list)
    success_qualifiers: list[SuccessQualifierEntry] = dataclasses.field(default_factory=list)
    input_preflight: tuple[str, ...] = ()
    audit_authority_publication: AuditAuthorityPublicationSpec | None = None
    audit_output_contracts: dict[AuditOutputMode, AuditOutputContract] = dataclasses.field(
        default_factory=dict
    )
    audit_output_mode: AuditOutputMode | None = None
    delegated_write_input: str | None = None
    delegated_write_output: str | None = None

    def __post_init__(self) -> None:
        if self.external_effect not in VALID_EXTERNAL_EFFECTS:
            raise ValueError(f"external_effect must be {EXTERNAL_EFFECT_CHOICES}")
        if not isinstance(self.input_preflight, tuple):
            raise ValueError("input_preflight must be a tuple")
        try:
            normalized = tuple(PreflightKind(value).value for value in self.input_preflight)
        except ValueError as exc:
            raise ValueError(f"unsupported input preflight: {self.input_preflight!r}") from exc
        self.input_preflight = normalized


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
    block_fingerprints: tuple[BlockFingerprint, ...] = dataclasses.field(default_factory=tuple)
