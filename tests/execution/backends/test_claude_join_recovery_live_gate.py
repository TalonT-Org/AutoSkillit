"""Credentialed native-Claude proof of failed Agent replacement and Stop release."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from autoskillit.core import (
    MARKETPLACE_PREFIX,
    SkillExecutionRole,
    pkg_root,
    write_versioned_json,
)
from autoskillit.execution.backends._codex_catalog import run_owned_bounded
from autoskillit.hook_registry import render_hooks_json_text
from autoskillit.hooks._join import OUTCOME_FAILURE, OUTCOME_SUCCESS
from autoskillit.hooks._join_ledger import active_batch, ledger_paths
from autoskillit.hooks._session_binding import (
    read_binding,
    resolve_binding_path,
    resolve_channel_dir,
)
from autoskillit.workspace import (
    DefaultSkillResolver,
    EffectiveSkillCatalog,
    SkillCatalogEntry,
    SkillProjectionContext,
    materialize_sanitized_plugin_root,
    write_generated_hooks_json,
)
from autoskillit.workspace._projected_artifact import projected_plugin_artifact_digest
from tests.conftest import production_interpreter_env

pytestmark = [
    pytest.mark.layer("execution"),
    pytest.mark.large,
    pytest.mark.smoke,
    pytest.mark.timeout(1200),
    pytest.mark.ambient_env("ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN"),
]

_LIVE_ENV = "AUTOSKILLIT_CLAUDE_JOIN_RECOVERY_LIVE"
_AUTH_ENV_NAMES = ("ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN")
_SOURCE_CREDENTIALS = Path("~/.claude/.credentials.json").expanduser()
_MAX_CAPTURE_BYTES = 4 * 1024 * 1024
_skip_unless_live_gate = pytest.mark.skipif(
    os.environ.get(_LIVE_ENV) != "1"
    or shutil.which("claude") is None
    or not any(os.environ.get(name) for name in _AUTH_ENV_NAMES)
    and not _SOURCE_CREDENTIALS.is_file(),
    reason=f"Set {_LIVE_ENV}=1 and provide Claude authentication for this live gate",
)


def _json_rows(payload: bytes) -> list[dict[str, Any]]:
    assert len(payload) <= _MAX_CAPTURE_BYTES
    rows: list[dict[str, Any]] = []
    for line in payload.decode("utf-8", errors="replace").splitlines():
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            rows.append(value)
    return rows


def _walk(value: object) -> list[object]:
    values = [value]
    if isinstance(value, dict):
        for child in value.values():
            values.extend(_walk(child))
    elif isinstance(value, list):
        for child in value:
            values.extend(_walk(child))
    return values


def _agent_calls(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        value
        for row in rows
        for value in _walk(row)
        if isinstance(value, dict)
        and value.get("type") == "tool_use"
        and value.get("name") == "Agent"
        and isinstance(value.get("id"), str)
    ]


def _tool_result_text(rows: list[dict[str, Any]], tool_use_id: str) -> str:
    matches = [
        value
        for row in rows
        for value in _walk(row)
        if isinstance(value, dict)
        and value.get("type") == "tool_result"
        and value.get("tool_use_id") == tool_use_id
    ]
    assert matches
    return json.dumps(matches, sort_keys=True)


def _diagnostics(log_dir: Path) -> list[dict[str, Any]]:
    path = log_dir / "join_diagnostics.jsonl"
    assert path.stat().st_size <= _MAX_CAPTURE_BYTES
    return [
        value
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and isinstance((value := json.loads(line)), dict)
    ]


def _build_projected_plugin(plugin: Path, project: Path) -> Path:
    source = DefaultSkillResolver().resolve("dry-walkthrough")
    assert source is not None and source.semantic_plan is not None
    catalog = EffectiveSkillCatalog(
        skills=(SkillCatalogEntry.from_skill_info(source),),
        execution_role=SkillExecutionRole.SESSION,
    )
    manifest_path = materialize_sanitized_plugin_root(
        pkg_root(),
        plugin,
        catalog,
        SkillProjectionContext(cwd=project, catalog=catalog),
        mcp_tool_prefix=MARKETPLACE_PREFIX,
    )
    assert manifest_path.is_file()
    write_generated_hooks_json(plugin)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest.update(
        {
            "artifact_digest": projected_plugin_artifact_digest(plugin),
            "incarnation_id": f"live-{uuid4().hex}",
        }
    )
    write_versioned_json(manifest_path, manifest, schema_version=2)
    hooks_json = plugin / "hooks" / "hooks.json"
    assert hooks_json.read_text(encoding="utf-8") == render_hooks_json_text()
    assert (plugin / "skills" / "dry-walkthrough" / "SKILL.md").is_file()
    return manifest_path


def _configure_mcp(plugin: Path, project: Path, log_dir: Path) -> None:
    executable_path = shutil.which("autoskillit")
    assert executable_path is not None
    executable = Path(executable_path)
    assert executable.is_file()
    (plugin / ".mcp.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "autoskillit": {
                        "command": str(executable),
                        "env": {
                            "AUTOSKILLIT_AGENT_BACKEND": "claude-code",
                            "AUTOSKILLIT_FEATURES__EXPERIMENTAL_ENABLED": "true",
                            "AUTOSKILLIT_LOG_DIR": str(log_dir),
                            "AUTOSKILLIT_STATE_ROOT": str(project),
                        },
                    }
                }
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def _prompt(session_id: str, plan: Path, missing_agent: str) -> str:
    return f"""
Call open_kitchen with no arguments and call the AutoSkillit declare_join_batch tool with
skill_name "dry-walkthrough",
session_id "{session_id}",
and exactly one assignment label "replacement-worker". Omit top_level_parent so the server uses
the binding-authoritative parent.
Call the Agent tool with subagent_type "{missing_agent}" and a short prompt. It must fail because
that agent type does not exist. After the failure, call declare_join_batch again with the exact
same session, parent, skill, and one assignment label. Attempt to end your response with
PENDING_STOP_PROBE before calling another tool; the Stop hook must reject that pending wave.
When the hook continues the turn, call Agent once with subagent_type "general-purpose" and ask
it to return exactly VALID_REPLACEMENT. Do not use Task, teams,
background execution, or any extra Agent calls. After the valid Agent returns, reply LIVE_JOIN_OK.
""".strip()


def _seed_projected_skill_binding(
    plugin: Path,
    project: Path,
    session_id: str,
    env: dict[str, str],
) -> None:
    payload = {
        "hook_event_name": "UserPromptExpansion",
        "expansion_type": "slash_command",
        "command_name": "dry-walkthrough",
        "session_id": session_id,
        "cwd": str(project),
    }
    completed = subprocess.run(
        [sys.executable, "-B", str(plugin / "hooks" / "_dispatch.py"), "skill_load_post_hook"],
        cwd=project,
        env=env,
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr


@_skip_unless_live_gate
def test_native_claude_unknown_agent_replacement_releases_stop(tmp_path: Path) -> None:
    project = tmp_path / "project"
    plugin = tmp_path / "projected-plugin"
    home = tmp_path / "home"
    claude_config = home / ".claude"
    log_dir = tmp_path / "hook-logs"
    project.mkdir()
    claude_config.mkdir(parents=True)
    (project / ".autoskillit" / "temp").mkdir(parents=True)
    plan = project / "plan.md"
    plan.write_text("# Live recovery probe\n", encoding="utf-8")
    if _SOURCE_CREDENTIALS.is_file():
        destination = claude_config / ".credentials.json"
        shutil.copyfile(_SOURCE_CREDENTIALS, destination)
        destination.chmod(0o600)

    manifest_path = _build_projected_plugin(plugin, project)
    _configure_mcp(plugin, project, log_dir)
    assert manifest_path.parent == plugin.parent
    assert manifest_path.name == f".{plugin.name}.autoskillit-projection.json"

    session_id = str(uuid4())
    missing_agent = f"autoskillit-missing-{uuid4().hex}"
    env = production_interpreter_env()
    env.update(
        {
            "HOME": str(home),
            "CLAUDE_CONFIG_DIR": str(claude_config),
            "AUTOSKILLIT_AGENT_BACKEND": "claude-code",
            "AUTOSKILLIT_LOG_DIR": str(log_dir),
            "AUTOSKILLIT_STATE_ROOT": str(project),
        }
    )
    _seed_projected_skill_binding(plugin, project, session_id, env)
    binding = read_binding(resolve_binding_path(str(project), session_id))
    assert binding is not None and binding.binding_valid
    assert binding.managed_parent_id == "top_level"
    assert binding.managed_leaf_id == ""
    command = (
        "claude",
        "-p",
        "--dangerously-skip-permissions",
        "--plugin-dir",
        str(plugin),
        "--session-id",
        session_id,
        "--output-format",
        "stream-json",
        "--verbose",
        "--include-hook-events",
        _prompt(session_id, plan, missing_agent),
    )
    completed = run_owned_bounded(
        command,
        cwd=project,
        environment=env,
        deadline=time.monotonic()
        + int(os.environ.get("AUTOSKILLIT_CLAUDE_JOIN_RECOVERY_TIMEOUT", "900")),
        stdout_limit=_MAX_CAPTURE_BYTES,
    )
    assert completed.returncode == 0, completed.stderr[-4_000:].decode("utf-8", errors="replace")
    rows = _json_rows(completed.stdout)
    rendered = completed.stdout.decode("utf-8", errors="replace")
    calls = _agent_calls(rows)
    assert len(calls) == 2, rendered[-8_000:]
    missing_call = next(
        call for call in calls if call.get("input", {}).get("subagent_type") == missing_agent
    )
    valid_call = next(
        call for call in calls if call.get("input", {}).get("subagent_type") == "general-purpose"
    )
    assert missing_agent in rendered
    assert "PostToolUseFailure" in rendered
    assert "VALID_REPLACEMENT" in rendered

    diagnostics = _diagnostics(log_dir)
    missing_id = str(missing_call["id"])
    valid_id = str(valid_call["id"])
    missing_result = _tool_result_text(rows, missing_id).casefold()
    assert any(token in missing_result for token in ("unknown", "not found", "does not exist"))
    assert any(
        row.get("gate") == "join_claim_guard"
        and row.get("status") == "claim"
        and row.get("tool_use_id") == missing_id
        for row in diagnostics
    )
    assert any(
        row.get("gate") == "join_settle_guard"
        and row.get("status") == OUTCOME_FAILURE
        and row.get("tool_use_id") == missing_id
        for row in diagnostics
    )
    replacement = next(row for row in diagnostics if row.get("status") == "replacement_batch")
    assert replacement.get("original_join_batch_id")
    assert replacement.get("replacement_join_batch_id")
    assert any(
        row.get("gate") == "join_claim_guard"
        and row.get("status") == "claim"
        and row.get("tool_use_id") == valid_id
        for row in diagnostics
    )
    assert any(
        row.get("gate") == "join_settle_guard"
        and row.get("status") == OUTCOME_SUCCESS
        and row.get("tool_use_id") == valid_id
        for row in diagnostics
    )
    stop_statuses = {
        row.get("status") for row in diagnostics if row.get("gate") == "join_stop_guard"
    }
    assert {"block", "allow"}.issubset(stop_statuses)

    flag_dir = resolve_channel_dir(project)
    ledger_path, _lock_path = ledger_paths(flag_dir)
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    assert isinstance(ledger, dict)
    assert len(ledger["batches"]) == 2
    active = active_batch(flag_dir, session_id=session_id, top_level_parent="top_level")
    assert active is not None and active.get("wave_outcome") == "complete"
