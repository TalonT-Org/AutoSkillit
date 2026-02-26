#!/usr/bin/env python3
"""MCP server for orchestrating automated skill-driven workflows.

All tools are gated by default and require the user to type the
open_kitchen prompt to activate. The prompt name depends on how the
server is loaded (plugin vs --plugin-dir). This uses MCP prompts
(user-controlled, model cannot invoke) to set an in-memory flag
that each tool checks before executing. The gate survives
--dangerously-skip-permissions.

Transport: stdio (default for FastMCP).
"""

from __future__ import annotations

import asyncio
import base64
import importlib
import inspect
import json
import os
import re
import shutil
import sqlite3
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog
from fastmcp import Context, FastMCP
from fastmcp.dependencies import CurrentContext
from fastmcp.prompts.prompt import Message, PromptResult

from autoskillit._audit import FailureRecord, _audit_log
from autoskillit._logging import get_logger
from autoskillit._token_log import _token_log
from autoskillit.config import AutomationConfig, load_config
from autoskillit.process_lifecycle import (
    SubprocessResult,
    TerminationReason,
    _extract_text_content,
    run_managed_async,
)
from autoskillit.types import (
    CONTEXT_EXHAUSTION_MARKER,
    PIPELINE_FORBIDDEN_TOOLS,
    MergeFailedStep,
    MergeState,
    RestartScope,
    RetryReason,
)

mcp = FastMCP("autoskillit")

_config: AutomationConfig = load_config(Path.cwd())

_plugin_dir = str(Path(__file__).parent)

_tools_enabled = False

logger = get_logger(__name__)


def _version_info() -> dict:
    """Return version health information for the running server."""
    from autoskillit import __version__

    plugin_json_path = Path(_plugin_dir) / ".claude-plugin" / "plugin.json"
    plugin_version = None
    if plugin_json_path.is_file():
        data = json.loads(plugin_json_path.read_text())
        plugin_version = data.get("version")

    return {
        "package_version": __version__,
        "plugin_json_version": plugin_version,
        "match": __version__ == plugin_version,
    }


def _gate_error_result(error_message: str) -> str:
    """Build a standard skill result for gate errors (tools disabled, dry-walkthrough)."""
    return json.dumps(
        {
            "success": False,
            "result": error_message,
            "session_id": "",
            "subtype": "gate_error",
            "is_error": True,
            "exit_code": -1,
            "needs_retry": False,
            "retry_reason": RetryReason.NONE,
            "stderr": "",
            "token_usage": None,
        }
    )


def _require_enabled() -> str | None:
    """Return error JSON if tools are not enabled, None if OK.

    All tools are gated by default and can only be activated by the user
    typing the open_kitchen prompt. The prompt name is prefixed by Claude
    Code based on how the server was loaded (plugin vs --plugin-dir).
    This survives --dangerously-skip-permissions because MCP prompts are
    outside the permission system.
    """
    if not _tools_enabled:
        return _gate_error_result(
            "AutoSkillit tools are not enabled. "
            "User must type the open_kitchen prompt to activate. "
            "Check the MCP prompt list for the exact name."
        )
    return None


async def _run_subprocess(
    cmd: list[str],
    *,
    cwd: str,
    timeout: float,
) -> tuple[int, str, str]:
    """Run a subprocess asynchronously with timeout. Returns (returncode, stdout, stderr).

    Delegates to run_managed_async which uses temp file I/O (immune to
    pipe-blocking from child FD inheritance) and psutil process tree cleanup.
    """
    result = await run_managed_async(cmd, cwd=Path(cwd), timeout=timeout)
    if result.termination == TerminationReason.TIMED_OUT:
        return -1, result.stdout, f"Process timed out after {timeout}s"
    return result.returncode, result.stdout, result.stderr


def _check_dry_walkthrough(skill_command: str, cwd: str) -> str | None:
    """If skill_command is an implement skill, verify the plan has been dry-walked.

    Returns an error JSON string if validation fails, None if OK.
    """
    parts = skill_command.strip().split(None, 1)
    if not parts or parts[0] not in _config.implement_gate.skill_names:
        return None

    skill_name = parts[0]

    if len(parts) < 2:
        return _gate_error_result(f"Missing plan path argument for {skill_name}")

    plan_path = Path(cwd) / parts[1].strip().strip('"').strip("'")
    if not plan_path.is_file():
        return _gate_error_result(f"Plan file not found: {plan_path}")

    first_line = plan_path.read_text().split("\n", 1)[0].strip()
    if first_line != _config.implement_gate.marker:
        return _gate_error_result(
            f"Plan has NOT been dry-walked. Run /dry-walkthrough on the plan first. "
            f"Expected first line: {_config.implement_gate.marker!r}, "
            f"actual: {first_line[:100]!r}"
        )

    return None


def _ensure_skill_prefix(skill_command: str) -> str:
    """Ensure skill commands start with 'Use' for headless session loading."""
    stripped = skill_command.strip()
    if stripped.startswith("/"):
        return f"Use {stripped}"
    return skill_command


def _truncate(text: str, max_len: int = 5000) -> str:
    if len(text) <= max_len:
        return text
    return f"...[truncated {len(text) - max_len} chars]...\n" + text[-max_len:]


def _session_log_dir(cwd: str) -> Path:
    """Derive Claude Code session log directory from project cwd."""
    project_hash = cwd.replace("/", "-").replace("_", "-")
    return Path.home() / ".claude" / "projects" / project_hash


def _inject_completion_directive(skill_command: str, marker: str) -> str:
    """Append an orchestration directive to make the session write a completion marker."""
    directive = (
        f"\n\nORCHESTRATION DIRECTIVE: When your task is complete, "
        f"your final text output MUST end with: {marker}"
    )
    return skill_command + directive


_FAILURE_SUBTYPES = frozenset({"unknown", "empty_output", "unparseable", "timeout"})


def _compute_success(
    session: ClaudeSessionResult,
    returncode: int,
    termination: TerminationReason,
    completion_marker: str = "",
) -> bool:
    """Cross-validate all signals to determine unambiguous success/failure."""
    if termination in (TerminationReason.TIMED_OUT, TerminationReason.STALE):
        return False
    if returncode != 0:
        # COMPLETED path: the process was killed by our own async_kill_process_tree
        # (signal -15 or -9), so a non-zero returncode is expected and trustworthy
        # when the session envelope says "success". Trust the envelope.
        #
        # NATURAL_EXIT path: the process exited on its own with an error code.
        # We cannot distinguish PTY-masking quirks from genuine CLI errors here,
        # so we fail conservatively. The session result record (if any) may still
        # be present in stdout — but a non-zero natural exit is treated as authoritative
        # evidence of failure. No asymmetric bypass is applied.
        if (
            termination == TerminationReason.COMPLETED
            and session.subtype == "success"
            and session.result.strip()
        ):
            pass  # fall through to remaining checks
        else:
            return False
    if session.is_error:
        return False
    if not session.result.strip():
        return False
    if session.subtype in _FAILURE_SUBTYPES:
        return False

    if completion_marker:
        result_text = session.result.strip()
        marker_stripped = result_text.replace(completion_marker, "").strip()
        if not marker_stripped:
            return False
        if completion_marker not in result_text:
            return False

    return True


def _compute_retry(
    session: ClaudeSessionResult,
    returncode: int,
    termination: TerminationReason,
) -> tuple[bool, RetryReason]:
    """Cross-validate all signals to determine retry eligibility."""
    # API-level retries (session knew it should be retried)
    if session.needs_retry:
        return True, RetryReason.RESUME

    # Infrastructure failure: session never ran (empty stdout, clean exit)
    if session.subtype == "empty_output" and returncode == 0:
        return True, RetryReason.RESUME

    # unparseable output under COMPLETED means the process was killed mid-write
    # (drain timeout expired). The session likely completed; retry with resume.
    if session.subtype == "unparseable" and termination == TerminationReason.COMPLETED:
        return True, RetryReason.RESUME

    return False, RetryReason.NONE


def _capture_failure(
    skill_command: str,
    exit_code: int,
    subtype: str,
    needs_retry: bool,
    retry_reason: str,
    stderr: str,
) -> None:
    """Record a failure in the audit log. No-op if skill_command is empty."""
    if not skill_command:
        return
    _audit_log.record_failure(
        FailureRecord(
            timestamp=datetime.now(UTC).isoformat(),
            skill_command=skill_command,
            exit_code=exit_code,
            subtype=subtype,
            needs_retry=needs_retry,
            retry_reason=retry_reason,
            stderr=stderr,
        )
    )


def _build_skill_result(
    result: SubprocessResult,
    completion_marker: str = "",
    skill_command: str = "",
) -> str:
    """Route SubprocessResult fields into the standard run_skill JSON response."""
    if result.termination == TerminationReason.STALE:
        # Attempt to recover from stdout before declaring stale failure.
        # A session that completed its result record before going quiet deserves
        # to have its output honored.
        stale_session = parse_session_result(result.stdout)
        if (
            stale_session.subtype == "success"
            and stale_session.result.strip()
            and not stale_session.is_error
        ):
            # The session wrote a valid result before going stale.
            # Treat as COMPLETED rather than STALE.
            stale_returncode = result.returncode if result.returncode is not None else -1
            success = _compute_success(
                stale_session,
                stale_returncode,
                TerminationReason.COMPLETED,
                completion_marker=completion_marker,
            )
            if success:
                logger.warning(
                    "Session went stale but stdout contained a valid result; recovering"
                )
                return json.dumps(
                    {
                        "success": True,
                        "result": _truncate(stale_session.agent_result),
                        "session_id": stale_session.session_id,
                        "subtype": "recovered_from_stale",
                        "is_error": False,
                        "exit_code": stale_returncode,
                        "needs_retry": False,
                        "retry_reason": RetryReason.NONE,
                        "stderr": result.stderr if result.stderr else "",
                        "token_usage": stale_session.token_usage,
                    }
                )
        # No valid result in stdout — fall through to original stale response
        _capture_failure(
            skill_command,
            exit_code=result.returncode if result.returncode is not None else -1,
            subtype="stale",
            needs_retry=True,
            retry_reason=RetryReason.RESUME,
            stderr=result.stderr if result.stderr else "",
        )
        return json.dumps(
            {
                "success": False,
                "result": "Session went stale (no activity for configured threshold). "
                "Partial progress may have been made. Retry to continue.",
                "session_id": "",
                "subtype": "stale",
                "is_error": False,
                "exit_code": -1,
                "needs_retry": True,
                "retry_reason": RetryReason.RESUME,
                "stderr": "",
                "token_usage": None,
            }
        )

    if result.termination == TerminationReason.TIMED_OUT:
        returncode = -1
        session = ClaudeSessionResult(
            subtype="timeout",
            is_error=True,
            result=_truncate(result.stdout) if result.stdout.strip() else "",
            session_id="",
            errors=[],
        )
    else:
        returncode = result.returncode if result.returncode is not None else -1
        session = parse_session_result(result.stdout)

    success = _compute_success(session, returncode, result.termination, completion_marker)
    needs_retry, retry_reason = _compute_retry(session, returncode, result.termination)

    if not success or needs_retry:
        _capture_failure(
            skill_command,
            exit_code=returncode,
            subtype=session.subtype,
            needs_retry=needs_retry,
            retry_reason=retry_reason.value,
            stderr=result.stderr if result.stderr else "",
        )

    result_text = _truncate(session.agent_result)
    if completion_marker:
        result_text = result_text.replace(completion_marker, "").strip()

    return json.dumps(
        {
            "success": success,
            "result": result_text,
            "session_id": session.session_id,
            "subtype": session.subtype,
            "is_error": session.is_error,
            "exit_code": returncode,
            "needs_retry": needs_retry,
            "retry_reason": retry_reason,
            "stderr": _truncate(result.stderr),
            "token_usage": session.token_usage,
        }
    )


@dataclass
class ClaudeSessionResult:
    """Parsed result from a Claude Code headless session."""

    subtype: str  # "success", "error_max_turns", "error_during_execution", etc.
    is_error: bool
    result: str
    session_id: str
    errors: list[str] = field(default_factory=list)
    token_usage: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.result, str):
            self.result = _extract_text_content(self.result)
        if not isinstance(self.errors, list):
            self.errors = [] if self.errors is None else [str(self.errors)]
        if not isinstance(self.subtype, str):
            self.subtype = "unknown" if self.subtype is None else str(self.subtype)
        if not isinstance(self.session_id, str):
            self.session_id = "" if self.session_id is None else str(self.session_id)

    def _is_context_exhausted(self) -> bool:
        """Detect context window exhaustion from Claude's error output.

        Requires both ``is_error=True`` AND the marker to appear in the
        ``errors`` list (structured CLI signal).  Falls back to checking
        ``result`` only when the subtype is a known error subtype, to
        narrow false-positives from model prose that happens to contain
        the marker phrase.
        """
        if not self.is_error:
            return False
        # Primary: check the structured errors list from Claude CLI
        marker = CONTEXT_EXHAUSTION_MARKER
        if any(marker in e.lower() for e in self.errors):
            return True
        # Fallback: only trust result text for error subtypes, not execution errors
        # where the model's own output could contain the marker phrase
        if self.subtype in ("success", "error_max_turns") and marker in self.result.lower():
            return True
        return False

    @property
    def agent_result(self) -> str:
        """Result text rewritten for LLM agent consumption.

        When the session ended due to a retriable condition (context exhaustion,
        max turns), the raw result text from Claude CLI can be misleading to
        LLM callers. This property returns semantically correct, actionable text.
        The raw result is preserved in self.result for debugging.
        """
        if self._is_context_exhausted():
            return (
                "Context limit reached during session execution. "
                "The session made partial progress. "
                "Use needs_retry and retry_reason to continue from where it left off."
            )
        if self.subtype == "error_max_turns":
            return (
                "Turn limit reached during session execution. "
                "The session made partial progress. "
                "Use needs_retry and retry_reason to continue from where it left off."
            )
        return self.result

    @property
    def needs_retry(self) -> bool:
        """Whether the session didn't finish and should be retried."""
        if self.subtype == "error_max_turns":
            return True
        if self._is_context_exhausted():
            return True
        return False

    @property
    def retry_reason(self) -> RetryReason:
        """Why retry is needed. NONE if needs_retry is False."""
        if self.needs_retry:
            return RetryReason.RESUME
        return RetryReason.NONE


_TOKEN_FIELDS = (
    "input_tokens",
    "output_tokens",
    "cache_creation_input_tokens",
    "cache_read_input_tokens",
)


def extract_token_usage(stdout: str) -> dict[str, Any] | None:
    """Extract token usage from Claude CLI NDJSON output.

    Scans assistant records for per-model usage and the result record
    for authoritative aggregated totals.  Returns None if no usage
    data is found.
    """
    if not stdout.strip():
        return None

    model_buckets: dict[str, dict[str, int]] = {}
    result_usage: dict[str, int] | None = None

    for line in stdout.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict):
            continue

        record_type = obj.get("type")

        if record_type == "assistant":
            msg = obj.get("message")
            if not isinstance(msg, dict):
                continue
            usage = msg.get("usage")
            if not isinstance(usage, dict):
                continue
            model = msg.get("model", "unknown")
            bucket = model_buckets.setdefault(model, {f: 0 for f in _TOKEN_FIELDS})
            for f in _TOKEN_FIELDS:
                bucket[f] += usage.get(f, 0)

        elif record_type == "result":
            usage = obj.get("usage")
            if isinstance(usage, dict):
                result_usage = {f: usage.get(f, 0) for f in _TOKEN_FIELDS}

    if not model_buckets and result_usage is None:
        return None

    # Aggregated totals: prefer result record, fall back to assistant sum
    if result_usage is not None:
        totals = dict(result_usage)
    else:
        totals = {f: 0 for f in _TOKEN_FIELDS}
        for bucket in model_buckets.values():
            for f in _TOKEN_FIELDS:
                totals[f] += bucket[f]

    return {
        **totals,
        "model_breakdown": dict(model_buckets) if model_buckets else {},
    }


def parse_session_result(stdout: str) -> ClaudeSessionResult:
    """Parse Claude Code's --output-format json stdout into a typed result.

    Handles multi-line NDJSON (Claude Code may emit multiple JSON objects;
    the last 'result' type object is authoritative).
    Falls back gracefully for non-JSON or missing fields.
    """
    if not stdout.strip():
        return ClaudeSessionResult(
            subtype="empty_output",
            is_error=True,
            result="",
            session_id="",
            errors=[],
        )

    result_obj = None
    for line in stdout.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
            if isinstance(obj, dict) and obj.get("type") == "result":
                result_obj = obj
        except json.JSONDecodeError:
            continue

    if result_obj is None:
        try:
            fallback = json.loads(stdout)
            if isinstance(fallback, dict) and fallback.get("type") == "result":
                result_obj = fallback
            else:
                return ClaudeSessionResult(
                    subtype="unparseable",
                    is_error=True,
                    result=stdout,
                    session_id="",
                    errors=[],
                )
        except json.JSONDecodeError:
            return ClaudeSessionResult(
                subtype="unparseable",
                is_error=True,
                result=stdout,
                session_id="",
                errors=[],
            )

    token_usage = extract_token_usage(stdout)

    return ClaudeSessionResult(
        subtype=result_obj.get("subtype", "unknown"),
        is_error=result_obj.get("is_error", False),
        result=result_obj.get("result", ""),
        session_id=result_obj.get("session_id", ""),
        errors=result_obj.get("errors", []),
        token_usage=token_usage,
    )


@dataclass
class CleanupResult:
    deleted: list[str] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)

    @property
    def success(self) -> bool:
        return len(self.failed) == 0

    def to_dict(self) -> dict:
        return {
            "success": self.success,
            "deleted": self.deleted,
            "failed": [{"path": p, "error": e} for p, e in self.failed],
            "skipped": self.skipped,
        }


def _delete_directory_contents(
    directory: Path,
    preserve: set[str] | None = None,
) -> CleanupResult:
    """Delete all items in directory, skipping preserved names.

    Never raises. All errors captured in CleanupResult.failed.
    FileNotFoundError treated as success (item already gone).
    """
    result = CleanupResult()
    for item_name in os.listdir(directory):
        if preserve and item_name in preserve:
            result.skipped.append(item_name)
            continue
        path = directory / item_name
        try:
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()
            result.deleted.append(item_name)
        except FileNotFoundError:
            result.deleted.append(item_name)  # gone = success
        except OSError as exc:
            result.failed.append((item_name, f"{type(exc).__name__}: {exc}"))
    return result


@mcp.tool(tags={"automation"})
async def run_cmd(cmd: str, cwd: str, timeout: int = 600) -> str:
    """Run an arbitrary shell command in the specified directory.

    Args:
        cmd: The full command to run (e.g. "make build").
        cwd: Working directory for the command.
        timeout: Max seconds before killing the process (default 600).
    """
    if (gate := _require_enabled()) is not None:
        return gate
    logger.info("run_cmd", cmd=cmd[:80], cwd=cwd)
    returncode, stdout, stderr = await _run_subprocess(
        ["bash", "-c", cmd],
        cwd=cwd,
        timeout=float(timeout),
    )
    return json.dumps(
        {
            "success": returncode == 0,
            "exit_code": returncode,
            "stdout": _truncate(stdout),
            "stderr": _truncate(stderr),
        }
    )


async def _import_and_call(
    dotted_path: str,
    args: dict[str, object] | None = None,
    timeout: float = 30,
) -> dict[str, object]:
    """Import a Python callable by dotted path and invoke it.

    Returns dict with 'success', 'result' (or 'error').
    Handles sync and async callables, with timeout protection.
    """
    if args is None:
        args = {}

    if "." not in dotted_path:
        return {"success": False, "error": f"Invalid dotted path: {dotted_path!r}"}

    module_path, attr_name = dotted_path.rsplit(".", 1)

    try:
        module = importlib.import_module(module_path)
    except ImportError as exc:
        return {"success": False, "error": f"Import failed for {module_path!r}: {exc}"}

    try:
        func = getattr(module, attr_name)
    except AttributeError:
        return {
            "success": False,
            "error": f"Module {module_path!r} has no attribute {attr_name!r}",
        }

    if not callable(func):
        return {"success": False, "error": f"{dotted_path!r} is not callable"}

    try:
        if inspect.iscoroutinefunction(func):
            result = await asyncio.wait_for(func(**args), timeout=timeout)
        else:
            result = await asyncio.wait_for(asyncio.to_thread(func, **args), timeout=timeout)
    except TimeoutError:
        return {"success": False, "error": f"Timeout after {timeout}s calling {dotted_path}"}
    except Exception as exc:
        return {"success": False, "error": f"{type(exc).__name__}: {exc}"}

    try:
        json.dumps(result)
        return {"success": True, "result": result}
    except (TypeError, ValueError):
        return {"success": True, "result": str(result)}


@mcp.tool(tags={"automation"})
async def run_python(
    callable: str, args: dict[str, object] | None = None, timeout: int = 30
) -> str:
    """Call a Python function directly by dotted module path.

    Imports the module, resolves the function, and calls it with the
    provided arguments. Use for lightweight decision logic that does
    not need an LLM session (counter checks, status lookups, eligibility
    decisions).

    Both sync and async functions are supported. Async functions are
    awaited directly; sync functions run in a thread pool.

    Args:
        callable: Dotted path to the function (e.g. "mypackage.module.function").
        args: Keyword arguments to pass to the function.
        timeout: Max seconds before aborting the call (default 30).
    """
    if (gate := _require_enabled()) is not None:
        return gate
    logger.info("run_python", callable=callable, timeout=timeout)
    result = await _import_and_call(callable, args=args, timeout=float(timeout))
    return json.dumps(result)


_FORBIDDEN_SQL = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|REPLACE|ATTACH|DETACH|PRAGMA|VACUUM|REINDEX)\b",
    re.IGNORECASE,
)
_STRIP_SQL_COMMENTS = re.compile(r"--[^\n]*|/\*.*?\*/", re.DOTALL)


def _validate_select_only(sql: str) -> None:
    """Raise ValueError if the query is not a valid SELECT statement."""
    if not sql or not sql.strip():
        raise ValueError("Query must not be empty")
    cleaned = _STRIP_SQL_COMMENTS.sub("", sql).strip()
    if _FORBIDDEN_SQL.search(cleaned):
        raise ValueError(
            f"Query contains forbidden keyword: {_FORBIDDEN_SQL.search(cleaned).group()}"  # type: ignore[union-attr]
        )
    if not re.match(r"(?i)^\s*SELECT\b", cleaned):
        raise ValueError("Query must begin with SELECT")


_ALLOWED_ACTIONS: frozenset[int] = frozenset(
    {
        sqlite3.SQLITE_SELECT,
        sqlite3.SQLITE_READ,
        sqlite3.SQLITE_FUNCTION,
    }
)


def _select_only_authorizer(
    action: int,
    arg1: str | None,
    arg2: str | None,
    db_name: str | None,
    trigger_name: str | None,
) -> int:
    """SQLite authorizer callback allowing only SELECT, READ, and FUNCTION."""
    if action in _ALLOWED_ACTIONS:
        return sqlite3.SQLITE_OK
    return sqlite3.SQLITE_DENY


def _row_to_dict(columns: list[str], row: tuple) -> dict:  # type: ignore[type-arg]
    """Convert a SQLite row tuple to a dict, base64-encoding bytes values."""
    result: dict[str, object] = {}
    for col, val in zip(columns, row):
        if isinstance(val, bytes):
            result[col] = base64.b64encode(val).decode("ascii")
        else:
            result[col] = val
    return result


def _execute_readonly_query(
    db_path: str,
    query: str,
    params: list | dict,  # type: ignore[type-arg]
    timeout_sec: int,
    max_rows: int,
) -> dict:  # type: ignore[type-arg]
    """Execute a read-only query against a SQLite database (synchronous)."""
    uri = f"file:{db_path}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    try:
        conn.set_authorizer(_select_only_authorizer)

        timer = threading.Timer(timeout_sec, conn.interrupt)
        timer.start()
        try:
            cursor = conn.cursor()
            cursor.execute(query, params)

            column_names = [desc[0] for desc in cursor.description] if cursor.description else []
            rows: list[dict] = []  # type: ignore[type-arg]
            truncated = False
            for i, row in enumerate(cursor):
                if i >= max_rows:
                    truncated = True
                    break
                rows.append(_row_to_dict(column_names, row))

            return {
                "column_names": column_names,
                "rows": rows,
                "row_count": len(rows),
                "truncated": truncated,
            }
        except sqlite3.OperationalError as exc:
            if "interrupted" in str(exc):
                raise TimeoutError from exc
            raise
        finally:
            timer.cancel()
    finally:
        conn.close()


@mcp.tool(tags={"automation"})
async def read_db(db_path: str, query: str, params: str = "[]", timeout: int = 0) -> str:
    """Run a read-only SQL query against a SQLite database, return JSON.

    Defense-in-depth: regex pre-validation rejects non-SELECT queries, the connection
    is opened with mode=ro (OS-level read-only), and a set_authorizer callback blocks
    any operation other than SELECT/READ/FUNCTION at the engine level.

    Args:
        db_path: Absolute path to the SQLite database file.
        query: SQL SELECT query. Use ? for positional or :name for named placeholders.
        params: JSON-encoded array or object of query parameter values (default "[]").
        timeout: Query timeout in seconds. 0 uses the configured default.
    """
    if (gate := _require_enabled()) is not None:
        return gate
    logger.info("read_db", db_path=db_path, query=query[:80])

    # Parse params
    try:
        parsed_params = json.loads(params)
    except json.JSONDecodeError as exc:
        return json.dumps({"error": f"Invalid params JSON: {exc}"})
    if not isinstance(parsed_params, (list, dict)):
        return json.dumps({"error": "params must be a JSON array or object"})

    # Validate db_path
    db = Path(db_path).resolve()
    if not db.exists():
        return json.dumps({"error": f"Database does not exist: {db}"})
    if not db.is_file():
        return json.dumps({"error": f"Path is not a file: {db}"})

    # SQL validation (regex pre-check)
    try:
        _validate_select_only(query)
    except ValueError as exc:
        return json.dumps({"error": str(exc), "hint": "Only SELECT queries are allowed"})

    # Resolve timeout
    effective_timeout = timeout if timeout > 0 else _config.read_db.timeout
    max_rows = _config.read_db.max_rows

    # Execute in thread (sqlite3 is blocking)
    loop = asyncio.get_running_loop()
    try:
        result = await loop.run_in_executor(
            None,
            _execute_readonly_query,
            str(db),
            query,
            parsed_params,
            effective_timeout,
            max_rows,
        )
        return json.dumps(result)
    except TimeoutError:
        return json.dumps({"error": f"Query exceeded {effective_timeout}s timeout"})
    except Exception as exc:
        return json.dumps({"error": f"Query failed: {exc}"})


def _resolve_model(step_model: str) -> str | None:
    """Resolve model selection: config override > step > config default."""
    if _config.model.override:
        return _config.model.override
    if step_model:
        return step_model
    if _config.model.default:
        return _config.model.default
    return None


async def _run_headless_core(
    skill_command: str,
    cwd: str,
    plugin_dir: str | None = None,
    model: str | None = None,
    step_name: str = "",
    add_dir: str = "",
    timeout: int | None = None,
    stale_threshold: int | None = None,
) -> dict:
    """Shared headless runner used by run_skill, run_skill_retry, and load_recipe.

    Does NOT check open_kitchen gate — callers are responsible for authorization context.
    Returns the raw result dict with at minimum a 'success' key.

    Args:
        timeout: Override the default run_skill timeout. Used by run_skill_retry
            to pass its longer timeout without a separate subprocess-building path.
        stale_threshold: Override the default stale threshold. Used by run_skill_retry.
    """
    cfg = _config.run_skill
    original_skill_command = skill_command
    skill_command = _inject_completion_directive(
        _ensure_skill_prefix(skill_command), cfg.completion_marker
    )
    effective_plugin_dir = plugin_dir if plugin_dir is not None else _plugin_dir
    cmd = [
        "claude",
        "-p",
        skill_command,
        "--plugin-dir",
        effective_plugin_dir,
        "--output-format",
        "json",
        "--dangerously-skip-permissions",
    ]
    if add_dir:
        cmd.extend(["--add-dir", add_dir])
    resolved_model = _resolve_model(model or "")
    if resolved_model:
        cmd.extend(["--model", resolved_model])

    result = await run_managed_async(
        cmd,
        cwd=Path(cwd),
        timeout=timeout if timeout is not None else cfg.timeout,
        pty_mode=True,
        heartbeat_marker=cfg.heartbeat_marker,
        session_log_dir=_session_log_dir(cwd),
        completion_marker=cfg.completion_marker,
        stale_threshold=stale_threshold if stale_threshold is not None else cfg.stale_threshold,
        completion_drain_timeout=cfg.completion_drain_timeout,
    )

    result_str = _build_skill_result(
        result, completion_marker=cfg.completion_marker, skill_command=original_skill_command
    )
    parsed = json.loads(result_str)
    if step_name:
        _token_log.record(step_name, parsed.get("token_usage"))
    return parsed


@mcp.tool(tags={"automation"})
async def run_skill(
    skill_command: str,
    cwd: str,
    add_dir: str = "",
    model: str = "",
    step_name: str = "",
    ctx: Context = CurrentContext(),
) -> str:
    """Run a Claude Code headless session with a skill command.

    Returns JSON with: success, result, session_id, subtype, is_error, exit_code,
    needs_retry, retry_reason. When needs_retry is true, retry_reason is
    "resume" — the session should be retried to continue from where it left off.

    This is the correct MCP tool to delegate work to a headless session during
    pipeline execution. NEVER use native tools (Read, Grep, Glob, Edit, Write,
    Bash, Task, Explore, WebFetch, WebSearch, NotebookEdit) from the orchestrator.
    All code changes, investigation, and research happen through the headless
    session launched by this tool.

    Args:
        skill_command: The full prompt including skill invocation (e.g. "/investigate ...").
        cwd: Working directory for the claude session.
        add_dir: Optional additional directory to add to the session context.
        model: Model to use (e.g. "sonnet", "opus"). Empty string = use config default.
        step_name: Optional YAML step key (e.g. "implement"). When set, token usage is
            accumulated in the server-side token log, grouped by this name.
    """
    if (gate := _require_enabled()) is not None:
        return gate
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(tool="run_skill", cwd=cwd)
    logger.info("run_skill", command=skill_command[:80], cwd=cwd)
    try:
        await ctx.info(
            f"run_skill: {skill_command[:80]}",
            logger_name="autoskillit.run_skill",
            extra={"cwd": cwd, "model": model or "default"},
        )
    except AttributeError:
        pass

    if _config.safety.require_dry_walkthrough:
        if (gate_error := _check_dry_walkthrough(skill_command, cwd)) is not None:
            return gate_error

    parsed = await _run_headless_core(
        skill_command, cwd, model=model, add_dir=add_dir, step_name=step_name
    )
    if not parsed.get("success"):
        try:
            await ctx.error(
                "run_skill failed",
                logger_name="autoskillit.run_skill",
                extra={"exit_code": parsed.get("exit_code"), "subtype": parsed.get("subtype")},
            )
        except AttributeError:
            pass
    return json.dumps(parsed)


@mcp.tool(tags={"automation"})
async def run_skill_retry(
    skill_command: str,
    cwd: str,
    add_dir: str = "",
    model: str = "",
    step_name: str = "",
    ctx: Context = CurrentContext(),
) -> str:
    """Run a Claude Code headless session with retry detection.

    Use this for long-running skill sessions that may hit the context limit.
    Returns JSON with: success, result, session_id, subtype, is_error, exit_code,
    needs_retry, retry_reason. The needs_retry field indicates whether the session
    didn't finish. When needs_retry is true, retry_reason is "resume" — the session
    should be retried to continue from where it left off.

    IMPORTANT: When needs_retry is true, the result field contains an actionable
    summary, not the raw CLI error. Do NOT interpret the result text as indicating
    the input was too large — it means the session's context window filled during
    execution. The correct action is always to resume the session.

    This is the correct MCP tool for long-running delegated work during pipeline
    execution. NEVER use native tools (Read, Grep, Glob, Edit, Write, Bash, Task,
    Explore, WebFetch, WebSearch, NotebookEdit) from the orchestrator. All code
    changes, investigation, and research happen through the headless session
    launched by this tool.

    Args:
        skill_command: The full prompt including skill invocation.
        cwd: Working directory for the claude session.
        add_dir: Optional additional directory to add to the session context.
        model: Model to use (e.g. "sonnet", "opus"). Empty string = use config default.
        step_name: Optional YAML step key (e.g. "implement"). When set, token usage is
            accumulated in the server-side token log, grouped by this name.
    """
    if (gate := _require_enabled()) is not None:
        return gate
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(tool="run_skill_retry", cwd=cwd)
    logger.info("run_skill_retry", command=skill_command[:80], cwd=cwd)
    try:
        await ctx.info(
            f"run_skill_retry: {skill_command[:80]}",
            logger_name="autoskillit.run_skill_retry",
            extra={"cwd": cwd, "model": model or "default"},
        )
    except AttributeError:
        pass

    if _config.safety.require_dry_walkthrough:
        if (gate_error := _check_dry_walkthrough(skill_command, cwd)) is not None:
            return gate_error

    cfg = _config.run_skill_retry
    parsed = await _run_headless_core(
        skill_command,
        cwd,
        model=model,
        add_dir=add_dir,
        step_name=step_name,
        timeout=cfg.timeout,
        stale_threshold=cfg.stale_threshold,
    )
    if not parsed.get("success"):
        try:
            await ctx.error(
                "run_skill_retry failed",
                logger_name="autoskillit.run_skill_retry",
                extra={"exit_code": parsed.get("exit_code"), "subtype": parsed.get("subtype")},
            )
        except AttributeError:
            pass
    return json.dumps(parsed)


_OUTCOME_PATTERN = re.compile(
    r"(\d+)\s+(passed|failed|error|xfailed|xpassed|skipped|warnings?|deselected)"
)


def _parse_pytest_summary(stdout: str) -> dict[str, int]:
    """Extract pytest outcome counts from the last ``=``-delimited summary line.

    Pytest's summary line is always delimited by ``=`` characters, e.g.
    ``= 5 passed, 1 warning in 2.31s =``.  Only lines that start and end
    with ``=`` are considered, preventing false matches on log output
    containing phrases like ``"3 failed connections"``.

    Returns empty dict if no summary line found.
    """
    for line in reversed(stdout.splitlines()):
        stripped = line.strip()
        if not (stripped.startswith("=") and stripped.endswith("=")):
            continue
        matches = _OUTCOME_PATTERN.findall(stripped)
        if matches:
            counts: dict[str, int] = {}
            for count_str, outcome in matches:
                key = outcome.rstrip("s") if outcome == "warnings" else outcome
                counts[key] = int(count_str)
            return counts
    return {}


def _check_test_passed(returncode: int, stdout: str) -> bool:
    """Determine test pass/fail with cross-validation.

    Uses exit code as primary signal, but overrides to False if the
    output contains failure indicators — defense against exit code bugs
    in external tools (e.g. Taskfile PIPESTATUS in non-bash shell).
    """
    if returncode != 0:
        return False
    counts = _parse_pytest_summary(stdout)
    if counts.get("failed", 0) > 0 or counts.get("error", 0) > 0:
        return False
    return True


@mcp.tool(tags={"automation"})
async def test_check(worktree_path: str) -> str:
    """Run the configured test command in a worktree directory. Returns unambiguous PASS/FAIL.

    CRITICAL: This tool is a pipeline gate, not a diagnostic tool. When it
    returns {"passed": false}, follow the pipeline script's on_failure routing
    (e.g. call assess-and-merge via run_skill). Do NOT:
    - Run tests yourself (pytest, make test, etc.) to investigate
    - Read test output or try to diagnose failures
    - Attempt to fix code directly
    The on_failure step handles all diagnosis and remediation.

    Args:
        worktree_path: Path to the git worktree to run tests in.
    """
    if (gate := _require_enabled()) is not None:
        return gate
    logger.info("test_check", worktree=worktree_path)
    returncode, stdout, stderr = await _run_subprocess(
        _config.test_check.command,
        cwd=worktree_path,
        timeout=_config.test_check.timeout,
    )

    passed = _check_test_passed(returncode, stdout)

    return json.dumps({"passed": passed})


@mcp.tool(tags={"automation"})
async def merge_worktree(worktree_path: str, base_branch: str) -> str:
    """Merge a worktree branch into the base branch after verifying tests pass.

    Programmatic gate: runs the configured test command in the worktree before allowing merge.
    If tests fail, returns error without merging.
    On failure, consider using /autoskillit:assess-and-merge via run_skill
    for automated diagnosis and remediation.

    Args:
        worktree_path: Absolute path to the git worktree.
        base_branch: Branch to merge into (e.g. "main").
    """
    if (gate := _require_enabled()) is not None:
        return gate
    logger.info("merge_worktree", path=worktree_path, base=base_branch)

    # Validate worktree path exists
    if not os.path.isdir(worktree_path):
        return json.dumps({"error": f"Path does not exist: {worktree_path}"})

    # Verify it's a git worktree
    rc, git_dir, stderr = await _run_subprocess(
        ["git", "rev-parse", "--git-dir"],
        cwd=worktree_path,
        timeout=10,
    )
    if rc != 0 or "/worktrees/" not in git_dir:
        return json.dumps({"error": f"Not a git worktree: {worktree_path}", "stderr": stderr})

    # Get branch name
    rc, branch_out, stderr = await _run_subprocess(
        ["git", "branch", "--show-current"],
        cwd=worktree_path,
        timeout=10,
    )
    if rc != 0:
        return json.dumps({"error": f"Could not determine branch: {stderr}"})
    worktree_branch = branch_out.strip()

    # Test gate
    if _config.safety.test_gate_on_merge:
        rc, test_stdout, test_stderr = await _run_subprocess(
            _config.test_check.command,
            cwd=worktree_path,
            timeout=_config.test_check.timeout,
        )
        if not _check_test_passed(rc, test_stdout):
            return json.dumps(
                {
                    "error": "Tests failed in worktree — merge blocked",
                    "failed_step": MergeFailedStep.TEST_GATE,
                    "state": MergeState.WORKTREE_INTACT,
                    "worktree_path": worktree_path,
                }
            )

    # Rebase
    fetch_rc, _, fetch_stderr = await _run_subprocess(
        ["git", "fetch", "origin"],
        cwd=worktree_path,
        timeout=60,
    )
    if fetch_rc != 0:
        return json.dumps(
            {
                "error": "git fetch origin failed",
                "failed_step": MergeFailedStep.FETCH,
                "state": MergeState.WORKTREE_INTACT,
                "stderr": _truncate(fetch_stderr),
                "worktree_path": worktree_path,
            }
        )

    rc, _, rebase_stderr = await _run_subprocess(
        ["git", "rebase", f"origin/{base_branch}"],
        cwd=worktree_path,
        timeout=120,
    )
    if rc != 0:
        await _run_subprocess(
            ["git", "rebase", "--abort"],
            cwd=worktree_path,
            timeout=30,
        )
        return json.dumps(
            {
                "error": "Rebase failed — aborted to clean state",
                "failed_step": MergeFailedStep.REBASE,
                "state": MergeState.WORKTREE_INTACT_REBASE_ABORTED,
                "stderr": rebase_stderr,
                "worktree_path": worktree_path,
            }
        )

    # Determine main repo path from worktree list
    rc, wt_list, _ = await _run_subprocess(
        ["git", "worktree", "list", "--porcelain"],
        cwd=worktree_path,
        timeout=10,
    )
    main_repo = ""
    for line in wt_list.splitlines():
        if line.startswith("worktree "):
            main_repo = line.split(" ", 1)[1].strip()
            break  # First entry is always the main working tree

    if not main_repo:
        return json.dumps({"error": "Could not determine main repo path from worktree list"})

    # Merge from main repo
    rc, _, merge_stderr = await _run_subprocess(
        ["git", "merge", worktree_branch],
        cwd=main_repo,
        timeout=60,
    )
    if rc != 0:
        await _run_subprocess(
            ["git", "merge", "--abort"],
            cwd=main_repo,
            timeout=30,
        )
        return json.dumps(
            {
                "error": "Merge failed — aborted to clean state",
                "failed_step": MergeFailedStep.MERGE,
                "state": MergeState.MAIN_REPO_MERGE_ABORTED,
                "stderr": merge_stderr,
                "worktree_path": worktree_path,
            }
        )

    # Cleanup
    wt_rc, _, wt_stderr = await _run_subprocess(
        ["git", "worktree", "remove", worktree_path],
        cwd=main_repo,
        timeout=30,
    )
    br_rc, _, br_stderr = await _run_subprocess(
        ["git", "branch", "-D", worktree_branch],
        cwd=main_repo,
        timeout=10,
    )

    return json.dumps(
        {
            "success": True,
            "merged_branch": worktree_branch,
            "into_branch": base_branch,
            "worktree_removed": wt_rc == 0,
            "branch_deleted": br_rc == 0,
        }
    )


@mcp.tool(tags={"automation"})
async def reset_test_dir(test_dir: str, force: bool = False) -> str:
    """Remove all files from a test directory. Only works on directories with a reset guard marker.

    The directory must contain the configured marker file (default: .autoskillit-workspace)
    unless force=True is set. Use ``autoskillit workspace init <dir>`` to create the marker.

    Args:
        test_dir: Path to the test directory to clear. Must contain the reset guard marker.
        force: Override the marker check. When True, all contents are deleted
               including the marker file itself.
    """
    if (gate := _require_enabled()) is not None:
        return gate
    resolved = os.path.realpath(test_dir)
    logger.info("reset_test_dir", resolved=str(resolved), force=force)

    if not os.path.isdir(resolved):
        return json.dumps({"error": f"Directory does not exist: {resolved}"})

    marker_name = _config.safety.reset_guard_marker
    marker_path = Path(resolved) / marker_name
    if not force and not marker_path.is_file():
        return json.dumps(
            {
                "error": f"Safety: directory missing reset guard marker ({marker_name})",
                "hint": f"Create the marker with: autoskillit workspace init {resolved}",
            }
        )

    preserve = None if force else {marker_name}
    cleanup = _delete_directory_contents(Path(resolved), preserve=preserve)
    return json.dumps({**cleanup.to_dict(), "forced": force})


@mcp.tool(tags={"automation"})
async def classify_fix(worktree_path: str, base_branch: str) -> str:
    """Analyze a worktree's changes to determine if the fix requires restarting
    from plan creation or just re-running the implementation.

    Inspects git diff between the worktree HEAD and the base branch merge-base.
    If any changed files are in critical paths, returns full_restart.
    Otherwise returns partial_restart.

    Routing guidance:
    - full_restart: The fix touches critical paths. Re-run investigation and
      plan creation (e.g. call /autoskillit:investigate via run_skill).
    - partial_restart: The fix is localized. Re-run implementation only
      (e.g. call /autoskillit:implement-worktree-no-merge via run_skill_retry).

    Args:
        worktree_path: Path to the git worktree with the implemented fix.
        base_branch: The branch the worktree was created from (for merge-base).
    """
    if (gate := _require_enabled()) is not None:
        return gate
    logger.info("classify_fix", worktree=worktree_path, base=base_branch)

    returncode, stdout, stderr = await _run_subprocess(
        ["git", "diff", "--name-only", f"origin/{base_branch}...HEAD"],
        cwd=worktree_path,
        timeout=30,
    )

    if returncode != 0:
        return json.dumps({"error": f"git diff failed: {stderr}"})

    changed_files = [f.strip() for f in stdout.splitlines() if f.strip()]

    prefixes = _config.classify_fix.path_prefixes
    critical_files = [f for f in changed_files if any(f.startswith(prefix) for prefix in prefixes)]

    if critical_files:
        return json.dumps(
            {
                "restart_scope": RestartScope.FULL_RESTART,
                "reason": f"Fix touches critical paths: {', '.join(critical_files[:5])}",
                "critical_files": critical_files,
                "all_changed_files": changed_files,
            }
        )

    return json.dumps(
        {
            "restart_scope": RestartScope.PARTIAL_RESTART,
            "reason": "Fix does not touch critical paths — partial restart is sufficient",
            "critical_files": [],
            "all_changed_files": changed_files,
        }
    )


@mcp.tool(tags={"automation"})
async def reset_workspace(test_dir: str) -> str:
    """Runs a configured reset command then deletes directory contents,
    preserving configured directories and the reset guard marker.

    Args:
        test_dir: Path to the test project directory. Must contain the reset guard marker.
    """
    if (gate := _require_enabled()) is not None:
        return gate
    resolved = os.path.realpath(test_dir)
    logger.info("reset_workspace", resolved=str(resolved))

    if not os.path.isdir(resolved):
        return json.dumps({"error": f"Directory does not exist: {resolved}"})

    marker_name = _config.safety.reset_guard_marker
    marker_path = Path(resolved) / marker_name
    if not marker_path.is_file():
        return json.dumps(
            {
                "error": f"Safety: directory missing reset guard marker ({marker_name})",
                "hint": f"Create the marker with: autoskillit workspace init {resolved}",
            }
        )

    if _config.reset_workspace.command is None:
        return json.dumps({"error": "reset_workspace not configured for this project"})

    returncode, stdout, stderr = await _run_subprocess(
        _config.reset_workspace.command,
        cwd=resolved,
        timeout=60,
    )

    if returncode != 0:
        return json.dumps(
            {
                "error": "reset command failed",
                "exit_code": returncode,
                "stderr": _truncate(stderr),
            }
        )

    preserve = set(_config.reset_workspace.preserve_dirs) | {marker_name}
    cleanup = _delete_directory_contents(Path(resolved), preserve=preserve)
    return json.dumps(cleanup.to_dict())


@mcp.tool(tags={"automation"})
async def kitchen_status() -> str:
    """Return version health and configuration status for the running server.

    Reports package version, plugin.json version, version match status,
    tools enabled state, and active configuration summary. Call this after
    enabling tools or anytime you need to verify the server is healthy.

    This tool is always available (not gated by open_kitchen).
    """
    info = _version_info()
    status = {
        "package_version": info["package_version"],
        "plugin_json_version": info["plugin_json_version"],
        "versions_match": info["match"],
        "tools_enabled": _tools_enabled,
    }
    if not info["match"]:
        status["warning"] = (
            f"Version mismatch: package is {info['package_version']} but "
            f"plugin.json reports {info['plugin_json_version']}. "
            f"Run `autoskillit doctor` for details or "
            f"`autoskillit install` to refresh the plugin cache."
        )
    status["token_usage_verbosity"] = _config.token_usage.verbosity
    return json.dumps(status)


@mcp.tool(tags={"automation"})
async def get_pipeline_report(clear: bool = False) -> str:
    """Return accumulated run_skill / run_skill_retry failures since last clear.

    Orchestrators should call this at the end of a pipeline run to retrieve
    a structured summary of every non-success result. Pass clear=True to
    atomically retrieve and reset the store for the next pipeline run.

    Returns JSON with:
      - total_failures: int
      - failures: list of {timestamp, skill_command, exit_code, subtype,
                            needs_retry, retry_reason, stderr}

    This tool is always available (not gated by open_kitchen).
    """
    report = _audit_log.get_report()
    if clear:
        _audit_log.clear()
    return json.dumps(
        {
            "total_failures": len(report),
            "failures": [r.to_dict() for r in report],
        }
    )


@mcp.tool(tags={"automation"})
async def get_token_summary(clear: bool = False) -> str:
    """Return accumulated run_skill/run_skill_retry token usage grouped by step name.

    This tool is always available (not gated by open_kitchen).

    Returns JSON with:
    - steps: list of {step_name, input_tokens, output_tokens,
                       cache_creation_input_tokens, cache_read_input_tokens,
                       invocation_count}
    - total: {input_tokens, output_tokens, cache_creation_input_tokens,
               cache_read_input_tokens}

    Args:
        clear: If True, reset the token log after returning current data.
    """
    steps = _token_log.get_report()
    if clear:
        _token_log.clear()
    total: dict[str, int] = {
        "input_tokens": sum(s["input_tokens"] for s in steps),
        "output_tokens": sum(s["output_tokens"] for s in steps),
        "cache_creation_input_tokens": sum(s["cache_creation_input_tokens"] for s in steps),
        "cache_read_input_tokens": sum(s["cache_read_input_tokens"] for s in steps),
    }
    return json.dumps({"steps": steps, "total": total})


@mcp.tool(tags={"automation"})
async def list_recipes() -> str:
    """List available recipes from .autoskillit/recipes/.

    Returns a JSON array of recipes with name, description, and summary.
    Recipes are YAML workflow definitions that agents follow as orchestration
    instructions. Use load_recipe to load a specific recipe.
    To create a new recipe, use the /autoskillit:write-recipe skill.
    To generate recipes as part of project onboarding, use /autoskillit:setup-project.

    IMPORTANT: Recipes are NOT slash commands. They cannot be invoked
    as /autoskillit:<name>. They are loaded via load_recipe and executed
    step-by-step by the agent. Recipes live in .autoskillit/recipes/ (NOT in
    .autoskillit/skills/ or any other directory).

    This tool is always available (not gated by open_kitchen).
    """
    from autoskillit.recipe_parser import list_recipes as _list_recipes

    result = _list_recipes(Path.cwd())
    response: dict[str, object] = {
        "recipes": [
            {"name": r.name, "description": r.description, "summary": r.summary}
            for r in result.items
        ],
    }
    if result.errors:
        response["errors"] = [{"file": e.path.name, "error": e.error} for e in result.errors]
    return json.dumps(response)


@mcp.tool(tags={"automation"})
async def load_recipe(name: str) -> str:
    """Load a recipe by name and return its raw YAML content.

    The YAML follows the recipe schema (ingredients, steps with tool/action,
    on_success/on_failure routing, retry blocks). The agent should interpret
    the YAML and execute the steps using the appropriate MCP tools.

    After loading:
    1. Present the recipe to the user using the preview format below
    2. If the user requests changes, use the /autoskillit:write-recipe skill
       to apply modifications. That skill has the complete schema, validation rules,
       and formatting constraints needed for correct changes. Do NOT edit the YAML
       file directly — always delegate modifications to write-recipe.
    3. Prompt for input values using AskUserQuestion
    4. Execute the pipeline steps by calling MCP tools directly

    Preview format for step 1:

        ## {name}
        {description}

        **Flow:** {summary}

        ### Graph
        Render a route table showing the full execution flow. Use this exact
        column layout (align columns with spaces):

          Step               Tool                  ✓ success           ✗ failure
          ───────────────────────────────────────────────────────────────────────
          {step}             {tool/action/python}  → {on_success}      → {on_failure}

        Rules:
        - List steps in YAML declaration order.
        - For the Tool column: use the tool/action/python value. Append
          [model] if a model is set, e.g. "run_skill [sonnet]".
        - If on_success routes back to an earlier step, append ↑ to the name.
        - If on_failure routes back to an earlier step, append ↑ to the name.
        - If a step has retry: add an indented continuation line below it:
              ↺ ×{max_attempts} ({on} condition)  → {on_exhausted}
        - If a step uses on_result instead of on_success: leave the ✓ success
          cell empty and add indented continuation lines for each route:
              {route_key}  → {route_target}
          Append ↑ to any target that is an earlier step.
        - Terminal steps (action: stop) are excluded from the table and
          listed below the closing rule, one per line:
              {name}  "{message}"
        - Close the table with the same ─── rule used to open it.

        ### Ingredients
        For each ingredient show: name, description, required/optional, default value.
        Distinguish user-supplied ingredients (required=true or meaningful defaults)
        from agent-managed state (default="" or default=null with description
        indicating it is set by a prior step or the agent).

        ### Steps
        For each non-terminal step show:
        - Step name and tool/action/python discriminator
        - If optional: true, mark as "[Optional]" and show the note
        - If retry block exists: retries Nx on {condition}, then → {on_exhausted}
        - If note exists, show it (notes contain critical agent instructions)
        - If capture exists, show what values are extracted
        - If model: show the model value (e.g., "Model: sonnet")

        ### Kitchen Rules
        If present, list all kitchen_rules strings.
        If absent, note: "No kitchen rules defined"

    NEVER use native Claude Code tools from the orchestrator during pipeline
    execution. The following are prohibited: Read, Grep, Glob, Edit, Write,
    Bash, Task, Explore, WebFetch, WebSearch, NotebookEdit.
    - Code investigation happens inside headless sessions launched by
      run_skill/run_skill_retry, which have full tool access.
    - Code modification is delegated through run_skill/run_skill_retry.
    - Shell commands use run_cmd, not the native Bash tool.
    - Research and multi-step work are delegated via run_skill.

    Allowed during pipeline execution:
    - AutoSkillit MCP tools (call directly, not via subagents)
    - AskUserQuestion (user interaction)
    - Steps with `capture:` fields extract values from tool results into a
      pipeline context dict. Use captured values in subsequent steps via
      ${{ context.var_name }} in `with:` arguments.
    - Thread outputs from each step into the next (e.g. worktree_path from
      implement into test_check).
    - Steps with a `model:` field: when calling `run_skill` or `run_skill_retry`,
      pass the step's `model` value as the `model` parameter to the tool.

    TOKEN USAGE TRACKING:
    - BEFORE executing the pipeline, call kitchen_status() and read
      token_usage_verbosity. This controls how you handle token reporting:
        "summary" → call get_token_summary(clear=True) ONCE after the
                     pipeline completes and render the table below.
        "none"    → do NOT call get_token_summary. Skip token reporting entirely.
    - Do NOT print or render a token usage table after individual steps.
      Only one call to get_token_summary is permitted per pipeline run,
      at the very end. Intermediate rendering is prohibited.
    - Pass step_name (the YAML step key, e.g. "implement") in the with: block
      when calling run_skill or run_skill_retry. The server accumulates token
      usage server-side, grouped by step name.
    - When verbosity is "summary", call get_token_summary(clear=True) at pipeline
      completion and render as:

      ## Token Usage Summary
      | Step | input | output | cache_create | cache_read |
      |------|-------|--------|--------------|------------|
      | investigate | 7 | 5939 | 8495 | 252179 |
      | implement | 2031 | 122306 | 280601 | 19,071,323 |
      | **Total** | ... | ... | ... | ... |

    - Non-skill steps (test_check, run_cmd, merge_worktree) have no token usage —
      they are not included in get_token_summary output. Do not add rows for them.

    ROUTING RULES — MANDATORY:
    - When a tool returns a failure result, you MUST follow the step's on_failure route.
    - When a step fails, route to on_failure — do not use Read, Grep, Glob, Edit,
      Write, Bash, Task, Explore, WebFetch, WebSearch, NotebookEdit or any native
      tool to investigate. The on_failure step (e.g., assess-and-merge) has
      diagnostic access that the orchestrator does not.
    - Your ONLY job is to route to the correct next step and pass the
      required arguments. The downstream skill does the actual work.

    FAILURE PREDICATES — when to follow on_failure:
    - test_check: {"passed": false}
    - merge_worktree: "error" key present in response
    - run_cmd: {"success": false}
    - run_skill / run_skill_retry: {"success": false}
    - classify_fix: "error" key present in response

    To CREATE a new recipe, use the /autoskillit:write-recipe skill.
    This tool is for loading and executing existing recipes.

    IMPORTANT: Recipes are NOT slash commands. They cannot be invoked
    as /autoskillit:<name>. The correct way to run a recipe is to call this
    tool, then follow the YAML steps. Recipes live in .autoskillit/recipes/
    as .yaml files (NOT in .autoskillit/skills/ or any other directory).

    This tool is always available (not gated by open_kitchen).

    Response format: always JSON with ``content`` (raw YAML string) and
    ``suggestions`` (list of semantic findings, possibly empty) keys.
    On error: JSON with ``error`` key.
    """
    import yaml

    from autoskillit.contract_validator import (
        check_contract_staleness,
        generate_recipe_card,
        load_recipe_card,
        validate_recipe_cards,
    )
    from autoskillit.recipe_parser import _parse_recipe
    from autoskillit.recipe_parser import list_recipes as _list_recipes_all
    from autoskillit.semantic_rules import run_semantic_rules

    _all = _list_recipes_all(Path.cwd())
    _match = next((r for r in _all.items if r.name == name), None)
    if _match is None:
        return json.dumps({"error": f"No recipe named '{name}' found"})
    content = _match.path.read_text()

    suggestions: list[dict[str, str]] = []
    try:
        data = yaml.safe_load(content)
        if isinstance(data, dict) and "steps" in data:
            recipe = _parse_recipe(data)

            # --- Auto-migration block ---
            from autoskillit import __version__
            from autoskillit.failure_store import FailureStore, default_store_path
            from autoskillit.migration_engine import MigrationFile, default_migration_engine
            from autoskillit.migration_loader import applicable_migrations

            migrations = applicable_migrations(recipe.version, __version__)
            if migrations and name not in _config.migration.suppressed:
                project_dir = Path.cwd()
                temp_dir = project_dir / ".autoskillit" / "temp"
                recipes_dir = project_dir / ".autoskillit" / "recipes"
                recipe_path = recipes_dir / f"{name}.yaml"
                failure_store = FailureStore(default_store_path(project_dir))

                engine = default_migration_engine()
                mfile = MigrationFile(
                    name=name,
                    path=recipe_path,
                    file_type="recipe",
                    current_version=recipe.version,
                )
                migration_result = await engine.migrate_file(
                    mfile,
                    run_headless=_run_headless_core,
                    temp_dir=temp_dir,
                )

                if migration_result.success:
                    content = recipe_path.read_text()
                    data = yaml.safe_load(content)
                    recipe = _parse_recipe(data)
                    failure_store.clear(name)
                else:
                    failure_store.record(
                        name=name,
                        file_path=recipe_path,
                        file_type="recipe",
                        error=migration_result.error or "unknown",
                        retries_attempted=migration_result.retries_attempted,
                    )
                    suggestions.append(
                        {
                            "rule": "migration-failed",
                            "severity": "error",
                            "step": "(auto-migration)",
                            "message": (
                                f"Auto-migration failed: {migration_result.error}. "
                                "Check .autoskillit/temp/migrations/failures.json. "
                                "Manual intervention required."
                            ),
                        }
                    )
            # --- End auto-migration block ---

            findings = run_semantic_rules(recipe)
            semantic_suggestions = [f.to_dict() for f in findings]

            if name in _config.migration.suppressed:
                semantic_suggestions = [
                    s for s in semantic_suggestions if s.get("rule") != "outdated-recipe-version"
                ]
            suggestions.extend(semantic_suggestions)

            # Contract validation
            recipes_dir = Path.cwd() / ".autoskillit" / "recipes"
            contract = load_recipe_card(name, recipes_dir)
            if contract is None:
                # Auto-generate for first load
                recipe_path = recipes_dir / f"{name}.yaml"
                if not recipe_path.exists():
                    recipe_path = recipes_dir / f"{name}.yml"
                if recipe_path.exists():
                    try:
                        generate_recipe_card(recipe_path, recipes_dir)
                        contract = load_recipe_card(name, recipes_dir)
                    except Exception:
                        pass  # Non-blocking

            if contract:
                contract_findings = validate_recipe_cards(recipe, contract)
                suggestions.extend(contract_findings)

                # Staleness check
                stale = check_contract_staleness(contract)
                for item in stale:
                    suggestions.append(
                        {
                            "rule": "stale-contract",
                            "severity": "warning",
                            "step": item.skill,
                            "message": (
                                f"Contract is stale: {item.reason} for "
                                f"'{item.skill}' (stored={item.stored_value}, "
                                f"current={item.current_value}). Consider "
                                f"regenerating the contract."
                            ),
                        }
                    )
    except Exception:
        pass  # Non-blocking: parse failures don't affect load

    return json.dumps({"content": content, "suggestions": suggestions})


@mcp.tool(tags={"automation"})
async def validate_recipe(script_path: str) -> str:
    """Validate a recipe YAML file against the recipe schema.

    Parses the file, checks all validation rules (name, steps, routing,
    retry fields, ingredient references), and returns structured results.
    Use after generating or modifying a recipe (via write-recipe)
    to confirm it is valid. The /autoskillit:write-recipe skill
    calls this tool automatically after generating a recipe.

    When validation fails ({"valid": false}), do NOT edit the YAML file
    directly to fix errors. Use the /autoskillit:write-recipe skill
    to apply corrections — it has the complete schema, validation rules,
    and formatting constraints needed for correct modifications.

    IMPORTANT: Recipes are NOT slash commands. They cannot be invoked
    as /autoskillit:<name>. They are loaded via load_recipe and executed
    step-by-step by the agent. Recipes live in .autoskillit/recipes/
    as .yaml files.

    This tool is always available (not gated by open_kitchen).

    Args:
        script_path: Absolute path to the .yaml recipe file to validate.
    """
    import yaml

    from autoskillit.contract_validator import (
        load_recipe_card,
        validate_recipe_cards,
    )
    from autoskillit.recipe_parser import (
        _parse_recipe,
        analyze_dataflow,
    )
    from autoskillit.recipe_parser import (
        validate_recipe as _validate_recipe,
    )
    from autoskillit.semantic_rules import Severity, run_semantic_rules

    path = Path(script_path)
    if not path.is_file():
        return json.dumps({"error": f"File not found: {script_path}"})

    try:
        data = yaml.safe_load(path.read_text())
    except yaml.YAMLError as exc:
        return json.dumps({"error": f"YAML parse error: {exc}"})

    if not isinstance(data, dict):
        return json.dumps({"error": "File must contain a YAML mapping"})

    recipe = _parse_recipe(data)
    errors = _validate_recipe(recipe)
    report = analyze_dataflow(recipe)
    semantic_findings = run_semantic_rules(recipe)

    quality = {
        "warnings": [
            {
                "code": w.code,
                "step": w.step_name,
                "field": w.field,
                "message": w.message,
            }
            for w in report.warnings
        ],
        "summary": report.summary,
    }
    semantic = [f.to_dict() for f in semantic_findings]

    # Contract validation
    contract_findings: list[dict] = []
    recipes_dir = path.parent
    recipe_name = path.stem
    contract = load_recipe_card(recipe_name, recipes_dir)
    if contract:
        contract_findings = validate_recipe_cards(recipe, contract)

    has_schema_errors = bool(errors)
    has_semantic_errors = any(f.severity == Severity.ERROR for f in semantic_findings)
    has_contract_errors = any(f.get("severity") == "error" for f in contract_findings)
    valid = not has_schema_errors and not has_semantic_errors and not has_contract_errors

    return json.dumps(
        {
            "valid": valid,
            "errors": errors,
            "quality": quality,
            "semantic": semantic,
            "contracts": contract_findings,
        }
    )


def _open_kitchen_handler() -> None:
    """Set the tools-enabled flag. Extracted for testability."""
    global _tools_enabled
    _tools_enabled = True


def _close_kitchen_handler() -> None:
    """Clear the tools-enabled flag. Extracted for testability."""
    global _tools_enabled
    _tools_enabled = False


@mcp.resource("recipe://{name}")
def get_recipe(name: str) -> str:
    """Return recipe YAML for the orchestrating agent to follow."""
    from autoskillit.recipe_parser import list_recipes

    result = list_recipes(Path.cwd())
    match = next((r for r in result.items if r.name == name), None)
    if match is None:
        return json.dumps({"error": f"No recipe named '{name}'."})
    return match.path.read_text()


@mcp.prompt()
def open_kitchen() -> PromptResult:
    """Open the AutoSkillit kitchen for service."""
    _open_kitchen_handler()

    _forbidden_list = ", ".join(PIPELINE_FORBIDDEN_TOOLS)

    text = (
        "Kitchen is open. AutoSkillit tools are ready for service. "
        "Call the kitchen_status tool now to display version "
        "and health information to the user.\n\n"
        "IMPORTANT — Orchestrator Discipline:\n"
        f"NEVER use native Claude Code tools ({_forbidden_list}) "
        "in this session. All code reading, searching, editing, and "
        "investigation MUST be delegated through run_skill or "
        "run_skill_retry, which launch headless sessions with full "
        "tool access. Do NOT use native tools to investigate failures — "
        "route to on_failure and let the downstream skill handle diagnosis."
    )

    # Check if the project needs an upgrade
    scripts_dir = Path.cwd() / ".autoskillit" / "scripts"
    recipes_dir = Path.cwd() / ".autoskillit" / "recipes"
    if scripts_dir.exists() and not recipes_dir.exists():
        text += (
            "\n\n⚠️ UPGRADE NEEDED: This project has not been migrated to the new recipe format.\n"
            "`.autoskillit/scripts/` still exists. Run `autoskillit upgrade` in this directory\n"
            "to migrate automatically, or ask me to do it for you."
        )

    return PromptResult([Message(text, role="user")])


@mcp.prompt()
def close_kitchen() -> PromptResult:
    """Close the AutoSkillit kitchen."""
    _close_kitchen_handler()
    return PromptResult([Message("Kitchen is closed.", role="assistant")])
