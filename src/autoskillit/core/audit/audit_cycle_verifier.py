from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

from ..io.io import decode_versioned_json_bytes
from ..io.path_containment import ContainmentError, read_stable_contained_bytes
from ..logging import get_logger
from ..types._type_audit_artifact_ref import ArtifactRef
from ..types._type_audit_cycle_authority import (
    AUDIT_CYCLE_SCHEMA_VERSION,
    AuditCycleAuthority,
    AuditCycleHead,
    AuditVerdict,
)
from ..types._type_audit_cycle_disposition import (
    AdmissionReason,
    AuditFindingWaiver,
    InventoryAdmissionDecision,
    PlanDispositionReport,
)
from .audit_semantic_codec import (
    implementation_step_blocks,
    load_audit_finding_waivers,
    parse_requirements_map,
    validate_waiver_rows,
)
from .closure_hashing import compute_bytes_hash

__all__ = [
    "ArtifactByteReader",
    "AuditCycleVerificationError",
    "AuditCycleVerifier",
    "InventoryAdmissionEvaluator",
    "VerifiedAuditCycle",
]

logger = get_logger(__name__)


@runtime_checkable
class ArtifactByteReader(Protocol):
    def __call__(
        self, path: str | Path, allowed_root: str | Path, *, max_size_bytes: int
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
        waivers: tuple[AuditFindingWaiver, ...] = (),
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
                waivers=waivers,
            )
        except Exception as exc:
            logger.error("inventory admission evaluation failed", exc_info=True)
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
        waivers: tuple[AuditFindingWaiver, ...],
    ) -> InventoryAdmissionDecision:
        authority_decision = self._verify_authority_consistency(
            authority=authority,
            trusted_head=trusted_head,
            report=report,
            expected_generation=expected_generation,
            expected_plan_set_id=expected_plan_set_id,
            expected_scope_id=expected_scope_id,
        )
        if authority_decision is not None:
            return authority_decision

        assert authority is not None
        assert trusted_head is not None
        verdict_decision = self._evaluate_go_successor_or_no_go(
            authority=authority,
            trusted_head=trusted_head,
            expected_part_id=expected_part_id,
        )
        if verdict_decision is not None:
            return verdict_decision

        return self._verify_no_go(
            authority=authority,
            report=report,
            current_plan_ref=current_plan_ref,
            inventory_requirement_ids=inventory_requirement_ids,
            current_plan_text=current_plan_text,
            waivers=waivers,
        )

    @staticmethod
    def _verify_authority_consistency(
        *,
        authority: AuditCycleAuthority | None,
        trusted_head: AuditCycleHead | None,
        report: PlanDispositionReport | None,
        expected_generation: str,
        expected_plan_set_id: str,
        expected_scope_id: str,
    ) -> InventoryAdmissionDecision | None:
        if authority is None:
            if report is not None:
                return _reject(
                    AdmissionReason.REPORT_WITHOUT_AUTHORITY,
                    "a disposition report cannot activate without authority",
                )
            return InventoryAdmissionDecision.omit(AdmissionReason.NO_AUTHORITY)
        if trusted_head is None:
            return _reject(AdmissionReason.HEAD_MISSING, "trusted audit-cycle head is absent")
        for (reason, detail), failed in (
            (
                (
                    AdmissionReason.AUTHORITY_NOT_CURRENT,
                    "authority is not the trusted current head",
                ),
                authority.authority_digest != trusted_head.current_authority_digest,
            ),
            (
                (
                    AdmissionReason.GENERATION_MISMATCH,
                    "authority and trusted head generations differ",
                ),
                authority.execution_generation != trusted_head.execution_generation,
            ),
            (
                (AdmissionReason.CYCLE_MISMATCH, "authority and head cycle IDs differ"),
                authority.cycle_id != trusted_head.cycle_id,
            ),
            (
                (AdmissionReason.PLAN_SET_MISMATCH, "authority and head plan-set IDs differ"),
                authority.plan_set_id != trusted_head.plan_set_id,
            ),
            (
                (AdmissionReason.SCOPE_MISMATCH, "authority and head scope IDs differ"),
                authority.scope_id != trusted_head.scope_id,
            ),
            (
                (AdmissionReason.PART_MISMATCH, "authority and head part IDs differ"),
                authority.part_id != trusted_head.part_id,
            ),
            (
                (AdmissionReason.ROUND_MISMATCH, "authority and head rounds differ"),
                authority.audit_round != trusted_head.audit_round,
            ),
            (
                (
                    AdmissionReason.AUTHORITY_NOT_CURRENT,
                    "authority and trusted head verdicts differ",
                ),
                authority.verdict is not trusted_head.verdict,
            ),
            (
                (
                    AdmissionReason.GENERATION_MISMATCH,
                    "authority is from another execution generation",
                ),
                authority.execution_generation != expected_generation,
            ),
            (
                (AdmissionReason.PLAN_SET_MISMATCH, "authority is from another plan set"),
                authority.plan_set_id != expected_plan_set_id,
            ),
            (
                (AdmissionReason.SCOPE_MISMATCH, "authority is from another scope"),
                authority.scope_id != expected_scope_id,
            ),
        ):
            if failed:
                return _reject(reason, detail)

        return None

    @staticmethod
    def _evaluate_go_successor_or_no_go(
        *,
        authority: AuditCycleAuthority,
        trusted_head: AuditCycleHead,
        expected_part_id: str,
    ) -> InventoryAdmissionDecision | None:
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

        return None

    @staticmethod
    def _verify_no_go(
        *,
        authority: AuditCycleAuthority,
        report: PlanDispositionReport | None,
        current_plan_ref: ArtifactRef | None,
        inventory_requirement_ids: tuple[str, ...],
        current_plan_text: str,
        waivers: tuple[AuditFindingWaiver, ...],
    ) -> InventoryAdmissionDecision:
        if report is None:
            return _reject(
                AdmissionReason.AUTHORITY_WITHOUT_REPORT,
                "current NO GO authority requires a disposition report",
            )
        failure = next(
            (
                (reason, detail)
                for (actual, expected), reason, detail in (
                    (
                        (report.execution_generation, authority.execution_generation),
                        AdmissionReason.GENERATION_MISMATCH,
                        "report generation differs from authority",
                    ),
                    (
                        (report.cycle_id, authority.cycle_id),
                        AdmissionReason.CYCLE_MISMATCH,
                        "report cycle differs from authority",
                    ),
                    (
                        (report.plan_set_id, authority.plan_set_id),
                        AdmissionReason.PLAN_SET_MISMATCH,
                        "report plan set differs from authority",
                    ),
                    (
                        (report.scope_id, authority.scope_id),
                        AdmissionReason.SCOPE_MISMATCH,
                        "report scope differs from authority",
                    ),
                    (
                        (report.part_id, authority.part_id),
                        AdmissionReason.PART_MISMATCH,
                        "report part differs from authority",
                    ),
                    (
                        (report.audit_round, authority.audit_round),
                        AdmissionReason.ROUND_MISMATCH,
                        "report round differs from authority",
                    ),
                    (
                        (report.parent_authority_digest, authority.authority_digest),
                        AdmissionReason.PARENT_MISMATCH,
                        "report is not bound to this authority",
                    ),
                    (
                        (report.inventory_digest, authority.inventory_ref.content_digest),
                        AdmissionReason.INVENTORY_MISMATCH,
                        "report inventory differs from authority",
                    ),
                    (
                        (report.findings_digest, authority.findings_digest),
                        AdmissionReason.FINDINGS_MISMATCH,
                        "report findings differ from authority",
                    ),
                )
                if actual != expected
            ),
            None,
        )
        if failure is not None:
            return _reject(*failure)
        if current_plan_ref is None or (
            report.current_plan_ref.content_digest != current_plan_ref.content_digest
        ):
            return _reject(
                AdmissionReason.PLAN_MISMATCH,
                "current plan is unverified"
                if current_plan_ref is None
                else "report is bound to another current plan",
            )
        inventory_is_invalid = (
            not inventory_requirement_ids
            or len(inventory_requirement_ids) != len(set(inventory_requirement_ids))
            or any(not isinstance(item, str) or not item for item in inventory_requirement_ids)
        )
        assessment_ids = tuple(row.requirement_id for row in authority.assessments)
        report_ids = tuple(row.requirement_id for row in report.dispositions)
        inventory_ids_are_misaligned = (assessment_ids, report_ids) != (
            inventory_requirement_ids,
            inventory_requirement_ids,
        )
        if inventory_is_invalid or inventory_ids_are_misaligned:
            return _reject(
                AdmissionReason.INVENTORY_INVALID
                if inventory_is_invalid
                else AdmissionReason.REQUIREMENT_ORDER_MISMATCH,
                "inventory requirement IDs must be non-empty and unique"
                if inventory_is_invalid
                else "inventory, assessment, and disposition IDs/order must match exactly",
            )
        try:
            plan_rows = parse_requirements_map(current_plan_text)
        except ValueError as exc:
            return _reject(AdmissionReason.REQUIREMENTS_MAP_INVALID, str(exc))
        plan_requirement_ids = tuple(row.requirement_id for row in plan_rows)
        plan_rows_are_aligned = (plan_requirement_ids, plan_rows) == (
            inventory_requirement_ids,
            report.dispositions,
        )
        if not plan_rows_are_aligned:
            return _reject(
                AdmissionReason.REQUIREMENT_ORDER_MISMATCH
                if plan_requirement_ids != inventory_requirement_ids
                else AdmissionReason.DISPOSITION_MISMATCH,
                "Requirements Map IDs/order differ from inventory"
                if plan_requirement_ids != inventory_requirement_ids
                else "Requirements Map and disposition report rows differ",
            )
        waiver_issue, legacy_rows = validate_waiver_rows(
            authority=authority,
            plan_rows=plan_rows,
            waivers=waivers,
        )
        if waiver_issue is not None:
            return waiver_issue
        try:
            step_blocks = (
                implementation_step_blocks(current_plan_text)
                if any(row.disposition == "carried@step" for row in plan_rows)
                else {}
            )
        except ValueError as exc:
            return _reject(AdmissionReason.IMPLEMENTATION_STEP_MISSING, str(exc))
        for assessment, disposition in legacy_rows:
            step = disposition.implementation_step
            blocking = assessment.assessment.blocking
            carried = disposition.disposition == "carried@step"
            block = step_blocks.get(step or "") if blocking and carried else None
            citation_is_missing = (
                blocking
                and carried
                and (
                    block is None
                    or re.search(
                        rf"(?<![A-Za-z0-9_-]){re.escape(assessment.requirement_id)}"
                        r"(?![A-Za-z0-9_-])",
                        block,
                    )
                    is None
                )
            )
            row_is_invalid = (blocking and (not carried or citation_is_missing)) or (
                not blocking and disposition.satisfied_round != authority.audit_round
            )
            if row_is_invalid:
                reason, detail = (
                    (
                        AdmissionReason.UNMAPPED_REQUIREMENT,
                        f"{assessment.requirement_id} is blocking but not carried",
                    )
                    if blocking and not carried
                    else (
                        (
                            AdmissionReason.IMPLEMENTATION_STEP_MISSING,
                            f"{assessment.requirement_id} is not cited by {step!r}",
                        )
                        if blocking
                        else (
                            AdmissionReason.SATISFIED_ROUND_MISMATCH,
                            f"{assessment.requirement_id} must be satisfied-by-round-"
                            f"{authority.audit_round}",
                        )
                    )
                )
                return _reject(reason, detail)
        return InventoryAdmissionDecision.admitted(report.dispositions)


class AuditCycleVerifier:
    """Imperative bounded-I/O verifier feeding the pure evaluator."""

    def __init__(
        self,
        allowed_root: Path,
        *,
        max_size_bytes: int = 10_000_000,
        reader: ArtifactByteReader = read_stable_contained_bytes,
        waiver_root: Path | None = None,
    ) -> None:
        self._allowed_root = allowed_root
        self._max_size_bytes = max_size_bytes
        self._reader = reader
        self._waiver_root = waiver_root

    def _read_path(self, path: str | Path) -> bytes:
        try:
            _, data = self._reader(path, self._allowed_root, max_size_bytes=self._max_size_bytes)
        except (ContainmentError, OSError) as exc:
            raise AuditCycleVerificationError(
                AdmissionReason.INVENTORY_INVALID, f"artifact containment/read failed: {exc}"
            ) from exc
        return data

    def _load_waivers(self) -> tuple[AuditFindingWaiver, ...]:
        try:
            return load_audit_finding_waivers(
                waiver_root=self._waiver_root,
                reader=self._reader,
                max_size_bytes=self._max_size_bytes,
            )
        except ValueError as exc:
            raise AuditCycleVerificationError(
                AdmissionReason.WAIVER_LEDGER_INVALID,
                str(exc),
            ) from exc

    def verify_artifact_ref(self, ref: ArtifactRef) -> bytes:
        data = self._read_path(ref.locator)
        if len(data) != ref.byte_size:
            raise AuditCycleVerificationError(
                AdmissionReason.INVENTORY_MISMATCH, "artifact byte size differs from its reference"
            )
        if compute_bytes_hash(data) != ref.content_digest:
            raise AuditCycleVerificationError(
                AdmissionReason.INVENTORY_MISMATCH,
                "artifact content digest differs from its reference",
            )
        return data

    def load_authority(self, path: str | Path) -> AuditCycleAuthority:
        return self.decode_authority(self._read_path(path))

    def decode_authority(self, data: bytes) -> AuditCycleAuthority:
        """Decode authority bytes through the strict canonical verifier boundary."""
        raw = decode_versioned_json_bytes(
            data, expected_version=AUDIT_CYCLE_SCHEMA_VERSION, require_canonical=True
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
                AdmissionReason.AUTHORITY_NOT_CURRENT, f"authority validation failed: {exc}"
            ) from exc

    def load_report(self, path: str | Path) -> PlanDispositionReport:
        data = self._read_path(path)
        raw = decode_versioned_json_bytes(
            data, expected_version=AUDIT_CYCLE_SCHEMA_VERSION, require_canonical=True
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
                AdmissionReason.CYCLE_MISMATCH, "successor authority crosses cycle identity"
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
                AdmissionReason.ROUND_MISMATCH, "successor authority round is not monotonic"
            )
        if candidate.parent_authority_digest != trusted_head.current_authority_digest:
            raise AuditCycleVerificationError(
                AdmissionReason.PARENT_MISMATCH, "successor parent is not the trusted current head"
            )
        if candidate.inventory_ref != trusted_head.inventory_ref:
            raise AuditCycleVerificationError(
                AdmissionReason.INVENTORY_MISMATCH,
                "successor inventory identity differs from the trusted head",
            )
        if candidate.audited_plan_refs != trusted_head.audited_plan_refs:
            raise AuditCycleVerificationError(
                AdmissionReason.PLAN_MISMATCH,
                "successor audited-plan identities differ from the trusted head",
            )

    def verify_active_tuple(
        self,
        *,
        authority_path: str | Path,
        report_path: str | Path,
        trusted_head: AuditCycleHead,
        current_plan_path: str | Path,
    ) -> VerifiedAuditCycle:
        return self._verify_active_tuple(
            authority=self.load_authority(authority_path),
            report_path=report_path,
            trusted_head=trusted_head,
            current_plan_path=current_plan_path,
            waivers=self._load_waivers(),
        )

    def _verify_active_tuple(
        self,
        *,
        authority: AuditCycleAuthority,
        report_path: str | Path,
        trusted_head: AuditCycleHead,
        current_plan_path: str | Path,
        waivers: tuple[AuditFindingWaiver, ...] = (),
    ) -> VerifiedAuditCycle:
        if authority.authority_digest != trusted_head.current_authority_digest:
            raise AuditCycleVerificationError(
                AdmissionReason.AUTHORITY_NOT_CURRENT, "authority is stale or replayed"
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
            waivers=waivers,
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
        try:
            plan_text = self.verify_artifact_ref(report.current_plan_ref).decode(
                "utf-8", errors="strict"
            )
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
        requirement_ids = self._decode_inventory_requirement_ids(
            self.verify_artifact_ref(authority.inventory_ref),
            schema_version=authority.inventory_ref.schema_version,
        )
        return VerifiedAuditCycle(
            authority=authority,
            report=report,
            inventory_requirement_ids=requirement_ids,
            current_plan_text=plan_text,
        )

    @staticmethod
    def _decode_inventory_requirement_ids(
        inventory_bytes: bytes, *, schema_version: int
    ) -> tuple[str, ...]:
        inventory_raw = decode_versioned_json_bytes(
            inventory_bytes, expected_version=schema_version, require_canonical=True
        )
        if inventory_raw is None:
            raise AuditCycleVerificationError(
                AdmissionReason.INVENTORY_INVALID,
                "inventory is not strict canonical versioned JSON",
            )
        try:
            requirement_ids_raw = inventory_raw["requirement_ids"]
            requirements_raw = inventory_raw["requirements"]
            if not isinstance(requirement_ids_raw, list) or not isinstance(requirements_raw, list):
                raise TypeError("requirement_ids and requirements must be arrays")
            requirement_ids = tuple(requirement_ids_raw)
            row_ids = tuple(item["id"] for item in requirements_raw)
        except (KeyError, TypeError) as exc:
            raise AuditCycleVerificationError(
                AdmissionReason.INVENTORY_INVALID, f"inventory schema is invalid: {exc}"
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
        return requirement_ids

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
            if report_path is not None:
                return _reject(
                    AdmissionReason.REPORT_WITHOUT_AUTHORITY,
                    "a disposition report cannot activate without authority",
                )
            return evaluator.evaluate(
                authority=None,
                trusted_head=trusted_head,
                report=None,
                expected_generation=expected_generation,
                expected_plan_set_id=expected_plan_set_id,
                expected_scope_id=expected_scope_id,
                expected_part_id=expected_part_id,
            )
        try:
            authority = self.load_authority(authority_path)
            if authority.verdict is AuditVerdict.GO or report_path is None or trusted_head is None:
                return evaluator.evaluate(
                    authority=authority,
                    trusted_head=trusted_head,
                    report=None,
                    expected_generation=expected_generation,
                    expected_plan_set_id=expected_plan_set_id,
                    expected_scope_id=expected_scope_id,
                    expected_part_id=expected_part_id,
                )
            waivers = self._load_waivers()
            verified = self._verify_active_tuple(
                authority=authority,
                report_path=report_path,
                trusted_head=trusted_head,
                current_plan_path=current_plan_path,
                waivers=waivers,
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
                waivers=waivers,
            )
        except AuditCycleVerificationError as exc:
            return _reject(exc.reason, str(exc))
