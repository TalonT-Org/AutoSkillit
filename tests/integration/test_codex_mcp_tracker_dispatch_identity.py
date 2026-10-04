"""Pipeline tracker keys off the forwarded dispatch id across a real Codex MCP boundary.

Boots a genuine ``python -m autoskillit`` server subprocess with the exact env a
Codex-launched MCP server would receive: a food-truck L2 parent env (the shape
``dispatch_food_truck`` builds for the Codex CLI it spawns), narrowed through
Codex's own env-clear-and-rebuild step (``codex_mcp_server_env``, tests/fleet's
shared helper) using ``mcp_servers.autoskillit.env_vars`` from a real
``config.toml`` written by ``_ensure_codex_mcp_registered_unlocked``.

Proves the writer/reader closure end to end: the launched server writes tracker
credit keyed by ``AUTOSKILLIT_DISPATCH_ID`` (never the kitchen id it resolves for
itself), and ``load_dispatch_progress`` — the same function fleet dispatch calls
in-process to read progress back — reads that identical tracker file.

``load_recipe`` never installs ``active_recipe_steps`` (only ``open_kitchen``'s
``_serve_named_recipe`` does, via ``_cache_finalized_recipe_projection``), so the
credited step is driven through ``open_kitchen`` — the same attested path a real
Codex-dispatched orchestrator uses for a multi-step recipe. That path requires a
completed recipe execution (``complete_recipe_initialization``) and an exact
``recipe_execution_id``/``invocation_template_digest``/``skill_inputs`` match
against the compiled step template before ``run_skill`` admits the call.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import tomllib
from pathlib import Path
from uuid import uuid4

import anyio
import mcp.client.stdio
import pytest

from autoskillit.core import (
    AGENT_BACKEND_CODEX,
    CAMPAIGN_ID_ENV_VAR,
    DISPATCH_ID_ENV_VAR,
    FOOD_TRUCK_TOOL_TAGS_ENV_VAR,
    HEADLESS_ENV_VAR,
    SESSION_TYPE_ENV_VAR,
    SESSION_TYPE_ORCHESTRATOR,
    pipeline_tracker_directory,
    release_tracker_lease,
)
from autoskillit.execution.backends._codex_config import _ensure_codex_mcp_registered_unlocked
from autoskillit.execution.process._process_tether import default_tether_dir
from autoskillit.fleet._checkpoint_bridge import (
    load_dispatch_progress,
    retain_dispatch_tracker_authority,
)
from autoskillit.fleet.sidecar import sidecar_path
from tests.conftest import production_interpreter_env
from tests.fleet._codex_mcp_env import (
    CODEX_MCP_DEFAULT_ENV_VARS,
    codex_mcp_autoskillit_env_vars,
    codex_mcp_server_env,
)

pytestmark = [
    pytest.mark.layer("integration"),
    pytest.mark.medium,
    pytest.mark.integration,
    pytest.mark.feature("fleet"),
    pytest.mark.anyio,
    pytest.mark.skipif(sys.platform != "linux", reason="Linux-only: stdio client + /proc"),
]

_RECIPE = "promote-to-main-wrapper"
_STEP = "promote"
_MARKER_PATTERN = "%%ORDER_UP::[0-9a-f]+%%"
_WRITE_PATH = ".autoskillit/temp/promote-to-main"

# One `-p <prompt>` implementation of the real Claude Code CLI's contract: extract
# the injected completion marker, prove write evidence under the skill's declared
# write_paths, and satisfy this skill's registered output patterns
# (src/autoskillit/recipes/contracts/promote-to-main-wrapper.json).
_CLAUDE_IMPL_SCRIPT = f"""\
#!/usr/bin/env python3
import os
import json
import re
import sys
import time
from pathlib import Path

os.makedirs({_WRITE_PATH!r}, exist_ok=True)
with open(os.path.join({_WRITE_PATH!r}, "result.md"), "w") as f:
    f.write("synthetic promotion artifact\\n")

prompt = sys.argv[sys.argv.index("-p") + 1] if "-p" in sys.argv else ""
match = re.search({_MARKER_PATTERN!r}, prompt)
marker = match.group(0) if match else ""
# Stay observable until the runner records this workload's identity.
tether_dir = Path(__TETHER_DIR__)
deadline = time.monotonic() + 10
acknowledged = False
while not acknowledged and time.monotonic() < deadline:
    for path in tether_dir.glob("*.json"):
        try:
            record = json.loads(path.read_text())
        except FileNotFoundError:
            continue
        if record.get("workload_pid") == os.getpid():
            acknowledged = True
            break
    if not acknowledged:
        time.sleep(0.01)
if not acknowledged:
    raise RuntimeError("runner did not acknowledge the synthetic workload PID")
text = "pr_url = \\nverdict = dry_run\\ncategory_summary = synthetic test run\\n" + marker
envelope = {{
    "type": "result",
    "subtype": "success",
    "is_error": False,
    "result": text,
    "session_id": "test-run-skill-session",
    "errors": [],
    "usage": {{"input_tokens": 0, "output_tokens": 0}},
}}
print(json.dumps(envelope))
"""

# The real `claude` CLI is a Node shebang script; `resolve_trace_target`
# (src/autoskillit/execution/evidence/linux_tracing.py) walks PTY-spawn
# descendants matching basename "claude" (or "node") — a plain Python shebang
# script's exec'd interpreter never matches either, so run_skill's PTY-mode
# workload resolution times out (`TraceTargetResolutionError`, issue #806) before
# ever reaching this shim's own logic. `exec -a claude` makes the exec'd
# python3's argv[0] (and so /proc/<pid>/cmdline[0]) read "claude", which the
# resolver's `Path(cmdline[0]).name in _match_names` branch accepts.
_CLAUDE_SHIM_SCRIPT = (
    '#!/bin/bash\nexec -a claude python3 "$(dirname "$0")/_claude_impl.py" "$@"\n'
)


def _write_claude_shim(bin_dir: Path, tether_dir: Path) -> None:
    bin_dir.mkdir(parents=True, exist_ok=True)
    impl_path = bin_dir / "_claude_impl.py"
    impl_path.write_text(
        _CLAUDE_IMPL_SCRIPT.replace("__TETHER_DIR__", repr(str(tether_dir))), encoding="utf-8"
    )
    impl_path.chmod(0o755)
    shim_path = bin_dir / "claude"
    shim_path.write_text(_CLAUDE_SHIM_SCRIPT, encoding="utf-8")
    shim_path.chmod(0o755)


def _write_project_config(project_dir: Path) -> None:
    config_path = project_dir / ".autoskillit" / "config.yaml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        "quota_guard:\n  enabled: false\nlinux_tracing:\n  enabled: false\n",
        encoding="utf-8",
    )


def _tool_json(result: object) -> dict[str, object]:
    content = getattr(result, "content")
    assert len(content) == 1
    decoded = json.loads(getattr(content[0], "text"))
    assert isinstance(decoded, dict)
    return decoded


def test_claude_shim_waits_for_workload_identity(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    tether_dir = tmp_path / "tethers"
    tether_dir.mkdir()
    _write_claude_shim(bin_dir, tether_dir)
    marker = "%%ORDER_UP::" + "a" * 32 + "%%"
    process = subprocess.Popen(
        [str(bin_dir / "claude"), "-p", marker],
        cwd=tmp_path,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=production_interpreter_env(),
    )
    try:
        artifact = tmp_path / _WRITE_PATH / "result.md"
        deadline = time.monotonic() + 5
        while not artifact.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert artifact.exists()
        assert process.poll() is None
        (tether_dir / "workload.json").write_text(
            json.dumps({"workload_pid": process.pid}), encoding="utf-8"
        )
        stdout, stderr = process.communicate(timeout=10)
        assert process.returncode == 0, stderr
        assert marker in json.loads(stdout)["result"]
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)


async def test_tracker_keys_off_dispatch_id_across_real_codex_mcp_boundary(
    tmp_path: Path, tool_ctx
) -> None:
    from fastmcp.client import Client
    from fastmcp.client.transports import StdioTransport

    project_dir = tmp_path / "project"
    project_dir.mkdir()
    _write_project_config(project_dir)
    codex_home = tmp_path / "codex-home"
    codex_home.mkdir()
    bin_dir = tmp_path / "bin"
    _write_claude_shim(bin_dir, default_tether_dir())

    config_path = codex_home / "config.toml"
    assert _ensure_codex_mcp_registered_unlocked(config_path=config_path) is True
    with config_path.open("rb") as config_file:
        written_config = tomllib.load(config_file)
    assert written_config["mcp_servers"]["autoskillit"]["command"] == "autoskillit"
    env_vars = codex_mcp_autoskillit_env_vars(codex_home)

    dispatch_id = uuid4().hex
    campaign_id = f"campaign-{uuid4().hex[:8]}"
    parent_env = {
        **production_interpreter_env(),
        SESSION_TYPE_ENV_VAR: SESSION_TYPE_ORCHESTRATOR,
        HEADLESS_ENV_VAR: "1",
        DISPATCH_ID_ENV_VAR: dispatch_id,
        "AUTOSKILLIT_PROJECT_DIR": str(project_dir),
        CAMPAIGN_ID_ENV_VAR: campaign_id,
        FOOD_TRUCK_TOOL_TAGS_ENV_VAR: "kitchen-core",
        "PATH": os.pathsep.join((str(bin_dir), os.environ.get("PATH", ""))),
    }
    server_env = codex_mcp_server_env(parent_env, env_vars)
    assert set(mcp.client.stdio.DEFAULT_INHERITED_ENV_VARS) <= set(CODEX_MCP_DEFAULT_ENV_VARS)

    transport = StdioTransport(
        command=sys.executable,
        args=["-m", "autoskillit"],
        env=server_env,
        cwd=str(project_dir),
        keep_alive=False,
    )

    with anyio.fail_after(45):
        async with Client(transport) as client:
            open_payload = _tool_json(
                await client.call_tool(
                    "open_kitchen",
                    {"name": _RECIPE, "overrides": {"source_dir": str(project_dir)}},
                )
            )
            assert open_payload["success"] is True, open_payload
            assert open_payload["valid"] is True, open_payload

            completion_payload = _tool_json(
                await client.call_tool(
                    "complete_recipe_initialization",
                    {"initialization_id": open_payload["initialization_id"]},
                )
            )
            assert completion_payload["success"] is True, completion_payload
            recipe_execution = completion_payload["recipe_execution"]

            init_payload = _tool_json(
                await client.call_tool("record_pipeline_step", {"op": "init"})
            )
            assert init_payload["success"] is True, init_payload
            assert init_payload["pipeline_id"] == dispatch_id, init_payload
            assert init_payload["step_count"] > 0, init_payload

            run_payload = _tool_json(
                await client.call_tool(
                    "run_skill",
                    {
                        "skill_command": (
                            "/autoskillit:promote-to-main ${{ inputs.batch_branch }}"
                            " ${{ inputs.base_branch }}"
                        ),
                        "cwd": str(project_dir),
                        "step_name": _STEP,
                        "output_dir": _WRITE_PATH,
                        "recipe_execution_id": recipe_execution["execution_id"],
                        "invocation_template_digest": recipe_execution[
                            "invocation_template_digests"
                        ][_STEP],
                        "skill_inputs": {"batch_branch": "develop", "base_branch": "main"},
                    },
                )
            )
            assert run_payload["success"] is True, run_payload
            receipt_id = str(run_payload["receipt_id"])

            complete_payload = _tool_json(
                await client.call_tool("complete_run_skill_result", {"receipt_id": receipt_id})
            )
            assert complete_payload["success"] is True, complete_payload
            assert complete_payload["tracker"]["success"] is True, complete_payload

    tracker_dir = pipeline_tracker_directory(project_dir)
    assert {p.name for p in tracker_dir.glob("*.json")} == {f"{dispatch_id}.json"}

    tool_ctx.project_dir = project_dir
    tracker_key, tracker_lease = retain_dispatch_tracker_authority(tool_ctx, dispatch_id)
    try:
        _, _, checkpoint, tracker_error = load_dispatch_progress(
            tool_ctx=tool_ctx,
            dispatch_sidecar_path=str(sidecar_path(dispatch_id, project_dir)),
            dispatch_id=dispatch_id,
            backend_name=AGENT_BACKEND_CODEX,
            recipe=_RECIPE,
            tracker_lease=tracker_lease,
        )
    finally:
        with tool_ctx.tracker_leases_lock:
            release_tracker_lease(tool_ctx.tracker_leases, tracker_key)

    assert tracker_error is None, tracker_error
    assert checkpoint is not None
    assert checkpoint.completed_items == [_STEP]
