"""Quota cache schema, claude process state, and codex version doctor checks."""

from __future__ import annotations

import json
import shlex
import shutil
import subprocess
import tempfile
from collections import Counter
from datetime import UTC, date, datetime
from pathlib import Path
from typing import NamedTuple

import regex as re

from autoskillit.core import (
    CODEX_MODEL_ALIASES,
    CODEX_MODEL_ALIASES_LAST_VERIFIED,
    ArtifactLease,
    CodingAgentBackend,
    Severity,
    atomic_write,
    default_log_dir,
    get_logger,
    is_valid_codex_model_id,
)
from autoskillit.execution import (
    CODEX_LIMITS_LAST_VERIFIED_VERSION,
    QUOTA_CACHE_SCHEMA_VERSION,
    CodexBackend,
    resolve_log_dir,
    session_index_lock_path,
)

from ._doctor_types import DoctorResult

logger = get_logger(__name__)

_CODEX_ALIAS_STALENESS_DAYS: int = 90


class CodexVersionResult(NamedTuple):
    """Result of parsing the Codex CLI version."""

    parsed: tuple[int, int, int] | None
    skip_reason: str | None


def _parse_codex_version(
    *,
    backend: CodingAgentBackend | None = None,
) -> CodexVersionResult:
    """Parse the installed Codex CLI version."""
    if backend is None or not backend.capabilities.version_check_command:
        return CodexVersionResult(parsed=None, skip_reason="no version check command")
    try:
        result = subprocess.run(
            backend.capabilities.version_check_command.split(),
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as exc:
        return CodexVersionResult(
            parsed=None, skip_reason=f"codex unavailable ({type(exc).__name__})"
        )

    if result.returncode != 0:
        return CodexVersionResult(parsed=None, skip_reason=f"codex exited {result.returncode}")

    for line in (result.stdout + result.stderr).splitlines():
        m = re.search(r"(\d+)\.(\d+)\.(\d+)", line)
        if m:
            return CodexVersionResult(
                parsed=(int(m.group(1)), int(m.group(2)), int(m.group(3))),
                skip_reason=None,
            )

    return CodexVersionResult(parsed=None, skip_reason="codex --version output unparseable")


def _check_backend_version(*, backend: CodingAgentBackend | None = None) -> DoctorResult:
    check_name = "codex_version"
    ver = _parse_codex_version(backend=backend)
    if ver.skip_reason is not None:
        return DoctorResult(Severity.OK, check_name, f"Skipped ({ver.skip_reason})")
    assert ver.parsed is not None
    assert backend is not None
    minimum_match = re.fullmatch(
        r"(\d+)\.(\d+)\.(\d+)",
        backend.capabilities.min_version,
    )
    if minimum_match is None:
        return DoctorResult(
            Severity.OK,
            check_name,
            "Skipped (backend declares no parseable minimum version)",
        )
    minimum = tuple(int(minimum_match.group(index)) for index in range(1, 4))
    display_name = {
        "claude-code": "Claude Code",
        "codex": "Codex CLI",
    }.get(backend.name, backend.name)
    if ver.parsed < minimum:
        min_str = ".".join(str(v) for v in minimum)
        cur_str = ".".join(str(v) for v in ver.parsed)
        return DoctorResult(
            Severity.WARNING,
            check_name,
            f"{display_name} {cur_str} is below minimum {min_str}",
        )
    cur_str = ".".join(str(v) for v in ver.parsed)
    return DoctorResult(Severity.OK, check_name, f"{display_name} {cur_str}")


def _check_codex_limits_verified(*, backend: CodingAgentBackend | None = None) -> DoctorResult:
    check_name = "codex_limits_verified"
    # F3 guard: a Codex CLI limits pin is only ever meaningful for a Codex backend.
    # Comparing it against whatever CLI a differently-configured backend names
    # (e.g. `claude --version`) produces a factually false warning.
    if not isinstance(backend, CodexBackend):
        return DoctorResult(
            Severity.OK, check_name, "Skipped (backend declares no CLI limits pin)"
        )
    ver = _parse_codex_version(backend=backend)
    if ver.skip_reason is not None:
        return DoctorResult(Severity.OK, check_name, f"Skipped ({ver.skip_reason})")
    assert ver.parsed is not None
    cur_str = ".".join(str(v) for v in ver.parsed)
    if ver.parsed > CODEX_LIMITS_LAST_VERIFIED_VERSION:
        pin_str = ".".join(str(v) for v in CODEX_LIMITS_LAST_VERIFIED_VERSION)
        return DoctorResult(
            Severity.WARNING,
            check_name,
            f"Codex CLI {cur_str} is newer than verified pin {pin_str}; re-verify "
            "CODEX_HISTORY_RETENTION_TOKEN_LIMIT, CODEX_AUTO_COMPACT_LIMIT, and "
            "CODEX_RECIPE_DELIVERY_BUDGET against upstream behavior and update "
            "CODEX_LIMIT_VERIFICATION_REGISTRY, from which "
            "CODEX_LIMITS_LAST_VERIFIED_VERSION is derived",
        )
    return DoctorResult(Severity.OK, check_name, f"Codex CLI {cur_str} at or below verified pin")


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
    if backend:
        comm_aliases = backend.capabilities.process_name_aliases or frozenset({process_label})
    else:
        comm_aliases = frozenset({"claude"})
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
        if comm not in comm_aliases:
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


def _check_claude_binary() -> DoctorResult:
    """Check that the claude CLI is available on PATH for capability-driven rerouting."""
    if shutil.which("claude"):
        return DoctorResult(
            Severity.OK,
            "claude_binary",
            "claude CLI found on PATH",
        )
    return DoctorResult(
        Severity.WARNING,
        "claude_binary",
        (
            "claude CLI not found on PATH — skills requiring Claude Code worker "
            "routing (agent_subagent, agent_model, cross_skill_ref, "
            "git_metadata_write) will crash at dispatch time on non-Anthropic backends"
        ),
    )


def _check_codex_graduation(
    *,
    backend: CodingAgentBackend | None = None,
    log_dir: Path | None = None,
) -> DoctorResult:
    check_name = "codex_graduation"

    if backend is None or not backend.capabilities.version_check_command:
        return DoctorResult(Severity.OK, check_name, "Skipped (no codex backend)")

    backend_name = backend.name
    log_root = log_dir or default_log_dir()

    # Criterion 1: version check
    version_result = _check_backend_version(backend=backend)
    version_status = "pass" if version_result.severity == Severity.OK else "fail"

    # Criterion 2: probe-harness cache
    probe_path = log_root / "codex-probe-cache.json"
    probe_status = "not-yet-run"
    try:
        raw = json.loads(probe_path.read_text())
        entries = raw.get("entries", {}) if isinstance(raw, dict) else {}
        if entries:
            probe_status = (
                "pass"
                if any(isinstance(e, dict) and e.get("passed") for e in entries.values())
                else "fail"
            )
    except (OSError, json.JSONDecodeError, ValueError):
        pass

    # Criterion 3: matrix last-run result
    matrix_path = log_root / "codex-matrix-result.json"
    matrix_status = "not-yet-run"
    try:
        matrix_raw = json.loads(matrix_path.read_text())
        if isinstance(matrix_raw, dict) and "passed" in matrix_raw:
            matrix_status = "pass" if matrix_raw["passed"] else "fail"
    except (OSError, json.JSONDecodeError, ValueError):
        pass

    # Criterion 4: sessions.jsonl smoke
    sessions_path = log_root / "sessions.jsonl"
    smoke_status = "not-found"
    try:
        for line in reversed(sessions_path.read_text().splitlines()):
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue
            if entry.get("backend") == backend_name:
                smoke_status = "pass" if entry.get("success") else "fail"
                break
    except OSError:
        pass

    statuses = [version_status, probe_status, matrix_status, smoke_status]
    summary = (
        f"version={version_status} | probe={probe_status}"
        f" | matrix={matrix_status} | smoke={smoke_status}"
    )

    if not all(s == "pass" for s in statuses):
        pending = sum(1 for s in statuses if s != "pass")
        summary += f" — EXPERIMENTAL hold: {pending} of 4 criteria pending"

    return DoctorResult(Severity.INFO, check_name, summary)


def _check_cli_conformance_probes(*, backend: CodingAgentBackend | None = None) -> DoctorResult:
    check_name = "cli_conformance_probes"

    if backend is None or not backend.capabilities.version_check_command:
        return DoctorResult(Severity.OK, check_name, "Skipped (no version check command)")

    cli_argv = shlex.split(backend.capabilities.version_check_command)
    try:
        subprocess.run(cli_argv, capture_output=True, text=True, timeout=5)
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return DoctorResult(Severity.OK, check_name, "CLI unavailable; skipping config probe")

    cli_binary = cli_argv[0]
    result = None
    with tempfile.TemporaryDirectory() as tmpdir:
        probe_path = Path(tmpdir) / "probe.toml"
        atomic_write(probe_path, 'model = "test-model"\n')
        try:
            result = subprocess.run(
                [cli_binary, "-c", str(probe_path), "--version"],
                capture_output=True,
                text=True,
                timeout=10,
            )
        except subprocess.TimeoutExpired:
            return DoctorResult(
                Severity.OK,
                check_name,
                f"{cli_binary} config probe timed out; skipping",
            )
        except (FileNotFoundError, OSError):
            return DoctorResult(Severity.OK, check_name, "CLI unavailable; skipping config probe")

    if result is not None and result.returncode == 0:
        return DoctorResult(Severity.OK, check_name, f"{cli_binary} accepted minimal config probe")
    if result is None:
        return DoctorResult(Severity.OK, check_name, "CLI unavailable; skipping config probe")
    return DoctorResult(
        Severity.WARNING,
        check_name,
        f"{cli_binary} rejected config probe (exit {result.returncode})",
    )


def _check_codex_ndjson_drift(
    *,
    log_dir: str = "",
    backend: CodingAgentBackend | None = None,
) -> DoctorResult:
    check_name = "codex_ndjson_drift"
    if backend is None:
        return DoctorResult(Severity.OK, check_name, "Skipped (no codex backend)")
    backend_name = backend.name
    log_root = Path(log_dir).expanduser() if log_dir else default_log_dir()
    sessions_path = log_root / "sessions.jsonl"
    if not sessions_path.exists():
        return DoctorResult(Severity.OK, check_name, "No sessions.jsonl found")

    affected = 0
    try:
        for line in sessions_path.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue
            if entry.get("backend") != backend_name:
                continue
            if (
                entry.get("ndjson_unknown_event_count", 0) > 0
                or entry.get("ndjson_unknown_item_count", 0) > 0
            ):
                affected += 1
    except OSError:
        return DoctorResult(Severity.OK, check_name, "Could not read sessions.jsonl")

    if affected > 0:
        return DoctorResult(
            Severity.WARNING,
            check_name,
            f"{affected} codex session(s) contain unknown NDJSON event types"
            " — parser vocabulary may be stale",
        )
    return DoctorResult(Severity.OK, check_name, "No NDJSON vocabulary drift detected")


def _read_session_index_projection(
    log_root: Path,
) -> tuple[Counter[str], Counter[str], int]:
    summaries = Counter(
        summary.parent.name for summary in (log_root / "sessions").glob("*/summary.json")
    )
    rows: Counter[str] = Counter()
    malformed = 0
    try:
        lines = (log_root / "sessions.jsonl").read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        lines = []
    for line in lines:
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError:
            malformed += 1
            continue
        dir_name = row.get("dir_name") if isinstance(row, dict) else None
        if isinstance(dir_name, str) and dir_name:
            rows[dir_name] += 1
        else:
            malformed += 1
    return summaries, rows, malformed


def _check_session_index_projection(*, log_dir: str = "") -> DoctorResult:
    check_name = "session_index_projection"
    log_root = resolve_log_dir(log_dir)
    lock_path = session_index_lock_path(log_root)

    try:
        if lock_path.is_file():
            try:
                with ArtifactLease.acquire_existing_shared(lock_path):
                    summaries, rows, malformed = _read_session_index_projection(log_root)
            except FileNotFoundError:
                summaries, rows, malformed = _read_session_index_projection(log_root)
        else:
            summaries, rows, malformed = _read_session_index_projection(log_root)
            if lock_path.is_file():
                try:
                    with ArtifactLease.acquire_existing_shared(lock_path):
                        summaries, rows, malformed = _read_session_index_projection(log_root)
                except FileNotFoundError:
                    pass
    except OSError as exc:
        return DoctorResult(
            Severity.WARNING,
            check_name,
            f"Could not inspect retained session index: {exc}",
        )

    if summaries == rows and malformed == 0:
        return DoctorResult(
            Severity.OK,
            check_name,
            f"{sum(summaries.values())} committed session(s) exactly match the retained index",
        )

    missing = summaries - rows
    duplicate_names = {name for name, count in rows.items() if count > 1}
    dangling = set(rows) - set(summaries)
    details = [
        f"missing={sum(missing.values())}",
        f"duplicate={len(duplicate_names)}",
        f"dangling={len(dangling)}",
        f"malformed={malformed}",
    ]
    return DoctorResult(
        Severity.WARNING,
        check_name,
        "Committed summary/index projection mismatch (" + ", ".join(details) + ")",
    )


def _check_orphaned_codex_processes() -> list[DoctorResult]:
    """Check for orphaned interactive codex TUI processes (fd 0 → deleted pty)."""
    from autoskillit.execution import find_orphaned_codex_processes

    check_name = "orphaned_codex_processes"
    try:
        orphans = find_orphaned_codex_processes()
    except Exception as exc:
        return [
            DoctorResult(
                Severity.WARNING,
                check_name,
                f"scan failed: {type(exc).__name__}: {exc}",
            )
        ]

    if not orphans:
        return [DoctorResult(Severity.OK, check_name, "no orphaned codex processes")]

    return [
        DoctorResult(
            Severity.WARNING,
            check_name,
            f"orphaned codex TUI pid={o.pid}"
            f" started={datetime.fromtimestamp(o.started_at, tz=UTC).isoformat()}"
            f" fd0={o.fd0_target}"
            f" — spinning after pty loss;"
            f" run: autoskillit codex-orphans --reap",
        )
        for o in orphans
    ]


def _check_codex_model_alias_staleness() -> DoctorResult:
    check_name = "codex_model_alias_staleness"
    try:
        verified = date.fromisoformat(CODEX_MODEL_ALIASES_LAST_VERIFIED)
    except (ValueError, TypeError):
        return DoctorResult(
            Severity.WARNING,
            check_name,
            f"Cannot parse CODEX_MODEL_ALIASES_LAST_VERIFIED="
            f"{CODEX_MODEL_ALIASES_LAST_VERIFIED!r}",
        )
    age_days = (date.today() - verified).days
    if age_days > _CODEX_ALIAS_STALENESS_DAYS:
        return DoctorResult(
            Severity.WARNING,
            check_name,
            f"CODEX_MODEL_ALIASES last verified {age_days} days ago"
            f" (threshold {_CODEX_ALIAS_STALENESS_DAYS}d);"
            f" re-verify alias targets and update CODEX_MODEL_ALIASES_LAST_VERIFIED",
        )
    invalid = {k: v for k, v in CODEX_MODEL_ALIASES.items() if not is_valid_codex_model_id(v)}
    if invalid:
        pairs = ", ".join(f"{k}={v!r}" for k, v in invalid.items())
        return DoctorResult(
            Severity.WARNING,
            check_name,
            f"CODEX_MODEL_ALIASES contains unrecognized model IDs: {pairs};"
            f" update CODEX_VALID_MODEL_IDS or fix the alias",
        )
    return DoctorResult(
        Severity.OK,
        check_name,
        f"CODEX_MODEL_ALIASES verified {age_days}d ago; all alias values valid",
    )
