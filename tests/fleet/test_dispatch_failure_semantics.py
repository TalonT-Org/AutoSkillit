"""Group F: Core failure path semantics — timeout, no-sentinel, completed-dirty, clean."""

from __future__ import annotations

import pytest

from autoskillit.core.types import CliSubtype
from autoskillit.recipe.schema import RecipeIngredient
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


_SIDECAR_INGREDIENTS = {"issue_urls": RecipeIngredient(description="Issue URLs")}


class TestSidecarBasedResultSynthesis:
    @pytest.mark.anyio
    async def test_no_sentinel_with_completed_sidecar_entries_is_not_failure(
        self, tool_ctx, monkeypatch
    ):
        """no_sentinel + sidecar with all-completed entries + pr_url → SUCCESS."""
        from autoskillit.core import DispatchIdentity
        from autoskillit.fleet.sidecar import IssueSidecarEntry, append_sidecar_entry

        _setup_dispatch(tool_ctx, monkeypatch, ingredients=_SIDECAR_INGREDIENTS)

        fixed_dispatch_id = "aaaabbbb-1111-2222-3333-ffffffffffff"
        _fixed_identity = DispatchIdentity.from_dispatch_id(fixed_dispatch_id)

        class _FixedDispatchIdentity:
            @classmethod
            def fresh(cls) -> DispatchIdentity:
                return _fixed_identity

        monkeypatch.setattr("autoskillit.fleet.state.DispatchIdentity", _FixedDispatchIdentity)

        monkeypatch.setattr(
            "autoskillit.fleet._api.parse_l3_result_block",
            lambda **_: _make_no_sentinel(),
        )

        issue_url = "https://github.com/org/repo/issues/1"
        entry = IssueSidecarEntry(
            issue_url=issue_url,
            status="completed",
            ts="2026-05-27T00:00:00Z",
            pr_url="https://github.com/org/repo/pull/100",
        )
        append_sidecar_entry(fixed_dispatch_id, entry, tool_ctx.project_dir)

        result = await _run(tool_ctx, ingredients={"issue_urls": issue_url})
        assert result["dispatch_status"] == "success"

    @pytest.mark.anyio
    async def test_no_sentinel_with_failed_sidecar_entries_remains_failure(
        self, tool_ctx, monkeypatch
    ):
        """no_sentinel + sidecar with failed entries → still FAILURE, no synthesis."""
        from autoskillit.core import DispatchIdentity
        from autoskillit.fleet.sidecar import IssueSidecarEntry, append_sidecar_entry

        _setup_dispatch(tool_ctx, monkeypatch, ingredients=_SIDECAR_INGREDIENTS)

        fixed_dispatch_id = "aaaabbbb-4444-5555-6666-ffffffffffff"
        _fixed_identity = DispatchIdentity.from_dispatch_id(fixed_dispatch_id)

        class _FixedDispatchIdentity:
            @classmethod
            def fresh(cls) -> DispatchIdentity:
                return _fixed_identity

        monkeypatch.setattr("autoskillit.fleet.state.DispatchIdentity", _FixedDispatchIdentity)

        monkeypatch.setattr(
            "autoskillit.fleet._api.parse_l3_result_block",
            lambda **_: _make_no_sentinel(),
        )

        issue_url = "https://github.com/org/repo/issues/1"
        entry = IssueSidecarEntry(
            issue_url=issue_url,
            status="failed",
            ts="2026-05-27T00:00:00Z",
            reason="compilation error",
        )
        append_sidecar_entry(fixed_dispatch_id, entry, tool_ctx.project_dir)

        result = await _run(tool_ctx, ingredients={"issue_urls": issue_url})
        assert result["success"] is False

    @pytest.mark.anyio
    async def test_sidecar_synthesis_requires_all_issues_completed(self, tool_ctx, monkeypatch):
        """Synthesis requires len(completed sidecar entries) == len(dispatched issues)."""
        from autoskillit.core import DispatchIdentity
        from autoskillit.fleet.sidecar import IssueSidecarEntry, append_sidecar_entry

        _setup_dispatch(tool_ctx, monkeypatch, ingredients=_SIDECAR_INGREDIENTS)

        fixed_dispatch_id = "aaaabbbb-7777-8888-9999-ffffffffffff"
        _fixed_identity = DispatchIdentity.from_dispatch_id(fixed_dispatch_id)

        class _FixedDispatchIdentity:
            @classmethod
            def fresh(cls) -> DispatchIdentity:
                return _fixed_identity

        monkeypatch.setattr("autoskillit.fleet.state.DispatchIdentity", _FixedDispatchIdentity)

        monkeypatch.setattr(
            "autoskillit.fleet._api.parse_l3_result_block",
            lambda **_: _make_no_sentinel(),
        )

        issue1 = "https://github.com/org/repo/issues/1"
        issue2 = "https://github.com/org/repo/issues/2"
        entry = IssueSidecarEntry(
            issue_url=issue1,
            status="completed",
            ts="2026-05-27T00:00:00Z",
            pr_url="https://github.com/org/repo/pull/100",
        )
        append_sidecar_entry(fixed_dispatch_id, entry, tool_ctx.project_dir)

        result = await _run(tool_ctx, ingredients={"issue_urls": f"{issue1},{issue2}"})
        assert result["dispatch_status"] != "success"

    @pytest.mark.anyio
    async def test_sidecar_synthesis_skips_capture_extraction(self, tool_ctx, monkeypatch):
        """Sidecar-synthesized SUCCESS must not call _extract_captures."""
        import json

        from autoskillit.core import DispatchIdentity
        from autoskillit.fleet._api import execute_dispatch
        from autoskillit.fleet.sidecar import IssueSidecarEntry, append_sidecar_entry
        from tests.fleet._helpers import (
            _no_sleep_quota_checker,
            _noop_quota_refresher,
            _simple_prompt_builder,
        )

        _setup_dispatch(tool_ctx, monkeypatch, ingredients=_SIDECAR_INGREDIENTS)

        fixed_dispatch_id = "aaaabbbb-cccc-dddd-eeee-ffffffffffff"
        _fixed_identity = DispatchIdentity.from_dispatch_id(fixed_dispatch_id)

        class _FixedDispatchIdentity:
            @classmethod
            def fresh(cls) -> DispatchIdentity:
                return _fixed_identity

        monkeypatch.setattr("autoskillit.fleet.state.DispatchIdentity", _FixedDispatchIdentity)

        monkeypatch.setattr(
            "autoskillit.fleet._api.parse_l3_result_block",
            lambda **_: _make_no_sentinel(),
        )

        def _should_not_be_called(spec, payload):
            raise AssertionError("_extract_captures called on sidecar-synthesized result")

        monkeypatch.setattr("autoskillit.fleet._api._extract_captures", _should_not_be_called)

        issue_url = "https://github.com/org/repo/issues/1"
        entry = IssueSidecarEntry(
            issue_url=issue_url,
            status="completed",
            ts="2026-05-27T00:00:00Z",
            pr_url="https://github.com/org/repo/pull/100",
        )
        append_sidecar_entry(fixed_dispatch_id, entry, tool_ctx.project_dir)

        result = await execute_dispatch(
            tool_ctx=tool_ctx,
            recipe="test-recipe",
            task="t",
            ingredients={"issue_urls": issue_url},
            dispatch_name=None,
            timeout_sec=None,
            prompt_builder=_simple_prompt_builder,
            quota_checker=_no_sleep_quota_checker,
            quota_refresher=_noop_quota_refresher,
            capture={"pr_url": "${{ result.pr_url }}"},
        )
        envelope = json.loads(result.outcome.to_envelope())
        assert envelope["dispatch_status"] == "success"


class TestTrackerBridgeIntegration:
    @pytest.mark.anyio
    async def test_single_issue_killed_with_tracker_progress_is_resumable(
        self, tool_ctx, monkeypatch
    ):
        """process_killed + no sidecar + populated tracker file → RESUMABLE via checkpoint bridge."""
        import dataclasses
        import json

        from autoskillit.core import DispatchIdentity, InfraOutcome
        from tests.fakes import _DEFAULT_SKILL_RESULT

        _setup_dispatch(tool_ctx, monkeypatch)

        fixed_dispatch_id = "aaaabbbb-aaaa-bbbb-cccc-111111111111"
        _fixed_identity = DispatchIdentity.from_dispatch_id(fixed_dispatch_id)

        class _FixedDispatchIdentity:
            @classmethod
            def fresh(cls) -> DispatchIdentity:
                return _fixed_identity

        monkeypatch.setattr("autoskillit.fleet.state.DispatchIdentity", _FixedDispatchIdentity)

        tool_ctx.executor = InMemoryHeadlessExecutor(
            default_result=dataclasses.replace(
                _DEFAULT_SKILL_RESULT,
                success=False,
                session_id="sess-tracker-001",
                lifespan_started=True,
                retry_reason="resume",
                infra=InfraOutcome(exit_category="process_killed"),
            )
        )

        monkeypatch.setattr(
            "autoskillit.fleet._api.parse_l3_result_block",
            lambda **_: _make_no_sentinel(),
        )

        tracker_dir = tool_ctx.project_dir / ".autoskillit" / "temp" / "pipeline_tracker"
        tracker_dir.mkdir(parents=True, exist_ok=True)
        tracker_file = tracker_dir / f"{fixed_dispatch_id}.json"
        tracker_file.write_text(
            json.dumps(
                {
                    "pipeline_id": fixed_dispatch_id,
                    "kitchen_id": "k-test",
                    "initialized_at": "2026-06-01T00:00:00Z",
                    "steps": {
                        "plan": {
                            "status": "complete",
                            "completed_at": "2026-06-01T00:01:00Z",
                        },
                        "implement": {
                            "status": "complete",
                            "completed_at": "2026-06-01T00:02:00Z",
                        },
                        "review-pr": {"status": "pending"},
                    },
                    "dependencies": {},
                }
            )
        )

        await _run(tool_ctx)

        record = _read_dispatch_record(tool_ctx)
        assert record["status"] == "resumable"
        assert record["reason"] == "fleet_l3_no_result_block"
        assert record.get("resume_checkpoint") is not None
        assert "plan" in record["resume_checkpoint"].get("completed_items", [])
