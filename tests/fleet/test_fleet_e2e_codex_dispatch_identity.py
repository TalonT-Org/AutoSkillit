"""Codex MCP environment boundary coverage for fleet dispatch identity."""

from __future__ import annotations

import json
import os
import re
import stat
import subprocess
import sys
from collections.abc import Generator
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import anyio
import psutil
import pytest

from autoskillit.config import load_config
from autoskillit.core import DefaultManagedWorkerCapacity, atomic_write
from autoskillit.execution.backends import CodexBackend
from autoskillit.execution.headless import DefaultHeadlessExecutor
from autoskillit.execution.process import default_tether_dir
from autoskillit.pipeline.gate import DefaultGateState
from autoskillit.server.tools.tools_fleet_dispatch import dispatch_food_truck
from tests.conftest import production_interpreter_env
from tests.fakes import InMemoryRecipeRepository
from tests.fleet._codex_mcp_env import (
    CODEX_MCP_DEFAULT_ENV_VARS,
    codex_mcp_autoskillit_env_vars,
    codex_mcp_server_env,
)
from tests.fleet._descendant_worker import (
    is_identity_alive,
    kill_identity_fenced,
    read_identity,
    write_descendant_worker_script,
)
from tests.fleet.test_fleet_e2e import FleetTestRunner

pytestmark = [
    pytest.mark.layer("fleet"),
    pytest.mark.medium,
    pytest.mark.integration,
    pytest.mark.feature("fleet"),
    pytest.mark.skipif(sys.platform != "linux", reason="Linux-only: /proc filesystem required"),
]

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
import signal
import subprocess
import sys
import time
import tomllib

PYTHON = __PYTHON__
PROBE = __PROBE__
BASE_ENV_VARS = __BASE_ENV_VARS__
STANDIN_SCRIPT = __STANDIN_SCRIPT__

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

teardown_mode = os.environ.get("CODEX_STANDIN_TEARDOWN", "")
if teardown_mode:
    server_env["DESCENDANT_TEST_DIR"] = os.environ["DESCENDANT_TEST_DIR"]
    # codex-rs spawns its MCP server in its own process group (process_group(0));
    # env_clear() plus DEFAULT_ENV_VARS/config env_vars is server_env above.
    standin = subprocess.Popen(
        [PYTHON, STANDIN_SCRIPT],
        env=server_env,
        cwd=os.getcwd(),
        process_group=0,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    identity_path = os.path.join(os.environ["DESCENDANT_TEST_DIR"], "worker_identity.json")
    deadline = time.monotonic() + 10.0
    while not os.path.isfile(identity_path) and time.monotonic() < deadline:
        time.sleep(0.02)
    if teardown_mode == "reap":
        # Codex's real teardown (codex-rs stdio_server_launcher.rs terminate()):
        # killpg(SIGTERM) on the server's group, then SIGKILL the server's own
        # pid only — never a group-level SIGKILL. A funnel-spawned descendant
        # in its own session survives this and is settled only by owner scope.
        try:
            os.killpg(standin.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        time.sleep(0.01)
        try:
            os.kill(standin.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        try:
            standin.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
    # "lingering": the shim exits without touching the stand-in at all.
else:
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

# MCP-server stand-in: records the owner-scope env it was launched with, then
# funnel-spawns a worker into that scope exactly the way a real MCP server
# tool handler would (env=None -> ambient inheritance from its own env, which
# is server_env above). After the initial spawn it polls for a test-written
# go-file and, on the first sighting, attempts a second funnel spawn into the
# same (by-then-sealed) scope so the test can observe OwnerScopeSealedError.
_OWNER_SCOPE_STANDIN_SCRIPT = """\
import json
import os
import subprocess
import time

from autoskillit.core import (
    OWNER_SCOPE_DIR_ENV_VAR,
    OWNER_SCOPE_ENV_VAR,
    read_boot_id,
    read_starttime_ticks,
)
from autoskillit.execution.process import TetherSpec, spawn_owned_process

PYTHON = __PYTHON__
WORKER_SCRIPT = __WORKER_SCRIPT__

test_dir = os.environ["DESCENDANT_TEST_DIR"]
os.makedirs(test_dir, exist_ok=True)


def _publish(path, payload):
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(payload)
    os.replace(tmp, path)


_publish(
    os.path.join(test_dir, "standin_owner_scope.json"),
    json.dumps(
        {
            "token": os.environ.get(OWNER_SCOPE_ENV_VAR, ""),
            "dir": os.environ.get(OWNER_SCOPE_DIR_ENV_VAR, ""),
        }
    ),
)
_standin_pid = os.getpid()
_publish(
    os.path.join(test_dir, "standin_identity.json"),
    json.dumps(
        {
            "pid": _standin_pid,
            "boot_id": read_boot_id(),
            "starttime_ticks": read_starttime_ticks(_standin_pid),
        }
    ),
)


def _spawn_worker():
    spawn_owned_process(
        [PYTHON, WORKER_SCRIPT],
        env=None,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        tether=TetherSpec(origin="test-owner-scope-worker", ceiling_seconds=600.0),
    )


_spawn_worker()

go_path = os.path.join(test_dir, "attempt_go")
outcome_path = os.path.join(test_dir, "attempt_outcome.json")
attempted = False
deadline = time.monotonic() + 120.0
while time.monotonic() < deadline:
    if not attempted and os.path.isfile(go_path):
        attempted = True
        try:
            _spawn_worker()
        except Exception as exc:
            outcome = type(exc).__name__
        else:
            outcome = "no_error"
        _publish(outcome_path, json.dumps({"outcome": outcome}))
    time.sleep(0.05)
"""


def _write_codex_mcp_boundary_shim(bin_dir: Path) -> Path:
    bin_dir.mkdir(parents=True, exist_ok=True)
    worker_script = write_descendant_worker_script(bin_dir)
    standin_path = bin_dir / "_owner_scope_standin.py"
    standin_script = _OWNER_SCOPE_STANDIN_SCRIPT.replace(
        "__PYTHON__", repr(sys.executable)
    ).replace("__WORKER_SCRIPT__", repr(str(worker_script)))
    atomic_write(standin_path, standin_script)
    shim_path = bin_dir / "codex"
    script = (
        _CODEX_MCP_BOUNDARY_SHIM_SCRIPT.replace("__PYTHON__", repr(sys.executable))
        .replace("__PROBE__", repr(_PROBE_SCRIPT))
        .replace("__BASE_ENV_VARS__", repr(CODEX_MCP_DEFAULT_ENV_VARS))
        .replace("__STANDIN_SCRIPT__", repr(str(standin_path)))
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


def _reap_descendants(test_dir: Path) -> None:
    """Identity-fenced cleanup run regardless of test outcome — a red run (no
    settlement) must never leave the worker/grandchild/stand-in heartbeating past
    the test."""
    kill_identity_fenced(test_dir / "worker_identity.json")
    kill_identity_fenced(test_dir / "grandchild_identity.json")
    kill_identity_fenced(test_dir / "standin_identity.json")


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
    env_vars = codex_mcp_autoskillit_env_vars(generated_home)
    return codex_mcp_server_env(agent_env, env_vars)


class TestCodexMcpDispatchIdentityE2E:
    @pytest.fixture()
    def codex_mcp_runtime(
        self, make_tool_ctx: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> Generator[dict[str, Any], None, None]:
        _write_project_config(tmp_path)
        tool_ctx = make_tool_ctx(config=load_config(tmp_path))
        tool_ctx.gate = DefaultGateState(enabled=True)
        monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "fleet")
        monkeypatch.setenv("AUTOSKILLIT_LOG_DIR", str(tmp_path / "autoskillit-log-dir"))

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
        for child in leaked:
            try:
                child.kill()
                child.wait(timeout=2)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        assert not leaked, (
            f"codex_mcp_runtime fixture leaked process(es): {[c.pid for c in leaked]}"
        )

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

    @pytest.mark.anyio
    async def test_owner_scope_vars_forwarded_and_descendants_settled(
        self,
        codex_mcp_runtime: dict[str, Any],
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The owner-scope env survives Codex's real env_clear() narrowing, and a
        funnel-spawned worker + grandchild left running past a non-tidy teardown
        (killpg(SIGTERM) on the stand-in's group, then SIGKILL its own pid only —
        never the worker's session) are still settled by owner-scope, not by any
        process-group signal reaching them.
        """
        test_dir = tmp_path / "owner-scope-reap"
        monkeypatch.setenv("DESCENDANT_TEST_DIR", str(test_dir))
        monkeypatch.setenv("CODEX_STANDIN_TEARDOWN", "reap")
        _add_recipe(codex_mcp_runtime["recipes"], "test-codex-mcp-boundary")

        try:
            envelope = await self._dispatch(codex_mcp_runtime)
            assert envelope["success"] is True, envelope.get("stderr") or envelope
            dispatch_id = envelope["dispatch_id"]

            standin_scope = json.loads((test_dir / "standin_owner_scope.json").read_text())
            token_pattern = re.compile(rf"^dispatch-{re.escape(dispatch_id)}-[0-9a-f]{{12}}$")
            assert token_pattern.fullmatch(standin_scope["token"]), standin_scope
            assert standin_scope["dir"] == str(default_tether_dir()), standin_scope

            worker_identity = read_identity(test_dir / "worker_identity.json")
            grandchild_identity = read_identity(test_dir / "grandchild_identity.json")
            assert worker_identity is not None, "worker never recorded its identity"
            assert grandchild_identity is not None, "grandchild never recorded its identity"
            assert not is_identity_alive(worker_identity), "worker survived the dispatch"
            assert not is_identity_alive(grandchild_identity), "grandchild survived the dispatch"
        finally:
            _reap_descendants(test_dir)

    @pytest.mark.anyio
    async def test_owner_scope_seals_against_late_spawn_from_lingering_standin(
        self,
        codex_mcp_runtime: dict[str, Any],
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A stand-in the shim never kills is still sealed off: once the dispatch
        returns, a funnel spawn it attempts into the same scope is refused.
        """
        test_dir = tmp_path / "owner-scope-lingering"
        monkeypatch.setenv("DESCENDANT_TEST_DIR", str(test_dir))
        monkeypatch.setenv("CODEX_STANDIN_TEARDOWN", "lingering")
        _add_recipe(codex_mcp_runtime["recipes"], "test-codex-mcp-boundary")

        try:
            envelope = await self._dispatch(codex_mcp_runtime)
            assert envelope["success"] is True, envelope.get("stderr") or envelope

            standin_identity = read_identity(test_dir / "standin_identity.json")
            assert standin_identity is not None, "stand-in never recorded its identity"
            assert is_identity_alive(standin_identity), (
                "the shim must not have touched the lingering stand-in"
            )

            worker_identity = read_identity(test_dir / "worker_identity.json")
            assert worker_identity is not None, "worker never recorded its identity"
            assert not is_identity_alive(worker_identity), "worker survived the dispatch"

            (test_dir / "attempt_go").touch()
            outcome_path = test_dir / "attempt_outcome.json"
            with anyio.fail_after(10.0):
                while not outcome_path.is_file():
                    await anyio.sleep(0.02)
            outcome = json.loads(outcome_path.read_text())
            assert outcome["outcome"] == "OwnerScopeSealedError", outcome

            assert read_identity(test_dir / "worker_identity.json") == worker_identity, (
                "a sealed scope must never admit a new worker"
            )
        finally:
            _reap_descendants(test_dir)
