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
    reset_blocking_dispatch,
    update_orchestrator_session_id,
    upsert_dispatch_record_by_name,
    write_captured_values,
    write_initial_state,
)

pytestmark = [pytest.mark.layer("fleet"), pytest.mark.small, pytest.mark.feature("fleet")]

_FCNTL_ALLOWED_RELATIVE_PATHS: frozenset[str] = frozenset(
    {
        "core/_plugin_cache.py",
        "execution/session/_session_state.py",
        "workspace/clone_registry.py",
        "fleet/state.py",
        "planner/merge.py",
    }
)

_MUTATION_FUNCTIONS: dict[str, object] = {
    "mark_dispatch_running": mark_dispatch_running,
    "mark_dispatch_interrupted": mark_dispatch_interrupted,
    "mark_dispatch_resumable": mark_dispatch_resumable,
    "append_dispatch_record": append_dispatch_record,
    "write_captured_values": write_captured_values,
    "reset_blocking_dispatch": reset_blocking_dispatch,
    "update_orchestrator_session_id": update_orchestrator_session_id,
    "upsert_dispatch_record_by_name": upsert_dispatch_record_by_name,
}


# -------------------------------------------------------------------
# 1a. Lock-target consistency contract test (AST scan)
# -------------------------------------------------------------------


class TestFlockLockTarget:
    def test_all_flock_callers_use_lock_sidecar(self) -> None:
        """Every fcntl.flock call in the scan scope must target the .lock sidecar.

        Scans all open() calls (both with-statement and bare-assignment forms)
        and os.open() calls within function bodies. If fcntl.flock appears in the
        same function without the target path containing .lock, it is flagged.
        planner/merge.py is excepted — it intentionally locks data files directly.
        """
        import autoskillit.fleet

        fleet_root = Path(autoskillit.fleet.__file__).parent
        cli_fleet_root = fleet_root.parent / "cli" / "fleet"
        src_root = fleet_root.parent

        FCNTL_ALLOWED_MODULES = {src_root / p for p in _FCNTL_ALLOWED_RELATIVE_PATHS}
        FLOCK_DATA_FILE_EXCEPTIONS = {src_root / "planner" / "merge.py"}

        scan_roots = [fleet_root, cli_fleet_root] + list(FCNTL_ALLOWED_MODULES)

        violations: list[tuple[str, str, int]] = []

        def _is_open_call(call: ast.Call) -> bool:
            return (isinstance(call.func, ast.Attribute) and call.func.attr == "open") or (
                isinstance(call.func, ast.Name) and call.func.id == "open"
            )

        def _is_os_open(call: ast.Call) -> bool:
            return (
                isinstance(call.func, ast.Attribute)
                and call.func.attr == "open"
                and isinstance(call.func.value, ast.Name)
                and call.func.value.id == "os"
            )

        py_files: set[Path] = set()
        for r in scan_roots:
            if r.is_dir():
                py_files.update(r.rglob("*.py"))
            elif r.suffix == ".py":
                py_files.add(r)
        for py_file in py_files:
            try:
                content = py_file.read_text()
                tree = ast.parse(content, filename=str(py_file))
            except SyntaxError:
                continue

            for node in ast.walk(tree):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                if py_file in FLOCK_DATA_FILE_EXCEPTIONS:
                    continue

                open_calls: list[tuple[str, int]] = []
                for child in ast.walk(node):
                    if not isinstance(child, ast.Call):
                        continue
                    if _is_open_call(child) and child.args:
                        open_calls.append((ast.unparse(child.args[0]), child.lineno))
                    elif _is_os_open(child):
                        open_calls.append((ast.unparse(child.args[0]), child.lineno))

                for arg_src, lineno in open_calls:
                    if (
                        ".lock" in arg_src
                        or "with_suffix('.lock')" in arg_src
                        or "lock_path" in arg_src
                    ):
                        continue
                    # Non-.lock file opened — check for fcntl.flock in same function
                    has_flock = any(
                        isinstance(n, ast.Call)
                        and isinstance(n.func, ast.Attribute)
                        and n.func.attr == "flock"
                        and isinstance(n.func.value, ast.Name)
                        and n.func.value.id == "fcntl"
                        for n in ast.walk(node)
                    )
                    if has_flock:
                        violations.append((str(py_file), arg_src, lineno))

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

        with patch("autoskillit.fleet.state.fcntl.flock", side_effect=tracking_flock):
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
            elif fn_name == "reset_blocking_dispatch":
                append_dispatch_record(
                    sp, DispatchRecord(name="d1", status=DispatchStatus.FAILURE)
                )
                flock_calls.clear()
                fn(sp, "d1")  # type: ignore[operator]
            elif fn_name == "update_orchestrator_session_id":
                fn(sp, "sess-123")  # type: ignore[operator]
            elif fn_name == "upsert_dispatch_record_by_name":
                fn(sp, DispatchRecord(name="d1", status=DispatchStatus.SUCCESS))  # type: ignore[operator]

        assert flock_calls, f"{fn_name} did not call fcntl.flock"
        assert any(op == fcntl.LOCK_EX for _, op in flock_calls), (
            f"{fn_name} called flock but not with LOCK_EX"
        )


# -------------------------------------------------------------------
# 1d. Two-write atomicity test
# -------------------------------------------------------------------


class TestAppendAndCaptureAtomic:
    def test_append_and_capture_atomic(self, tmp_path: Path) -> None:
        """CampaignStateMutator serializes concurrent multi-step writes atomically.

        When a caller uses a single CampaignStateMutator block to update both
        dispatch status and captured_values, a concurrent reader must never
        observe the intermediate state (all_success=True with captured_values={}).

        Note: calling append_dispatch_record + write_captured_values as two
        separate public API functions does NOT provide this guarantee — each
        function acquires and releases its own mutator block, leaving a visible
        window between the two writes. Multi-step atomicity requires a shared
        CampaignStateMutator context.
        """
        from autoskillit.fleet import CampaignStateMutator

        sp = tmp_path / "state.json"
        write_initial_state(sp, "cid", "camp", "/m.yaml", [DispatchRecord(name="d1")])

        observed_intermediate: list[bool] = []
        start_barrier = threading.Barrier(2)

        def reader() -> None:
            start_barrier.wait()
            for _ in range(500):
                state = read_state(sp)
                if state is None:
                    continue
                all_success = all(d.status == DispatchStatus.SUCCESS for d in state.dispatches)
                if all_success and not state.captured_values:
                    observed_intermediate.append(True)

        def writer() -> None:
            start_barrier.wait()
            with CampaignStateMutator(sp) as m:
                if m.state is not None:
                    for i, d in enumerate(m.state.dispatches):
                        if d.name == "d1":
                            m.state.dispatches[i] = DispatchRecord(
                                name="d1", status=DispatchStatus.SUCCESS
                            )
                            break
                    m.state.captured_values = {"key": "val"}
                    m.mark_dirty()

        t_write = threading.Thread(target=writer)
        t_read = threading.Thread(target=reader)
        t_write.start()
        t_read.start()
        t_write.join()
        t_read.join()

        assert not observed_intermediate, (
            "Reader observed all_success=True with captured_values={} — "
            "CampaignStateMutator two-write TOCTOU window exists"
        )


# -------------------------------------------------------------------
# 1c. Runtime lock-target path verification
# -------------------------------------------------------------------


class TestFlockTargetPathVerification:
    @pytest.mark.parametrize(
        "fn_name,fn", list(_MUTATION_FUNCTIONS.items()), ids=list(_MUTATION_FUNCTIONS.keys())
    )
    def test_flock_fd_resolves_to_lock_sidecar(
        self, tmp_path: Path, fn_name: str, fn: object
    ) -> None:
        """Every fd passed to fcntl.flock must correspond to a .lock sidecar file.

        Patches builtins.open to record fileno→path mappings, then cross-references
        each fd seen by flock against those mappings to assert the locked file
        is the .lock sidecar, not the state JSON directly.
        """
        import builtins

        sp = tmp_path / "state.json"
        write_initial_state(sp, "cid", "camp", "/m.yaml", [DispatchRecord(name="d1")])

        fd_to_path: dict[int, str] = {}
        original_open = builtins.open

        def tracking_open(*args: object, **kwargs: object) -> object:
            result = original_open(*args, **kwargs)  # type: ignore[arg-type]
            if hasattr(result, "fileno"):
                try:
                    fd = result.fileno()
                    path_arg = args[0] if args else kwargs.get("file", "")
                    fd_to_path[fd] = str(path_arg)
                except Exception:
                    pass
            return result

        flock_calls: list[tuple[int, int]] = []
        original_flock = fcntl.flock

        def tracking_flock(fd: int, op: int) -> None:
            # fcntl.flock is duck-typed: accepts file objects (extracts fileno internally)
            actual_fd = fd.fileno() if hasattr(fd, "fileno") else fd  # type: ignore[union-attr]
            flock_calls.append((actual_fd, op))
            return original_flock(fd, op)

        if fn_name in ("mark_dispatch_interrupted", "mark_dispatch_resumable"):
            import json

            raw = json.loads(sp.read_text())
            raw["dispatches"][0]["status"] = "running"
            sp.write_text(json.dumps(raw))

        with (
            patch.object(builtins, "open", side_effect=tracking_open),
            patch("autoskillit.fleet.state.fcntl.flock", side_effect=tracking_flock),
        ):
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
            elif fn_name == "reset_blocking_dispatch":
                append_dispatch_record(
                    sp, DispatchRecord(name="d1", status=DispatchStatus.FAILURE)
                )
                flock_calls.clear()
                fd_to_path.clear()
                fn(sp, "d1")  # type: ignore[operator]
            elif fn_name == "update_orchestrator_session_id":
                fn(sp, "sess-123")  # type: ignore[operator]
            elif fn_name == "upsert_dispatch_record_by_name":
                fn(sp, DispatchRecord(name="d1", status=DispatchStatus.SUCCESS))  # type: ignore[operator]

        for fd, op in flock_calls:
            if op == fcntl.LOCK_UN:
                continue
            path = fd_to_path.get(fd, "")
            assert path.endswith(".lock"), (
                f"{fn_name}: flock fd={fd} resolves to {path!r}, not a .lock sidecar"
            )
