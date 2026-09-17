"""Dispatch-contract validation, field parsing, and outcome invariant evaluation.

Extracts declared output fields from session result text as ``KEY = value``
lines, typed per the contract's output declarations. Evaluates outcome
invariants and success qualifiers against parsed fields.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

import regex as re

from autoskillit.core import (
    RetryReason,
    SkillProjectionBinding,
    SkillResult,
    WorkspaceOutcomeKind,
    evaluate_outcome_expression,
    get_logger,
)

if TYPE_CHECKING:
    from autoskillit.core import WorkspaceOutcomeLedger, WorkspaceOutcomeRecord
    from autoskillit.recipe._contracts_types import (
        OutcomeInvariantEntry,
        SkillContract,
        SuccessQualifierEntry,
    )

logger = get_logger(__name__)

_FIELD_RE = re.compile(r"^(\w+)\s*=\s*(.*)$", re.MULTILINE)

_DISPOSITION_VALUES = frozenset({"applied", "skipped", "failed"})


@dataclasses.dataclass(frozen=True, slots=True)
class FindingDisposition:
    """One terminal session-reported disposition for an accepted finding."""

    finding_id: str
    disposition: str
    detail: str | None = None


def _trailing_field_lines(result_text: str) -> list[str]:
    """Return the authoritative trailing contiguous field block, in reverse order."""
    field_lines: list[str] = []
    collecting = False
    for line in reversed(result_text.splitlines()):
        if not collecting:
            if _FIELD_RE.match(line):
                collecting = True
                field_lines.append(line)
        elif _FIELD_RE.match(line):
            field_lines.append(line)
        else:
            break
    return field_lines


def validated_dispatch_cwd(
    capability_contract: SkillProjectionBinding | None,
    *,
    resolved_command: str,
    cwd: str,
) -> str:
    """Normalize cwd and reject primitives that disagree with immutable authority."""
    normalized_cwd = str(Path(cwd).resolve()) if cwd else ""
    if capability_contract is None:
        return normalized_cwd
    if not resolved_command:
        raise ValueError("headless command is empty")
    if capability_contract.cwd != normalized_cwd:
        raise ValueError("headless cwd does not match capability dispatch contract")
    members = set(capability_contract.member_names)
    if not members:
        raise ValueError("capability dispatch contract has no projected members")
    for field_name, values in (
        ("source identities", capability_contract.source_identities),
        ("canonical digests", capability_contract.canonical_digests),
        ("projected digests", capability_contract.projected_digests),
        ("semantic digests", capability_contract.semantic_digests),
        ("adaptation digests", capability_contract.adaptation_digests),
    ):
        if set(values) != members:
            raise ValueError(f"capability dispatch {field_name} do not match members")
    return capability_contract.cwd


def parse_outcome_fields(
    result_text: str,
    contract: SkillContract,
) -> dict[str, int | str]:
    """Extract declared output fields from the trailing contiguous block.

    Only fields declared in the contract's ``outputs`` are extracted.
    Walks backward from the end of the text, skipping non-field trailing
    lines (gate tags, blank lines), then collects the contiguous run of
    ``KEY = value`` lines. This ensures earlier prose containing field-like
    syntax cannot overwrite the authoritative final output block.

    Integer-typed fields are parsed to int; malformed integer values are
    recorded as the raw string (the invariant evaluator treats missing/
    malformed fields as violations when referenced by a ``require``).
    """
    declared = {o.name: o.type for o in contract.outputs if o.type != "dispositions"}

    parsed: dict[str, int | str] = {}
    for line in _trailing_field_lines(result_text):
        m = _FIELD_RE.match(line)
        if not m:
            continue
        name, raw_value = m.group(1), m.group(2).strip()
        if name not in declared:
            continue
        if declared[name] == "integer":
            try:
                parsed[name] = int(raw_value)
            except ValueError:
                parsed[name] = raw_value
        else:
            parsed[name] = raw_value
    return parsed


def parse_finding_dispositions(
    result_text: str,
    contract: SkillContract,
) -> tuple[list[FindingDisposition], list[str]]:
    """Parse repeated disposition rows from the authoritative output block."""
    if not any(
        output.name == "finding_disposition" and output.type == "dispositions"
        for output in contract.outputs
    ):
        return [], []

    dispositions: list[FindingDisposition] = []
    defects: list[str] = []
    seen_ids: set[str] = set()
    for line in reversed(_trailing_field_lines(result_text)):
        match = _FIELD_RE.match(line)
        if match is None or match.group(1) != "finding_disposition":
            continue
        parts = [part.strip() for part in match.group(2).split("|")]
        if len(parts) not in {2, 3} or not parts[0]:
            defects.append(f"malformed finding_disposition row: {match.group(2)!r}")
            continue
        finding_id, disposition = parts[0], parts[1]
        detail = parts[2] if len(parts) == 3 and parts[2] else None
        if disposition not in _DISPOSITION_VALUES:
            defects.append(f"finding {finding_id!r} has unknown disposition {disposition!r}")
            continue
        if finding_id in seen_ids:
            defects.append(f"duplicate finding disposition for {finding_id!r}")
            continue
        seen_ids.add(finding_id)
        if disposition == "applied" and detail is None:
            defects.append(f"applied finding {finding_id!r} is missing a commit SHA")
            continue
        dispositions.append(FindingDisposition(finding_id, disposition, detail))
    return dispositions, defects


def derive_outcome_counters(
    dispositions: Sequence[FindingDisposition],
) -> dict[str, int]:
    """Project the four gated counters from terminal finding dispositions."""
    return {
        "accept_count": len(dispositions),
        "fixes_applied": sum(item.disposition == "applied" for item in dispositions),
        "skipped_in_fix_phase": sum(item.disposition == "skipped" for item in dispositions),
        "fix_failures": sum(item.disposition == "failed" for item in dispositions),
    }


_DERIVED_COUNTER_NAMES = (
    "accept_count",
    "fixes_applied",
    "skipped_in_fix_phase",
    "fix_failures",
)
_SUCCESS_VERDICTS = frozenset({"real_fix", "already_green", "flake_suspected", "ci_only_failure"})


def _demote_outcome_report(
    sr: SkillResult,
    fields: dict[str, int | str],
    detail: str,
) -> SkillResult:
    logger.warning("outcome_report_malformed", detail=detail)
    return dataclasses.replace(
        sr,
        success=False,
        subtype="outcome_report_malformed",
        needs_retry=True,
        retry_reason=RetryReason.OUTCOME_REPORT_MALFORMED,
        result=detail,
        outcome_fields=fields,
    )


def _demote_outcome_evidence(
    sr: SkillResult,
    fields: dict[str, int | str],
    *,
    subtype: str,
    detail: str,
) -> SkillResult:
    logger.warning("outcome_evidence_failed", subtype=subtype, detail=detail)
    return dataclasses.replace(
        sr,
        success=False,
        subtype=subtype,
        needs_retry=True,
        retry_reason=RetryReason.OUTCOME_INVARIANT,
        result=detail,
        outcome_fields=fields,
    )


def _report_defect(
    emitted_fields: dict[str, int | str],
    derived_fields: dict[str, int],
    dispositions: list[FindingDisposition],
    defects: list[str],
) -> str | None:
    status = emitted_fields.get("review_status")
    if status == "no_pr":
        extra_fields = set(emitted_fields) - {"review_status"}
        if dispositions or defects or extra_fields:
            return "review_status=no_pr must be emitted without other outcome fields"
        return None
    if status != "processed":
        return "processed review result is missing review_status=processed"
    if defects:
        return "; ".join(defects)
    for name in _DERIVED_COUNTER_NAMES:
        if name in emitted_fields and emitted_fields[name] != derived_fields[name]:
            return (
                f"emitted {name}={emitted_fields[name]!r} disagrees with "
                f"server-derived {name}={derived_fields[name]}"
            )
    return None


def _read_outcome_records(
    outcome_ledger: WorkspaceOutcomeLedger | None,
    *,
    cwd: str,
    start_ts: str,
    end_ts: str,
) -> tuple[list[WorkspaceOutcomeRecord] | None, str | None]:
    if outcome_ledger is None:
        return None, "workspace outcome evidence is unavailable for disposition reconciliation"
    try:
        return outcome_ledger.read(cwd, since=start_ts, until=end_ts), None
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        return None, f"workspace outcome evidence is unavailable: {exc}"


def _commit_reconciliation_defect(
    dispositions: list[FindingDisposition],
    successful_commits: list[WorkspaceOutcomeRecord],
) -> str | None:
    observed_shas = {record.commit_sha for record in successful_commits}
    applied = [item for item in dispositions if item.disposition == "applied"]
    unobserved = sorted(item.detail or "" for item in applied if item.detail not in observed_shas)
    if unobserved:
        return "applied dispositions cite unobserved commit SHAs: " + ", ".join(unobserved)
    if len(applied) > len(successful_commits):
        return "applied disposition count exceeds observed successful commit attempts"
    return None


def _disposition_semantics_failure(
    dispositions: list[FindingDisposition],
    verdict: int | str | None,
) -> tuple[str, str] | None:
    applied = any(item.disposition == "applied" for item in dispositions)
    if any(item.disposition == "failed" for item in dispositions):
        return (
            "outcome_invariant_violation",
            "one or more accepted findings have a terminal failed disposition",
        )
    if verdict == "real_fix" and not applied:
        return (
            "outcome_invariant_violation",
            "verdict=real_fix requires at least one applied finding disposition",
        )
    if verdict in _SUCCESS_VERDICTS - {"real_fix"} and applied:
        return (
            "outcome_invariant_violation",
            f"verdict={verdict} cannot accompany an applied finding disposition",
        )
    if verdict not in _SUCCESS_VERDICTS:
        return (
            "outcome_report_malformed",
            "processed review result is missing a recognized verdict",
        )
    return None


def _recorded_at(recorded_at: str) -> datetime:
    return datetime.fromisoformat(recorded_at.replace("Z", "+00:00"))


def _test_evidence_failure(
    records: list[WorkspaceOutcomeRecord],
    successful_commits: list[WorkspaceOutcomeRecord],
) -> tuple[str, str] | None:
    test_records = [record for record in records if record.kind is WorkspaceOutcomeKind.TEST_RUN]
    if not test_records:
        return "test_evidence_missing", "processed review has no recorded test_check outcome"
    last_test = max(
        enumerate(test_records),
        key=lambda item: (_recorded_at(item[1].recorded_at), item[0]),
    )[1]
    if not last_test.succeeded:
        return "tests_not_green", "processed review's final recorded test_check did not pass"
    if successful_commits and _recorded_at(last_test.recorded_at) <= max(
        _recorded_at(record.recorded_at) for record in successful_commits
    ):
        return (
            "test_evidence_stale",
            "final passing test_check predates or coincides with a successful commit",
        )
    return None


def _apply_semantics_failure(
    sr: SkillResult,
    fields: dict[str, int | str],
    failure: tuple[str, str],
) -> SkillResult:
    subtype, detail = failure
    if subtype == "outcome_report_malformed":
        return _demote_outcome_report(sr, fields, detail)
    return _demote_outcome_evidence(sr, fields, subtype=subtype, detail=detail)


def apply_finding_disposition_adjudication(
    sr: SkillResult,
    emitted_fields: dict[str, int | str],
    skill_contract: SkillContract,
    *,
    cwd: str,
    outcome_ledger: WorkspaceOutcomeLedger | None,
    start_ts: str,
    end_ts: str,
) -> tuple[SkillResult, dict[str, int | str]]:
    """Reconcile terminal finding observations with server-owned workspace evidence."""
    if not any(output.type == "dispositions" for output in skill_contract.outputs):
        return sr, emitted_fields

    dispositions, defects = parse_finding_dispositions(sr.result, skill_contract)
    derived_fields = derive_outcome_counters(dispositions)
    fields = {**emitted_fields, **derived_fields}
    report_defect = _report_defect(emitted_fields, derived_fields, dispositions, defects)
    if report_defect is not None:
        return _demote_outcome_report(sr, fields, report_defect), fields
    if emitted_fields["review_status"] == "no_pr":
        return dataclasses.replace(sr, outcome_fields=emitted_fields), emitted_fields

    records, evidence_defect = _read_outcome_records(
        outcome_ledger,
        cwd=cwd,
        start_ts=start_ts,
        end_ts=end_ts,
    )
    if records is None:
        assert evidence_defect is not None
        return _demote_outcome_report(sr, fields, evidence_defect), fields
    successful_commits = [
        record
        for record in records
        if record.kind is WorkspaceOutcomeKind.COMMIT_ATTEMPT and record.succeeded
    ]
    commit_defect = _commit_reconciliation_defect(dispositions, successful_commits)
    if commit_defect is not None:
        return _demote_outcome_report(sr, fields, commit_defect), fields
    semantics_failure = _disposition_semantics_failure(dispositions, emitted_fields.get("verdict"))
    if semantics_failure is not None:
        return _apply_semantics_failure(sr, fields, semantics_failure), fields
    test_failure = _test_evidence_failure(records, successful_commits)
    if test_failure is not None:
        subtype, detail = test_failure
        return _demote_outcome_evidence(sr, fields, subtype=subtype, detail=detail), fields
    return dataclasses.replace(sr, outcome_fields=fields), fields


def evaluate_outcome_invariants(
    fields: dict[str, int | str],
    invariants: list[OutcomeInvariantEntry],
) -> tuple[bool, str]:
    """Evaluate outcome invariants against parsed fields.

    Returns (violated, detail). ``violated`` is True if any invariant's
    ``when`` predicate is satisfied but its ``require`` condition is not.
    A missing ``require`` field when ``when`` is true is a violation
    (fail-closed). A missing ``when`` field causes the invariant to be
    skipped (the field was never emitted — legitimate no-PR-found exit).
    """
    for inv in invariants:
        when_result = evaluate_outcome_expression(inv.when, fields)
        if when_result is None or not when_result:
            continue
        require_result = evaluate_outcome_expression(inv.require, fields)
        if require_result is None or not require_result:
            return True, f"invariant violated: when '{inv.when}' require '{inv.require}'"
    return False, ""


def evaluate_success_qualifier(
    fields: dict[str, int | str],
    qualifiers: list[SuccessQualifierEntry],
) -> str | None:
    """Evaluate success qualifiers against parsed fields.

    Returns the qualifier string if any qualifier's ``when`` predicate
    matches, or None if no qualifier applies.
    """
    for sq in qualifiers:
        when_result = evaluate_outcome_expression(sq.when, fields)
        if when_result is not None and when_result:
            return sq.qualifier
    return None
