"""Tests verifying process.py decomposition into focused sub-modules.

P8-2: Each _process_*.py sub-module exports its expected symbols.
process.py remains a re-export facade for all public symbols.
"""

from __future__ import annotations

import inspect

import pytest

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]

_EXPECTED_PROCESS_SYMBOLS: frozenset[str] = frozenset(
    {
        "DefaultSubprocessRunner",
        "_extract_stdout_session_id",
        "_resolve_session_id",
        "RaceAccumulator",
        "RaceSignals",
        "_has_active_api_connection",
        "_has_active_child_processes",
        "_heartbeat",
        "_jsonl_contains_marker",
        "_jsonl_has_record_type",
        "_jsonl_last_record_type",
        "_marker_is_standalone",
        "_session_log_monitor",
        "_wait_process_dead",
        "_watch_heartbeat",
        "_watch_process",
        "_watch_session_log",
        "async_kill_process_tree",
        "create_temp_io",
        "decide_termination_action",
        "execute_termination_action",
        "kill_process_tree",
        "pty_wrap_command",
        "read_temp_output",
        "resolve_termination",
        "run_managed_async",
        "run_managed_sync",
    }
)


def test_process_kill_exports():
    """kill_process_tree and async_kill_process_tree are defined in _process_kill submodule."""
    from autoskillit.execution.process._process_kill import (
        async_kill_process_tree,
        kill_process_tree,
    )

    assert callable(kill_process_tree)
    assert kill_process_tree.__module__ == "autoskillit.execution.process._process_kill"
    assert callable(async_kill_process_tree)
    assert async_kill_process_tree.__module__ == "autoskillit.execution.process._process_kill"


def test_process_pty_exports():
    """pty_wrap_command is defined in _process_pty submodule."""
    from autoskillit.execution.process._process_pty import pty_wrap_command

    assert callable(pty_wrap_command)
    assert pty_wrap_command.__module__ == "autoskillit.execution.process._process_pty"


def test_process_jsonl_exports():
    """_jsonl_contains_marker, _jsonl_has_record_type, and _marker_is_standalone
    are defined in _process_jsonl submodule."""
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
    """create_temp_io and read_temp_output are defined in _process_io submodule."""
    from autoskillit.execution.process._process_io import create_temp_io, read_temp_output

    assert callable(create_temp_io)
    assert create_temp_io.__module__ == "autoskillit.execution.process._process_io"
    assert callable(read_temp_output)
    assert read_temp_output.__module__ == "autoskillit.execution.process._process_io"


def test_process_monitor_exports():
    """_heartbeat, _session_log_monitor, and _has_active_api_connection
    are defined in _process_monitor submodule."""
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
    """Race types/functions are defined in _process_race submodule."""
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
    """process.py facade re-exports exactly the expected 27 public symbols."""
    from autoskillit.execution import process

    assert hasattr(process, "__all__")
    assert set(process.__all__) == _EXPECTED_PROCESS_SYMBOLS, (
        f"process.__all__ mismatch.\n"
        f"  Extra   : {set(process.__all__) - _EXPECTED_PROCESS_SYMBOLS}\n"
        f"  Missing : {_EXPECTED_PROCESS_SYMBOLS - set(process.__all__)}"
    )


def test_default_subprocess_runner_pty_mode_default_false():

    from autoskillit.execution.process import DefaultSubprocessRunner

    sig = inspect.signature(DefaultSubprocessRunner.__call__)
    assert sig.parameters["pty_mode"].default is False


def test_run_managed_async_pty_mode_default_false():

    from autoskillit.execution.process import run_managed_async

    sig = inspect.signature(run_managed_async)
    assert sig.parameters["pty_mode"].default is False
