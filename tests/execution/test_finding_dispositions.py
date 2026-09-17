"""Server-derived finding dispositions and workspace-outcome reconciliation."""

from __future__ import annotations

import dataclasses
import json
from datetime import datetime
from pathlib import Path

import pytest

from autoskillit.core import (
    CommitFailureClass,
    RetryReason,
    TerminationReason,
    WorkspaceOutcomeKind,
    WorkspaceOutcomeLedger,
    WorkspaceOutcomeRecord,
    WriteBehaviorSpec,
    WriteEvidence,
)
from autoskillit.core.types._type_results import ApiRetryOutcome, SkillResult
from autoskillit.execution.backends.claude import ClaudeCodeBackend
from autoskillit.execution.headless import _build_skill_result
from autoskillit.execution.headless._headless_adjudication import (
    _apply_outcome_qualifier,
    _apply_post_session_adjudication,
)
from autoskillit.execution.headless._headless_outcome import (
    FindingDisposition,
    derive_outcome_counters,
    parse_finding_dispositions,
)
from autoskillit.recipe import SkillContract, SkillOutput
from autoskillit.recipe.contracts._contracts_types import (
    OutcomeInvariantEntry,
    SuccessQualifierEntry,
)
from tests.conftest import _make_result

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]

_START = "2026-09-16T10:00:00+00:00"
_END = "2026-09-16T10:01:00+00:00"


def _contract() -> SkillContract:
    return SkillContract(
        inputs=(),
        outputs=[
            SkillOutput("review_status", "string", ["processed", "no_pr"]),
            SkillOutput("finding_disposition", "dispositions"),
            SkillOutput(
                "verdict",
                "string",
                ["real_fix", "already_green", "flake_suspected", "ci_only_failure"],
            ),
            SkillOutput("accept_count", "integer"),
            SkillOutput("fixes_applied", "integer"),
            SkillOutput("skipped_in_fix_phase", "integer"),
            SkillOutput("fix_failures", "integer"),
        ],
        outcome_invariants=[
            OutcomeInvariantEntry(
                when="accept_count > 0",
                require="fix_failures == 0",
            )
        ],
        success_qualifiers=[
            SuccessQualifierEntry(
                when="accept_count > 0 and fixes_applied == 0 and fix_failures == 0",
                qualifier="accepted_without_changes",
            )
        ],
    )


def _skill_result(result_text: str, *, success: bool = True) -> SkillResult:
    return SkillResult(
        success=success,
        result=result_text,
        session_id="test-session",
        subtype="success" if success else "path_contamination",
        is_error=not success,
        exit_code=0 if success else 1,
        needs_retry=not success,
        retry_reason=RetryReason.NONE if success else RetryReason.PATH_CONTAMINATION,
        stderr="",
        api_retry=ApiRetryOutcome(),
    )


def _record(
    workspace: Path,
    at: str,
    *,
    kind: WorkspaceOutcomeKind,
    succeeded: bool,
    sha: str | None = None,
) -> WorkspaceOutcomeRecord:
    return WorkspaceOutcomeRecord(
        workspace=str(workspace),
        recorded_at=at,
        kind=kind,
        succeeded=succeeded,
        commit_sha=sha,
        failure_class=(
            CommitFailureClass.GIT_COMMIT_FAILED
            if kind is WorkspaceOutcomeKind.COMMIT_ATTEMPT and not succeeded
            else None
        ),
    )


class _MemoryOutcomeLedger:
    def __init__(self) -> None:
        self.records: list[WorkspaceOutcomeRecord] = []

    def record(self, record: WorkspaceOutcomeRecord) -> None:
        self.records.append(record)

    def read(
        self,
        workspace: str,
        *,
        since: str,
        until: str,
    ) -> list[WorkspaceOutcomeRecord]:
        start = datetime.fromisoformat(since.replace("Z", "+00:00"))
        end = datetime.fromisoformat(until.replace("Z", "+00:00"))
        canonical = Path(workspace).resolve()
        return [
            record
            for record in self.records
            if Path(record.workspace).resolve() == canonical
            and start <= datetime.fromisoformat(record.recorded_at) <= end
        ]


def _ledger(
    records: list[WorkspaceOutcomeRecord],
) -> _MemoryOutcomeLedger:
    ledger = _MemoryOutcomeLedger()
    for record in records:
        ledger.record(record)
    return ledger


def _adjudicate(
    text: str,
    workspace: Path,
    ledger: WorkspaceOutcomeLedger | None,
    *,
    success: bool = True,
) -> SkillResult:
    return _apply_post_session_adjudication(
        _skill_result(text, success=success),
        WriteEvidence(1, False, False),
        WriteBehaviorSpec(
            mode="conditional",
            expected_when=(r"verdict[ \t]*=[ \t]*real_fix",),
        ),
        _contract(),
        str(workspace),
        outcome_ledger=ledger,
        start_ts=_START,
        end_ts=_END,
    )


def _processed(verdict: str, rows: list[str], *extra: str) -> str:
    return "\n".join(["review_status = processed", f"verdict = {verdict}", *rows, *extra])


@pytest.mark.parametrize(
    "dispositions",
    [
        [],
        [FindingDisposition("A", "applied", "a1")],
        [FindingDisposition("A", "skipped", "not actionable")],
        [FindingDisposition("A", "failed", "tests remain red")],
        [
            FindingDisposition("A", "applied", "a1"),
            FindingDisposition("B", "skipped", "not actionable"),
            FindingDisposition("C", "failed", "tests remain red"),
        ],
    ],
)
def test_derived_counters_form_a_partition(
    dispositions: list[FindingDisposition],
) -> None:
    fields = derive_outcome_counters(dispositions)

    assert fields["accept_count"] == (
        fields["fixes_applied"] + fields["skipped_in_fix_phase"] + fields["fix_failures"]
    )


def test_historical_all_applied_report_preserves_success(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    rows = [f"finding_disposition = F-{index} | applied | sha-{index}" for index in range(13)]
    records = [
        _record(
            workspace,
            f"2026-09-16T10:00:{index + 1:02d}+00:00",
            kind=WorkspaceOutcomeKind.COMMIT_ATTEMPT,
            succeeded=True,
            sha=f"sha-{index}",
        )
        for index in range(13)
    ]
    records.append(
        _record(
            workspace,
            "2026-09-16T10:00:30+00:00",
            kind=WorkspaceOutcomeKind.TEST_RUN,
            succeeded=True,
        )
    )

    result = _adjudicate(
        _processed("real_fix", rows),
        workspace,
        _ledger(records),
    )

    assert result.success is True
    assert result.outcome_fields is not None
    assert result.outcome_fields["accept_count"] == 13
    assert result.outcome_fields["fixes_applied"] == 13
    assert result.outcome_fields["fix_failures"] == 0


def test_failed_commit_attempts_do_not_change_terminal_disposition(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    records = [
        _record(
            workspace,
            f"2026-09-16T10:00:0{index}+00:00",
            kind=WorkspaceOutcomeKind.COMMIT_ATTEMPT,
            succeeded=False,
        )
        for index in (1, 2)
    ]
    records.extend(
        [
            _record(
                workspace,
                "2026-09-16T10:00:03+00:00",
                kind=WorkspaceOutcomeKind.COMMIT_ATTEMPT,
                succeeded=True,
                sha="landed",
            ),
            _record(
                workspace,
                "2026-09-16T10:00:04+00:00",
                kind=WorkspaceOutcomeKind.TEST_RUN,
                succeeded=True,
            ),
        ]
    )

    result = _adjudicate(
        _processed("real_fix", ["finding_disposition = F-1 | applied | landed"]),
        workspace,
        _ledger(records),
    )

    assert result.success is True
    assert result.outcome_fields is not None
    assert result.outcome_fields["fix_failures"] == 0


def test_lying_model_real_fix_with_fix_failures_demoted(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    ledger = _ledger(
        [
            _record(
                workspace,
                "2026-09-16T10:00:01+00:00",
                kind=WorkspaceOutcomeKind.TEST_RUN,
                succeeded=True,
            )
        ],
    )

    result = _adjudicate(
        _processed("real_fix", ["finding_disposition = F-1 | failed | still red"]),
        workspace,
        ledger,
    )

    assert result.retry_reason is RetryReason.OUTCOME_INVARIANT
    assert result.subtype == "outcome_invariant_violation"


def test_legitimate_all_skipped_already_green_qualified_not_demoted(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    ledger = _ledger(
        [
            _record(
                workspace,
                "2026-09-16T10:00:01+00:00",
                kind=WorkspaceOutcomeKind.TEST_RUN,
                succeeded=True,
            )
        ],
    )

    result = _adjudicate(
        _processed("already_green", ["finding_disposition = F-1 | skipped | invalid"]),
        workspace,
        ledger,
    )
    result = _apply_outcome_qualifier(result, _contract())

    assert result.success is True
    assert result.outcome_qualifier == "accepted_without_changes"


@pytest.mark.parametrize(
    ("text", "ledger_present", "expected_detail"),
    [
        (
            _processed(
                "real_fix",
                ["finding_disposition = F-1 | applied | absent"],
            ),
            True,
            "unobserved commit SHAs",
        ),
        (
            _processed(
                "real_fix",
                ["finding_disposition = F-1 | applied | absent"],
            ),
            False,
            "evidence is unavailable",
        ),
        (
            _processed(
                "already_green",
                ["finding_disposition = F-1 | skipped | invalid"],
                "fixes_applied = 1",
            ),
            True,
            "disagrees with server-derived",
        ),
        (
            _processed(
                "real_fix",
                [
                    "finding_disposition = F-1 | applied | sha-1",
                    "finding_disposition = F-1 | applied | sha-1",
                ],
            ),
            True,
            "duplicate finding disposition",
        ),
    ],
)
def test_malformed_reports_have_a_distinct_failure(
    tmp_path: Path,
    text: str,
    ledger_present: bool,
    expected_detail: str,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    ledger = _ledger([]) if ledger_present else None

    result = _adjudicate(text, workspace, ledger)

    assert result.retry_reason is RetryReason.OUTCOME_REPORT_MALFORMED
    assert result.subtype == "outcome_report_malformed"
    assert expected_detail in result.result
    assert result.outcome_fields is not None


@pytest.mark.parametrize(
    ("records", "expected_subtype"),
    [
        ([], "test_evidence_missing"),
        (
            [("2026-09-16T10:00:01+00:00", WorkspaceOutcomeKind.TEST_RUN, False, None)],
            "tests_not_green",
        ),
        (
            [
                ("2026-09-16T10:00:01+00:00", WorkspaceOutcomeKind.TEST_RUN, True, None),
                (
                    "2026-09-16T10:00:02+00:00",
                    WorkspaceOutcomeKind.COMMIT_ATTEMPT,
                    True,
                    "landed",
                ),
            ],
            "test_evidence_stale",
        ),
    ],
)
def test_processed_review_requires_fresh_green_tests(
    tmp_path: Path,
    records: list[tuple[str, WorkspaceOutcomeKind, bool, str | None]],
    expected_subtype: str,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    ledger = _ledger(
        [
            _record(workspace, at, kind=kind, succeeded=succeeded, sha=sha)
            for at, kind, succeeded, sha in records
        ],
    )

    result = _adjudicate(_processed("already_green", []), workspace, ledger)

    assert result.retry_reason is RetryReason.OUTCOME_INVARIANT
    assert result.subtype == expected_subtype
    assert result.outcome_fields is not None


def test_malformed_workspace_record_timestamps_demote_to_report_malformed(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    good_record = _record(
        workspace,
        "2026-09-16T10:00:01+00:00",
        kind=WorkspaceOutcomeKind.TEST_RUN,
        succeeded=True,
    )
    corrupted = dataclasses.replace(good_record)
    object.__setattr__(corrupted, "recorded_at", "not-a-timestamp")
    ledger = _CorruptedTimestampLedger(workspace, [corrupted])

    result = _adjudicate(_processed("already_green", []), workspace, ledger)

    assert result.retry_reason is RetryReason.OUTCOME_REPORT_MALFORMED
    assert result.subtype == "outcome_report_malformed"
    assert result.outcome_fields is not None


class _CorruptedTimestampLedger:
    """Test-only ledger that returns records whose ``recorded_at`` is unparseable."""

    def __init__(self, workspace: Path, records: list[WorkspaceOutcomeRecord]) -> None:
        self._workspace = workspace
        self._records = records

    def record(self, record: WorkspaceOutcomeRecord) -> None:
        self._records.append(record)

    def read(
        self,
        workspace: str,
        *,
        since: str,
        until: str,
    ) -> list[WorkspaceOutcomeRecord]:
        canonical = Path(workspace).resolve()
        return [
            record for record in self._records if Path(record.workspace).resolve() == canonical
        ]


@pytest.mark.parametrize(
    ("sequence", "expected_success"),
    [([False, True], True), ([True, False], False)],
)
def test_last_test_is_selected_chronologically(
    tmp_path: Path,
    sequence: list[bool],
    expected_success: bool,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    ledger = _ledger(
        [
            _record(
                workspace,
                f"2026-09-16T10:00:0{index + 1}+00:00",
                kind=WorkspaceOutcomeKind.TEST_RUN,
                succeeded=succeeded,
            )
            for index, succeeded in enumerate(sequence)
        ],
    )

    result = _adjudicate(_processed("already_green", []), workspace, ledger)

    assert result.success is expected_success
    if not expected_success:
        assert result.subtype == "tests_not_green"


def test_evidence_time_window_filter_excludes_out_of_window_records(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    ledger = _ledger(
        [
            _record(
                workspace,
                "2026-09-16T10:00:01+00:00",
                kind=WorkspaceOutcomeKind.TEST_RUN,
                succeeded=True,
            ),
            _record(
                workspace,
                "2026-09-16T10:02:00+00:00",
                kind=WorkspaceOutcomeKind.TEST_RUN,
                succeeded=False,
            ),
        ],
    )

    result = _adjudicate(_processed("already_green", []), workspace, ledger)

    assert result.success is True


def test_evidence_workspace_filter_excludes_other_workspace_records(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    other = tmp_path / "other"
    workspace.mkdir()
    other.mkdir()
    ledger = _ledger(
        [
            _record(
                workspace,
                "2026-09-16T10:00:01+00:00",
                kind=WorkspaceOutcomeKind.TEST_RUN,
                succeeded=True,
            ),
            _record(
                other,
                "2026-09-16T10:00:02+00:00",
                kind=WorkspaceOutcomeKind.TEST_RUN,
                succeeded=False,
            ),
        ],
    )

    result = _adjudicate(_processed("already_green", []), workspace, ledger)

    assert result.success is True


def test_unreadable_evidence_is_malformed(tmp_path: Path) -> None:
    class UnreadableLedger:
        def read(self, workspace: str, *, since: str, until: str) -> list[WorkspaceOutcomeRecord]:
            raise RuntimeError("evidence is incomplete")

    workspace = tmp_path / "workspace"
    workspace.mkdir()

    result = _apply_post_session_adjudication(
        _skill_result(_processed("already_green", [])),
        WriteEvidence(1, False, False),
        None,
        _contract(),
        str(workspace),
        outcome_ledger=UnreadableLedger(),
        start_ts=_START,
        end_ts=_END,
    )

    assert result.retry_reason is RetryReason.OUTCOME_REPORT_MALFORMED
    assert "evidence is incomplete" in result.result


def test_no_pr_status_clean_exit(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    no_pr = _adjudicate("review_status = no_pr", workspace, None)

    assert no_pr.success is True
    assert no_pr.outcome_fields == {"review_status": "no_pr"}


def test_missing_review_status_demotes_to_malformed(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    missing = _adjudicate("verdict = already_green", workspace, None)

    assert missing.retry_reason is RetryReason.OUTCOME_REPORT_MALFORMED


def test_failed_session_anchor_is_preserved_as_path_contamination(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    failed = _adjudicate(
        _processed("real_fix", ["finding_disposition = F-1 | applied | absent"]),
        workspace,
        None,
        success=False,
    )

    assert failed.retry_reason is RetryReason.PATH_CONTAMINATION
    assert failed.subtype == "path_contamination"


def test_red_test_demotes_on_stall_recovery_path(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    ledger = _ledger(
        [
            _record(
                workspace,
                "2026-09-16T10:00:01+00:00",
                kind=WorkspaceOutcomeKind.TEST_RUN,
                succeeded=False,
            )
        ],
    )
    text = _processed("already_green", [])
    stdout = json.dumps(
        {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "result": text,
            "session_id": "test-session",
        }
    )
    subprocess_result = dataclasses.replace(
        _make_result(
            stdout=stdout,
            termination_reason=TerminationReason.STALE,
        ),
        start_ts=_START,
        end_ts=_END,
    )

    result = _build_skill_result(
        subprocess_result,
        cwd=str(workspace),
        skill_contract=_contract(),
        outcome_ledger=ledger,
        backend=ClaudeCodeBackend(),
    )

    assert result.retry_reason is RetryReason.OUTCOME_INVARIANT
    assert result.subtype == "tests_not_green"


def test_parser_reports_duplicate_and_malformed_rows() -> None:
    contract = _contract()
    dispositions, defects = parse_finding_dispositions(
        "\n".join(
            [
                "review_status = processed",
                "finding_disposition = F-1 | applied | sha-1",
                "finding_disposition = F-1 | skipped | duplicate",
                "finding_disposition = F-2 | applied",
                "finding_disposition = F-3 | unknown | detail",
            ]
        ),
        contract,
    )

    assert dispositions == [FindingDisposition("F-1", "applied", "sha-1")]
    assert len(defects) == 3
