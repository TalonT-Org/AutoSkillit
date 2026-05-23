"""Crash path diagnostic persistence tests for fleet dispatch."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.fleet._helpers import (
    _no_sleep_quota_checker,
    _noop_quota_refresher,
    _setup_dispatch,
    _simple_prompt_builder,
)

pytestmark = [pytest.mark.layer("fleet"), pytest.mark.small, pytest.mark.feature("fleet")]


class TestCrashPathDiagnosticPersistence:
    """When _run_dispatch raises, the exception message must be persisted to campaign state."""

    async def test_crash_persists_exception_message_to_campaign_state(
        self, tool_ctx, monkeypatch, tmp_path: Path
    ) -> None:
        """Inject RuntimeError into _run_dispatch; verify campaign state carries the message."""
        from autoskillit.fleet import (
            DispatchRecord,
            read_state,
            write_initial_state,
        )
        from tests.fakes import InMemoryHeadlessExecutor

        campaign_state_path = tmp_path / "campaign.json"
        write_initial_state(
            campaign_state_path,
            "cmp-crash",
            "test-campaign",
            "/m.yaml",
            [DispatchRecord(name="dispatch-a")],
        )
        _setup_dispatch(tool_ctx, monkeypatch)
        executor = InMemoryHeadlessExecutor()

        # Inject a crash: any dispatch result will be replaced with a raising call
        async def crashing_run(*args, **kwargs):
            raise RuntimeError("kaboom: database connection lost")

        executor.dispatch_food_truck = crashing_run  # type: ignore[method-assign]
        tool_ctx.executor = executor

        from autoskillit.fleet._api import execute_dispatch
        from autoskillit.server.tools.tools_fleet_dispatch import _write_dispatch_to_campaign_state

        result = await execute_dispatch(
            tool_ctx=tool_ctx,
            recipe="test-recipe",
            task="do something",
            ingredients=None,
            dispatch_name="dispatch-a",
            timeout_sec=None,
            prompt_builder=_simple_prompt_builder,
            quota_checker=_no_sleep_quota_checker,
            quota_refresher=_noop_quota_refresher,
        )

        _write_dispatch_to_campaign_state(
            str(campaign_state_path), "dispatch-a", result.outcome, result.per_dispatch_state_path
        )

        envelope = json.loads(result.outcome.to_envelope())
        assert envelope["success"] is False
        assert envelope["error"] == "fleet_l3_startup_or_crash"
        assert "RuntimeError" in envelope["user_visible_message"]
        assert "kaboom: database connection lost" in envelope["user_visible_message"]

        state = read_state(campaign_state_path)
        assert state is not None
        refused = [d for d in state.dispatches if d.status.value == "refused"]
        assert len(refused) == 1
        assert "RuntimeError" in refused[0].diagnostic_message
        assert "kaboom: database connection lost" in refused[0].diagnostic_message
        assert refused[0].reason == "fleet_l3_startup_or_crash"

    async def test_crash_logs_structured_fields(self, tool_ctx, monkeypatch) -> None:
        """WARNING log must include exc_type and dispatch_name."""
        import structlog

        from tests.fakes import InMemoryHeadlessExecutor

        _setup_dispatch(tool_ctx, monkeypatch)
        executor = InMemoryHeadlessExecutor()

        async def crashing_run(*args, **kwargs):
            raise TypeError("unexpected None in argument")

        executor.dispatch_food_truck = crashing_run  # type: ignore[method-assign]
        tool_ctx.executor = executor

        from autoskillit.fleet._api import execute_dispatch

        with structlog.testing.capture_logs() as cap:
            result = await execute_dispatch(
                tool_ctx=tool_ctx,
                recipe="test-recipe",
                task="do something",
                ingredients=None,
                dispatch_name="my-test-dispatch",
                timeout_sec=None,
                prompt_builder=_simple_prompt_builder,
                quota_checker=_no_sleep_quota_checker,
                quota_refresher=_noop_quota_refresher,
            )

        envelope = json.loads(result.outcome.to_envelope())
        assert envelope["error"] == "fleet_l3_startup_or_crash"

        warning_logs = [
            r
            for r in cap
            if r.get("event", "") == "execute_dispatch crashed before dispatch completion"
        ]
        assert len(warning_logs) >= 1
        log_record = warning_logs[0]
        assert log_record.get("exc_type") == "TypeError"
        assert log_record.get("dispatch_name") == "my-test-dispatch"

    async def test_reject_with_state_persists_message_to_both_states(
        self, tool_ctx, monkeypatch, tmp_path: Path
    ) -> None:
        """_reject_with_state persists diagnostic message to both state files.

        The diagnostic message should be written to both the per-dispatch state file
        and the campaign state file when a dispatch is rejected.
        """
        from autoskillit.fleet import (
            DispatchRecord,
            read_state,
            write_initial_state,
        )

        campaign_state_path = tmp_path / "campaign.json"
        write_initial_state(
            campaign_state_path,
            "cmp-reject",
            "test-campaign",
            "/m.yaml",
            [DispatchRecord(name="dispatch-b")],
        )

        _setup_dispatch(tool_ctx, monkeypatch)
        tool_ctx.kitchen_id = "cmp-reject"

        from autoskillit.fleet._api import execute_dispatch
        from autoskillit.server.tools.tools_fleet_dispatch import _write_dispatch_to_campaign_state

        result = await execute_dispatch(
            tool_ctx=tool_ctx,
            recipe="test-recipe",
            task="do something",
            ingredients={"unknown_ingredient_key": "value"},
            dispatch_name="dispatch-b",
            timeout_sec=None,
            prompt_builder=_simple_prompt_builder,
            quota_checker=_no_sleep_quota_checker,
            quota_refresher=_noop_quota_refresher,
        )

        _write_dispatch_to_campaign_state(
            str(campaign_state_path), "dispatch-b", result.outcome, result.per_dispatch_state_path
        )

        envelope = json.loads(result.outcome.to_envelope())
        assert envelope["success"] is False
        assert "Unknown ingredient keys" in envelope["user_visible_message"]

        campaign_state = read_state(campaign_state_path)
        assert campaign_state is not None
        refused_campaign = [d for d in campaign_state.dispatches if d.status.value == "refused"]
        assert len(refused_campaign) == 1
        assert "Unknown ingredient keys" in refused_campaign[0].diagnostic_message

        assert result.per_dispatch_state_path is not None
        per_dispatch_state = read_state(result.per_dispatch_state_path)
        assert per_dispatch_state is not None
        refused_per_dispatch = [
            d for d in per_dispatch_state.dispatches if d.status.value == "refused"
        ]
        assert len(refused_per_dispatch) == 1
        assert "Unknown ingredient keys" in refused_per_dispatch[0].diagnostic_message
