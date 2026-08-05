from __future__ import annotations

import os
from collections.abc import Iterator

import pytest

# The two tool-name shapes the run_cmd matcher (`mcp__.*autoskillit.*__run_cmd`)
# must accept: the marketplace-prefixed install form and Codex's direct-prefix
# production form (codex.py + core/_plugin_ids.py).
_RUN_CMD_TOOL_MARKETPLACE: str = "mcp__plugin_autoskillit_autoskillit__run_cmd"
_RUN_CMD_TOOL_DIRECT: str = "mcp__autoskillit__run_cmd"


@pytest.fixture(autouse=True)
def _isolate_hook_cwd(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> Iterator[None]:
    """Run every hook test from an isolated root with secure directory modes."""
    previous_umask = os.umask(0o022)
    try:
        monkeypatch.chdir(tmp_path)
        yield
    finally:
        os.umask(previous_umask)


def make_hook_event(
    *,
    tool: str,
    command: str,
    payload_cwd: str | None,
    tool_cwd: str | None = None,
    extra_tool_input: dict | None = None,
    run_cmd_tool_name: str = _RUN_CMD_TOOL_MARKETPLACE,
) -> dict:
    """Build a PreToolUse hook payload for Bash- or run_cmd-shaped tools.

    ``payload_cwd`` is the hook envelope's top-level ``cwd`` (the session
    cwd). ``tool_cwd`` is the run_cmd tool's own required target-dir
    argument (``tool_input["cwd"]``) — omitted from the payload entirely
    when ``None``. The two fields are independent facts, not competing
    authorities: real Codex session payloads populate both simultaneously
    with differing values.
    """
    if tool == "Bash":
        tool_input: dict = {"command": command}
        if extra_tool_input:
            tool_input.update(extra_tool_input)
        return {"tool_name": "Bash", "tool_input": tool_input, "cwd": payload_cwd}
    if tool == "run_cmd":
        tool_input = {"cmd": command}
        if tool_cwd is not None:
            tool_input["cwd"] = tool_cwd
        if extra_tool_input:
            tool_input.update(extra_tool_input)
        return {"tool_name": run_cmd_tool_name, "tool_input": tool_input, "cwd": payload_cwd}
    raise ValueError(f"unknown tool kind: {tool!r}")
