"""Dispatch marker suppression gate tests for _session_log_monitor."""

from __future__ import annotations

import os
import time

import anyio
import pytest

from autoskillit.core.types import ChannelBStatus
from autoskillit.execution.process import _session_log_monitor

pytestmark = [pytest.mark.layer("execution"), pytest.mark.medium]


class TestStaleSuppressionDispatchMarker:
    """Dispatch marker suppresses stale: fresh marker → suppressed; expired → pass-through.

    Tests the following scenarios for dispatch marker stale suppression:
    - T1: Active marker (True→False via monkeypatch) suppresses stale, then fires
    - T2: Expired marker (mtime > 60s) does NOT suppress stale (real helper, no monkeypatch —
      exercises the mtime-expiry threshold in _has_active_dispatch_marker directly)
    - T3: No marker_dir (None) skips dispatch check entirely (regression guard)
    - T4: Bounded suppression fires after max_suppression_seconds
    - T5: Session-scoped matching — sessionA marker does not suppress sessionB

    Monkeypatch target: autoskillit.execution.process._process_monitor._has_active_dispatch_marker
    Convention: caller_session_id= is the kwarg used at call sites.
    Note: T1, T4 monkeypatch the helper; T2, T5 use real marker files to test helper internals.
    """

    @pytest.mark.anyio
    async def test_stale_suppressed_by_active_dispatch_marker(self, tmp_path, monkeypatch):
        """Active dispatch marker suppresses stale, then fires when marker disappears."""
        session_file = tmp_path / "session.jsonl"
        session_file.write_text("")
        spawn_time = time.time() - 10
        call_count = {"n": 0}

        def side_effect_fn(marker_dir, session_id=None):
            call_count["n"] += 1
            return call_count["n"] == 1

        monkeypatch.setattr(
            "autoskillit.execution.process._process_monitor._has_active_dispatch_marker",
            side_effect_fn,
        )
        with anyio.fail_after(5.0):
            result = await _session_log_monitor(
                tmp_path,
                "DONE",
                stale_threshold=0.05,
                spawn_time=spawn_time,
                marker_dir=tmp_path,
                caller_session_id="test-session",
                _phase1_poll=0.01,
                _phase2_poll=0.05,
            )
        assert result.status == ChannelBStatus.STALE

    @pytest.mark.anyio
    async def test_stale_not_suppressed_when_marker_expired(self, tmp_path):
        """Expired marker (mtime > 60s) does not suppress stale."""
        session_file = tmp_path / "session.jsonl"
        session_file.write_text("")
        spawn_time = time.time() - 10
        marker_path = tmp_path / "dispatch-in-progress-test-session-abc.marker"
        marker_path.write_text("{}")
        past = time.time() - 120
        os.utime(marker_path, (past, past))
        with anyio.fail_after(5.0):
            result = await _session_log_monitor(
                tmp_path,
                "DONE",
                stale_threshold=0.05,
                spawn_time=spawn_time,
                marker_dir=tmp_path,
                caller_session_id="test-session",
                _phase1_poll=0.01,
                _phase2_poll=0.05,
            )
        assert result.status == ChannelBStatus.STALE

    @pytest.mark.anyio
    async def test_stale_not_suppressed_when_no_marker(self, tmp_path):
        """No marker_dir means dispatch check is skipped entirely."""
        session_file = tmp_path / "session.jsonl"
        session_file.write_text("")
        spawn_time = time.time() - 10
        with anyio.fail_after(2.0):
            result = await _session_log_monitor(
                tmp_path,
                "DONE",
                stale_threshold=0.05,
                spawn_time=spawn_time,
                marker_dir=None,
                _phase1_poll=0.01,
                _phase2_poll=0.05,
            )
        assert result.status == ChannelBStatus.STALE

    @pytest.mark.anyio
    async def test_stale_marker_suppression_bounded(self, tmp_path, monkeypatch):
        """Stale fires after max_suppression_seconds despite perpetually-fresh marker."""
        session_file = tmp_path / "session.jsonl"
        session_file.write_text("")
        spawn_time = time.time() - 10
        monkeypatch.setattr(
            "autoskillit.execution.process._process_monitor._has_active_dispatch_marker",
            lambda marker_dir, session_id=None: True,
        )
        with anyio.fail_after(3.0):
            result = await _session_log_monitor(
                tmp_path,
                "DONE",
                stale_threshold=0.05,
                spawn_time=spawn_time,
                pid=None,
                _phase1_poll=0.01,
                _phase2_poll=0.05,
                max_suppression_seconds=0.1,
                marker_dir=tmp_path,
                caller_session_id=None,
            )
        assert result.status == ChannelBStatus.STALE

    @pytest.mark.anyio
    async def test_session_scoped_marker_matching(self, tmp_path):
        """Marker for sessionA does not suppress stale for sessionB."""
        session_file = tmp_path / "session.jsonl"
        session_file.write_text("")
        spawn_time = time.time() - 10
        marker_path = tmp_path / "dispatch-in-progress-sessionA-dispatch1.marker"
        marker_path.write_text("{}")
        with anyio.fail_after(3.0):
            result = await _session_log_monitor(
                tmp_path,
                "DONE",
                stale_threshold=0.05,
                spawn_time=spawn_time,
                pid=None,
                _phase1_poll=0.01,
                _phase2_poll=0.05,
                marker_dir=tmp_path,
                caller_session_id="sessionB",
            )
        assert result.status == ChannelBStatus.STALE


class TestDispatchMarkerSuppression:
    """Dispatch marker suppression branch via marker_dir/caller_session_id parameters."""

    @pytest.mark.anyio
    async def test_accepts_marker_dir_and_caller_session_id_params(self, tmp_path):
        """marker_dir and caller_session_id params are forwarded; monitor exits STALE."""
        session_file = tmp_path / "abc123.jsonl"
        session_file.write_text("")
        spawn_time = time.time() - 10
        with anyio.fail_after(5.0):
            result = await _session_log_monitor(
                tmp_path,
                "DONE",
                stale_threshold=0.05,
                spawn_time=spawn_time,
                _phase1_poll=0.01,
                _phase2_poll=0.05,
                marker_dir=tmp_path,
                caller_session_id="test-session",
            )
        assert result.status == ChannelBStatus.STALE

    @pytest.mark.anyio
    async def test_dispatch_marker_suppresses_stale(self, tmp_path, monkeypatch):
        """Active dispatch marker suppresses stale kill, then fires when marker disappears."""
        session_file = tmp_path / "abc123.jsonl"
        session_file.write_text("")
        spawn_time = time.time() - 10
        call_count = {"n": 0}

        def fake_api_conn(pid):
            return False

        def fake_child_proc(pid):
            return False

        def fake_dispatch_marker(marker_dir, session_id=None):
            call_count["n"] += 1
            return call_count["n"] == 1

        monkeypatch.setattr(
            "autoskillit.execution.process._process_monitor._has_active_api_connection",
            fake_api_conn,
        )
        monkeypatch.setattr(
            "autoskillit.execution.process._process_monitor._has_active_child_processes",
            fake_child_proc,
        )
        monkeypatch.setattr(
            "autoskillit.execution.process._process_monitor._has_active_dispatch_marker",
            fake_dispatch_marker,
        )
        with anyio.fail_after(5.0):
            result = await _session_log_monitor(
                tmp_path,
                "DONE",
                stale_threshold=0.05,
                spawn_time=spawn_time,
                pid=9999,
                _phase1_poll=0.01,
                _phase2_poll=0.05,
                marker_dir=tmp_path,
                caller_session_id="sess-abc",
            )
        assert result.status == ChannelBStatus.STALE
        assert call_count["n"] >= 2

    @pytest.mark.anyio
    async def test_dispatch_marker_bounded_by_max_suppression(self, tmp_path, monkeypatch):
        """Stale fires after max_suppression_seconds despite active dispatch marker."""
        import structlog.testing

        session_file = tmp_path / "abc123.jsonl"
        session_file.write_text("")
        spawn_time = time.time() - 10

        monkeypatch.setattr(
            "autoskillit.execution.process._process_monitor._has_active_api_connection",
            lambda pid: False,
        )
        monkeypatch.setattr(
            "autoskillit.execution.process._process_monitor._has_active_child_processes",
            lambda pid: False,
        )
        monkeypatch.setattr(
            "autoskillit.execution.process._process_monitor._has_active_dispatch_marker",
            lambda marker_dir, session_id=None: True,
        )
        with structlog.testing.capture_logs() as logs:
            with anyio.fail_after(8.0):
                result = await _session_log_monitor(
                    tmp_path,
                    "DONE",
                    stale_threshold=0.05,
                    spawn_time=spawn_time,
                    pid=9999,
                    _phase1_poll=0.01,
                    _phase2_poll=0.05,
                    marker_dir=tmp_path,
                    caller_session_id="sess-abc",
                    max_suppression_seconds=1.0,
                )
        assert result.status == ChannelBStatus.STALE
        bounded_logs = [log for log in logs if "Suppression bounded" in str(log.get("event", ""))]
        assert len(bounded_logs) == 1

    @pytest.mark.anyio
    async def test_dispatch_marker_suppression_emits_warning_with_fields(
        self, tmp_path, monkeypatch
    ):
        """Suppression warning includes elapsed, caller_session_id, and marker_dir."""
        import structlog.testing

        session_file = tmp_path / "abc123.jsonl"
        session_file.write_text("")
        spawn_time = time.time() - 10
        call_count = {"n": 0}

        def fake_api_conn(pid):
            return False

        def fake_child_proc(pid):
            return False

        def fake_dispatch_marker(marker_dir, session_id=None):
            call_count["n"] += 1
            return call_count["n"] == 1

        monkeypatch.setattr(
            "autoskillit.execution.process._process_monitor._has_active_api_connection",
            fake_api_conn,
        )
        monkeypatch.setattr(
            "autoskillit.execution.process._process_monitor._has_active_child_processes",
            fake_child_proc,
        )
        monkeypatch.setattr(
            "autoskillit.execution.process._process_monitor._has_active_dispatch_marker",
            fake_dispatch_marker,
        )
        with structlog.testing.capture_logs() as logs:
            with anyio.fail_after(5.0):
                await _session_log_monitor(
                    tmp_path,
                    "DONE",
                    stale_threshold=0.05,
                    spawn_time=spawn_time,
                    pid=9999,
                    _phase1_poll=0.01,
                    _phase2_poll=0.05,
                    marker_dir=tmp_path,
                    caller_session_id="sess-abc",
                )
        warning_logs = [log for log in logs if "dispatch" in str(log.get("event", ""))]
        assert len(warning_logs) == 1
        log = warning_logs[0]
        assert log.get("caller_session_id") == "sess-abc"
        assert log.get("marker_dir") == str(tmp_path)

    @pytest.mark.anyio
    async def test_dispatch_marker_bounded_kill_emits_warning_with_fields(
        self, tmp_path, monkeypatch
    ):
        """Bounded kill warning includes elapsed, caller_session_id, marker_dir."""
        import structlog.testing

        session_file = tmp_path / "abc123.jsonl"
        session_file.write_text("")
        spawn_time = time.time() - 10

        monkeypatch.setattr(
            "autoskillit.execution.process._process_monitor._has_active_api_connection",
            lambda pid: False,
        )
        monkeypatch.setattr(
            "autoskillit.execution.process._process_monitor._has_active_child_processes",
            lambda pid: False,
        )
        monkeypatch.setattr(
            "autoskillit.execution.process._process_monitor._has_active_dispatch_marker",
            lambda marker_dir, session_id=None: True,
        )
        with structlog.testing.capture_logs() as logs:
            with anyio.fail_after(8.0):
                await _session_log_monitor(
                    tmp_path,
                    "DONE",
                    stale_threshold=0.05,
                    spawn_time=spawn_time,
                    pid=9999,
                    _phase1_poll=0.01,
                    _phase2_poll=0.05,
                    marker_dir=tmp_path,
                    caller_session_id="sess-abc",
                    max_suppression_seconds=1.0,
                )
        bounded_logs = [log for log in logs if "Suppression bounded" in str(log.get("event", ""))]
        assert len(bounded_logs) == 1
        log = bounded_logs[0]
        assert log.get("caller_session_id") == "sess-abc"
        assert log.get("marker_dir") == str(tmp_path)

    @pytest.mark.anyio
    async def test_marker_dir_none_skips_dispatch_check(self, tmp_path, monkeypatch):
        """When marker_dir is None (default), _has_active_dispatch_marker is never called."""
        session_file = tmp_path / "abc123.jsonl"
        session_file.write_text("")
        spawn_time = time.time() - 10
        called = {"n": False}

        def fake_api_conn(pid):
            return False

        def fake_child_proc(pid):
            return False

        def track_dispatch_marker(marker_dir, session_id=None):
            called["n"] = True
            return False

        monkeypatch.setattr(
            "autoskillit.execution.process._process_monitor._has_active_api_connection",
            fake_api_conn,
        )
        monkeypatch.setattr(
            "autoskillit.execution.process._process_monitor._has_active_child_processes",
            fake_child_proc,
        )
        monkeypatch.setattr(
            "autoskillit.execution.process._process_monitor._has_active_dispatch_marker",
            track_dispatch_marker,
        )
        with anyio.fail_after(5.0):
            result = await _session_log_monitor(
                tmp_path,
                "DONE",
                stale_threshold=0.05,
                spawn_time=spawn_time,
                pid=9999,
                _phase1_poll=0.01,
                _phase2_poll=0.05,
            )
        assert result.status == ChannelBStatus.STALE
        assert not called["n"]

    @pytest.mark.anyio
    async def test_existing_callers_unaffected_by_new_params(self, tmp_path):
        """Calling _session_log_monitor without marker_dir/caller_session_id works identically."""
        session_file = tmp_path / "abc123.jsonl"
        session_file.write_text("")
        spawn_time = time.time() - 10
        with anyio.fail_after(5.0):
            result = await _session_log_monitor(
                tmp_path,
                "DONE",
                stale_threshold=0.05,
                spawn_time=spawn_time,
                _phase1_poll=0.01,
                _phase2_poll=0.05,
            )
        assert result.status == ChannelBStatus.STALE
        assert result.session_id == "abc123"
