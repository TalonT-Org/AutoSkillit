# Adjudication helpers extracted from _headless_result.py
"""Adjudication helpers for headless Claude session SkillResult construction.

Extracted from `_headless_result.py`. This module owns the post-session
adjudication chain: parse-stdout, build-api-retry-outcome, make-terminated-
result, out-of-cwd-file-change detection, post-session-adjudication, and
declared-artifact validation. The SkillResult constructor itself
(`_build_skill_result`) remains in the parent module because it is
the headless orchestration authority.
"""

from __future__ import annotations

import dataclasses
import errno
import stat
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, cast

from autoskillit.core import (
    AGENT_BACKEND_CLAUDE_CODE,
    ApiRetryOutcome,
    InfraOutcome,
    NdjsonDriftOutcome,
    ProviderOutcome,
    RetryReason,
    SkillResult,
    WriteBehaviorSpec,
    WriteEvidence,
    get_logger,
)
from autoskillit.execution.headless._headless_evidence import _adapt_agent_result
from autoskillit.execution.headless._headless_outcome import (
    evaluate_outcome_invariants,
    parse_outcome_fields,
)
from autoskillit.execution.headless._headless_path_tokens import _is_path_outside_cwd
from autoskillit.execution.session._session_content import _check_expected_patterns
from autoskillit.execution.session._session_model import (
    ClaudeSessionResult,
    parse_session_result,
)

if TYPE_CHECKING:
    from autoskillit.core import CodingAgentBackend, SubprocessResult
    from autoskillit.recipe._contracts_types import SkillContract

logger = get_logger(__name__)


def _resolve_skill_session_id(
    session: ClaudeSessionResult | None,
    result: SubprocessResult,
) -> str:
    """Return the best-available Claude session UUID."""
    if session is not None and session.session_id:
        return session.session_id
    return result.session_id or result.channel_b_session_id


def _parse_stdout(stdout: str, backend: CodingAgentBackend) -> ClaudeSessionResult:
    if backend.name == AGENT_BACKEND_CLAUDE_CODE:
        return parse_session_result(stdout)
    agent_result = backend.result_parser().parse_stdout(stdout)
    return _adapt_agent_result(agent_result)


def _build_api_retry_outcome(session: ClaudeSessionResult) -> ApiRetryOutcome:
    return ApiRetryOutcome(
        count=session.api_retry_count,
        last_error=session.api_retry_last_error,
        last_status=session.api_retry_last_status,
        exhausted=session.api_retry_exhausted,
    )


def _make_terminated_result(
    *,
    result: SubprocessResult,
    session: ClaudeSessionResult,
    success: bool,
    result_text: str,
    subtype: str,
    needs_retry: bool,
    retry_reason: RetryReason,
    evidence: WriteEvidence,
    provider_used: str = "",
    infra: InfraOutcome = InfraOutcome(),
    api_retry: ApiRetryOutcome = ApiRetryOutcome(),
) -> SkillResult:
    """Construct SkillResult for infrastructure-terminated sessions (stale/idle_stall)."""
    return SkillResult(
        success=success,
        result=result_text,
        session_id=session.session_id or _resolve_skill_session_id(session, result),
        subtype=subtype,
        is_error=session.is_error if success else False,
        exit_code=result.returncode if result.returncode is not None else -1,
        needs_retry=needs_retry,
        retry_reason=retry_reason,
        stderr=result.stderr if result.stderr else "",
        token_usage=session.token_usage,
        evidence=evidence,
        kill_reason=result.kill_reason,
        last_stop_reason=session.last_stop_reason,
        lifespan_started=session.lifespan_started,
        provider=ProviderOutcome(provider_used=provider_used, fallback_activated=False),
        infra=infra,
        api_retry=api_retry,
        ndjson_drift=NdjsonDriftOutcome(
            unknown_event_count=session.seen_ndjson_unknown_event_count,
            unknown_item_count=session.seen_ndjson_unknown_item_count,
        ),
    )


def _has_out_of_cwd_file_change(file_changes: Sequence[str], cwd: str) -> bool:
    """Return True iff any raw Codex FILE_CHANGE path lexically resolves outside cwd.

    Empty/invalid entries are ignored. If cwd is missing, relative, or ``/``,
    no boundary proof is produced — matching the validator's safety contract.
    """
    for path in file_changes:
        if not isinstance(path, str) or not path:
            continue
        if _is_path_outside_cwd(path, cwd, allow_relative=True):
            return True
    return False


def _apply_post_session_adjudication(
    sr: SkillResult,
    evidence: WriteEvidence,
    write_behavior: WriteBehaviorSpec | None,
    skill_contract: SkillContract | None,
    cwd: str,
) -> SkillResult:
    """Apply write, invariant, and declared-artifact contract checks.

    Invoked as the last adjudication step before each success-finalizing
    return. Makes "a success path that skips adjudication" unrepresentable.
    """
    fields = parse_outcome_fields(sr.result, skill_contract) if skill_contract else {}
    if fields:
        sr = dataclasses.replace(sr, outcome_fields=fields)

    if not sr.success:
        return sr

    if not evidence.has_implementation_evidence and write_behavior is not None:
        write_expected = False
        if write_behavior.mode == "always":
            write_expected = True
        elif write_behavior.mode == "conditional" and write_behavior.expected_when:
            write_expected = _check_expected_patterns(
                sr.result,
                write_behavior.expected_when,
            )
        if write_expected:
            return dataclasses.replace(
                sr,
                success=False,
                subtype="zero_writes",
                needs_retry=True,
                retry_reason=RetryReason.ZERO_WRITES,
            )

    if skill_contract is not None and skill_contract.outcome_invariants:
        violated, detail = evaluate_outcome_invariants(fields, skill_contract.outcome_invariants)
        if violated:
            logger.warning("outcome_invariant_violated", detail=detail)
            return dataclasses.replace(
                sr,
                success=False,
                subtype="outcome_invariant_violation",
                needs_retry=True,
                retry_reason=RetryReason.OUTCOME_INVARIANT,
                outcome_fields=None,
            )

    if skill_contract is not None:
        for output in skill_contract.outputs:
            value = fields.get(output.name)
            if output.type != "file_path" or value is None:
                continue
            failure = _validate_declared_artifact(cwd, output.name, cast(str, value))
            if failure is not None:
                subtype, detail = failure
                retry_reason = (
                    RetryReason.CONTRACT_RECOVERY
                    if subtype == "artifact_contract_violation"
                    else RetryReason.RESUME
                )
                return dataclasses.replace(
                    sr,
                    success=False,
                    is_error=True,
                    subtype=subtype,
                    needs_retry=True,
                    retry_reason=retry_reason,
                    result=detail,
                    outcome_fields=None,
                )

    return sr


def _validate_declared_artifact(cwd: str, field_name: str, value: str) -> tuple[str, str] | None:
    """Validate one emitted ``file_path`` without exposing unsafe paths."""
    safe_name = "."
    producer_detail = (
        f"Skill output '{field_name}' did not identify a contained regular file: {safe_name}"
    )
    infrastructure_detail = (
        f"Could not validate skill output '{field_name}' because filesystem access failed."
    )
    try:
        root = Path(cwd).resolve()
        candidate = Path(value)
        safe_name = candidate.name or "."
        producer_detail = (
            f"Skill output '{field_name}' did not identify a contained regular file: {safe_name}"
        )
        target = (
            (root / candidate).resolve() if not candidate.is_absolute() else candidate.resolve()
        )
        try:
            target.relative_to(root)
        except ValueError:
            return "artifact_contract_violation", producer_detail
        target_stat = target.stat()
        if not stat.S_ISREG(target_stat.st_mode):
            return "artifact_contract_violation", producer_detail
    except (TypeError, ValueError, RuntimeError):
        return "artifact_contract_violation", producer_detail
    except OSError as exc:
        if exc.errno in {errno.ENOENT, errno.ENOTDIR, errno.ELOOP}:
            return "artifact_contract_violation", producer_detail
        logger.warning(
            "artifact_adjudication_error",
            field_name=field_name,
            artifact_name=safe_name,
            exc_info=True,
        )
        return "artifact_adjudication_error", infrastructure_detail
    return None
