"""Tamper-evident audit-cycle verification and pure inventory admission."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

from .closure_hashing import compute_bytes_hash
from .io import decode_versioned_json_bytes
from .path_containment import ContainmentError, read_stable_contained_bytes
from .types._type_audit_cycle import (
    AUDIT_CYCLE_SCHEMA_VERSION,
    AdmissionReason,
    ArtifactRef,
    AuditCycleAuthority,
    AuditCycleHead,
    AuditVerdict,
    InventoryAdmissionDecision,
    PlanDispositionReport,
    PlanDispositionRow,
)

__all__ = [
    "ArtifactByteReader",
    "AuditCycleVerificationError",
    "AuditCycleVerifier",
    "InventoryAdmissionEvaluator",
    "VerifiedAuditCycle",
]

_REQUIREMENTS_HEADER = ("Requirement ID", "Disposition", "Implementation Step")
_STEP_HEADING_RE = re.compile(
    r"^###\s+(Step\s+[1-9][0-9]*(?:\.[1-9][0-9]*)*)(?::[^\n]*)?$",
    re.MULTILINE,
)


@runtime_checkable
class ArtifactByteReader(Protocol):
    def __call__(
        self,
        path: str | Path,
        allowed_root: str | Path,
        *,
        max_size_bytes: int,
    ) -> tuple[Path, bytes]: ...


class AuditCycleVerificationError(ValueError):
    """A stable-reason rejection raised by the imperative verifier."""

    def __init__(self, reason: AdmissionReason, message: str) -> None:
        super().__init__(message)
        self.reason = reason


@dataclass(frozen=True, slots=True)
class VerifiedAuditCycle:
    authority: AuditCycleAuthority
    report: PlanDispositionReport
    inventory_requirement_ids: tuple[str, ...]
    current_plan_text: str


def _reject(reason: AdmissionReason, detail: str) -> InventoryAdmissionDecision:
    return InventoryAdmissionDecision.reject(reason, detail)


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


def _parse_requirements_map(markdown: str) -> tuple[PlanDispositionRow, ...]:
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


def _implementation_step_blocks(markdown: str) -> dict[str, str]:
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


class InventoryAdmissionEvaluator:
    """Pure, total evaluator for one verified authority/report/plan tuple."""

    def evaluate(
        self,
        *,
        authority: AuditCycleAuthority | None,
        trusted_head: AuditCycleHead | None,
        report: PlanDispositionReport | None,
        expected_generation: str,
        expected_plan_set_id: str,
        expected_scope_id: str,
        expected_part_id: str,
        current_plan_ref: ArtifactRef | None = None,
        inventory_requirement_ids: tuple[str, ...] = (),
        current_plan_text: str = "",
    ) -> InventoryAdmissionDecision:
        try:
            return self._evaluate(
                authority=authority,
                trusted_head=trusted_head,
                report=report,
                expected_generation=expected_generation,
                expected_plan_set_id=expected_plan_set_id,
                expected_scope_id=expected_scope_id,
                expected_part_id=expected_part_id,
                current_plan_ref=current_plan_ref,
                inventory_requirement_ids=inventory_requirement_ids,
                current_plan_text=current_plan_text,
            )
        except Exception as exc:
            return _reject(AdmissionReason.INTERNAL_ERROR, f"inventory admission failed: {exc}")

    def _evaluate(
        self,
        *,
        authority: AuditCycleAuthority | None,
        trusted_head: AuditCycleHead | None,
        report: PlanDispositionReport | None,
        expected_generation: str,
        expected_plan_set_id: str,
        expected_scope_id: str,
        expected_part_id: str,
        current_plan_ref: ArtifactRef | None,
        inventory_requirement_ids: tuple[str, ...],
        current_plan_text: str,
    ) -> InventoryAdmissionDecision:
        if authority is None:
            if report is not None:
                return _reject(
                    AdmissionReason.REPORT_WITHOUT_AUTHORITY,
                    "a disposition report cannot activate without authority",
                )
            return InventoryAdmissionDecision.omit(AdmissionReason.NO_AUTHORITY)
        if trusted_head is None:
            return _reject(AdmissionReason.HEAD_MISSING, "trusted audit-cycle head is absent")
        if authority.authority_digest != trusted_head.current_authority_digest:
            return _reject(
                AdmissionReason.AUTHORITY_NOT_CURRENT,
                "authority is not the trusted current head",
            )
        if authority.execution_generation != trusted_head.execution_generation:
            return _reject(
                AdmissionReason.GENERATION_MISMATCH,
                "authority and trusted head generations differ",
            )
        if authority.cycle_id != trusted_head.cycle_id:
            return _reject(AdmissionReason.CYCLE_MISMATCH, "authority and head cycle IDs differ")
        if authority.plan_set_id != trusted_head.plan_set_id:
            return _reject(
                AdmissionReason.PLAN_SET_MISMATCH,
                "authority and head plan-set IDs differ",
            )
        if authority.scope_id != trusted_head.scope_id:
            return _reject(AdmissionReason.SCOPE_MISMATCH, "authority and head scope IDs differ")
        if authority.part_id != trusted_head.part_id:
            return _reject(AdmissionReason.PART_MISMATCH, "authority and head part IDs differ")
        if authority.audit_round != trusted_head.audit_round:
            return _reject(AdmissionReason.ROUND_MISMATCH, "authority and head rounds differ")
        if authority.verdict is not trusted_head.verdict:
            return _reject(
                AdmissionReason.AUTHORITY_NOT_CURRENT,
                "authority and trusted head verdicts differ",
            )
        if authority.execution_generation != expected_generation:
            return _reject(
                AdmissionReason.GENERATION_MISMATCH,
                "authority is from another execution generation",
            )
        if authority.plan_set_id != expected_plan_set_id:
            return _reject(
                AdmissionReason.PLAN_SET_MISMATCH,
                "authority is from another plan set",
            )
        if authority.scope_id != expected_scope_id:
            return _reject(AdmissionReason.SCOPE_MISMATCH, "authority is from another scope")
        if authority.verdict is AuditVerdict.GO:
            if expected_part_id == authority.part_id:
                return InventoryAdmissionDecision.omit(AdmissionReason.TRUSTED_GO)
            if expected_part_id == trusted_head.authorized_successor_part_id:
                return InventoryAdmissionDecision.omit(AdmissionReason.TRUSTED_GO_SUCCESSOR)
            return _reject(
                AdmissionReason.PART_MISMATCH,
                "GO authority does not authorize this successor part",
            )
        if authority.part_id != expected_part_id:
            return _reject(
                AdmissionReason.PART_MISMATCH,
                "NO GO authority is from another part",
            )
        if report is None:
            return _reject(
                AdmissionReason.AUTHORITY_WITHOUT_REPORT,
                "current NO GO authority requires a disposition report",
            )
        provenance_checks = (
            (
                report.execution_generation == authority.execution_generation,
                AdmissionReason.GENERATION_MISMATCH,
                "report generation differs from authority",
            ),
            (
                report.cycle_id == authority.cycle_id,
                AdmissionReason.CYCLE_MISMATCH,
                "report cycle differs from authority",
            ),
            (
                report.plan_set_id == authority.plan_set_id,
                AdmissionReason.PLAN_SET_MISMATCH,
                "report plan set differs from authority",
            ),
            (
                report.scope_id == authority.scope_id,
                AdmissionReason.SCOPE_MISMATCH,
                "report scope differs from authority",
            ),
            (
                report.part_id == authority.part_id,
                AdmissionReason.PART_MISMATCH,
                "report part differs from authority",
            ),
            (
                report.audit_round == authority.audit_round,
                AdmissionReason.ROUND_MISMATCH,
                "report round differs from authority",
            ),
            (
                report.parent_authority_digest == authority.authority_digest,
                AdmissionReason.PARENT_MISMATCH,
                "report is not bound to this authority",
            ),
            (
                report.inventory_digest == authority.inventory_ref.content_digest,
                AdmissionReason.INVENTORY_MISMATCH,
                "report inventory differs from authority",
            ),
            (
                report.findings_digest == authority.findings_digest,
                AdmissionReason.FINDINGS_MISMATCH,
                "report findings differ from authority",
            ),
        )
        for matches, reason, detail in provenance_checks:
            if not matches:
                return _reject(reason, detail)
        if current_plan_ref is None:
            return _reject(AdmissionReason.PLAN_MISMATCH, "current plan is unverified")
        if report.current_plan_ref.content_digest != current_plan_ref.content_digest:
            return _reject(
                AdmissionReason.PLAN_MISMATCH,
                "report is bound to another current plan",
            )
        if (
            not inventory_requirement_ids
            or len(inventory_requirement_ids) != len(set(inventory_requirement_ids))
            or any(not isinstance(item, str) or not item for item in inventory_requirement_ids)
        ):
            return _reject(
                AdmissionReason.INVENTORY_INVALID,
                "inventory requirement IDs must be non-empty and unique",
            )
        assessment_ids = tuple(row.requirement_id for row in authority.assessments)
        report_ids = tuple(row.requirement_id for row in report.dispositions)
        if assessment_ids != inventory_requirement_ids or report_ids != inventory_requirement_ids:
            return _reject(
                AdmissionReason.REQUIREMENT_ORDER_MISMATCH,
                "inventory, assessment, and disposition IDs/order must match exactly",
            )
        try:
            plan_rows = _parse_requirements_map(current_plan_text)
        except ValueError as exc:
            return _reject(AdmissionReason.REQUIREMENTS_MAP_INVALID, str(exc))
        if tuple(row.requirement_id for row in plan_rows) != inventory_requirement_ids:
            return _reject(
                AdmissionReason.REQUIREMENT_ORDER_MISMATCH,
                "Requirements Map IDs/order differ from inventory",
            )
        if plan_rows != report.dispositions:
            return _reject(
                AdmissionReason.DISPOSITION_MISMATCH,
                "Requirements Map and disposition report rows differ",
            )
        try:
            step_blocks = _implementation_step_blocks(current_plan_text)
        except ValueError as exc:
            if any(row.disposition == "carried@step" for row in plan_rows):
                return _reject(AdmissionReason.IMPLEMENTATION_STEP_MISSING, str(exc))
            step_blocks = {}
        for assessment, disposition in zip(authority.assessments, plan_rows, strict=True):
            if assessment.assessment.blocking:
                if disposition.disposition != "carried@step":
                    return _reject(
                        AdmissionReason.UNMAPPED_REQUIREMENT,
                        f"{assessment.requirement_id} is blocking but not carried",
                    )
                step = disposition.implementation_step
                block = step_blocks.get(step or "")
                if (
                    block is None
                    or re.search(
                        rf"(?<![A-Za-z0-9_-]){re.escape(assessment.requirement_id)}"
                        r"(?![A-Za-z0-9_-])",
                        block,
                    )
                    is None
                ):
                    return _reject(
                        AdmissionReason.IMPLEMENTATION_STEP_MISSING,
                        f"{assessment.requirement_id} is not cited by {step!r}",
                    )
            else:
                if disposition.satisfied_round != authority.audit_round:
                    return _reject(
                        AdmissionReason.SATISFIED_ROUND_MISMATCH,
                        f"{assessment.requirement_id} must be satisfied-by-round-"
                        f"{authority.audit_round}",
                    )
        return InventoryAdmissionDecision.admitted(report.dispositions)


class AuditCycleVerifier:
    """Imperative bounded-I/O verifier feeding the pure evaluator."""

    def __init__(
        self,
        allowed_root: Path,
        *,
        max_size_bytes: int = 10_000_000,
        reader: ArtifactByteReader = read_stable_contained_bytes,
    ) -> None:
        self._allowed_root = allowed_root
        self._max_size_bytes = max_size_bytes
        self._reader = reader

    def _read_path(self, path: str | Path) -> bytes:
        try:
            _, data = self._reader(
                path,
                self._allowed_root,
                max_size_bytes=self._max_size_bytes,
            )
        except (ContainmentError, OSError) as exc:
            raise AuditCycleVerificationError(
                AdmissionReason.INVENTORY_INVALID,
                f"artifact containment/read failed: {exc}",
            ) from exc
        return data

    def verify_artifact_ref(self, ref: ArtifactRef) -> bytes:
        data = self._read_path(ref.locator)
        if len(data) != ref.byte_size:
            raise AuditCycleVerificationError(
                AdmissionReason.INVENTORY_MISMATCH,
                "artifact byte size differs from its reference",
            )
        if compute_bytes_hash(data) != ref.content_digest:
            raise AuditCycleVerificationError(
                AdmissionReason.INVENTORY_MISMATCH,
                "artifact content digest differs from its reference",
            )
        return data

    def load_authority(self, path: str | Path) -> AuditCycleAuthority:
        data = self._read_path(path)
        raw = decode_versioned_json_bytes(
            data,
            expected_version=AUDIT_CYCLE_SCHEMA_VERSION,
            require_canonical=True,
        )
        if raw is None:
            raise AuditCycleVerificationError(
                AdmissionReason.AUTHORITY_NOT_CURRENT,
                "authority is not strict canonical versioned JSON",
            )
        try:
            return AuditCycleAuthority.from_dict(raw)
        except ValueError as exc:
            raise AuditCycleVerificationError(
                AdmissionReason.AUTHORITY_NOT_CURRENT,
                f"authority validation failed: {exc}",
            ) from exc

    def load_report(self, path: str | Path) -> PlanDispositionReport:
        data = self._read_path(path)
        raw = decode_versioned_json_bytes(
            data,
            expected_version=AUDIT_CYCLE_SCHEMA_VERSION,
            require_canonical=True,
        )
        if raw is None:
            raise AuditCycleVerificationError(
                AdmissionReason.DISPOSITION_MISMATCH,
                "disposition report is not strict canonical versioned JSON",
            )
        try:
            return PlanDispositionReport.from_dict(raw)
        except ValueError as exc:
            raise AuditCycleVerificationError(
                AdmissionReason.DISPOSITION_MISMATCH,
                f"disposition report validation failed: {exc}",
            ) from exc

    @staticmethod
    def verify_successor(candidate: AuditCycleAuthority, trusted_head: AuditCycleHead) -> None:
        if candidate.execution_generation != trusted_head.execution_generation:
            raise AuditCycleVerificationError(
                AdmissionReason.GENERATION_MISMATCH,
                "successor authority crosses execution generations",
            )
        if candidate.cycle_id != trusted_head.cycle_id:
            raise AuditCycleVerificationError(
                AdmissionReason.CYCLE_MISMATCH,
                "successor authority crosses cycle identity",
            )
        if (
            candidate.plan_set_id != trusted_head.plan_set_id
            or candidate.scope_id != trusted_head.scope_id
            or candidate.part_id != trusted_head.part_id
        ):
            raise AuditCycleVerificationError(
                AdmissionReason.SCOPE_MISMATCH,
                "successor authority crosses plan/scope/part identity",
            )
        if candidate.audit_round != trusted_head.audit_round + 1:
            raise AuditCycleVerificationError(
                AdmissionReason.ROUND_MISMATCH,
                "successor authority round is not monotonic",
            )
        if candidate.parent_authority_digest != trusted_head.current_authority_digest:
            raise AuditCycleVerificationError(
                AdmissionReason.PARENT_MISMATCH,
                "successor parent is not the trusted current head",
            )

    def verify_active_tuple(
        self,
        *,
        authority_path: str | Path,
        report_path: str | Path,
        trusted_head: AuditCycleHead,
        current_plan_path: str | Path,
    ) -> VerifiedAuditCycle:
        authority = self.load_authority(authority_path)
        return self._verify_active_tuple(
            authority=authority,
            report_path=report_path,
            trusted_head=trusted_head,
            current_plan_path=current_plan_path,
        )

    def _verify_active_tuple(
        self,
        *,
        authority: AuditCycleAuthority,
        report_path: str | Path,
        trusted_head: AuditCycleHead,
        current_plan_path: str | Path,
    ) -> VerifiedAuditCycle:
        if authority.authority_digest != trusted_head.current_authority_digest:
            raise AuditCycleVerificationError(
                AdmissionReason.AUTHORITY_NOT_CURRENT,
                "authority is stale or replayed",
            )
        report = self.load_report(report_path)
        provenance = InventoryAdmissionEvaluator().evaluate(
            authority=authority,
            trusted_head=trusted_head,
            report=report,
            expected_generation=trusted_head.execution_generation,
            expected_plan_set_id=trusted_head.plan_set_id,
            expected_scope_id=trusted_head.scope_id,
            expected_part_id=trusted_head.part_id,
            current_plan_ref=report.current_plan_ref,
        )
        if provenance.reason is not AdmissionReason.INVENTORY_INVALID:
            raise AuditCycleVerificationError(
                provenance.reason,
                provenance.details[0]
                if provenance.details
                else "audit-cycle provenance verification failed",
            )
        if Path(report.current_plan_ref.locator) != Path(current_plan_path):
            raise AuditCycleVerificationError(
                AdmissionReason.PLAN_MISMATCH,
                "report current-plan locator differs from bound plan path",
            )
        plan_bytes = self.verify_artifact_ref(report.current_plan_ref)
        try:
            plan_text = plan_bytes.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise AuditCycleVerificationError(
                AdmissionReason.PLAN_MISMATCH, "current plan is not UTF-8"
            ) from exc
        for audited_plan_ref in authority.audited_plan_refs:
            self.verify_artifact_ref(audited_plan_ref)
        if authority.remediation_ref is None:
            raise AuditCycleVerificationError(
                AdmissionReason.AUTHORITY_NOT_CURRENT,
                "active NO GO authority has no remediation artifact",
            )
        self.verify_artifact_ref(authority.remediation_ref)
        inventory_bytes = self.verify_artifact_ref(authority.inventory_ref)
        inventory_raw = decode_versioned_json_bytes(
            inventory_bytes,
            expected_version=authority.inventory_ref.schema_version,
            require_canonical=True,
        )
        if inventory_raw is None:
            raise AuditCycleVerificationError(
                AdmissionReason.INVENTORY_INVALID,
                "inventory is not strict canonical versioned JSON",
            )
        try:
            requirement_ids_raw = inventory_raw["requirement_ids"]
            requirements_raw = inventory_raw["requirements"]
            requirement_ids = tuple(requirement_ids_raw)
            row_ids = tuple(item["id"] for item in requirements_raw)
        except (KeyError, TypeError) as exc:
            raise AuditCycleVerificationError(
                AdmissionReason.INVENTORY_INVALID,
                f"inventory schema is invalid: {exc}",
            ) from exc
        if requirement_ids != row_ids:
            raise AuditCycleVerificationError(
                AdmissionReason.INVENTORY_INVALID,
                "inventory requirement_ids and requirements order differ",
            )
        if any(not isinstance(item, str) or not item for item in requirement_ids):
            raise AuditCycleVerificationError(
                AdmissionReason.INVENTORY_INVALID,
                "inventory requirement IDs must be non-empty strings",
            )
        return VerifiedAuditCycle(
            authority=authority,
            report=report,
            inventory_requirement_ids=requirement_ids,
            current_plan_text=plan_text,
        )

    def evaluate_paths(
        self,
        *,
        authority_path: str | Path | None,
        report_path: str | Path | None,
        trusted_head: AuditCycleHead | None,
        current_plan_path: str | Path,
        expected_generation: str,
        expected_plan_set_id: str,
        expected_scope_id: str,
        expected_part_id: str,
    ) -> InventoryAdmissionDecision:
        evaluator = InventoryAdmissionEvaluator()
        if authority_path is None:
            return evaluator.evaluate(
                authority=None,
                trusted_head=trusted_head,
                report=None if report_path is None else self.load_report(report_path),
                expected_generation=expected_generation,
                expected_plan_set_id=expected_plan_set_id,
                expected_scope_id=expected_scope_id,
                expected_part_id=expected_part_id,
            )
        try:
            authority = self.load_authority(authority_path)
            if authority.verdict is AuditVerdict.GO:
                return evaluator.evaluate(
                    authority=authority,
                    trusted_head=trusted_head,
                    report=None,
                    expected_generation=expected_generation,
                    expected_plan_set_id=expected_plan_set_id,
                    expected_scope_id=expected_scope_id,
                    expected_part_id=expected_part_id,
                )
            if report_path is None or trusted_head is None:
                return evaluator.evaluate(
                    authority=authority,
                    trusted_head=trusted_head,
                    report=None,
                    expected_generation=expected_generation,
                    expected_plan_set_id=expected_plan_set_id,
                    expected_scope_id=expected_scope_id,
                    expected_part_id=expected_part_id,
                )
            verified = self._verify_active_tuple(
                authority=authority,
                report_path=report_path,
                trusted_head=trusted_head,
                current_plan_path=current_plan_path,
            )
            return evaluator.evaluate(
                authority=verified.authority,
                trusted_head=trusted_head,
                report=verified.report,
                expected_generation=expected_generation,
                expected_plan_set_id=expected_plan_set_id,
                expected_scope_id=expected_scope_id,
                expected_part_id=expected_part_id,
                current_plan_ref=verified.report.current_plan_ref,
                inventory_requirement_ids=verified.inventory_requirement_ids,
                current_plan_text=verified.current_plan_text,
            )
        except AuditCycleVerificationError as exc:
            return _reject(exc.reason, str(exc))
