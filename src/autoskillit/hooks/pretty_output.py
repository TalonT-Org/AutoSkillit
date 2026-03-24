#!/usr/bin/env python3
"""PostToolUse hook: prettify MCP tool output as Markdown-KV.

Intercepts MCP tool responses and reformats them from raw JSON to Markdown-KV
before Claude consumes them. Reduces token overhead 30-77% and improves
LLM field-extraction accuracy.

Stdlib-only — runs under any Python interpreter without the autoskillit package.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from autoskillit.recipe._api import ListRecipesResult, LoadRecipeResult

_HOOK_CONFIG_PATH_COMPONENTS = (".autoskillit", "temp", ".autoskillit_hook_config.json")


@dataclass(frozen=True)
class _DictPayload:
    data: dict[str, Any]


@dataclass(frozen=True)
class _PlainTextPayload:
    text: str


_Payload = _DictPayload | _PlainTextPayload
_CHECK_MARK = "\u2713"  # ✓
_CROSS_MARK = "\u2717"  # ✗
_WARN_MARK = "\u26a0"  # ⚠


def _is_pipeline_mode() -> bool:
    """Check if kitchen is open (pipeline mode) by hook config file presence."""
    config_path = Path.cwd().joinpath(*_HOOK_CONFIG_PATH_COMPONENTS)
    return config_path.is_file()


def _fmt_tokens(n: int | None) -> str:
    """Format a token count as compact string (45.2k, 1.2M, etc.)."""
    if n is None or n == 0:
        return "0"
    if not isinstance(n, (int, float)):
        return "0"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}k"
    return str(n)


def _extract_tool_short_name(tool_name: str) -> str:
    """Extract short tool name from full MCP tool name.

    "mcp__plugin_autoskillit_autoskillit__run_skill" -> "run_skill"
    Falls back to the full tool_name if no __ separator found.
    """
    return tool_name.rsplit("__", 1)[-1] if "__" in tool_name else tool_name


def _fmt_run_skill(data: dict, pipeline: bool) -> str:
    """Format run_skill result as Markdown-KV."""
    success = data.get("success", False)
    subtype = data.get("subtype", "")
    mark = _CHECK_MARK if success else _CROSS_MARK
    status = subtype if subtype else ("OK" if success else "FAIL")

    if pipeline:
        # Compact format for pipeline mode
        header = f"run_skill: {'OK' if success else 'FAIL'} [{status}]"
        lines = [header]
        session_id = data.get("session_id", "")
        if session_id:
            lines.append(f"session_id: {session_id}")
        lines.append(f"exit_code: {data.get('exit_code', '')}")
        lines.append(f"needs_retry: {data.get('needs_retry', False)}")
        if data.get("retry_reason") and data["retry_reason"] != "none":
            lines.append(f"retry_reason: {data['retry_reason']}")
        worktree = data.get("worktree_path", "")
        if worktree:
            lines.append(f"worktree_path: {worktree}")
        result = data.get("result", "")
        if result:
            lines.append(f"\nresult:\n{result}")
        stderr = (data.get("stderr") or "").strip()
        if stderr:
            lines.extend(["", "### stderr", stderr])
        return "\n".join(lines)

    # Interactive mode
    lines = [f"## run_skill {mark} {status}", ""]
    lines.append(f"success: {success}")
    session_id = data.get("session_id", "")
    if session_id:
        lines.append(f"session_id: {session_id}")
    lines.append(f"subtype: {subtype}")
    lines.append(f"exit_code: {data.get('exit_code', '')}")
    lines.append(f"needs_retry: {data.get('needs_retry', False)}")
    retry_reason = data.get("retry_reason", "none")
    if retry_reason and retry_reason != "none":
        lines.append(f"retry_reason: {retry_reason}")
    worktree = data.get("worktree_path", "")
    if worktree:
        lines.append(f"worktree_path: {worktree}")
    token_usage = data.get("token_usage")
    if isinstance(token_usage, dict):
        lines.append("")
        lines.append(f"tokens_in: {_fmt_tokens(token_usage.get('input_tokens'))}")
        lines.append(f"tokens_out: {_fmt_tokens(token_usage.get('output_tokens'))}")
        cr = token_usage.get("cache_read_input_tokens", 0)
        if cr:
            lines.append(f"tokens_cached: {_fmt_tokens(cr)}")
    result = data.get("result", "")
    if result:
        lines.extend(["", "### Result", result])
    stderr = data.get("stderr", "")
    if stderr:
        lines.extend(["", "### stderr", stderr])
    return "\n".join(lines)


def _fmt_run_cmd(data: dict, pipeline: bool) -> str:
    """Format run_cmd result as Markdown-KV."""
    success = data.get("success", False)
    exit_code = data.get("exit_code", "")
    mark = _CHECK_MARK if success else _CROSS_MARK

    if pipeline:
        lines = [
            f"run_cmd: {'OK' if success else 'FAIL'} [{exit_code}]",
            f"success: {success}",
            f"exit_code: {exit_code}",
        ]
        stdout = (data.get("stdout") or "").strip()
        if stdout:
            lines.extend(["", "### stdout", stdout])
        stderr = (data.get("stderr") or "").strip()
        if stderr:
            lines.extend(["", "### stderr", stderr])
        return "\n".join(lines)

    lines = [
        f"## run_cmd {mark} {'OK' if success else 'FAIL'}",
        "",
        f"success: {success}",
        f"exit_code: {exit_code}",
    ]
    stdout = (data.get("stdout") or "").strip()
    if stdout:
        lines.extend(["", "### stdout", stdout])
    stderr = (data.get("stderr") or "").strip()
    if stderr:
        lines.extend(["", "### stderr", stderr])
    return "\n".join(lines)


def _filter_pytest_output(raw: str) -> str:
    """Filter pytest boilerplate, keeping only failure-relevant lines."""
    boilerplate_prefixes = (
        "platform ",
        "rootdir:",
        "configfile:",
        "plugins:",
        "collecting ",
        "collected ",
        "cacheprovider",
    )
    boilerplate_exact = {"", " "}
    lines = raw.splitlines()
    filtered = []
    for line in lines:
        stripped = line.strip()
        if stripped in boilerplate_exact:
            continue
        if any(stripped.startswith(p) for p in boilerplate_prefixes):
            continue
        filtered.append(line)
    return "\n".join(filtered)


def _fmt_test_check(data: dict, _pipeline: bool) -> str:
    """Format test_check result as Markdown-KV."""
    passed = data.get("passed", False)
    mark = _CHECK_MARK if passed else _CROSS_MARK
    status = "PASS" if passed else "FAIL"
    lines = [f"## test_check {mark} {status}", "", f"passed: {passed}"]
    raw_output = data.get("output", "")
    if raw_output:
        filtered = _filter_pytest_output(raw_output)
        lines.extend(["", "### Output", filtered])
    error = data.get("error", "")
    if error:
        lines.extend(["", f"error: {error}"])
    return "\n".join(lines)


def _fmt_merge_worktree(data: dict, _pipeline: bool) -> str:
    """Format merge_worktree result as Markdown-KV."""
    succeeded = data.get("merge_succeeded")
    has_error = "error" in data

    if succeeded:
        mark = _CHECK_MARK
        status = "OK"
    elif has_error:
        mark = _CROSS_MARK
        status = "FAIL"
    else:
        mark = _CROSS_MARK
        status = "UNKNOWN"

    lines = [f"## merge_worktree {mark} {status}", ""]
    for key, val in data.items():
        if isinstance(val, list):
            lines.append(f"{key}:")
            for item in val:
                lines.append(f"  - {item}")
        elif isinstance(val, dict):
            continue
        elif key == "stderr":
            continue
        else:
            lines.append(f"{key}: {val}")
    stderr = (data.get("stderr") or "").strip()
    if stderr:
        lines.extend(["", "### stderr", stderr])
    return "\n".join(lines)


def _fmt_get_token_summary(data: dict, _pipeline: bool) -> str:
    """Format get_token_summary compact Markdown-KV output.

    This formatter receives only the JSON dict payload (format="json").
    When format="table" is used, the tool returns a pre-formatted markdown
    string. _resolve_payload() detects this as a _PlainTextPayload and routes
    it through _PLAIN_TEXT_FORMATTERS (pass-through), so this function is
    never called for the table format.
    """
    lines = ["## token_summary", ""]
    steps = data.get("steps", [])
    for step in steps:
        name = step.get("step_name", "?")
        count = step.get("invocation_count", 1)
        inp = _fmt_tokens(step.get("input_tokens", 0))
        out = _fmt_tokens(step.get("output_tokens", 0))
        cached = _fmt_tokens(
            step.get("cache_read_input_tokens", 0) + step.get("cache_creation_input_tokens", 0)
        )
        wc = step.get("wall_clock_seconds", step.get("elapsed_seconds", 0.0))
        lines.append(f"{name} x{count} [in:{inp} out:{out} cached:{cached} t:{wc:.1f}s]")
    total = data.get("total", {})
    if total:
        lines.append("")
        lines.append(f"total_in: {_fmt_tokens(total.get('input_tokens', 0))}")
        lines.append(f"total_out: {_fmt_tokens(total.get('output_tokens', 0))}")
        cache_tokens = total.get("cache_read_input_tokens", 0) + total.get(
            "cache_creation_input_tokens", 0
        )
        lines.append(f"total_cached: {_fmt_tokens(cache_tokens)}")
    mcp = data.get("mcp_responses", {})
    mcp_total = mcp.get("total", {})
    if mcp_total:
        lines.append("")
        lines.append(f"mcp_invocations: {mcp_total.get('total_invocations', 0)}")
        est_tokens = mcp_total.get("total_estimated_response_tokens", 0)
        lines.append(f"mcp_response_tokens: ~{_fmt_tokens(est_tokens)}")
    return "\n".join(lines)


def _fmt_get_timing_summary(data: dict, _pipeline: bool) -> str:
    """Format get_timing_summary compact Markdown-KV output.

    This formatter receives only the JSON dict payload (format="json").
    When format="table" is used, the tool returns a pre-formatted markdown
    string. _resolve_payload() detects this as a _PlainTextPayload and routes
    it through _PLAIN_TEXT_FORMATTERS (pass-through), so this function is
    never called for the table format.

    Each step becomes: name xN [dur:Xs]
    """
    lines = ["## timing_summary", ""]
    steps = data.get("steps", [])
    for step in steps:
        name = step.get("step_name", "?")
        count = step.get("invocation_count", 1)
        secs = step.get("total_seconds", 0.0)
        if secs < 60:
            dur = f"{secs:.0f}s"
        elif secs < 3600:
            m, s = divmod(int(secs), 60)
            dur = f"{m}m {s}s"
        else:
            h, remainder = divmod(int(secs), 3600)
            m = remainder // 60
            dur = f"{h}h {m}m"
        lines.append(f"{name} x{count} [dur:{dur}]")
    total = data.get("total", {})
    if total:
        total_secs = total.get("total_seconds", 0.0)
        if total_secs < 60:
            total_dur = f"{total_secs:.0f}s"
        elif total_secs < 3600:
            m, s = divmod(int(total_secs), 60)
            total_dur = f"{m}m {s}s"
        else:
            h, remainder = divmod(int(total_secs), 3600)
            m = remainder // 60
            total_dur = f"{h}h {m}m"
        lines.append("")
        lines.append(f"total: {total_dur}")
    return "\n".join(lines)


def _fmt_kitchen_status(data: dict, _pipeline: bool) -> str:
    """Format kitchen_status as Markdown-KV."""
    success = not data.get("error")
    mark = _CHECK_MARK if success else _CROSS_MARK
    enabled = data.get("tools_enabled", False)
    gate_str = "OPEN" if enabled else "CLOSED"
    lines = [f"## kitchen_status {mark} {gate_str}", ""]
    for key in (
        "package_version",
        "plugin_json_version",
        "versions_match",
        "tools_enabled",
        "token_usage_verbosity",
        "quota_guard_enabled",
        "github_token_configured",
        "github_default_repo",
    ):
        if key in data:
            lines.append(f"{key}: {data[key]}")
    warning = data.get("warning")
    if warning:
        lines.extend(["", f"warning: {warning}"])
    return "\n".join(lines)


def _fmt_clone_repo(data: dict, _pipeline: bool) -> str:
    """Format clone_repo result as Markdown-KV."""
    is_warning = "uncommitted_changes" in data or "unpublished_branch" in data
    has_error = "error" in data

    if is_warning:
        mark = "\u26a0"
        status = "WARNING"
    elif has_error:
        mark = _CROSS_MARK
        status = "FAIL"
    else:
        mark = _CHECK_MARK
        status = "OK"

    lines = [f"## clone_repo {mark} {status}", ""]
    for key, val in data.items():
        if isinstance(val, (dict, list)):
            continue
        lines.append(f"{key}: {val}")
    return "\n".join(lines)


# Field coverage contract for _fmt_load_recipe ↔ LoadRecipeResult
_FMT_LOAD_RECIPE_RENDERED: frozenset[str] = frozenset(
    {
        "valid",
        "suggestions",
        "error",
        "content",
        "ingredients_table",
    }
)
_FMT_LOAD_RECIPE_SUPPRESSED: frozenset[str] = frozenset(
    {
        "greeting",  # delivered via positional CLI arg, not MCP response
        "diagram",  # user sees it in terminal preview; agent doesn't need it
        "kitchen_rules",  # already in the YAML content
    }
)


def _fmt_recipe_body(data: Mapping[str, Any]) -> list[str]:
    """Shared recipe content rendering for load_recipe and open_kitchen+recipe."""
    lines: list[str] = []
    content = data.get("content")
    if content:
        lines.append("\n--- RECIPE ---")
        lines.append(content)
        lines.append("--- END RECIPE ---")
    ing_table = data.get("ingredients_table")
    if ing_table:
        lines.append("\n--- INGREDIENTS TABLE (display this verbatim to the user) ---")
        lines.append(ing_table)
        lines.append("--- END TABLE ---")
    suggestions = data.get("suggestions") or []
    errors = [
        f for f in suggestions if isinstance(f, dict) and f.get("severity") in ("error", "warning")
    ]
    if errors:
        lines.append(f"\n{len(errors)} finding(s)")
    return lines


def _fmt_load_recipe(data: LoadRecipeResult, pipeline: bool) -> str:
    """Format load_recipe result as Markdown-KV."""
    if not isinstance(data, dict):
        return "## load_recipe\n\n_(unexpected response type)_"

    error = data.get("error")
    if error:
        return f"## load_recipe {_CROSS_MARK}\n\n**Error:** {error}"

    valid = data.get("valid", True)
    mark = _CHECK_MARK if valid else _CROSS_MARK
    lines: list[str] = [f"## load_recipe {mark}"]
    lines.extend(_fmt_recipe_body(data))
    return "\n".join(lines)


# Field coverage contract for _fmt_list_recipes ↔ ListRecipesResult
_FMT_LIST_RECIPES_RENDERED: frozenset[str] = frozenset(
    {
        "recipes",
        "count",
        "errors",
    }
)
_FMT_LIST_RECIPES_SUPPRESSED: frozenset[str] = frozenset()

# Field coverage contract for per-item recipe entries ↔ RecipeListItem
_FMT_RECIPE_LIST_ITEM_RENDERED: frozenset[str] = frozenset(
    {
        "name",
        "description",
        "summary",
        "source",
    }
)
_FMT_RECIPE_LIST_ITEM_SUPPRESSED: frozenset[str] = frozenset()


def _fmt_open_kitchen(data: dict, pipeline: bool) -> str:
    """Format open_kitchen combined kitchen+recipe result."""
    version = data.get("version", "")

    error = data.get("error")
    if error:
        return f"## open_kitchen {_CROSS_MARK} v{version}\n\nKitchen open. Recipe error: {error}"

    valid = data.get("valid", True)
    mark = _CHECK_MARK if valid else _CROSS_MARK
    lines: list[str] = [f"## open_kitchen {mark} v{version}"]
    lines.extend(_fmt_recipe_body(data))
    return "\n".join(lines)


def _fmt_open_kitchen_plain_text(text: str, _pipeline: bool) -> str:
    """Format open_kitchen plain-text response (no recipe attached)."""
    return f"## open_kitchen\n\n{text}"


def _fmt_list_recipes(data: ListRecipesResult, pipeline: bool) -> str:
    """Format list_recipes result as Markdown-KV."""
    if not isinstance(data, dict):
        return "## list_recipes\n\n_(unexpected response type)_"
    lines: list[str] = ["## list_recipes"]
    recipes = data.get("recipes") or []
    for recipe in recipes[:30]:
        if isinstance(recipe, dict):
            name = recipe.get("name", "?")
            desc = recipe.get("description", "")
            summary = recipe.get("summary", "")
            source = recipe.get("source", "")
            source_tag = f" [{source}]" if source else ""
            lines.append(f"  - {name}{source_tag}: {desc}" if desc else f"  - {name}{source_tag}")
            if summary:
                lines.append(f"    {summary}")
        else:
            lines.append(f"  - {recipe}")
    if len(recipes) > 30:
        lines.append(f"  ... and {len(recipes) - 30} more")
    count = data.get("count", len(recipes))
    lines.append(f"\n{count} recipe(s) available")
    errors = data.get("errors") or []
    if errors:
        lines.append(f"\n{_WARN_MARK} {len(errors)} recipe file(s) had load errors")
    return "\n".join(lines)


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


def _fmt_generic(short_name: str, data: dict, _pipeline: bool) -> str:
    """Generic key-value formatter for tools without dedicated formatters."""
    lines = [f"## {short_name}", ""]
    for key, val in data.items():
        if isinstance(val, list):
            val = list(val)
            if not val:
                lines.append(f"{key}: []")
            elif all(isinstance(item, str) for item in val):
                lines.append(f"{key}:")
                for item in val[:20]:
                    lines.append(f"  - {item}")
                if len(val) > 20:
                    lines.append(f"  ... and {len(val) - 20} more")
            else:
                # Non-string list (list-of-dicts or mixed): render per-item up to 20-item cap
                lines.append(f"{key}:")
                for item in val[:20]:
                    if isinstance(item, dict):
                        # Render first two key-value pairs inline for readability
                        parts = [f"{k}: {v}" for k, v in list(item.items())[:2]]
                        lines.append(f"  - {', '.join(parts)}")
                    else:
                        compact = json.dumps(item, separators=(",", ":"))
                        lines.append(f"  - {compact[:200]}")
                if len(val) > 20:
                    lines.append(f"  ... and {len(val) - 20} more")
        elif isinstance(val, dict):
            if not val:
                lines.append(f"{key}: {{}}")
            else:
                lines.append(f"{key}:")
                for k, v in val.items():
                    if isinstance(v, (dict, list)):
                        compact = json.dumps(v, separators=(",", ":"))
                        if len(compact) > 200:
                            compact = compact[:200] + "..."
                        lines.append(f"  {k}: {compact}")
                    else:
                        lines.append(f"  {k}: {v}")
        else:
            lines.append(f"{key}: {val}")
    return "\n".join(lines)


# Dispatch table: short tool name → formatter function
_FORMATTERS: dict[str, Callable[..., str]] = {
    "run_skill": _fmt_run_skill,
    "run_cmd": _fmt_run_cmd,
    "test_check": _fmt_test_check,
    "merge_worktree": _fmt_merge_worktree,
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
        "toggle_auto_merge",  # simple success/error result dict, generic renders correctly
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
        "register_clone_status",  # simple registered/error result
        "batch_cleanup_clones",  # bulk delete summary dict
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
        data = json.loads(tool_response)
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
    data = payload.data

    if data.get("subtype") == "gate_error":
        return _fmt_gate_error(data, pipeline)
    if data.get("subtype") == "tool_exception":
        return _fmt_tool_exception(data, pipeline)

    if short_name in _UNFORMATTED_TOOLS:
        return _fmt_generic(short_name, data, pipeline)

    formatter = _FORMATTERS.get(short_name)
    if formatter is not None:
        return formatter(data, pipeline)

    return _fmt_generic(short_name, data, pipeline)


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
    sys.exit(0)


if __name__ == "__main__":
    main()
