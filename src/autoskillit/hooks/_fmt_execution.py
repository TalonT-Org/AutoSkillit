"""Execution-tool formatters for the pretty_output split.

Hosts the per-tool formatters for ``run_skill``, ``run_cmd``, ``test_check``,
and ``merge_worktree``. Stdlib-only at runtime.
"""

from __future__ import annotations

from _fmt_primitives import (  # type: ignore[import-not-found]
    _CHECK_MARK,
    _CROSS_MARK,
    _fmt_tokens,
)


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
        lines.append(f"tokens_uncached: {_fmt_tokens(token_usage.get('input_tokens'))}")
        lines.append(f"tokens_out: {_fmt_tokens(token_usage.get('output_tokens'))}")
        cr = token_usage.get("cache_read_input_tokens", 0)
        if cr:
            lines.append(f"tokens_cache_read: {_fmt_tokens(cr)}")
        cw = token_usage.get("cache_creation_input_tokens", 0)
        if cw:
            lines.append(f"tokens_cache_write: {_fmt_tokens(cw)}")
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
