#!/usr/bin/env python3
"""PostToolUse hook: prettify MCP tool output as Markdown-KV.

Intercepts MCP tool responses and reformats them from raw JSON to Markdown-KV
before Claude consumes them. Reduces token overhead 30-77% and improves
LLM field-extraction accuracy.

This dispatch entrypoint delegates to the ``_fmt_primitives``,
``_fmt_response_spill``, ``_fmt_execution``, ``_fmt_status``, and
``_fmt_recipe`` helpers to stay within its line budget.

Stdlib-only — runs under any Python interpreter without the autoskillit package.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from pathlib import Path  # re-exported for tests that patch ``pretty_output.Path.cwd``
from typing import Any

# Hooks run as ``python3 /path/to/pretty_output.py`` subprocesses outside the
# autoskillit venv (test_hooks_are_stdlib_only). To split the formatter without
# breaking that constraint, sibling helpers are imported by bare name with the
# script's directory placed first on sys.path. The same bootstrap makes
# package-mode loading (``from autoskillit.hooks.pretty_output import ...``)
# resolve the helpers to the same top-level modules so identity stays
# consistent across both invocation modes.
_HOOKS_DIR = str(Path(__file__).resolve().parent)
_PACKAGE_ROOT = str(Path(__file__).resolve().parents[2])
for _import_dir in (_HOOKS_DIR, _PACKAGE_ROOT):
    if _import_dir not in sys.path:
        sys.path.insert(0, _import_dir)

from _fmt_dispatch import (  # type: ignore[import-not-found]  # noqa: E402, F401
    _FMT_DISPATCH_FOOD_TRUCK_RENDERED,
    _FMT_DISPATCH_FOOD_TRUCK_SUPPRESSED,
    _fmt_dispatch_food_truck,
)
from _fmt_execution import (  # type: ignore[import-not-found]  # noqa: E402, F401
    _FMT_MERGE_WORKTREE_RENDERED,
    _FMT_MERGE_WORKTREE_SUPPRESSED,
    _FMT_RUN_CMD_RENDERED,
    _FMT_RUN_CMD_SUPPRESSED,
    _FMT_RUN_SKILL_RENDERED,
    _FMT_RUN_SKILL_SUPPRESSED,
    _FMT_TEST_CHECK_RENDERED,
    _FMT_TEST_CHECK_SUPPRESSED,
    _fmt_merge_worktree,
    _fmt_run_cmd,
    _fmt_run_skill,
    _fmt_test_check,
)
from _fmt_primitives import (  # type: ignore[import-not-found]  # noqa: E402, F401
    _CHECK_MARK,
    _CROSS_MARK,
    _HOOK_CONFIG_PATH_COMPONENTS,
    _RESPONSE_BACKSTOP_EXEMPTION_REGISTRY,
    _RESPONSE_BACKSTOP_EXEMPTION_REGISTRY_DIGEST,
    _RESPONSE_SPILL_METADATA_KEY,
    _RESPONSE_SPILL_METADATA_KEYS,
    _RESPONSE_SPILL_REASONS,
    _RESPONSE_SPILL_SCHEMA_DIGEST,
    _RESPONSE_SPILL_SCHEMA_VERSION,
    _DictPayload,
    _extract_tool_short_name,
    _fmt_generic,
    _is_pipeline_mode,
    _Payload,
    _PlainTextPayload,
    _validate_response_spill_metadata,
)
from _fmt_recipe import (  # type: ignore[import-not-found]  # noqa: E402, F401
    _FMT_LIST_RECIPES_RENDERED,
    _FMT_LIST_RECIPES_SUPPRESSED,
    _FMT_LOAD_RECIPE_RENDERED,
    _FMT_LOAD_RECIPE_SUPPRESSED,
    _FMT_OPEN_KITCHEN_RENDERED,
    _FMT_OPEN_KITCHEN_SUPPRESSED,
    _FMT_RECIPE_LIST_ITEM_RENDERED,
    _FMT_RECIPE_LIST_ITEM_SUPPRESSED,
    _LOAD_RECIPE_CONTENT_DERIVED_FROM,
    _fmt_list_recipes,
    _fmt_load_recipe,
    _fmt_open_kitchen,
    _fmt_open_kitchen_plain_text,
    _fmt_recipe_body,
    _fmt_recipe_segment,
    _strip_yaml_ingredients_block,
)
from _fmt_status import (  # type: ignore[import-not-found]  # noqa: E402, F401
    _FMT_CLONE_REPO_RENDERED,
    _FMT_CLONE_REPO_SUPPRESSED,
    _FMT_KITCHEN_STATUS_RENDERED,
    _FMT_KITCHEN_STATUS_SUPPRESSED,
    _FMT_TIMING_SUMMARY_RENDERED,
    _FMT_TIMING_SUMMARY_SUPPRESSED,
    _FMT_TOKEN_SUMMARY_RENDERED,
    _FMT_TOKEN_SUMMARY_SUPPRESSED,
    _fmt_clone_repo,
    _fmt_get_timing_summary,
    _fmt_get_token_summary,
    _fmt_kitchen_status,
)


def _fmt_tool_exception(data: dict, pipeline: bool) -> str:
    """Format a tool_exception response with full diagnostics."""
    error = data.get("error", "unknown error")
    exit_code = data.get("exit_code", -1)
    if pipeline:
        return f"TOOL EXCEPTION [{exit_code}]: {error}"
    return f"## {_CROSS_MARK} Tool Exception\n\nerror: {error}\nexit_code: {exit_code}"


def _fmt_gate_error(data: dict, _pipeline: bool) -> str:
    """Format a gate_error response."""
    result = data.get("result", data.get("message", "Kitchen is closed."))
    lines = [f"## {_CROSS_MARK} Gate Error", "", f"message: {result}", "subtype: gate_error"]
    return "\n".join(lines)


def _response_spill_notice(metadata: dict) -> str:
    path = metadata.get("artifact_path", "unavailable")
    original_bytes = metadata.get("original_utf8_bytes", "unknown")
    return f"response_spilled: {original_bytes} bytes\nartifact_path: {path}"


# Dispatch table: short tool name → formatter function
_FORMATTERS: dict[str, Callable[..., str]] = {
    "run_skill": _fmt_run_skill,
    "run_cmd": _fmt_run_cmd,
    "test_check": _fmt_test_check,
    "merge_worktree": _fmt_merge_worktree,
    "dispatch_food_truck": _fmt_dispatch_food_truck,
    "get_token_summary": _fmt_get_token_summary,
    "get_timing_summary": _fmt_get_timing_summary,
    "kitchen_status": _fmt_kitchen_status,
    "clone_repo": _fmt_clone_repo,
    "load_recipe": _fmt_load_recipe,
    "open_kitchen": _fmt_open_kitchen,
    "list_recipes": _fmt_list_recipes,
}

# Tools explicitly opted out of dedicated formatters.
# The generic formatter is sufficient for these tools' response shapes.
# When adding a new tool, it MUST appear either in _FORMATTERS or here.
_UNFORMATTED_TOOLS: frozenset[str] = frozenset(
    {
        "run_python",  # structured result dict, generic renders correctly
        "recover_run_skill_result",  # structured recovery result
        "complete_run_skill_result",  # structured acknowledgement result
        "read_db",  # tabular rows, generic renders correctly
        "reset_test_dir",  # simple ack
        "classify_fix",  # simple classification result
        "reset_workspace",  # simple ack
        "migrate_recipe",  # simple migration result
        "remove_clone",  # simple ack
        "push_to_remote",  # simple ack
        "report_bug",  # simple result
        "prepare_issue",  # simple result
        "enrich_issues",  # simple result
        "claim_issue",  # simple result
        "release_issue",  # simple result
        "wait_for_ci",  # ci status dict, generic renders correctly
        "wait_for_merge_queue",  # merge queue result dict, generic renders correctly
        "check_repo_merge_state",  # simple JSON boolean bundle, generic renders correctly
        "toggle_auto_merge",  # simple success/error result dict, generic renders correctly
        "enqueue_pr",  # simple success/error result dict, generic renders correctly
        "create_unique_branch",  # simple result
        "write_telemetry_files",  # simple path results
        "get_pr_reviews",  # list of reviews
        "bulk_close_issues",  # bulk result
        "check_pr_mergeable",  # simple bool result
        "set_commit_status",  # simple ack
        "get_pipeline_report",  # list-of-dicts, now renders correctly via hardened _fmt_generic
        "validate_recipe",  # suggestions list, now renders correctly via hardened _fmt_generic
        "fetch_github_issue",  # issue data dict
        "get_issue_title",  # simple string
        "get_ci_status",  # ci status dict
        "get_quota_events",  # list of quota events, generic renders correctly
        "close_kitchen",  # simple ack
        "disable_quota_guard",  # simple success/error result
        "enable_exploration",  # simple success/error result
        "register_clone_status",  # simple registered/error result
        "batch_cleanup_clones",  # bulk delete summary dict
        "reload_session",  # simple status dict, generic renders correctly
        "analyze_tool_sequences",  # DFG analysis result, generic renders correctly
        "record_gate_dispatch",  # simple success/error result
        "bootstrap_clone",  # composite clone result dict
        "claim_and_resolve_issue",  # composite claim result dict
        "create_and_publish_branch",  # composite branch result dict
        "configure_fleet",  # simple config snapshot dict
        "configure_order",  # simple config snapshot dict
        "lock_ingredients",  # simple success/error result
        "record_pipeline_step",  # structured init/status result
        "inspect_session_logs",  # bounded inspection envelope, generic renders correctly
        "reset_dispatch",  # JSON cleanup report, generic renders correctly
        "get_recipe_section",  # bounded section content with continuation
        "complete_recipe_initialization",  # simple completion receipt
        "submit_exploration_query",  # bounded evidence page JSON, generic renders correctly
        "get_exploration_page",  # bounded evidence page JSON, generic renders correctly
        "resume_exploration_context",  # bounded evidence page JSON, generic renders correctly
    }
)

# Plain-text dispatch: called when _resolve_payload() returns _PlainTextPayload.
_PLAIN_TEXT_FORMATTERS: dict[str, Callable[[str, bool], str]] = {
    # open_kitchen returns a plain orchestrator-notice string when no recipe is loaded.
    # All other plain-text responses pass through unchanged (pre-formatted markdown).
    "open_kitchen": _fmt_open_kitchen_plain_text,
}


def _resolve_payload(tool_name: str, tool_response: str) -> _Payload | None:
    """Resolve a raw Claude Code PostToolUse event into a typed payload.

    Returns:
        _DictPayload  — tool returned a JSON dict (envelope successfully unwrapped,
                         or outer response was already a bare dict).
        _PlainTextPayload — tool returned a pre-formatted string (non-JSON inner content).
        None          — response cannot be parsed or is not a dict at the outer level;
                         hook should pass through.
    """
    try:
        data: Any = json.loads(tool_response)
    except (json.JSONDecodeError, ValueError):
        return None

    if not isinstance(data, dict):
        return None

    if (
        tool_name.startswith("mcp__")
        and list(data.keys()) == ["result"]
        and isinstance(data["result"], str)
    ):
        try:
            inner = json.loads(data["result"])
            if isinstance(inner, dict):
                return _DictPayload(data=inner)
            # Inner parsed but is not a dict (e.g. list, int) — pass through unformatted
            return None
        except (json.JSONDecodeError, ValueError):
            pass
        # Inner content is plain text (not valid JSON)
        return _PlainTextPayload(text=data["result"])

    return _DictPayload(data=data)


def _format_response(tool_name: str, tool_response: str, pipeline: bool) -> str | None:
    """Parse tool_response JSON and dispatch to the appropriate formatter.

    Returns formatted string or None if formatting should be skipped.
    """
    payload = _resolve_payload(tool_name, tool_response)
    if payload is None:
        return None

    short_name = _extract_tool_short_name(tool_name)

    if isinstance(payload, _PlainTextPayload):
        # Tool returned pre-formatted content. Named dict-formatters must not
        # receive this shape. Route through the plain-text dispatch table or
        # pass through unchanged.
        handler = _PLAIN_TEXT_FORMATTERS.get(short_name)
        return handler(payload.text, pipeline) if handler is not None else payload.text

    # DictPayload path — envelope was successfully unwrapped (or was never an envelope).
    data = dict(payload.data)
    recipe_segment = data.pop("recipe_segment", None)
    raw_spill_metadata = data.get(_RESPONSE_SPILL_METADATA_KEY)
    spill_metadata = _validate_response_spill_metadata(raw_spill_metadata)
    artifact_backed = spill_metadata is not None
    if artifact_backed:
        data.pop(_RESPONSE_SPILL_METADATA_KEY)
    elif _RESPONSE_SPILL_METADATA_KEY in data:
        return _fmt_generic(short_name, data, pipeline, artifact_backed=False)

    def with_spill(formatted: str) -> str:
        if not isinstance(spill_metadata, dict):
            return formatted
        notice = _response_spill_notice(spill_metadata)
        return f"{notice}\n\n{formatted}" if formatted else notice

    if isinstance(spill_metadata, dict) and set(data) == {"preview"}:
        return with_spill(str(data["preview"]))

    if data.get("subtype") == "gate_error":
        rendered = _fmt_gate_error(data, pipeline)
    elif data.get("subtype") == "tool_exception":
        rendered = _fmt_tool_exception(data, pipeline)
    elif short_name in _UNFORMATTED_TOOLS:
        rendered = _fmt_generic(short_name, data, pipeline, artifact_backed=artifact_backed)
    elif (formatter := _FORMATTERS.get(short_name)) is not None:
        rendered = formatter(data, pipeline)
    else:
        rendered = _fmt_generic(short_name, data, pipeline, artifact_backed=artifact_backed)

    error_text = data.get("error", "")
    if error_text and isinstance(error_text, str) and error_text not in rendered:
        rendered = f"{rendered}\nerror: {error_text}"

    formatted_segment = _fmt_recipe_segment(recipe_segment)
    if formatted_segment:
        rendered = f"{rendered}\n\n{formatted_segment}" if rendered else formatted_segment

    return with_spill(rendered)


def main() -> None:
    try:
        raw = sys.stdin.read()
        event = json.loads(raw)
    except (json.JSONDecodeError, ValueError, OSError):
        sys.exit(0)  # fail-open

    tool_name = event.get("tool_name", "")
    tool_response = event.get("tool_response", "")

    if not tool_name or not tool_response:
        sys.exit(0)  # no data to format

    try:
        pipeline = _is_pipeline_mode()
        formatted = _format_response(tool_name, tool_response, pipeline)
    except Exception:
        sys.exit(0)  # fail-open — never block on hook bug

    if formatted is None:
        sys.exit(0)  # pass-through

    output = json.dumps(
        {
            "hookSpecificOutput": {
                "hookEventName": "PostToolUse",
                "updatedMCPToolOutput": formatted,
            }
        }
    )
    print(output)


if __name__ == "__main__":
    main()
