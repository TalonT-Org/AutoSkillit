"""Tests for resolve_trace_target — descendant-walk and basename-match contract.

These tests verify the new TraceTarget resolver introduced to fix issue #806:
anyio.open_process returns the PID of script(1), not claude. The resolver walks
process descendants to find the actual workload.
"""

from __future__ import annotations

import shutil
import subprocess
import sys

import pytest

pytestmark = [
    pytest.mark.layer("execution"),
    pytest.mark.skipif(
        sys.platform != "linux",
        reason="Linux only — tests /proc descendant walking",
    ),
]


@pytest.mark.skipif(shutil.which("script") is None, reason="script(1) not available")
@pytest.mark.anyio
async def test_resolve_trace_target_walks_from_wrapper_to_workload():
    """resolve_trace_target resolves from script(1) wrapper to the workload process.

    Test 1.3: lock in the descendant-walk + basename-match contract.
    """
    from autoskillit.execution.linux_tracing import resolve_trace_target

    proc = subprocess.Popen(
        ["script", "-qefc", "python3 -c 'import time; time.sleep(5)'", "/dev/null"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        target = await resolve_trace_target(
            root_pid=proc.pid, expected_basename="python3", timeout=2.0
        )
        assert target.pid != proc.pid, (
            "Resolved PID must not be the wrapper (script) PID — "
            "resolver must walk children to find the workload"
        )
        assert target.comm == "python3", f"Expected comm='python3', got {target.comm!r}"
        assert any(p.endswith("python3") for p in target.cmdline), (
            f"Expected python3 in cmdline, got {target.cmdline!r}"
        )
        assert isinstance(target.starttime_ticks, int) and target.starttime_ticks > 0
    finally:
        proc.kill()
        proc.wait()


@pytest.mark.anyio
async def test_resolve_trace_target_raises_on_miss():
    """resolve_trace_target raises TraceTargetResolutionError when workload never appears.

    Test 1.4: failure must be loud, not a silent fall-back to wrapper PID.
    """
    from autoskillit.execution.linux_tracing import (
        TraceTargetResolutionError,
        resolve_trace_target,
    )

    proc = subprocess.Popen(["sleep", "5"])
    try:
        with pytest.raises(TraceTargetResolutionError) as exc_info:
            await resolve_trace_target(
                root_pid=proc.pid,
                expected_basename="definitely_not_there",
                timeout=0.5,
            )
        error_msg = str(exc_info.value)
        # Error must mention the root pid and/or expected basename so the caller
        # can diagnose what resolution was attempted
        assert str(proc.pid) in error_msg or "definitely_not_there" in error_msg, (
            f"Error message must mention root_pid or expected_basename. Got: {error_msg!r}"
        )
        assert exc_info.value.root_pid == proc.pid
        assert exc_info.value.expected_basename == "definitely_not_there"
    finally:
        proc.kill()
        proc.wait()
