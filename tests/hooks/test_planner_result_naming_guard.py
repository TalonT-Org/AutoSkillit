"""Tests for planner_result_naming_guard.py PreToolUse hook."""

from __future__ import annotations

import io
import json
import os
import unittest.mock
from contextlib import redirect_stdout

import pytest

pytestmark = [pytest.mark.layer("infra"), pytest.mark.small]


def _build_event(tool_name: str, file_path: str) -> dict:
    return {"tool_name": tool_name, "tool_input": {"file_path": file_path}}


def _run_hook(event: dict | str, headless: bool = True) -> str:
    from autoskillit.hooks.guards.planner_result_naming_guard import main

    stdin_text = json.dumps(event) if isinstance(event, dict) else event
    if headless:
        clean_env = {**os.environ, "AUTOSKILLIT_HEADLESS": "1"}
    else:
        clean_env = {k: v for k, v in os.environ.items() if k != "AUTOSKILLIT_HEADLESS"}
    buf = io.StringIO()
    with redirect_stdout(buf):
        with unittest.mock.patch("sys.stdin", io.StringIO(stdin_text)):
            with unittest.mock.patch.dict(os.environ, clean_env, clear=True):
                try:
                    main()
                except SystemExit:
                    pass
    return buf.getvalue()


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
        assert result == ""

    def test_allows_write_to_canonical_assign_result_file(self) -> None:
        event = _build_event("Write", "/clone/.autoskillit/planner/assignments/P1-A1_result.json")
        result = _run_hook(event)
        assert result == ""

    def test_allows_write_to_canonical_phase_result_file(self) -> None:
        event = _build_event("Write", "/clone/.autoskillit/planner/phases/P1_result.json")
        result = _run_hook(event)
        assert result == ""

    def test_allows_edit_to_canonical_result_file(self) -> None:
        event = _build_event(
            "Edit", "/clone/.autoskillit/planner/work_packages/P1-A1-WP1_result.json"
        )
        result = _run_hook(event)
        assert result == ""

    def test_allows_write_to_deeply_nested_canonical_file(self) -> None:
        event = _build_event(
            "Write", "/clone/.autoskillit/planner/work_packages/P12-A99-WP34_result.json"
        )
        result = _run_hook(event)
        assert result == ""


class TestPlannerResultNamingGuardNonCanonical:
    """Non-canonical result files should be denied with a correction hint."""

    def test_denies_write_to_non_canonical_wp_result_file(self) -> None:
        event = _build_event(
            "Write", "/clone/.autoskillit/planner/work_packages/P1-A1-WP2a_result.json"
        )
        result = _run_hook(event)
        parsed = _parse_result(result)
        assert parsed["hookSpecificOutput"]["permissionDecision"] == "deny"

    def test_denies_write_to_non_canonical_wp_result_file_alpha_suffix(self) -> None:
        event = _build_event(
            "Write", "/clone/.autoskillit/planner/work_packages/P1-A1-WP3b_result.json"
        )
        result = _run_hook(event)
        parsed = _parse_result(result)
        assert parsed["hookSpecificOutput"]["permissionDecision"] == "deny"

    def test_denies_write_to_non_canonical_wp_result_file_letter_in_number(self) -> None:
        event = _build_event(
            "Write", "/clone/.autoskillit/planner/work_packages/P1-A1-WP6-C_result.json"
        )
        result = _run_hook(event)
        parsed = _parse_result(result)
        assert parsed["hookSpecificOutput"]["permissionDecision"] == "deny"

    def test_denies_write_to_non_canonical_assign_result_file(self) -> None:
        event = _build_event("Write", "/clone/.autoskillit/planner/assignments/P1-A2b_result.json")
        result = _run_hook(event)
        parsed = _parse_result(result)
        assert parsed["hookSpecificOutput"]["permissionDecision"] == "deny"

    def test_denies_write_to_non_canonical_phase_result_file(self) -> None:
        event = _build_event("Write", "/clone/.autoskillit/planner/phases/Phase1_result.json")
        result = _run_hook(event)
        parsed = _parse_result(result)
        assert parsed["hookSpecificOutput"]["permissionDecision"] == "deny"

    def test_denies_edit_to_non_canonical_result_file(self) -> None:
        event = _build_event(
            "Edit", "/clone/.autoskillit/planner/work_packages/P1-A1-WP3b_result.json"
        )
        result = _run_hook(event)
        parsed = _parse_result(result)
        assert parsed["hookSpecificOutput"]["permissionDecision"] == "deny"

    def test_deny_message_includes_corrected_pattern(self) -> None:
        event = _build_event(
            "Write", "/clone/.autoskillit/planner/work_packages/P1-A1-WP2a_result.json"
        )
        result = _run_hook(event)
        parsed = _parse_result(result)
        reason = parsed["hookSpecificOutput"]["permissionDecisionReason"]
        assert "P\\d+-A\\d+-WP\\d+" in reason

    def test_denies_wp_result_file_without_phase_prefix(self) -> None:
        event = _build_event("Write", "/clone/.autoskillit/planner/work_packages/wp1_result.json")
        result = _run_hook(event)
        parsed = _parse_result(result)
        assert parsed["hookSpecificOutput"]["permissionDecision"] == "deny"


class TestPlannerResultNamingGuardNonResultFiles:
    """Non-result files in planner directories should be allowed."""

    def test_allows_write_to_non_result_file_in_planner_dir(self) -> None:
        event = _build_event("Write", "/clone/.autoskillit/planner/work_packages/wp_index.json")
        result = _run_hook(event)
        assert result == ""

    def test_allows_write_to_context_file(self) -> None:
        event = _build_event("Write", "/clone/.autoskillit/planner/assignments/context_P1.json")
        result = _run_hook(event)
        assert result == ""

    def test_allows_write_outside_planner_directories(self) -> None:
        event = _build_event("Write", "/clone/src/foo.py")
        result = _run_hook(event)
        assert result == ""

    def test_allows_write_to_manifest_file(self) -> None:
        event = _build_event(
            "Write", "/clone/.autoskillit/planner/phases/phase_assignment_manifest.json"
        )
        result = _run_hook(event)
        assert result == ""


class TestPlannerResultNamingGuardSentinels:
    """Sentinel files in subdirectories should be allowed (they are not tier result files)."""

    def test_allows_sentinel_files_in_subdirectory(self) -> None:
        event = _build_event(
            "Write", "/clone/.autoskillit/planner/work_packages/wp_sentinels/P1_result.json"
        )
        result = _run_hook(event)
        assert result == ""

    def test_allows_wp_style_filename_in_subdirectory(self) -> None:
        event = _build_event(
            "Write",
            "/clone/.autoskillit/planner/work_packages/wp_sentinels/P1-A1-WP1_result.json",
        )
        result = _run_hook(event)
        assert result == ""

    def test_allows_assign_style_filename_in_subdirectory(self) -> None:
        event = _build_event(
            "Write",
            "/clone/.autoskillit/planner/assignments/sub/P1-A1_result.json",
        )
        result = _run_hook(event)
        assert result == ""


class TestPlannerResultNamingGuardFailOpen:
    """Malformed input should fail-open (exit 0, allow the call)."""

    def test_failopen_on_malformed_json(self) -> None:
        result = _run_hook("not json at all")
        assert result == ""

    def test_failopen_on_empty_input(self) -> None:
        result = _run_hook("")
        assert result == ""

    def test_failopen_on_missing_tool_name(self) -> None:
        event = {
            "tool_input": {"file_path": "/clone/.autoskillit/planner/work_packages/P1_result.json"}
        }
        result = _run_hook(event)
        assert result == ""

    def test_failopen_on_non_write_edit_tool(self) -> None:
        event = _build_event(
            "Read", "/clone/.autoskillit/planner/work_packages/P1-A1-WP1_result.json"
        )
        result = _run_hook(event)
        assert result == ""

    def test_failopen_on_missing_file_path(self) -> None:
        event = {"tool_name": "Write", "tool_input": {}}
        result = _run_hook(event)
        assert result == ""


class TestPlannerResultNamingGuardSessionScope:
    """Guard only fires in headless sessions (AUTOSKILLIT_HEADLESS=1)."""

    def test_denies_non_canonical_when_headless(self) -> None:
        event = _build_event(
            "Write", "/clone/.autoskillit/planner/work_packages/P1-A1-WP2a_result.json"
        )
        result = _run_hook(event, headless=True)
        parsed = json.loads(result)
        assert parsed["hookSpecificOutput"]["permissionDecision"] == "deny"

    def test_allows_non_canonical_when_not_headless(self) -> None:
        event = _build_event(
            "Write", "/clone/.autoskillit/planner/work_packages/P1-A1-WP2a_result.json"
        )
        result = _run_hook(event, headless=False)
        assert result == ""
