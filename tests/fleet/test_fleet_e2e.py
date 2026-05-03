"""Fleet Group O: end-to-end test suite for fleet dispatch loop.

Exercises the full execute_dispatch → DefaultHeadlessExecutor.dispatch_food_truck
→ build_food_truck_cmd → _execute_claude_headless pipeline, substituting only
the claude binary with a parameterized Python shim.
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import subprocess
import threading
import time
from collections.abc import Generator
from pathlib import Path
from typing import Any
from uuid import uuid4

import anyio
import psutil
import pytest

pytestmark = [
    pytest.mark.layer("fleet"),
    pytest.mark.medium,
    pytest.mark.integration,
    pytest.mark.feature("fleet"),
]


# ---------------------------------------------------------------------------
# Claude shim script
# ---------------------------------------------------------------------------

_SHIM_SCRIPT = """\
#!/usr/bin/env python3
\"\"\"Parameterized claude shim for fleet E2E tests.\"\"\"
import json
import os
import sys
import time

dispatch_id = os.environ.get("AUTOSKILLIT_DISPATCH_ID", "unknown")
mode = os.environ.get("CLAUDE_SHIM_MODE", "success")
sleep_sec = float(os.environ.get("CLAUDE_SHIM_SLEEP_SEC", "10"))


def _sentinel(payload: str) -> str:
    return (
        f"---l2-result::{dispatch_id}---\\n"
        f"{payload}\\n"
        f"---end-l2-result::{dispatch_id}---"
    )


if mode == "exit_nonzero":
    sys.exit(1)
elif mode == "success":
    text = _sentinel('{"success": true, "reason": ""}')
elif mode == "malformed_sentinel":
    text = _sentinel("NOT VALID JSON")
elif mode == "no_sentinel":
    text = "Task completed without sentinel."
elif mode == "sleep_then_exit":
    time.sleep(sleep_sec)
    text = _sentinel('{"success": true, "reason": ""}')
else:
    text = ""

envelope = {
    "type": "result",
    "subtype": "success",
    "is_error": False,
    "result": text,
    "session_id": "test-session-id",
    "errors": [],
    "usage": {"input_tokens": 0, "output_tokens": 0},
}
print(json.dumps(envelope), flush=True)
"""


def _write_claude_shim(bin_dir: Path) -> Path:
    """Write a Python shim to bin_dir/claude and make it executable."""
    bin_dir.mkdir(parents=True, exist_ok=True)
    shim_path = bin_dir / "claude"
    shim_path.write_text(_SHIM_SCRIPT, encoding="utf-8")
    shim_path.chmod(0o755)
    return shim_path


# ---------------------------------------------------------------------------
# Stub callables (module-level, not fixtures)
# ---------------------------------------------------------------------------


def _simple_prompt_builder(**kwargs: Any) -> str:
    return f"dispatch {kwargs.get('recipe', 'unknown')} for {kwargs.get('task', 'test')}"


async def _no_sleep_quota_checker(config: Any, **kwargs: Any) -> dict[str, Any]:
    return {
        "should_sleep": False,
        "sleep_seconds": 0,
        "utilization": None,
        "resets_at": None,
        "window_name": None,
    }


async def _noop_quota_refresher(config: Any, **kwargs: Any) -> None:
    pass


# ---------------------------------------------------------------------------
# FleetTestRunner
# ---------------------------------------------------------------------------


class FleetTestRunner:
    """SubprocessRunner-conforming runner that spawns real subprocesses.

    Used by FleetRuntime to exercise the full dispatch pipeline with
    the claude shim binary, avoiding any mocking of the headless executor.
    """

    def __init__(self) -> None:
        self.call_count: int = 0
        self.last_pid: int = 0

    async def __call__(
        self,
        cmd: list[str],
        *,
        cwd: Any,
        timeout: float,
        env: Any = None,
        on_pid_resolved: Any = None,
        **kwargs: Any,
    ) -> Any:
        from autoskillit.core.runtime._linux_proc import read_starttime_ticks
        from autoskillit.core.types._type_enums import (
            ChannelConfirmation,
            KillReason,
            TerminationReason,
        )
        from autoskillit.core.types._type_subprocess import SubprocessResult
        from autoskillit.execution import kill_process_tree

        self.call_count += 1
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            cwd=str(cwd),
        )
        self.last_pid = proc.pid
        if on_pid_resolved is not None:
            ticks = read_starttime_ticks(proc.pid) or 0
            on_pid_resolved(proc.pid, ticks)

        try:
            stdout_b, stderr_b = await asyncio.to_thread(lambda: proc.communicate(timeout=timeout))
        except subprocess.TimeoutExpired:
            await asyncio.to_thread(kill_process_tree, proc.pid)
            await asyncio.to_thread(proc.wait)
            return SubprocessResult(
                returncode=-9,
                stdout="",
                stderr="",
                termination=TerminationReason.TIMED_OUT,
                pid=proc.pid,
                channel_confirmation=ChannelConfirmation.UNMONITORED,
                kill_reason=KillReason.INFRA_KILL,
            )

        return SubprocessResult(
            returncode=proc.returncode,
            stdout=stdout_b.decode("utf-8", errors="replace"),
            stderr=stderr_b.decode("utf-8", errors="replace"),
            termination=TerminationReason.NATURAL_EXIT,
            pid=proc.pid,
            channel_confirmation=ChannelConfirmation.UNMONITORED,
            kill_reason=KillReason.NATURAL_EXIT,
        )


# ---------------------------------------------------------------------------
# FleetRuntime helper
# ---------------------------------------------------------------------------


class FleetRuntime:
    """Test harness for end-to-end fleet dispatch tests.

    Wires FleetTestRunner into ToolContext, provides helper methods
    for configuring shim behavior and reading per-dispatch state files.
    """

    def __init__(
        self,
        tool_ctx: Any,
        dispatches_dir: Path,
        shim_dir: Path,
        runner: FleetTestRunner,
        recipes: Any,
        monkeypatch: Any,
    ) -> None:
        self.tool_ctx = tool_ctx
        self.dispatches_dir = dispatches_dir
        self.shim_dir = shim_dir
        self.runner = runner
        self.recipes = recipes
        self._monkeypatch = monkeypatch

    def configure_shim(self, mode: str, sleep_sec: float | None = None) -> None:
        """Set CLAUDE_SHIM_MODE (and optionally CLAUDE_SHIM_SLEEP_SEC) for next dispatch."""
        self._monkeypatch.setenv("CLAUDE_SHIM_MODE", mode)
        if sleep_sec is not None:
            self._monkeypatch.setenv("CLAUDE_SHIM_SLEEP_SEC", str(sleep_sec))

    def add_recipe(self, name: str) -> None:
        """Register a minimal standard recipe."""
        from autoskillit.recipe.schema import Recipe, RecipeInfo, RecipeKind, RecipeSource

        info = RecipeInfo(
            name=name,
            description="test",
            source=RecipeSource.PROJECT,
            path=Path(f"/fake/{name}.yaml"),
        )
        self.recipes.add_recipe(name, info)
        self.recipes.add_full_recipe(
            info.path,
            Recipe(name=name, description="test", kind=RecipeKind.STANDARD, ingredients={}),
        )

    async def dispatch(
        self,
        recipe: str,
        task: str = "test-task",
        *,
        ingredients: dict[str, str] | None = None,
        dispatch_name: str | None = None,
        timeout_sec: int | None = None,
        shim_mode: str = "success",
        sleep_sec: float | None = None,
        quota_checker: Any = None,
    ) -> dict[str, Any]:
        """Run execute_dispatch and return the parsed JSON envelope."""
        from autoskillit.fleet._api import execute_dispatch

        self.configure_shim(shim_mode, sleep_sec=sleep_sec)
        raw = await execute_dispatch(
            tool_ctx=self.tool_ctx,
            recipe=recipe,
            task=task,
            ingredients=ingredients,  # type: ignore[arg-type]
            dispatch_name=dispatch_name,
            timeout_sec=timeout_sec,
            prompt_builder=_simple_prompt_builder,
            quota_checker=quota_checker if quota_checker is not None else _no_sleep_quota_checker,
            quota_refresher=_noop_quota_refresher,
        )
        return json.loads(raw)  # type: ignore[no-any-return]

    def dispatch_state_path(self, dispatch_id: str) -> Path:
        """Path to per-dispatch state file created by execute_dispatch."""
        return self.dispatches_dir / f"{dispatch_id}.json"

    def read_dispatch_state(self, dispatch_id: str) -> Any:
        """Read per-dispatch CampaignState (or None if missing/corrupt)."""
        from autoskillit.fleet.state import read_state

        return read_state(self.dispatch_state_path(dispatch_id))


# ---------------------------------------------------------------------------
# State manipulation helpers (bypass transition validation)
# ---------------------------------------------------------------------------


def _force_running_state(
    state_path: Path,
    name: str,
    pid: int,
    ticks: int,
    boot_id: str,
) -> None:
    """Directly set a dispatch to RUNNING with PID identity fields in the JSON."""
    data = json.loads(state_path.read_text(encoding="utf-8"))
    for d in data["dispatches"]:
        if d["name"] == name:
            d["status"] = "running"
            d["l2_pid"] = pid
            d["l2_starttime_ticks"] = ticks
            d["l2_boot_id"] = boot_id
            d["started_at"] = time.time()
            break
    state_path.write_text(json.dumps(data), encoding="utf-8")


def _force_state_statuses(state_path: Path, overrides: dict[str, str]) -> None:
    """Bulk override dispatch statuses, bypassing transition validation."""
    data = json.loads(state_path.read_text(encoding="utf-8"))
    for d in data["dispatches"]:
        if d["name"] in overrides:
            d["status"] = overrides[d["name"]]
    state_path.write_text(json.dumps(data), encoding="utf-8")


# ---------------------------------------------------------------------------
# fleet_runtime fixture (local — avoids conflict with test_pack_enforcement_e2e.py)
# ---------------------------------------------------------------------------


@pytest.fixture
def fleet_runtime(
    tmp_path: Path, monkeypatch: Any, tool_ctx: Any
) -> Generator[FleetRuntime, None, None]:
    from autoskillit.execution.headless import DefaultHeadlessExecutor
    from tests.fakes import InMemoryRecipeRepository

    shim_dir = tmp_path / "bin"
    _write_claude_shim(shim_dir)
    monkeypatch.setenv("PATH", f"{shim_dir}:{os.environ['PATH']}")

    from autoskillit.fleet import FleetSemaphore

    runner = FleetTestRunner()
    tool_ctx.runner = runner
    tool_ctx.executor = DefaultHeadlessExecutor(tool_ctx)
    tool_ctx.fleet_lock = FleetSemaphore(max_concurrent=1)
    recipes = InMemoryRecipeRepository()
    tool_ctx.recipes = recipes
    tool_ctx.kitchen_id = uuid4().hex[:16]
    tool_ctx.project_dir = tmp_path

    dispatches_dir = tool_ctx.temp_dir / "dispatches"
    dispatches_dir.mkdir(parents=True, exist_ok=True)

    pre_children = {c.pid for c in psutil.Process(os.getpid()).children(recursive=True)}

    rt = FleetRuntime(
        tool_ctx=tool_ctx,
        dispatches_dir=dispatches_dir,
        shim_dir=shim_dir,
        runner=runner,
        recipes=recipes,
        monkeypatch=monkeypatch,
    )

    yield rt

    # Process leak detection
    post_children = psutil.Process(os.getpid()).children(recursive=True)
    leaked = []
    for c in post_children:
        if c.pid not in pre_children:
            try:
                if c.is_running() and c.status() not in (
                    psutil.STATUS_ZOMBIE,
                    psutil.STATUS_DEAD,
                ):
                    leaked.append(c)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
    for c in leaked:
        try:
            c.kill()
            c.wait(timeout=2)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    assert not leaked, f"Test leaked processes: {[c.pid for c in leaked]}"


@pytest.fixture
def fleet_runtime_factory(fleet_runtime: FleetRuntime):
    """Factory variant of fleet_runtime that accepts max_concurrent_dispatches."""

    def _factory(max_concurrent_dispatches: int = 1) -> FleetRuntime:
        from autoskillit.fleet import FleetSemaphore

        fleet_runtime.tool_ctx.fleet_lock = FleetSemaphore(
            max_concurrent=max_concurrent_dispatches
        )
        return fleet_runtime

    return _factory


# ---------------------------------------------------------------------------
# Tests 1–3, 5, 9: dispatch-pipeline (happy path, failure, continue-on-failure)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_two_dispatch_happy_path(fleet_runtime: FleetRuntime) -> None:
    """Full dispatch pipeline runs twice and both succeed."""
    rt = fleet_runtime
    rt.add_recipe("recipe-a")
    rt.add_recipe("recipe-b")

    result_a = await rt.dispatch("recipe-a", shim_mode="success")
    assert result_a["success"] is True

    result_b = await rt.dispatch("recipe-b", shim_mode="success")
    assert result_b["success"] is True

    for result in [result_a, result_b]:
        state = rt.read_dispatch_state(result["dispatch_id"])
        assert state is not None
        d = state.dispatches[0]
        from autoskillit.fleet.state import DispatchStatus

        assert d.status == DispatchStatus.SUCCESS
        assert d.l2_pid > 0
        assert d.ended_at > d.started_at


@pytest.mark.anyio
async def test_halt_on_first_failure_default(fleet_runtime: FleetRuntime) -> None:
    """Failure detection + campaign halt with continue_on_failure=False."""
    from autoskillit.fleet.state import DispatchStatus, resume_campaign_from_state

    rt = fleet_runtime
    rt.add_recipe("recipe-a")

    result = await rt.dispatch("recipe-a", shim_mode="exit_nonzero")
    assert result["success"] is False

    state_path = rt.dispatch_state_path(result["dispatch_id"])
    decision = resume_campaign_from_state(state_path, continue_on_failure=False)
    assert decision is not None
    assert decision.next_dispatch_name == ""
    assert decision.completed_dispatches_block == "fleet_halted_on_failure"

    state = rt.read_dispatch_state(result["dispatch_id"])
    assert state is not None
    assert state.dispatches[0].status == DispatchStatus.FAILURE


@pytest.mark.anyio
async def test_continue_on_failure_when_flagged(fleet_runtime: FleetRuntime) -> None:
    """continue_on_failure=True does not halt; FAILURE dispatches excluded from next selection."""
    from autoskillit.fleet.state import (
        FLEET_HALTED_SENTINEL,
        DispatchStatus,
        resume_campaign_from_state,
    )

    rt = fleet_runtime
    rt.add_recipe("recipe-a")
    rt.add_recipe("recipe-b")

    result_a = await rt.dispatch("recipe-a", shim_mode="no_sentinel")
    assert result_a["success"] is False
    assert result_a["reason"] == "fleet_l2_no_result_block"

    state_path = rt.dispatch_state_path(result_a["dispatch_id"])
    decision = resume_campaign_from_state(state_path, continue_on_failure=True)
    assert decision is not None
    # Campaign did not halt (FLEET_HALTED_SENTINEL not emitted)
    assert decision.completed_dispatches_block != FLEET_HALTED_SENTINEL
    # FAILURE dispatches are excluded from next_dispatch_name selection;
    # with only one failed dispatch and no pending ones, next_name is empty.
    assert decision.next_dispatch_name == ""

    result_b = await rt.dispatch("recipe-b", shim_mode="success")
    assert result_b["success"] is True

    state_b = rt.read_dispatch_state(result_b["dispatch_id"])
    assert state_b is not None
    assert state_b.dispatches[0].status == DispatchStatus.SUCCESS


@pytest.mark.anyio
async def test_malformed_l2_result_surfaces_warning(fleet_runtime: FleetRuntime) -> None:
    """Malformed sentinel body produces l2_parse_failed failure with diagnostic fields."""
    from autoskillit.fleet.state import DispatchStatus

    rt = fleet_runtime
    rt.add_recipe("recipe-a")

    result = await rt.dispatch("recipe-a", shim_mode="malformed_sentinel")
    assert result["success"] is False
    assert result["reason"] == "fleet_l2_parse_failed"
    assert "l2_raw_body" in result
    assert "l2_parse_error" in result

    state = rt.read_dispatch_state(result["dispatch_id"])
    assert state is not None
    assert state.dispatches[0].status == DispatchStatus.FAILURE
    assert state.dispatches[0].reason == "fleet_l2_parse_failed"


@pytest.mark.anyio
async def test_l3_halts_on_missing_result_block_when_continue_on_failure_false(
    fleet_runtime: FleetRuntime,
) -> None:
    """No-sentinel failure + continue_on_failure=False yields fleet_halted_on_failure."""
    from autoskillit.fleet.state import DispatchStatus, resume_campaign_from_state

    rt = fleet_runtime
    rt.add_recipe("recipe-a")

    result = await rt.dispatch("recipe-a", shim_mode="no_sentinel")
    assert result["success"] is False
    assert result["reason"] == "fleet_l2_no_result_block"

    state = rt.read_dispatch_state(result["dispatch_id"])
    assert state is not None
    assert state.dispatches[0].status == DispatchStatus.FAILURE
    assert state.dispatches[0].reason == "fleet_l2_no_result_block"

    state_path = rt.dispatch_state_path(result["dispatch_id"])
    decision = resume_campaign_from_state(state_path, continue_on_failure=False)
    assert decision is not None
    assert decision.next_dispatch_name == ""
    assert decision.completed_dispatches_block == "fleet_halted_on_failure"


# ---------------------------------------------------------------------------
# Tests 4, 10, 12, 13: concurrency and validation
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_parallel_dispatch_refused_mid_campaign(
    fleet_runtime: FleetRuntime,
) -> None:
    """Lock guard rejects second concurrent dispatch with fleet_parallel_refused."""
    rt = fleet_runtime
    rt.add_recipe("slow-recipe")

    results: list[dict[str, Any] | None] = [None, None]

    async def _first() -> None:
        results[0] = await rt.dispatch(
            "slow-recipe",
            shim_mode="sleep_then_exit",
            sleep_sec=2.0,
            timeout_sec=10,
        )

    async def _second() -> None:
        await anyio.sleep(0.3)
        results[1] = await rt.dispatch("slow-recipe", shim_mode="success")

    async with anyio.create_task_group() as tg:
        tg.start_soon(_first)
        tg.start_soon(_second)

    assert results[0] is not None
    assert results[0]["success"] is True

    assert results[1] is not None
    assert results[1]["error"] == "fleet_parallel_refused"
    assert results[1]["success"] is False


@pytest.mark.anyio
async def test_state_json_atomic_under_concurrent_read(
    fleet_runtime: FleetRuntime, tmp_path: Path
) -> None:
    """atomic_write guarantees readers never observe corrupted partial JSON."""
    from autoskillit.fleet.state import DispatchRecord, write_initial_state

    state_path = tmp_path / "atomic-test-state.json"
    write_initial_state(state_path, "cid-0", "cn", str(state_path), [DispatchRecord(name="d0")])

    json_errors: list[Exception] = []
    stop_event = threading.Event()

    def _writer() -> None:
        i = 0
        while not stop_event.is_set():
            dispatches = [DispatchRecord(name=f"d{i % 5}")]
            write_initial_state(state_path, f"cid-{i % 100}", "cn", str(state_path), dispatches)
            i += 1

    writer_thread = threading.Thread(target=_writer, daemon=True)
    writer_thread.start()

    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        try:
            json.loads(state_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            json_errors.append(exc)

    stop_event.set()
    writer_thread.join(timeout=5.0)

    assert not json_errors, f"JSONDecodeError under concurrent writes: {json_errors[:3]}"


@pytest.mark.anyio
async def test_ingredient_type_validation(fleet_runtime: FleetRuntime) -> None:
    """Non-string ingredient values are rejected before any subprocess is spawned."""
    rt = fleet_runtime
    rt.add_recipe("recipe-a")

    result = await rt.dispatch("recipe-a", ingredients={"key": 123})  # type: ignore[arg-type]
    assert result["success"] is False
    assert result["error"] == "fleet_unknown_ingredient"
    assert rt.runner.call_count == 0


@pytest.mark.anyio
async def test_quota_exhausted_mid_campaign_sleeps_and_retries_once(
    fleet_runtime: FleetRuntime,
) -> None:
    """Quota sleep is honored before dispatch proceeds to completion."""
    rt = fleet_runtime
    rt.add_recipe("recipe-a")

    call_count = [0]

    async def _stateful_quota_checker(config: Any, **kwargs: Any) -> dict[str, Any]:
        call_count[0] += 1
        if call_count[0] == 1:
            return {
                "should_sleep": True,
                "sleep_seconds": 0.1,
                "utilization": None,
                "resets_at": None,
                "window_name": None,
            }
        return {
            "should_sleep": False,
            "sleep_seconds": 0,
            "utilization": None,
            "resets_at": None,
            "window_name": None,
        }

    t0 = time.monotonic()
    result = await rt.dispatch(
        "recipe-a",
        shim_mode="success",
        quota_checker=_stateful_quota_checker,
    )
    elapsed = time.monotonic() - t0

    assert result["success"] is True
    assert elapsed >= 0.1, f"Expected quota sleep ≥ 0.1s, got {elapsed:.3f}s"
    assert call_count[0] >= 1


# ---------------------------------------------------------------------------
# Tests 6, 8, 11: process lifecycle
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_l2_killed_mid_dispatch_records_failure(
    fleet_runtime: FleetRuntime,
) -> None:
    """L2 process killed mid-dispatch produces l2_no_result_block failure (not crash)."""
    from autoskillit.fleet.state import DispatchStatus

    rt = fleet_runtime
    rt.add_recipe("sleepy-recipe")

    dispatch_result: dict[str, Any] | None = None

    async def _dispatch() -> None:
        nonlocal dispatch_result
        dispatch_result = await rt.dispatch(
            "sleepy-recipe",
            shim_mode="sleep_then_exit",
            sleep_sec=30,
            timeout_sec=60,
        )

    async def _killer() -> None:
        await anyio.sleep(0.5)
        pid = rt.runner.last_pid
        if pid > 0:
            os.kill(pid, signal.SIGKILL)

    async with anyio.create_task_group() as tg:
        tg.start_soon(_dispatch)
        tg.start_soon(_killer)

    assert dispatch_result is not None
    assert dispatch_result["success"] is False
    assert dispatch_result["reason"] == "fleet_l2_no_result_block"

    state = rt.read_dispatch_state(dispatch_result["dispatch_id"])
    assert state is not None
    assert state.dispatches[0].status == DispatchStatus.FAILURE
    assert state.dispatches[0].reason == "fleet_l2_no_result_block"

    killed_pid = rt.runner.last_pid
    assert killed_pid > 0
    assert not psutil.pid_exists(killed_pid) or _is_zombie(killed_pid)


def _is_zombie(pid: int) -> bool:
    """Return True if the process is a zombie (already dead, reap pending)."""
    try:
        return psutil.Process(pid).status() == psutil.STATUS_ZOMBIE
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return True


@pytest.mark.anyio
async def test_orphan_l2_reaping(fleet_runtime: FleetRuntime, tmp_path: Path) -> None:
    """_reap_stale_dispatches kills a real orphan process and marks it interrupted."""
    from autoskillit.cli._fleet import _reap_stale_dispatches
    from autoskillit.core.runtime._linux_proc import read_boot_id, read_starttime_ticks
    from autoskillit.fleet.state import (
        DispatchRecord,
        DispatchStatus,
        read_state,
        write_initial_state,
    )

    orphan = subprocess.Popen(["sleep", "999"])
    orphan_pid = orphan.pid
    orphan_ticks = read_starttime_ticks(orphan_pid) or 0
    boot_id = read_boot_id() or ""

    try:
        state_path = tmp_path / "orphan-test-state.json"
        write_initial_state(
            state_path,
            "test-campaign",
            "test",
            str(state_path),
            [DispatchRecord(name="orphaned")],
        )
        _force_running_state(state_path, "orphaned", orphan_pid, orphan_ticks, boot_id)

        _reap_stale_dispatches(state_path, dry_run=False)

        assert not psutil.pid_exists(orphan_pid) or _is_zombie(orphan_pid)

        state = read_state(state_path)
        assert state is not None
        d = next(d for d in state.dispatches if d.name == "orphaned")
        assert d.status == DispatchStatus.INTERRUPTED
        assert d.reason == "reaped_orphan"
    finally:
        if orphan.poll() is None:
            orphan.kill()
            orphan.wait()


@pytest.mark.anyio
async def test_l2_timeout_enforced(fleet_runtime: FleetRuntime) -> None:
    """timeout_sec=1 kills a sleeping L2 process and returns l2_timeout fleet_error."""
    from autoskillit.fleet.state import DispatchStatus

    rt = fleet_runtime
    rt.add_recipe("slow-recipe")

    result = await rt.dispatch(
        "slow-recipe",
        shim_mode="sleep_then_exit",
        sleep_sec=10,
        timeout_sec=1,
    )

    assert result["success"] is False
    assert result["error"] == "fleet_l2_timeout"
    assert "details" in result
    assert "dispatch_id" in result["details"]
    assert "l2_session_id" in result["details"]

    state = rt.read_dispatch_state(result["details"]["dispatch_id"])
    assert state is not None
    assert state.dispatches[0].status == DispatchStatus.FAILURE
    assert state.dispatches[0].reason == "fleet_l2_timeout"

    killed_pid = rt.runner.last_pid
    assert killed_pid > 0
    assert not psutil.pid_exists(killed_pid) or _is_zombie(killed_pid)


# ---------------------------------------------------------------------------
# Tests 7, 14, 15: state and manifest edge cases
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_resume_after_l3_crash(fleet_runtime: FleetRuntime, tmp_path: Path) -> None:
    """resume_campaign_from_state marks stale RUNNING as interrupted and returns next pending."""
    from autoskillit.fleet.state import (
        DispatchRecord,
        DispatchStatus,
        read_state,
        resume_campaign_from_state,
        write_initial_state,
    )

    state_path = tmp_path / "crash-test-state.json"
    dispatches = [
        DispatchRecord(name="completed-one"),
        DispatchRecord(name="crashed-one"),
        DispatchRecord(name="pending-one"),
    ]
    write_initial_state(
        state_path, "test-campaign-id", "test-campaign", str(state_path), dispatches
    )
    _force_state_statuses(state_path, {"completed-one": "success", "crashed-one": "running"})

    decision = resume_campaign_from_state(state_path, continue_on_failure=True)

    assert decision is not None
    assert decision.next_dispatch_name == "pending-one"

    state = read_state(state_path)
    assert state is not None
    crashed = next(d for d in state.dispatches if d.name == "crashed-one")
    assert crashed.status == DispatchStatus.INTERRUPTED
    assert crashed.reason == "stale_running_on_resume"


@pytest.mark.anyio
async def test_manifest_corrupted_yaml(fleet_runtime: FleetRuntime) -> None:
    """Recipe with wrong kind returns fleet_invalid_recipe_kind without spawning."""
    from autoskillit.recipe.schema import Recipe, RecipeInfo, RecipeKind, RecipeSource

    rt = fleet_runtime
    recipe_info = RecipeInfo(
        name="bad-recipe",
        description="bad",
        source=RecipeSource.PROJECT,
        path=Path("/fake/bad-recipe.yaml"),
    )
    rt.recipes.add_recipe("bad-recipe", recipe_info)
    rt.recipes.add_full_recipe(
        recipe_info.path,
        Recipe(name="bad-recipe", description="bad", kind=RecipeKind.CAMPAIGN, ingredients={}),
    )

    result = await rt.dispatch("bad-recipe")
    assert result["success"] is False
    assert result["error"] == "fleet_invalid_recipe_kind"
    assert rt.runner.call_count == 0


@pytest.mark.anyio
async def test_manifest_mid_campaign_deletion(fleet_runtime: FleetRuntime, tmp_path: Path) -> None:
    """resume_campaign_from_state returns None when state file is missing."""
    from autoskillit.fleet.state import (
        DispatchRecord,
        resume_campaign_from_state,
        write_initial_state,
    )

    state_path = tmp_path / "missing-state.json"
    write_initial_state(
        state_path,
        "test-campaign",
        "test",
        str(state_path),
        [DispatchRecord(name="d1")],
    )
    state_path.unlink()

    decision = resume_campaign_from_state(state_path, continue_on_failure=True)
    assert decision is None


# ---------------------------------------------------------------------------
# Parallel dispatch tests — FleetSemaphore with max > 1
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_two_concurrent_dispatches_allowed_with_max2(
    fleet_runtime_factory,
) -> None:
    """FleetSemaphore(max=2) allows both L2 dispatches to complete successfully."""
    rt = fleet_runtime_factory(max_concurrent_dispatches=2)
    rt.add_recipe("slow-recipe")
    results: list[dict | None] = [None, None]

    async def _dispatch(idx: int) -> None:
        results[idx] = await rt.dispatch(
            "slow-recipe", shim_mode="sleep_then_exit", sleep_sec=1.0, timeout_sec=10
        )

    async with anyio.create_task_group() as tg:
        tg.start_soon(_dispatch, 0)
        tg.start_soon(_dispatch, 1)

    assert results[0] is not None and results[0]["success"] is True
    assert results[1] is not None and results[1]["success"] is True


@pytest.mark.anyio
async def test_third_concurrent_dispatch_refused_with_max2(
    fleet_runtime_factory,
) -> None:
    """FleetSemaphore(max=2) rejects a third concurrent dispatch immediately."""
    rt = fleet_runtime_factory(max_concurrent_dispatches=2)
    rt.add_recipe("slow-recipe")
    results: list[dict | None] = [None, None, None]

    async def _slow(idx: int) -> None:
        results[idx] = await rt.dispatch(
            "slow-recipe", shim_mode="sleep_then_exit", sleep_sec=3.0, timeout_sec=15
        )

    async def _fast() -> None:
        await anyio.sleep(0.3)
        results[2] = await rt.dispatch("slow-recipe", shim_mode="success")

    async with anyio.create_task_group() as tg:
        tg.start_soon(_slow, 0)
        tg.start_soon(_slow, 1)
        tg.start_soon(_fast)

    assert results[0] is not None and results[0]["success"] is True
    assert results[1] is not None and results[1]["success"] is True
    assert results[2] is not None
    assert results[2]["error"] == "fleet_parallel_refused"
    assert results[2]["success"] is False
