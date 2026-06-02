"""Quota cache schema, claude process state, and codex version doctor checks."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import regex as re

from autoskillit.core import CodingAgentBackend, Severity, get_logger
from autoskillit.execution import QUOTA_CACHE_SCHEMA_VERSION

from ._doctor_types import DoctorResult

logger = get_logger(__name__)

CODEX_MIN_VERSION: tuple[int, ...] = (0, 130, 0)


def _check_codex_version(*, backend: CodingAgentBackend | None = None) -> DoctorResult:
    check_name = "codex_version"
    if backend is None or not backend.capabilities.version_check_command:
        return DoctorResult(Severity.OK, check_name, "Skipped (no version check command)")
    try:
        result = subprocess.run(
            backend.capabilities.version_check_command.split(),
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as exc:
        return DoctorResult(
            Severity.OK,
            check_name,
            f"codex unavailable ({type(exc).__name__}); skipping version check",
        )

    if result.returncode != 0:
        return DoctorResult(
            Severity.OK,
            check_name,
            f"codex exited {result.returncode}; skipping version check",
        )

    for line in (result.stdout + result.stderr).splitlines():
        m = re.search(r"(\d+)\.(\d+)\.(\d+)", line)
        if m:
            parsed = (int(m.group(1)), int(m.group(2)), int(m.group(3)))
            if parsed < CODEX_MIN_VERSION:
                min_str = ".".join(str(v) for v in CODEX_MIN_VERSION)
                cur_str = ".".join(str(v) for v in parsed)
                return DoctorResult(
                    Severity.WARNING,
                    check_name,
                    f"Codex CLI {cur_str} is below minimum {min_str}",
                )
            cur_str = ".".join(str(v) for v in parsed)
            return DoctorResult(Severity.OK, check_name, f"Codex CLI {cur_str}")

    return DoctorResult(
        Severity.OK,
        check_name,
        "codex --version output unparseable; skipping version check",
    )


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


def _check_claude_process_state_breakdown(
    *, backend: CodingAgentBackend | None = None
) -> DoctorResult:
    """Check current D-state and CPU usage of claude/codex processes via ps."""
    process_label = backend.capabilities.process_name if backend else "claude"
    comm_filter = process_label
    check_name = f"{process_label}_process_state"

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
            f"ps unavailable ({type(exc).__name__}); skipping {process_label} process check",
        )

    if result.returncode != 0:
        return DoctorResult(
            Severity.OK,
            check_name,
            f"ps exited {result.returncode}; skipping {process_label} process check",
        )

    rows: list[tuple[int, str, float]] = []
    for line in result.stdout.splitlines()[1:]:
        parts = line.split(maxsplit=3)
        if len(parts) < 4:
            continue
        comm = parts[3]
        if comm != comm_filter:
            continue
        try:
            rows.append((int(parts[0]), parts[1], float(parts[2])))
        except ValueError:
            continue

    if not rows:
        return DoctorResult(Severity.OK, check_name, f"No {process_label} processes running")

    breakdown: dict[str, int] = {}
    for _, state, _ in rows:
        breakdown[state] = breakdown.get(state, 0) + 1

    summary = ", ".join(f"{s}={c}" for s, c in sorted(breakdown.items()))

    d_rows = [f"pid={pid} pcpu={pcpu}" for pid, state, pcpu in rows if state == "D"]
    if d_rows:
        return DoctorResult(
            Severity.WARNING,
            check_name,
            f"{process_label} processes in D state: {', '.join(d_rows)} (breakdown: {summary})",
        )

    return DoctorResult(
        Severity.OK,
        check_name,
        f"{process_label} process state breakdown: {summary}",
    )


def _check_script_binary() -> DoctorResult:
    """Check that script(1) is available and supports -qefc flags."""
    check_name = "script_binary"
    try:
        result = subprocess.run(
            ["script", "-qefc", "true", "/dev/null"],
            capture_output=True,
            timeout=5,
        )
    except FileNotFoundError:
        return DoctorResult(
            Severity.WARNING,
            check_name,
            "script(1) not found — PTY wrapping unavailable; headless sessions may misbehave",
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        return DoctorResult(
            Severity.WARNING,
            check_name,
            f"script(1) probe failed ({type(exc).__name__}) — PTY wrapping may be unavailable",
        )
    if result.returncode != 0:
        return DoctorResult(
            Severity.WARNING,
            check_name,
            "script(1) present but -qefc flags unsupported — PTY wrapping may not work correctly",
        )
    return DoctorResult(Severity.OK, check_name, "script(1) available with -qefc support")
