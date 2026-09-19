"""Candidate aggregation and verdict derivation for the experimental review pipeline."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Collection, Mapping, Sequence
from pathlib import Path

from autoskillit.core import AnchorAdmission, DiffAnchorAuthority
from autoskillit.smoke_utils._review_contracts import (
    EXPERIMENTAL_REVIEW_AUDITORS,
    _is_non_empty_string,
)
from autoskillit.smoke_utils.review._constants import _STANDARD_REVIEW_DIMENSIONS
from autoskillit.smoke_utils.review._validation import (
    _doc_count_finding_validation_error,
    _standard_finding_validation_error,
)


def _as_int(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


_SEVERITY_RANK: dict[str, int] = {"critical": 0, "warning": 1, "info": 2}


def _source_finding_validation_error(
    *,
    source: str,
    finding: object,
    deletion_only: bool,
    anchor_authority: DiffAnchorAuthority,
    allowed_dimensions: Collection[str],
    review_root: Path,
) -> str | AnchorAdmission | None:
    """Select the anchor-aware or review-level validation route for one source."""
    if source == "doc_count":
        return _doc_count_finding_validation_error(finding, review_root=review_root)
    return _standard_finding_validation_error(
        finding,
        deletion_only=deletion_only,
        anchor_authority=anchor_authority,
        allowed_dimensions=allowed_dimensions,
        review_root=review_root,
    )


def _has_no_accepted_source_findings(
    *,
    standard: Sequence[Mapping[str, object]],
    deletion: Sequence[Mapping[str, object]],
    doc_count: Sequence[Mapping[str, object]],
    unpostable: Sequence[Mapping[str, object]],
) -> bool:
    """Return whether source validation yielded no usable finding at all."""
    return not standard and not deletion and not doc_count and not unpostable


def _normalize_doc_count_findings(
    findings: Sequence[Mapping[str, object]],
    normalize: Callable[..., dict[str, object]],
) -> tuple[list[dict[str, object]], set[str]]:
    """Normalize review-level findings without adding a branch to aggregation."""
    normalized = [
        normalize(finding, default_source="tests", original_index=index)
        for index, finding in enumerate(findings)
    ]
    return normalized, {str(finding["candidate_id"]) for finding in normalized}


def aggregate_combined_review_candidates(
    *,
    candidates: Sequence[Mapping[str, object]],
    dispositions: Sequence[Mapping[str, object]],
    prior_resolved_findings: Sequence[Mapping[str, object]],
    anchor_authority: DiffAnchorAuthority,
    allowed_dimensions: Collection[str] = _STANDARD_REVIEW_DIMENSIONS,
    standard_findings: Sequence[Mapping[str, object]] = (),
    deletion_findings: Sequence[Mapping[str, object]] = (),
    doc_count_findings: Sequence[Mapping[str, object]] = (),
    snapshot: Mapping[str, str] | None = None,
    review_root: str = "",
) -> dict[str, object]:
    """Combine every source, then apply suppression-first deterministic deduplication."""
    dimension_order = tuple(sorted(set(allowed_dimensions)))
    raw_findings = (*standard_findings, *deletion_findings, *doc_count_findings)
    validated_standard: list[Mapping[str, object]] = []
    validated_deletion: list[Mapping[str, object]] = []
    validated_doc_count: list[Mapping[str, object]] = []
    unpostable: list[dict[str, object]] = []
    validation_errors: list[str] = []
    if raw_findings:
        root = Path(review_root)
        head_sha = (snapshot or {}).get("head_sha", (snapshot or {}).get("_head_sha", ""))
        base_sha = (snapshot or {}).get("base_sha", (snapshot or {}).get("_base_sha", ""))
        if not root.is_absolute():
            validation_errors.append("review_root must be absolute")
        if not _is_non_empty_string(head_sha) or not _is_non_empty_string(base_sha):
            validation_errors.append("snapshot head/base authority must be non-empty")
        if not validation_errors:
            root = root.resolve()
            snapshot_identity = dict(snapshot or {})
            for source, findings, deletion_only, destination in (
                ("standard", standard_findings, False, validated_standard),
                ("deletion", deletion_findings, True, validated_deletion),
                ("doc_count", doc_count_findings, False, validated_doc_count),
            ):
                for index, finding in enumerate(findings):
                    error = _source_finding_validation_error(
                        source=source,
                        finding=finding,
                        deletion_only=deletion_only,
                        anchor_authority=anchor_authority,
                        allowed_dimensions=dimension_order,
                        review_root=root,
                    )
                    if isinstance(error, str) and not isinstance(error, AnchorAdmission):
                        validation_errors.append(f"{source}[{index}]: {error}")
                        continue
                    canonical = json.dumps(finding, sort_keys=True, separators=(",", ":"))
                    enriched = {
                        **finding,
                        "record_digest": hashlib.sha256(canonical.encode()).hexdigest(),
                        "snapshot": snapshot_identity,
                    }
                    if isinstance(error, AnchorAdmission):
                        unpostable.append({**enriched, "admission_reason": error.value})
                    else:
                        destination.append(enriched)
    if validation_errors:
        if _has_no_accepted_source_findings(
            standard=validated_standard,
            deletion=validated_deletion,
            doc_count=validated_doc_count,
            unpostable=unpostable,
        ):
            return {
                "state": "degraded",
                "survivors": [],
                "unpostable": [],
                "review_level_findings": [],
                "aggregation_records": [],
                "validation_errors": validation_errors,
            }

    accepted_dispositions: dict[str, Mapping[str, object]] = {}
    for item in dispositions:
        if (
            item.get("reason_code") == "accepted"
            and _is_non_empty_string(item.get("candidate_id"))
            and _is_non_empty_string(item.get("disposition_id"))
        ):
            accepted_dispositions.setdefault(str(item["candidate_id"]), item)
    source_names = (
        *dimension_order,
        "deletion_regression",
        *EXPERIMENTAL_REVIEW_AUDITORS,
    )
    source_rank = {name: rank for rank, name in enumerate(source_names)}

    def source_name(finding: Mapping[str, object], default: str) -> str:
        dimension = str(finding.get("dimension", ""))
        if dimension in dimension_order or dimension == "deletion_regression":
            return dimension
        auditor_name = str(finding.get("auditor_name", ""))
        if auditor_name in EXPERIMENTAL_REVIEW_AUDITORS:
            return auditor_name
        return default

    def normalize(
        finding: Mapping[str, object],
        *,
        default_source: str,
        original_index: int,
    ) -> dict[str, object]:
        normalized = dict(finding)
        normalized_source = source_name(normalized, default_source)
        normalized_index = _as_int(normalized.get("original_index", original_index))
        identity_input = json.dumps(
            {
                "finding": normalized,
                "original_index": normalized_index,
                "source": normalized_source,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        normalized.setdefault(
            "candidate_id",
            hashlib.sha256(identity_input.encode()).hexdigest(),
        )
        normalized["source_name"] = normalized_source
        normalized["source_rank"] = source_rank.get(normalized_source, len(source_rank))
        normalized["original_index"] = normalized_index
        return normalized

    def rank(candidate: Mapping[str, object]) -> tuple[int, int, str]:
        return (
            _as_int(candidate.get("source_rank")),
            _as_int(candidate.get("original_index")),
            str(candidate.get("candidate_id", "")),
        )

    eligible: list[dict[str, object]] = []
    for index, finding in enumerate(validated_standard):
        eligible.append(
            normalize(
                finding,
                default_source="arch",
                original_index=index,
            )
        )
    for index, finding in enumerate(validated_deletion):
        eligible.append(
            normalize(
                finding,
                default_source="deletion_regression",
                original_index=index,
            )
        )
    normalized_doc_count, review_level_candidate_ids = _normalize_doc_count_findings(
        validated_doc_count,
        normalize,
    )
    eligible.extend(normalized_doc_count)
    for index, candidate in enumerate(candidates):
        candidate_id = str(candidate.get("candidate_id", ""))
        if candidate_id not in accepted_dispositions:
            continue
        eligible.append(
            normalize(
                {
                    **candidate,
                    "disposition_id": accepted_dispositions[candidate_id]["disposition_id"],
                },
                default_source=str(candidate.get("auditor_name", "")),
                original_index=index,
            )
        )
    eligible.sort(key=rank)
    aggregation_records: list[dict[str, object]] = []
    unsuppressed: list[Mapping[str, object]] = []
    for candidate in eligible:
        suppressed = any(
            str(prior.get("file", prior.get("path", ""))) == str(candidate.get("file"))
            and abs(_as_int(prior.get("line")) - _as_int(candidate.get("line"))) <= 5
            for prior in prior_resolved_findings
        )
        if suppressed:
            aggregation_records.append(
                {
                    "candidate_id": candidate["candidate_id"],
                    "reason_code": "suppressed_prior_thread",
                }
            )
        else:
            unsuppressed.append(candidate)

    groups: dict[tuple[str, int, str], list[Mapping[str, object]]] = {}
    for unsuppressed_candidate in unsuppressed:
        candidate_id = str(unsuppressed_candidate["candidate_id"])
        key = (
            str(unsuppressed_candidate["file"]),
            _as_int(unsuppressed_candidate["line"]),
            ("review_level" if candidate_id in review_level_candidate_ids else "diff_anchored"),
        )
        groups.setdefault(key, []).append(unsuppressed_candidate)

    survivors: list[Mapping[str, object]] = []
    for key in sorted(groups):
        members = sorted(
            groups[key],
            key=lambda candidate: (
                _SEVERITY_RANK.get(str(candidate.get("severity")), len(_SEVERITY_RANK)),
                bool(candidate.get("requires_decision")),
                *rank(candidate),
            ),
        )
        winner = members[0]
        survivors.append(winner)
        if len(members) == 1:
            continue
        member_ids = [str(member["candidate_id"]) for member in members]
        group_fields = {"file": key[0], "line": key[1], "member_ids": member_ids}
        group_input = json.dumps(
            group_fields,
            sort_keys=True,
            separators=(",", ":"),
        )
        dedup_group_id = hashlib.sha256(group_input.encode()).hexdigest()
        for loser in members[1:]:
            aggregation_records.append(
                {
                    "candidate_id": loser["candidate_id"],
                    "reason_code": "duplicate_candidate",
                    "dedup_group_id": dedup_group_id,
                    "member_ids": member_ids,
                    "winner_candidate_id": winner["candidate_id"],
                    "rationale": "severity, requires_decision, fixed source rank, original index",
                }
            )
    survivors.sort(key=rank)
    review_level_findings = [
        finding
        for finding in survivors
        if str(finding["candidate_id"]) in review_level_candidate_ids
    ]
    return {
        "state": "degraded" if validation_errors else "complete",
        "survivors": survivors,
        "unpostable": unpostable,
        **({"review_level_findings": review_level_findings} if review_level_findings else {}),
        "aggregation_records": aggregation_records,
        "validation_errors": validation_errors,
    }


def determine_experimental_review_verdict(
    *,
    retained_snapshot_was_valid: bool,
    final_snapshot_is_fresh: bool,
    gate_state: str,
    experimental_audit_state: str,
    findings: Sequence[Mapping[str, object]],
) -> str:
    """Keep gate-authority degradation distinct from later snapshot movement."""
    if retained_snapshot_was_valid and not final_snapshot_is_fresh:
        return "stale_snapshot"
    if any(
        finding.get("severity") == "critical" and not finding.get("requires_decision")
        for finding in findings
    ):
        return "changes_requested"
    if gate_state == "degraded" or experimental_audit_state == "degraded":
        return "needs_human"
    if any(
        finding.get("severity") == "warning" and not finding.get("requires_decision")
        for finding in findings
    ):
        return "approved_with_comments"
    if any(finding.get("requires_decision") for finding in findings):
        return "needs_human"
    return "approved"
