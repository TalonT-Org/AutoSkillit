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
    PlanSetBindingMode,
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


_REASON_MAP: dict[str, PlanSetRejectReason] = {
    "path_escape": PlanSetRejectReason.PATH_ESCAPE,
    "symlink": PlanSetRejectReason.SYMLINK,
    "hardlink": PlanSetRejectReason.HARDLINK,
    "oversized": PlanSetRejectReason.OVERSIZED,
    "world_writable": PlanSetRejectReason.WORLD_WRITABLE,
    "metadata_drift": PlanSetRejectReason.METADATA_DRIFT,
}
_MESSAGE_REASONS = (
    ("escapes allowed root", PlanSetRejectReason.PATH_ESCAPE),
    ("symlink", PlanSetRejectReason.SYMLINK),
    ("hardlink", PlanSetRejectReason.HARDLINK),
    ("too large", PlanSetRejectReason.OVERSIZED),
    ("world-writable", PlanSetRejectReason.WORLD_WRITABLE),
    ("modified between reads", PlanSetRejectReason.METADATA_DRIFT),
)


def _read_reject_reason(exc: Exception, *, part: bool = False) -> PlanSetRejectReason:
    if part and isinstance(exc, FileNotFoundError):
        return PlanSetRejectReason.PART_FILE_MISSING
    if isinstance(exc, ContainmentError):
        mapped = _REASON_MAP.get(exc.reason)
        if mapped is not None:
            return mapped
    if isinstance(exc, FileNotFoundError):
        return PlanSetRejectReason.CONTAINMENT
    message = str(exc).lower()
    return next(
        (reason for phrase, reason in _MESSAGE_REASONS if phrase in message),
        PlanSetRejectReason.CONTAINMENT,
    )


def _identity_failures(
    authority: PlanSetAuthority,
    expected_execution_generation: str | None,
    expected_kitchen_id: str | None,
    expected_binding_mode: PlanSetBindingMode | None,
    require_sealed: bool,
) -> list[tuple[PlanSetRejectReason, str]]:
    failures: list[tuple[PlanSetRejectReason, str]] = []
    if (
        expected_execution_generation is not None
        and authority.execution_generation != expected_execution_generation
    ):
        failures.append(
            (
                PlanSetRejectReason.EXECUTION_GENERATION,
                "authority execution generation does not match",
            )
        )
    expected_mode = expected_binding_mode or (
        PlanSetBindingMode.RECIPE if expected_execution_generation is not None else None
    )
    if expected_mode is not None and authority.binding_mode is not expected_mode:
        failures.append(
            (PlanSetRejectReason.BINDING_MODE, "authority binding mode does not match")
        )
    if expected_kitchen_id is not None and authority.kitchen_id != expected_kitchen_id:
        failures.append((PlanSetRejectReason.KITCHEN_ID, "authority kitchen ID does not match"))
    if require_sealed and authority.state is not PlanSetState.SEALED:
        failures.append((PlanSetRejectReason.AUTHORITY_NOT_SEALED, "authority is not sealed"))
    return failures


def _issue_snapshot_failure(
    authority: PlanSetAuthority, allowed_root: str | Path
) -> tuple[PlanSetRejectReason, str] | None:
    if authority.issue is None:
        return None
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
            return PlanSetRejectReason.PART_CONTENT_CHANGED, "issue snapshot content changed"
    except (ContainmentError, OSError) as exc:
        return _read_reject_reason(exc), f"issue snapshot: {exc}"
    return None


def _bound_artifact_failures(
    authority: PlanSetAuthority,
    allowed_root: str | Path,
    current_plan_path: str | Path | None,
    allow_part_drift: bool,
) -> tuple[str | None, list[tuple[PlanSetRejectReason, str]]]:
    failures: list[tuple[PlanSetRejectReason, str]] = []
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
            failures.append((_read_reject_reason(exc, part=True), f"current plan: {exc}"))

    for part in authority.parts:
        try:
            path, data = read_stable_contained_bytes(
                part.locator,
                allowed_root,
                max_size_bytes=PLAN_SET_MAX_PART_BYTES,
            )
        except (ContainmentError, OSError) as exc:
            failures.append((_read_reject_reason(exc, part=True), f"{part.part_key}: {exc}"))
            continue
        if (
            len(data) != part.byte_size or compute_bytes_hash(data) != part.content_digest
        ) and not allow_part_drift:
            failures.append(
                (
                    PlanSetRejectReason.PART_CONTENT_CHANGED,
                    f"{part.part_key}: part content changed",
                )
            )
        if wanted is not None and path == wanted:
            part_key = part.part_key
    issue_failure = _issue_snapshot_failure(authority, allowed_root)
    if issue_failure is not None:
        failures.append(issue_failure)
    if wanted is not None and part_key is None:
        failures.append(
            (PlanSetRejectReason.PART_NOT_FOUND, "current plan is not bound by the authority")
        )
    return part_key, failures


def verify_plan_set_authority(
    authority_path: str | Path,
    *,
    allowed_root: str | Path,
    expected_execution_generation: str | None,
    expected_kitchen_id: str | None,
    expected_binding_mode: PlanSetBindingMode | None = None,
    current_plan_path: str | Path | None,
    require_sealed: bool,
    allow_part_drift: bool = False,
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
        return _rejection(_read_reject_reason(exc), [str(exc)])
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
    failures = _identity_failures(
        authority,
        expected_execution_generation,
        expected_kitchen_id,
        expected_binding_mode,
        require_sealed,
    )
    part_key, artifact_failures = _bound_artifact_failures(
        authority, allowed_root, current_plan_path, allow_part_drift
    )
    failures.extend(artifact_failures)
    if failures:
        return _rejection(failures[0][0], [detail for _, detail in failures])
    selected_part = next((part for part in authority.parts if part.part_key == part_key), None)
    evidence = PlanSetPreflightEvidence(
        status="admitted",
        plan_set_authority_path=str(resolved_authority),
        plan_set_authority_digest=authority.authority_digest,
        plan_set_authority_id=authority.plan_set_authority_id,
        revision=authority.revision,
        part_key=part_key,
        part_ordinal=selected_part.ordinal if selected_part else None,
        part_count=len(authority.parts),
        part_suffix=selected_part.part_suffix if selected_part else None,
        plan_set_state=authority.state,
        coverage_status=authority.coverage.status.value,
        assigned_requirements=assigned_requirements(authority, part_key) if part_key else (),
    )
    return PlanSetVerification(True, None, (), authority, evidence)
