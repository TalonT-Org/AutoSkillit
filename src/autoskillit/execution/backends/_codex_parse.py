"""NDJSON and persisted-rollout parsing for the Codex backend."""

from __future__ import annotations

import json
import os
import stat
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, BinaryIO

import zstandard

from autoskillit.core import (
    AGENT_BACKEND_CODEX,
    AgentSessionResult,
    BackendEventKind,
    CanonicalTokenUsage,
    CliSubtype,
    CodexEventData,
    CodexEventType,
    CodexItemType,
    SessionEvent,
    fast_loads,
    get_logger,
)
from autoskillit.execution.process import _marker_is_standalone

logger = get_logger(__name__)
_ROLLOUT_METADATA_LIMIT = 64 * 1024
_ROLLOUT_SUFFIXES = (".jsonl", ".jsonl.zst")


def _safe_relative_value(value: str) -> Path:
    if not value or "\\" in value:
        raise RuntimeError(f"Unsafe relative rollout path: {value!r}")
    relative = Path(value)
    if relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
        raise RuntimeError(f"Unsafe relative rollout path: {value!r}")
    if not relative.name.endswith(_ROLLOUT_SUFFIXES):
        raise RuntimeError(f"Unsupported rollout filename: {value!r}")
    return relative


def _require_real_directory(path: Path, *, label: str) -> None:
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError as exc:
        raise RuntimeError(f"Missing {label}: {path}") from exc
    if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
        raise RuntimeError(f"{label} must be a non-symlink directory: {path}")


def _safe_relative(path: Path, root: Path) -> Path:
    _require_real_directory(root, label="rollout root")
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError as exc:
        raise RuntimeError(f"Missing rollout file: {path}") from exc
    if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
        raise RuntimeError(f"Rollout must be a regular non-symlink file: {path}")
    try:
        relative = path.relative_to(root)
    except ValueError as exc:
        raise RuntimeError(f"Rollout escapes its root: {path}") from exc
    relative = _safe_relative_value(relative.as_posix())
    cursor = root
    for part in relative.parts[:-1]:
        cursor /= part
        _require_real_directory(cursor, label="rollout parent")
    return relative


def _rollout_files(root: Path) -> Iterator[Path]:
    if not os.path.lexists(root):
        return
    _require_real_directory(root, label="rollout root")
    found: list[Path] = []
    for directory, directory_names, file_names in os.walk(root, followlinks=False):
        parent = Path(directory)
        for name in directory_names:
            candidate = parent / name
            if candidate.is_symlink():
                raise RuntimeError(f"Symlink directory in rollout tree: {candidate}")
        for name in file_names:
            candidate = parent / name
            if candidate.is_symlink():
                raise RuntimeError(f"Symlink file in rollout tree: {candidate}")
            if name.endswith(_ROLLOUT_SUFFIXES):
                _safe_relative(candidate, root)
                found.append(candidate)
    yield from sorted(found)


def _read_prefix(path: Path, limit: int) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags)
    try:
        file_stat = os.fstat(fd)
        if not stat.S_ISREG(file_stat.st_mode):
            raise ValueError(f"{path} is not a regular file")
        if path.name.endswith(".zst"):
            with os.fdopen(fd, "rb", closefd=False) as source:
                with zstandard.ZstdDecompressor().stream_reader(source) as reader:
                    return reader.read(limit)
        return os.read(fd, limit)
    finally:
        os.close(fd)


def _thread_id_from_bytes(data: bytes) -> str | None:
    for raw_line in data.splitlines():
        if not raw_line.strip():
            continue
        try:
            row = json.loads(raw_line)
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        if not isinstance(row, Mapping):
            continue
        if row.get("type") == "thread.started" and isinstance(row.get("thread_id"), str):
            return str(row["thread_id"])
        if row.get("type") == "session_meta":
            payload = row.get("payload")
            if isinstance(payload, Mapping) and isinstance(payload.get("id"), str):
                return str(payload["id"])
    return None


def _cwd_from_bytes(data: bytes) -> str | None:
    for raw_line in data.splitlines():
        if not raw_line.strip():
            continue
        try:
            row = json.loads(raw_line)
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        if not isinstance(row, Mapping) or row.get("type") != "session_meta":
            continue
        payload = row.get("payload")
        if isinstance(payload, Mapping) and isinstance(payload.get("cwd"), str):
            raw_cwd = str(payload["cwd"])
            path = Path(raw_cwd)
            if path.is_absolute():
                return str(path.expanduser().resolve(strict=False))
    return None


def _thread_id(path: Path) -> str | None:
    try:
        return _thread_id_from_bytes(_read_prefix(path, _ROLLOUT_METADATA_LIMIT))
    except (OSError, ValueError, zstandard.ZstdError):
        return None


def _rollout_cwd(path: Path) -> str | None:
    try:
        return _cwd_from_bytes(_read_prefix(path, _ROLLOUT_METADATA_LIMIT))
    except (OSError, ValueError, zstandard.ZstdError):
        return None


def _identity(path: Path) -> tuple[int, int]:
    file_stat = path.stat(follow_symlinks=False)
    if not stat.S_ISREG(file_stat.st_mode):
        raise RuntimeError(f"Expected regular rollout file: {path}")
    return file_stat.st_dev, file_stat.st_ino


@contextmanager
def _logical_rollout_reader(path: Path) -> Iterator[BinaryIO]:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags)
    try:
        file_stat = os.fstat(fd)
        if not stat.S_ISREG(file_stat.st_mode):
            raise RuntimeError(f"Rollout must be a regular file: {path}")
        with os.fdopen(fd, "rb", closefd=False) as source:
            if path.name.endswith(".zst"):
                with zstandard.ZstdDecompressor().stream_reader(source) as reader:
                    yield reader
            else:
                yield source
    finally:
        os.close(fd)


def _read_exact(reader: BinaryIO, size: int) -> bytes:
    chunks: list[bytes] = []
    remaining = size
    while remaining:
        chunk = reader.read(remaining)
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _preserves_rollout_prefix(prior: Path, candidate: Path) -> bool:
    """Return whether candidate preserves every logical byte already in prior."""
    if _identity(prior) == _identity(candidate):
        return True
    try:
        with (
            _logical_rollout_reader(prior) as prior_reader,
            _logical_rollout_reader(candidate) as candidate_reader,
        ):
            while chunk := prior_reader.read(64 * 1024):
                if _read_exact(candidate_reader, len(chunk)) != chunk:
                    return False
    except (OSError, ValueError, zstandard.ZstdError):
        return False
    return True


@dataclass
class _CodexParseAccumulator:
    session_id: str = ""
    agent_messages: list[str] = field(default_factory=list)
    command_executions: list[dict[str, Any]] = field(default_factory=list)
    mcp_tool_calls: list[dict[str, Any]] = field(default_factory=list)
    file_changes: list[str] = field(default_factory=list)
    last_usage: dict[str, Any] | None = None
    saw_failure: bool = False
    success: bool = False
    error_message: str = ""
    error_code: str = ""
    ndjson_unknown_event_count: int = 0
    ndjson_unknown_item_count: int = 0


def _scan_codex_ndjson(stdout: str) -> _CodexParseAccumulator:
    if not stdout.strip():
        return _CodexParseAccumulator()
    acc = _CodexParseAccumulator()
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
        event_type = CodexEventType.from_ndjson(obj.get("type", ""))
        if event_type == CodexEventType.UNKNOWN:
            logger.warning("codex_ndjson_unknown_event_type", type=obj.get("type", ""))
            acc.ndjson_unknown_event_count += 1
            continue
        if event_type == CodexEventType.THREAD_STARTED:
            acc.session_id = obj.get("thread_id", "")
        elif event_type == CodexEventType.SESSION_META:
            acc.session_id = obj.get("payload", {}).get("id", "")
        elif event_type in (
            CodexEventType.TURN_STARTED,
            CodexEventType.ITEM_STARTED,
            CodexEventType.ITEM_UPDATED,
        ):
            continue
        elif event_type == CodexEventType.ITEM_COMPLETED:
            item = obj.get("item", {})
            if not isinstance(item, dict):
                continue
            item_type = CodexItemType.from_ndjson(item.get("type", ""))
            if item_type == CodexItemType.AGENT_MESSAGE:
                text = item.get("text", "")
                if text:
                    acc.agent_messages.append(text)
            elif item_type == CodexItemType.MESSAGE:
                for block in item.get("content", []):
                    if isinstance(block, dict) and block.get("type") == "text":
                        text = block.get("text", "")
                        if text:
                            acc.agent_messages.append(text)
            elif item_type in (CodexItemType.COMMAND_EXECUTION, CodexItemType.FUNCTION_CALL):
                acc.command_executions.append(item)
            elif item_type == CodexItemType.MCP_TOOL_CALL:
                acc.mcp_tool_calls.append(item)
            elif item_type == CodexItemType.FILE_CHANGE:
                changes = item.get("changes", [])
                if changes and isinstance(changes, list):
                    for change in changes:
                        if isinstance(change, dict):
                            if path := change.get("path"):
                                acc.file_changes.append(path)
                else:
                    if path := item.get("path"):
                        acc.file_changes.append(path)
            elif item_type in (CodexItemType.COLLAB_TOOL_CALL, CodexItemType.WEB_SEARCH):
                acc.command_executions.append(item)
            elif item_type in (CodexItemType.REASONING, CodexItemType.TODO_LIST):
                logger.debug("codex_ndjson_informational_item", item_type=item_type.value)
                continue
            elif item_type == CodexItemType.UNKNOWN:
                logger.warning("codex_ndjson_unknown_item_type", item_type=item.get("type", ""))
                acc.ndjson_unknown_item_count += 1
                continue
        elif event_type == CodexEventType.TURN_COMPLETED:
            usage = obj.get("usage")
            if isinstance(usage, dict):
                acc.last_usage = usage
            if not acc.saw_failure:
                acc.success = True
        elif event_type == CodexEventType.TURN_FAILED:
            error = obj.get("error", {})
            if isinstance(error, dict):
                error_msg = error.get("message", "")
                error_code = error.get("code", "")
                acc.error_code = error_code
                if error_code and error_code not in error_msg:
                    acc.error_message = f"{error_msg} [{error_code}]" if error_msg else error_code
                else:
                    acc.error_message = error_msg
            else:
                acc.error_message = str(error) if error else ""
            acc.saw_failure = True
            acc.success = False
    return acc


@dataclass(frozen=True, slots=True)
class CodexResultParser:
    def parse_result(self, events: Sequence[SessionEvent]) -> AgentSessionResult:
        if not events:
            return AgentSessionResult(
                success=False,
                exit_code=1,
                backend_name=AGENT_BACKEND_CODEX,
                elapsed_seconds=0.0,
                error="empty events sequence",
            )
        session_id: str | None = None
        has_completion = False
        for event in events:
            if event.kind == BackendEventKind.SESSION_META and event.session_id:
                session_id = event.session_id
            if event.kind == BackendEventKind.COMPLETION:
                has_completion = True
        return AgentSessionResult(
            success=has_completion,
            exit_code=0 if has_completion else 1,
            backend_name=AGENT_BACKEND_CODEX,
            elapsed_seconds=0.0,
            session_id=session_id,
        )

    def parse_stdout(self, stdout: str, *, exit_code: int = 0) -> AgentSessionResult:
        acc = _scan_codex_ndjson(stdout)
        if acc.success:
            subtype = CliSubtype.SUCCESS.value
        elif acc.error_message:
            subtype = CliSubtype.ERROR_DURING_EXECUTION.value
        elif not stdout.strip():
            subtype = CliSubtype.EMPTY_OUTPUT.value
        else:
            subtype = CliSubtype.UNPARSEABLE.value
        is_error = subtype != CliSubtype.SUCCESS.value
        canonical_dict = None
        if acc.last_usage is not None:
            canonical = CanonicalTokenUsage.from_codex_dict(acc.last_usage)
            canonical_dict = canonical.to_dict()
        return AgentSessionResult(
            success=not is_error,
            exit_code=0 if not is_error else (exit_code or 1),
            backend_name=AGENT_BACKEND_CODEX,
            elapsed_seconds=0.0,
            session_id=acc.session_id or None,
            output="\n".join(acc.agent_messages),
            error=acc.error_message,
            raw={
                "subtype": subtype,
                "is_error": is_error,
                "token_usage": acc.last_usage,
                "canonical_token_usage": canonical_dict,
                "agent_messages": acc.agent_messages,
                "command_executions": acc.command_executions,
                "mcp_tool_calls": acc.mcp_tool_calls,
                "file_changes": acc.file_changes,
                "error_code": acc.error_code,
                "ndjson_unknown_event_count": acc.ndjson_unknown_event_count,
                "ndjson_unknown_item_count": acc.ndjson_unknown_item_count,
            },
        )


@dataclass(slots=True)
class CodexStreamParser:
    """Stateful NDJSON stream parser for Codex CLI output.

    One instance per session — accumulates marker detection state across
    parse_line() calls. Not reusable across sessions.
    """

    completion_marker: str = ""
    _saw_marker: bool = field(default=False, init=False, repr=False)
    ndjson_unknown_event_count: int = field(default=0, init=False, repr=False)
    ndjson_unknown_item_count: int = field(default=0, init=False, repr=False)

    def _check_marker_text(self, text: str) -> None:
        if self.completion_marker and _marker_is_standalone(text, self.completion_marker):
            self._saw_marker = True

    def parse_line(self, line: str) -> SessionEvent | None:
        line = line.strip()
        if not line:
            return None
        try:
            obj = fast_loads(line)
        except (ValueError, TypeError):
            return None
        if not isinstance(obj, dict):
            return None

        event_type = CodexEventType.from_ndjson(obj.get("type", ""))

        if event_type == CodexEventType.THREAD_STARTED:
            return SessionEvent(
                kind=BackendEventKind.SESSION_META,
                is_terminal=False,
                has_marker=False,
                session_id=obj.get("thread_id", "") or None,
            )

        if event_type == CodexEventType.SESSION_META:
            return SessionEvent(
                kind=BackendEventKind.SESSION_META,
                is_terminal=False,
                has_marker=False,
                session_id=obj.get("payload", {}).get("id", "") or None,
            )

        if event_type in (CodexEventType.TURN_STARTED, CodexEventType.ITEM_STARTED):
            return SessionEvent(
                kind=BackendEventKind.IGNORED,
                is_terminal=False,
                has_marker=False,
            )

        if event_type == CodexEventType.ITEM_COMPLETED:
            item = obj.get("item", {})
            if not isinstance(item, dict):
                return SessionEvent(
                    kind=BackendEventKind.IGNORED,
                    is_terminal=False,
                    has_marker=False,
                )
            item_type = CodexItemType.from_ndjson(item.get("type", ""))

            if item_type == CodexItemType.AGENT_MESSAGE:
                self._check_marker_text(item.get("text", ""))
                return SessionEvent(
                    kind=BackendEventKind.TOOL_OUTPUT,
                    is_terminal=False,
                    has_marker=False,
                    backend_data=CodexEventData(
                        record_type="item.completed",
                        thread_id="",
                        item_type="agent_message",
                        raw=obj,
                    ),
                )

            if item_type == CodexItemType.MESSAGE:
                for block in item.get("content", []):
                    if isinstance(block, dict) and block.get("type") == "text":
                        self._check_marker_text(block.get("text", ""))
                return SessionEvent(
                    kind=BackendEventKind.TOOL_OUTPUT,
                    is_terminal=False,
                    has_marker=False,
                    backend_data=CodexEventData(
                        record_type="item.completed",
                        thread_id="",
                        item_type="message",
                        raw=obj,
                    ),
                )

            if item_type in (
                CodexItemType.FILE_CHANGE,
                CodexItemType.COMMAND_EXECUTION,
                CodexItemType.FUNCTION_CALL,
                CodexItemType.MCP_TOOL_CALL,
                CodexItemType.COLLAB_TOOL_CALL,
                CodexItemType.WEB_SEARCH,
            ):
                return SessionEvent(
                    kind=BackendEventKind.TOOL_OUTPUT,
                    is_terminal=False,
                    has_marker=False,
                    backend_data=CodexEventData(
                        record_type="item.completed",
                        thread_id="",
                        item_type=item_type.value,
                        raw=obj,
                    ),
                )

            if item_type in (CodexItemType.REASONING, CodexItemType.TODO_LIST):
                return SessionEvent(
                    kind=BackendEventKind.IGNORED,
                    is_terminal=False,
                    has_marker=False,
                )

            self.ndjson_unknown_item_count += 1
            logger.warning("codex_ndjson_unknown_item_type", item_type=item.get("type", ""))
            return SessionEvent(
                kind=BackendEventKind.IGNORED,
                is_terminal=False,
                has_marker=False,
            )

        if event_type == CodexEventType.TURN_COMPLETED:
            return SessionEvent(
                kind=BackendEventKind.COMPLETION,
                is_terminal=True,
                has_marker=self._saw_marker,
                backend_data=CodexEventData(
                    record_type="turn.completed",
                    thread_id="",
                    item_type="",
                    raw=obj,
                    usage=obj.get("usage"),
                ),
            )

        if event_type == CodexEventType.TURN_FAILED:
            return SessionEvent(
                kind=BackendEventKind.COMPLETION,
                is_terminal=True,
                has_marker=False,
                backend_data=CodexEventData(
                    record_type="turn.failed",
                    thread_id="",
                    item_type="",
                    raw=obj,
                ),
            )

        if event_type == CodexEventType.ERROR:
            return SessionEvent(
                kind=BackendEventKind.ERROR,
                is_terminal=True,
                has_marker=False,
                backend_data=CodexEventData(
                    record_type="error",
                    thread_id="",
                    item_type="",
                    raw=obj,
                ),
            )

        if event_type == CodexEventType.ITEM_UPDATED:
            return SessionEvent(
                kind=BackendEventKind.IGNORED,
                is_terminal=False,
                has_marker=False,
            )

        self.ndjson_unknown_event_count += 1
        logger.warning("codex_ndjson_unknown_event_type", type=obj.get("type", ""))
        return SessionEvent(
            kind=BackendEventKind.IGNORED,
            is_terminal=False,
            has_marker=False,
        )
