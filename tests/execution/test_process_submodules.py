"""Tests verifying process.py decomposition into focused sub-modules.

P8-2: Each _process_*.py sub-module exports its expected symbols.
process.py remains a re-export facade for all public symbols.
"""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]


def test_process_kill_exports():
    """_process_kill.py exports kill/async_kill functions."""
    from autoskillit.execution.process._process_kill import (
        async_kill_process_tree,
        kill_process_tree,
    )

    assert callable(kill_process_tree)
    assert kill_process_tree.__module__ == "autoskillit.execution.process._process_kill"
    assert callable(async_kill_process_tree)
    assert async_kill_process_tree.__module__ == "autoskillit.execution.process._process_kill"


def test_process_pty_exports():
    """_process_pty.py exports pty_wrap_command."""
    from autoskillit.execution.process._process_pty import pty_wrap_command

    assert callable(pty_wrap_command)
    assert pty_wrap_command.__module__ == "autoskillit.execution.process._process_pty"


def test_process_jsonl_exports():
    """_process_jsonl.py exports JSONL parsing helpers."""
    from autoskillit.execution.process._process_jsonl import (
        _jsonl_contains_marker,
        _jsonl_has_record_type,
        _marker_is_standalone,
    )

    assert callable(_jsonl_contains_marker)
    assert _jsonl_contains_marker.__module__ == "autoskillit.execution.process._process_jsonl"
    assert callable(_jsonl_has_record_type)
    assert _jsonl_has_record_type.__module__ == "autoskillit.execution.process._process_jsonl"
    assert callable(_marker_is_standalone)
    assert _marker_is_standalone.__module__ == "autoskillit.execution.process._process_jsonl"


def test_process_io_exports():
    """_process_io.py exports temp I/O helpers."""
    from autoskillit.execution.process._process_io import create_temp_io, read_temp_output

    assert callable(create_temp_io)
    assert create_temp_io.__module__ == "autoskillit.execution.process._process_io"
    assert callable(read_temp_output)
    assert read_temp_output.__module__ == "autoskillit.execution.process._process_io"


def test_process_monitor_exports():
    """_process_monitor.py exports monitoring functions."""
    from autoskillit.execution.process._process_monitor import (
        _has_active_api_connection,
        _heartbeat,
        _session_log_monitor,
    )

    assert callable(_heartbeat)
    assert _heartbeat.__module__ == "autoskillit.execution.process._process_monitor"
    assert callable(_session_log_monitor)
    assert _session_log_monitor.__module__ == "autoskillit.execution.process._process_monitor"
    assert callable(_has_active_api_connection)
    assert (
        _has_active_api_connection.__module__ == "autoskillit.execution.process._process_monitor"
    )


def test_process_race_exports():
    """_process_race.py exports race coordination types and functions."""
    from autoskillit.execution.process._process_race import (
        RaceAccumulator,
        RaceSignals,
        _watch_heartbeat,
        resolve_termination,
    )

    assert RaceAccumulator.__module__ == "autoskillit.execution.process._process_race"
    assert RaceSignals.__module__ == "autoskillit.execution.process._process_race"
    assert callable(resolve_termination)
    assert resolve_termination.__module__ == "autoskillit.execution.process._process_race"
    assert callable(_watch_heartbeat)
    assert _watch_heartbeat.__module__ == "autoskillit.execution.process._process_race"


def test_process_facade_reexports_all_public_symbols():
    """process.py facade re-exports at least 21 public symbols."""
    from autoskillit.execution import process

    assert hasattr(process, "__all__")
    assert len(process.__all__) >= 21, (
        f"process.py __all__ has {len(process.__all__)} symbols, expected at least 21"
    )
