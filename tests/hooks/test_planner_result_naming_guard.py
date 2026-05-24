"""Tests for planner_result_naming_guard.py PreToolUse hook."""

from __future__ import annotations

import io
import json
import os
import unittest.mock
from contextlib import redirect_stdout
from typing import NamedTuple

import pytest

pytestmark = [pytest.mark.layer("infra"), pytest.mark.small]


def _build_event(tool_name: str, file_path: str) -> dict:
    return {"tool_name": tool_name, "tool_input": {"file_path": file_path}}


class HookResult(NamedTuple):
    output: str
    exit_code: int


def _run_hook(event: dict | str, headless: bool = True) -> HookResult:
    from autoskillit.hooks.guards.planner_result_naming_guard import main

    stdin_text = json.dumps(event) if isinstance(event, dict) else event
    if headless:
        clean_env = {**os.environ, "AUTOSKILLIT_HEADLESS": "1"}
    else:
        clean_env = {k: v for k, v in os.environ.items() if k != "AUTOSKILLIT_HEADLESS"}
    buf = io.StringIO()
    exit_code = 0
    with redirect_stdout(buf):
        with unittest.mock.patch("sys.stdin", io.StringIO(stdin_text)):
            with unittest.mock.patch.dict(os.environ, clean_env, clear=True):
                try:
                    main()
                except SystemExit as e:
                    exit_code = e.code if isinstance(e.code, int) else 0
    return HookResult(output=buf.getvalue(), exit_code=exit_code)


def _parse_result(result: str) -> dict:
    if not result.strip():
        return {}
    return json.loads(result)


class TestPlannerResultNamingGuardCanonical:
    """Canonical result files should always be allowed."""

    def test_allows_write_to_canonical_wp_result_file(self) -> None:
        event = _build_event(
            "Write", "/clone/.autoskillit/planner/work_packages/P1-A1-WP1_result.json"
        )
        result = _run_hook(event)
        assert result.output == ""
        assert result.exit_code == 0

    def test_allows_write_to_canonical_assign_result_file(self) -> None:
        event = _build_event("Write", "/clone/.autoskillit/planner/assignments/P1-A1_result.json")
        result = _run_hook(event)
        assert result.output == ""
        assert result.exit_code == 0

    def test_allows_write_to_canonical_phase_result_file(self) -> None:
        event = _build_event("Write", "/clone/.autoskillit/planner/phases/P1_result.json")
        result = _run_hook(event)
        assert result.output == ""
        assert result.exit_code == 0

    def test_allows_edit_to_canonical_result_file(self) -> None:
        event = _build_event(
            "Edit", "/clone/.autoskillit/planner/work_packages/P1-A1-WP1_result.json"
        )
        result = _run_hook(event)
        assert result.output == ""
        assert result.exit_code == 0

    def test_allows_write_to_deeply_nested_canonical_file(self) -> None:
        event = _build_event(
            "Write", "/clone/.autoskillit/planner/work_packages/P12-A99-WP34_result.json"
        )
        result = _run_hook(event)
        assert result.output == ""
        assert result.exit_code == 0


class TestPlannerResultNamingGuardNonCanonical:
    """Non-canonical result files should be denied with a correction hint."""

    def test_denies_write_to_non_canonical_wp_result_file(self) -> None:
        event = _build_event(
            "Write", "/clone/.autoskillit/planner/work_packages/P1-A1-WP2a_result.json"
        )
        result = _run_hook(event)
        parsed = _parse_result(result.output)
        assert parsed["hookSpecificOutput"]["permissionDecision"] == "deny"

    def test_denies_write_to_non_canonical_wp_result_file_alpha_suffix(self) -> None:
        event = _build_event(
            "Write", "/clone/.autoskillit/planner/work_packages/P1-A1-WP3b_result.json"
        )
        result = _run_hook(event)
        parsed = _parse_result(result.output)
        assert parsed["hookSpecificOutput"]["permissionDecision"] == "deny"

    def test_denies_write_to_non_canonical_wp_result_file_letter_in_number(self) -> None:
        event = _build_event(
            "Write", "/clone/.autoskillit/planner/work_packages/P1-A1-WP6-C_result.json"
        )
        result = _run_hook(event)
        parsed = _parse_result(result.output)
        assert parsed["hookSpecificOutput"]["permissionDecision"] == "deny"

    def test_denies_write_to_non_canonical_assign_result_file(self) -> None:
        event = _build_event("Write", "/clone/.autoskillit/planner/assignments/P1-A2b_result.json")
        result = _run_hook(event)
        parsed = _parse_result(result.output)
        assert parsed["hookSpecificOutput"]["permissionDecision"] == "deny"

    def test_denies_write_to_non_canonical_phase_result_file(self) -> None:
        event = _build_event("Write", "/clone/.autoskillit/planner/phases/Phase1_result.json")
        result = _run_hook(event)
        parsed = _parse_result(result.output)
        assert parsed["hookSpecificOutput"]["permissionDecision"] == "deny"

    def test_denies_edit_to_non_canonical_result_file(self) -> None:
        event = _build_event(
            "Edit", "/clone/.autoskillit/planner/work_packages/P1-A1-WP3b_result.json"
        )
        result = _run_hook(event)
        parsed = _parse_result(result.output)
        assert parsed["hookSpecificOutput"]["permissionDecision"] == "deny"

    def test_deny_message_includes_corrected_pattern(self) -> None:
        event = _build_event(
            "Write", "/clone/.autoskillit/planner/work_packages/P1-A1-WP2a_result.json"
        )
        result = _run_hook(event)
        parsed = _parse_result(result.output)
        reason = parsed["hookSpecificOutput"]["permissionDecisionReason"]
        assert "P\\d+-A\\d+-WP\\d+" in reason

    def test_denies_wp_result_file_without_phase_prefix(self) -> None:
        event = _build_event("Write", "/clone/.autoskillit/planner/work_packages/wp1_result.json")
        result = _run_hook(event)
        parsed = _parse_result(result.output)
        assert parsed["hookSpecificOutput"]["permissionDecision"] == "deny"


class TestPlannerResultNamingGuardNonResultFiles:
    """Non-result files in planner directories should be allowed."""

    def test_allows_write_to_non_result_file_in_planner_dir(self) -> None:
        event = _build_event("Write", "/clone/.autoskillit/planner/work_packages/wp_index.json")
        result = _run_hook(event)
        assert result.output == ""
        assert result.exit_code == 0

    def test_allows_write_to_context_file(self) -> None:
        event = _build_event("Write", "/clone/.autoskillit/planner/assignments/context_P1.json")
        result = _run_hook(event)
        assert result.output == ""
        assert result.exit_code == 0

    def test_allows_write_outside_planner_directories(self) -> None:
        event = _build_event("Write", "/clone/src/foo.py")
        result = _run_hook(event)
        assert result.output == ""
        assert result.exit_code == 0

    def test_allows_write_to_manifest_file(self) -> None:
        event = _build_event(
            "Write", "/clone/.autoskillit/planner/phases/phase_assignment_manifest.json"
        )
        result = _run_hook(event)
        assert result.output == ""
        assert result.exit_code == 0


class TestPlannerResultNamingGuardSentinels:
    """Sentinel files in subdirectories should be allowed (they are not tier result files)."""

    def test_allows_sentinel_files_in_subdirectory(self) -> None:
        event = _build_event(
            "Write", "/clone/.autoskillit/planner/work_packages/wp_sentinels/P1_result.json"
        )
        result = _run_hook(event)
        assert result.output == ""
        assert result.exit_code == 0

    def test_allows_wp_style_filename_in_subdirectory(self) -> None:
        event = _build_event(
            "Write",
            "/clone/.autoskillit/planner/work_packages/wp_sentinels/P1-A1-WP1_result.json",
        )
        result = _run_hook(event)
        assert result.output == ""
        assert result.exit_code == 0

    def test_allows_assign_style_filename_in_subdirectory(self) -> None:
        event = _build_event(
            "Write",
            "/clone/.autoskillit/planner/assignments/sub/P1-A1_result.json",
        )
        result = _run_hook(event)
        assert result.output == ""
        assert result.exit_code == 0


class TestPlannerResultNamingGuardFailOpen:
    """Malformed input should fail-open (exit 0, allow the call)."""

    def test_failopen_on_malformed_json(self) -> None:
        result = _run_hook("not json at all")
        assert result.output == ""
        assert result.exit_code == 0

    def test_failopen_on_empty_input(self) -> None:
        result = _run_hook("")
        assert result.output == ""
        assert result.exit_code == 0

    def test_failopen_on_missing_tool_name(self) -> None:
        event = {
            "tool_input": {"file_path": "/clone/.autoskillit/planner/work_packages/P1_result.json"}
        }
        result = _run_hook(event)
        assert result.output == ""
        assert result.exit_code == 0

    def test_failopen_on_non_write_edit_tool(self) -> None:
        event = _build_event(
            "Read", "/clone/.autoskillit/planner/work_packages/P1-A1-WP1_result.json"
        )
        result = _run_hook(event)
        assert result.output == ""
        assert result.exit_code == 0

    def test_failopen_on_missing_file_path(self) -> None:
        event = {"tool_name": "Write", "tool_input": {}}
        result = _run_hook(event)
        assert result.output == ""
        assert result.exit_code == 0


class TestPlannerResultNamingGuardSessionScope:
    """Guard only fires in headless sessions (AUTOSKILLIT_HEADLESS=1)."""

    def test_denies_non_canonical_when_headless(self) -> None:
        event = _build_event(
            "Write", "/clone/.autoskillit/planner/work_packages/P1-A1-WP2a_result.json"
        )
        result = _run_hook(event, headless=True)
        parsed = json.loads(result.output)
        assert parsed["hookSpecificOutput"]["permissionDecision"] == "deny"

    def test_allows_non_canonical_when_not_headless(self) -> None:
        event = _build_event(
            "Write", "/clone/.autoskillit/planner/work_packages/P1-A1-WP2a_result.json"
        )
        result = _run_hook(event, headless=False)
        assert result.output == ""
        assert result.exit_code == 0


def _run_write_guard(
    event: dict | str,
    *,
    allowed_prefix: str = "",
    skill_name: str = "",
) -> str:
    """Run write_guard.py with optional planner session environment."""
    from autoskillit.hooks.guards.write_guard import main

    stdin_text = json.dumps(event) if isinstance(event, dict) else event
    env_patch: dict[str, str] = {
        "AUTOSKILLIT_HEADLESS": "1",
        # Explicitly set (or clear) the write prefix so ambient skill-session
        # env vars do not interfere with tests that expect an unrestricted env.
        "AUTOSKILLIT_ALLOWED_WRITE_PREFIX": allowed_prefix,
    }
    if skill_name:
        env_patch["AUTOSKILLIT_SKILL_NAME"] = skill_name
    buf = io.StringIO()
    with (
        unittest.mock.patch("sys.stdin", io.StringIO(stdin_text)),
        redirect_stdout(buf),
        unittest.mock.patch.dict(os.environ, env_patch, clear=False),
    ):
        try:
            main()
        except SystemExit:
            pass
    return buf.getvalue()


class TestPlannerWriteScopeGuard:
    """write_guard enforces AUTOSKILLIT_ALLOWED_WRITE_PREFIX in planner sessions."""

    PREFIX = "/clone/.autoskillit/temp/planner/run-xyz/work_packages/"
    SKILL = "planner-elaborate-wps"

    def test_planner_session_write_to_source_denied(self) -> None:
        event = _build_event("Write", "/clone/src/daemon.rs")
        result = _run_write_guard(event, allowed_prefix=self.PREFIX, skill_name=self.SKILL)
        parsed = json.loads(result)
        assert parsed["hookSpecificOutput"]["permissionDecision"] == "deny"
        assert (
            "read-only skill session" in parsed["hookSpecificOutput"]["permissionDecisionReason"]
        )

    def test_planner_session_write_to_output_dir_allowed(self) -> None:
        path = "/clone/.autoskillit/temp/planner/run-xyz/work_packages/P1-A1-WP1_result.json"
        event = _build_event("Write", path)
        result = _run_write_guard(event, allowed_prefix=self.PREFIX, skill_name=self.SKILL)
        assert result == ""

    def test_non_planner_session_unaffected(self) -> None:
        event = _build_event("Write", "/clone/src/foo.py")
        result = _run_write_guard(event, allowed_prefix="", skill_name="")
        assert result == ""
