"""Group F: Core failure path semantics — timeout, no-sentinel, completed-dirty, clean."""

from __future__ import annotations

import pytest

from autoskillit.core.types import CliSubtype
from tests.fakes import InMemoryHeadlessExecutor
from tests.fleet._helpers import (
    _make_completed_clean,
    _make_completed_dirty,
    _make_no_sentinel,
    _read_dispatch_record,
    _run,
    _setup_dispatch,
)

pytestmark = [pytest.mark.layer("fleet"), pytest.mark.small, pytest.mark.feature("fleet")]


class TestTimeoutPath:
    @pytest.mark.anyio
    async def test_timeout_returns_fleet_error_envelope(self, tool_ctx, monkeypatch):
        """Timeout without session/sidecar → FAILURE envelope with error='l3_timeout'."""
        import dataclasses

        from tests.fakes import _DEFAULT_SKILL_RESULT

        _setup_dispatch(tool_ctx, monkeypatch)
        tool_ctx.executor = InMemoryHeadlessExecutor(
            default_result=dataclasses.replace(_DEFAULT_SKILL_RESULT, subtype="timeout")
        )

        result = await _run(tool_ctx)
        assert result["success"] is False
        assert result["reason"] == "fleet_l3_timeout"

    @pytest.mark.anyio
    async def test_timeout_writes_state_with_reason_l3_timeout(self, tool_ctx, monkeypatch):
        """Timeout without session/sidecar → DispatchRecord.status=failure, reason=l3_timeout."""
        import dataclasses

        from tests.fakes import _DEFAULT_SKILL_RESULT

        _setup_dispatch(tool_ctx, monkeypatch)
        tool_ctx.executor = InMemoryHeadlessExecutor(
            default_result=dataclasses.replace(_DEFAULT_SKILL_RESULT, subtype="timeout")
        )

        await _run(tool_ctx)

        record = _read_dispatch_record(tool_ctx)
        assert record["status"] == "failure"
        assert record["reason"] == "fleet_l3_timeout"

    @pytest.mark.anyio
    async def test_timeout_skips_parse_l3_result_block(self, tool_ctx, monkeypatch):
        """Timeout path must not call parse_l3_result_block."""
        import dataclasses

        from tests.fakes import _DEFAULT_SKILL_RESULT

        _setup_dispatch(tool_ctx, monkeypatch)
        tool_ctx.executor = InMemoryHeadlessExecutor(
            default_result=dataclasses.replace(_DEFAULT_SKILL_RESULT, subtype="timeout")
        )

        def _should_not_be_called(**_kwargs):
            raise AssertionError("parse_l3_result_block called on timeout path")

        monkeypatch.setattr(
            "autoskillit.fleet._api.parse_l3_result_block",
            _should_not_be_called,
        )

        # Should succeed (return l3_timeout envelope) without raising
        result = await _run(tool_ctx)
        assert result["reason"] == "fleet_l3_timeout"

    @pytest.mark.anyio
    async def test_timeout_envelope_includes_dispatch_metadata(self, tool_ctx, monkeypatch):
        """Timeout envelope includes dispatch_id, dispatched_session_id, and token_usage."""
        import dataclasses

        from tests.fakes import _DEFAULT_SKILL_RESULT

        _setup_dispatch(tool_ctx, monkeypatch)
        tool_ctx.executor = InMemoryHeadlessExecutor(
            default_result=dataclasses.replace(
                _DEFAULT_SKILL_RESULT,
                subtype="timeout",
                session_id="sess-timeout-123",
                token_usage={"input_tokens": 50},
            )
        )

        from autoskillit.fleet.state import normalize_dispatch_token_usage

        result = await _run(tool_ctx)
        assert "dispatch_id" in result
        assert result["dispatched_session_id"] == "sess-timeout-123"
        assert result["token_usage"] == normalize_dispatch_token_usage({"input_tokens": 50})

    @pytest.mark.anyio
    async def test_idle_stall_falls_through_to_parse(self, tool_ctx, monkeypatch):
        """idle_stall subtype must NOT trigger the timeout pre-check; parse is called."""
        import dataclasses

        from tests.fakes import _DEFAULT_SKILL_RESULT

        _setup_dispatch(tool_ctx, monkeypatch)
        tool_ctx.executor = InMemoryHeadlessExecutor(
            default_result=dataclasses.replace(
                _DEFAULT_SKILL_RESULT,
                subtype="idle_stall",
                success=False,
            )
        )

        parse_called = []

        def _recording_parse(**kwargs):
            parse_called.append(True)
            return _make_no_sentinel()

        monkeypatch.setattr("autoskillit.fleet._api.parse_l3_result_block", _recording_parse)

        await _run(tool_ctx)
        assert parse_called, "parse_l3_result_block was not called for idle_stall"

    @pytest.mark.anyio
    async def test_timed_out_unparseable_triggers_timeout_precheck(self, tool_ctx, monkeypatch):
        """TIMED_OUT + UNPARSEABLE subtype must trigger the fleet timeout precheck.

        Regression test: before the fix, TIMED_OUT sessions with non-SUCCESS parsed
        subtypes (e.g., UNPARSEABLE from partial stdout with no type=result record)
        would leak subtype='unparseable' through normalize_subtype, bypassing the
        timeout precheck at _api.py:476 (subtype == 'timeout') and causing the dispatch
        to be misclassified as fleet_l3_no_result_block instead of fleet_l3_timeout.
        """
        import dataclasses

        from tests.fakes import _DEFAULT_SKILL_RESULT

        _setup_dispatch(tool_ctx, monkeypatch)
        tool_ctx.executor = InMemoryHeadlessExecutor(
            default_result=dataclasses.replace(
                _DEFAULT_SKILL_RESULT,
                subtype="timeout",
                success=False,
                cli_subtype=CliSubtype.TIMEOUT,
            )
        )

        result = await _run(tool_ctx)
        # The timeout precheck should fire and produce fleet_l3_timeout
        assert result["success"] is False
        assert result["reason"] == "fleet_l3_timeout"

    @pytest.mark.anyio
    async def test_timed_out_session_never_reaches_parse_l3_result_block(
        self, tool_ctx, monkeypatch
    ):
        """TIMED_OUT (subtype=timeout) must not call parse_l3_result_block.

        The fix routes all outcomes through classify_dispatch_outcome, which
        handles timeout by setting parsed_result=None. The parse call is still
        bypassed for timeout — but now the path goes through the classifier.
        """
        import dataclasses

        from tests.fakes import _DEFAULT_SKILL_RESULT

        _setup_dispatch(tool_ctx, monkeypatch)
        tool_ctx.executor = InMemoryHeadlessExecutor(
            default_result=dataclasses.replace(
                _DEFAULT_SKILL_RESULT,
                subtype="timeout",
                success=False,
                cli_subtype=CliSubtype.TIMEOUT,
            )
        )

        parse_calls: list[dict] = []

        def _recording_parse(**kwargs):
            parse_calls.append(kwargs)

        monkeypatch.setattr(
            "autoskillit.fleet._api.parse_l3_result_block",
            _recording_parse,
        )

        result = await _run(tool_ctx)
        assert not parse_calls, (
            "parse_l3_result_block should not be called for timeout subtype "
            "(should go through classifier with parsed_result=None). "
            f"Got {len(parse_calls)} calls."
        )
        assert result["success"] is False
        assert result["reason"] == "fleet_l3_timeout"


class TestNoSentinelPath:
    @pytest.mark.anyio
    async def test_no_sentinel_writes_state_with_reason_l3_no_result_block(
        self, tool_ctx, monkeypatch
    ):
        """no_sentinel outcome → DispatchRecord.reason = 'l3_no_result_block'."""
        _setup_dispatch(tool_ctx, monkeypatch)
        monkeypatch.setattr(
            "autoskillit.fleet._api.parse_l3_result_block",
            lambda **_: _make_no_sentinel(),
        )

        await _run(tool_ctx)

        record = _read_dispatch_record(tool_ctx)
        assert record["reason"] == "fleet_l3_no_result_block"

    @pytest.mark.anyio
    async def test_no_sentinel_clean_exit_is_not_success(self, tool_ctx, monkeypatch):
        """no_sentinel outcome → envelope.success=False even when SkillResult.success=True."""
        import dataclasses

        from tests.fakes import _DEFAULT_SKILL_RESULT

        _setup_dispatch(tool_ctx, monkeypatch)
        tool_ctx.executor = InMemoryHeadlessExecutor(
            default_result=dataclasses.replace(
                _DEFAULT_SKILL_RESULT,
                success=True,
                exit_code=0,
            )
        )
        monkeypatch.setattr(
            "autoskillit.fleet._api.parse_l3_result_block",
            lambda **_: _make_no_sentinel(),
        )

        result = await _run(tool_ctx)
        assert result["success"] is False


class TestCompletedDirtyPath:
    @pytest.mark.anyio
    async def test_completed_dirty_writes_state_with_reason_l3_parse_failed(
        self, tool_ctx, monkeypatch
    ):
        """completed_dirty outcome → DispatchRecord.reason = 'l3_parse_failed'."""
        _setup_dispatch(tool_ctx, monkeypatch)
        monkeypatch.setattr(
            "autoskillit.fleet._api.parse_l3_result_block",
            lambda **_: _make_completed_dirty(),
        )

        await _run(tool_ctx)

        record = _read_dispatch_record(tool_ctx)
        assert record["reason"] == "fleet_l3_parse_failed"


class TestCompletedCleanPath:
    @pytest.mark.anyio
    async def test_completed_clean_success_writes_empty_reason(self, tool_ctx, monkeypatch):
        """completed_clean with success=True → DispatchRecord.reason = ''."""
        _setup_dispatch(tool_ctx, monkeypatch)
        monkeypatch.setattr(
            "autoskillit.fleet._api.parse_l3_result_block",
            lambda **_: _make_completed_clean(success=True),
        )

        await _run(tool_ctx)

        record = _read_dispatch_record(tool_ctx)
        assert record["reason"] == ""

    @pytest.mark.anyio
    async def test_completed_clean_failure_writes_reason_from_payload(self, tool_ctx, monkeypatch):
        """completed_clean success=False: payload.reason → DispatchRecord.reason."""
        _setup_dispatch(tool_ctx, monkeypatch)
        monkeypatch.setattr(
            "autoskillit.fleet._api.parse_l3_result_block",
            lambda **_: _make_completed_clean(success=False, reason="my-failure-reason"),
        )

        await _run(tool_ctx)

        record = _read_dispatch_record(tool_ctx)
        assert record["reason"] == "my-failure-reason"
