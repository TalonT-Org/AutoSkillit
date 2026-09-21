"""Codex MCP environment boundary coverage for fleet dispatch identity."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import tomllib
import warnings
from collections.abc import Generator
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import psutil
import pytest

from autoskillit.config import load_config
from autoskillit.core import DefaultManagedWorkerCapacity, atomic_write
from autoskillit.execution.backends import CodexBackend
from autoskillit.execution.headless import DefaultHeadlessExecutor
from autoskillit.pipeline.gate import DefaultGateState
from autoskillit.server.tools.tools_fleet_dispatch import dispatch_food_truck
from tests.conftest import production_interpreter_env
from tests.fakes import InMemoryRecipeRepository
from tests.fleet.test_fleet_e2e import FleetTestRunner

pytestmark = [
    pytest.mark.layer("fleet"),
    pytest.mark.medium,
    pytest.mark.integration,
    pytest.mark.feature("fleet"),
    pytest.mark.skipif(sys.platform != "linux", reason="Linux-only: /proc filesystem required"),
]

_MCP_SERVER_BASE_ENV_VARS = (
    "HOME",
    "LOGNAME",
    "PATH",
    "SHELL",
    "USER",
    "__CF_USER_TEXT_ENCODING",
    "LANG",
    "LC_ALL",
    "TERM",
    "TMPDIR",
    "TZ",
)

_PROBE_SCRIPT = """\
import json
import os
import time
from pathlib import Path

from autoskillit.config import (
    build_config_authoritative_layer,
    load_config,
    resolve_ingredient_defaults,
)
from autoskillit.core import session_shape
from autoskillit.execution.session_log import resolve_log_dir
from autoskillit.recipe import bind_recipe
from autoskillit.recipe.io import builtin_recipes_dir, load_recipe

layer = build_config_authoritative_layer(resolve_ingredient_defaults(Path.cwd()))
recipe = load_recipe(builtin_recipes_dir() / "implementation.yaml")
projection = bind_recipe(recipe, ingredient_values=layer)
bound_dispatch_id = next(
    value.effective_value
    for value in projection.invocations["analyze_pipeline_health"].skill_inputs
    if value.name == "dispatch_id"
)
observation = {
    "dispatch_id": layer["dispatch_id"],
    "is_fleet_dispatch": layer["is_fleet_dispatch"],
    "bound_dispatch_id": bound_dispatch_id,
}
if layer["dispatch_id"]:
    log_dir = resolve_log_dir(load_config(Path.cwd()).linux_tracing.log_dir)
    report_dir = log_dir / "health-reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / f'{layer["dispatch_id"]}_health_report.json').write_text(
        json.dumps(
            {
                "kitchen_id": session_shape().label,
                "dispatch_id": layer["dispatch_id"],
                "observed_is_fleet_dispatch": layer["is_fleet_dispatch"],
                "observed_campaign_id": os.environ.get("AUTOSKILLIT_CAMPAIGN_ID", ""),
                "observed_bound_dispatch_id": bound_dispatch_id,
                "timestamp": time.time(),
                "findings": [],
                "summary": "probe",
            }
        )
    )
print(json.dumps(observation))
"""

_CODEX_MCP_BOUNDARY_SHIM_SCRIPT = """\
#!/usr/bin/env python3
import json
import os
import subprocess
import sys
import tomllib

PYTHON = __PYTHON__
PROBE = __PROBE__
BASE_ENV_VARS = __BASE_ENV_VARS__

with open(os.path.join(os.environ["CODEX_HOME"], "config.toml"), "rb") as config_file:
    config = tomllib.load(config_file)
env_vars = config["mcp_servers"]["autoskillit"]["env_vars"]
server_env = {
    key: os.environ[key]
    for key in BASE_ENV_VARS
    if key in os.environ
} | {
    key: os.environ[key]
    for key in env_vars
    if key in os.environ
}
subprocess.run(
    [PYTHON, "-c", PROBE],
    env=server_env,
    cwd=os.getcwd(),
    check=True,
    stdout=subprocess.DEVNULL,
)

dispatch_id = os.environ.get("AUTOSKILLIT_DISPATCH_ID", "unknown")
session_id = "test-codex-session-" + dispatch_id[:8]
sentinel_body = json.dumps({"success": True, "reason": ""})
sentinel_text = (
    f"Task completed.\\n"
    f"---l3-result::{dispatch_id}---\\n"
    f"{sentinel_body}\\n"
    f"---end-l3-result::{dispatch_id}---"
)
for event in (
    {"type": "thread.started", "thread_id": session_id},
    {"type": "item.completed", "item": {"type": "agent_message", "text": sentinel_text}},
    {"type": "turn.completed", "usage": {"input_tokens": 100, "output_tokens": 50}},
):
    sys.stdout.write(json.dumps(event) + "\\n")
sys.stdout.flush()
"""


def _write_codex_mcp_boundary_shim(bin_dir: Path) -> Path:
    bin_dir.mkdir(parents=True, exist_ok=True)
    shim_path = bin_dir / "codex"
    script = (
        _CODEX_MCP_BOUNDARY_SHIM_SCRIPT.replace("__PYTHON__", repr(sys.executable))
        .replace("__PROBE__", repr(_PROBE_SCRIPT))
        .replace("__BASE_ENV_VARS__", repr(_MCP_SERVER_BASE_ENV_VARS))
    )
    atomic_write(shim_path, script)
    shim_path.chmod(shim_path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return shim_path


def _write_project_config(tmp_path: Path) -> None:
    config_path = tmp_path / ".autoskillit" / "config.yaml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(
        config_path,
        "\n".join(
            (
                "features:",
                "  fleet: true",
                "diagnostics:",
                "  pipeline_health: true",
                "linux_tracing:",
                f"  log_dir: {tmp_path / 'session_logs'}",
                "",
            )
        ),
    )


def _add_recipe(recipes: InMemoryRecipeRepository, name: str) -> None:
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


def _server_env_from_generated_home(generated_home: Path) -> dict[str, str]:
    agent_env = production_interpreter_env()
    with (generated_home / "config.toml").open("rb") as config_file:
        config = tomllib.load(config_file)
    env_vars = config["mcp_servers"]["autoskillit"]["env_vars"]
    return {key: agent_env[key] for key in _MCP_SERVER_BASE_ENV_VARS if key in agent_env} | {
        key: agent_env[key] for key in env_vars if key in agent_env
    }


class TestCodexMcpDispatchIdentityE2E:
    @pytest.fixture()
    def codex_mcp_runtime(
        self, make_tool_ctx: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> Generator[dict[str, Any], None, None]:
        _write_project_config(tmp_path)
        tool_ctx = make_tool_ctx(config=load_config(tmp_path))
        tool_ctx.gate = DefaultGateState(enabled=True)
        monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "fleet")

        shim_dir = tmp_path / "bin"
        _write_codex_mcp_boundary_shim(shim_dir)
        monkeypatch.setenv("PATH", f"{shim_dir}:{os.environ['PATH']}")
        monkeypatch.setattr(tool_ctx, "backend", CodexBackend())

        runner = FleetTestRunner()
        tool_ctx.runner = runner
        tool_ctx.executor = DefaultHeadlessExecutor(tool_ctx)
        tool_ctx.worker_capacity = DefaultManagedWorkerCapacity(max_concurrent=1)
        recipes = InMemoryRecipeRepository()
        tool_ctx.recipes = recipes
        tool_ctx.kitchen_id = uuid4().hex[:16]
        tool_ctx.project_dir = tmp_path

        dispatches_dir = tool_ctx.temp_dir / "dispatches"
        dispatches_dir.mkdir(parents=True, exist_ok=True)
        pre_children = {
            child.pid for child in psutil.Process(os.getpid()).children(recursive=True)
        }

        yield {
            "tool_ctx": tool_ctx,
            "recipes": recipes,
            "runner": runner,
            "dispatches_dir": dispatches_dir,
            "log_dir": tmp_path / "session_logs",
        }

        leaked = []
        for child in psutil.Process(os.getpid()).children(recursive=True):
            if child.pid not in pre_children:
                try:
                    if child.is_running() and child.status() not in (
                        psutil.STATUS_ZOMBIE,
                        psutil.STATUS_DEAD,
                    ):
                        leaked.append(child)
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
        if leaked:
            pids = [child.pid for child in leaked]
            warnings.warn(
                f"codex_mcp_runtime fixture killed leaked process(es): pids={pids}",
                ResourceWarning,
                stacklevel=2,
            )
        for child in leaked:
            try:
                child.kill()
                child.wait(timeout=2)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass

    async def _dispatch(self, runtime: dict[str, Any], **resume: str | None) -> dict[str, Any]:
        raw = await dispatch_food_truck(
            recipe="test-codex-mcp-boundary",
            task="Observe the Codex MCP environment boundary",
            resume_session_id=resume.get("resume_session_id"),
            prior_dispatch_id=resume.get("prior_dispatch_id"),
        )
        return cast(dict[str, Any], json.loads(raw))

    @pytest.mark.anyio
    async def test_fresh_dispatch_id_survives_codex_mcp_boundary(
        self, codex_mcp_runtime: dict[str, Any]
    ) -> None:
        assert "AUTOSKILLIT_DISPATCH_ID" not in os.environ
        _add_recipe(codex_mcp_runtime["recipes"], "test-codex-mcp-boundary")

        envelope = await self._dispatch(codex_mcp_runtime)

        assert envelope["success"] is True, envelope.get("stderr") or envelope
        dispatch_id = envelope["dispatch_id"]
        assert dispatch_id
        report = envelope["health_report"]
        assert report["dispatch_id"] == dispatch_id
        assert report["observed_bound_dispatch_id"] == dispatch_id
        assert report["observed_is_fleet_dispatch"] == "true"
        assert report["observed_campaign_id"] == codex_mcp_runtime["tool_ctx"].kitchen_id
        assert (
            codex_mcp_runtime["log_dir"] / "health-reports" / f"{dispatch_id}_health_report.json"
        ).is_file()

    @pytest.mark.anyio
    async def test_resumed_dispatch_keeps_identity_across_codex_mcp_boundary(
        self, codex_mcp_runtime: dict[str, Any]
    ) -> None:
        _add_recipe(codex_mcp_runtime["recipes"], "test-codex-mcp-boundary")
        first = await self._dispatch(codex_mcp_runtime)
        assert first["success"] is True, first.get("stderr") or first

        second = await self._dispatch(
            codex_mcp_runtime,
            resume_session_id=first["dispatched_session_id"],
            prior_dispatch_id=first["dispatch_id"],
        )

        assert second["success"] is True, second
        assert second["dispatch_id"] == first["dispatch_id"]
        assert second["health_report"]["dispatch_id"] == first["dispatch_id"]
        assert second["health_report"]["observed_bound_dispatch_id"] == first["dispatch_id"]
        report_path = (
            codex_mcp_runtime["log_dir"]
            / "health-reports"
            / f"{first['dispatch_id']}_health_report.json"
        )
        assert report_path.is_file()
        assert json.loads(report_path.read_text())["dispatch_id"] == first["dispatch_id"]

    def test_non_fleet_codex_shaped_env_keeps_empty_identity_valid(
        self, codex_mcp_runtime: dict[str, Any], tmp_path: Path
    ) -> None:
        assert "AUTOSKILLIT_DISPATCH_ID" not in os.environ
        generated_home = tmp_path / "generated-codex-home"
        codex_mcp_runtime["tool_ctx"].backend.ensure_pre_launch(session_dir=generated_home)
        server_env = _server_env_from_generated_home(generated_home)

        probe = subprocess.run(
            [sys.executable, "-c", _PROBE_SCRIPT],
            capture_output=True,
            check=True,
            cwd=tmp_path,
            env=server_env,
            text=True,
        )
        observation = json.loads(probe.stdout)

        assert observation["dispatch_id"] == ""
        assert observation["is_fleet_dispatch"] == "false"
        assert not list((codex_mcp_runtime["log_dir"] / "health-reports").glob("*.json"))
