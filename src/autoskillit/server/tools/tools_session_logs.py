"""Bounded, server-owned inspection of retained session diagnostics."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import stat
import time
from pathlib import Path
from typing import Any

import regex as re

from autoskillit.core import (
    ContainmentError,
    claude_code_log_path,
    get_logger,
    read_stable_contained_range,
)
from autoskillit.execution import read_session_index_rows, resolve_log_dir
from autoskillit.server import _get_ctx, mcp
from autoskillit.server._guards import _require_enabled
from autoskillit.server.tools._cancellation_shield import _cancellation_shield

logger = get_logger(__name__)

_MAX_SESSION_IDS = 32
_MAX_SESSION_ID_CHARS = 160
_MAX_INDEX_BYTES = 8_000_000
_MAX_QUERY_CHARS = 256
_MAX_PAGE_BYTES = 64_000
_MAX_SCAN_BYTES = 512_000
_MAX_MATCHES = 50
_MAX_EXCERPT_CHARS = 500
_MAX_CONTINUATION_CHARS = 8_192
_CONTINUATION_LIFETIME_SECONDS = 300
_CONTINUATION_VERSION = 1
_CONTINUATION_KEY: bytes | None = None
_CONTINUATION_CLOCK = time.time
_HANDLES = frozenset({"summary", "anomalies", "audit", "transcript"})
_DIR_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,199}\Z")


class _InspectionError(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _blocked(operation: str, reason: str) -> str:
    return _json(
        {
            "operation": operation,
            "status": "blocked",
            "reason": reason,
            "searched_scope": {},
            "truncated": False,
            "incomplete_final_line": False,
        }
    )


def _load_rows(log_root: Path) -> list[dict[str, Any]]:
    try:
        return read_session_index_rows(
            log_root / "sessions.jsonl",
            max_bytes=_MAX_INDEX_BYTES,
        )
    except (OSError, ValueError) as exc:
        raise _InspectionError("index_invalid") from exc


def _find_row(rows: list[dict[str, Any]], session_id: str) -> dict[str, Any]:
    matches = [row for row in rows if row.get("session_id") == session_id]
    if not matches:
        raise _InspectionError("session_unknown")
    if len(matches) != 1:
        raise _InspectionError("session_ambiguous")
    return matches[0]


def _same_path(left: object, right: Path) -> bool:
    if not isinstance(left, str) or not left:
        return False
    try:
        return Path(left).expanduser().resolve(strict=False) == right.resolve(strict=False)
    except OSError:
        return False


def _artifact_location(
    row: dict[str, Any],
    artifact: str,
    log_root: Path,
) -> tuple[Path, Path, bool]:
    if artifact not in _HANDLES:
        raise _InspectionError("artifact_invalid")
    session_id = row.get("session_id")
    dir_name = row.get("dir_name")
    if not isinstance(session_id, str) or not isinstance(dir_name, str):
        raise _InspectionError("index_invalid")
    if not _DIR_NAME_RE.fullmatch(dir_name) or Path(dir_name).name != dir_name:
        raise _InspectionError("index_invalid")

    if artifact != "transcript":
        filenames = {
            "summary": "summary.json",
            "anomalies": "anomalies.jsonl",
            "audit": "audit_log.json",
        }
        root = log_root / "sessions"
        return root / dir_name / filenames[artifact], root, artifact == "anomalies"

    claude_log = row.get("claude_code_log")
    codex_log = row.get("codex_log")
    if isinstance(claude_log, str) and claude_log and not codex_log:
        cwd = row.get("cwd")
        if not isinstance(cwd, str) or not cwd:
            raise _InspectionError("index_invalid")
        expected = claude_code_log_path(cwd, session_id)
        indexed = claude_log
    elif isinstance(codex_log, str) and codex_log and not claude_log:
        ctx = _get_ctx()
        if ctx.backend is None:
            raise _InspectionError("backend_unavailable")
        expected = ctx.backend.session_locator().locate_session(session_id)
        indexed = codex_log
    else:
        raise _InspectionError("backend_invalid")
    if expected is None or not _same_path(indexed, expected):
        raise _InspectionError("transcript_identity_mismatch")
    return expected, expected.parent, True


def _existing_handles(row: dict[str, Any], log_root: Path) -> list[str]:
    handles: list[str] = []
    for handle in ("summary", "anomalies", "audit", "transcript"):
        try:
            path, _, _ = _artifact_location(row, handle, log_root)
            mode = path.lstat().st_mode
        except (OSError, _InspectionError):
            continue
        if stat.S_ISREG(mode):
            handles.append(handle)
    return handles


def _index_result(session_ids: list[str], log_root: Path) -> str:
    rows = _load_rows(log_root)
    sessions: list[dict[str, Any]] = []
    for session_id in session_ids:
        row = _find_row(rows, session_id)
        sessions.append(
            {
                "session_id": session_id,
                "dir_name": row.get("dir_name", ""),
                "backend": row.get("backend", ""),
                "kitchen_id": row.get("kitchen_id", ""),
                "step_name": row.get("step_name", ""),
                "handles": _existing_handles(row, log_root),
                "retry": {
                    key: row.get(key)
                    for key in (
                        "success",
                        "subtype",
                        "exit_code",
                        "kill_reason",
                        "needs_retry",
                        "retry_reason",
                    )
                    if key in row
                },
            }
        )
    return _json(
        {
            "operation": "index",
            "status": "answered",
            "reason": "",
            "sessions": sessions,
            "searched_scope": {"requested_session_ids": session_ids},
            "truncated": False,
            "incomplete_final_line": False,
        }
    )


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _b64decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.b64decode(value + padding, altchars=b"-_", validate=True)


def _continuation_key() -> bytes:
    global _CONTINUATION_KEY
    if _CONTINUATION_KEY is None:
        _CONTINUATION_KEY = secrets.token_bytes(32)
    return _CONTINUATION_KEY


def _encode_continuation(payload: dict[str, Any]) -> str:
    body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    signature = hmac.digest(_continuation_key(), body, "sha256")
    return f"{_b64encode(body)}.{_b64encode(signature)}"


def _decode_continuation(token: str) -> dict[str, Any]:
    if not token or len(token) > _MAX_CONTINUATION_CHARS:
        raise _InspectionError("continuation_invalid")
    try:
        encoded_body, encoded_signature = token.split(".", 1)
        body = _b64decode(encoded_body)
        signature = _b64decode(encoded_signature)
        if not hmac.compare_digest(hmac.digest(_continuation_key(), body, "sha256"), signature):
            raise ValueError
        payload = json.loads(body)
    except (ValueError, TypeError, json.JSONDecodeError):
        raise _InspectionError("continuation_invalid") from None
    if not isinstance(payload, dict) or payload.get("version") != _CONTINUATION_VERSION:
        raise _InspectionError("continuation_invalid")
    now = _CONTINUATION_CLOCK()
    issued = payload.get("issued")
    expires = payload.get("expires")
    if (
        isinstance(issued, bool)
        or isinstance(expires, bool)
        or not isinstance(issued, (int, float))
        or not isinstance(expires, (int, float))
    ):
        raise _InspectionError("continuation_invalid")
    if issued > now or expires < now or expires - issued > _CONTINUATION_LIFETIME_SECONDS:
        raise _InspectionError("continuation_invalid")
    return payload


def _path_digest(path: Path) -> str:
    return hashlib.sha256(os.fsencode(path.resolve(strict=False))).hexdigest()


def _snapshot_payload(path: Path, opened: os.stat_result) -> dict[str, int | str]:
    return {
        "path": _path_digest(path),
        "device": opened.st_dev,
        "inode": opened.st_ino,
        "mode": opened.st_mode,
        "links": opened.st_nlink,
        "size": opened.st_size,
        "mtime_ns": opened.st_mtime_ns,
    }


def _validate_snapshot(
    payload: dict[str, Any],
    path: Path,
    opened: os.stat_result,
) -> None:
    expected = payload.get("snapshot")
    if not isinstance(expected, dict) or expected != _snapshot_payload(path, opened):
        raise _InspectionError("snapshot_stale")


def _continuation_payload(
    *,
    operation: str,
    session_id: str,
    artifact: str,
    query: str,
    path: Path,
    opened: os.stat_result,
    offset: int,
    line: int,
) -> dict[str, Any]:
    issued = int(_CONTINUATION_CLOCK())
    return {
        "version": _CONTINUATION_VERSION,
        "issued": issued,
        "expires": issued + _CONTINUATION_LIFETIME_SECONDS,
        "operation": operation,
        "session_id": session_id,
        "artifact": artifact,
        "query_sha256": hashlib.sha256(query.encode()).hexdigest(),
        "snapshot": _snapshot_payload(path, opened),
        "offset": offset,
        "line": line,
    }


def _request_position(
    *,
    continuation: str,
    operation: str,
    session_id: str,
    artifact: str,
    query: str,
) -> tuple[int, int, dict[str, Any] | None]:
    if not continuation:
        return 0, 1, None
    payload = _decode_continuation(continuation)
    expected = (
        payload.get("operation") == operation
        and payload.get("session_id") == session_id
        and payload.get("artifact") == artifact
        and payload.get("query_sha256") == hashlib.sha256(query.encode()).hexdigest()
    )
    offset = payload.get("offset")
    line = payload.get("line")
    if (
        not expected
        or isinstance(offset, bool)
        or isinstance(line, bool)
        or not isinstance(offset, int)
        or not isinstance(line, int)
    ):
        raise _InspectionError("continuation_invalid")
    if offset < 0 or line < 1:
        raise _InspectionError("continuation_invalid")
    return offset, line, payload


def _complete_lines(
    data: bytes,
    *,
    starts_at: int,
    snapshot_size: int,
    newline_required: bool,
) -> tuple[list[bytes], bool]:
    lines = data.splitlines(keepends=True)
    at_end = starts_at + len(data) >= snapshot_size
    incomplete = False
    if lines and not lines[-1].endswith(b"\n"):
        if newline_required or not at_end:
            lines.pop()
            incomplete = at_end
    return lines, incomplete


def _read_or_search(
    *,
    operation: str,
    session_id: str,
    artifact: str,
    query: str,
    continuation: str,
    page_limit: int,
    log_root: Path,
) -> str:
    row = _find_row(_load_rows(log_root), session_id)
    path, allowed_root, newline_required = _artifact_location(row, artifact, log_root)
    if path.suffix == ".zst":
        raise _InspectionError("unsupported_format")
    offset, line_number, prior = _request_position(
        continuation=continuation,
        operation=operation,
        session_id=session_id,
        artifact=artifact,
        query=query,
    )
    try:
        resolved, data, opened = read_stable_contained_range(
            path,
            allowed_root,
            offset=offset,
            length=_MAX_SCAN_BYTES,
            max_range_bytes=_MAX_SCAN_BYTES,
        )
    except FileNotFoundError as exc:
        raise _InspectionError("artifact_missing") from exc
    except ContainmentError as exc:
        raise _InspectionError(exc.reason) from exc
    if prior is not None:
        _validate_snapshot(prior, resolved, opened)

    lines, incomplete_final_line = _complete_lines(
        data,
        starts_at=offset,
        snapshot_size=opened.st_size,
        newline_required=newline_required,
    )
    if not lines and data and offset + len(data) < opened.st_size:
        raise _InspectionError("record_too_large")
    start_line = line_number
    consumed = 0
    returned_bytes = 0
    end_line = line_number - 1
    content: list[str] = []
    matches: list[dict[str, Any]] = []
    query_bytes = query.encode()
    for raw_line in lines:
        try:
            decoded = raw_line.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise _InspectionError("invalid_utf8") from exc
        if operation == "read":
            if returned_bytes + len(raw_line) > page_limit:
                if not content:
                    raise _InspectionError("record_too_large")
                break
            content.append(decoded)
            returned_bytes += len(raw_line)
        elif query_bytes in raw_line:
            excerpt = decoded.rstrip("\r\n")[:_MAX_EXCERPT_CHARS]
            excerpt_bytes = len(excerpt.encode())
            if matches and (
                len(matches) >= _MAX_MATCHES or returned_bytes + excerpt_bytes > page_limit
            ):
                break
            matches.append(
                {
                    "line": line_number,
                    "citation": f"{session_id}/{artifact}:{line_number}",
                    "excerpt": excerpt,
                }
            )
            returned_bytes += excerpt_bytes
        consumed += len(raw_line)
        end_line = line_number
        line_number += 1

    next_offset = offset + consumed
    more = next_offset < opened.st_size and not incomplete_final_line
    next_continuation = ""
    if more:
        next_continuation = _encode_continuation(
            _continuation_payload(
                operation=operation,
                session_id=session_id,
                artifact=artifact,
                query=query,
                path=resolved,
                opened=opened,
                offset=next_offset,
                line=line_number,
            )
        )
    status = "partial" if incomplete_final_line else "answered"
    response: dict[str, Any] = {
        "operation": operation,
        "status": status,
        "reason": "incomplete_final_line" if incomplete_final_line else "",
        "session_id": session_id,
        "artifact": artifact,
        "citation": f"{session_id}/{artifact}:{start_line}-{end_line}",
        "line_range": {"start": start_line, "end": end_line},
        "exact_bytes": returned_bytes,
        "bytes_scanned": consumed,
        "searched_scope": {
            "start_byte": offset,
            "end_byte": next_offset,
            "start_line": start_line,
            "end_line": end_line,
        },
        "truncated": more,
        "incomplete_final_line": incomplete_final_line,
        "next_continuation": next_continuation,
    }
    if operation == "read":
        response["content"] = "".join(content)
    else:
        response["query"] = query
        response["matches"] = matches
    return _json(response)


def _validate_arguments(
    operation: str,
    session_ids: list[str] | None,
    session_id: str,
    artifact: str,
    query: str,
    continuation: str,
) -> None:
    if operation not in {"index", "read", "search"}:
        raise _InspectionError("operation_invalid")
    if operation == "index":
        if session_id or artifact or query or continuation:
            raise _InspectionError("arguments_invalid")
        if not session_ids or len(session_ids) > _MAX_SESSION_IDS:
            raise _InspectionError("session_batch_invalid")
        if len(set(session_ids)) != len(session_ids):
            raise _InspectionError("session_batch_invalid")
        if any(not value or len(value) > _MAX_SESSION_ID_CHARS for value in session_ids):
            raise _InspectionError("session_batch_invalid")
        return
    if session_ids:
        raise _InspectionError("arguments_invalid")
    if not session_id or len(session_id) > _MAX_SESSION_ID_CHARS or artifact not in _HANDLES:
        raise _InspectionError("arguments_invalid")
    if operation == "read" and query:
        raise _InspectionError("arguments_invalid")
    if operation == "search" and (not query or len(query) > _MAX_QUERY_CHARS):
        raise _InspectionError("arguments_invalid")


@mcp.tool(
    tags={"autoskillit", "kitchen", "kitchen-core"},
    annotations={"readOnlyHint": True},
)
@_cancellation_shield()
async def inspect_session_logs(
    operation: str,
    session_ids: list[str] | None = None,
    session_id: str = "",
    artifact: str = "",
    query: str = "",
    continuation: str = "",
    byte_limit: int = _MAX_PAGE_BYTES,
) -> str:
    """Inspect retained session evidence through bounded server-derived handles.

    Never raises.
    """
    try:
        if (gate := _require_enabled()) is not None:
            return gate
        _validate_arguments(
            operation,
            session_ids,
            session_id,
            artifact,
            query,
            continuation,
        )
        if byte_limit <= 0:
            raise _InspectionError("byte_limit_invalid")
        page_limit = min(byte_limit, _MAX_PAGE_BYTES)
        log_root = resolve_log_dir(_get_ctx().config.linux_tracing.log_dir)
        if operation == "index":
            return _index_result(session_ids or [], log_root)
        return _read_or_search(
            operation=operation,
            session_id=session_id,
            artifact=artifact,
            query=query,
            continuation=continuation,
            page_limit=page_limit,
            log_root=log_root,
        )
    except _InspectionError as exc:
        return _blocked(operation, exc.reason)
    except Exception:
        logger.warning("session log inspection failed", exc_info=True)
        return _blocked(operation, "internal_error")
