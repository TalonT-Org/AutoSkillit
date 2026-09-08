"""Decision fields survive the real session-log projection."""

from __future__ import annotations

import json

import pytest

from autoskillit.core import (
    ChannelConfirmation,
    ProviderOutcome,
    RecipeIdentity,
    SessionTelemetry,
    SubprocessResult,
    TerminationReason,
)
from autoskillit.execution.backends.claude import ClaudeCodeBackend
from autoskillit.execution.headless._headless_result import _build_skill_result
from autoskillit.execution.session._session_model import parse_session_result
from autoskillit.execution.session_log import flush_session_log

pytestmark = [pytest.mark.layer("execution"), pytest.mark.medium]


def _stdout(*, status: int | None, is_error: bool, result: str) -> str:
    return json.dumps(
        {
            "type": "result",
            "subtype": "empty_output" if is_error else "success",
            "is_error": is_error,
            "result": result,
            "session_id": f"roundtrip-{status or 'success'}",
            "api_error_status": status,
        }
    )


@pytest.mark.parametrize(
    ("status", "is_error", "result"),
    [(429, True, ""), (404, True, ""), (None, False, "done")],
)
def test_session_decision_roundtrip(tmp_path, status, is_error, result) -> None:
    stdout = _stdout(status=status, is_error=is_error, result=result)
    parsed = parse_session_result(stdout)
    process = SubprocessResult(
        returncode=0,
        stdout=stdout,
        stderr="",
        termination=TerminationReason.NATURAL_EXIT,
        pid=123,
        channel_confirmation=ChannelConfirmation.UNMONITORED,
    )
    skill_result = _build_skill_result(process, backend=ClaudeCodeBackend())
    assert parsed.session_id == skill_result.session_id

    flush_session_log(
        log_dir=str(tmp_path),
        cwd=str(tmp_path),
        session_id=skill_result.session_id,
        pid=process.pid,
        skill_command="/test",
        success=skill_result.success,
        needs_retry=skill_result.needs_retry,
        retry_reason=skill_result.retry_reason.value,
        infra_exit_category=skill_result.infra.exit_category,
        infra_cleanup_incomplete=skill_result.infra.cleanup_incomplete,
        infra_fault_domain=skill_result.infra.fault_domain.value,
        api_error_status=skill_result.api_failure.status,
        is_error=skill_result.is_error,
        subtype=skill_result.subtype,
        exit_code=skill_result.exit_code,
        start_ts="2026-08-28T00:00:00+00:00",
        proc_snapshots=None,
        kill_reason=skill_result.kill_reason.value,
        provider_outcome=ProviderOutcome.none_used(),
        recipe_identity=RecipeIdentity.empty(),
        telemetry=SessionTelemetry.empty(),
    )

    summary = json.loads(
        (tmp_path / "sessions" / skill_result.session_id / "summary.json").read_text()
    )
    index = json.loads((tmp_path / "sessions.jsonl").read_text().strip())
    expected = {
        "needs_retry": skill_result.needs_retry,
        "retry_reason": skill_result.retry_reason.value,
        "infra_exit_category": skill_result.infra.exit_category,
        "api_error_status": skill_result.api_failure.status,
        "is_error": skill_result.is_error,
    }
    for key, value in expected.items():
        assert summary[key] == value
        assert index[key] == value


def _weekly_quota_replay_stdout() -> str:
    """One of the July 22-24 weekly-quota shapes from issue #4349: the CLI
    self-reports ``subtype: "success"``, but the seven-day rate limit rejected the
    request mid-session, so the result text never reaches the recipe's completion
    marker. This is the shape `normalize_subtype()`'s downward-normalization branch
    was written for — non-SUCCEEDED outcome + "success" cli_subtype + a result that
    doesn't contain the marker yields "missing_completion_marker".
    """
    records = [
        {"type": "system", "subtype": "init"},
        {"type": "user", "message": {"content": []}},
        {
            "type": "rate_limit_event",
            "rate_limit_info": {
                "status": "rejected",
                "resetsAt": 1785085200,
                "rateLimitType": "seven_day",
            },
        },
        {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "result": "Made partial progress before the session was cut off.",
            "session_id": "weekly-quota-replay",
            "api_error_status": 429,
            "errors": [],
        },
    ]
    return "\n".join(json.dumps(record) for record in records)


def test_historical_weekly_quota_replay_keeps_subtype_alongside_retry_fields(
    tmp_path,
) -> None:
    """Replay the fourteen-wasted-launches incident through the real, unmocked
    chain: the retained rate-limit fields (needs_retry, retry_reason,
    infra_exit_category, api_error_status) must land in the durable session log
    ALONGSIDE the pre-existing subtype="missing_completion_marker" classification,
    not in place of it. The subtype was never wrong, it was only ever incomplete —
    normalize_subtype() is exercised as-is and is not modified by this test.
    """
    stdout = _weekly_quota_replay_stdout()
    process = SubprocessResult(
        returncode=0,
        stdout=stdout,
        stderr="",
        termination=TerminationReason.NATURAL_EXIT,
        pid=456,
        channel_confirmation=ChannelConfirmation.UNMONITORED,
    )
    skill_result = _build_skill_result(
        process, completion_marker="%%ORDER_UP%%", backend=ClaudeCodeBackend()
    )
    assert skill_result.subtype == "missing_completion_marker"

    flush_session_log(
        log_dir=str(tmp_path),
        cwd=str(tmp_path),
        session_id=skill_result.session_id,
        pid=process.pid,
        skill_command="/test",
        success=skill_result.success,
        needs_retry=skill_result.needs_retry,
        retry_reason=skill_result.retry_reason.value,
        infra_exit_category=skill_result.infra.exit_category,
        infra_cleanup_incomplete=skill_result.infra.cleanup_incomplete,
        infra_fault_domain=skill_result.infra.fault_domain.value,
        api_error_status=skill_result.api_failure.status,
        is_error=skill_result.is_error,
        subtype=skill_result.subtype,
        exit_code=skill_result.exit_code,
        start_ts="2026-08-28T00:00:00+00:00",
        proc_snapshots=None,
        kill_reason=skill_result.kill_reason.value,
        provider_outcome=ProviderOutcome.none_used(),
        recipe_identity=RecipeIdentity.empty(),
        telemetry=SessionTelemetry.empty(),
    )

    summary = json.loads(
        (tmp_path / "sessions" / skill_result.session_id / "summary.json").read_text()
    )
    index = json.loads((tmp_path / "sessions.jsonl").read_text().strip())
    expected = {
        "infra_exit_category": "rate_limited",
        "needs_retry": True,
        "retry_reason": "rate_limited",
        "api_error_status": 429,
        "subtype": "missing_completion_marker",
    }
    for key, value in expected.items():
        assert summary[key] == value, f"summary.json[{key!r}] != {value!r}"
        assert index[key] == value, f"sessions.jsonl[{key!r}] != {value!r}"
