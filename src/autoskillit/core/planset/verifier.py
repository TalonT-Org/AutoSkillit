"""Fail-closed verification for persisted plan-set authority artifacts."""

from __future__ import annotations

from pathlib import Path

from ..closure_hashing import (
    compute_bytes_hash,
    compute_canonical_hash,
    parse_canonical_json_bytes,
)
from ..io.path_containment import ContainmentError, read_stable_contained_bytes
from ..types._type_plan_set_authority import (
    PLAN_SET_AUTHORITY_DOMAIN,
    PLAN_SET_MAX_AUTHORITY_BYTES,
    PLAN_SET_MAX_ISSUE_BYTES,
    PLAN_SET_MAX_PART_BYTES,
    PLAN_SET_SCHEMA_VERSION,
    PlanSetAuthority,
    PlanSetPreflightEvidence,
    PlanSetRejectReason,
    PlanSetState,
    PlanSetVerification,
)
from .coverage import assigned_requirements

__all__ = ["verify_plan_set_authority"]


def _rejection(
    reason: PlanSetRejectReason,
    detail: list[str],
) -> PlanSetVerification:
    return PlanSetVerification(False, reason, tuple(detail), None, None)


def verify_plan_set_authority(
    authority_path: str | Path,
    *,
    allowed_root: str | Path,
    expected_execution_generation: str | None,
    expected_kitchen_id: str | None,
    current_plan_path: str | Path | None,
    require_sealed: bool,
) -> PlanSetVerification:
    """Verify canonical authority bytes and every artifact they bind, without trust-on-read."""
    details: list[str] = []
    try:
        resolved_authority, raw = read_stable_contained_bytes(
            authority_path,
            allowed_root,
            max_size_bytes=PLAN_SET_MAX_AUTHORITY_BYTES,
        )
    except (ContainmentError, OSError) as exc:
        return _rejection(PlanSetRejectReason.CONTAINMENT, [str(exc)])
    try:
        payload = parse_canonical_json_bytes(raw)
    except ValueError as exc:
        return _rejection(PlanSetRejectReason.AUTHORITY_NOT_CANONICAL, [str(exc)])
    if not isinstance(payload, dict):
        return _rejection(PlanSetRejectReason.AUTHORITY_INVALID, ["authority is not an object"])
    if payload.get("schema_version") != PLAN_SET_SCHEMA_VERSION:
        return _rejection(
            PlanSetRejectReason.SCHEMA_VERSION, ["unsupported authority schema version"]
        )
    raw_without_digest = dict(payload)
    supplied_digest = raw_without_digest.pop("authority_digest", None)
    if supplied_digest != compute_canonical_hash(
        raw_without_digest, domain=PLAN_SET_AUTHORITY_DOMAIN
    ):
        details.append("authority_digest does not match canonical authority bytes")
    try:
        authority = PlanSetAuthority.from_dict(payload)
    except ValueError as exc:
        if details:
            return _rejection(PlanSetRejectReason.AUTHORITY_DIGEST, details + [str(exc)])
        return _rejection(PlanSetRejectReason.AUTHORITY_INVALID, [str(exc)])
    if details:
        return _rejection(PlanSetRejectReason.AUTHORITY_DIGEST, details)
    if (
        expected_execution_generation is not None
        and authority.execution_generation != expected_execution_generation
    ):
        details.append("authority execution generation does not match")
    if expected_kitchen_id is not None and authority.kitchen_id != expected_kitchen_id:
        details.append("authority kitchen ID does not match")
    if require_sealed and authority.state is not PlanSetState.SEALED:
        details.append("authority is not sealed")

    part_key: str | None = None
    wanted: Path | None = None
    if current_plan_path is not None:
        try:
            wanted, _ = read_stable_contained_bytes(
                current_plan_path,
                allowed_root,
                max_size_bytes=PLAN_SET_MAX_PART_BYTES,
            )
        except (ContainmentError, OSError) as exc:
            details.append(f"current plan: {exc}")

    for part in authority.parts:
        try:
            path, data = read_stable_contained_bytes(
                part.locator,
                allowed_root,
                max_size_bytes=PLAN_SET_MAX_PART_BYTES,
            )
        except (ContainmentError, OSError) as exc:
            details.append(f"{part.part_key}: {exc}")
            continue
        if len(data) != part.byte_size or compute_bytes_hash(data) != part.content_digest:
            details.append(f"{part.part_key}: part content changed")
        if wanted is not None and path == wanted:
            part_key = part.part_key
    if authority.issue is not None:
        try:
            _, data = read_stable_contained_bytes(
                authority.issue.locator,
                allowed_root,
                max_size_bytes=PLAN_SET_MAX_ISSUE_BYTES,
            )
            if (
                len(data) != authority.issue.byte_size
                or compute_bytes_hash(data) != authority.issue.content_digest
            ):
                details.append("issue snapshot content changed")
        except (ContainmentError, OSError) as exc:
            details.append(f"issue snapshot: {exc}")
    if wanted is not None and part_key is None:
        details.append("current plan is not bound by the authority")
    if details:
        if any("execution generation" in detail for detail in details):
            reason = PlanSetRejectReason.EXECUTION_GENERATION
        elif any("kitchen ID" in detail for detail in details):
            reason = PlanSetRejectReason.KITCHEN_ID
        elif any("not sealed" in detail for detail in details):
            reason = PlanSetRejectReason.AUTHORITY_NOT_SEALED
        elif any("current plan" in detail for detail in details):
            reason = PlanSetRejectReason.PART_NOT_FOUND
        else:
            reason = PlanSetRejectReason.PART_CONTENT_CHANGED
        return _rejection(reason, details)
    evidence = PlanSetPreflightEvidence(
        plan_set_authority_path=str(resolved_authority),
        plan_set_authority_digest=authority.authority_digest,
        plan_set_authority_id=authority.plan_set_authority_id,
        part_key=part_key,
        plan_set_state=authority.state,
        coverage_status=authority.coverage.status.value,
        assigned_requirements=assigned_requirements(authority, part_key) if part_key else (),
    )
    return PlanSetVerification(True, None, (), authority, evidence)
