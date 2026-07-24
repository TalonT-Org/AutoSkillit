"""Bounded, durable observability records for Codex cook startup."""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import regex as re

from autoskillit.core import default_log_dir

_SCHEMA_VERSION = 1
_RECORD_LIMIT_BYTES = 16 * 1024
_LAUNCH_ID_RE = re.compile(r"[0-9a-f]{16}\Z")
_BUDGETS_SECONDS = {
    "confirmation_to_spawn": 5.0,
    "spawn_to_hook_review": 12.0,
    "total_startup": 17.0,
}


class _MandatoryRecordOverflow(Exception):
    """A trace record's required fields cannot fit in the schema limit."""


def startup_trace_path(project_dir: Path, launch_id: str) -> Path:
    """Return the sole canonical trace path for a project launch."""
    if _LAUNCH_ID_RE.fullmatch(launch_id) is None:
        raise ValueError("launch_id must be exactly 16 lowercase hexadecimal characters")

    resolved_project = Path(project_dir).resolve(strict=True)
    project_key = hashlib.sha256(os.fsencode(str(resolved_project))).hexdigest()[:16]
    trace_root = default_log_dir() / "codex-startup"
    trace_parent = trace_root / project_key

    resolved_root = trace_root.resolve(strict=False)
    resolved_parent = trace_parent.resolve(strict=False)
    if not resolved_parent.is_relative_to(resolved_root):
        raise ValueError("startup trace parent escapes the canonical trace root")

    return trace_parent / f"{launch_id}.jsonl"


def _json_line(record: Mapping[str, object]) -> bytes:
    serialized = json.dumps(
        record,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return serialized.encode("utf-8") + b"\n"


def _truncate_utf8(value: str, limit: int) -> str:
    raw = value.encode("utf-8")
    if len(raw) <= limit:
        return value
    return raw[:limit].decode("utf-8", errors="ignore")


def _truncate_strings(value: Any, limit: int) -> Any:
    """Return JSON-shaped diagnostics with every string byte-bounded."""
    if isinstance(value, str):
        return _truncate_utf8(value, limit)
    if isinstance(value, list):
        return [_truncate_strings(item, limit) for item in value]
    if isinstance(value, dict):
        return {key: _truncate_strings(item, limit) for key, item in value.items()}
    return value


def _serialize_bounded(record: dict[str, object]) -> bytes:
    """Serialize one record, truncating only its optional diagnostics."""
    raw = _json_line(record)
    if len(raw) <= _RECORD_LIMIT_BYTES:
        return raw

    if "diagnostics" not in record:
        raise _MandatoryRecordOverflow

    mandatory = dict(record)
    diagnostics = mandatory.pop("diagnostics")
    mandatory_raw = _json_line(mandatory)
    if len(mandatory_raw) > _RECORD_LIMIT_BYTES:
        raise _MandatoryRecordOverflow

    # Preserve the diagnostic shape and as much UTF-8 string content as possible.
    # A uniform per-string bound also handles nested diagnostic payloads without
    # ever truncating required schema fields.
    low = 0
    high = _RECORD_LIMIT_BYTES
    best: bytes | None = None
    while low <= high:
        midpoint = (low + high) // 2
        candidate = dict(mandatory)
        candidate["diagnostics"] = _truncate_strings(diagnostics, midpoint)
        candidate_raw = _json_line(candidate)
        if len(candidate_raw) <= _RECORD_LIMIT_BYTES:
            best = candidate_raw
            low = midpoint + 1
        else:
            high = midpoint - 1

    if best is not None:
        return best

    # Collection structure or diagnostic keys alone may be oversized. In that
    # case the optional payload is discarded rather than weakening the cap.
    marker = dict(mandatory)
    marker["diagnostics"] = {"truncated": True}
    marker_raw = _json_line(marker)
    if len(marker_raw) <= _RECORD_LIMIT_BYTES:
        return marker_raw
    return mandatory_raw


class StartupTrace:
    """Append-only startup trace with monotonic anchors and hard budgets."""

    def __init__(
        self,
        project_dir: Path,
        launch_id: str,
        *,
        enabled: bool,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.path = startup_trace_path(project_dir, launch_id)
        self.enabled = enabled
        self._project_dir = Path(project_dir)
        self._launch_id = launch_id
        self._clock = clock if clock is not None else time.monotonic
        self._lock = threading.RLock()
        self._launch_at: float | None = None
        self._spawn_at: float | None = None
        self._hook_review_at: float | None = None
        self._current_attempt: int | None = None
        self._current_view_id: str | None = None
        self._spawned_attempts: set[int] = set()
        self._terminal_status: str | None = None

    def record_launch_anchor(self, *, diagnostics: Mapping[str, object] | None = None) -> None:
        """Record the post-confirmation launch anchor."""
        with self._lock:
            self._ensure_open()
            recorded_at = self._now()
            self._launch_at = recorded_at
            if not self.enabled:
                return
            self._append_record(self._record("launch", recorded_at, diagnostics=diagnostics))

    def record_attempt_anchor(
        self,
        *,
        attempt: int,
        view_id: str,
        diagnostics: Mapping[str, object] | None = None,
    ) -> None:
        """Record the anchor and bounded diagnostics for one launch attempt."""
        with self._lock:
            self._ensure_open()
            self._current_attempt = attempt
            self._current_view_id = view_id
            if not self.enabled:
                return
            recorded_at = self._now()
            record = self._record("attempt", recorded_at, diagnostics=diagnostics)
            record.update(attempt=attempt, view_id=view_id)
            self._append_record(record)

    def record_stage(
        self,
        stage: str,
        *,
        attempt: int,
        view_id: str,
        diagnostics: Mapping[str, object] | None = None,
    ) -> None:
        """Record a real startup stage anchor."""
        with self._lock:
            self._ensure_open()
            if not self.enabled:
                return
            recorded_at = self._now()
            record = self._record("stage", recorded_at, diagnostics=diagnostics)
            record.update(stage=stage, attempt=attempt, view_id=view_id)
            if stage == "spawn" and attempt in self._spawned_attempts:
                raise RuntimeError(f"startup spawn already recorded for attempt {attempt}")
            self._append_record(record)
            if stage == "spawn":
                self._spawned_attempts.add(attempt)
                if self._spawn_at is None:
                    self._spawn_at = recorded_at
            elif stage == "hook_review":
                if self._hook_review_at is None:
                    self._hook_review_at = recorded_at

    def record_spawn(self) -> None:
        """Record the exact successful-Popen boundary for the current attempt."""
        with self._lock:
            attempt = self._current_attempt
            view_id = self._current_view_id
        if attempt is None or view_id is None:
            raise RuntimeError("startup trace has no current attempt for spawn")
        self.record_stage("spawn", attempt=attempt, view_id=view_id)

    def require_startup_budgets(self) -> None:
        """Fail an enabled launch with missing or exceeded hard startup budgets."""
        with self._lock:
            if not self.enabled:
                return
            durations = {
                "confirmation_to_spawn": self._duration(self._launch_at, self._spawn_at),
                "spawn_to_hook_review": self._duration(
                    self._spawn_at,
                    self._hook_review_at,
                ),
                "total_startup": self._duration(self._launch_at, self._hook_review_at),
            }
            missing = [name for name, duration in durations.items() if duration is None]
            exceeded = [
                name
                for name, budget in _BUDGETS_SECONDS.items()
                if (duration := durations[name]) is not None and duration > budget
            ]
        if missing or exceeded:
            details = []
            if missing:
                details.append(f"unmeasured={','.join(missing)}")
            if exceeded:
                details.append(f"exceeded={','.join(exceeded)}")
            raise RuntimeError("Codex startup budgets failed: " + "; ".join(details))

    def close(
        self,
        *,
        status: str,
        diagnostics: Mapping[str, object] | None = None,
    ) -> None:
        """Append exactly one terminal summary."""
        with self._lock:
            if self._terminal_status is not None:
                if status == self._terminal_status:
                    return
                raise RuntimeError(
                    f"startup trace is already closed with terminal status "
                    f"{self._terminal_status!r}"
                )
            if not self.enabled:
                self._terminal_status = status
                return

            recorded_at = self._now()
            self._append_record(self._summary_record(status, recorded_at, diagnostics=diagnostics))
            self._terminal_status = status

    def _record(
        self,
        record_type: str,
        recorded_at: float,
        *,
        diagnostics: Mapping[str, object] | None = None,
    ) -> dict[str, object]:
        record: dict[str, object] = {
            "schema_version": _SCHEMA_VERSION,
            "record_type": record_type,
            "launch_id": self._launch_id,
            "monotonic_seconds": recorded_at,
        }
        if diagnostics is not None:
            record["diagnostics"] = dict(diagnostics)
        return record

    def _summary_record(
        self,
        status: str,
        recorded_at: float,
        *,
        diagnostics: Mapping[str, object] | None = None,
    ) -> dict[str, object]:
        confirmation_to_spawn = self._duration(self._launch_at, self._spawn_at)
        spawn_to_hook_review = self._duration(self._spawn_at, self._hook_review_at)
        total_startup = self._duration(self._launch_at, self._hook_review_at)
        durations = {
            "confirmation_to_spawn": confirmation_to_spawn,
            "spawn_to_hook_review": spawn_to_hook_review,
            "total_startup": total_startup,
        }
        exceeded: list[str] = []
        missing: list[str] = []
        for name, budget in _BUDGETS_SECONDS.items():
            duration = durations[name]
            if duration is None:
                missing.append(name)
            elif duration > budget:
                exceeded.append(name)
        record = self._record("summary", recorded_at, diagnostics=diagnostics)
        record.update(
            status=status,
            durations_seconds=durations,
            budgets_seconds=dict(_BUDGETS_SECONDS),
            budget_missing=missing,
            budget_exceeded=exceeded,
            budgets_passed=not missing and not exceeded,
        )
        return record

    @staticmethod
    def _duration(start: float | None, end: float | None) -> float | None:
        if start is None or end is None:
            return None
        return end - start

    def _now(self) -> float:
        return float(self._clock())

    def _ensure_open(self) -> None:
        if self._terminal_status is not None:
            raise RuntimeError(
                f"startup trace is closed with terminal status {self._terminal_status!r}"
            )

    def _append_record(self, record: dict[str, object]) -> None:
        try:
            raw = _serialize_bounded(record)
        except _MandatoryRecordOverflow as exc:
            self._close_for_overflow()
            raise RuntimeError("mandatory startup trace record exceeds the 16 KiB limit") from exc
        self._append_bytes(raw)

    def _close_for_overflow(self) -> None:
        if self._terminal_status is not None:
            return
        summary = self._summary_record("trace_record_overflow", self._now())
        try:
            raw = _serialize_bounded(summary)
        except _MandatoryRecordOverflow as exc:  # pragma: no cover - fixed schema
            raise RuntimeError("terminal startup trace summary exceeds 16 KiB") from exc
        self._append_bytes(raw)
        self._terminal_status = "trace_record_overflow"

    def _append_bytes(self, raw: bytes) -> None:
        if len(raw) > _RECORD_LIMIT_BYTES:
            raise AssertionError("bounded trace serializer exceeded its record limit")

        # Re-resolve after directory creation so an existing parent symlink cannot
        # redirect this append outside the canonical project-keyed trace root.
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        current_path = startup_trace_path(self._project_dir, self._launch_id)
        if current_path != self.path:
            raise OSError("canonical startup trace path changed during launch")

        no_follow = getattr(os, "O_NOFOLLOW", None)
        if no_follow is None:  # pragma: no cover - the cook path is POSIX-only
            raise OSError("startup tracing requires O_NOFOLLOW support")
        flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND | no_follow
        flags |= getattr(os, "O_CLOEXEC", 0)
        descriptor = os.open(self.path, flags, 0o600)
        try:
            view = memoryview(raw)
            while view:
                written = os.write(descriptor, view)
                if written == 0:
                    raise OSError("short write while appending startup trace")
                view = view[written:]
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
