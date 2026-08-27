"""Envelope construction and atomic validation for proof-only review-auditor outputs."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from pathlib import Path

from autoskillit.smoke_utils._review_contracts import (
    EXPERIMENTAL_REVIEW_AUDITOR_REGISTRY,
    EXPERIMENTAL_REVIEW_AUDITORS,
    _closed_key_set_error,
    _is_non_empty_string,
)
from autoskillit.smoke_utils.review._constants import (
    _STANDARD_REVIEW_DIMENSIONS,
    _bounded_utf8,
)

_EXPERIMENTAL_DIMENSIONS = dict(EXPERIMENTAL_REVIEW_AUDITOR_REGISTRY)

_MAX_ENVELOPE_ERRORS = 32
_MAX_EXPERIMENTAL_OUTPUT_BYTES = 1024 * 1024

_STANDARD_FINDING_KEYS = {
    "file",
    "line",
    "dimension",
    "severity",
    "message",
    "requires_decision",
}
_REVIEW_SEVERITIES = {"critical", "warning", "info"}

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
    "exceptions_errors": ("exception", "exceptions", "error", "errors"),
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


def _is_positive_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


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
    tokens = set(
        "".join(
            character if character.isalnum() or character in {"_", "-"} else " "
            for character in normalized
        ).split()
    )
    return [
        facet
        for facet, accepted_terms in _SIMPLER_BEHAVIOR_FACETS.items()
        if not any(term in tokens for term in accepted_terms)
    ]


def _candidate_validation_error(
    candidate: object,
    *,
    auditor_name: str,
    valid_diff_lines: Mapping[str, Sequence[int]],
    review_root: Path,
) -> tuple[str, str] | None:
    key_error = _closed_key_set_error(
        candidate,
        expected=_EXPERIMENTAL_CANDIDATE_KEYS,
        subject="candidate",
    )
    if key_error is not None:
        return ("schema_invalid", key_error)
    assert isinstance(candidate, dict)
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
    key_error = _closed_key_set_error(
        finding,
        expected=_STANDARD_FINDING_KEYS,
        subject="finding",
    )
    if key_error is not None:
        return key_error
    assert isinstance(finding, dict)
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
