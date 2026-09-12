"""Codex CLI event and data-payload test fixtures."""

from __future__ import annotations

from pathlib import Path

from autoskillit.core import CmdSpec, ValidatedAddDir

CODEX_SCHEMA_VERSION: int = 2
CODEX_FIXTURE_MIN_VERSION: str = "0.136.0"

HAPPY_PATH_SINGLE_TURN: str = "happy_path_single_turn_v0133.ndjson"
HAPPY_PATH_V0136: str = "happy_path_v0136.ndjson"
FLAT_ERROR_MODEL_CAPACITY: str = "flat_error_model_capacity_v0133.ndjson"
MARKER_DETECTION_V0136: str = "marker_detection_v0136.ndjson"
LARGE_EMBEDDED_PAYLOAD_V1: str = "large_embedded_payload_v1.jsonl"
MULTI_TURN_WITH_COMPACTION: str = "multi_turn_with_compaction_v0133.ndjson"
TURN_FAILED_ERROR: str = "turn_failed_error_v0133.ndjson"
TURN_FAILED_MODEL_CAPACITY: str = "turn_failed_model_capacity_v0133.ndjson"
SESSION_WITH_REASONING: str = "session_with_reasoning_v0133.ndjson"
SESSION_WITH_MCP_TOOL_CALL: str = "session_with_mcp_tool_call_v0133.ndjson"


def fixture_path(name: str) -> Path:
    """Return the absolute path to a fixture file in this directory."""
    return Path(__file__).parent / name


def codex_skill_add_dirs(
    cwd: str, *, skill_name: str = "test-skill"
) -> tuple[ValidatedAddDir, ...]:
    """A single ValidatedAddDir satisfying CodexBackend's app-server skill-session invariant.

    ``build_skill_session_cmd`` requires exactly one add-dir bound to a nonempty
    ``session_home`` with a frozen, nonempty skill catalog; this is the canonical
    fixture shape for tests that only need the invariant satisfied, not a specific
    catalog layout.
    """
    return (
        ValidatedAddDir(
            path=f"{cwd}/add-dir",
            session_home=cwd,
            skill_entries=((skill_name, f"{skill_name}/SKILL.md"),),
        ),
    )


def prompt_text(spec: CmdSpec) -> str:
    """Extract the composed prompt from a CmdSpec, backend-agnostic.

    A Codex app-server skill session carries its fully composed prompt on
    ``spec.app_server_plan.prompt`` instead of as a trailing ``cmd``
    positional (the app-server transport has no such positional); every
    other builder still delivers it via ``cmd``, either after a ``-p`` flag
    or as the trailing positional.
    """
    if spec.app_server_plan is not None:
        return spec.app_server_plan.prompt
    cmd = list(spec.cmd)
    if "-p" in cmd:
        return cmd[cmd.index("-p") + 1]
    return cmd[-1]


__all__ = [
    "CODEX_FIXTURE_MIN_VERSION",
    "CODEX_SCHEMA_VERSION",
    "FLAT_ERROR_MODEL_CAPACITY",
    "HAPPY_PATH_SINGLE_TURN",
    "HAPPY_PATH_V0136",
    "LARGE_EMBEDDED_PAYLOAD_V1",
    "MARKER_DETECTION_V0136",
    "MULTI_TURN_WITH_COMPACTION",
    "SESSION_WITH_MCP_TOOL_CALL",
    "SESSION_WITH_REASONING",
    "TURN_FAILED_ERROR",
    "TURN_FAILED_MODEL_CAPACITY",
    "codex_skill_add_dirs",
    "fixture_path",
    "prompt_text",
]
