"""Fleet dispatch owner-scope descendant settlement — real-process coverage.

Drives ``execute_dispatch`` through the real-subprocess ``FleetTestRunner`` and
a real ``DefaultHeadlessExecutor``, substituting only the ``claude`` binary
with a shim that models the real leak topology: it plain-subprocess-spawns a
short-lived intermediate (an MCP-server stand-in) that funnel-spawns a worker
(plus an attached grandchild) and exits immediately, so the worker is
reparented to init/subreaper before the shim's own terminal path runs — it is
never a psutil descendant of the shim, so only owner-scope settlement (never a
generic process-tree kill of the shim) can reach it. A separate, untethered
double-forked daemon is spawned outside the funnel entirely. Every
terminal-write chokepoint is spied so that no worker/grandchild identity is
ever alive at the moment a dispatch outcome is persisted, across the
natural-exit, timeout, idle, and cancellation paths.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import sys
from collections.abc import Generator
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

import anyio
import psutil
import pytest

from autoskillit.core import CLAUDE_CODE_CAPABILITIES
from autoskillit.execution.process import default_tether_dir
from tests.conftest import bind_test_kitchen_identity
from tests.fleet._descendant_worker import (
    is_identity_alive,
    kill_identity_fenced,
    read_identity,
    write_descendant_worker_script,
)
from tests.fleet.test_fleet_e2e import (
    FleetRuntime,
    FleetTestRunner,
    _noop_quota_refresher,
    _simple_prompt_builder,
)

pytestmark = [
    pytest.mark.layer("fleet"),
    pytest.mark.medium,
    pytest.mark.integration,
    pytest.mark.feature("fleet"),
    pytest.mark.skipif(sys.platform != "linux", reason="Linux-only: /proc filesystem required"),
]


# ---------------------------------------------------------------------------
# Intermediate "MCP-server stand-in" — a plain (non-funnel) child of the shim
# that funnel-spawns the worker and exits immediately, so the worker is
# reparented away from the shim's own process tree before the shim reaps this
# intermediate and moves on to its terminal behavior.
# ---------------------------------------------------------------------------

_INTERMEDIATE_SCRIPT = """\
import subprocess

PYTHON = __PYTHON__
WORKER_SCRIPT = __WORKER_SCRIPT__

from autoskillit.execution.process import TetherSpec, spawn_owned_process

spawn_owned_process(
    [PYTHON, WORKER_SCRIPT],
    env=None,
    stdin=subprocess.DEVNULL,
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
    start_new_session=True,
    tether=TetherSpec(origin="test-worker", ceiling_seconds=600.0),
)
"""


# ---------------------------------------------------------------------------
# Claude shim — reaps the short-lived intermediate, double-forks an
# untethered daemon outside the funnel, then behaves per mode.
# ---------------------------------------------------------------------------

_DESCENDANT_SHIM_SCRIPT = """\
#!/usr/bin/env python3
import json
import os
import subprocess
import sys
import time

PYTHON = __PYTHON__
INTERMEDIATE_SCRIPT = __INTERMEDIATE_SCRIPT__

if "--version" in sys.argv:
    print("__CLAUDE_VERSION__ (Claude Code)")
    sys.exit(0)

if sys.executable != PYTHON:
    os.execv(PYTHON, [PYTHON, *sys.argv])

from autoskillit.core import read_boot_id, read_starttime_ticks

dispatch_id = os.environ.get("AUTOSKILLIT_DISPATCH_ID", "unknown")
mode = os.environ["DESCENDANT_SHIM_MODE"]
test_dir = os.environ["DESCENDANT_TEST_DIR"]
os.makedirs(test_dir, exist_ok=True)


def _identity_payload():
    pid = os.getpid()
    return json.dumps(
        {"pid": pid, "boot_id": read_boot_id(), "starttime_ticks": read_starttime_ticks(pid)}
    )


def _publish(path, payload):
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(payload)
    os.replace(tmp, path)


def _detach_stdio():
    devnull = os.open(os.devnull, os.O_RDWR)
    os.dup2(devnull, 0)
    os.dup2(devnull, 1)
    os.dup2(devnull, 2)
    os.close(devnull)


def _heartbeat(seconds=120.0):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        time.sleep(1.0)


def _daemonize_untethered(daemon_path):
    first = os.fork()
    if first > 0:
        os.waitpid(first, 0)
        return
    os.setsid()
    second = os.fork()
    if second > 0:
        os._exit(0)
    _detach_stdio()
    _publish(daemon_path, _identity_payload())
    _heartbeat()
    os._exit(0)


def _wait_for_path(path, timeout=20.0):
    deadline = time.monotonic() + timeout
    while not os.path.exists(path):
        if time.monotonic() >= deadline:
            raise RuntimeError(f"descendant identity file never appeared: {path}")
        time.sleep(0.02)


daemon_identity_path = os.path.join(test_dir, "daemon_identity.json")
worker_identity_path = os.path.join(test_dir, "worker_identity.json")
grandchild_identity_path = os.path.join(test_dir, "grandchild_identity.json")

_daemonize_untethered(daemon_identity_path)
_wait_for_path(daemon_identity_path)

intermediate = subprocess.Popen([PYTHON, INTERMEDIATE_SCRIPT])
intermediate.wait()
_wait_for_path(worker_identity_path)
_wait_for_path(grandchild_identity_path)

if mode == "natural_exit_no_result":
    envelope = {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "result": "Task completed without sentinel.",
        "session_id": f"test-session-{os.getpid()}",
        "errors": [],
        "usage": {"input_tokens": 0, "output_tokens": 0},
    }
    print(json.dumps(envelope), flush=True)
elif mode == "natural_exit_with_result":
    body = json.dumps({"success": True, "reason": ""})
    text = f"---l3-result::{dispatch_id}---\\n{body}\\n---end-l3-result::{dispatch_id}---"
    envelope = {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "result": text,
        "session_id": f"test-session-{os.getpid()}",
        "errors": [],
        "usage": {"input_tokens": 0, "output_tokens": 0},
    }
    print(json.dumps(envelope), flush=True)
elif mode == "block_until_killed":
    _heartbeat()
"""


def _write_descendant_intermediate_script(bin_dir: Path, worker_script: Path) -> Path:
    bin_dir.mkdir(parents=True, exist_ok=True)
    script_path = bin_dir / "_descendant_intermediate.py"
    script = _INTERMEDIATE_SCRIPT.replace("__PYTHON__", repr(sys.executable)).replace(
        "__WORKER_SCRIPT__", repr(str(worker_script))
    )
    script_path.write_text(script, encoding="utf-8")
    return script_path


def _write_descendant_shim(bin_dir: Path) -> Path:
    worker_script = write_descendant_worker_script(bin_dir)
    intermediate_script = _write_descendant_intermediate_script(bin_dir, worker_script)
    shim_path = bin_dir / "claude"
    script = (
        _DESCENDANT_SHIM_SCRIPT.replace("__PYTHON__", repr(sys.executable))
        .replace("__INTERMEDIATE_SCRIPT__", repr(str(intermediate_script)))
        .replace("__CLAUDE_VERSION__", CLAUDE_CODE_CAPABILITIES.min_version)
    )
    shim_path.write_text(script, encoding="utf-8")
    shim_path.chmod(0o755)
    return shim_path


# ---------------------------------------------------------------------------
# Identity + tether inspection helpers
# ---------------------------------------------------------------------------


def _assert_reparented_away_from(
    identity: tuple[int, str, int], shim_pid: int, label: str
) -> None:
    pid = identity[0]
    ppid = psutil.Process(pid).ppid()
    assert ppid != shim_pid, f"{label} (pid {pid}) is still a child of the shim (pid {shim_pid})"


def _alive_descendant_labels(descendant_dir: Path) -> list[str]:
    alive: list[str] = []
    for label, filename in (
        ("worker", "worker_identity.json"),
        ("grandchild", "grandchild_identity.json"),
    ):
        identity = read_identity(descendant_dir / filename)
        if identity is not None and is_identity_alive(identity):
            alive.append(f"{label}:{identity[0]}")
    return alive


def _scoped_tether_tokens(tether_dir: Path) -> list[str]:
    if not tether_dir.is_dir():
        return []
    tokens: list[str] = []
    for path in tether_dir.glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        scope = data.get("owner_scope")
        if scope:
            tokens.append(scope)
    return tokens


async def _wait_for_file(path: Path, *, timeout: float = 15.0) -> None:
    with anyio.fail_after(timeout):
        while not path.is_file():
            await anyio.sleep(0.02)


# ---------------------------------------------------------------------------
# Terminal-write spies
# ---------------------------------------------------------------------------


def _wrap_terminal_write(
    original: Any,
    armed: dict[str, bool],
    descendant_dir: Path,
    violations: list[str],
    label: str,
) -> Any:
    def _spy(*args: Any, **kwargs: Any) -> Any:
        if armed["value"]:
            alive = _alive_descendant_labels(descendant_dir)
            if alive:
                violations.append(f"{label} called while alive: {alive}")
        return original(*args, **kwargs)

    return _spy


def _install_descendant_settlement_spies(
    monkeypatch: pytest.MonkeyPatch, tool_ctx: Any, descendant_dir: Path
) -> list[str]:
    """Arm on entry to dispatch_food_truck; spy every terminal state writer."""
    from autoskillit.fleet.campaign_state import state as _state_mod
    from autoskillit.fleet.dispatch import _classification as _classification_mod

    armed = {"value": False}
    violations: list[str] = []

    original_dispatch_food_truck = tool_ctx.executor.dispatch_food_truck

    async def _armed_dispatch_food_truck(*args: Any, **kwargs: Any) -> Any:
        armed["value"] = True
        return await original_dispatch_food_truck(*args, **kwargs)

    monkeypatch.setattr(tool_ctx.executor, "dispatch_food_truck", _armed_dispatch_food_truck)

    for target_module, name in (
        (_classification_mod, "upsert_dispatch_record_by_name"),
        (_classification_mod, "write_captured_values"),
        (_state_mod, "mark_dispatch_interrupted"),
        (_state_mod, "append_dispatch_record"),
    ):
        original = getattr(target_module, name)
        monkeypatch.setattr(
            target_module,
            name,
            _wrap_terminal_write(original, armed, descendant_dir, violations, name),
        )

    return violations


# ---------------------------------------------------------------------------
# Fixture — replicates tests/fleet/test_fleet_e2e.py's fleet_runtime with the
# descendant-settlement shim in place of its own claude shim.
# ---------------------------------------------------------------------------


@pytest.fixture
def descendant_runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, tool_ctx: Any
) -> Generator[FleetRuntime, None, None]:
    from autoskillit.core import DefaultManagedWorkerCapacity
    from autoskillit.execution.headless import DefaultHeadlessExecutor
    from autoskillit.execution.session import DefaultManagedHeadlessSessionLineageStore
    from tests.fakes import InMemoryRecipeRepository

    monkeypatch.setenv("AUTOSKILLIT_LOG_DIR", str(tmp_path / "autoskillit-log-dir"))

    shim_dir = tmp_path / "bin"
    _write_descendant_shim(shim_dir)
    monkeypatch.setenv("PATH", f"{shim_dir}:{os.environ['PATH']}")

    runner = FleetTestRunner()
    tool_ctx.runner = runner
    tool_ctx.executor = DefaultHeadlessExecutor(tool_ctx)
    tool_ctx.worker_capacity = DefaultManagedWorkerCapacity(max_concurrent=1)
    recipes = InMemoryRecipeRepository()
    tool_ctx.recipes = recipes
    bind_test_kitchen_identity(tool_ctx, kitchen_id=uuid4().hex[:16])
    tool_ctx.project_dir = tmp_path
    tool_ctx.managed_headless_session_lineage_store = DefaultManagedHeadlessSessionLineageStore()

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
    rt.add_recipe("descendant-recipe")

    yield rt

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


def _assert_descendants_settled(descendant_dir: Path) -> None:
    worker_identity = read_identity(descendant_dir / "worker_identity.json")
    grandchild_identity = read_identity(descendant_dir / "grandchild_identity.json")
    assert worker_identity is not None, "worker never recorded its identity"
    assert grandchild_identity is not None, "grandchild never recorded its identity"
    assert not is_identity_alive(worker_identity), "worker survived settlement"
    assert not is_identity_alive(grandchild_identity), "grandchild survived settlement"
    assert not _scoped_tether_tokens(default_tether_dir()), "scoped tether outlived the dispatch"


def _daemon_identity_path(descendant_dir: Path) -> Path:
    return descendant_dir / "daemon_identity.json"


def _assert_daemon_alive(descendant_dir: Path) -> None:
    """The untethered daemon is never registered under the scope, so it must survive."""
    daemon_identity = read_identity(_daemon_identity_path(descendant_dir))
    assert daemon_identity is not None, "untethered daemon never recorded its identity"
    assert is_identity_alive(daemon_identity), "untethered daemon must survive settlement"


def _reap_descendants(descendant_dir: Path) -> None:
    """Identity-fenced cleanup run regardless of test outcome — a red run (no
    settlement) must never leave the worker/grandchild heartbeating past the test."""
    kill_identity_fenced(descendant_dir / "worker_identity.json")
    kill_identity_fenced(descendant_dir / "grandchild_identity.json")
    kill_identity_fenced(_daemon_identity_path(descendant_dir))


# ---------------------------------------------------------------------------
# Terminal paths: natural exit (with/without a result), timeout kill, idle kill
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _TerminalCase:
    case_id: str
    shim_mode: str
    timeout_sec: int | None
    idle_output_timeout: int | None
    expect_success: bool
    expect_reason: str | None


_TERMINAL_CASES = (
    _TerminalCase(
        "natural_exit_no_result",
        "natural_exit_no_result",
        None,
        None,
        False,
        "fleet_l3_no_result_block",
    ),
    _TerminalCase("natural_exit_with_result", "natural_exit_with_result", None, None, True, None),
    # A generous timeout_sec gives the shim's fork/reap sequence headroom to
    # complete under load before FleetTestRunner's TimeoutExpired can fire —
    # the shim itself gates on both identity files existing regardless.
    _TerminalCase("timeout_kill", "block_until_killed", 8, None, False, "fleet_l3_timeout"),
    _TerminalCase("idle_kill", "block_until_killed", 8, 1, False, "fleet_l3_timeout"),
)


@pytest.mark.parametrize("case", _TERMINAL_CASES, ids=lambda c: c.case_id)
@pytest.mark.anyio
async def test_descendant_settlement_terminal_paths(
    case: _TerminalCase,
    descendant_runtime: FleetRuntime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No funnel-spawned worker/grandchild is alive when the outcome is persisted."""
    rt = descendant_runtime
    descendant_dir = rt.tool_ctx.project_dir / f"descendants-{case.case_id}"
    monkeypatch.setenv("DESCENDANT_SHIM_MODE", case.shim_mode)
    monkeypatch.setenv("DESCENDANT_TEST_DIR", str(descendant_dir))

    violations = _install_descendant_settlement_spies(monkeypatch, rt.tool_ctx, descendant_dir)

    from autoskillit.fleet._api import execute_dispatch

    try:
        result = await execute_dispatch(
            tool_ctx=rt.tool_ctx,
            recipe="descendant-recipe",
            task="descendant-settlement",
            ingredients=None,
            dispatch_name=None,
            timeout_sec=case.timeout_sec,
            prompt_builder=_simple_prompt_builder,
            quota_refresher=_noop_quota_refresher,
            idle_output_timeout=case.idle_output_timeout,
        )
        envelope = json.loads(result.outcome.to_envelope())

        assert envelope["success"] is case.expect_success, envelope
        if case.expect_reason is not None:
            assert envelope.get("reason") == case.expect_reason, envelope

        assert not violations, violations
        _assert_descendants_settled(descendant_dir)
        _assert_daemon_alive(descendant_dir)
    finally:
        _reap_descendants(descendant_dir)


# ---------------------------------------------------------------------------
# Terminal path: cancellation mid-dispatch
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_descendant_settlement_cancellation(
    descendant_runtime: FleetRuntime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cancelling execute_dispatch after the worker is up still settles it first."""
    rt = descendant_runtime
    descendant_dir = rt.tool_ctx.project_dir / "descendants-cancellation"
    monkeypatch.setenv("DESCENDANT_SHIM_MODE", "block_until_killed")
    monkeypatch.setenv("DESCENDANT_TEST_DIR", str(descendant_dir))

    violations = _install_descendant_settlement_spies(monkeypatch, rt.tool_ctx, descendant_dir)

    from autoskillit.fleet._api import execute_dispatch

    task = asyncio.create_task(
        execute_dispatch(
            tool_ctx=rt.tool_ctx,
            recipe="descendant-recipe",
            task="descendant-settlement-cancel",
            ingredients=None,
            dispatch_name=None,
            timeout_sec=30,
            prompt_builder=_simple_prompt_builder,
            quota_refresher=_noop_quota_refresher,
        )
    )
    try:
        try:
            await _wait_for_file(descendant_dir / "worker_identity.json")
            await _wait_for_file(_daemon_identity_path(descendant_dir))
            shim_pid = rt.runner.last_pid
            worker_identity = read_identity(descendant_dir / "worker_identity.json")
            daemon_identity = read_identity(_daemon_identity_path(descendant_dir))
            assert worker_identity is not None
            assert daemon_identity is not None
            _assert_reparented_away_from(worker_identity, shim_pid, "worker")
            _assert_reparented_away_from(daemon_identity, shim_pid, "daemon")

            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        finally:
            if not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

        assert not violations, violations
        _assert_descendants_settled(descendant_dir)
        _assert_daemon_alive(descendant_dir)
    finally:
        _reap_descendants(descendant_dir)
