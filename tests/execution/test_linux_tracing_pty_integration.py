"""Integration test: PTY-wrapped command is traced at the workload level, not the wrapper.

Regression test for issue #806: anyio.open_process([script, ...]) returns the PID
of the script(1) PTY wrapper, not the intended workload binary. Before the fix,
every snapshot in proc_trace.jsonl described script(1) (~2 MB RSS, 1 thread,
do_sys_poll wchan) instead of the actual workload.

After the fix (TraceTarget resolver), the tracer resolves from the spawn PID to
the workload PID by walking descendants and matching on the command basename.
"""

from __future__ import annotations

import json
import shutil
import sys

import pytest

from tests.execution.conftest import _ALLOCATE_60MB_SCRIPT

pytestmark = [
    pytest.mark.layer("execution"),
    pytest.mark.medium,
    pytest.mark.skipif(
        sys.platform != "linux",
        reason="Linux only — tests PTY wrapping + /proc tracing",
    ),
]

# Skip the entire module when script(1) is absent; no stub needed.
pytestmark_script = pytest.mark.skipif(
    shutil.which("script") is None,
    reason="script(1) not available on this system",
)


@pytestmark_script
@pytest.mark.anyio
async def test_pty_wrapped_command_is_traced_as_grandchild(isolated_tracing_config, tmp_path):
    """run_managed_async(pty_mode=True, tracing) observes the workload, not script(1).

    Test 1.1 — reproduces the exact production combination that no pre-#806 test
    exercised: pty_mode=True + linux_tracing_config together.

    Failure signal:
    - peak_rss_kb ≤ 30_000 → tracer is watching script(1) (~2 MB)
    - comm == 'script' in snapshots → tracer is watching script(1) by name
    """
    from autoskillit.execution.process import run_managed_async

    helper = tmp_path / "allocate_60mb.py"
    helper.write_text(_ALLOCATE_60MB_SCRIPT)

    result = await run_managed_async(
        ["python3", str(helper)],
        cwd=tmp_path,
        timeout=30.0,
        pty_mode=True,
        linux_tracing_config=isolated_tracing_config,
    )

    assert result.proc_snapshots is not None, (
        "proc_snapshots must be collected when linux_tracing_config is provided"
    )
    snapshots = result.proc_snapshots
    assert len(snapshots) >= 1, "At least one snapshot must be captured"

    # No snapshot should describe script(1) — only the workload
    comms = [s.get("comm", "") for s in snapshots]
    assert "script" not in comms, (
        f"Found script(1) comm in snapshots — tracer is still observing the PTY wrapper. "
        f"comms={comms}. This is issue #806."
    )

    # At least one snapshot must show the workload's elevated RSS
    peak_rss = max((s.get("vm_rss_kb", 0) for s in snapshots), default=0)
    assert peak_rss > 30_000, (
        f"Peak RSS is {peak_rss} KB — too low for a 60 MB allocation. "
        "If the tracer is watching script(1) it would see ~2 MB RSS. "
        "This is the exact fingerprint of issue #806."
    )

    # At least one snapshot must self-identify as the python workload
    workload_comms = [c for c in comms if "python" in c.lower()]
    assert workload_comms, (
        f"No python comm found in snapshots. comms={comms}. "
        "The workload process should have comm containing 'python'."
    )


@pytestmark_script
@pytest.mark.anyio
async def test_pty_wrapped_tracing_produces_no_script_snapshots_in_proc_trace_jsonl(
    isolated_tracing_config, tmp_path
):
    """proc_trace.jsonl must not contain any rows with comm='script'.

    Test 1.10 (partial): after the fix, every row in proc_trace.jsonl self-identifies
    the tracked process, and 'script' must never appear there.
    """
    from autoskillit.execution.process import run_managed_async
    from autoskillit.execution.session_log import flush_session_log

    helper = tmp_path / "allocate_60mb.py"
    helper.write_text(_ALLOCATE_60MB_SCRIPT)

    result = await run_managed_async(
        ["python3", str(helper)],
        cwd=tmp_path,
        timeout=30.0,
        pty_mode=True,
        linux_tracing_config=isolated_tracing_config,
    )

    assert result.proc_snapshots is not None

    flush_session_log(
        log_dir=str(tmp_path / "logs"),
        cwd=str(tmp_path),
        session_id="pty-trace-test-001",
        pid=result.pid,
        skill_command="/test",
        success=result.returncode == 0,
        subtype="completed",
        exit_code=result.returncode if result.returncode is not None else -1,
        start_ts=result.start_ts or "2026-01-01T00:00:00+00:00",
        proc_snapshots=result.proc_snapshots,
    )

    trace_path = tmp_path / "logs" / "sessions" / "pty-trace-test-001" / "proc_trace.jsonl"
    assert trace_path.exists(), "proc_trace.jsonl must be written"
    rows = [json.loads(line) for line in trace_path.read_text().splitlines()]
    assert rows, "proc_trace.jsonl must have at least one row"

    for row in rows:
        assert "comm" in row, f"Every row must have a 'comm' field. Row: {row}"
        assert row["comm"] != "script", (
            f"Found comm='script' in proc_trace.jsonl — tracer observed PTY wrapper. Row: {row}"
        )
