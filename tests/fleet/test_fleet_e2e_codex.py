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
import time

dispatch_id = os.environ.get("AUTOSKILLIT_DISPATCH_ID", "unknown")
mode = os.environ.get("CODEX_SHIM_MODE", "success")
sleep_sec = float(os.environ.get("CODEX_SHIM_SLEEP_SEC", "0"))
session_id = "test-codex-session-" + dispatch_id[:8]

sentinel_body = json.dumps({"success": True, "reason": ""})
sentinel_text = (
    f"Task completed.\\n"
    f"---l3-result::{dispatch_id}---\\n"
    f"{sentinel_body}\\n"
    f"---end-l3-result::{dispatch_id}---"
)

def emit(event):
    sys.stdout.write(json.dumps(event) + "\\n")
    sys.stdout.flush()


emit({"type": "thread.started", "thread_id": session_id})

if mode == "mcp_silence":
    emit(
        {
            "type": "item.started",
            "item": {"id": "mcp-1", "type": "mcp_tool_call", "name": "open_kitchen"},
        }
    )
    emit(
        {
            "type": "item.updated",
            "status": "in_progress",
            "item": {
                "id": "mcp-1",
                "type": "mcp_tool_call",
                "name": "open_kitchen",
                "status": "in_progress",
            },
        }
    )
    time.sleep(sleep_sec)
    emit(
        {
            "type": "item.completed",
            "item": {
                "id": "mcp-1",
                "type": "mcp_tool_call",
                "name": "open_kitchen",
                "result": "ok",
            },
        }
    )

for event in [
    {
        "type": "item.completed",
        "item": {"type": "agent_message", "text": sentinel_text},
    },
    {
        "type": "turn.completed",
        "usage": {"input_tokens": 100, "output_tokens": 50},
    },
]:
    emit(event)
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

    @pytest.mark.anyio
    async def test_codex_dispatch_survives_in_flight_mcp_silence_at_fleet_level(
        self,
        codex_runtime: dict[str, Any],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        ctx = codex_runtime["tool_ctx"]
        recipes = codex_runtime["recipes"]

        # FleetTestRunner waits with subprocess.communicate(), so this covers the
        # Codex food-truck flow and NDJSON shape without claiming process-watchdog
        # coverage; process watchdog tests own idle suppression behavior.
        monkeypatch.setenv("CODEX_SHIM_MODE", "mcp_silence")
        monkeypatch.setenv("CODEX_SHIM_SLEEP_SEC", "0.15")
        monkeypatch.setattr(ctx.config.fleet, "idle_output_timeout", 0.05)

        _add_recipe(recipes, "test-codex-mcp-silence")
        result = await execute_dispatch(
            tool_ctx=ctx,
            recipe="test-codex-mcp-silence",
            task="Test codex dispatch with in-flight MCP silence",
            ingredients=None,
            dispatch_name=None,
            timeout_sec=None,
            prompt_builder=_simple_prompt_builder,
            quota_checker=_no_sleep_quota_checker,
            quota_refresher=_noop_quota_refresher,
        )
        envelope = cast(dict[str, Any], json.loads(result.outcome.to_envelope()))

        assert envelope["success"] is True, f"Dispatch failed: {envelope}"
        assert "idle_stall" not in json.dumps(envelope).lower()
