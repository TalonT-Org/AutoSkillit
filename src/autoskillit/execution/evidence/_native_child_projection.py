"""Read and attribute structurally verified native child evidence for reports."""

from __future__ import annotations

import hashlib
import json
import math
from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import zstandard

from autoskillit.core import (
    CANONICAL_ACCOUNTING_FIELDS,
    SerializedTokenMeasure,
    TokenMeasure,
    iter_merged_assistant_turns,
)
from autoskillit.execution.backends._codex_execution_identity import (
    extract_codex_child_metadata,
)
from autoskillit.execution.backends._codex_parse import (
    _logical_rollout_reader,
    extract_codex_child_turn_usage,
)
from autoskillit.execution.child_outcomes import (
    collect_child_outcomes,
    enumerate_claude_subagent_transcripts,
    normalize_backend_name,
)
from autoskillit.execution.session import extract_token_usage
from autoskillit.execution.session.turn_usage import (
    classify_token_measure,
    claude_inclusive_input_tokens,
)


def project_child_outcomes(
    row: dict[str, Any],
    *,
    log_root: Path,
    parent_rows: Mapping[tuple[str, str], Sequence[dict[str, Any]]],
) -> tuple[dict[str, Any], ...]:
    parent_id = row.get("session_id")
    backend = row.get("backend")
    if not isinstance(parent_id, str) or not parent_id or not isinstance(backend, str):
        return ()
    normalized_backend = normalize_backend_name(backend)
    candidates = parent_rows.get((normalized_backend, parent_id), ())
    outcomes = collect_child_outcomes(
        backend=normalized_backend,
        parent_session_id=parent_id,
        log_root=log_root,
    )
    projected = []
    for outcome in outcomes:
        child = _project_child_outcome(
            row,
            dict(outcome),
            parent_id=parent_id,
            backend=normalized_backend,
            candidates=candidates,
        )
        if child is not None:
            projected.append(child)
    return tuple(projected)


def _project_child_outcome(
    row: dict[str, Any],
    child: dict[str, Any],
    *,
    parent_id: str,
    backend: str,
    candidates: Sequence[dict[str, Any]],
) -> dict[str, Any] | None:
    if not _is_native_child(child, backend, parent_id):
        return None
    path, identity_verified = _child_transcript_path(row, child, backend, parent_id)
    if not identity_verified:
        return None
    text = _read_child_transcript(path, backend) if path is not None and path.is_file() else None
    complete = _complete_jsonl(text)
    child_timestamp = _first_transcript_timestamp(text) if text is not None else None
    if not _child_belongs_to_parent(row, candidates, child_timestamp):
        return None

    provider = child.get("effective_provider")
    provider_used = (
        provider
        if isinstance(provider, str) and provider and provider.casefold() != "unknown"
        else ""
    )
    tool_counts, token_usage, usage_state = _child_measurements(
        backend,
        child["child_id"],
        path,
        text,
        complete,
        provider_used,
    )
    child.update(
        {
            "native_parent_session_id": parent_id,
            "parent_session_key": row.get("dir_name"),
            "transcript_state": "observed" if complete else "unknown",
            "usage_state": usage_state,
            "tool_counts": tool_counts,
            "token_usage": token_usage,
            "_child_timestamp": child_timestamp,
            "_transcript_fingerprint": _transcript_fingerprint(path, text),
        }
    )
    return child


def _is_native_child(child: Mapping[str, Any], backend: str, parent_id: str) -> bool:
    expected_source = {
        "claude_code": "transcript_metadata",
        "codex": "codex_rollout_metadata",
    }.get(backend)
    return (
        expected_source is not None
        and child.get("backend") == backend
        and child.get("parent_session_id") == parent_id
        and isinstance(child.get("child_id"), str)
        and bool(child.get("child_id"))
        and isinstance(child.get("role"), str)
        and bool(child.get("role"))
        and child.get("evidence_source") == expected_source
    )


def _child_transcript_path(
    row: dict[str, Any], child: Mapping[str, Any], backend: str, parent_id: str
) -> tuple[Path | None, bool]:
    child_id = child.get("child_id")
    if not isinstance(child_id, str) or not child_id:
        return None, False
    if backend == "claude_code":
        return _claude_child_transcript_path(row, child_id, parent_id)
    if backend == "codex":
        return _codex_child_transcript_path(child, child_id, parent_id)
    return None, False


def _claude_child_transcript_path(
    row: dict[str, Any], child_id: str, parent_id: str
) -> tuple[Path | None, bool]:
    parent_log = row.get("claude_code_log")
    if not isinstance(parent_log, str) or not Path(parent_log).is_absolute():
        return None, True
    if Path(parent_log).stem != parent_id:
        return None, False
    try:
        paths = enumerate_claude_subagent_transcripts(Path(parent_log))
    except OSError:
        return None, True
    return next((path for path in paths if path.name == f"agent-{child_id}.jsonl"), None), True


def _codex_child_transcript_path(
    child: Mapping[str, Any], child_id: str, parent_id: str
) -> tuple[Path | None, bool]:
    locator = child.get("transcript_locator")
    if not isinstance(locator, str) or not Path(locator).is_absolute():
        return None, True
    path = Path(locator)
    if not path.exists():
        return path, True
    try:
        extract_codex_child_metadata(
            path,
            expected_parent_id=parent_id,
            expected_child_id=child_id,
        )
    except (OSError, RuntimeError, ValueError, zstandard.ZstdError):
        return None, False
    return path, True


def _child_belongs_to_parent(
    row: dict[str, Any], candidates: Sequence[dict[str, Any]], child_timestamp: float | None
) -> bool:
    row_key = row.get("dir_name")
    if not isinstance(row_key, str):
        return False
    if len(candidates) == 1:
        return candidates[0].get("dir_name") == row_key
    if child_timestamp is None:
        return False
    matches = [
        candidate for candidate in candidates if _invocation_contains(candidate, child_timestamp)
    ]
    return len(matches) == 1 and matches[0].get("dir_name") == row_key


def _invocation_contains(row: dict[str, Any], timestamp: float) -> bool:
    start = _timestamp_seconds(row.get("timestamp"))
    duration = row.get("duration_seconds")
    if (
        start is None
        or isinstance(duration, bool)
        or not isinstance(duration, (int, float))
        or not math.isfinite(float(duration))
        or duration < 0
    ):
        return False
    return start <= timestamp <= start + float(duration)


def _timestamp_seconds(value: object) -> float | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.timestamp()


def _first_transcript_timestamp(text: str) -> float | None:
    for line in text.splitlines():
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(record, Mapping):
            timestamp = _timestamp_seconds(record.get("timestamp"))
            if timestamp is not None:
                return timestamp
    return None


def _read_child_transcript(path: Path, backend: str) -> str | None:
    try:
        if backend == "codex":
            with _logical_rollout_reader(path) as handle:
                return handle.read().decode("utf-8")
        return path.read_text(encoding="utf-8")
    except (OSError, RuntimeError, UnicodeError, ValueError, zstandard.ZstdError):
        return None


def _complete_jsonl(text: str | None) -> bool:
    if not text or not text.endswith("\n"):
        return False
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            return False
        if not isinstance(record, Mapping):
            return False
    return True


def _child_measurements(
    backend: str,
    child_id: str,
    path: Path | None,
    text: str | None,
    complete: bool,
    provider: str,
) -> tuple[dict[str, int] | None, dict[str, SerializedTokenMeasure], str]:
    if not complete or text is None:
        return None, _unknown_usage(), "unknown"
    parser_backend = "claude" if backend == "claude_code" else backend
    turns = list(iter_merged_assistant_turns(text, backend=parser_backend))
    tool_counts = dict(Counter(name for turn in turns for name in turn.tool_names))
    token_usage, usage_state = _child_token_usage(backend, child_id, path, text, provider)
    return tool_counts, token_usage, usage_state


def _child_token_usage(
    backend: str, child_id: str, path: Path | None, text: str, provider: str
) -> tuple[dict[str, SerializedTokenMeasure], str]:
    if not provider:
        return _unknown_usage(), "unknown"
    if backend == "claude_code":
        aggregate, _rows = extract_token_usage(text, provider_used=provider)
        if aggregate is not None:
            usage = {
                field: aggregate.get(field, TokenMeasure.unknown().to_dict())
                for field in CANONICAL_ACCOUNTING_FIELDS
            }
            inclusive_input = claude_inclusive_input_tokens(
                *(
                    TokenMeasure.from_dict(usage[field]).value
                    for field in ("input_tokens", "cache_read_tokens", "cache_write_tokens")
                )
            )
            usage["input_tokens"] = classify_token_measure(
                backend, provider, "input_tokens", inclusive_input
            ).to_dict()
            return (
                usage,
                "observed",
            )
        return _unknown_usage(), "unknown"
    if backend == "codex" and path is not None:
        rows = extract_codex_child_turn_usage(path, child_id, provider_used=provider)
        if rows:
            return _codex_usage_measures(rows, provider), "observed"
    return _unknown_usage(), "unknown"


def _codex_usage_measures(
    rows: Sequence[Mapping[str, Any]], provider: str
) -> dict[str, SerializedTokenMeasure]:
    measures: dict[str, SerializedTokenMeasure] = {}
    for field in CANONICAL_ACCOUNTING_FIELDS:
        values = [row.get(field) for row in rows]
        total = (
            sum(
                value for value in values if isinstance(value, int) and not isinstance(value, bool)
            )
            if values
            and all(
                isinstance(value, int) and not isinstance(value, bool) and value >= 0
                for value in values
            )
            else None
        )
        measures[field] = classify_token_measure("codex", provider, field, total).to_dict()
    return measures


def _unknown_usage() -> dict[str, SerializedTokenMeasure]:
    return {field: TokenMeasure.unknown().to_dict() for field in CANONICAL_ACCOUNTING_FIELDS}


def _transcript_fingerprint(path: Path | None, text: str | None) -> str | None:
    if path is None:
        return None
    identity: list[int] | None = None
    try:
        stat = path.stat()
        identity = [stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns]
    except OSError:
        pass
    material = json.dumps(
        {
            "path": str(path),
            "identity": identity,
            "content": hashlib.sha256((text or "").encode("utf-8")).hexdigest(),
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(material).hexdigest()


def child_evidence_fingerprint(
    log_root: Path,
    rows: Sequence[dict[str, Any]],
    parent_rows: Mapping[tuple[str, str], Sequence[dict[str, Any]]],
) -> str:
    """Digest owner-attributed child facts and the dedicated transcripts they use."""
    dependencies = [
        _child_dependency(row, child)
        for row in rows
        for child in project_child_outcomes(row, log_root=log_root, parent_rows=parent_rows)
    ]
    dependencies.sort(key=_canonical_json)
    return hashlib.sha256(_canonical_json(dependencies).encode("utf-8")).hexdigest()


def _child_dependency(row: dict[str, Any], child: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "parent_session_key": child.get("parent_session_key"),
        "parent_timestamp": row.get("timestamp"),
        "parent_duration_seconds": row.get("duration_seconds"),
        "native_parent_session_id": child.get("native_parent_session_id"),
        "child_id": child.get("child_id"),
        "backend": child.get("backend"),
        "role": child.get("role"),
        "skill": child.get("attribution_skill"),
        "provider": child.get("effective_provider"),
        "model": child.get("effective_model"),
        "evidence_source": child.get("evidence_source"),
        "transcript_state": child.get("transcript_state"),
        "usage_state": child.get("usage_state"),
        "tool_counts": child.get("tool_counts"),
        "token_usage": child.get("token_usage"),
        "transcript_fingerprint": child.get("_transcript_fingerprint"),
    }


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
