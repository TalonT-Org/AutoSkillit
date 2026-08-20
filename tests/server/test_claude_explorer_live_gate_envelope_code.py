"""Augment the Claude explorer live gate to assert the broker envelope code
(#4684 Fix A / Step 1.14).

test_claude_explorer_live_gate.py verifies only that the parent replied
LIVE_OK; it does not verify that enable_exploration itself succeeded. A
future broker regression that swallows a precondition failure into a typed
failure code (or the final unexpected_internal_error catch-all — the exact
bug class #4684 fixes) could still produce a LIVE_OK reply from the parent
via some other retry path, and the existing gate would not catch it.

This test reuses the same setup helpers (repository, plugin, MCP config)
from test_claude_explorer_live_gate.py by importing them by name — it does
NOT modify that file, so the existing, already-verified live gate is
untouched. It adds its own instrumentation that intercepts
tools_exploration._failure(code), the single module-level helper every
except-branch in enable_exploration (including the final catch-all) routes
through to build its error envelope — the same "reassign a module-level
name, called via bare-name lookup from the target function's body" pattern
_write_consumer_instrumentation already uses successfully for
consume_exploration_request_record, rather than trying to intercept the
already-@mcp.tool()-decorated enable_exploration function itself (whose
registration the running MCP framework captures a direct reference to,
not a re-lookupable module attribute).

Recording every _failure(code) call, rather than trying to capture the
inline-built success envelope, is the more direct test of "the happy path
was taken, not the catch-all": zero recorded failure codes plus a LIVE_OK
reply is exactly the property this gate needs to hold.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.execution._process_group_helpers import _cleanup_owned_process_group
from tests.server.test_claude_explorer_live_gate import (
    _ROOT,
    _SOURCE_CREDENTIALS,
    _build_plugin,
    _has_authentication,
    _initialize_repository,
)

pytestmark = [pytest.mark.layer("server"), pytest.mark.large, pytest.mark.smoke]

_LIVE_ENV = "AUTOSKILLIT_CLAUDE_EXPLORER_LIVE_GATE"
_skip_unless_live_gate = pytest.mark.skipif(
    os.environ.get(_LIVE_ENV) != "1" or shutil.which("claude") is None or not _has_authentication,
    reason="Claude explorer live gate requires its opt-in, executable, and isolated auth",
)


def _configure_plugin_mcp_for_envelope_evidence(
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
                            "AUTOSKILLIT_CLAUDE_EXPLORER_ENVELOPE_EVIDENCE": str(evidence),
                            "PYTHONPATH": str(instrumentation),
                        },
                    }
                }
            },
            indent=2,
        )
        + "\n"
    )


def _write_failure_capturing_instrumentation(directory: Path) -> None:
    directory.mkdir()
    (directory / "sitecustomize.py").write_text(
        """import json
import os
from pathlib import Path

from autoskillit.server.tools import tools_exploration

_original_failure = tools_exploration._failure
_evidence = Path(os.environ["AUTOSKILLIT_CLAUDE_EXPLORER_ENVELOPE_EVIDENCE"])

def _append(row):
    with _evidence.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(row) + "\\n")

def _recording_failure(code):
    _append({"event": "enable_exploration_failure", "code": code})
    return _original_failure(code)

tools_exploration._failure = _recording_failure
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
    output_path = project / ".autoskillit" / "temp" / "claude-live-envelope-output.txt"
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
            pytest.fail(
                f"Claude explorer envelope gate timed out: {output_path.read_text()[-4000:]}"
            )
    output = output_path.read_text()
    assert len(output.encode()) <= 256_000, "Claude live output exceeded its evidence bound"
    assert process.returncode == 0, output[-4000:]
    return output


@_skip_unless_live_gate
def test_enable_exploration_envelope_is_happy_path_not_catch_all(tmp_path: Path) -> None:
    project = tmp_path / "project"
    plugin = tmp_path / "plugin"
    home = tmp_path / "home"
    claude_config = home / ".claude"
    evidence = Path(os.environ["AUTOSKILLIT_CLAUDE_EXPLORER_ENVELOPE_EVIDENCE"])
    instrumentation = tmp_path / "instrumentation"
    claude_config.mkdir(parents=True)
    evidence.parent.mkdir(parents=True, exist_ok=True)
    evidence.unlink(missing_ok=True)
    if _SOURCE_CREDENTIALS.is_file():
        (claude_config / ".credentials.json").symlink_to(_SOURCE_CREDENTIALS.resolve())
    _initialize_repository(project)
    _write_failure_capturing_instrumentation(instrumentation)
    _build_plugin(plugin)
    _configure_plugin_mcp_for_envelope_evidence(plugin, project, evidence, instrumentation)

    output = _run_claude(project, plugin, home)
    rows = [json.loads(line) for line in evidence.read_text().splitlines() if line.strip()]
    failure_codes = [
        row["code"] for row in rows if row.get("event") == "enable_exploration_failure"
    ]

    assert "LIVE_OK" in output
    assert not failure_codes, (
        f"enable_exploration returned failure code(s) {failure_codes!r} even though the "
        "parent replied LIVE_OK — LIVE_OK does not prove the broker itself succeeded."
    )
