"""Durable cross-process workspace outcome history."""

from __future__ import annotations

import hashlib
import json
import os
import stat
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from autoskillit.core import (
    ARTIFACT_LEASE_TIMEOUT_SECONDS,
    ArtifactLease,
    CommitFailureClass,
    WorkspaceOutcomeKind,
    WorkspaceOutcomeRecord,
    atomic_write,
)

__all__ = ["DefaultWorkspaceOutcomeLedger", "find_stale_workspace_outcome_shards"]

MAX_RECORD_BYTES = 64 * 1024
MAX_RETAINED_RECORDS = 2048
MAX_SHARD_BYTES = 4 * 1024 * 1024
QUARANTINE_DIRNAME = "quarantine"

_RECORD_KEYS = frozenset(
    {
        "workspace",
        "recorded_at",
        "kind",
        "succeeded",
        "commit_sha",
        "failure_class",
        "timed_out",
        "infrastructure_missing",
    }
)


def _parse_instant(value: str, *, field_name: str) -> datetime:
    # Duplicated from core.types._type_results._require_aware_timestamp to
    # avoid a cross-package submodule import (REQ-IMP-002 forbids
    # ``autoskillit.core.types.*`` from non-core/server/cli modules). Both
    # sites must apply identical rules; the duplicate is the trade-off the
    # layering invariant forces.
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field_name} must be a non-empty ISO-8601 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field_name} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field_name} must include a UTC offset")
    return parsed.astimezone(UTC)


def _canonical_workspace(workspace: str) -> str:
    if not isinstance(workspace, str) or not workspace:
        raise ValueError("workspace must be non-empty")
    # realpath() returns the input unchanged for non-existent paths. Use abspath
    # for normalization (collapses relative segments without filesystem lookups)
    # and only invoke realpath when the path actually resolves.
    if os.path.exists(workspace):
        return os.path.realpath(workspace)
    return os.path.abspath(workspace)


def _record_payload(record: WorkspaceOutcomeRecord) -> dict[str, Any]:
    return {
        "workspace": record.workspace,
        "recorded_at": record.recorded_at,
        "kind": record.kind.value,
        "succeeded": record.succeeded,
        "commit_sha": record.commit_sha,
        "failure_class": (
            record.failure_class.value if record.failure_class is not None else None
        ),
        "timed_out": record.timed_out,
        "infrastructure_missing": record.infrastructure_missing,
    }


def _encode_row(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n"


def _decode_record(payload: object) -> WorkspaceOutcomeRecord:
    if not isinstance(payload, dict) or frozenset(payload) != _RECORD_KEYS:
        raise ValueError("workspace outcome row has an invalid shape")
    failure_class = payload["failure_class"]
    return WorkspaceOutcomeRecord(
        workspace=payload["workspace"],
        recorded_at=payload["recorded_at"],
        kind=WorkspaceOutcomeKind(payload["kind"]),
        succeeded=payload["succeeded"],
        commit_sha=payload["commit_sha"],
        failure_class=(CommitFailureClass(failure_class) if failure_class is not None else None),
        timed_out=payload["timed_out"],
        infrastructure_missing=payload["infrastructure_missing"],
    )


def _read_shard(
    path: Path,
    *,
    canonical_workspace: str,
) -> tuple[str | None, list[WorkspaceOutcomeRecord]] | None:
    """Read one complete shard, returning None for any unavailable state."""
    try:
        path_stat = path.lstat()
    except FileNotFoundError:
        return None, []
    except OSError:
        return None
    if not stat.S_ISREG(path_stat.st_mode) or path_stat.st_size > MAX_SHARD_BYTES:
        return None
    try:
        raw = path.read_bytes()
        if not raw or len(raw) > MAX_SHARD_BYTES or not raw.endswith(b"\n"):
            return None
        lines = raw.decode("utf-8").splitlines()
        header = json.loads(lines[0])
        if not isinstance(header, dict) or set(header) != {"discarded_through"}:
            return None
        discarded_through = header["discarded_through"]
        if discarded_through is not None:
            _parse_instant(discarded_through, field_name="discarded_through")
        records = [_decode_record(json.loads(line)) for line in lines[1:]]
        if any(
            _canonical_workspace(record.workspace) != canonical_workspace for record in records
        ):
            return None
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError):
        return None
    return discarded_through, records


def _render_shard(
    discarded_through: str | None,
    record_lines: list[str],
) -> str:
    return _encode_row({"discarded_through": discarded_through}) + "".join(record_lines)


def _later_timestamp(current: str | None, candidate: str) -> str:
    if current is None:
        return candidate
    if _parse_instant(candidate, field_name="recorded_at") > _parse_instant(
        current,
        field_name="discarded_through",
    ):
        return candidate
    return current


def find_stale_workspace_outcome_shards(root: Path) -> tuple[Path, ...]:
    """Return shards whose recorded machine-local workspaces no longer exist."""
    stale: list[Path] = []
    for shard_path in sorted(Path(root).glob("*.jsonl")):
        try:
            shard_stat = shard_path.lstat()
            if not stat.S_ISREG(shard_stat.st_mode) or shard_stat.st_size > MAX_SHARD_BYTES:
                raise ValueError("invalid workspace outcome shard")
            content = shard_path.read_text(encoding="utf-8")
            if not content.endswith("\n"):
                raise ValueError("incomplete workspace outcome shard")
            lines = content.splitlines()
            header = json.loads(lines[0])
            if not isinstance(header, dict) or set(header) != {"discarded_through"}:
                raise ValueError("invalid workspace outcome header")
            workspaces: set[str] = set()
            for line in lines[1:]:
                payload = json.loads(line)
                if not isinstance(payload, dict) or frozenset(payload) != _RECORD_KEYS:
                    raise ValueError("invalid workspace outcome row")
                workspace = payload["workspace"]
                if not isinstance(workspace, str):
                    raise ValueError("invalid workspace outcome path")
                workspaces.add(workspace)
        except (OSError, UnicodeError, json.JSONDecodeError, IndexError, ValueError):
            stale.append(shard_path)
            continue
        if any(not Path(path).exists() for path in workspaces):
            stale.append(shard_path)
    return tuple(stale)


class DefaultWorkspaceOutcomeLedger:
    """JSONL workspace outcome ledger serialized by per-workspace leases."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root).resolve()

    def _paths(self, workspace: str) -> tuple[str, Path, Path]:
        canonical = _canonical_workspace(workspace)
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        return canonical, self.root / f"{digest}.jsonl", self.root / f"{digest}.lock"

    def _quarantine_corrupt_shard(self, shard_path: Path) -> None:
        """Move an unreadable shard aside so subsequent writes can succeed.

        Without this, every write attempt to the same workspace keeps failing
        with the same RuntimeError until the corrupt file is manually removed.
        The quarantine preserves the bytes for post-mortem inspection.
        """
        quarantine = self.root / QUARANTINE_DIRNAME
        try:
            quarantine.mkdir(parents=True, exist_ok=True)
            digest = hashlib.sha256(str(shard_path).encode("utf-8")).hexdigest()[:16]
            destination = quarantine / f"{shard_path.name}.{digest}.corrupt"
            counter = 0
            while destination.exists():
                counter += 1
                destination = quarantine / f"{shard_path.name}.{digest}.{counter}.corrupt"
            shard_path.rename(destination)
        except OSError:
            # Quarantine failure must not prevent the new write from proceeding;
            # the caller will surface its own RuntimeError on the next attempt.
            return

    def record(self, record: WorkspaceOutcomeRecord) -> None:
        canonical, shard_path, lock_path = self._paths(record.workspace)
        record = replace(record, workspace=canonical)
        record_line = _encode_row(_record_payload(record))
        if len(record_line.encode("utf-8")) > MAX_RECORD_BYTES:
            raise ValueError("workspace outcome record exceeds the 64 KiB limit")

        with ArtifactLease.acquire_exclusive(
            lock_path,
            timeout=ARTIFACT_LEASE_TIMEOUT_SECONDS,
        ):
            if shard_path.exists():
                loaded = _read_shard(shard_path, canonical_workspace=canonical)
                if loaded is None:
                    self._quarantine_corrupt_shard(shard_path)
                    discarded_through = None
                    records: list[WorkspaceOutcomeRecord] = []
                else:
                    discarded_through, records = loaded
            else:
                discarded_through = None
                records = []

            records.append(record)
            record_lines = [_encode_row(_record_payload(item)) for item in records]
            needs_compaction = not shard_path.exists()
            while len(record_lines) > MAX_RETAINED_RECORDS:
                needs_compaction = True
                discarded = records.pop(0)
                record_lines.pop(0)
                discarded_through = _later_timestamp(
                    discarded_through,
                    discarded.recorded_at,
                )

            content = _render_shard(discarded_through, record_lines)
            while len(content.encode("utf-8")) > MAX_SHARD_BYTES:
                needs_compaction = True
                discarded = records.pop(0)
                record_lines.pop(0)
                discarded_through = _later_timestamp(
                    discarded_through,
                    discarded.recorded_at,
                )
                content = _render_shard(discarded_through, record_lines)
            if needs_compaction:
                atomic_write(shard_path, content, strict_durability=True)
            else:
                with shard_path.open("a", encoding="utf-8") as shard:
                    shard.write(record_line)
                    shard.flush()
                    os.fsync(shard.fileno())

    def read(
        self,
        workspace: str,
        *,
        since: str,
        until: str,
    ) -> list[WorkspaceOutcomeRecord]:
        since_instant = _parse_instant(since, field_name="since")
        until_instant = _parse_instant(until, field_name="until")
        if since_instant > until_instant:
            raise ValueError("since must not be later than until")

        canonical, shard_path, lock_path = self._paths(workspace)
        with ArtifactLease.acquire_shared(
            lock_path,
            timeout=ARTIFACT_LEASE_TIMEOUT_SECONDS,
        ):
            if not shard_path.exists():
                return []
            loaded = _read_shard(shard_path, canonical_workspace=canonical)
        if loaded is None:
            raise RuntimeError(f"workspace outcome shard is unreadable: {shard_path}")
        discarded_through, records = loaded
        if discarded_through is not None and since_instant <= _parse_instant(
            discarded_through,
            field_name="discarded_through",
        ):
            raise RuntimeError(
                f"workspace outcome evidence is incomplete before {discarded_through}"
            )
        return [
            record
            for record in records
            if since_instant
            <= _parse_instant(record.recorded_at, field_name="recorded_at")
            <= until_instant
        ]
