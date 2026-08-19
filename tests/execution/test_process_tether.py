"""Tests for the process-tether registry: spawner-death immunity for detached children.

Class-level regression coverage for issue #4678 Incident A: a detached child
whose only guardian is its mortal spawner survives that spawner's death
indefinitely. See ``execution/process/_process_tether.py`` for the mechanism
these tests exercise: a mandatory tether record written at spawn time, and a
generic sweep that reaps only identity-verified targets of a dead-or-expired
guardian.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from contextlib import suppress

import psutil
import pytest

from autoskillit.config._config_dataclasses import ProcessTetherConfig
from autoskillit.core import read_boot_id, read_starttime_ticks
from autoskillit.execution import (
    DEFAULT_TETHER_CEILING_SECONDS,
    INTERACTIVE_TETHER_CEILING_SECONDS,
    TetherSpec,
    probe_systemd_scope_available,
    spawn_owned_process,
    sweep_orphaned_tethers,
    wrap_systemd_scope,
)
from autoskillit.execution.process._process_tether import (
    TetherRecord,
    write_tether,
)

pytestmark = [
    pytest.mark.layer("execution"),
    pytest.mark.medium,
    pytest.mark.skipif(sys.platform != "linux", reason="Linux only"),
]


def _sleeper_cmd(seconds: float = 30.0) -> list[str]:
    return [sys.executable, "-c", f"import time; time.sleep({seconds})"]


def _read_single_tether(tether_dir) -> dict:
    files = list(tether_dir.glob("*.json"))
    assert len(files) == 1, f"expected exactly one tether under {tether_dir}, found {files}"
    return json.loads(files[0].read_text())


def _wait_for_death(pid: int, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while psutil.pid_exists(pid) and time.monotonic() < deadline:
        time.sleep(0.1)
    assert not psutil.pid_exists(pid), f"pid {pid} should be dead"


def _write_synthetic_tether(
    tether_dir,
    *,
    child_pid: int,
    child_pgid: int | None = None,
    child_starttime_ticks: int | None = None,
    boot_id: str | None = None,
    spawner_pid: int | None = None,
    spawner_starttime_ticks: int | None = None,
    not_after: float | None = None,
    origin: str = "test",
    pidns_inode: int | None = None,
    workload_pid: int | None = None,
    workload_starttime_ticks: int | None = None,
) -> object:
    """Build and durably write one tether record with sensible real-identity defaults."""
    record = TetherRecord(
        child_pid=child_pid,
        child_pgid=child_pgid if child_pgid is not None else child_pid,
        child_starttime_ticks=(
            child_starttime_ticks
            if child_starttime_ticks is not None
            else (read_starttime_ticks(child_pid) or 0)
        ),
        boot_id=boot_id if boot_id is not None else (read_boot_id() or ""),
        spawner_pid=spawner_pid if spawner_pid is not None else os.getpid(),
        spawner_starttime_ticks=(
            spawner_starttime_ticks
            if spawner_starttime_ticks is not None
            else (
                read_starttime_ticks(spawner_pid if spawner_pid is not None else os.getpid()) or 0
            )
        ),
        spawned_at_ns=time.time_ns(),
        not_after=not_after if not_after is not None else time.time() + 60.0,
        origin=origin,
        pidns_inode=pidns_inode,
        workload_pid=workload_pid,
        workload_starttime_ticks=workload_starttime_ticks,
    )
    return write_tether(record, tether_dir)


class TestSpawnWritesTether:
    def test_spawn_writes_tether(self, tmp_path) -> None:
        owner = spawn_owned_process(
            _sleeper_cmd(),
            start_new_session=True,
            tether=TetherSpec(origin="test", ceiling_seconds=60.0, tether_dir=tmp_path),
        )
        try:
            data = _read_single_tether(tmp_path)
            assert data["child_pid"] == owner.pid
            assert data["child_pgid"] == owner.pgid
            assert data["child_starttime_ticks"] == read_starttime_ticks(owner.pid)
            assert data["boot_id"] == read_boot_id()
            assert data["spawner_pid"] == os.getpid()
            assert data["spawner_starttime_ticks"] == read_starttime_ticks(os.getpid())
            assert abs(data["not_after"] - (time.time() + 60.0)) < 10.0
            assert data["origin"] == "test"
        finally:
            owner.settle_evidence()


class TestSpawnOwnedProcessRequiresTether:
    def test_spawn_owned_process_requires_tether(self) -> None:
        """``tether`` is keyword-only with no default — locks the required-param
        invariant against future soft-defaulting that would silently reopen an
        untethered detached-spawn path."""
        with pytest.raises(TypeError):
            spawn_owned_process(_sleeper_cmd(), start_new_session=True)  # type: ignore[call-arg]


class TestSettleRemovesTether:
    def test_settle_removes_tether(self, tmp_path) -> None:
        owner = spawn_owned_process(
            [sys.executable, "-c", "pass"],
            start_new_session=True,
            tether=TetherSpec(origin="test", ceiling_seconds=60.0, tether_dir=tmp_path),
        )
        owner.settle()
        assert list(tmp_path.glob("*.json")) == []


class TestSpawnFailsClosedOnUnwritableTetherDir:
    def test_spawn_fails_closed_on_unwritable_tether_dir(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An untethered detached child must never exist.

        Simulates an unwritable tether dir by making ``write_tether`` raise —
        chmod-based read-only-dir simulation is unreliable under root (CI
        containers frequently run as root, where directory permissions are
        not enforced at all).
        """
        captured: dict[str, int] = {}

        def _boom(record, tether_dir):
            captured["pid"] = record.child_pid
            raise OSError("simulated unwritable tether dir")

        monkeypatch.setattr("autoskillit.execution.process._process_kill.write_tether", _boom)

        with pytest.raises(OSError, match="simulated unwritable tether dir"):
            spawn_owned_process(
                _sleeper_cmd(),
                start_new_session=True,
                tether=TetherSpec(origin="test", ceiling_seconds=60.0, tether_dir=tmp_path),
            )

        assert "pid" in captured
        _wait_for_death(captured["pid"])


class TestSweepReapsChildOfDeadSpawner:
    def test_sweep_reaps_child_of_dead_spawner(self, tmp_path) -> None:
        """The two-hop spawner-death test — the direct regression test for Incident A."""
        tether_dir_repr = repr(str(tmp_path))
        script = (
            "import sys, time\n"
            "from pathlib import Path\n"
            "from autoskillit.execution import TetherSpec, spawn_owned_process\n"
            "owner = spawn_owned_process(\n"
            "    [sys.executable, '-c', 'import time; time.sleep(60)'],\n"
            "    start_new_session=True,\n"
            f"    tether=TetherSpec(origin='test', ceiling_seconds=60.0,\n"
            f"                      tether_dir=Path({tether_dir_repr})),\n"
            ")\n"
            "print(owner.pid, flush=True)\n"
            "time.sleep(60)\n"
        )
        intermediate = subprocess.Popen(
            [sys.executable, "-c", script],
            stdout=subprocess.PIPE,
            text=True,
        )
        grandchild_pid: int | None = None
        try:
            assert intermediate.stdout is not None
            line = intermediate.stdout.readline()
            grandchild_pid = int(line.strip())

            deadline = time.monotonic() + 5.0
            while not list(tmp_path.glob("*.json")) and time.monotonic() < deadline:
                time.sleep(0.05)
            assert list(tmp_path.glob("*.json")), "tether was never written by the intermediate"

            intermediate.kill()
            intermediate.wait(timeout=5)
            _wait_for_death(intermediate.pid)

            report = sweep_orphaned_tethers(tmp_path, min_age_seconds=0.0)

            _wait_for_death(grandchild_pid)
            assert list(tmp_path.glob("*.json")) == []
            assert any(o.outcome == "reaped_orphan" for o in report.outcomes)
        finally:
            with suppress(Exception):
                intermediate.kill()
                intermediate.wait(timeout=2)
            if grandchild_pid is not None:
                with suppress(ProcessLookupError, OSError):
                    os.kill(grandchild_pid, signal.SIGKILL)


class TestSweepReapsExpiredCeilingWithLiveSpawner:
    def test_sweep_reaps_expired_ceiling_with_live_spawner(self, tmp_path) -> None:
        child = subprocess.Popen(_sleeper_cmd(), start_new_session=True)
        try:
            _write_synthetic_tether(
                tmp_path,
                child_pid=child.pid,
                not_after=time.time() - 10.0,
            )
            report = sweep_orphaned_tethers(tmp_path, min_age_seconds=0.0)
            _wait_for_death(child.pid)
            assert list(tmp_path.glob("*.json")) == []
            assert any(o.outcome == "reaped_ceiling" for o in report.outcomes)
        finally:
            with suppress(Exception):
                child.kill()
                child.wait(timeout=2)


class TestSweepLeavesLiveSpawnerWithinCeiling:
    def test_sweep_leaves_live_spawner_within_ceiling(self, tmp_path) -> None:
        child = subprocess.Popen(_sleeper_cmd(), start_new_session=True)
        try:
            _write_synthetic_tether(
                tmp_path,
                child_pid=child.pid,
                not_after=time.time() + 3600.0,
            )
            report = sweep_orphaned_tethers(tmp_path, min_age_seconds=0.0)
            assert psutil.pid_exists(child.pid)
            assert len(list(tmp_path.glob("*.json"))) == 1
            assert any(o.outcome == "kept" for o in report.outcomes)
        finally:
            with suppress(Exception):
                child.kill()
                child.wait(timeout=2)


class TestSweepIdentityMismatchNoKill:
    def test_sweep_identity_mismatch_forged_starttime_ticks(self, tmp_path) -> None:
        child = subprocess.Popen(_sleeper_cmd(), start_new_session=True)
        try:
            _write_synthetic_tether(
                tmp_path,
                child_pid=child.pid,
                child_starttime_ticks=999_999_999,
                not_after=time.time() - 10.0,
            )
            report = sweep_orphaned_tethers(tmp_path, min_age_seconds=0.0)
            assert psutil.pid_exists(child.pid)
            assert list(tmp_path.glob("*.json")) == []
            assert any(o.outcome == "identity_mismatch" for o in report.outcomes)
        finally:
            with suppress(Exception):
                child.kill()
                child.wait(timeout=2)

    def test_sweep_identity_mismatch_forged_pidns_inode(self, tmp_path) -> None:
        child = subprocess.Popen(_sleeper_cmd(), start_new_session=True)
        try:
            _write_synthetic_tether(
                tmp_path,
                child_pid=child.pid,
                pidns_inode=1,  # a plainly-wrong inode value
                not_after=time.time() - 10.0,
            )
            report = sweep_orphaned_tethers(tmp_path, min_age_seconds=0.0)
            assert psutil.pid_exists(child.pid)
            assert list(tmp_path.glob("*.json")) == []
            assert any(o.outcome == "identity_mismatch" for o in report.outcomes)
        finally:
            with suppress(Exception):
                child.kill()
                child.wait(timeout=2)

    def test_sweep_reaches_verdict_when_pidns_inode_absent(self, tmp_path) -> None:
        """An absent pidns_inode falls back to the triple — never refuses or crashes."""
        child = subprocess.Popen(_sleeper_cmd(), start_new_session=True)
        try:
            _write_synthetic_tether(
                tmp_path,
                child_pid=child.pid,
                pidns_inode=None,
                not_after=time.time() - 10.0,
            )
            report = sweep_orphaned_tethers(tmp_path, min_age_seconds=0.0)
            _wait_for_death(child.pid)
            assert list(tmp_path.glob("*.json")) == []
            assert any(o.outcome == "reaped_ceiling" for o in report.outcomes)
        finally:
            with suppress(Exception):
                child.kill()
                child.wait(timeout=2)


class TestSweepRemovesTetherForDeadChild:
    def test_sweep_removes_tether_for_dead_child(self, tmp_path) -> None:
        child = subprocess.Popen([sys.executable, "-c", "pass"], start_new_session=True)
        child.wait(timeout=5)
        _wait_for_death(child.pid)
        _write_synthetic_tether(
            tmp_path,
            child_pid=child.pid,
            not_after=time.time() + 3600.0,  # live spawner, ceiling not expired
        )
        report = sweep_orphaned_tethers(tmp_path, min_age_seconds=0.0)
        assert list(tmp_path.glob("*.json")) == []
        assert any(o.outcome == "dead_child" for o in report.outcomes)


class TestSweepMinAgeGate:
    def test_young_record_untouched_regardless_of_content(self, tmp_path) -> None:
        path = tmp_path / "not-even-json.json"
        path.write_text("not valid json{{{")
        report = sweep_orphaned_tethers(tmp_path, min_age_seconds=3600.0)
        assert path.exists()
        assert report.outcomes == ()

    def test_old_malformed_record_deleted(self, tmp_path) -> None:
        path = tmp_path / "not-even-json.json"
        path.write_text("not valid json{{{")
        old = time.time() - 3600.0
        os.utime(path, (old, old))
        report = sweep_orphaned_tethers(tmp_path, min_age_seconds=0.0)
        assert not path.exists()
        assert any(o.outcome == "malformed" for o in report.outcomes)


class TestPtyWrappedSpawnUpdatesTetherWithWorkloadIdentity:
    @pytest.mark.anyio
    async def test_pty_wrapped_spawn_updates_tether_with_workload_identity(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import shutil

        import autoskillit.execution.process as process_module
        from autoskillit.execution.process import run_managed_async

        if shutil.which("script") is None:
            pytest.skip("script(1) unavailable")

        # By the time run_managed_async returns, a successfully-completed spawn's
        # tether has already been removed by cleanup() — assert on the resolved
        # workload identity as it's applied, not on post-hoc file state. Patched
        # via the module object (not a string path) — run_managed_async resolves
        # this bare name from execution.process's own globals, the same module
        # it's defined in, not from the outer execution package's re-export.
        update_calls: list[tuple[object, ...]] = []
        monkeypatch.setattr(
            process_module,
            "update_tether_workload",
            lambda path, workload_pid, workload_starttime_ticks: update_calls.append(
                (path, workload_pid, workload_starttime_ticks)
            ),
        )

        result = await run_managed_async(
            [sys.executable, "-c", "import time; time.sleep(0.3)"],
            cwd=tmp_path,
            timeout=5.0,
            pty_mode=True,
            ceiling_seconds=60.0,
        )
        assert result is not None
        assert update_calls, "update_tether_workload was never called for a PTY-wrapped spawn"
        _path, workload_pid, workload_starttime_ticks = update_calls[0]
        assert workload_pid != result.pid
        assert isinstance(workload_pid, int) and workload_pid > 0
        assert isinstance(workload_starttime_ticks, int) and workload_starttime_ticks > 0


class TestPtyWorkloadResolutionFailureDoesNotAbortSpawn:
    @pytest.mark.anyio
    async def test_pty_workload_resolution_failure_does_not_abort_spawn(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import shutil

        import structlog.testing

        import autoskillit.execution.process as process_module
        from autoskillit.execution.linux_tracing import TraceTargetResolutionError
        from autoskillit.execution.process import run_managed_async

        if shutil.which("script") is None:
            pytest.skip("script(1) unavailable")

        async def _boom(**kwargs):
            raise TraceTargetResolutionError(root_pid=1, expected_basename="python")

        monkeypatch.setattr("autoskillit.execution.linux_tracing.resolve_trace_target", _boom)

        # See the sibling test above for why this is patched via the module
        # object rather than a string path.
        update_calls: list[object] = []
        monkeypatch.setattr(
            process_module,
            "update_tether_workload",
            lambda *a, **kw: update_calls.append((a, kw)),
        )

        with structlog.testing.capture_logs() as cap_logs:
            result = await run_managed_async(
                [sys.executable, "-c", "pass"],
                cwd=tmp_path,
                timeout=5.0,
                pty_mode=True,
                ceiling_seconds=60.0,
            )
        assert result is not None
        assert update_calls == []
        assert any(entry.get("event") == "tether_workload_resolution_failed" for entry in cap_logs)


class TestSweepReapsWorkloadWhenWrapperDead:
    def test_sweep_reaps_workload_when_wrapper_dead(self, tmp_path) -> None:
        """The wrapper-death test: guards the one-level-down recurrence of the orphan class."""
        wrapper = subprocess.Popen([sys.executable, "-c", "pass"], start_new_session=True)
        wrapper.wait(timeout=5)
        _wait_for_death(wrapper.pid)

        workload = subprocess.Popen(_sleeper_cmd(), start_new_session=True)
        try:
            dead_spawner = subprocess.Popen([sys.executable, "-c", "pass"], start_new_session=True)
            dead_spawner.wait(timeout=5)
            _wait_for_death(dead_spawner.pid)

            _write_synthetic_tether(
                tmp_path,
                child_pid=wrapper.pid,
                spawner_pid=dead_spawner.pid,
                spawner_starttime_ticks=1,  # any value refuses match; real ticks are gone
                not_after=time.time() + 3600.0,
                workload_pid=workload.pid,
                workload_starttime_ticks=read_starttime_ticks(workload.pid),
            )
            report = sweep_orphaned_tethers(tmp_path, min_age_seconds=0.0)
            _wait_for_death(workload.pid)
            assert list(tmp_path.glob("*.json")) == []
            assert any(o.outcome == "reaped_orphan" for o in report.outcomes)
        finally:
            with suppress(Exception):
                workload.kill()
                workload.wait(timeout=2)


class TestConfigParityAndCoherence:
    def test_config_defaults_equal_module_constants(self) -> None:
        cfg = ProcessTetherConfig()
        assert cfg.orphan_ceiling_seconds == DEFAULT_TETHER_CEILING_SECONDS
        assert cfg.cook_ceiling_seconds == INTERACTIVE_TETHER_CEILING_SECONDS

    def test_coherence_gate_warns_when_ceiling_undercuts_max_session_duration(self) -> None:
        import structlog.testing

        from autoskillit.config._config_dataclasses import FleetConfig, RunSkillConfig
        from autoskillit.config.settings import _process_tether_coherence_gate

        fleet = FleetConfig(default_timeout_sec=3600, max_extension_seconds=7200)
        run_skill = RunSkillConfig()
        max_session = max(
            fleet.default_timeout_sec + fleet.max_extension_seconds, run_skill.timeout
        )
        tether_cfg = ProcessTetherConfig(orphan_ceiling_seconds=max_session - 1)

        with structlog.testing.capture_logs() as cap_logs:
            _process_tether_coherence_gate(tether_cfg, fleet, run_skill)
        assert any(
            "process_tether_ceiling_coherence" in entry.get("event", "") for entry in cap_logs
        )

    def test_coherence_gate_silent_when_ceiling_exceeds_max_session_duration(self) -> None:
        import structlog.testing

        from autoskillit.config._config_dataclasses import FleetConfig, RunSkillConfig
        from autoskillit.config.settings import _process_tether_coherence_gate

        fleet = FleetConfig()
        run_skill = RunSkillConfig()
        tether_cfg = ProcessTetherConfig()  # defaults are safe today

        with structlog.testing.capture_logs() as cap_logs:
            _process_tether_coherence_gate(tether_cfg, fleet, run_skill)
        assert not any(
            "process_tether_ceiling_coherence" in entry.get("event", "") for entry in cap_logs
        )


class TestSystemdScopeWrap:
    """Optional-ceiling tests: config default off / probe fails / probe passes."""

    def test_disabled_leaves_argv_unchanged(self, monkeypatch: pytest.MonkeyPatch) -> None:
        probed = False

        def _fail_if_probed() -> bool:
            nonlocal probed
            probed = True
            return True

        monkeypatch.setattr(
            "autoskillit.execution.process._process_tether.probe_systemd_scope_available",
            _fail_if_probed,
        )
        result = wrap_systemd_scope(["echo", "hi"], enabled=False, ceiling_seconds=60.0)
        assert result == ["echo", "hi"]
        assert probed is False, "disabled must short-circuit before probing"

    def test_enabled_probe_fails_leaves_argv_unchanged_and_warns(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import structlog.testing

        monkeypatch.setattr(
            "autoskillit.execution.process._process_tether.probe_systemd_scope_available",
            lambda: False,
        )
        with structlog.testing.capture_logs() as cap_logs:
            result = wrap_systemd_scope(["echo", "hi"], enabled=True, ceiling_seconds=60.0)
        assert result == ["echo", "hi"]
        assert any(entry.get("event") == "systemd_scope_probe_failed" for entry in cap_logs)

    def test_enabled_probe_passes_prefixes_systemd_run_wrapper(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "autoskillit.execution.process._process_tether.probe_systemd_scope_available",
            lambda: True,
        )
        result = wrap_systemd_scope(["echo", "hi"], enabled=True, ceiling_seconds=90.0)
        assert result == [
            "systemd-run",
            "--user",
            "--scope",
            "--quiet",
            "-p",
            "RuntimeMaxSec=90",
            "echo",
            "hi",
        ]

    def test_probe_false_without_systemd_run_on_path(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "autoskillit.execution.process._process_tether.shutil.which", lambda _name: None
        )
        assert probe_systemd_scope_available() is False

    def test_probe_false_when_systemctl_reports_not_running(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import subprocess as _subprocess

        monkeypatch.setattr(
            "autoskillit.execution.process._process_tether.shutil.which",
            lambda _name: "/usr/bin/systemd-run",
        )

        def _fake_run(*args, **kwargs):
            return _subprocess.CompletedProcess(args, 1, stdout="offline\n")

        monkeypatch.setattr(
            "autoskillit.execution.process._process_tether.subprocess.run", _fake_run
        )
        assert probe_systemd_scope_available() is False

    def test_probe_true_when_systemctl_reports_running(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import subprocess as _subprocess

        monkeypatch.setattr(
            "autoskillit.execution.process._process_tether.shutil.which",
            lambda _name: "/usr/bin/systemd-run",
        )

        def _fake_run(*args, **kwargs):
            return _subprocess.CompletedProcess(args, 0, stdout="running\n")

        monkeypatch.setattr(
            "autoskillit.execution.process._process_tether.subprocess.run", _fake_run
        )
        assert probe_systemd_scope_available() is True

    def test_probe_true_when_systemctl_reports_degraded(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import subprocess as _subprocess

        monkeypatch.setattr(
            "autoskillit.execution.process._process_tether.shutil.which",
            lambda _name: "/usr/bin/systemd-run",
        )

        def _fake_run(*args, **kwargs):
            return _subprocess.CompletedProcess(args, 0, stdout="degraded\n")

        monkeypatch.setattr(
            "autoskillit.execution.process._process_tether.subprocess.run", _fake_run
        )
        assert probe_systemd_scope_available() is True

    def test_probe_false_on_systemctl_timeout(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import subprocess as _subprocess

        monkeypatch.setattr(
            "autoskillit.execution.process._process_tether.shutil.which",
            lambda _name: "/usr/bin/systemd-run",
        )

        def _raise_timeout(*args, **kwargs):
            raise _subprocess.TimeoutExpired(cmd="systemctl", timeout=5.0)

        monkeypatch.setattr(
            "autoskillit.execution.process._process_tether.subprocess.run", _raise_timeout
        )
        assert probe_systemd_scope_available() is False
