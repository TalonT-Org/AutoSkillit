"""Tests: pretty_output hook infrastructure, fail-open, and coverage contracts."""

from __future__ import annotations

import json

import pytest

from autoskillit.core.types import ChannelConfirmation, TerminationReason
from autoskillit.execution.headless import _build_skill_result
from autoskillit.hooks.formatters.pretty_output_hook import _format_response
from tests.conftest import _make_result
from tests.infra._pretty_output_helpers import (
    _run_hook,
    _wrap_for_claude_code,
    _wrap_plain_str_for_claude_code,
)

pytestmark = [pytest.mark.layer("infra"), pytest.mark.medium]


# PHK-1
def test_hook_script_exists():
    """pretty_output.py must exist in the hooks directory."""
    from autoskillit.core.paths import pkg_root

    assert (pkg_root() / "hooks" / "formatters" / "pretty_output_hook.py").exists()


# PHK-2
def test_hook_emits_posttooluse_event_name():
    """Hook output JSON must have hookSpecificOutput.hookEventName == 'PostToolUse'."""
    event = {
        "tool_name": "mcp__plugin_autoskillit_autoskillit__run_cmd",
        "tool_response": json.dumps(
            {"success": True, "exit_code": 0, "stdout": "hi", "stderr": ""}
        ),
    }
    out, _ = _run_hook(event=event)
    assert out.strip(), "Expected non-empty output"
    data = json.loads(out)
    assert data["hookSpecificOutput"]["hookEventName"] == "PostToolUse"


# PHK-3
def test_hook_emits_updated_mcp_tool_output_field():
    """Hook output must have non-empty hookSpecificOutput.updatedMCPToolOutput."""
    event = {
        "tool_name": "mcp__plugin_autoskillit_autoskillit__run_cmd",
        "tool_response": json.dumps(
            {"success": True, "exit_code": 0, "stdout": "hi", "stderr": ""}
        ),
    }
    out, _ = _run_hook(event=event)
    data = json.loads(out)
    assert data["hookSpecificOutput"]["updatedMCPToolOutput"]


# PHK-4
def test_hook_fail_open_on_invalid_json_stdin():
    """Non-JSON stdin → exit 0, no stdout output."""
    out, code = _run_hook(raw_stdin="not valid json {{{{")
    assert code == 0
    assert out.strip() == ""


# PHK-5
def test_hook_fail_open_on_missing_tool_response():
    """Valid JSON but missing tool_response key → exit 0, no stdout."""
    event = {"tool_name": "mcp__plugin_autoskillit_autoskillit__run_cmd"}
    out, code = _run_hook(event=event)
    assert code == 0
    assert out.strip() == ""


# ---------------------------------------------------------------------------
# PHK-41: Formatter coverage contract
# ---------------------------------------------------------------------------


def test_formatter_coverage_contract():
    """PHK-41: Every MCP tool is either in _FORMATTERS or explicitly in _UNFORMATTED_TOOLS."""
    from autoskillit.core.types import GATED_TOOLS, UNGATED_TOOLS
    from autoskillit.hooks.formatters.pretty_output_hook import _FORMATTERS, _UNFORMATTED_TOOLS

    all_tools = GATED_TOOLS | UNGATED_TOOLS
    covered = set(_FORMATTERS.keys()) | _UNFORMATTED_TOOLS
    uncovered = all_tools - covered
    assert uncovered == set(), (
        f"Tools have no formatter and are not in _UNFORMATTED_TOOLS: {sorted(uncovered)}. "
        "Either add a dedicated formatter or add to _UNFORMATTED_TOOLS."
    )


def test_all_formatters_have_coverage_contracts():
    """Every dedicated formatter must have a registered field coverage contract."""
    from autoskillit.hooks.formatters.pretty_output_hook import _FORMATTERS
    from tests.infra.conftest import _FORMATTER_COVERAGE_REGISTRY

    uncovered = set(_FORMATTERS.keys()) - set(_FORMATTER_COVERAGE_REGISTRY.keys())
    assert uncovered == set(), (
        f"Formatters without coverage contracts: {sorted(uncovered)}. "
        "Define a TypedDict for the tool result, add RENDERED/SUPPRESSED frozensets, "
        "and register in _FORMATTER_COVERAGE_REGISTRY."
    )


def test_coverage_registry_entries_are_valid():
    """Every registry entry's frozensets must exactly cover its TypedDict annotations."""
    from tests.infra.conftest import _FORMATTER_COVERAGE_REGISTRY

    for name, entry in _FORMATTER_COVERAGE_REGISTRY.items():
        import typing

        all_fields = set(typing.get_type_hints(entry.typed_dict))
        covered = entry.rendered | entry.suppressed
        overlap = entry.rendered & entry.suppressed
        uncovered = all_fields - covered
        extra = covered - all_fields

        assert overlap == set(), (
            f"{name}: fields in both RENDERED and SUPPRESSED: {sorted(overlap)}"
        )
        assert uncovered == set(), (
            f"{name}: TypedDict fields without coverage: {sorted(uncovered)}"
        )
        assert extra == set(), f"{name}: frozenset entries not in TypedDict: {sorted(extra)}"


# ---------------------------------------------------------------------------
# T-3: _wrap_plain_str_for_claude_code helper shape
# ---------------------------------------------------------------------------


def test_wrap_plain_str_helper_produces_correct_shape():
    """_wrap_plain_str_for_claude_code produces the real hook event shape."""
    raw = _wrap_plain_str_for_claude_code("hello world")
    parsed = json.loads(raw)
    assert parsed == {"result": "hello world"}


# ---------------------------------------------------------------------------
# T-4/T-5: _UNFORMATTED_TOOLS behavioral gate
# ---------------------------------------------------------------------------


def test_unformatted_tools_and_formatters_are_disjoint():
    """_UNFORMATTED_TOOLS and _FORMATTERS must be mutually exclusive."""
    from autoskillit.hooks.formatters.pretty_output_hook import _FORMATTERS, _UNFORMATTED_TOOLS

    overlap = set(_FORMATTERS) & _UNFORMATTED_TOOLS
    assert not overlap, f"Tools in both dispatch tables: {overlap}"


def test_unformatted_tool_routes_to_generic_not_named_formatter(tmp_path):
    """A tool in _UNFORMATTED_TOOLS must reach _fmt_generic."""
    payload = {"total_failures": 1, "failures": [{"step": "impl", "reason": "red"}]}
    event = {
        "tool_name": "mcp__plugin_autoskillit_autoskillit__get_pipeline_report",
        "tool_response": _wrap_for_claude_code(payload),
    }
    out, code = _run_hook(event=event, cwd=tmp_path)
    assert code == 0
    text = json.loads(out)["hookSpecificOutput"]["updatedMCPToolOutput"]
    assert "get_pipeline_report" in text


def test_pretty_output_public_surface_unchanged() -> None:
    """T-5 (audit finding 8.3): the hook entrypoint and the format router are public surface."""
    import autoskillit.hooks.formatters.pretty_output_hook as p

    assert callable(p.main)
    assert callable(p._format_response)


# Issue #346
def test_fmt_run_skill_contradictory_subtype_never_renders_fail_success():
    """Test A: full pipeline — COMPLETED+empty never renders 'FAIL [success]'."""
    stdout = json.dumps(
        {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "result": "",
            "session_id": "s1",
        }
    )
    result = _make_result(
        returncode=0,
        stdout=stdout,
        termination_reason=TerminationReason.COMPLETED,
        channel_confirmation=ChannelConfirmation.UNMONITORED,
    )
    sr = _build_skill_result(result, completion_marker="", skill_command="/test")
    assert sr.success is False, "Precondition: this path must produce a failure"

    payload = json.loads(sr.to_json())

    pipeline_out = _format_response(
        "mcp__plugin_autoskillit_autoskillit__run_skill",
        json.dumps(payload),
        pipeline=True,
    )
    assert pipeline_out is not None
    assert "FAIL [success]" not in pipeline_out, (
        f"Pipeline mode rendered contradictory 'FAIL [success]': {pipeline_out!r}"
    )
    assert "FAIL [empty_result]" in pipeline_out, (
        f"Expected 'FAIL [empty_result]' in pipeline output: {pipeline_out!r}"
    )

    interactive_out = _format_response(
        "mcp__plugin_autoskillit_autoskillit__run_skill",
        json.dumps(payload),
        pipeline=False,
    )
    assert interactive_out is not None
    cross = "\u2717"
    assert f"{cross} success" not in interactive_out, (
        f"Interactive mode rendered contradictory '{cross} success': {interactive_out!r}"
    )
