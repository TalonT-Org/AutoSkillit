"""Tests for Health Inspector IL-0 types and re-exports.

These tests cover:
- InspectorEvidence dataclass construction, frozenness, defaults
- InspectorVerdict dataclass construction, frozenness
- make_stub_inspector helper from tests.conftest
- TerminationReason.HEALTH_INSPECTOR and KillReason.HEALTH_INSPECTOR enum values
- SubprocessResult.inspector_verdict field
- RaceSignals.inspector_verdict field
- BackendCapabilities.inspector_capable field
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from autoskillit.core import (
    BackendCapabilities,
    InspectorCallback,
    InspectorEvidence,
    InspectorVerdict,
    KillReason,
    TerminationReason,
)
from autoskillit.core.types import RaceSignals, SubprocessResult
from tests.conftest import make_stub_inspector

pytestmark = [pytest.mark.layer("core"), pytest.mark.small]


class TestInspectorEvidence:
    def test_construction_all_fields(self):
        ev = InspectorEvidence(
            idle_seconds=120.0,
            stdout_path="/tmp/stdout",
            jsonl_lines=("line1", "line2"),
            cpu_trend=(0.1, 0.2),
            rss_trend=(100.0, 200.0),
            connection_summary="ESTABLISHED=3, CLOSE_WAIT=47",
            execution_marker_present=True,
            dispatch_context="recipe=X, step=Y, elapsed=60s",
        )
        assert ev.idle_seconds == 120.0
        assert ev.jsonl_lines == ("line1", "line2")
        assert ev.cpu_trend == (0.1, 0.2)
        assert ev.rss_trend == (100.0, 200.0)
        assert ev.connection_summary == "ESTABLISHED=3, CLOSE_WAIT=47"
        assert ev.execution_marker_present is True
        assert ev.dispatch_context == "recipe=X, step=Y, elapsed=60s"

    def test_frozen(self):
        ev = InspectorEvidence(idle_seconds=1.0, stdout_path="", jsonl_lines=())
        with pytest.raises(FrozenInstanceError):
            ev.idle_seconds = 2.0  # type: ignore[misc]

    def test_defaults(self):
        ev = InspectorEvidence(idle_seconds=1.0, stdout_path="", jsonl_lines=())
        assert ev.cpu_trend == ()
        assert ev.rss_trend == ()
        assert ev.connection_summary == ""
        assert ev.execution_marker_present is False
        assert ev.dispatch_context == ""


class TestInspectorVerdict:
    def test_construction(self):
        v = InspectorVerdict(
            action="KILL",
            reasoning="stuck",
            confidence="high",
            elapsed_seconds=1.5,
        )
        assert v.action == "KILL"
        assert v.reasoning == "stuck"
        assert v.confidence == "high"
        assert v.elapsed_seconds == 1.5

    def test_frozen(self):
        v = InspectorVerdict(
            action="SPARE",
            reasoning="ok",
            confidence="low",
            elapsed_seconds=0.0,
        )
        with pytest.raises(FrozenInstanceError):
            v.action = "KILL"  # type: ignore[misc]


class TestMakeStubInspector:
    @pytest.mark.anyio
    async def test_spare_default(self):
        cb: InspectorCallback = make_stub_inspector()
        ev = InspectorEvidence(idle_seconds=1.0, stdout_path="", jsonl_lines=())
        result = await cb(ev)
        assert result.action == "SPARE"
        assert result.confidence == "high"
        assert result.reasoning == "stub"
        assert result.elapsed_seconds == 0.0

    @pytest.mark.anyio
    async def test_kill(self):
        cb: InspectorCallback = make_stub_inspector("KILL")
        ev = InspectorEvidence(idle_seconds=1.0, stdout_path="", jsonl_lines=())
        result = await cb(ev)
        assert result.action == "KILL"


class TestEnumValues:
    def test_termination_reason_health_inspector(self):
        assert TerminationReason.HEALTH_INSPECTOR == "health_inspector"

    def test_kill_reason_health_inspector(self):
        assert KillReason.HEALTH_INSPECTOR == "health_inspector"


class TestSubprocessResultInspectorVerdict:
    def test_default_none(self):
        r = SubprocessResult(
            returncode=0,
            stdout="",
            stderr="",
            termination=TerminationReason.NATURAL_EXIT,
            pid=1,
        )
        assert r.inspector_verdict is None

    def test_set_verdict(self):
        v = InspectorVerdict(
            action="KILL",
            reasoning="stuck",
            confidence="high",
            elapsed_seconds=1.0,
        )
        r = SubprocessResult(
            returncode=0,
            stdout="",
            stderr="",
            termination=TerminationReason.NATURAL_EXIT,
            pid=1,
            inspector_verdict=v,
        )
        assert r.inspector_verdict is v


class TestRaceSignalsInspectorVerdict:
    def test_default_none(self):
        rs = RaceSignals(
            process_exited=True,
            process_returncode=0,
            channel_a_confirmed=False,
            channel_b_status=None,
        )
        assert rs.inspector_verdict is None


class TestBackendCapabilitiesInspectorCapable:
    def test_default_false(self):
        caps = BackendCapabilities(
            channel_b_capable=False,
            pty_required=False,
            session_resume_capable=False,
            skill_injection_capable=False,
            supports_thinking_blocks=False,
            supports_claude_format_stdout=False,
            exit_code_is_terminal=False,
            mcp_config_capable=False,
            food_truck_capable=False,
            completion_record_types=frozenset(),
            session_record_types=frozenset(),
        )
        assert caps.inspector_capable is False

    def test_set_true(self):
        caps = BackendCapabilities(
            channel_b_capable=False,
            pty_required=False,
            session_resume_capable=False,
            skill_injection_capable=False,
            supports_thinking_blocks=False,
            supports_claude_format_stdout=False,
            exit_code_is_terminal=False,
            mcp_config_capable=False,
            food_truck_capable=False,
            completion_record_types=frozenset(),
            session_record_types=frozenset(),
            inspector_capable=True,
        )
        assert caps.inspector_capable is True
