"""Integration coverage for the managed-process combined output ceiling."""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import pytest

from autoskillit.core.types import KillReason, TerminationReason
from autoskillit.execution.process import run_managed_async

pytestmark = [pytest.mark.layer("execution"), pytest.mark.medium]


_INCREMENTAL_DUAL_STREAM_OUTPUT = textwrap.dedent(
    """\
    import sys
    import time

    for _ in range(100):
        sys.stdout.write("stdout-chunk-" + "o" * 64 + "\\n")
        sys.stdout.flush()
        sys.stderr.write("stderr-chunk-" + "e" * 64 + "\\n")
        sys.stderr.flush()
        time.sleep(0.02)

    print("terminal-marker")
    """
)


@pytest.mark.anyio
async def test_combined_output_ceiling_kills_and_bounds_retained_streams(tmp_path: Path) -> None:
    ceiling = 320

    result = await run_managed_async(
        [sys.executable, "-u", "-c", _INCREMENTAL_DUAL_STREAM_OUTPUT],
        cwd=tmp_path,
        timeout=10,
        max_combined_output_bytes=ceiling,
    )

    assert result.termination is TerminationReason.OUTPUT_LIMIT
    assert result.kill_reason is KillReason.INFRA_KILL
    assert result.cleanup_evidence is not None
    assert result.cleanup_evidence.complete is True
    assert result.stdout
    assert result.stderr
    assert len(result.stdout.encode()) + len(result.stderr.encode()) <= ceiling
    assert "terminal-marker" not in result.stdout
    assert "terminal-marker" not in result.stderr
