"""Shared helpers for issue-lifecycle server tests."""

from __future__ import annotations

from autoskillit.core import RetryReason, SkillResult


def _make_skill_result(
    success: bool = True,
    result: str = "",
    subtype: str = "",
    retry_reason: RetryReason = RetryReason.NONE,
    exit_code: int = 0,
    stderr: str = "",
    session_id: str = "sess-1",
) -> SkillResult:
    return SkillResult(
        success=success,
        result=result,
        session_id=session_id,
        subtype=subtype,
        is_error=not success,
        exit_code=exit_code,
        needs_retry=False,
        retry_reason=retry_reason,
        stderr=stderr,
        token_usage=None,
    )
