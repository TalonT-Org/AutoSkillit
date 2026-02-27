"""Read-only SQLite data access layer.

No dependency on MCP, config, types, or other autoskillit modules beyond _logging.py.
"""

from __future__ import annotations

import base64
import re
import sqlite3
import threading

from autoskillit.core.logging import get_logger

logger = get_logger(__name__)

_FORBIDDEN_SQL = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|REPLACE|ATTACH|DETACH|PRAGMA|VACUUM|REINDEX)\b",
    re.IGNORECASE,
)
_STRIP_SQL_COMMENTS = re.compile(r"--[^\n]*|/\*.*?\*/", re.DOTALL)

_ALLOWED_ACTIONS: frozenset[int] = frozenset(
    {
        sqlite3.SQLITE_SELECT,
        sqlite3.SQLITE_READ,
        sqlite3.SQLITE_FUNCTION,
    }
)


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
    conn = sqlite3.connect(uri, uri=True, cached_statements=0)
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
