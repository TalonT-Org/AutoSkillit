#!/usr/bin/env python3
"""PostToolUse hook: prettify MCP tool output as Markdown-KV.

Intercepts MCP tool responses and reformats them from raw JSON to Markdown-KV
before Claude consumes them. Reduces token overhead 30-77% and improves
LLM field-extraction accuracy.

Stdlib-only — runs under any Python interpreter without the autoskillit package.
"""

import json
import sys
from pathlib import Path

_HOOK_CONFIG_PATH_COMPONENTS = (".autoskillit", "temp", ".autoskillit_hook_config.json")
_CHECK_MARK = "\u2713"  # ✓
_CROSS_MARK = "\u2717"  # ✗


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
    return "\n".join(lines)


def _fmt_merge_worktree(data: dict, _pipeline: bool) -> str:
    """Format merge_worktree result as Markdown-KV."""
    has_error = "error" in data
    mark = _CROSS_MARK if has_error else _CHECK_MARK
    status = "FAIL" if has_error else "OK"
    lines = [f"## merge_worktree {mark} {status}", ""]
    if has_error:
        lines.append(f"error: {data['error']}")
    failed_step = data.get("failed_step")
    if failed_step:
        lines.append(f"failed_step: {failed_step}")
    state = data.get("state")
    if state:
        lines.append(f"state: {state}")
    merged = data.get("merged")
    if merged is not None:
        lines.append(f"merged: {merged}")
    worktree_path = data.get("worktree_path", "")
    if worktree_path:
        lines.append(f"worktree_path: {worktree_path}")
    branch = data.get("branch")
    if branch:
        lines.append(f"branch: {branch}")
    stderr = (data.get("stderr") or "").strip()
    if stderr:
        lines.extend(["", "### stderr", stderr])
    return "\n".join(lines)


def _fmt_get_token_summary(data: dict, _pipeline: bool) -> str:
    """Format get_token_summary as compact Markdown-KV.

    Each step becomes: name xN [in:Xk out:Xk cached:XM]
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
        lines.append(f"{name} x{count} [in:{inp} out:{out} cached:{cached}]")
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
    has_error = "error" in data
    mark = _CROSS_MARK if has_error else _CHECK_MARK
    lines = [f"## clone_repo {mark} {'FAIL' if has_error else 'OK'}", ""]
    for key in ("clone_path", "source_dir", "remote_url", "error"):
        if key in data:
            lines.append(f"{key}: {data[key]}")
    return "\n".join(lines)


def _fmt_gate_error(data: dict, _pipeline: bool) -> str:
    """Format a gate_error response."""
    result = data.get("result", data.get("message", "Kitchen is closed."))
    lines = [f"## {_CROSS_MARK} Gate Error", "", f"message: {result}", "subtype: gate_error"]
    return "\n".join(lines)


def _fmt_generic(short_name: str, data: dict, _pipeline: bool) -> str:
    """Generic key-value formatter for unrecognized tools."""
    lines = [f"## {short_name}", ""]
    for key, val in data.items():
        if isinstance(val, (dict, list)):
            continue  # skip nested structures
        lines.append(f"{key}: {val}")
    return "\n".join(lines)


# Dispatch table: short tool name → formatter function
_FORMATTERS = {
    "run_skill": _fmt_run_skill,
    "run_cmd": _fmt_run_cmd,
    "test_check": _fmt_test_check,
    "merge_worktree": _fmt_merge_worktree,
    "get_token_summary": _fmt_get_token_summary,
    "kitchen_status": _fmt_kitchen_status,
    "clone_repo": _fmt_clone_repo,
}


def _format_response(tool_name: str, tool_response: str, pipeline: bool) -> str | None:
    """Parse tool_response JSON and dispatch to the appropriate formatter.

    Returns formatted string or None if formatting should be skipped.
    """
    try:
        data = json.loads(tool_response)
    except (json.JSONDecodeError, ValueError):
        return None  # non-JSON response -> skip

    if not isinstance(data, dict):
        return None

    # Gate error: any tool can return this
    if data.get("subtype") == "gate_error":
        return _fmt_gate_error(data, pipeline)

    short_name = _extract_tool_short_name(tool_name)
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
