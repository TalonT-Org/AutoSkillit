#!/usr/bin/env python3
"""MCP server for orchestrating automated skill-driven workflows.

All tools are gated by default and require the user to type the
enable_tools prompt to activate. The prompt name depends on how the
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
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path

from fastmcp import FastMCP
from fastmcp.prompts.prompt import Message, PromptResult

from autoskillit.config import AutomationConfig, load_config
from autoskillit.process_lifecycle import (
    SubprocessResult,
    TerminationReason,
    _extract_text_content,
    run_managed_async,
)
from autoskillit.types import (
    CONTEXT_EXHAUSTION_MARKER,
    MergeFailedStep,
    MergeState,
    RestartScope,
    RetryReason,
)

mcp = FastMCP("autoskillit")

_config: AutomationConfig = load_config(Path.cwd())

_plugin_dir = str(Path(__file__).parent)

_tools_enabled = False

PIPELINE_FORBIDDEN_TOOLS: tuple[str, ...] = (
    "Read",
    "Grep",
    "Glob",
    "Edit",
    "Write",
    "Bash",
    "Task",
    "Explore",
    "WebFetch",
    "WebSearch",
    "NotebookEdit",
)


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
        }
    )


def _require_enabled() -> str | None:
    """Return error JSON if tools are not enabled, None if OK.

    All tools are gated by default and can only be activated by the user
    typing the enable_tools prompt. The prompt name is prefixed by Claude
    Code based on how the server was loaded (plugin vs --plugin-dir).
    This survives --dangerously-skip-permissions because MCP prompts are
    outside the permission system.
    """
    if not _tools_enabled:
        return _gate_error_result(
            "AutoSkillit tools are not enabled. "
            "User must type the enable_tools prompt to activate. "
            "Check the MCP prompt list for the exact name."
        )
    return None


def _log(msg: str) -> None:
    print(msg, file=sys.stderr)


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
        # Monitor-completion path: when the session monitor or heartbeat
        # detects completion and kills the process, returncode is a negative
        # signal code (e.g. -15 for SIGTERM) or 0 through PTY masking.
        # Trust the session envelope when termination is COMPLETED.
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

    return False, RetryReason.NONE


def _build_skill_result(result: SubprocessResult, completion_marker: str = "") -> str:
    """Route SubprocessResult fields into the standard run_skill JSON response."""
    if result.termination == TerminationReason.STALE:
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

    return ClaudeSessionResult(
        subtype=result_obj.get("subtype", "unknown"),
        is_error=result_obj.get("is_error", False),
        result=result_obj.get("result", ""),
        session_id=result_obj.get("session_id", ""),
        errors=result_obj.get("errors", []),
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
    _log(f"run_cmd: cmd={cmd[:80]}... cwd={cwd}")
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
    _log(f"run_python: callable={callable} timeout={timeout}")
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
    _log(f"read_db: db_path={db_path} query={query[:80]}")

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


@mcp.tool(tags={"automation"})
async def run_skill(skill_command: str, cwd: str, add_dir: str = "", model: str = "") -> str:
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
    """
    if (gate := _require_enabled()) is not None:
        return gate
    _log(f"run_skill: command={skill_command[:80]}... cwd={cwd}")

    if _config.safety.require_dry_walkthrough:
        if (gate_error := _check_dry_walkthrough(skill_command, cwd)) is not None:
            return gate_error

    cfg = _config.run_skill
    skill_command = _inject_completion_directive(
        _ensure_skill_prefix(skill_command), cfg.completion_marker
    )

    cmd = [
        "claude",
        "-p",
        skill_command,
        "--plugin-dir",
        _plugin_dir,
        "--output-format",
        "json",
        "--dangerously-skip-permissions",
    ]
    if add_dir:
        cmd.extend(["--add-dir", add_dir])
    resolved_model = _resolve_model(model)
    if resolved_model:
        cmd.extend(["--model", resolved_model])

    result = await run_managed_async(
        cmd,
        cwd=Path(cwd),
        timeout=cfg.timeout,
        pty_mode=True,
        heartbeat_marker=cfg.heartbeat_marker,
        session_log_dir=_session_log_dir(cwd),
        completion_marker=cfg.completion_marker,
        stale_threshold=cfg.stale_threshold,
    )

    return _build_skill_result(result, completion_marker=cfg.completion_marker)


@mcp.tool(tags={"automation"})
async def run_skill_retry(skill_command: str, cwd: str, model: str = "") -> str:
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
        model: Model to use (e.g. "sonnet", "opus"). Empty string = use config default.
    """
    if (gate := _require_enabled()) is not None:
        return gate
    _log(f"run_skill_retry: command={skill_command[:80]}...")

    if _config.safety.require_dry_walkthrough:
        if (gate_error := _check_dry_walkthrough(skill_command, cwd)) is not None:
            return gate_error

    cfg = _config.run_skill_retry
    skill_command = _inject_completion_directive(
        _ensure_skill_prefix(skill_command), cfg.completion_marker
    )

    cmd = [
        "claude",
        "-p",
        skill_command,
        "--plugin-dir",
        _plugin_dir,
        "--output-format",
        "json",
        "--dangerously-skip-permissions",
    ]
    resolved_model = _resolve_model(model)
    if resolved_model:
        cmd.extend(["--model", resolved_model])

    result = await run_managed_async(
        cmd,
        cwd=Path(cwd),
        timeout=cfg.timeout,
        pty_mode=True,
        heartbeat_marker=cfg.heartbeat_marker,
        session_log_dir=_session_log_dir(cwd),
        completion_marker=cfg.completion_marker,
        stale_threshold=cfg.stale_threshold,
    )

    return _build_skill_result(result, completion_marker=cfg.completion_marker)


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
    _log(f"test_check: worktree={worktree_path}")
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
    _log(f"merge_worktree: path={worktree_path} base={base_branch}")

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
    _log(f"reset_test_dir: resolved={resolved} force={force}")

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
    _log(f"classify_fix: worktree={worktree_path} base={base_branch}")

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
    _log(f"reset_workspace: resolved={resolved}")

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
async def autoskillit_status() -> str:
    """Return version health and configuration status for the running server.

    Reports package version, plugin.json version, version match status,
    tools enabled state, and active configuration summary. Call this after
    enabling tools or anytime you need to verify the server is healthy.

    This tool is always available (not gated by enable_tools).
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
    return json.dumps(status)


@mcp.tool(tags={"automation"})
async def list_skill_scripts() -> str:
    """List available pipeline scripts from .autoskillit/scripts/.

    Returns a JSON array of scripts with name, description, and summary.
    Scripts are YAML workflow definitions that agents follow as orchestration
    instructions. Use load_skill_script to load a specific script.
    To create a new script, use the /autoskillit:make-script-skill skill.
    To generate scripts as part of project onboarding, use /autoskillit:setup-project.

    IMPORTANT: Pipeline scripts are NOT slash commands. They cannot be invoked
    as /autoskillit:<name>. They are loaded via load_skill_script and executed
    step-by-step by the agent. Scripts live in .autoskillit/scripts/ (NOT in
    .autoskillit/skills/ or any other directory).

    This tool is always available (not gated by enable_tools).
    """
    from autoskillit.script_loader import list_scripts

    result = list_scripts(Path.cwd())
    response: dict[str, object] = {
        "scripts": [
            {"name": s.name, "description": s.description, "summary": s.summary}
            for s in result.items
        ],
    }
    if result.errors:
        response["errors"] = [{"file": e.path.name, "error": e.error} for e in result.errors]
    return json.dumps(response)


@mcp.tool(tags={"automation"})
async def load_skill_script(name: str) -> str:
    """Load a pipeline script by name and return its raw YAML content.

    The YAML follows the workflow schema (inputs, steps with tool/action,
    on_success/on_failure routing, retry blocks). The agent should interpret
    the YAML and execute the steps using the appropriate MCP tools.

    After loading:
    1. Present the script to the user using the preview format below
    2. If the user requests changes, use the /autoskillit:make-script-skill skill
       to apply modifications. That skill has the complete schema, validation rules,
       and formatting constraints needed for correct changes. Do NOT edit the YAML
       file directly — always delegate modifications to make-script-skill.
    3. Prompt for input values using AskUserQuestion
    4. Execute the pipeline steps by calling MCP tools directly

    Preview format for step 1:

        ## {name}
        {description}

        **Flow:** {summary}

        ### Inputs
        For each input show: name, description, required/optional, default value.
        Distinguish user-supplied inputs (required=true or meaningful defaults)
        from agent-managed state (default="" or default=null with description
        indicating it is set by a prior step or the agent).

        ### Steps
        For each step show:
        - Step name and tool/action/python discriminator
        - Routing: on_success → X, on_failure → Y
        - If on_result: show field name and each route
        - If optional: true, mark as "[Optional]" and show the note explaining
          the skip condition
        - If retry block exists: retries Nx on {condition}, then → {on_exhausted}
        - If note exists, show it (notes contain critical agent instructions)
        - If capture exists, show what values are extracted
        - If model: show the model value (e.g., "Model: sonnet")

        ### Constraints
        If present, list all constraint strings.
        If absent, note: "No constraints defined"

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

    To CREATE a new script, use the /autoskillit:make-script-skill skill.
    This tool is for loading and executing existing scripts.

    IMPORTANT: Pipeline scripts are NOT slash commands. They cannot be invoked
    as /autoskillit:<name>. The correct way to run a script is to call this
    tool, then follow the YAML steps. Scripts live in .autoskillit/scripts/
    as .yaml files (NOT in .autoskillit/skills/ or any other directory).

    This tool is always available (not gated by enable_tools).

    Response format: always JSON with ``content`` (raw YAML string) and
    ``suggestions`` (list of semantic findings, possibly empty) keys.
    On error: JSON with ``error`` key.
    """
    import yaml

    from autoskillit.contract_validator import (
        check_contract_staleness,
        generate_pipeline_contract,
        load_pipeline_contract,
        validate_pipeline_contracts,
    )
    from autoskillit.script_loader import load_script
    from autoskillit.semantic_rules import run_semantic_rules
    from autoskillit.workflow_loader import _parse_workflow

    content = load_script(Path.cwd(), name)
    if content is None:
        return json.dumps({"error": f"No script named '{name}' in .autoskillit/scripts/"})

    suggestions: list[dict[str, str]] = []
    try:
        data = yaml.safe_load(content)
        if isinstance(data, dict) and "steps" in data:
            wf = _parse_workflow(data)
            findings = run_semantic_rules(wf)
            suggestions = [f.to_dict() for f in findings]

            # Contract validation
            scripts_dir = Path.cwd() / ".autoskillit" / "scripts"
            contract = load_pipeline_contract(name, scripts_dir)
            if contract is None:
                # Auto-generate for first load
                script_path = scripts_dir / f"{name}.yaml"
                if not script_path.exists():
                    script_path = scripts_dir / f"{name}.yml"
                if script_path.exists():
                    try:
                        generate_pipeline_contract(script_path, scripts_dir)
                        contract = load_pipeline_contract(name, scripts_dir)
                    except Exception:
                        pass  # Non-blocking

            if contract:
                contract_findings = validate_pipeline_contracts(wf, contract)
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
async def validate_script(script_path: str) -> str:
    """Validate a pipeline script YAML file against the workflow schema.

    Parses the file, checks all validation rules (name, steps, routing,
    retry fields, input references), and returns structured results.
    Use after generating or modifying a script (via make-script-skill)
    to confirm it is valid. The /autoskillit:make-script-skill skill
    calls this tool automatically after generating a script.

    When validation fails ({"valid": false}), do NOT edit the YAML file
    directly to fix errors. Use the /autoskillit:make-script-skill skill
    to apply corrections — it has the complete schema, validation rules,
    and formatting constraints needed for correct modifications.

    IMPORTANT: Pipeline scripts are NOT slash commands. They cannot be invoked
    as /autoskillit:<name>. They are loaded via load_skill_script and executed
    step-by-step by the agent. Scripts live in .autoskillit/scripts/
    as .yaml files.

    This tool is always available (not gated by enable_tools).

    Args:
        script_path: Absolute path to the .yaml script file to validate.
    """
    import yaml

    from autoskillit.contract_validator import (
        load_pipeline_contract,
        validate_pipeline_contracts,
    )
    from autoskillit.semantic_rules import Severity, run_semantic_rules
    from autoskillit.workflow_loader import (
        _parse_workflow,
        analyze_dataflow,
        validate_workflow,
    )

    path = Path(script_path)
    if not path.is_file():
        return json.dumps({"error": f"File not found: {script_path}"})

    try:
        data = yaml.safe_load(path.read_text())
    except yaml.YAMLError as exc:
        return json.dumps({"error": f"YAML parse error: {exc}"})

    if not isinstance(data, dict):
        return json.dumps({"error": "File must contain a YAML mapping"})

    wf = _parse_workflow(data)
    errors = validate_workflow(wf)
    report = analyze_dataflow(wf)
    semantic_findings = run_semantic_rules(wf)

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
    scripts_dir = path.parent
    script_name = path.stem
    contract = load_pipeline_contract(script_name, scripts_dir)
    if contract:
        contract_findings = validate_pipeline_contracts(wf, contract)

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


def _enable_tools_handler() -> None:
    """Set the tools-enabled flag. Extracted for testability."""
    global _tools_enabled
    _tools_enabled = True


def _disable_tools_handler() -> None:
    """Clear the tools-enabled flag. Extracted for testability."""
    global _tools_enabled
    _tools_enabled = False


@mcp.resource("workflow://{name}")
def get_workflow(name: str) -> str:
    """Return workflow YAML for the orchestrating agent to follow."""
    from autoskillit.workflow_loader import list_workflows

    result = list_workflows(Path.cwd())
    match = next((w for w in result.items if w.name == name), None)
    if match is None:
        return json.dumps({"error": f"No workflow named '{name}'."})
    return match.path.read_text()


@mcp.prompt()
def enable_tools() -> PromptResult:
    """Enable AutoSkillit MCP tools for this session."""
    _enable_tools_handler()

    _forbidden_list = ", ".join(PIPELINE_FORBIDDEN_TOOLS)

    text = (
        "AutoSkillit tools are now enabled for this session. "
        "Call the autoskillit_status tool now to display version "
        "and health information to the user.\n\n"
        "IMPORTANT — Orchestrator Discipline:\n"
        f"NEVER use native Claude Code tools ({_forbidden_list}) "
        "in this session. All code reading, searching, editing, and "
        "investigation MUST be delegated through run_skill or "
        "run_skill_retry, which launch headless sessions with full "
        "tool access. Do NOT use native tools to investigate failures — "
        "route to on_failure and let the downstream skill handle diagnosis."
    )

    return PromptResult([Message(text, role="user")])


@mcp.prompt()
def disable_tools() -> PromptResult:
    """Disable AutoSkillit MCP tools for this session."""
    _disable_tools_handler()
    return PromptResult([Message("AutoSkillit tools are now disabled.", role="assistant")])
