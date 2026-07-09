"""Fleet Group O-codex: end-to-end codex-backend dispatch.

Exercises the execute_dispatch -> DefaultHeadlessExecutor -> build_food_truck_cmd
->_execute_claude_headless pipeline with a codex shim binary that outputs
Codex-format NDJSON, asserting success adjudication through the
_scan_codex_ndjson -> _adapt_agent_result -> parse_l3_result_block path.
"""

from __future__ import annotations

import json
import os
import stat
import sys
import warnings
from collections.abc import Generator
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import psutil
import pytest

from autoskillit.core import atomic_write
from autoskillit.execution.backends import CodexBackend
from autoskillit.execution.headless import DefaultHeadlessExecutor
from autoskillit.execution.process import DefaultSubprocessRunner
from autoskillit.fleet._api import execute_dispatch
from autoskillit.fleet._semaphore import FleetSemaphore
from tests.fakes import InMemoryRecipeRepository
from tests.fleet.test_fleet_e2e import FleetTestRunner

pytestmark = [
    pytest.mark.layer("fleet"),
    pytest.mark.medium,
    pytest.mark.integration,
    pytest.mark.feature("fleet"),
    pytest.mark.skipif(sys.platform != "linux", reason="Linux-only: /proc filesystem required"),
]

_CODEX_SHIM_SCRIPT = '''\
#!/usr/bin/env python3
"""Codex shim for fleet E2E tests - emits Codex-format NDJSON."""
import json
import os
import sys

dispatch_id = os.environ.get("AUTOSKILLIT_DISPATCH_ID", "unknown")
session_id = "test-codex-session-" + dispatch_id[:8]

sentinel_body = json.dumps({"success": True, "reason": ""})
sentinel_text = (
    f"Task completed.\\n"
    f"---l3-result::{dispatch_id}---\\n"
    f"{sentinel_body}\\n"
    f"---end-l3-result::{dispatch_id}---"
)

events = [
    {"type": "thread.started", "thread_id": session_id},
    {
        "type": "item.completed",
        "item": {"type": "agent_message", "text": sentinel_text},
    },
    {
        "type": "turn.completed",
        "usage": {"input_tokens": 100, "output_tokens": 50},
    },
]

for event in events:
    sys.stdout.write(json.dumps(event) + "\\n")
sys.stdout.flush()
sys.exit(0)
'''


def _write_codex_shim(bin_dir: Path) -> Path:
    bin_dir.mkdir(parents=True, exist_ok=True)
    shim_path = bin_dir / "codex"
    atomic_write(shim_path, _CODEX_SHIM_SCRIPT)
    shim_path.chmod(shim_path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return shim_path


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


def _add_recipe(recipes: InMemoryRecipeRepository, name: str) -> None:
    """Register a minimal standard recipe (replicates FleetRuntime.add_recipe)."""
    from autoskillit.recipe.schema import Recipe, RecipeInfo, RecipeKind, RecipeSource

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


class TestCodexFleetE2E:
    """REQ-SHIM-001: codex shim dispatch through event-driven result path."""

    @pytest.fixture()
    def codex_runtime(
        self, tool_ctx: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> Generator[dict[str, Any], None, None]:
        shim_dir = tmp_path / "bin"
        _write_codex_shim(shim_dir)
        monkeypatch.setenv("PATH", f"{shim_dir}:{os.environ['PATH']}")

        monkeypatch.setattr(tool_ctx, "backend", CodexBackend())

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

        yield {
            "tool_ctx": tool_ctx,
            "recipes": recipes,
            "runner": runner,
            "dispatches_dir": dispatches_dir,
        }

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
        if leaked:
            pids = [c.pid for c in leaked]
            warnings.warn(
                f"codex_runtime fixture: {len(leaked)} leaked process(es) detected "
                f"and killed in teardown: pids={pids}",
                ResourceWarning,
                stacklevel=2,
            )
        for c in leaked:
            try:
                c.kill()
                c.wait(timeout=2)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass

    @pytest.mark.anyio
    async def test_codex_dispatch_happy_path(self, codex_runtime: dict[str, Any]) -> None:
        ctx = codex_runtime["tool_ctx"]
        recipes = codex_runtime["recipes"]

        _add_recipe(recipes, "test-codex-recipe")
        result = await execute_dispatch(
            tool_ctx=ctx,
            recipe="test-codex-recipe",
            task="Test codex dispatch",
            ingredients=None,
            dispatch_name=None,
            timeout_sec=None,
            prompt_builder=_simple_prompt_builder,
            quota_checker=_no_sleep_quota_checker,
            quota_refresher=_noop_quota_refresher,
        )
        envelope = cast(dict[str, Any], json.loads(result.outcome.to_envelope()))
        assert envelope["success"] is True, f"Dispatch failed: {envelope}"

        dispatch_id = envelope.get("dispatch_id", "")
        assert dispatch_id, "No dispatch_id in envelope"


# ---------------------------------------------------------------------------
# Slice 1 reproducer (plan: rectify_codex_l2_attempt_liveness)
# ---------------------------------------------------------------------------
# The real managed Codex L2 reproducer. Must be present at the start of the
# plan so each lower vertical slice can validate progress on a single
# failing test that exercises the full DefaultSubprocessRunner +
# CodexStreamParser + LivenessCoordinator path.
#
# Until Slice A/B/C/D/E/F all land, this test legitimately times out:
# the managed runner's idle watcher is keyed only to stdout byte growth,
# so a real Codex MCP operation that emits an item.started record and then
# stalls for any period > idle_output_timeout will be killed.
#
# Marked xfail(strict=True) so the repository remains green while lower
# slices are built. Slice G removes the xfail and asserts the test passes
# with and without optional item.updated records.
# ---------------------------------------------------------------------------


_CODEX_L2_SHIM_SCRIPT = '''\
#!/usr/bin/env python3
"""Codex L2 liveness shim for the plan reproducer.

Emits Codex-format NDJSON: thread.started, turn.started, MCP item.started,
optionally an item.updated in the middle of the stall (controlled via env),
then the matching item.completed (carrying a sentinel payload), a separate
agent_message item.completed carrying the real L3 sentinel block, and
finally turn.completed.

Real Codex backends do not emit the L3 sentinel themselves — only the agent
emits it inside an agent_message item. To keep this shim self-contained, we
fold the L3 sentinel into the agent_message item.

Stall between the MCP item.started and the matching item.completed is
controlled by `AUTOSKILLIT_SHIM_STALL_SEC` (default 1.5s). Optional
item.updated is emitted when `AUTOSKILLIT_SHIM_ITEM_UPDATED=1`.
"""
import json
import os
import sys
import time

dispatch_id = os.environ.get("AUTOSKILLIT_DISPATCH_ID", "unknown")
session_id = "test-codex-l2-" + dispatch_id[:8]

stall_sec = float(os.environ.get("AUTOSKILLIT_SHIM_STALL_SEC", "1.5"))
emit_update = os.environ.get("AUTOSKILLIT_SHIM_ITEM_UPDATED", "1") == "1"
tool_id = "toolu_l2_repro"

sentinel_body = json.dumps({"success": True, "reason": ""})
sentinel_text = (
    "Task completed.\\n"
    f"---l3-result::{dispatch_id}---\\n"
    f"{sentinel_body}\\n"
    f"---end-l3-result::{dispatch_id}---"
)
tool_result_payload = "ok"


def _flush(obj):
    sys.stdout.write(json.dumps(obj) + "\\n")
    sys.stdout.flush()


_flush({"type": "thread.started", "thread_id": session_id})
_flush({"type": "turn.started"})

# MCP operation start
_flush({
    "type": "item.started",
    "item": {"type": "mcp_tool_call", "id": tool_id, "name": "read_file",
             "arguments": {"path": "/dev/null"}},
})

time.sleep(stall_sec)

if emit_update:
    _flush({
        "type": "item.updated",
        "item": {"type": "mcp_tool_call", "id": tool_id, "status": "in_progress"},
    })

time.sleep(stall_sec)

# MCP operation complete (terminal)
_flush({
    "type": "item.completed",
    "item": {"type": "mcp_tool_call", "id": tool_id, "status": "completed",
             "result": tool_result_payload},
})

# Agent message carrying the L3 sentinel
_flush({
    "type": "item.completed",
    "item": {"type": "agent_message", "text": sentinel_text},
})

_flush({"type": "turn.completed", "usage": {"input_tokens": 100, "output_tokens": 50}})
sys.exit(0)
'''


def _write_l2_shim(bin_dir: Path) -> Path:
    bin_dir.mkdir(parents=True, exist_ok=True)
    shim_path = bin_dir / "codex"
    if shim_path.exists():
        shim_path.unlink()
    atomic_write(shim_path, _CODEX_L2_SHIM_SCRIPT)
    shim_path.chmod(shim_path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return shim_path


@pytest.mark.xfail(strict=True, reason="codex-l2-attempt-liveness plan slice 1")
@pytest.mark.anyio
async def test_managed_codex_l2_idle_during_mcp_operation_completes(
    tool_ctx: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Codex shim stalls while a live MCP operation is in flight.

    Repro of the Codex L2 attempt-liveness bug from the plan:
    `item.started` through the matching `item.completed` must grant
    authoritative typed liveness. With the current HEAD, the managed
    runner kills the process as `IDLE_STALL` once stdout byte growth
    stalls for `idle_output_timeout` seconds.
    """
    shim_dir = tmp_path / "bin"
    _write_l2_shim(shim_dir)
    monkeypatch.setenv("PATH", f"{shim_dir}:{os.environ['PATH']}")
    monkeypatch.setenv("AUTOSKILLIT_SHIM_STALL_SEC", "0.6")
    monkeypatch.setenv("AUTOSKILLIT_SHIM_ITEM_UPDATED", "1")

    # Force tiny idle caps so the test exposes the bug fast and stable.
    monkeypatch.setenv("AUTOSKILLIT_IDLE_OUTPUT_TIMEOUT", "0.2")
    monkeypatch.setenv("AUTOSKILLIT_MAX_SUPPRESSION_SECONDS", "0.2")

    runner = DefaultSubprocessRunner()
    tool_ctx.runner = runner
    tool_ctx.executor = DefaultHeadlessExecutor(tool_ctx)

    recipes = InMemoryRecipeRepository()
    tool_ctx.recipes = recipes
    tool_ctx.kitchen_id = uuid4().hex[:16]
    tool_ctx.project_dir = tmp_path
    tool_ctx.backend = CodexBackend()

    dispatches_dir = tool_ctx.temp_dir / "dispatches"
    dispatches_dir.mkdir(parents=True, exist_ok=True)
    tool_ctx.fleet_lock = FleetSemaphore(max_concurrent=1)

    _add_recipe(recipes, "test-codex-l2-recipe")

    result = await execute_dispatch(
        tool_ctx=tool_ctx,
        recipe="test-codex-l2-recipe",
        task="Test codex L2 idle-in-mcp liveness",
        ingredients=None,
        dispatch_name=None,
        timeout_sec=None,
        prompt_builder=_simple_prompt_builder,
        quota_checker=_no_sleep_quota_checker,
        quota_refresher=_noop_quota_refresher,
    )
    envelope = cast(dict[str, Any], json.loads(result.outcome.to_envelope()))
    assert envelope["success"] is True, f"L2 dispatch failed: {envelope}"
