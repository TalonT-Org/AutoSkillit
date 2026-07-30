"""Pure validation and aggregation helpers for proof-only PR review auditors."""

from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path

EXPERIMENTAL_REVIEW_AUDITORS = (
    "pr-review-auditor-reachability",
    "pr-review-auditor-abstraction-surface",
)
_EXPERIMENTAL_DIMENSIONS = {
    "pr-review-auditor-reachability": "overengineering_reachability",
    "pr-review-auditor-abstraction-surface": "overengineering_abstraction_surface",
}
_EXPERIMENTAL_CANDIDATE_KEYS = {
    "file",
    "line",
    "dimension",
    "severity",
    "message",
    "requires_decision",
    "evidence",
    "trace",
    "boundary_checks",
    "confidence",
    "simpler_behavior",
}
_EXPERIMENTAL_EVIDENCE_ROLES = {
    "anchor",
    "caller",
    "consumer",
    "registration",
    "invariant",
    "counterevidence_checked",
}
_EXPERIMENTAL_BOUNDARIES = {
    "reflection_decorators",
    "dependency_injection",
    "plugin_registry",
    "cli_entrypoint",
    "serialization",
    "generated_code",
    "public_api",
}
_EXPERIMENTAL_BOUNDARY_STATUSES = {
    "checked_absent",
    "checked_no_reachable_path",
    "not_applicable",
}
_SIMPLER_BEHAVIOR_FACETS = {
    "return_values": ("return",),
    "exceptions_errors": ("exception", "error"),
    "ordering": ("ordering",),
    "persistence": ("persistence",),
    "concurrency": ("concurrency",),
    "compatibility": ("compatibility",),
}
_TERMINAL_FAILURE_REASONS = {
    "tool_failure",
    "refusal",
    "interruption",
    "truncation",
}
_MAX_ENVELOPE_ERRORS = 32
_MAX_EXPERIMENTAL_OUTPUT_BYTES = 1024 * 1024
_STANDARD_REVIEW_DIMENSIONS = (
    "arch",
    "tests",
    "defense",
    "bugs",
    "cohesion",
    "slop",
)
_STANDARD_FINDING_KEYS = {
    "file",
    "line",
    "dimension",
    "severity",
    "message",
    "requires_decision",
}
_REVIEW_SEVERITIES = {"critical", "warning", "info"}
_PUBLICATION_FILENAMES = {
    "raw_findings": "raw_findings_{pr_number}.json",
    "diff_context": "diff_context_{pr_number}.json",
    "local_findings": "local_findings_{pr_number}.json",
    "review_receipt": "batch_review_response_{pr_number}.json",
}


def _bounded_utf8(value: str, limit: int) -> str:
    """Return a UTF-8 string whose encoded representation is at most ``limit`` bytes."""
    return value.encode("utf-8")[:limit].decode("utf-8", errors="ignore")


def build_malformed_review_envelope(
    *,
    producer: str,
    terminal_status: str,
    raw_output: str | bytes,
    errors: Sequence[str],
    rejection_reason: str,
    excerpt_limit: int = 4096,
    error_limit: int = _MAX_ENVELOPE_ERRORS,
) -> dict[str, object]:
    """Build the bounded diagnostic envelope required by the review-pr contract."""
    if excerpt_limit < 0:
        raise ValueError("excerpt_limit must be non-negative")
    if error_limit < 0:
        raise ValueError("error_limit must be non-negative")
    raw_bytes = raw_output.encode("utf-8") if isinstance(raw_output, str) else raw_output
    excerpt = raw_bytes[:excerpt_limit].decode("utf-8", errors="ignore")
    return {
        "producer": _bounded_utf8(producer, 1024),
        "terminal_status": _bounded_utf8(terminal_status, 1024),
        "received_byte_length": len(raw_bytes),
        "received_sha256": hashlib.sha256(raw_bytes).hexdigest(),
        "excerpt": excerpt,
        "excerpt_byte_length": len(excerpt.encode("utf-8")),
        "errors": [
            _bounded_utf8(str(errors[index]), 1024)
            for index in range(min(len(errors), error_limit))
        ],
        "rejection_reason": _bounded_utf8(rejection_reason, 1024),
    }


def _is_non_empty_string(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _is_positive_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _as_int(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _is_contained_relative_path(value: object, review_root: Path) -> bool:
    if not _is_non_empty_string(value):
        return False
    candidate = Path(str(value))
    if candidate.is_absolute() or ".." in candidate.parts:
        return False
    return (review_root / candidate).resolve().is_relative_to(review_root)


def _missing_simpler_behavior_facets(value: object) -> list[str]:
    if not _is_non_empty_string(value):
        return list(_SIMPLER_BEHAVIOR_FACETS)
    normalized = str(value).casefold()
    return [
        facet
        for facet, accepted_terms in _SIMPLER_BEHAVIOR_FACETS.items()
        if not any(term in normalized for term in accepted_terms)
    ]


def _candidate_validation_error(
    candidate: object,
    *,
    auditor_name: str,
    valid_diff_lines: Mapping[str, Sequence[int]],
    review_root: Path,
) -> tuple[str, str] | None:
    if not isinstance(candidate, dict) or set(candidate) != _EXPERIMENTAL_CANDIDATE_KEYS:
        return ("schema_invalid", "candidate must have the exact closed key set")
    if candidate["dimension"] != _EXPERIMENTAL_DIMENSIONS[auditor_name]:
        return ("schema_invalid", "dimension does not match producer")
    if not isinstance(candidate["severity"], str) or candidate["severity"] not in {
        "critical",
        "warning",
        "info",
    }:
        return ("schema_invalid", "severity is outside the closed enum")
    if type(candidate["requires_decision"]) is not bool:
        return ("schema_invalid", "requires_decision must be an exact boolean")
    if not all(
        _is_non_empty_string(candidate[key]) for key in ("file", "message", "simpler_behavior")
    ):
        return ("schema_invalid", "base string fields must be non-empty")
    missing_facets = _missing_simpler_behavior_facets(candidate["simpler_behavior"])
    if missing_facets:
        return (
            "schema_invalid",
            f"simpler_behavior omits required facets: {','.join(missing_facets)}",
        )
    if not _is_positive_int(candidate["line"]):
        return ("schema_invalid", "primary line must be a positive integer")
    if not _is_contained_relative_path(candidate["file"], review_root):
        return ("path_escape", "primary path escapes the review root")
    if candidate["line"] not in valid_diff_lines.get(str(candidate["file"]), ()):
        return ("not_changed_line", "primary anchor is not an exact changed line")

    evidence = candidate["evidence"]
    if not isinstance(evidence, list) or not evidence:
        return ("schema_invalid", "evidence must be a non-empty array")
    evidence_locations: set[tuple[str, int]] = set()
    for item in evidence:
        if not isinstance(item, dict) or set(item) != {"path", "line", "role", "claim"}:
            return ("schema_invalid", "evidence item has an invalid schema")
        if not _is_contained_relative_path(item["path"], review_root):
            return ("path_escape", "evidence path escapes the review root")
        if (
            not _is_positive_int(item["line"])
            or not _is_non_empty_string(item["role"])
            or item["role"] not in _EXPERIMENTAL_EVIDENCE_ROLES
            or not _is_non_empty_string(item["claim"])
        ):
            return ("schema_invalid", "evidence item has an invalid field")
        evidence_locations.add((str(item["path"]), int(item["line"])))
    if len(evidence_locations) < 2:
        return ("schema_invalid", "evidence must cite two distinct locations")

    trace = candidate["trace"]
    if not isinstance(trace, list) or not trace:
        return ("schema_invalid", "trace must be a non-empty ordered array")
    for item in trace:
        if not isinstance(item, dict) or set(item) != {"path", "line", "relation"}:
            return ("schema_invalid", "trace item has an invalid schema")
        if not _is_contained_relative_path(item["path"], review_root):
            return ("path_escape", "trace path escapes the review root")
        if not _is_positive_int(item["line"]) or not _is_non_empty_string(item["relation"]):
            return ("schema_invalid", "trace item has an invalid field")

    boundary_checks = candidate["boundary_checks"]
    if not isinstance(boundary_checks, list):
        return ("schema_invalid", "boundary_checks must be an array")
    boundaries: set[str] = set()
    for item in boundary_checks:
        if not isinstance(item, dict) or set(item) != {"boundary", "status", "claim"}:
            return ("schema_invalid", "boundary check has an invalid schema")
        boundary = item["boundary"]
        if (
            not _is_non_empty_string(boundary)
            or boundary not in _EXPERIMENTAL_BOUNDARIES
            or boundary in boundaries
            or not _is_non_empty_string(item["status"])
            or item["status"] not in _EXPERIMENTAL_BOUNDARY_STATUSES
            or not _is_non_empty_string(item["claim"])
        ):
            return ("schema_invalid", "boundary check has an invalid field")
        boundaries.add(str(boundary))
    if boundaries != _EXPERIMENTAL_BOUNDARIES:
        return ("schema_invalid", "boundary checks do not cover the closed boundary set")

    confidence = candidate["confidence"]
    if type(confidence) is int:
        if not 0 <= confidence <= 1:
            return ("schema_invalid", "confidence must be finite and within [0,1]")
    elif type(confidence) is float:
        if not math.isfinite(confidence) or not 0 <= confidence <= 1:
            return ("schema_invalid", "confidence must be finite and within [0,1]")
    else:
        return ("schema_invalid", "confidence must be finite and within [0,1]")
    return None


def deletion_regression_is_eligible(deletion_context: object) -> bool:
    """Return deletion eligibility without consulting the experimental gate."""
    return isinstance(deletion_context, Mapping) and _is_non_empty_string(
        deletion_context.get("merge_base")
    )


def _standard_finding_validation_error(
    finding: object,
    *,
    deletion_only: bool,
    valid_diff_lines: Mapping[str, Sequence[int]],
    valid_line_ranges: Mapping[str, Sequence[Sequence[int]]],
    review_root: Path,
) -> str | None:
    if not isinstance(finding, dict) or set(finding) != _STANDARD_FINDING_KEYS:
        return "finding must have the exact standard closed key set"
    expected_dimensions = (
        {"deletion_regression"} if deletion_only else set(_STANDARD_REVIEW_DIMENSIONS)
    )
    if not _is_non_empty_string(finding["dimension"]):
        return "dimension must be a non-empty string"
    if finding["dimension"] not in expected_dimensions:
        return "dimension does not match the standard finding source"
    if not _is_non_empty_string(finding["severity"]):
        return "severity must be a non-empty string"
    if finding["severity"] not in _REVIEW_SEVERITIES:
        return "severity is outside the closed enum"
    if type(finding["requires_decision"]) is not bool:
        return "requires_decision must be an exact boolean"
    if not _is_non_empty_string(finding["file"]) or not _is_non_empty_string(finding["message"]):
        return "file and message must be non-empty strings"
    if not _is_positive_int(finding["line"]):
        return "line must be a positive integer"
    if not _is_contained_relative_path(finding["file"], review_root):
        return "file escapes the review root"
    file_path = str(finding["file"])
    line = int(finding["line"])
    if valid_diff_lines:
        if line not in valid_diff_lines.get(file_path, ()):
            return "file and line are not an exact changed-line anchor"
    elif valid_line_ranges:
        ranges = valid_line_ranges.get(file_path, ())
        if not any(
            isinstance(bounds, Sequence)
            and not isinstance(bounds, (str, bytes))
            and len(bounds) == 2
            and _is_positive_int(bounds[0])
            and _is_positive_int(bounds[1])
            and int(bounds[0]) <= line <= int(bounds[1])
            for bounds in ranges
        ):
            return "file and line are not within a changed hunk"
    return None


def validate_experimental_auditor_outputs(
    *,
    outputs: Mapping[str, Mapping[str, object]],
    valid_diff_lines: Mapping[str, Sequence[int]],
    snapshot: Mapping[str, str],
    review_root: str,
) -> dict[str, object]:
    """Validate both proof-only outputs atomically and assign deterministic identities."""
    root = Path(review_root)
    if not root.is_absolute():
        raise ValueError(f"review_root must be absolute, got {review_root!r}")
    root = root.resolve()
    status_by_name: dict[str, dict[str, str]] = {}
    malformed_envelopes: list[dict[str, object]] = []
    pending_candidates: list[dict[str, object]] = []

    for auditor_name in EXPERIMENTAL_REVIEW_AUDITORS:
        raw_result = outputs.get(auditor_name)
        result = raw_result if isinstance(raw_result, Mapping) else None
        terminal_status = (
            str(result.get("terminal_status", "missing_result"))
            if result is not None
            else "missing_result"
        )
        payload: object = result.get("output") if result else None
        raw_output: str | bytes
        if isinstance(payload, bytes):
            raw_output = payload
        elif isinstance(payload, str):
            raw_output = payload
        else:
            try:
                raw_output = json.dumps(payload, sort_keys=True, separators=(",", ":"))
            except (TypeError, ValueError):
                raw_output = f"<unserializable {type(payload).__name__}>"

        error: str | None = None
        validation_detail: str | None = None
        parsed: object = None
        parsed_candidates: list[object] = []
        if result is None or "output" not in result:
            error = "missing_result"
        elif terminal_status != "success":
            error = (
                terminal_status if terminal_status in _TERMINAL_FAILURE_REASONS else "tool_failure"
            )
        elif len(raw_output.encode("utf-8") if isinstance(raw_output, str) else raw_output) > (
            _MAX_EXPERIMENTAL_OUTPUT_BYTES
        ):
            error = "schema_invalid"
            validation_detail = (
                f"output exceeds {_MAX_EXPERIMENTAL_OUTPUT_BYTES} byte validation limit"
            )
        else:
            try:
                parsed = json.loads(payload) if isinstance(payload, (str, bytes)) else payload
            except (json.JSONDecodeError, UnicodeDecodeError, TypeError, ValueError):
                error = "malformed_json"
            if error is None and not isinstance(parsed, list):
                error = "non_array"
            if isinstance(parsed, list):
                parsed_candidates = parsed
            if error is None:
                for original_index, candidate in enumerate(parsed_candidates):
                    validation_error = _candidate_validation_error(
                        candidate,
                        auditor_name=auditor_name,
                        valid_diff_lines=valid_diff_lines,
                        review_root=root,
                    )
                    if validation_error is not None:
                        error, validation_detail = validation_error
                        break
                    assert isinstance(candidate, dict)
                    canonical = json.dumps(candidate, sort_keys=True, separators=(",", ":"))
                    record_digest = hashlib.sha256(canonical.encode()).hexdigest()
                    identity_fields = {
                        "snapshot": snapshot,
                        "auditor_name": auditor_name,
                        "original_index": original_index,
                        "record_digest": record_digest,
                    }
                    identity = json.dumps(
                        identity_fields,
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                    pending_candidates.append(
                        {
                            **candidate,
                            "auditor_name": auditor_name,
                            "original_index": original_index,
                            "record_digest": record_digest,
                            "candidate_id": hashlib.sha256(identity.encode()).hexdigest(),
                            "snapshot": dict(snapshot),
                        }
                    )

        if error is None:
            status_by_name[auditor_name] = {"status": "success", "reason_code": "accepted"}
            continue
        status_by_name[auditor_name] = {"status": "degraded", "reason_code": error}
        malformed_envelopes.append(
            build_malformed_review_envelope(
                producer=auditor_name,
                terminal_status=terminal_status,
                raw_output=raw_output,
                errors=[validation_detail or error],
                rejection_reason=error,
            )
        )

    if malformed_envelopes:
        return {
            "state": "degraded",
            "status_by_name": status_by_name,
            "candidates": [],
            "malformed_envelopes": malformed_envelopes,
        }
    return {
        "state": "complete",
        "status_by_name": status_by_name,
        "candidates": pending_candidates,
        "malformed_envelopes": [],
    }


def aggregate_experimental_review_candidates(
    *,
    candidates: Sequence[Mapping[str, object]],
    dispositions: Sequence[Mapping[str, object]],
    prior_resolved_findings: Sequence[Mapping[str, object]],
    standard_findings: Sequence[Mapping[str, object]] = (),
    deletion_findings: Sequence[Mapping[str, object]] = (),
    valid_diff_lines: Mapping[str, Sequence[int]] | None = None,
    valid_line_ranges: Mapping[str, Sequence[Sequence[int]]] | None = None,
    snapshot: Mapping[str, str] | None = None,
    review_root: str = "",
) -> dict[str, object]:
    """Combine every source, then apply suppression-first deterministic deduplication."""
    raw_findings = (*standard_findings, *deletion_findings)
    validated_standard: list[Mapping[str, object]] = []
    validated_deletion: list[Mapping[str, object]] = []
    validation_errors: list[str] = []
    if raw_findings:
        root = Path(review_root)
        head_sha = (snapshot or {}).get("head_sha", (snapshot or {}).get("_head_sha", ""))
        base_sha = (snapshot or {}).get("base_sha", (snapshot or {}).get("_base_sha", ""))
        if not root.is_absolute():
            validation_errors.append("review_root must be absolute")
        if not _is_non_empty_string(head_sha) or not _is_non_empty_string(base_sha):
            validation_errors.append("snapshot head/base authority must be non-empty")
        if valid_diff_lines is None and valid_line_ranges is None:
            validation_errors.append("changed-line authority is required")
        if not validation_errors:
            root = root.resolve()
            snapshot_identity = dict(snapshot or {})
            for source, findings, deletion_only, destination in (
                ("standard", standard_findings, False, validated_standard),
                ("deletion", deletion_findings, True, validated_deletion),
            ):
                for index, finding in enumerate(findings):
                    error = _standard_finding_validation_error(
                        finding,
                        deletion_only=deletion_only,
                        valid_diff_lines=valid_diff_lines or {},
                        valid_line_ranges=valid_line_ranges or {},
                        review_root=root,
                    )
                    if error is not None:
                        validation_errors.append(f"{source}[{index}]: {error}")
                        continue
                    canonical = json.dumps(finding, sort_keys=True, separators=(",", ":"))
                    destination.append(
                        {
                            **finding,
                            "record_digest": hashlib.sha256(canonical.encode()).hexdigest(),
                            "snapshot": snapshot_identity,
                        }
                    )
    if validation_errors:
        return {
            "state": "degraded",
            "survivors": [],
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
        *_STANDARD_REVIEW_DIMENSIONS,
        "deletion_regression",
        *EXPERIMENTAL_REVIEW_AUDITORS,
    )
    source_rank = {name: rank for rank, name in enumerate(source_names)}
    severity_rank = {"critical": 0, "warning": 1, "info": 2}

    def source_name(finding: Mapping[str, object], default: str) -> str:
        dimension = str(finding.get("dimension", ""))
        if dimension in _STANDARD_REVIEW_DIMENSIONS or dimension == "deletion_regression":
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
        default_source = (
            "deletion_regression" if finding.get("dimension") == "deletion_regression" else "arch"
        )
        eligible.append(
            normalize(
                finding,
                default_source=default_source,
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

    groups: dict[tuple[str, int], list[Mapping[str, object]]] = {}
    for unsuppressed_candidate in unsuppressed:
        key = (
            str(unsuppressed_candidate["file"]),
            _as_int(unsuppressed_candidate["line"]),
        )
        groups.setdefault(key, []).append(unsuppressed_candidate)

    survivors: list[Mapping[str, object]] = []
    for key in sorted(groups):
        members = sorted(
            groups[key],
            key=lambda candidate: (
                severity_rank.get(str(candidate.get("severity")), len(severity_rank)),
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
    return {
        "state": "complete",
        "survivors": survivors,
        "aggregation_records": aggregation_records,
        "validation_errors": [],
    }


def render_review_finding_body(finding: Mapping[str, object]) -> str:
    """Render one finding identically for primary and fallback GitHub effects."""
    body = (
        f"[{finding.get('severity', '')}] {finding.get('dimension', '')}: "
        f"{finding.get('message', '')}"
    )
    evidence = finding.get("evidence")
    if isinstance(evidence, list):
        rendered_evidence = [
            f"{item.get('path')}:{item.get('line')} [{item.get('role')}] {item.get('claim')}"
            for item in evidence
            if isinstance(item, Mapping)
        ]
        if rendered_evidence:
            body += "\nEvidence: " + "; ".join(rendered_evidence)
    candidate_id = finding.get("candidate_id")
    disposition_id = finding.get("disposition_id")
    if _is_non_empty_string(candidate_id) and _is_non_empty_string(disposition_id):
        body += f"\nProvenance: candidate_id={candidate_id} disposition_id={disposition_id}"
    return body


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


def _normalize_handoff_finding(finding: Mapping[str, object]) -> dict[str, object]:
    normalized = dict(finding)
    normalized.setdefault("path", normalized.get("file", ""))
    normalized.setdefault("body", render_review_finding_body(normalized))
    normalized.setdefault("side", "RIGHT")
    normalized.setdefault("code_region", "")
    return normalized


def prepare_experimental_review_publication(
    *,
    raw_ledger: Mapping[str, object],
    survivors: Sequence[Mapping[str, object]],
    snapshot: Mapping[str, str],
    annotation_generation_id: str,
    mode: str,
    snapshot_is_fresh: bool,
    handoff_metadata: Mapping[str, object] | None = None,
    receipt: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Build one immutable publication generation or suppress stale effects."""
    if mode not in {"github", "local"}:
        raise ValueError(f"mode must be 'github' or 'local', got {mode!r}")
    head_sha = snapshot.get("head_sha", snapshot.get("_head_sha", ""))
    base_sha = snapshot.get("base_sha", snapshot.get("_base_sha", ""))
    if not head_sha or not base_sha or not annotation_generation_id:
        raise ValueError("snapshot head/base and annotation generation must be non-empty")

    canonical_ledger = json.dumps(raw_ledger, sort_keys=True, separators=(",", ":"))
    normalized_survivors = [_normalize_handoff_finding(finding) for finding in survivors]
    normalized_survivors = json.loads(
        json.dumps(normalized_survivors, sort_keys=True, separators=(",", ":"))
    )
    effective_survivors = normalized_survivors if snapshot_is_fresh else []
    metadata = json.loads(
        json.dumps(dict(handoff_metadata or {}), sort_keys=True, separators=(",", ":"))
    )
    generation_input = json.dumps(
        {
            "annotation_generation_id": annotation_generation_id,
            "handoff_metadata": metadata,
            "mode": mode,
            "raw_ledger": json.loads(canonical_ledger),
            "snapshot": dict(snapshot),
            "snapshot_is_fresh": snapshot_is_fresh,
            "survivors": effective_survivors,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    identity: dict[str, object] = {
        "_head_sha": head_sha,
        "_base_sha": base_sha,
        "annotation_generation_id": annotation_generation_id,
        "review_generation_id": hashlib.sha256(generation_input.encode()).hexdigest(),
    }
    merge_base_sha = snapshot.get("merge_base_sha", snapshot.get("_merge_base_sha", ""))
    if merge_base_sha:
        identity["_merge_base_sha"] = merge_base_sha

    normalized_ledger = json.loads(canonical_ledger)
    raw_findings = {
        **normalized_ledger,
        **identity,
        "state": "complete" if snapshot_is_fresh else "stale_snapshot",
        "survivors": effective_survivors,
    }
    if not snapshot_is_fresh:
        return {
            "state": "stale_snapshot",
            "artifact_order": ["raw_findings"],
            "artifacts": {"raw_findings": raw_findings},
        }

    diff_context = {**metadata, **identity, "context_entries": normalized_survivors}
    artifacts: dict[str, object] = {
        "raw_findings": raw_findings,
        "diff_context": diff_context,
    }
    artifact_order = ["raw_findings", "diff_context"]
    if mode == "local":
        local_findings = {**metadata, **identity, "findings": normalized_survivors}
        artifacts["local_findings"] = local_findings
        artifact_order.append("local_findings")
    elif receipt is not None:
        normalized_receipt = json.loads(
            json.dumps(dict(receipt), sort_keys=True, separators=(",", ":"))
        )
        review_receipt = {**normalized_receipt, **identity}
        artifacts["review_receipt"] = review_receipt
        artifact_order.append("review_receipt")
    return {
        "state": "complete",
        "artifact_order": artifact_order,
        "artifacts": artifacts,
    }


def _write_temp_bytes(directory: Path, final_name: str, content: bytes) -> Path:
    fd, temporary_name = tempfile.mkstemp(
        dir=directory,
        prefix=f".{final_name}.",
        suffix=".tmp",
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise
    return temporary_path


def publish_experimental_review_artifacts(
    *,
    publication: Mapping[str, object],
    output_dir: str,
    pr_number: str,
) -> dict[str, object]:
    """Atomically publish a prepared generation and roll back partial renames."""
    root = Path(output_dir)
    if not root.is_absolute():
        raise ValueError(f"output_dir must be absolute, got {output_dir!r}")
    identifier = str(pr_number)
    if not identifier or Path(identifier).name != identifier or identifier in {".", ".."}:
        raise ValueError(f"pr_number must be a path-safe identifier, got {pr_number!r}")

    raw_order = publication.get("artifact_order")
    raw_artifacts = publication.get("artifacts")
    if not isinstance(raw_order, list) or not isinstance(raw_artifacts, Mapping):
        raise ValueError("publication must contain artifact_order and artifacts")
    order = [str(name) for name in raw_order]
    if len(order) != len(set(order)) or set(order) != set(raw_artifacts):
        raise ValueError("artifact_order must name every artifact exactly once")
    if any(name not in _PUBLICATION_FILENAMES for name in order):
        raise ValueError("publication contains an unknown artifact")
    if not order or order[0] != "raw_findings":
        raise ValueError("raw_findings must be the first publication")
    if "local_findings" in order and order[-1] != "local_findings":
        raise ValueError("local_findings must be the final publication marker")
    if "review_receipt" in order and order[-1] != "review_receipt":
        raise ValueError("review_receipt must be published after raw findings and handoffs")

    root.mkdir(parents=True, exist_ok=True)
    final_paths = {
        name: root / filename.format(pr_number=identifier)
        for name, filename in _PUBLICATION_FILENAMES.items()
    }
    retired_names = [name for name in final_paths if name not in order]
    prior_bytes = {
        name: path.read_bytes() if path.exists() else None for name, path in final_paths.items()
    }
    documents = {
        name: (
            json.dumps(raw_artifacts[name], sort_keys=True, separators=(",", ":")) + "\n"
        ).encode()
        for name in order
    }
    staged: dict[str, Path] = {}
    changed: list[str] = []
    try:
        for name in order:
            staged[name] = _write_temp_bytes(
                root,
                final_paths[name].name,
                documents[name],
            )
        for name in order[:-1]:
            os.replace(staged[name], final_paths[name])
            staged.pop(name)
            changed.append(name)
        for name in retired_names:
            if final_paths[name].exists():
                final_paths[name].unlink()
                changed.append(name)
        marker_name = order[-1]
        os.replace(staged[marker_name], final_paths[marker_name])
        staged.pop(marker_name)
        changed.append(marker_name)
    except Exception as publication_error:
        rollback_error: Exception | None = None
        for temporary_path in staged.values():
            temporary_path.unlink(missing_ok=True)
        for name in reversed(changed):
            try:
                previous = prior_bytes[name]
                if previous is None:
                    final_paths[name].unlink(missing_ok=True)
                else:
                    rollback_path = _write_temp_bytes(
                        root,
                        final_paths[name].name,
                        previous,
                    )
                    os.replace(rollback_path, final_paths[name])
            except OSError as error:  # pragma: no cover - exceptional filesystem failure
                rollback_error = error
        if rollback_error is not None:
            raise RuntimeError(
                "publication failed and rollback was incomplete"
            ) from rollback_error
        raise RuntimeError(
            "experimental review artifact publication failed"
        ) from publication_error

    publication_records = [
        {
            "artifact": name,
            "byte_length": len(documents[name]),
            "path": str(final_paths[name]),
            "sha256": hashlib.sha256(documents[name]).hexdigest(),
        }
        for name in order
    ]
    return {
        "state": publication.get("state"),
        "published_paths": {name: str(final_paths[name]) for name in order},
        "publication_records": publication_records,
    }
