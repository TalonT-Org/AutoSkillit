"""Contract tests for fleet state file locking.

These tests verify that all fleet state mutations acquire the correct locks
in the correct order, and that all flock call sites target the .lock sidecar,
not the state JSON file directly.
"""

from __future__ import annotations

import ast
import fcntl
import threading
from pathlib import Path
from unittest.mock import patch

import pytest

from autoskillit.fleet import (
    DispatchRecord,
    DispatchStatus,
    append_dispatch_record,
    mark_dispatch_interrupted,
    mark_dispatch_resumable,
    mark_dispatch_running,
    read_state,
    reset_failed_dispatch,
    update_orchestrator_session_id,
    write_captured_values,
    write_initial_state,
)
from autoskillit.fleet.state_recovery import resume_campaign_from_state

pytestmark = [pytest.mark.small, pytest.mark.feature("fleet")]

_MUTATION_FUNCTIONS: dict[str, object] = {
    "mark_dispatch_running": mark_dispatch_running,
    "mark_dispatch_interrupted": mark_dispatch_interrupted,
    "mark_dispatch_resumable": mark_dispatch_resumable,
    "append_dispatch_record": append_dispatch_record,
    "write_captured_values": write_captured_values,
    "reset_failed_dispatch": reset_failed_dispatch,
    "update_orchestrator_session_id": update_orchestrator_session_id,
}


# -------------------------------------------------------------------
# 1a. Lock-target consistency contract test (AST scan)
# -------------------------------------------------------------------


class TestFlockLockTarget:
    def test_all_fleet_flock_callers_use_lock_sidecar(self) -> None:
        """Every fcntl.flock call in fleet/ and cli/fleet/ must target the .lock sidecar.

        Uses AST to find all open() calls inside "with" statements and verifies
        that any fcntl.flock() call inside a non-.lock opening is flagged.
        """
        import autoskillit.cli.fleet
        import autoskillit.fleet

        fleet_root = Path(autoskillit.fleet.__file__).parent
        cli_fleet_root = Path(autoskillit.cli.fleet.__file__).parent

        violations: list[tuple[str, str, int]] = []

        for root in [fleet_root, cli_fleet_root]:
            for py_file in root.rglob("*.py"):
                try:
                    content = py_file.read_text()
                    tree = ast.parse(content, filename=str(py_file))
                except SyntaxError:
                    continue

                for node in ast.walk(tree):
                    if not isinstance(node, ast.With):
                        continue
                    for item in node.items:
                        if not isinstance(item.context_expr, ast.Call):
                            continue
                        call = item.context_expr
                        if not (
                            isinstance(call.func, ast.Attribute)
                            and call.func.attr == "open"
                            and call.args
                        ):
                            continue
                        arg_src = ast.unparse(call.args[0])
                        if ".lock" in arg_src or "with_suffix" in arg_src:
                            continue
                        # Non-.lock file opened — check for fcntl.flock inside
                        for child in ast.walk(node):
                            if (
                                isinstance(child, ast.Call)
                                and isinstance(child.func, ast.Attribute)
                                and child.func.attr == "flock"
                                and isinstance(child.func.value, ast.Name)
                                and child.func.value.id == "fcntl"
                            ):
                                violations.append((str(py_file), arg_src, call.lineno))

        assert not violations, (
            f"Found {len(violations)} flock call(s) targeting non-.lock file:\n"
            + "\n".join(f"  {f}:{ln} → {expr}" for f, expr, ln in violations)
        )


# -------------------------------------------------------------------
# 1b. All mutation functions acquire flock
# -------------------------------------------------------------------


class TestAllMutationsAcquireLock:
    @pytest.mark.parametrize(
        "fn_name,fn", list(_MUTATION_FUNCTIONS.items()), ids=list(_MUTATION_FUNCTIONS.keys())
    )
    def test_state_mutation_acquires_flock(self, tmp_path: Path, fn_name: str, fn: object) -> None:
        """Each state mutation function must call fcntl.flock before mutating state."""
        sp = tmp_path / "state.json"
        write_initial_state(sp, "cid", "camp", "/m.yaml", [DispatchRecord(name="d1")])

        flock_calls: list[tuple[int, int]] = []
        original_flock = fcntl.flock

        def tracking_flock(fd: int, op: int) -> None:
            flock_calls.append((fd, op))
            return original_flock(fd, op)

        # Some transitions require specific pre-states
        if fn_name in ("mark_dispatch_interrupted", "mark_dispatch_resumable"):
            # These need RUNNING dispatch — inject via direct JSON modification
            import json

            raw = json.loads(sp.read_text())
            raw["dispatches"][0]["status"] = "running"
            sp.write_text(json.dumps(raw))

        with patch("fcntl.flock", side_effect=tracking_flock):
            if fn_name == "mark_dispatch_running":
                fn(sp, "d1", dispatch_id="x", dispatched_pid=42)  # type: ignore[operator]
            elif fn_name == "mark_dispatch_interrupted":
                fn(sp, "d1", reason="test")  # type: ignore[operator]
            elif fn_name == "mark_dispatch_resumable":
                fn(sp, "d1", sidecar_path="/tmp/sidecar")  # type: ignore[operator]
            elif fn_name == "append_dispatch_record":
                fn(sp, DispatchRecord(name="d1", status=DispatchStatus.SUCCESS))  # type: ignore[operator]
            elif fn_name == "write_captured_values":
                fn(sp, {"key": "val"})  # type: ignore[operator]
            elif fn_name == "reset_failed_dispatch":
                append_dispatch_record(
                    sp, DispatchRecord(name="d1", status=DispatchStatus.FAILURE)
                )
                flock_calls.clear()
                fn(sp, "d1")  # type: ignore[operator]
            elif fn_name == "update_orchestrator_session_id":
                fn(sp, "sess-123")  # type: ignore[operator]

        assert flock_calls, f"{fn_name} did not call fcntl.flock"
        assert any(op == fcntl.LOCK_EX for _, op in flock_calls), (
            f"{fn_name} called flock but not with LOCK_EX"
        )


# -------------------------------------------------------------------
# 1c. Cross-caller concurrency test
# -------------------------------------------------------------------


class TestReapAndResumeMutualExclusion:
    def test_reap_and_resume_mutual_exclusion(self, tmp_path: Path) -> None:
        """Reap and resume must not corrupt state when racing on the same file.

        Sets up a state file with one RUNNING dispatch (stale PID), then
        spawns two threads: one calling _reap_stale_dispatches and one calling
        resume_campaign_from_state. Verifies the dispatch ends in exactly one
        terminal state and the state file is never corrupted.
        """
        import json

        from autoskillit.cli.fleet import _reap_stale_dispatches

        sp = tmp_path / "state.json"
        write_initial_state(sp, "cid", "camp", "/m.yaml", [DispatchRecord(name="d1")])
        raw = json.loads(sp.read_text())
        raw["dispatches"][0].update(
            {
                "status": "running",
                "dispatch_id": "did-1",
                "dispatched_pid": 99999,
                "dispatched_starttime_ticks": 0,
                "dispatched_boot_id": "boot-test",
                "started_at": 0.0,
            }
        )
        sp.write_text(json.dumps(raw))

        barrier = threading.Barrier(2, timeout=5)

        def do_reap() -> None:
            barrier.wait()
            with patch("psutil.pid_exists", return_value=False):
                with patch("autoskillit.execution.read_boot_id", return_value="boot-test"):
                    _reap_stale_dispatches(sp, dry_run=False)

        def do_resume() -> None:
            barrier.wait()
            resume_campaign_from_state(sp, continue_on_failure=True)

        t1 = threading.Thread(target=do_reap)
        t2 = threading.Thread(target=do_resume)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        state_data = json.loads(sp.read_text())
        assert len(state_data["dispatches"]) == 1
        disp = state_data["dispatches"][0]
        assert disp["status"] in ("interrupted", "resumable"), (
            f"Unexpected status {disp['status']}"
        )
        terminal_count = sum(
            1
            for d in state_data["dispatches"]
            if d["status"] in ("interrupted", "resumable", "success", "failure")
        )
        assert terminal_count == 1


# -------------------------------------------------------------------
# 1d. Two-write atomicity test
# -------------------------------------------------------------------


class TestAppendAndCaptureAtomic:
    def test_append_and_capture_atomic(self, tmp_path: Path) -> None:
        """append_dispatch_record + write_captured_values must be atomic together.

        A concurrent reader must never observe all_success=True with
        captured_values={} (the intermediate two-write window).
        """
        sp = tmp_path / "state.json"
        write_initial_state(sp, "cid", "camp", "/m.yaml", [DispatchRecord(name="d1")])

        observed_intermediate: list[bool] = []

        def reader() -> None:
            for _ in range(50):
                state = read_state(sp)
                if state is None:
                    continue
                all_success = all(d.status == DispatchStatus.SUCCESS for d in state.dispatches)
                if all_success and not state.captured_values:
                    observed_intermediate.append(True)

        def writer() -> None:
            append_dispatch_record(sp, DispatchRecord(name="d1", status=DispatchStatus.SUCCESS))
            write_captured_values(sp, {"key": "val"})

        t_write = threading.Thread(target=writer)
        t_read = threading.Thread(target=reader)
        t_write.start()
        t_read.start()
        t_write.join()
        t_read.join()

        assert not observed_intermediate, (
            "Reader observed all_success=True with captured_values={} — "
            "two-write TOCTOU window exists"
        )
