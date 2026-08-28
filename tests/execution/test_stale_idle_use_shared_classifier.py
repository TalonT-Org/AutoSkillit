"""Stall exits use the shared provider classification and retry policy."""

from __future__ import annotations

import json

import pytest

from autoskillit.core import (
    ChannelConfirmation,
    InfraExitCategory,
    RetryReason,
    SubprocessResult,
    TerminationReason,
)
from autoskillit.execution.backends.claude import ClaudeCodeBackend
from autoskillit.execution.headless._headless_result import _build_skill_result

pytestmark = [pytest.mark.layer("execution"), pytest.mark.medium]


def _provider_failure_result(
    termination: TerminationReason,
    *,
    api_error_status: int | None = None,
    assistant_message: str = "",
) -> SubprocessResult:
    records: list[dict[str, object]] = []
    if assistant_message:
        records.append(
            {
                "type": "assistant",
                "message": {"content": assistant_message},
            }
        )
    result_record: dict[str, object] = {
        "type": "result",
        "subtype": "empty_output",
        "is_error": True,
        "result": "",
        "session_id": "stall-provider-failure",
    }
    if api_error_status is not None:
        result_record["api_error_status"] = api_error_status
    records.append(result_record)
    return SubprocessResult(
        returncode=-1,
        stdout="\n".join(json.dumps(record) for record in records),
        stderr="",
        termination=termination,
        pid=12345,
        channel_confirmation=ChannelConfirmation.UNMONITORED,
    )


@pytest.mark.parametrize("termination", [TerminationReason.STALE, TerminationReason.IDLE_STALL])
def test_stall_with_textual_overload_uses_api_error_policy(termination: TerminationReason) -> None:
    skill_result = _build_skill_result(
        _provider_failure_result(termination, assistant_message="provider overloaded"),
        backend=ClaudeCodeBackend(),
    )

    assert skill_result.infra.exit_category == InfraExitCategory.API_ERROR.value
    assert skill_result.needs_retry is True
    assert skill_result.retry_reason is RetryReason.RESUME


@pytest.mark.parametrize("termination", [TerminationReason.STALE, TerminationReason.IDLE_STALL])
def test_stall_with_terminal_status_suppresses_retry(termination: TerminationReason) -> None:
    skill_result = _build_skill_result(
        _provider_failure_result(termination, api_error_status=403),
        backend=ClaudeCodeBackend(),
    )

    assert skill_result.infra.exit_category == InfraExitCategory.API_ERROR_TERMINAL.value
    assert skill_result.needs_retry is False
    assert skill_result.retry_reason is RetryReason.NONE


def test_stale_with_retriable_status_preserves_retry() -> None:
    skill_result = _build_skill_result(
        _provider_failure_result(TerminationReason.STALE, api_error_status=503),
        backend=ClaudeCodeBackend(),
    )

    assert skill_result.infra.exit_category == InfraExitCategory.API_ERROR.value
    assert skill_result.needs_retry is True
    assert skill_result.retry_reason is RetryReason.RESUME
