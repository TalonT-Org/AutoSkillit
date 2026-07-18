"""Env-gated end-to-end output-budget investigation probes.

Unlike the one-prompt CLI conformance probe, this harness constructs the real
server composition root, opens the kitchen, and dispatches ``/investigate``
through ``run_skill``. It requires isolated environment-provided credentials
and never reads the user's CLI configuration.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import NamedTuple
from unittest.mock import AsyncMock

import pytest

from autoskillit.config import (
    AgentBackendConfig,
    AutomationConfig,
    QuotaGuardConfig,
)
from autoskillit.core import DirectInstall, pkg_root
from autoskillit.execution.backends._codex_config import ensure_codex_mcp_registered
from autoskillit.execution.backends._codex_hooks import sync_hooks_to_codex_config
from autoskillit.execution.process import DefaultSubprocessRunner
from autoskillit.hook_registry import generate_hooks_json
from autoskillit.server._factory import make_context
from autoskillit.server.tools.tools_execution import run_skill
from autoskillit.server.tools.tools_kitchen import close_kitchen, open_kitchen

pytestmark = [pytest.mark.layer("server"), pytest.mark.large, pytest.mark.smoke]

_INVESTIGATION_PATH_RE = re.compile(r"investigation_path\s*=\s*(/\S+)")
_PROBE_RECIPE_NAME = "output-budget-deep-investigate-probe"
_PROBE_STEP_NAME = "output_budget_deep_investigate_probe"
_FIXTURE_SENTINELS = (
    "HEAD-SENTINEL::deep-investigate",
    "MIDDLE-SENTINEL::deep-investigate",
    "TAIL-SENTINEL::deep-investigate",
)
_REPORT_REQUIRED_SECTIONS = (
    "## Summary",
    "## Affected Components",
    "## Data Flow",
    "## Test Gap Analysis",
    "## Scope Boundary",
    "## Recommendations",
)
_CODEX_AUTH_PATH = Path("~/.codex/auth.json").expanduser()
_CLAUDE_CREDENTIALS_PATH = Path("~/.claude/.credentials.json").expanduser()


def _has_codex_credentials() -> bool:
    return bool(
        os.environ.get("CODEX_API_KEY")
        or os.environ.get("OPENAI_API_KEY")
        or _CODEX_AUTH_PATH.is_file()
    )


def _has_claude_credentials() -> bool:
    return bool(
        os.environ.get("ANTHROPIC_API_KEY")
        or os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")
        or _CLAUDE_CREDENTIALS_PATH.is_file()
    )


_BACKEND_CASES = [
    pytest.param(
        "codex",
        marks=pytest.mark.skipif(
            not os.environ.get("CODEX_SMOKE_TEST")
            or not shutil.which("codex")
            or not _has_codex_credentials(),
            reason="Codex E2E requires CODEX_SMOKE_TEST and an environment API key",
        ),
    ),
    pytest.param(
        "claude-code",
        marks=pytest.mark.skipif(
            not os.environ.get("CLAUDE_CODE_SMOKE_TEST")
            or not shutil.which("claude")
            or not _has_claude_credentials(),
            reason="Claude E2E requires CLAUDE_CODE_SMOKE_TEST and an environment credential",
        ),
    ),
]


def _fixture_payload(size: int = 520_000) -> str:
    head = f"{_FIXTURE_SENTINELS[0]}\n"
    middle = f"\n{_FIXTURE_SENTINELS[1]}\n"
    tail = f"\n{_FIXTURE_SENTINELS[2]}"
    filler = size - len(head) - len(middle) - len(tail)
    before_middle = filler // 2
    payload = head + ("a" * before_middle) + middle + ("z" * (filler - before_middle)) + tail
    assert len(payload.encode("utf-8")) == size
    return payload


def _init_fixture_repo(path: Path) -> Path:
    path.mkdir()
    payload_path = path / "large-output-fixture.txt"
    payload_path.write_text(_fixture_payload(), encoding="utf-8")
    (path / "README.md").write_text(
        "# Output budget fixture\n\nInvestigate lossless handling of the large fixture.\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "init", "-q"], cwd=path, check=True, timeout=10)
    subprocess.run(["git", "config", "user.email", "probe@example.invalid"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Output Budget Probe"], cwd=path, check=True)
    subprocess.run(["git", "add", "."], cwd=path, check=True)
    subprocess.run(["git", "commit", "-qm", "fixture"], cwd=path, check=True, timeout=10)
    return payload_path


def _configure_isolated_cli(
    backend: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_codex_auth = _CODEX_AUTH_PATH
    source_claude_credentials = _CLAUDE_CREDENTIALS_PATH
    home = tmp_path / "home"
    codex_home = home / ".codex"
    claude_config = home / ".claude"
    for directory in (home, codex_home, claude_config):
        directory.mkdir(parents=True)

    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(claude_config))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(home / ".local" / "share"))
    venv_bin = Path(__file__).resolve().parents[2] / ".venv" / "bin"
    monkeypatch.setenv("PATH", f"{venv_bin}{os.pathsep}{os.environ.get('PATH', '')}")
    monkeypatch.setenv("AUTOSKILLIT_FEATURES__EXPERIMENTAL_ENABLED", "true")

    if backend == "codex":
        if source_codex_auth.is_file():
            (codex_home / "auth.json").symlink_to(source_codex_auth.resolve())
        config_path = codex_home / "config.toml"
        ensure_codex_mcp_registered(config_path=config_path)
        sync_hooks_to_codex_config(config_path=config_path)
    else:
        if source_claude_credentials.is_file():
            (claude_config / ".credentials.json").symlink_to(source_claude_credentials.resolve())
        (claude_config / "settings.json").write_text(
            json.dumps(generate_hooks_json(), indent=2) + "\n",
            encoding="utf-8",
        )


def _assert_investigation_result(raw_result: str, fixture_path: Path) -> Path:
    result = json.loads(raw_result)
    assert result["success"] is True, result
    match = _INVESTIGATION_PATH_RE.search(str(result.get("result", "")))
    assert match is not None, f"missing investigation_path token: {result}"
    report_path = Path(match.group(1))
    assert report_path.is_file(), f"investigation report not found: {report_path}"
    report = report_path.read_text(encoding="utf-8")
    for section in _REPORT_REQUIRED_SECTIONS:
        assert section in report, f"report missing coherent synthesis section {section!r}"
    assert str(fixture_path) in report or fixture_path.name in report
    assert "Deep Analysis" in report
    for sentinel in _FIXTURE_SENTINELS:
        assert sentinel in report, f"report omitted bounded fixture evidence {sentinel!r}"
    return report_path


class _WorkflowEvent(NamedTuple):
    kind: str
    name: str
    call_id: str
    content: str
    source_index: int


def _session_index_offset(index_path: Path) -> int:
    return index_path.stat().st_size if index_path.exists() else 0


def _read_appended_session_rows(index_path: Path, offset: int) -> list[dict]:
    with index_path.open("rb") as handle:
        handle.seek(offset)
        raw_lines = handle.read().decode("utf-8").splitlines()
    rows: list[dict] = []
    for line in raw_lines:
        if not line.strip():
            continue
        row = json.loads(line)
        assert isinstance(row, dict)
        rows.append(row)
    return rows


def _select_probe_session_row(
    rows: list[dict],
    *,
    result: dict,
    backend: str,
    fixture_repo: Path,
    configured_model: str,
) -> dict:
    matches = [
        row
        for row in rows
        if row.get("session_id") == result.get("session_id")
        and row.get("step_name") == _PROBE_STEP_NAME
        and row.get("cwd") == str(fixture_repo)
        and row.get("backend") == backend
    ]
    assert len(matches) == 1, f"expected one new probe session row, got {matches}"
    row = matches[0]
    assert row["success"] is True
    assert row["backend_override_source"] == "explicit_config"
    assert row["recipe_name"] == _PROBE_RECIPE_NAME
    assert row["configured_model"] == configured_model
    return row


def _read_raw_events(path_value: object) -> list[dict]:
    assert isinstance(path_value, str) and path_value, "session index omitted backend log path"
    path = Path(path_value)
    assert path.is_file(), f"backend log does not exist: {path}"
    events: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            event = json.loads(line)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(event, dict):
            events.append(event)
    return events


def _assert_claude_200k_provenance(raw_events: list[dict]) -> None:
    observed_models = {
        str(message.get("model", ""))
        for event in raw_events
        if event.get("type") == "assistant"
        and event.get("isSidechain") is not True
        and isinstance((message := event.get("message", {})), dict)
        and message.get("model")
    }
    assert observed_models, "persisted Claude log contains no parent assistant model"
    assert all("sonnet" in model.lower() for model in observed_models)
    assert all("[1m]" not in model for model in observed_models)
    raw_text = "\n".join(json.dumps(event, sort_keys=True).lower() for event in raw_events)
    assert "prompt is too long" not in raw_text
    assert "context_exhaust" not in raw_text


def _content_text(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(part for item in value if (part := _content_text(item)))
    if isinstance(value, dict):
        text_parts = []
        for key in ("text", "content", "output_text"):
            if key in value and (part := _content_text(value[key])):
                text_parts.append(part)
        return "\n".join(text_parts)
    return ""


def _normalize_workflow_events(backend: str, raw_events: list[dict]) -> list[_WorkflowEvent]:
    normalized: list[_WorkflowEvent] = []
    for index, event in enumerate(raw_events):
        if backend == "claude-code":
            if event.get("isSidechain") is True:
                continue
            record_type = event.get("type")
            message = event.get("message", {})
            if not isinstance(message, dict):
                continue
            blocks = message.get("content", [])
            if not isinstance(blocks, list):
                continue
            for block in blocks:
                if not isinstance(block, dict):
                    continue
                block_type = block.get("type")
                if record_type == "assistant" and block_type == "text":
                    normalized.append(
                        _WorkflowEvent("text", "", "", str(block.get("text", "")), index)
                    )
                elif record_type == "assistant" and block_type == "tool_use":
                    name = str(block.get("name", ""))
                    kind = "launch" if name in {"Agent", "Task"} else "tool_call"
                    normalized.append(
                        _WorkflowEvent(
                            kind,
                            name,
                            str(block.get("id", "")),
                            json.dumps(block.get("input", {}), sort_keys=True),
                            index,
                        )
                    )
                elif record_type == "user" and block_type == "tool_result":
                    normalized.append(
                        _WorkflowEvent(
                            "completion",
                            "",
                            str(block.get("tool_use_id", "")),
                            _content_text(block.get("content", "")),
                            index,
                        )
                    )
        elif backend == "codex":
            if event.get("type") != "response_item":
                continue
            payload = event.get("payload", {})
            if not isinstance(payload, dict):
                continue
            payload_type = payload.get("type")
            if payload_type == "function_call":
                name = str(payload.get("name", ""))
                kind = "launch" if name in {"spawn_agent", "followup_task"} else "tool_call"
                normalized.append(
                    _WorkflowEvent(
                        kind,
                        name,
                        str(payload.get("call_id", "")),
                        str(payload.get("arguments", "")),
                        index,
                    )
                )
            elif payload_type == "function_call_output":
                normalized.append(
                    _WorkflowEvent(
                        "completion",
                        "",
                        str(payload.get("call_id", "")),
                        str(payload.get("output", "")),
                        index,
                    )
                )
            elif payload_type == "message" and payload.get("role") == "assistant":
                normalized.append(
                    _WorkflowEvent(
                        "text",
                        "",
                        "",
                        _content_text(payload.get("content", [])),
                        index,
                    )
                )
            elif payload_type == "agent_message":
                content = _content_text(payload.get("content", []))
                sender_match = re.search(r"(?m)^Sender:\s*(\S+)", content)
                normalized.append(
                    _WorkflowEvent(
                        "agent_result",
                        sender_match.group(1) if sender_match else "",
                        "",
                        content,
                        index,
                    )
                )
            elif payload_type == "custom_tool_call":
                name = str(payload.get("name", ""))
                content = str(payload.get("input", ""))
                if "inter-batch synthesis:" in content.lower():
                    normalized.append(_WorkflowEvent("text", name, "", content, index))
                if name in {"apply_patch", "tools.apply_patch"} or "tools.apply_patch" in content:
                    normalized.append(
                        _WorkflowEvent(
                            "tool_call",
                            "apply_patch",
                            str(payload.get("call_id", "")),
                            content,
                            index,
                        )
                    )
        else:  # pragma: no cover - parametrization is sealed above
            raise AssertionError(f"unsupported backend {backend}")
    return normalized


def _json_object(value: str) -> dict:
    try:
        parsed = json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _terminal_completion_positions(events: list[_WorkflowEvent]) -> dict[str, int]:
    completions = {event.call_id: event for event in events if event.kind == "completion"}
    agent_results = [event for event in events if event.kind == "agent_result"]
    terminal_positions: dict[str, int] = {}
    for launch in (event for event in events if event.kind == "launch"):
        immediate = completions.get(launch.call_id)
        if immediate is None:
            continue
        if launch.name not in {"spawn_agent", "followup_task"}:
            terminal_positions[launch.call_id] = immediate.source_index
            continue
        if launch.name == "spawn_agent":
            target = _json_object(immediate.content).get("task_name")
        else:
            target = _json_object(launch.content).get("target")
        if not isinstance(target, str) or not target:
            continue
        for result in agent_results:
            if result.name == target and result.source_index > launch.source_index:
                terminal_positions[launch.call_id] = result.source_index
                break
    return terminal_positions


def _successful_launches(events: list[_WorkflowEvent]) -> list[_WorkflowEvent]:
    completions = {event.call_id: event for event in events if event.kind == "completion"}
    successful = []
    for launch in (event for event in events if event.kind == "launch"):
        immediate = completions.get(launch.call_id)
        if immediate is None:
            continue
        if launch.name != "spawn_agent" or _json_object(immediate.content).get("task_name"):
            successful.append(launch)
    return successful


def _assert_deep_workflow_evidence(
    events: list[_WorkflowEvent],
    report_path: Path,
) -> None:
    launches = _successful_launches(events)
    assert 2 <= len(launches) <= 16, f"unexpected subagent launch count: {len(launches)}"
    completion_positions = _terminal_completion_positions(events)
    for launch in launches:
        assert launch.call_id in completion_positions, (
            f"subagent launch {launch.call_id} has no terminal result"
        )
        assert completion_positions[launch.call_id] > launch.source_index

    synthesis_events = [
        event
        for event in events
        if event.kind == "text" and "inter-batch synthesis:" in event.content.lower()
    ]
    assert synthesis_events, "deep workflow emitted no inter-batch synthesis"
    first_synthesis = synthesis_events[0].source_index
    first_wave = [launch for launch in launches if launch.source_index < first_synthesis]
    later_waves = [launch for launch in launches if launch.source_index > first_synthesis]
    assert first_wave and later_waves, "deep workflow did not execute two agent waves"
    assert all(completion_positions[launch.call_id] < first_synthesis for launch in first_wave)

    report_writes = [
        event
        for event in events
        if event.kind == "tool_call"
        and (str(report_path) in event.content or report_path.name in event.content)
    ]
    assert report_writes, f"raw backend log has no report publication for {report_path}"
    report_write_index = report_writes[0].source_index
    later_exploration = [
        launch for launch in later_waves if launch.source_index < report_write_index
    ]
    assert later_exploration, "deep workflow launched no later exploration wave"
    assert all(
        completion_positions[launch.call_id] < report_write_index for launch in later_exploration
    ), "later agent wave did not complete before report publication"

    validators = [launch for launch in launches if launch.source_index > report_write_index]
    assert len(validators) >= 2, f"expected at least two D6 validators, got {len(validators)}"
    assert all(launch.source_index > report_write_index for launch in validators)
    assert all(completion_positions[launch.call_id] > report_write_index for launch in validators)

    assistant_text = [event for event in events if event.kind == "text" and event.content.strip()]
    assert assistant_text, "raw backend log contains no assistant text"
    final_text = assistant_text[-1].content
    token = f"investigation_path = {report_path}"
    assert token in final_text, f"final assistant event omitted {token!r}"
    suffix = final_text.split(token, 1)[1].strip()
    assert not suffix or re.fullmatch(r"%%ORDER_UP::[A-Za-z0-9_-]+%%", suffix), (
        f"unexpected assistant text after investigation_path token: {suffix!r}"
    )


@pytest.mark.anyio
@pytest.mark.timeout(4500)
@pytest.mark.parametrize("backend", _BACKEND_CASES)
async def test_deep_investigate_completes_with_bounded_large_evidence(
    backend: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Real kitchen + run_skill dispatch completes and emits a validated report path."""
    fixture_repo = tmp_path / "fixture-repo"
    fixture_path = _init_fixture_repo(fixture_repo)
    _configure_isolated_cli(backend, tmp_path, monkeypatch)

    config = AutomationConfig(
        agent_backend=AgentBackendConfig(
            backend=backend,
            step_overrides={_PROBE_STEP_NAME: backend},
        ),
        quota_guard=QuotaGuardConfig(enabled=False),
        features={"codex_backend": backend == "codex"},
        experimental_enabled=True,
    )
    tool_ctx = make_context(
        config,
        runner=DefaultSubprocessRunner(),
        plugin_source=DirectInstall(plugin_dir=pkg_root()),
        project_dir=fixture_repo,
    )
    tool_ctx.config.linux_tracing.log_dir = str(tmp_path / "session-logs")
    tool_ctx.config.linux_tracing.tmpfs_path = str(tmp_path / "shm")

    from autoskillit.server import _state

    monkeypatch.setattr(_state, "_ctx", tool_ctx)
    monkeypatch.setattr(_state, "_startup_ready", None)
    mcp_ctx = AsyncMock()

    kitchen_result = json.loads(await open_kitchen(ctx=mcp_ctx))
    assert kitchen_result["success"] is True, kitchen_result
    assert kitchen_result["kitchen"] == "open"
    tool_ctx.recipe_name = _PROBE_RECIPE_NAME

    index_path = Path(tool_ctx.config.linux_tracing.log_dir) / "sessions.jsonl"
    index_offset = _session_index_offset(index_path)
    requested_model = "sonnet" if backend == "claude-code" else ""
    configured_model = requested_model or "sonnet"

    command = (
        f"/investigate --depth deep Analyze {fixture_path} and the repository paths that produce "
        "or consume it. Use byte-bounded evidence commands, complete every required deep-mode "
        "batch and validation stage, and do not modify tracked files. Prove all three fixture "
        f"sentinels in the final report: {', '.join(_FIXTURE_SENTINELS)}. Write the final report."
    )
    try:
        raw_result = await run_skill(
            command,
            str(fixture_repo),
            model=requested_model,
            step_name=_PROBE_STEP_NAME,
            idle_output_timeout=0,
            ctx=mcp_ctx,
        )
        result = json.loads(raw_result)
        report_path = _assert_investigation_result(raw_result, fixture_path)
        rows = _read_appended_session_rows(index_path, index_offset)
        session_row = _select_probe_session_row(
            rows,
            result=result,
            backend=backend,
            fixture_repo=fixture_repo,
            configured_model=configured_model,
        )
        log_key = "codex_log" if backend == "codex" else "claude_code_log"
        raw_events = _read_raw_events(session_row[log_key])
        if backend == "claude-code":
            _assert_claude_200k_provenance(raw_events)
        workflow_events = _normalize_workflow_events(backend, raw_events)
        _assert_deep_workflow_evidence(workflow_events, report_path)
    finally:
        await close_kitchen(ctx=mcp_ctx)
