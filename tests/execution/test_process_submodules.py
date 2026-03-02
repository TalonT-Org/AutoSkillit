"""Structural tests: verify process.py decomposition into focused sub-modules.

Tests imports from each new private sub-module and the re-export facade in process.py.
All imports from autoskillit.execution.process.* must continue to work (P8-2).
"""
import inspect
import pytest


# --- P8-2: Sub-module existence and export surface ---

def test_process_kill_exports_kill_functions():
    """_process_kill.py must exist and export kill utilities."""
    from autoskillit.execution._process_kill import (
        async_kill_process_tree,
        kill_process_tree,
    )
    assert callable(kill_process_tree)
    assert callable(async_kill_process_tree)


def test_process_pty_exports_pty_wrap():
    """_process_pty.py must exist and export pty_wrap_command."""
    from autoskillit.execution._process_pty import pty_wrap_command
    assert callable(pty_wrap_command)


def test_process_jsonl_exports_jsonl_helpers():
    """_process_jsonl.py must exist and export all three JSONL helpers."""
    from autoskillit.execution._process_jsonl import (
        _jsonl_contains_marker,
        _jsonl_has_record_type,
        _marker_is_standalone,
    )
    assert callable(_jsonl_contains_marker)
    assert callable(_jsonl_has_record_type)
    assert callable(_marker_is_standalone)


def test_process_io_exports_io_functions():
    """_process_io.py must exist and export temp I/O context manager and reader."""
    from autoskillit.execution._process_io import create_temp_io, read_temp_output
    assert callable(create_temp_io)
    assert callable(read_temp_output)


def test_process_monitor_exports_monitor_functions():
    """_process_monitor.py must exist and export heartbeat + session log monitor."""
    from autoskillit.execution._process_monitor import (
        _has_active_api_connection,
        _heartbeat,
        _session_log_monitor,
    )
    assert callable(_heartbeat)
    assert callable(_session_log_monitor)
    assert callable(_has_active_api_connection)


def test_process_race_exports_race_types():
    """_process_race.py must exist and export race detection machinery."""
    from autoskillit.execution._process_race import (
        RaceAccumulator,
        RaceSignals,
        resolve_termination,
    )
    assert callable(resolve_termination)


def test_process_facade_re_exports_all_public_symbols():
    """process.py must re-export all symbols so existing callers are unaffected."""
    from autoskillit.execution.process import (
        DefaultSubprocessRunner,
        RaceAccumulator,
        RaceSignals,
        _has_active_api_connection,
        _heartbeat,
        _jsonl_contains_marker,
        _jsonl_has_record_type,
        _marker_is_standalone,
        _session_log_monitor,
        async_kill_process_tree,
        create_temp_io,
        kill_process_tree,
        pty_wrap_command,
        read_temp_output,
        resolve_termination,
        run_managed_async,
        run_managed_sync,
    )


# --- P10-1: _heartbeat has no 'marker' parameter ---

def test_heartbeat_signature_has_no_marker_param():
    """_heartbeat must not accept a 'marker' parameter (P10-1)."""
    from autoskillit.execution._process_monitor import _heartbeat
    params = inspect.signature(_heartbeat).parameters
    assert "marker" not in params, (
        f"_heartbeat still has 'marker' parameter — P10-1 not applied. Params: {list(params)}"
    )


# --- P10-2: _watch_heartbeat has no 'heartbeat_marker' parameter ---

def test_watch_heartbeat_signature_has_no_heartbeat_marker_param():
    """_watch_heartbeat must not accept 'heartbeat_marker' (P10-2)."""
    from autoskillit.execution._process_race import _watch_heartbeat
    params = inspect.signature(_watch_heartbeat).parameters
    assert "heartbeat_marker" not in params, (
        f"_watch_heartbeat still has 'heartbeat_marker' — P10-2 not applied. Params: {list(params)}"
    )


# --- P10-3: run_managed_async accepts str, not str | None ---

def test_run_managed_async_heartbeat_marker_default_is_empty_string():
    """run_managed_async heartbeat_marker default must be '' not None (P10-3)."""
    from autoskillit.execution.process import run_managed_async
    params = inspect.signature(run_managed_async).parameters
    param = params["heartbeat_marker"]
    assert param.default == "", (
        f"run_managed_async heartbeat_marker default is {param.default!r}, expected ''"
    )


def test_default_subprocess_runner_no_none_coercion():
    """DefaultSubprocessRunner must not coerce heartbeat_marker str to None (P10-3)."""
    from autoskillit.execution.process import DefaultSubprocessRunner
    params = inspect.signature(DefaultSubprocessRunner.__call__).parameters
    param = params["heartbeat_marker"]
    assert param.default == "", "DefaultSubprocessRunner.heartbeat_marker default must be ''"
    # Verify type annotation is str, not str | None
    ann = param.annotation
    assert ann is not None
    # The annotation should be 'str' (not Optional[str] or str | None)
    ann_str = str(ann)
    assert "None" not in ann_str, (
        f"DefaultSubprocessRunner.heartbeat_marker should be str, not {ann_str}"
    )
