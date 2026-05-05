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


def _format_exit_code_line(data: dict) -> str:
    """Return the formatted exit_code line, annotated with kill_reason when present.

    Legacy payloads without kill_reason render as bare exit_code values so that
    JSON-line replay of old session logs still parses correctly.
    """
    exit_code = data.get("exit_code", "")
    kill_reason = data.get("kill_reason")
    if kill_reason is None:
        # Legacy payload — no annotation
        return f"exit_code: {exit_code}"
    if kill_reason == "kill_after_completion":
        return f"exit_code: {exit_code} (infra-terminated after completion — grace exceeded)"
    if kill_reason == "infra_kill":
        termination_reason = data.get("termination_reason") or data.get("subtype") or ""
        if termination_reason:
            return f"exit_code: {exit_code} (infra-killed: {termination_reason})"
        return f"exit_code: {exit_code} (infra-killed)"
    # natural_exit or any unknown value → bare
    return f"exit_code: {exit_code}"


def _maybe_provider_line(data: dict, lines: list[str]) -> None:
    provider_used = data.get("provider_used", "")
    if provider_used and provider_used != "anthropic":
        suffix = " [FALLBACK]" if data.get("provider_fallback", False) else ""
        lines.append(f"provider: {provider_used}{suffix}")


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
        lines.append(_format_exit_code_line(data))
        lines.append(f"needs_retry: {data.get('needs_retry', False)}")
        if data.get("retry_reason") and data["retry_reason"] != "none":
            lines.append(f"retry_reason: {data['retry_reason']}")
        worktree = data.get("worktree_path", "")
        if worktree:
            lines.append(f"worktree_path: {worktree}")
        _maybe_provider_line(data, lines)
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
    lines.append(_format_exit_code_line(data))
    lines.append(f"needs_retry: {data.get('needs_retry', False)}")
    retry_reason = data.get("retry_reason", "none")
    if retry_reason and retry_reason != "none":
        lines.append(f"retry_reason: {retry_reason}")
    worktree = data.get("worktree_path", "")
    if worktree:
        lines.append(f"worktree_path: {worktree}")
    _maybe_provider_line(data, lines)
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

    duration = data.get("duration_seconds")
    if duration is not None:
        lines.append(f"duration: {duration:.1f}s")

    filter_mode = data.get("filter_mode")
    if filter_mode:
        full_run_reason = data.get("full_run_reason")
        if full_run_reason:
            reason_display = full_run_reason.replace("_", " ")
            lines.append(f"filter: {filter_mode} (full run — {reason_display})")
        else:
            selected = data.get("tests_selected", "?")
            deselected = data.get("tests_deselected", "?")
            lines.append(f"filter: {filter_mode} ({selected} selected, {deselected} deselected)")
    else:
        lines.append("filter: off")

    stdout = data.get("stdout", "")
    if stdout:
        filtered = _filter_pytest_output(stdout)
        lines.extend(["", "### stdout", filtered])
    stderr = data.get("stderr", "")
    if stderr:
        lines.extend(["", "### stderr", stderr])
    error = data.get("error", "")
    if error:
        lines.extend(["", f"error: {error}"])
    return "\n".join(lines)


_FMT_RUN_SKILL_RENDERED: frozenset[str] = frozenset(
    {
        "success",
        "subtype",
        "session_id",
        "exit_code",
        "kill_reason",
        "needs_retry",
        "retry_reason",
        "worktree_path",
        "result",
        "stderr",
        "token_usage",
    }
)
_FMT_RUN_SKILL_SUPPRESSED: frozenset[str] = frozenset(
    {
        "cli_subtype",
        "is_error",
        "write_path_warnings",
        "write_call_count",
        "fs_writes_detected",
        "last_stop_reason",
        "lifespan_started",
        "order_id",
        "infra_exit_category",
    }
)

_FMT_RUN_CMD_RENDERED: frozenset[str] = frozenset({"success", "exit_code", "stdout", "stderr"})
_FMT_RUN_CMD_SUPPRESSED: frozenset[str] = frozenset({"error"})

_FMT_TEST_CHECK_RENDERED: frozenset[str] = frozenset(
    {
        "passed",
        "duration_seconds",
        "filter_mode",
        "full_run_reason",
        "tests_selected",
        "tests_deselected",
        "stdout",
        "stderr",
        "error",
    }
)
_FMT_TEST_CHECK_SUPPRESSED: frozenset[str] = frozenset()


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


_FMT_MERGE_WORKTREE_RENDERED: frozenset[str] = frozenset(
    {
        "merge_succeeded",
        "merged_branch",
        "into_branch",
        "worktree_removed",
        "branch_deleted",
        "cleanup_succeeded",
        "error",
        "failed_step",
        "state",
        "worktree_path",
        "stderr",
        "base_branch",
        "dirty_files",
        "merge_commits",
        "test_stdout",
        "test_stderr",
        "abort_failed",
        "abort_stderr",
        "poisoned_installs",
    }
)
_FMT_MERGE_WORKTREE_SUPPRESSED: frozenset[str] = frozenset()
