"""Group T18 (#4224): Codex-backend SkillResult flowing through classify_dispatch_outcome.

Exercises the real Codex NDJSON parse path (``_scan_codex_ndjson`` ->
``_adapt_agent_result``) with an incident-shaped stream -- item-level ``error``
items (which ``CodexItemType`` has no member for, so they fall through to
``codex_ndjson_unknown_item_type``) plus a turn-level failure and no final
agent message -- and verifies the resulting ``SkillResult`` classifies the
same way through ``classify_dispatch_outcome`` whether called directly or
through the production ``execute_dispatch`` -> ``run_outcome_classification``
call site.
"""

from __future__ import annotations

import asyncio
import json
import os
import stat
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
import structlog.testing

import autoskillit.fleet.dispatch._api as dispatch_api
from autoskillit.core import (
    DefaultManagedWorkerCapacity,
    DispatchIdentity,
    FleetErrorCode,
    atomic_write,
    release_tracker_lease,
)
from autoskillit.execution.backends import CodexBackend
from autoskillit.execution.headless import DefaultHeadlessExecutor
from autoskillit.fleet._api import execute_dispatch
from autoskillit.fleet._checkpoint_bridge import (
    load_dispatch_progress,
    retain_dispatch_tracker_authority,
)
from autoskillit.fleet._outcome import classify_dispatch_outcome
from autoskillit.fleet.campaign_state import state as campaign_state
from autoskillit.fleet.campaign_state.state import DispatchStatus
from autoskillit.fleet.result_parser import parse_l3_result_block
from autoskillit.fleet.sidecar import sidecar_path
from autoskillit.recipe.schema import Recipe, RecipeInfo, RecipeKind, RecipeSource
from tests.fakes import InMemoryRecipeRepository
from tests.fleet._helpers import _read_dispatch_record
from tests.fleet.test_fleet_e2e_codex import _noop_quota_refresher, _simple_prompt_builder

pytestmark = [
    pytest.mark.layer("fleet"),
    pytest.mark.medium,
    pytest.mark.feature("fleet"),
    pytest.mark.skipif(sys.platform != "linux", reason="Linux-only: /proc filesystem required"),
]

_RECIPE_NAME = "test-codex-incident-recipe"

# Item-level ``error`` items have no CodexItemType member, so they fall through
# to UNKNOWN (codex_ndjson_unknown_item_type). The command_execution item is
# real tool-use evidence -- the same shape production uses to populate
# SkillResult.lifespan_started -- not a substitute for it. No turn.completed
# ever follows, so the accumulator's success flag stays False.
_CODEX_INCIDENT_SHIM = '''\
#!/usr/bin/env python3
"""Codex shim: item-level errors + a turn-level failure, no final message."""
import json
import os
import sys

session_id = "test-codex-incident-" + str(os.getpid())

events = [
    {"type": "thread.started", "thread_id": session_id},
    {
        "type": "item.completed",
        "item": {"type": "command_execution", "command": "echo evidence"},
    },
    {"type": "item.completed", "item": {"type": "error", "message": "tool call failed"}},
    {"type": "item.completed", "item": {"type": "error", "message": "second tool failure"}},
    {"type": "error", "message": "turn failed hard", "code": "E_TURN_FAILED"},
]

for event in events:
    sys.stdout.write(json.dumps(event) + "\\n")
sys.stdout.flush()
sys.exit(1)
'''

# Same early evidence as the incident shim, but stalls past the caller's
# wall-clock timeout instead of ever reaching a turn-level failure or exit --
# the real TIMED_OUT path (not an injected subtype) is what forces
# SkillResult.subtype to "timeout".
_CODEX_STALL_SHIM = '''\
#!/usr/bin/env python3
"""Codex shim: real evidence, then stalls past the caller's timeout."""
import json
import os
import sys
import time

session_id = "test-codex-incident-" + str(os.getpid())

events = [
    {"type": "thread.started", "thread_id": session_id},
    {
        "type": "item.completed",
        "item": {"type": "command_execution", "command": "echo evidence"},
    },
    {"type": "item.completed", "item": {"type": "error", "message": "tool call failed"}},
    {"type": "item.completed", "item": {"type": "error", "message": "second tool failure"}},
]

for event in events:
    sys.stdout.write(json.dumps(event) + "\\n")
sys.stdout.flush()
time.sleep(60)
sys.exit(1)
'''


class _PartialCaptureRunner:
    """SubprocessRunner that preserves whatever the child already flushed on timeout.

    Modeled on tests/fleet/test_fleet_e2e.py's FleetTestRunner, which never
    invokes on_process_spawned/on_process_reaped -- so the Codex backend's
    session-attempt-lease bookkeeping (which requires durable child-reaped
    proof once a spawn is recorded) never engages against a fake shim binary
    that isn't the real Codex CLI. Unlike FleetTestRunner, on
    ``subprocess.TimeoutExpired`` this drains the killed process's already-
    buffered stdout/stderr instead of discarding it -- the TIMED_OUT case
    needs the shim's pre-stall NDJSON evidence to survive the kill.
    """

    def __init__(self) -> None:
        self.last_pid: int = 0

    async def __call__(
        self,
        cmd: list[str],
        *,
        cwd: Any,
        timeout: float,
        env: Any = None,
        pass_fds: tuple[int, ...] = (),
        **kwargs: Any,
    ) -> Any:
        from autoskillit.core.types._type_enums import (
            ChannelConfirmation,
            KillReason,
            TerminationReason,
        )
        from autoskillit.core.types._type_subprocess import SubprocessResult
        from autoskillit.execution import kill_process_tree

        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            cwd=str(cwd),
            pass_fds=pass_fds,
        )
        self.last_pid = proc.pid

        try:
            stdout_b, stderr_b = await asyncio.to_thread(lambda: proc.communicate(timeout=timeout))
        except subprocess.TimeoutExpired:
            await asyncio.to_thread(kill_process_tree, proc.pid)
            stdout_b, stderr_b = await asyncio.to_thread(proc.communicate)
            return SubprocessResult(
                returncode=-9,
                stdout=stdout_b.decode("utf-8", errors="replace"),
                stderr=stderr_b.decode("utf-8", errors="replace"),
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


def _write_shim(bin_dir: Path, script: str) -> Path:
    bin_dir.mkdir(parents=True, exist_ok=True)
    shim_path = bin_dir / "codex"
    atomic_write(shim_path, script)
    shim_path.chmod(shim_path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return shim_path


def _add_recipe(recipes: InMemoryRecipeRepository, name: str) -> None:
    info = RecipeInfo(
        name=name,
        description="test",
        source=RecipeSource.PROJECT,
        path=Path(f"/fake/{name}.yaml"),
    )
    recipes.add_recipe(name, info)
    recipes.add_full_recipe(
        info.path,
        Recipe(name=name, description="test", kind=RecipeKind.STANDARD, ingredients={}),
    )


def _pin_dispatch_id(monkeypatch: pytest.MonkeyPatch) -> str:
    """Fix DispatchIdentity.fresh() so the dispatch id is known before dispatch.

    Mirrors test_dispatch_failure_semantics.py's
    test_single_issue_killed_with_tracker_progress_is_resumable -- the tracker
    file for (b)/(c) must be seeded at the dispatch's own id, which is
    otherwise minted inside execute_dispatch.
    """
    fixed_dispatch_id = str(uuid4())
    fixed_identity = DispatchIdentity.from_dispatch_id(fixed_dispatch_id)

    class _FixedDispatchIdentity:
        @classmethod
        def fresh(cls) -> DispatchIdentity:
            return fixed_identity

    monkeypatch.setattr(campaign_state, "DispatchIdentity", _FixedDispatchIdentity)
    return fixed_dispatch_id


def _seed_tracker(tool_ctx: Any, dispatch_id: str) -> None:
    tracker_dir = tool_ctx.project_dir / ".autoskillit" / "temp" / "pipeline_tracker"
    tracker_dir.mkdir(parents=True, exist_ok=True)
    tracker_file = tracker_dir / f"{dispatch_id}.json"
    tracker_file.write_text(
        json.dumps(
            {
                "pipeline_id": dispatch_id,
                "kitchen_id": tool_ctx.kitchen_id,
                "initialized_at": "2026-06-01T00:00:00Z",
                "steps": {
                    "plan": {"status": "complete", "completed_at": "2026-06-01T00:01:00Z"},
                    "implement": {"status": "pending"},
                },
                "dependencies": {},
            }
        )
    )


def _wire_codex_runtime(
    tool_ctx: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, shim_script: str
) -> None:
    """Wire tool_ctx for a real Codex-backend dispatch (adapted from codex_runtime).

    Uses _PartialCaptureRunner rather than DefaultSubprocessRunner: the real
    subprocess runner invokes on_process_spawned/on_process_reaped, which
    engages the Codex backend's session-attempt-lease bookkeeping (durable
    child-reaped proof) -- machinery the fake shim binary in this test never
    satisfies since it isn't the real Codex CLI. Also unlike
    test_fleet_e2e_codex.py's FleetTestRunner (which shares that
    never-spawned-callback property but discards stdout on
    ``subprocess.TimeoutExpired``), _PartialCaptureRunner preserves whatever
    the child already flushed before being killed, which the TIMED_OUT case
    needs for its session_id/lifespan_started evidence.
    """
    shim_dir = tmp_path / "bin"
    _write_shim(shim_dir, shim_script)
    monkeypatch.setenv("PATH", f"{shim_dir}:{os.environ['PATH']}")

    monkeypatch.setattr(tool_ctx, "backend", CodexBackend())
    tool_ctx.runner = _PartialCaptureRunner()
    tool_ctx.executor = DefaultHeadlessExecutor(tool_ctx)
    tool_ctx.worker_capacity = DefaultManagedWorkerCapacity(max_concurrent=1)
    recipes = InMemoryRecipeRepository()
    tool_ctx.recipes = recipes
    tool_ctx.kitchen_id = uuid4().hex[:16]
    tool_ctx.project_dir = tmp_path
    (tool_ctx.temp_dir / "dispatches").mkdir(parents=True, exist_ok=True)
    _add_recipe(recipes, _RECIPE_NAME)


def _direct_classification(
    tool_ctx: Any, dispatch_id: str, skill_result: Any
) -> tuple[DispatchStatus, str]:
    """Reproduce the production call site (fleet/dispatch/_classification.py:289-295).

    Obtains the checkpoint through the same load_dispatch_progress + tracker
    lease chokepoint production uses, and derives ``parsed`` from the real
    parse_l3_result_block (skipped for the TIMED_OUT case, exactly as
    _materialize_outcome does for subtype == "timeout").
    """
    tracker_key, tracker_lease = retain_dispatch_tracker_authority(tool_ctx, dispatch_id)
    try:
        _sidecar_file, _entries, checkpoint, _tracker_error = load_dispatch_progress(
            tool_ctx=tool_ctx,
            dispatch_sidecar_path=str(sidecar_path(dispatch_id, tool_ctx.project_dir)),
            dispatch_id=dispatch_id,
            backend_name=tool_ctx.backend.name,
            recipe=_RECIPE_NAME,
            tracker_lease=tracker_lease,
        )
    finally:
        with tool_ctx.tracker_leases_lock:
            release_tracker_lease(tool_ctx.tracker_leases, tracker_key)

    parsed = (
        None
        if skill_result.subtype == "timeout"
        else parse_l3_result_block(
            stdout=skill_result.result or "", expected_dispatch_id=dispatch_id
        )
    )
    return classify_dispatch_outcome(
        parsed,
        skill_result,
        sidecar_exists=False,
        checkpoint=checkpoint,
        subtype=skill_result.subtype,
    )


@dataclass(frozen=True)
class _Case:
    case_id: str
    shim: str
    seed_tracker: bool
    timeout_sec: int | None
    expect_status: DispatchStatus
    expect_reason: str
    expect_error_subtype: bool


_CASES = [
    _Case(
        case_id="no_tracker_no_sidecar_is_failure",
        shim=_CODEX_INCIDENT_SHIM,
        seed_tracker=False,
        timeout_sec=None,
        expect_status=DispatchStatus.FAILURE,
        expect_reason=FleetErrorCode.FLEET_L3_NO_RESULT_BLOCK,
        expect_error_subtype=True,
    ),
    _Case(
        case_id="tracker_progress_is_resumable",
        shim=_CODEX_INCIDENT_SHIM,
        seed_tracker=True,
        timeout_sec=None,
        expect_status=DispatchStatus.RESUMABLE,
        expect_reason=FleetErrorCode.FLEET_L3_NO_RESULT_BLOCK,
        expect_error_subtype=True,
    ),
    _Case(
        case_id="timed_out_with_tracker_is_resumable",
        shim=_CODEX_STALL_SHIM,
        seed_tracker=True,
        timeout_sec=10,
        expect_status=DispatchStatus.RESUMABLE,
        expect_reason=FleetErrorCode.FLEET_L3_TIMEOUT,
        expect_error_subtype=False,
    ),
]


@pytest.mark.anyio
@pytest.mark.parametrize("case", _CASES, ids=lambda c: c.case_id)
async def test_codex_backend_skill_result_through_classify_dispatch_outcome(
    case: _Case, tool_ctx: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dispatch_id = _pin_dispatch_id(monkeypatch)
    _wire_codex_runtime(tool_ctx, tmp_path, monkeypatch, case.shim)
    if case.seed_tracker:
        _seed_tracker(tool_ctx, dispatch_id)

    captured: dict[str, Any] = {}
    real_run_outcome_classification = dispatch_api.run_outcome_classification

    async def _capturing_run_outcome_classification(**kwargs: Any) -> Any:
        captured["skill_result"] = kwargs["skill_result"]
        return await real_run_outcome_classification(**kwargs)

    monkeypatch.setattr(
        dispatch_api, "run_outcome_classification", _capturing_run_outcome_classification
    )

    with structlog.testing.capture_logs() as cap_logs:
        dispatch_result = await execute_dispatch(
            tool_ctx=tool_ctx,
            recipe=_RECIPE_NAME,
            task="codex incident dispatch",
            ingredients=None,
            dispatch_name=None,
            timeout_sec=case.timeout_sec,
            prompt_builder=_simple_prompt_builder,
            quota_refresher=_noop_quota_refresher,
        )
    envelope = json.loads(dispatch_result.outcome.to_envelope())
    assert envelope["dispatch_id"] == dispatch_id

    skill_result = captured.get("skill_result")
    assert skill_result is not None, "run_outcome_classification was never invoked"
    assert skill_result.session_id, "codex shim did not surface a session id"
    assert skill_result.lifespan_started, "command_execution evidence did not register"
    assert any(
        log["event"] == "codex_ndjson_unknown_item_type" and log["log_level"] == "warning"
        for log in cap_logs
    )
    if case.expect_error_subtype:
        assert skill_result.cli_subtype == "error_during_execution"
    else:
        assert skill_result.subtype == "timeout"

    direct_status, direct_reason = _direct_classification(tool_ctx, dispatch_id, skill_result)
    assert (direct_status, direct_reason) == (case.expect_status, case.expect_reason)

    record = _read_dispatch_record(tool_ctx)
    assert record["status"] == case.expect_status.value
    assert record["reason"] == case.expect_reason
