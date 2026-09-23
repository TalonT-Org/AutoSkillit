"""Credentialed native-Claude proof of failed Agent replacement and Stop release."""

from __future__ import annotations

import json
import os
import shutil
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


def _tool_calls(rows: list[dict[str, Any]], name: str) -> list[dict[str, Any]]:
    return [
        value
        for row in rows
        for value in _walk(row)
        if isinstance(value, dict)
        and value.get("type") == "tool_use"
        and value.get("name") == name
        and isinstance(value.get("id"), str)
    ]


def _tool_results(rows: list[dict[str, Any]], tool_use_id: str) -> list[dict[str, Any]]:
    matches = [
        value
        for row in rows
        for value in _walk(row)
        if isinstance(value, dict)
        and value.get("type") == "tool_result"
        and value.get("tool_use_id") == tool_use_id
    ]
    assert matches
    return matches


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
First invoke the Skill tool with skill "autoskillit:dry-walkthrough" and args "{plan}".
If activation fails, stop this probe and report that failure.
This is a controlled native-hook recovery probe. After activation, perform only this sequence,
one tool call at a time, waiting for each result:
Call open_kitchen with no arguments and call the AutoSkillit declare_join_batch tool with
skill_name "dry-walkthrough",
session_id "{session_id}",
and exactly one assignment label "replacement-worker". Omit top_level_parent so the server uses
the binding-authoritative parent.
Call Agent with subagent_type "{missing_agent}" and a short prompt. It must fail because
that agent type does not exist. Then attempt Bash "printf FAILED_EFFECT" and Write
".autoskillit/temp/failed.md" with content FAILED_WRITE. Both must be denied.
Attempt to end your response with FAILED_STOP_PROBE. When the hook continues the turn,
call the namespaced AutoSkillit declare_join_batch again with the same session, parent, skill,
and one assignment label. Then attempt Bash "printf PENDING_EFFECT" and Write
".autoskillit/temp/pending.md" with content PENDING_WRITE. Both must be denied.
Attempt to end your response with PENDING_STOP_PROBE. When the hook continues the turn,
call Agent once with subagent_type "general-purpose" and ask
it to return exactly VALID_REPLACEMENT. Do not use Task, teams,
background execution, or any extra Agent calls. After it returns, run Bash "printf RELEASED_EFFECT"
and Write ".autoskillit/temp/released.md" with content RELEASED_WRITE. These must succeed.
Then reply LIVE_JOIN_OK.
""".strip()


@_skip_unless_live_gate
def test_native_claude_unknown_agent_replacement_releases_stop(
    tmp_path: Path, native_join_evidence: Path, request: pytest.FixtureRequest
) -> None:
    project = tmp_path / "project"
    plugin = tmp_path / "projected-plugin"
    home = tmp_path / "home"
    claude_config = home / ".claude"
    log_dir = native_join_evidence / "hook-logs"
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
    shutil.copyfile(plugin / ".mcp.json", native_join_evidence / "mcp.json")
    shutil.copyfile(plugin / "hooks" / "hooks.json", native_join_evidence / "hooks.json")
    shutil.copyfile(
        plugin / "skills" / "dry-walkthrough" / "SKILL.md", native_join_evidence / "SKILL.md"
    )
    ledger_path, _lock_path = ledger_paths(resolve_channel_dir(project))

    def preserve_ledger() -> None:
        if ledger_path.is_file():
            shutil.copyfile(ledger_path, native_join_evidence / "ledger.json")

    request.addfinalizer(preserve_ledger)
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
    assert not resolve_binding_path(str(project), session_id).exists()
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
        capture_dir=native_join_evidence / "native",
    )
    assert completed.returncode == 0, completed.stderr[-4_000:].decode("utf-8", errors="replace")
    rows = _json_rows(completed.stdout)
    rendered = completed.stdout.decode("utf-8", errors="replace")
    binding = read_binding(resolve_binding_path(str(project), session_id))
    assert binding is not None and binding.binding_valid, rendered[-8_000:]
    assert binding.managed_parent_id == "top_level"
    assert binding.managed_leaf_id == ""
    skill_calls = _tool_calls(rows, "Skill")
    assert len(skill_calls) == 1
    assert skill_calls[0]["input"]["skill"] == "autoskillit:dry-walkthrough"
    assert not any(row.get("is_error") for row in _tool_results(rows, skill_calls[0]["id"]))
    calls = _tool_calls(rows, "Agent")
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

    diagnostics = [row for row in _diagnostics(log_dir) if row.get("session_id") == session_id]
    missing_id = str(missing_call["id"])
    valid_id = str(valid_call["id"])
    missing_result = json.dumps(_tool_results(rows, missing_id)).casefold()
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
    assert replacement["session_id"] == session_id
    assert replacement["top_level_parent"] == "top_level"
    assert replacement["skill_name"] == "dry-walkthrough"
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
    failure_index = next(
        i
        for i, row in enumerate(diagnostics)
        if row.get("gate") == "join_settle_guard"
        and row.get("tool_use_id") == missing_id
        and row.get("status") == OUTCOME_FAILURE
    )
    replacement_index = diagnostics.index(replacement)
    success_index = next(
        i
        for i, row in enumerate(diagnostics)
        if row.get("gate") == "join_settle_guard"
        and row.get("tool_use_id") == valid_id
        and row.get("status") == OUTCOME_SUCCESS
    )
    assert failure_index < replacement_index < success_index
    for phase, start, end in (
        ("FAILED", failure_index, replacement_index),
        ("PENDING", replacement_index, success_index),
    ):
        window = diagnostics[start + 1 : end]
        for tool in ("Bash", "Write"):
            effect = next(
                call
                for call in _tool_calls(rows, tool)
                if f"{phase}_" in json.dumps(call.get("input"))
            )
            assert any(
                row.get("gate") == "join_followup_guard"
                and row.get("status") == "block"
                and row.get("tool_use_id") == effect["id"]
                for row in window
            )
            assert any(row.get("is_error") for row in _tool_results(rows, effect["id"]))
        assert any(
            row.get("gate") == "join_stop_guard" and row.get("status") == "block" for row in window
        )
        assert not (project / ".autoskillit" / "temp" / f"{phase.lower()}.md").exists()
    released = next(
        call
        for call in _tool_calls(rows, "Bash")
        if "RELEASED_EFFECT" in json.dumps(call.get("input"))
    )
    released_results = _tool_results(rows, released["id"])
    assert not any(row.get("is_error") for row in released_results)
    assert "RELEASED_EFFECT" in json.dumps(released_results)
    assert (
        project / ".autoskillit" / "temp" / "released.md"
    ).read_text().strip() == "RELEASED_WRITE"
    assert any(
        row.get("gate") == "join_stop_guard" and row.get("status") == "allow"
        for row in diagnostics[success_index + 1 :]
    )

    flag_dir = resolve_channel_dir(project)
    ledger_path, _lock_path = ledger_paths(flag_dir)
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    assert isinstance(ledger, dict)
    assert len(ledger["batches"]) == 2
    active = active_batch(flag_dir, session_id=session_id, top_level_parent="top_level")
    assert active is not None and active.get("wave_outcome") == "complete"
