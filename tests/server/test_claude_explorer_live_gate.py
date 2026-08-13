"""Live Claude parent/child proof for request-correlated exploration authority."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from autoskillit.core import (
    BUNDLED_EXPLORER_ROLES,
    EXPLORATION_TOOLS,
    load_bundled_agent_definitions,
)
from autoskillit.hook_registry import generate_hooks_json
from tests.execution._process_group_helpers import _cleanup_owned_process_group

pytestmark = [pytest.mark.layer("server"), pytest.mark.large, pytest.mark.smoke]

_ROOT = Path(__file__).resolve().parents[2]
_PACKAGE_ROOT = _ROOT / "src" / "autoskillit"
_LIVE_ENV = "AUTOSKILLIT_CLAUDE_EXPLORER_LIVE_GATE"
_SOURCE_CREDENTIALS = Path.home() / ".claude" / ".credentials.json"
_has_authentication = bool(
    os.environ.get("ANTHROPIC_API_KEY")
    or os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")
    or _SOURCE_CREDENTIALS.is_file()
)
_skip_unless_live_gate = pytest.mark.skipif(
    os.environ.get(_LIVE_ENV) != "1" or shutil.which("claude") is None or not _has_authentication,
    reason="Claude explorer live gate requires its opt-in, executable, and isolated auth",
)


def _initialize_repository(project: Path) -> None:
    project.mkdir()
    (project / ".autoskillit" / "temp").mkdir(parents=True)
    (project / ".gitignore").write_text(".autoskillit/\n")
    (project / "probe.py").write_text("PROBE_VALUE = 1\n")
    for command in (
        ("git", "init", "-q"),
        ("git", "config", "user.email", "probe@example.invalid"),
        ("git", "config", "user.name", "Explorer Probe"),
        ("git", "add", "."),
        ("git", "commit", "-qm", "probe"),
    ):
        subprocess.run(command, cwd=project, check=True, timeout=10)


def _build_plugin(plugin: Path) -> None:
    (plugin / ".claude-plugin").mkdir(parents=True)
    shutil.copy2(
        _PACKAGE_ROOT / ".claude-plugin" / "plugin.json",
        plugin / ".claude-plugin" / "plugin.json",
    )
    shutil.copytree(_PACKAGE_ROOT / "hooks", plugin / "hooks")
    shutil.copytree(_PACKAGE_ROOT / "agents", plugin / "agents")
    for agent_path in (plugin / "agents").glob("*.md"):
        agent_path.write_text(
            agent_path.read_text().replace(
                "mcp__autoskillit__", "mcp__plugin_autoskillit_autoskillit__"
            )
        )
    (plugin / "hooks" / "hooks.json").write_text(
        json.dumps(generate_hooks_json(), indent=2) + "\n"
    )


def _configure_plugin_mcp(
    plugin: Path, project: Path, evidence: Path, instrumentation: Path
) -> None:
    (plugin / ".mcp.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "autoskillit": {
                        "command": str(_ROOT / ".venv" / "bin" / "autoskillit"),
                        "env": {
                            "AUTOSKILLIT_STATE_ROOT": str(project),
                            "AUTOSKILLIT_SESSION_TYPE": "skill",
                            "AUTOSKILLIT_AGENT_BACKEND": "claude-code",
                            "AUTOSKILLIT_FEATURES__EXPERIMENTAL_ENABLED": "true",
                            "AUTOSKILLIT_CLAUDE_EXPLORER_EVIDENCE": str(evidence),
                            "PYTHONPATH": str(instrumentation),
                        },
                    }
                }
            },
            indent=2,
        )
        + "\n"
    )


def _write_consumer_instrumentation(directory: Path) -> None:
    directory.mkdir()
    (directory / "sitecustomize.py").write_text(
        """import json
import os
from pathlib import Path

from autoskillit.server.tools import tools_exploration
from autoskillit.pipeline import OwnerBoundExplorationContextStore

_original = tools_exploration.consume_exploration_request_record
_original_lookup = OwnerBoundExplorationContextStore.session_scoped_capability
_original_submit = OwnerBoundExplorationContextStore.submit_for_capability
_evidence = Path(os.environ["AUTOSKILLIT_CLAUDE_EXPLORER_EVIDENCE"])

def _append(row):
    with _evidence.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(row) + "\\n")

def _recording_consumer(project_root, expected_tool_name, token):
    session_id = _original(project_root, expected_tool_name, token)
    _append({"tool": expected_tool_name, "session_id": session_id})
    return session_id

def _recording_lookup(self, session_id):
    capability = _original_lookup(self, session_id)
    _append({"event": "lookup", "session_id": session_id, "found": capability is not None})
    return capability

def _recording_submit(self, **kwargs):
    try:
        result = _original_submit(self, **kwargs)
    except Exception as exc:
        _append({"event": "submit_error", "error": type(exc).__name__, "detail": str(exc)})
        raise
    _append({"event": "submit_ok"})
    return result

tools_exploration.consume_exploration_request_record = _recording_consumer
OwnerBoundExplorationContextStore.session_scoped_capability = _recording_lookup
OwnerBoundExplorationContextStore.submit_for_capability = _recording_submit
"""
    )


def _run_claude(project: Path, plugin: Path, home: Path) -> str:
    prompt = (
        "Call enable_exploration first. Then dispatch the registered "
        "semantic-code-navigator agent and require that child to call "
        "submit_exploration_query for the definition of PROBE_VALUE. "
        "After the child returns, do not retry or call any other tool: immediately reply "
        "LIVE_OK if its broker response was accepted, otherwise reply LIVE_FAILED."
    )
    env = os.environ.copy()
    env["HOME"] = str(home)
    env["CLAUDE_CONFIG_DIR"] = str(home / ".claude")
    env["AUTOSKILLIT_STATE_ROOT"] = str(project)
    env.pop("AUTOSKILLIT_HEADLESS", None)
    output_path = project / ".autoskillit" / "temp" / "claude-live-output.txt"
    with output_path.open("w", encoding="utf-8") as output_stream:
        process = subprocess.Popen(
            [
                "claude",
                "--print",
                "--dangerously-skip-permissions",
                "--plugin-dir",
                str(plugin),
                "--output-format",
                "json",
                prompt,
            ],
            cwd=project,
            env=env,
            stdout=output_stream,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=True,
        )
        try:
            process.wait(timeout=50)
        except subprocess.TimeoutExpired:
            _cleanup_owned_process_group(process, timeout=10)
            pytest.fail(f"Claude explorer live gate timed out: {output_path.read_text()[-4000:]}")
    output = output_path.read_text()
    assert len(output.encode()) <= 256_000, "Claude live output exceeded its evidence bound"
    assert process.returncode == 0, output[-4000:]
    return output


@_skip_unless_live_gate
def test_real_parent_and_registered_child_share_native_session_authority(
    tmp_path: Path,
) -> None:
    for definition in load_bundled_agent_definitions():
        if definition.name in BUNDLED_EXPLORER_ROLES:
            tool_names = frozenset(
                tool.split("__")[-1] for tool in definition.tools if tool.startswith("mcp__")
            )
            assert tool_names == EXPLORATION_TOOLS

    project = tmp_path / "project"
    plugin = tmp_path / "plugin"
    home = tmp_path / "home"
    claude_config = home / ".claude"
    evidence = Path(os.environ["AUTOSKILLIT_CLAUDE_EXPLORER_EVIDENCE"])
    instrumentation = tmp_path / "instrumentation"
    claude_config.mkdir(parents=True)
    evidence.parent.mkdir(parents=True, exist_ok=True)
    evidence.unlink(missing_ok=True)
    if _SOURCE_CREDENTIALS.is_file():
        (claude_config / ".credentials.json").symlink_to(_SOURCE_CREDENTIALS.resolve())
    _initialize_repository(project)
    _write_consumer_instrumentation(instrumentation)
    _build_plugin(plugin)
    _configure_plugin_mcp(plugin, project, evidence, instrumentation)

    output = _run_claude(project, plugin, home)
    rows = [json.loads(line) for line in evidence.read_text().splitlines() if line.strip()]
    by_tool = {row["tool"]: row["session_id"] for row in rows if isinstance(row.get("tool"), str)}

    assert "LIVE_OK" in output
    assert by_tool["enable_exploration"]
    assert by_tool["submit_exploration_query"] == by_tool["enable_exploration"]
    assert any(row.get("event") == "submit_ok" for row in rows)
