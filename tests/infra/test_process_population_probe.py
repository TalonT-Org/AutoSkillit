"""C8: the ambient *process* surface, open-world.

Mirrors _ambient_env_surface.py's open-world philosophy in the process dimension: rather
than enumerating processes AutoSkillit spawns (a closed world four prior process-lifecycle
rectifies were each structurally unable to extend to a third-party double-forking daemon),
assert an invariant over the outcome -- a bounded probe slice mints no processes that
survive holding a reference into its own dedicated generation.

Marked large: not run in every PR loop. Predicate is scoped to a dedicated generation
(minted via scripts/pytest_tmp_lifecycle.py's setup subcommand) so concurrent activity from
other xdist workers sharing the same --basetemp is invisible by construction -- the same
xdist-cross-contamination hazard the plan explicitly warns this test would otherwise have.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from tests.conftest import production_interpreter_env

pytestmark = [pytest.mark.layer("infra"), pytest.mark.large]

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "pytest_tmp_lifecycle.py"


def _setup_dedicated_generation(platform_root: Path, run_id: str) -> tuple[Path, Path, Path]:
    user_root = platform_root / f"autoskillit-pytest-{os.getuid()}"
    generation = user_root / f"pytest-deadbeef-{run_id}"
    tmp_dir = generation / "tmp"
    cache_dir = generation / "cache"
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "setup",
            "--root",
            str(platform_root),
            "--dir",
            str(tmp_dir),
            "--cache-dir",
            str(cache_dir),
            "--owner-pid",
            str(os.getpid()),
        ],
        env=production_interpreter_env(),
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return generation, tmp_dir, cache_dir


def _processes_referencing(generation: Path) -> set[int]:
    """PIDs whose environ carries a path under `generation` -- the same TMPDIR= evidence
    class core.runtime.harvest_snapshot_references reads, checked directly here since this
    probe cares about raw process survival, not reclamation eligibility."""
    proc_root = Path("/proc")
    holders: set[int] = set()
    try:
        entries = list(proc_root.iterdir())
    except OSError:
        return holders
    generation_str = str(generation)
    for entry in entries:
        if not entry.name.isdigit():
            continue
        try:
            environ = (entry / "environ").read_bytes()
        except OSError:
            continue
        if generation_str.encode() in environ:
            holders.add(int(entry.name))
    return holders


@pytest.mark.skipif(sys.platform != "linux", reason="Linux /proc process-table inspection")
def test_a_bounded_test_slice_mints_no_surviving_processes(tmp_path: Path) -> None:
    platform_root = tmp_path / "platform"
    platform_root.mkdir()
    generation, tmp_dir, _cache_dir = _setup_dedicated_generation(platform_root, "probe")

    before = _processes_referencing(generation)
    assert not before, f"generation already referenced before the probe ran: {before}"

    env = production_interpreter_env()
    env["TMPDIR"] = str(tmp_dir)
    probe = subprocess.run(
        [sys.executable, "-c", "import time; time.sleep(0.1)"],
        env=env,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert probe.returncode == 0, probe.stderr

    # Settle window: a correctly-exiting child must not be miscounted as a leak because
    # its /proc entry (or a double-forked grandchild's) is still being torn down.
    deadline = time.monotonic() + 3
    after: set[int] = set()
    while time.monotonic() < deadline:
        after = _processes_referencing(generation)
        if not after:
            break
        time.sleep(0.1)

    assert not after, (
        f"process(es) still hold a TMPDIR reference into a generation only this probe "
        f"used, after the probe subprocess exited and a settle window elapsed: {after} -- "
        "this is the untraced-autolaunch-caller signal; the diagnostic follow-up is a "
        "dbus-launch shim early on PATH logging its getppid() chain and "
        "PYTEST_CURRENT_TEST to identify the caller"
    )


@pytest.mark.skipif(sys.platform != "linux", reason="Linux /proc process-table inspection")
def test_probe_detects_a_reverted_fix(tmp_path: Path) -> None:
    """A probe that cannot fail is not a probe: confirm it fires when the condition it
    checks for is deliberately reintroduced (a process holding a live reference)."""
    platform_root = tmp_path / "platform"
    platform_root.mkdir()
    generation, tmp_dir, _cache_dir = _setup_dedicated_generation(platform_root, "probe-canary")

    env = production_interpreter_env()
    env["TMPDIR"] = str(tmp_dir)
    holder = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"], env=env, text=True
    )
    try:
        deadline = time.monotonic() + 3
        found: set[int] = set()
        while time.monotonic() < deadline:
            found = _processes_referencing(generation)
            if found:
                break
            time.sleep(0.05)
        assert found, "canary process was not detected holding its own TMPDIR reference"
    finally:
        holder.terminate()
        try:
            holder.wait(timeout=5)
        except subprocess.TimeoutExpired:
            holder.kill()
            holder.wait(timeout=5)
