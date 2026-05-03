"""Quota cache schema and claude process state doctor checks."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from autoskillit.core import Severity, get_logger
from autoskillit.execution import QUOTA_CACHE_SCHEMA_VERSION

from ._doctor_types import DoctorResult

logger = get_logger(__name__)


def _check_quota_cache_schema(cache_path: Path | None = None) -> DoctorResult:
    """Check the quota cache file for schema version drift."""
    check_name = "quota_cache_schema"
    path = cache_path or (Path.home() / ".claude" / "autoskillit_quota_cache.json")
    if not path.exists():
        return DoctorResult(Severity.OK, check_name, "No quota cache present.")
    try:
        raw = json.loads(path.read_text())
    except Exception as exc:
        logger.warning("quota_cache_parse_error", path=str(path), exc_info=True)
        return DoctorResult(
            Severity.WARNING,
            check_name,
            f"Quota cache at {path} could not be parsed: {type(exc).__name__}.",
        )
    observed = raw.get("schema_version") if isinstance(raw, dict) else None
    if observed == QUOTA_CACHE_SCHEMA_VERSION:
        return DoctorResult(
            Severity.OK,
            check_name,
            f"Quota cache schema v{QUOTA_CACHE_SCHEMA_VERSION} at {path}.",
        )
    return DoctorResult(
        Severity.WARNING,
        check_name,
        f"Quota cache schema drift at {path}: observed={observed!r}, "
        f"expected={QUOTA_CACHE_SCHEMA_VERSION}.",
    )


def _check_claude_process_state_breakdown() -> DoctorResult:
    """Check current D-state and CPU usage of claude processes via ps."""
    check_name = "claude_process_state"
    try:
        result = subprocess.run(
            ["ps", "-axo", "pid,state,pcpu,comm"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as exc:
        return DoctorResult(
            Severity.OK,
            check_name,
            f"ps unavailable ({type(exc).__name__}); skipping claude process check",
        )

    if result.returncode != 0:
        return DoctorResult(
            Severity.OK,
            check_name,
            f"ps exited {result.returncode}; skipping claude process check",
        )

    claude_rows: list[tuple[int, str, float]] = []
    for line in result.stdout.splitlines()[1:]:
        parts = line.split(maxsplit=3)
        if len(parts) < 4:
            continue
        comm = parts[3]
        if comm != "claude":
            continue
        try:
            claude_rows.append((int(parts[0]), parts[1], float(parts[2])))
        except ValueError:
            continue

    if not claude_rows:
        return DoctorResult(Severity.OK, check_name, "No claude processes running")

    breakdown: dict[str, int] = {}
    for _, state, _ in claude_rows:
        breakdown[state] = breakdown.get(state, 0) + 1

    summary = ", ".join(f"{s}={c}" for s, c in sorted(breakdown.items()))

    d_rows = [f"pid={pid} pcpu={pcpu}" for pid, state, pcpu in claude_rows if state == "D"]
    if d_rows:
        return DoctorResult(
            Severity.WARNING,
            check_name,
            f"claude processes in D state: {', '.join(d_rows)} (breakdown: {summary})",
        )

    return DoctorResult(
        Severity.OK,
        check_name,
        f"claude process state breakdown: {summary}",
    )
