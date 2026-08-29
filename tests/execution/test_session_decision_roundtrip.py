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
