"""Strict bounded loader for child-produced audit semantics.

The semantic artifact intentionally excludes lifecycle, lineage, installation,
and output-location authority.  Those values belong to a parent-owned
materialization step, not to this codec.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal, Protocol

from ..io import YAMLError, load_yaml
from ..io.io import decode_versioned_json_bytes
from ..io.path_containment import ContainmentError, read_stable_contained_bytes
from ..types._type_audit_admission import (
    AUDIT_SEMANTIC_SCHEMA_VERSION,
    STANDALONE_AUDIT_EVIDENCE_KIND,
    STANDALONE_AUDIT_EVIDENCE_SCHEMA_VERSION,
    AuditSemanticResult,
    StandaloneAuditEvidence,
)
from ..types._type_audit_artifact_ref import ArtifactRef
from ..types._type_audit_cycle_authority import (
    AuditAssessmentRow,
    AuditCycleAuthority,
    AuditDisposition,
)
from ..types._type_audit_cycle_disposition import (
    AdmissionReason,
    AuditFindingWaiver,
    InventoryAdmissionDecision,
    PlanDispositionRow,
)

__all__ = [
    "AuditSemanticCodecError",
    "PRESCRIPTIVE_MECHANISM_CUES",
    "ProbedRequirement",
    "SUBSTITUTION_MARKERS",
    "SubstitutionFinding",
    "SubstitutionTrigger",
    "canonical_full_reference_records_match",
    "evaluate_diff_mock_of_prescribed_symbol",
    "evaluate_rationale_contradiction",
    "load_audit_semantic_result",
    "load_standalone_audit_evidence",
    "probe_substitutions",
]

_DEFAULT_MAX_SIZE_BYTES = 10_000_000
_TOP_LEVEL_KEYS = frozenset(
    {
        "assessments",
        "audited_plan_refs",
        "remediation_ref",
        "schema_version",
        "verdict",
    }
)
_STANDALONE_TOP_LEVEL_KEYS = _TOP_LEVEL_KEYS | {"kind"}
_ARTIFACT_REF_KEYS = frozenset(
    {
        "byte_size",
        "content_digest",
        "locator",
        "media_type",
        "schema_version",
    }
)
_ASSESSMENT_KEYS = frozenset(
    {
        "assessment",
        "evidence_summary",
        "requirement_id",
        "requirement_text",
        "row_digest",
    }
)

SUBSTITUTION_MARKERS: frozenset[str] = frozenset(
    {
        "mock",
        "mocks",
        "mocked",
        "monkeypatch",
        "patch",
        "patched",
        "stub",
        "stubbed",
        "side_effect",
        "fake",
        "faked",
        "simulated",
        "simulates",
        "instead of",
        "in lieu of",
        "rather than",
    }
)
PRESCRIPTIVE_MECHANISM_CUES: frozenset[str] = frozenset(
    {
        "spawn",
        "real",
        "actual",
        "topology",
        "parent",
        "child",
        "descendant",
        "process",
        "end-to-end",
        "integration",
        "live",
        "genuine",
        "not mocked",
    }
)

_TOKEN_RE = re.compile(r"[a-z0-9]+")
_IDENTIFIER_RE = re.compile(r"(?<![A-Za-z0-9_])[A-Za-z_][A-Za-z0-9_]*(?![A-Za-z0-9_])")


class SubstitutionTrigger(StrEnum):
    RATIONALE_CONTRADICTION = "RATIONALE_CONTRADICTION"
    DIFF_MOCK_OF_PRESCRIBED_SYMBOL = "DIFF_MOCK_OF_PRESCRIBED_SYMBOL"


@dataclass(frozen=True, slots=True)
class ProbedRequirement:
    requirement_id: str
    requirement_text: str
    evidence_summary: str


@dataclass(frozen=True, slots=True)
class SubstitutionFinding:
    requirement_id: str
    trigger: SubstitutionTrigger
    matched_marker: str
    matched_prescription: str


def _tokens(value: str) -> tuple[str, ...]:
    return tuple(_TOKEN_RE.findall(value.casefold().replace("_", " ")))


def _first_matching_term(value: str, terms: frozenset[str]) -> str | None:
    haystack = _tokens(value)
    for term in sorted(terms):
        needle = _tokens(term)
        width = len(needle)
        if width and any(
            haystack[index : index + width] == needle for index in range(len(haystack))
        ):
            return term
    return None


def _added_diff_text(diff_text: str) -> str:
    """Return the joined text of added unified-diff lines.

    Strips the leading ``+`` marker and skips the ``+++`` file header. The
    ``\\ No newline at end of file`` escape (always prefixed with a single
    space, never with ``+``) is filtered incidentally because it does not
    start with ``+``. The result is a corpus suitable for term and
    identifier matching, not a structural reconstruction of the diff.
    """
    chunks: list[str] = []
    for line in diff_text.splitlines():
        if not line.startswith("+"):
            continue
        if line.startswith("+++"):
            continue
        chunks.append(line[1:])
    return "\n".join(chunks)


def evaluate_rationale_contradiction(
    requirement_id: str,
    requirement_text: str,
    evidence_summary: str,
) -> SubstitutionFinding | None:
    """Detect evidence that substitutes for a requirement's prescribed mechanism."""

    marker = _first_matching_term(evidence_summary, SUBSTITUTION_MARKERS)
    cue = _first_matching_term(requirement_text, PRESCRIPTIVE_MECHANISM_CUES)
    if marker is None or cue is None:
        return None
    return SubstitutionFinding(
        requirement_id=requirement_id,
        trigger=SubstitutionTrigger.RATIONALE_CONTRADICTION,
        matched_marker=marker,
        matched_prescription=cue,
    )


def evaluate_diff_mock_of_prescribed_symbol(
    requirement_id: str,
    requirement_text: str,
    diff_text: str,
) -> SubstitutionFinding | None:
    """Detect added diff lines that mock an identifier named by a requirement."""

    identifiers = {
        match.group(0).casefold()
        for match in _IDENTIFIER_RE.finditer(requirement_text)
        if "_" in match.group(0)
    }
    if not identifiers:
        return None
    added_text = _added_diff_text(diff_text)
    marker = _first_matching_term(added_text, SUBSTITUTION_MARKERS)
    added_identifiers = {
        match.group(0).casefold() for match in _IDENTIFIER_RE.finditer(added_text)
    }
    matches = sorted(identifiers & added_identifiers)
    if marker is not None and matches:
        return SubstitutionFinding(
            requirement_id=requirement_id,
            trigger=SubstitutionTrigger.DIFF_MOCK_OF_PRESCRIBED_SYMBOL,
            matched_marker=marker,
            matched_prescription=matches[0],
        )
    return None


def probe_substitutions(
    requirements: tuple[ProbedRequirement, ...],
    diff_text: str,
) -> tuple[SubstitutionFinding, ...]:
    """Apply both deterministic substitution rules to each requirement."""

    findings: list[SubstitutionFinding] = []
    for requirement in requirements:
        candidates = (
            evaluate_rationale_contradiction(
                requirement.requirement_id,
                requirement.requirement_text,
                requirement.evidence_summary,
            ),
            evaluate_diff_mock_of_prescribed_symbol(
                requirement.requirement_id,
                requirement.requirement_text,
                diff_text,
            ),
        )
        findings.extend(candidate for candidate in candidates if candidate is not None)
    return tuple(dict.fromkeys(findings))


AuditSemanticCodecReason = Literal[
    "artifact_read_failed",
    "invalid_canonical_json",
    "invalid_semantic_schema",
    "forbidden_identity_field",
    "prepared_effect_mismatch",
]


class _ArtifactByteReader(Protocol):
    def __call__(
        self,
        path: str | Path,
        allowed_root: str | Path,
        /,
        *,
        max_size_bytes: int,
    ) -> tuple[Path, bytes]: ...


class AuditSemanticCodecError(ValueError):
    """Fail-closed semantic-artifact rejection with a stable machine reason."""

    def __init__(self, reason: AuditSemanticCodecReason, message: str) -> None:
        super().__init__(message)
        self.reason = reason


def _require_exact_mapping(
    value: object,
    *,
    expected_keys: frozenset[str],
    path: str,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise AuditSemanticCodecError(
            "invalid_semantic_schema",
            f"{path} must be an object",
        )
    actual_keys = frozenset(value)
    unknown = actual_keys - expected_keys
    if unknown:
        rendered = ", ".join(sorted(repr(key) for key in unknown))
        raise AuditSemanticCodecError(
            "forbidden_identity_field",
            f"{path} contains forbidden field(s): {rendered}",
        )
    missing = expected_keys - actual_keys
    if missing:
        rendered = ", ".join(sorted(repr(key) for key in missing))
        raise AuditSemanticCodecError(
            "invalid_semantic_schema",
            f"{path} is missing required field(s): {rendered}",
        )
    return value


def _require_exact_sequence(value: object, *, path: str) -> list[Any]:
    if not isinstance(value, list):
        raise AuditSemanticCodecError(
            "invalid_semantic_schema",
            f"{path} must be an array",
        )
    return value


def _require_scalar(value: object, *, path: str) -> None:
    if isinstance(value, dict):
        rendered = ", ".join(sorted(repr(key) for key in value))
        raise AuditSemanticCodecError(
            "forbidden_identity_field",
            f"{path} contains forbidden nested field(s): {rendered}",
        )
    if isinstance(value, list):
        raise AuditSemanticCodecError(
            "invalid_semantic_schema",
            f"{path} must be a scalar",
        )


def _validate_exact_recursive_schema(
    raw: dict[str, Any],
    *,
    expected_schema_version: int,
) -> None:
    top = _require_exact_mapping(raw, expected_keys=_TOP_LEVEL_KEYS, path="$")
    if (
        isinstance(top["schema_version"], bool)
        or not isinstance(top["schema_version"], int)
        or top["schema_version"] != expected_schema_version
    ):
        raise AuditSemanticCodecError(
            "invalid_semantic_schema",
            "$.schema_version is invalid",
        )

    audited_plan_refs = _require_exact_sequence(
        top["audited_plan_refs"],
        path="$.audited_plan_refs",
    )
    for index, ref in enumerate(audited_plan_refs):
        ref_mapping = _require_exact_mapping(
            ref,
            expected_keys=_ARTIFACT_REF_KEYS,
            path=f"$.audited_plan_refs[{index}]",
        )
        for field, value in ref_mapping.items():
            _require_scalar(
                value,
                path=f"$.audited_plan_refs[{index}].{field}",
            )

    assessments = _require_exact_sequence(top["assessments"], path="$.assessments")
    for index, assessment in enumerate(assessments):
        assessment_mapping = _require_exact_mapping(
            assessment,
            expected_keys=_ASSESSMENT_KEYS,
            path=f"$.assessments[{index}]",
        )
        for field, value in assessment_mapping.items():
            _require_scalar(
                value,
                path=f"$.assessments[{index}].{field}",
            )

    remediation_ref = top["remediation_ref"]
    if remediation_ref is not None:
        remediation_mapping = _require_exact_mapping(
            remediation_ref,
            expected_keys=_ARTIFACT_REF_KEYS,
            path="$.remediation_ref",
        )
        for field, value in remediation_mapping.items():
            _require_scalar(value, path=f"$.remediation_ref.{field}")
    _require_scalar(top["verdict"], path="$.verdict")


def canonical_full_reference_records_match(
    left: Sequence[ArtifactRef],
    right: Sequence[ArtifactRef],
) -> bool:
    """Compare ordered references using every canonical field.

    ``ArtifactRef`` equality intentionally compares only its content digest, so
    it is not a sufficient authority-boundary comparison.
    """

    return tuple(ref.to_dict() for ref in left) == tuple(ref.to_dict() for ref in right)


def load_audit_semantic_result(
    path: str | Path,
    allowed_root: str | Path,
    *,
    max_size_bytes: int = _DEFAULT_MAX_SIZE_BYTES,
    reader: _ArtifactByteReader = read_stable_contained_bytes,
) -> AuditSemanticResult:
    """Load one strict canonical audit-semantic artifact beneath ``allowed_root``."""

    try:
        _, data = reader(path, allowed_root, max_size_bytes=max_size_bytes)
    except (ContainmentError, OSError) as exc:
        raise AuditSemanticCodecError(
            "artifact_read_failed",
            f"audit semantic artifact containment/read failed: {exc}",
        ) from exc

    raw = decode_versioned_json_bytes(
        data,
        expected_version=AUDIT_SEMANTIC_SCHEMA_VERSION,
        require_canonical=True,
    )
    if raw is None:
        raise AuditSemanticCodecError(
            "invalid_canonical_json",
            "audit semantic artifact is not strict canonical versioned JSON",
        )

    _validate_exact_recursive_schema(
        raw,
        expected_schema_version=AUDIT_SEMANTIC_SCHEMA_VERSION,
    )
    try:
        result = AuditSemanticResult.from_dict(raw)
    except (TypeError, ValueError) as exc:
        raise AuditSemanticCodecError(
            "invalid_semantic_schema",
            f"audit semantic artifact validation failed: {exc}",
        ) from exc
    if result.to_dict() != raw:
        raise AuditSemanticCodecError(
            "invalid_semantic_schema",
            "audit semantic artifact does not round-trip exactly",
        )
    return result


def load_standalone_audit_evidence(
    path: str | Path,
    allowed_root: str | Path,
    *,
    max_size_bytes: int = _DEFAULT_MAX_SIZE_BYTES,
    reader: _ArtifactByteReader = read_stable_contained_bytes,
) -> StandaloneAuditEvidence:
    """Load one strict canonical standalone-audit artifact beneath ``allowed_root``."""

    try:
        _, data = reader(path, allowed_root, max_size_bytes=max_size_bytes)
    except (ContainmentError, OSError) as exc:
        raise AuditSemanticCodecError(
            "artifact_read_failed",
            f"standalone audit artifact containment/read failed: {exc}",
        ) from exc

    raw = decode_versioned_json_bytes(
        data,
        expected_version=STANDALONE_AUDIT_EVIDENCE_SCHEMA_VERSION,
        require_canonical=True,
    )
    if raw is None or set(raw) != _STANDALONE_TOP_LEVEL_KEYS:
        raise AuditSemanticCodecError(
            "invalid_canonical_json",
            "standalone audit artifact is not strict canonical versioned JSON",
        )
    if raw["kind"] != STANDALONE_AUDIT_EVIDENCE_KIND:
        raise AuditSemanticCodecError(
            "invalid_semantic_schema",
            "standalone audit artifact has an invalid kind",
        )

    _validate_exact_recursive_schema(
        {key: value for key, value in raw.items() if key != "kind"},
        expected_schema_version=STANDALONE_AUDIT_EVIDENCE_SCHEMA_VERSION,
    )
    try:
        result = StandaloneAuditEvidence.from_dict(raw)
    except (TypeError, ValueError) as exc:
        raise AuditSemanticCodecError(
            "invalid_semantic_schema",
            f"standalone audit artifact validation failed: {exc}",
        ) from exc
    if result.to_dict() != raw:
        raise AuditSemanticCodecError(
            "invalid_semantic_schema",
            "standalone audit artifact does not round-trip exactly",
        )
    return result


_REQUIREMENTS_HEADER = ("Requirement ID", "Disposition", "Implementation Step")
_STEP_HEADING_RE = re.compile(
    r"^###\s+(Step\s+[1-9][0-9]*(?:\.[1-9][0-9]*)*)(?::[^\n]*)?$",
    re.MULTILINE,
)


def _extract_section(markdown: str, heading: str) -> str:
    pattern = re.compile(rf"^## {re.escape(heading)}[ \t]*$", re.MULTILINE)
    matches = tuple(pattern.finditer(markdown))
    if len(matches) != 1:
        raise ValueError(f"expected exactly one ## {heading} section")
    start = matches[0].end()
    next_heading = re.search(r"^##\s+", markdown[start:], re.MULTILINE)
    end = start + next_heading.start() if next_heading is not None else len(markdown)
    return markdown[start:end]


def _split_table_row(line: str) -> tuple[str, ...]:
    stripped = line.strip()
    if not stripped.startswith("|") or not stripped.endswith("|"):
        raise ValueError("Requirements Map rows must be pipe-delimited")
    return tuple(cell.strip() for cell in stripped[1:-1].split("|"))


def parse_requirements_map(markdown: str) -> tuple[PlanDispositionRow, ...]:
    """Parse the plan's exact Requirements Map table."""
    section = _extract_section(markdown, "Requirements Map")
    lines = tuple(line for line in section.splitlines() if line.strip())
    if len(lines) < 3:
        raise ValueError("Requirements Map must contain a header, separator, and rows")
    if _split_table_row(lines[0]) != _REQUIREMENTS_HEADER:
        raise ValueError(
            "Requirements Map header must be "
            "| Requirement ID | Disposition | Implementation Step |"
        )
    separator = _split_table_row(lines[1])
    if len(separator) != 3 or any(re.fullmatch(r":?-{3,}:?", cell) is None for cell in separator):
        raise ValueError("Requirements Map separator is invalid")
    rows: list[PlanDispositionRow] = []
    for line in lines[2:]:
        cells = _split_table_row(line)
        if len(cells) != 3:
            raise ValueError("Requirements Map rows must have exactly three columns")
        requirement_id, disposition, implementation_step = cells
        step = None if implementation_step in {"", "-", "—"} else implementation_step
        rows.append(
            PlanDispositionRow.create(
                requirement_id=requirement_id,
                disposition=disposition,
                implementation_step=step,
            )
        )
    ids = tuple(row.requirement_id for row in rows)
    if len(ids) != len(set(ids)):
        raise ValueError("Requirements Map contains duplicate requirement IDs")
    return tuple(rows)


def implementation_step_blocks(markdown: str) -> dict[str, str]:
    """Return each numbered Implementation Steps block by its canonical name."""
    section = _extract_section(markdown, "Implementation Steps")
    matches = tuple(_STEP_HEADING_RE.finditer(section))
    if not matches:
        raise ValueError("Implementation Steps must contain ### Step N directives")
    blocks: dict[str, str] = {}
    for index, matched in enumerate(matches):
        step_name = matched.group(1)
        if step_name in blocks:
            raise ValueError(f"duplicate implementation step {step_name}")
        end = matches[index + 1].start() if index + 1 < len(matches) else len(section)
        blocks[step_name] = section[matched.start() : end]
    return blocks


def load_audit_finding_waivers(
    *,
    waiver_root: Path | None,
    reader: _ArtifactByteReader,
    max_size_bytes: int,
) -> tuple[AuditFindingWaiver, ...]:
    """Load the human-maintained waiver ledger from its explicit root."""
    if waiver_root is None:
        return ()
    ledger_path = waiver_root / ".autoskillit/waivers/audit-findings.yaml"
    try:
        _, data = reader(ledger_path, waiver_root, max_size_bytes=max_size_bytes)
    except FileNotFoundError:
        return ()
    except (ContainmentError, OSError) as exc:
        raise ValueError(f"waiver ledger containment/read failed: {exc}") from exc
    try:
        raw = load_yaml(data.decode("utf-8", errors="strict"))
        if not isinstance(raw, dict) or set(raw) != {"waivers"}:
            raise ValueError("waiver ledger must be a mapping with only waivers")
        entries = raw["waivers"]
        if not isinstance(entries, list) or not all(isinstance(entry, dict) for entry in entries):
            raise ValueError("waiver ledger waivers must be a list of mappings")
        waivers = tuple(AuditFindingWaiver.from_dict(entry) for entry in entries)
        if len({waiver.waiver_id for waiver in waivers}) != len(waivers):
            raise ValueError("waiver ledger contains duplicate waiver IDs")
        return waivers
    except (UnicodeDecodeError, YAMLError, TypeError, ValueError) as exc:
        raise ValueError(f"waiver ledger is invalid: {exc}") from exc


def _waiver_rejection(
    *,
    assessment: AuditAssessmentRow,
    authority: AuditCycleAuthority,
    waiver_id: str | None,
    waiver_by_id: dict[str, AuditFindingWaiver],
    as_of: date | None = None,
) -> InventoryAdmissionDecision | None:
    if (
        waiver_id is not None
        and assessment.assessment.disposition is not AuditDisposition.REQUIRES_DECISION
    ):
        return InventoryAdmissionDecision.reject(
            AdmissionReason.WAIVER_NOT_APPLICABLE,
            f"{assessment.requirement_id} cannot be waived by decision",
        )
    if assessment.assessment.disposition is not AuditDisposition.REQUIRES_DECISION:
        return None
    if waiver_id is None:
        return InventoryAdmissionDecision.reject(
            AdmissionReason.UNMAPPED_REQUIREMENT,
            f"{assessment.requirement_id} requires waived-by-decision@<id>",
        )
    waiver = waiver_by_id.get(waiver_id)
    if waiver is None:
        return InventoryAdmissionDecision.reject(
            AdmissionReason.WAIVER_NOT_FOUND,
            f"{assessment.requirement_id} names unknown waiver {waiver_id!r}",
        )
    if (
        waiver.requirement_id != assessment.requirement_id
        or waiver.finding_row_digest != assessment.row_digest
    ):
        return InventoryAdmissionDecision.reject(
            AdmissionReason.WAIVER_DIGEST_MISMATCH,
            f"{assessment.requirement_id} does not match waiver {waiver_id!r}",
        )
    if waiver.plan_set_id != authority.plan_set_id or waiver.scope_id != authority.scope_id:
        return InventoryAdmissionDecision.reject(
            AdmissionReason.WAIVER_SCOPE_MISMATCH,
            f"{assessment.requirement_id} waiver scope does not match authority",
        )
    if waiver.part_id != authority.part_id:
        return InventoryAdmissionDecision.reject(
            AdmissionReason.WAIVER_PART_MISMATCH,
            f"{assessment.requirement_id} waiver part does not match authority",
        )
    try:
        is_stale = waiver.is_stale(as_of=as_of or date.today())
    except ValueError as exc:
        return InventoryAdmissionDecision.reject(AdmissionReason.WAIVER_LEDGER_INVALID, str(exc))
    if is_stale:
        return InventoryAdmissionDecision.reject(
            AdmissionReason.WAIVER_EXPIRED,
            f"{assessment.requirement_id} waiver {waiver_id!r} has expired",
        )
    return None


def validate_waiver_rows(
    *,
    authority: AuditCycleAuthority,
    plan_rows: tuple[PlanDispositionRow, ...],
    waivers: tuple[AuditFindingWaiver, ...],
    as_of: date | None = None,
) -> tuple[
    InventoryAdmissionDecision | None,
    tuple[tuple[AuditAssessmentRow, PlanDispositionRow], ...],
]:
    """Validate decision waivers and return the rows for legacy admission checks."""
    if not all(isinstance(waiver, AuditFindingWaiver) for waiver in waivers):
        return (
            InventoryAdmissionDecision.reject(
                AdmissionReason.WAIVER_LEDGER_INVALID,
                "waiver ledger contains invalid records",
            ),
            (),
        )
    waiver_by_id = {waiver.waiver_id: waiver for waiver in waivers}
    if len(waiver_by_id) != len(waivers):
        return (
            InventoryAdmissionDecision.reject(
                AdmissionReason.WAIVER_LEDGER_INVALID,
                "waiver ledger contains duplicate waiver IDs",
            ),
            (),
        )
    legacy_rows: list[tuple[AuditAssessmentRow, PlanDispositionRow]] = []
    for assessment, disposition in zip(authority.assessments, plan_rows, strict=True):
        rejection = _waiver_rejection(
            assessment=assessment,
            authority=authority,
            waiver_id=disposition.waiver_id,
            waiver_by_id=waiver_by_id,
            as_of=as_of,
        )
        if rejection is not None:
            return rejection, ()
        if assessment.assessment.disposition is not AuditDisposition.REQUIRES_DECISION:
            legacy_rows.append((assessment, disposition))
    return None, tuple(legacy_rows)
