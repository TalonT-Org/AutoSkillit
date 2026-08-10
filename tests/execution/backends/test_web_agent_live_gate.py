"""Authenticated conformance gate for the terminal live-web Codex role."""

from __future__ import annotations

import json
import os
import re
import subprocess
import tomllib
from datetime import UTC, datetime
from pathlib import Path

import pytest

from autoskillit.core import (
    OUTPUT_DISCIPLINE_DIGEST,
    WEB_EVIDENCE_RESEARCHER_ROLE,
    agent_definition_digest,
    load_agent_definitions,
    pkg_root,
)
from tests.execution.backends._explorer_conformance_assertions import (
    assert_generated_codex_child_delivery,
)
from tests.execution.backends._live_codex_parent import (
    prepare_live_codex_parent,
    run_live_codex_parent,
)
from tests.execution.backends.test_cli_conformance_probes import (
    _collect_generated_child_rollout,
)

pytestmark = [pytest.mark.layer("execution"), pytest.mark.large, pytest.mark.timeout(1200)]

_GATE_ENV = "AUTOSKILLIT_WEB_AGENT_LIVE_GATE"
_ARTIFACT_DIR_ENV = "AUTOSKILLIT_WEB_AGENT_LIVE_GATE_ARTIFACT_DIR"
_CODEX_AUTH_PATH = Path("~/.codex/auth.json").expanduser()
_EXPECTED_CLI_VERSION = "codex-cli 0.147.0"
_FORBIDDEN_TOOL_FRAGMENTS = (
    "apply_patch",
    "browser",
    "computer",
    "exec_command",
    "imagegen",
    "shell",
    "spawn_agent",
    "write_stdin",
)
_NESTED_TOOL_CALL = re.compile(r"\btools\.([A-Za-z0-9_]+)\s*\(")


def _assistant_text(events: list[dict]) -> str:
    fragments: list[str] = []
    for event in events:
        payload = event.get("payload", {})
        if not isinstance(payload, dict):
            continue
        if event.get("type") == "response_item" and payload.get("type") == "message":
            if payload.get("role") != "assistant":
                continue
            content = payload.get("content", [])
            if isinstance(content, str):
                fragments.append(content)
            elif isinstance(content, list):
                fragments.extend(
                    str(block.get("text", "")) for block in content if isinstance(block, dict)
                )
        elif event.get("type") == "agent_message":
            fragments.append(str(payload.get("text") or payload.get("message") or ""))
    return "\n".join(fragments)


def _observed_tool_names(events: list[dict]) -> set[str]:
    names: set[str] = set()
    for event in events:
        payload = event.get("payload", {})
        if not isinstance(payload, dict):
            continue
        payload_type = str(payload.get("type", ""))
        name = str(payload.get("name", ""))
        if "call" not in payload_type or payload_type.endswith("_output"):
            continue
        if payload_type == "custom_tool_call" and name == "exec":
            source = str(payload.get("input") or payload.get("arguments") or "")
            names.update(_NESTED_TOOL_CALL.findall(source))
        else:
            names.add(name or payload_type)
    return names


def _forbidden_called_tools(tool_names: set[str]) -> set[str]:
    return {
        name
        for name in tool_names
        if any(fragment in name.lower() for fragment in _FORBIDDEN_TOOL_FRAGMENTS)
    }


def _nested_exec_call(source: str) -> dict:
    return {
        "type": "response_item",
        "payload": {"type": "custom_tool_call", "name": "exec", "input": source},
    }


def test_observed_tool_names_records_nested_calls_not_gateway_or_catalog() -> None:
    events = [
        _nested_exec_call(
            "const result = await tools.web__run({search_query: [{q: 'Python'}]}); "
            "text(JSON.stringify(result));"
        ),
        {
            "type": "response_item",
            "payload": {"type": "custom_tool_call_output", "output": "search results"},
        },
        {
            "type": "tool_catalog",
            "payload": {"tools": ["exec_command", "spawn_agent", "computer_use"]},
        },
    ]

    assert _observed_tool_names(events) == {"web__run"}


def test_view_image_is_an_allowed_nested_call() -> None:
    tool_names = _observed_tool_names(
        [_nested_exec_call("await tools.view_image({path: '/tmp/chart.png'});")]
    )
    assert tool_names == {"view_image"}
    assert not _forbidden_called_tools(tool_names)


@pytest.mark.parametrize("tool_name", ["exec_command", "browser_control", "spawn_agent"])
def test_forbidden_nested_calls_are_rejected(tool_name: str) -> None:
    event = _nested_exec_call(f"await tools.{tool_name}({{}});")
    assert _forbidden_called_tools(_observed_tool_names([event])) == {tool_name}


@pytest.mark.smoke
@pytest.mark.skipif(
    not os.environ.get(_GATE_ENV)
    or (
        not os.environ.get("CODEX_API_KEY")
        and not os.environ.get("OPENAI_API_KEY")
        and not _CODEX_AUTH_PATH.exists()
    ),
    reason="run through task test-smoke-codex-web-agent-live-gate with Codex auth",
)
def test_live_web_agent_is_luna_xhigh_read_only_leaf(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cli_version = subprocess.run(  # noqa: S603
        ["codex", "--version"],
        capture_output=True,
        check=True,
        text=True,
        timeout=30,
    ).stdout.strip()
    assert cli_version == _EXPECTED_CLI_VERSION

    definition = next(
        definition
        for definition in load_agent_definitions(pkg_root() / "agents")
        if definition.name == WEB_EVIDENCE_RESEARCHER_ROLE
    )
    digest = agent_definition_digest(definition)
    prepared = prepare_live_codex_parent(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        source_auth=_CODEX_AUTH_PATH,
        agent_defs=(definition,),
    )
    session_config = tomllib.loads(
        (prepared.session_home / "config.toml").read_text(encoding="utf-8")
    )
    registered = session_config["agents"][WEB_EVIDENCE_RESEARCHER_ROLE]
    assert registered["config_file"] == (f"agents/{WEB_EVIDENCE_RESEARCHER_ROLE}.toml")
    role_config_path = prepared.session_home / registered["config_file"]
    role_config = tomllib.loads(role_config_path.read_text(encoding="utf-8"))
    assert role_config["web_search"] == "live"
    assert role_config["agents"] == {"enabled": False}

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    subprocess.run(  # noqa: S603
        ["git", "init", "--quiet"], cwd=workspace, check=True, timeout=30
    )
    prompt = f"""
Spawn exactly one child with agent_type={WEB_EVIDENCE_RESEARCHER_ROLE!r},
fork_turns='none', and task_name='live_web_evidence'. Do not pass model or
reasoning_effort. Ask it to use live web search to identify the current stable
Python release from an official Python source. Require it to make at least one
shared functions.exec gateway call whose cell directly awaits tools.web__run
with a search_query, and return at least one exact
https://docs.python.org/ URL through its terminal verdict envelope. Wait for the
child, then return its URL and the marker WEB_AGENT_GATE_COMPLETE. Do not search
the web in the parent.
""".strip()
    result = run_live_codex_parent(
        env=prepared.env,
        cwd=workspace,
        model="gpt-5.6-sol",
        prompt=prompt,
        timeout=int(os.environ.get("WEB_AGENT_LIVE_GATE_TIMEOUT", "900")),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        extra_overrides=("web_search=disabled",),
    )
    assert result.returncode == 0, (result.stdout + "\n" + result.stderr)[-16000:]

    rollout = _collect_generated_child_rollout(result, session_home=prepared.session_home)
    identity = assert_generated_codex_child_delivery(
        rollout.parent_events,
        rollout.child_events,
        parent_id=rollout.parent_id,
        agent_role=WEB_EVIDENCE_RESEARCHER_ROLE,
        output_discipline_digest=OUTPUT_DISCIPLINE_DIGEST,
        expected_parent_model="gpt-5.6-sol",
        expected_parent_sandbox_mode="read-only",
        expected_model="gpt-5.6-luna",
        expected_reasoning_effort="xhigh",
        expected_sandbox_mode="read-only",
        expected_definition_digest=digest,
    )
    tool_names = _observed_tool_names(rollout.child_events)
    assert "web__run" in tool_names, tool_names
    assert not _forbidden_called_tools(tool_names)
    child_text = _assistant_text(rollout.child_events)
    returned_urls = sorted(set(re.findall(r"https://docs\.python\.org/[^\s)>\]]+", child_text)))
    assert returned_urls, child_text[-4000:]

    artifact_dir = Path(os.environ[_ARTIFACT_DIR_ENV])
    artifact_dir.mkdir(parents=True, exist_ok=True)
    evidence = {
        "schema_version": 1,
        "contract": "live-web-agent-codex-0.147.0",
        "generated_at": datetime.now(UTC).isoformat(),
        "cli_version": cli_version,
        "parent_id": identity.parent_id,
        "child_id": identity.child_id,
        "agent_role": identity.agent_role,
        "agent_config_path": registered["config_file"],
        "definition_digest": digest,
        "parent_model": identity.parent_model,
        "parent_web_search": "disabled",
        "child_model": identity.model,
        "child_reasoning_effort": identity.reasoning_effort,
        "child_sandbox_mode": identity.sandbox_mode,
        "child_web_search": role_config["web_search"],
        "child_agents_enabled": role_config["agents"]["enabled"],
        "disabled_features": sorted(definition.codex.disabled_features),
        "observed_tool_names": sorted(tool_names),
        "returned_urls": returned_urls[:10],
    }
    (artifact_dir / "live-web-agent-gate.json").write_text(
        json.dumps(evidence, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
