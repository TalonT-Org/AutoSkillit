"""Detect orphaned registered-stdio AutoSkillit daemons on narrow Linux hosts.

The supported profile assumes the coding-agent client and MCP daemon share a PID
namespace and that no child subreaper intervenes.  A daemon in scope is therefore
reparented to PID 1.  Every ambiguous observation fails closed.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import regex as re

from autoskillit.core import (
    AUTOSKILLIT_STATE_ROOT_ENV_VAR,
    LAUNCH_ID_ENV_VAR,
    get_logger,
    read_boot_id,
    read_registry,
    read_starttime_ticks,
)
from autoskillit.execution.process._process_kill import kill_process_tree

logger = get_logger(__name__)

_LAUNCH_ID_RE = re.compile(r"[0-9a-f]{16}")
_BOOT_ID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


@dataclass(frozen=True, slots=True)
class OrphanedAutoSkillitDaemon:
    """A registered-stdio daemon whose exact logical owner is dead."""

    pid: int
    launch_id: str
    state_root: str
    boot_id: str
    starttime_ticks: int
    owner_pid: int
    owner_boot_id: str
    owner_starttime_ticks: int


@dataclass(frozen=True, slots=True)
class DaemonOrphanReapResult:
    """Outcome of revalidating and optionally terminating one daemon."""

    pid: int
    action: Literal["terminated", "skipped", "incomplete"]
    survivor_pids: tuple[int, ...] = ()
    access_denied_pids: tuple[int, ...] = ()


def _iter_proc_pids() -> list[int]:
    try:
        return sorted(int(entry.name) for entry in Path("/proc").iterdir() if entry.name.isdigit())
    except OSError:
        return []


def _read_cmdline(pid: int) -> tuple[str, ...] | None:
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return None
    parts = tuple(part.decode(errors="surrogateescape") for part in raw.split(b"\0") if part)
    return parts or None


def _is_registered_stdio_command(command: tuple[str, ...]) -> bool:
    """Match only the installed command with no transport/subcommand arguments."""
    return (len(command) == 1 and Path(command[0]).name == "autoskillit") or (
        len(command) == 2
        and Path(command[0]).name.startswith("python")
        and Path(command[1]).name == "autoskillit"
    )


def _read_uid(pid: int) -> int | None:
    try:
        return os.stat(f"/proc/{pid}").st_uid
    except OSError:
        return None


def _read_ppid(pid: int) -> int | None:
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
        rpar = stat.rfind(")")
        if rpar < 0:
            return None
        return int(stat[rpar + 2 :].split()[1])
    except (OSError, ValueError, IndexError):
        return None


def _read_environ(pid: int) -> dict[str, str] | None:
    try:
        raw = Path(f"/proc/{pid}/environ").read_bytes()
    except OSError:
        return None
    result: dict[str, str] = {}
    try:
        for item in raw.split(b"\0"):
            if not item:
                continue
            key, value = item.split(b"=", 1)
            result[key.decode()] = value.decode()
    except (UnicodeDecodeError, ValueError):
        return None
    return result


def _owner_is_dead(owner_pid: int, boot_id: str, starttime_ticks: int) -> bool | None:
    """Return True for affirmative death, False for a live match, None if unknown."""
    if owner_pid <= 0 or _BOOT_ID_RE.fullmatch(boot_id) is None or starttime_ticks <= 0:
        return None
    current_boot_id = read_boot_id()
    if current_boot_id is None or _BOOT_ID_RE.fullmatch(current_boot_id) is None:
        return None
    if current_boot_id != boot_id:
        return True
    current_ticks = read_starttime_ticks(owner_pid)
    if current_ticks is not None:
        return current_ticks != starttime_ticks
    try:
        os.kill(owner_pid, 0)
    except ProcessLookupError:
        return True
    except (PermissionError, OSError):
        return None
    return None


def _candidate_for_pid(pid: int) -> OrphanedAutoSkillitDaemon | None:
    command = _read_cmdline(pid)
    if command is None or not _is_registered_stdio_command(command):
        return None
    if _read_uid(pid) != os.geteuid() or _read_ppid(pid) != 1:
        return None
    environ = _read_environ(pid)
    if environ is None:
        return None
    launch_id = environ.get(LAUNCH_ID_ENV_VAR, "")
    state_root = environ.get(AUTOSKILLIT_STATE_ROOT_ENV_VAR, "")
    if _LAUNCH_ID_RE.fullmatch(launch_id) is None or not state_root:
        return None
    root = Path(state_root)
    if not root.is_absolute():
        return None
    row = read_registry(root).get(launch_id)
    if not isinstance(row, dict):
        return None
    owner_pid = row.get("owner_pid")
    owner_boot_id = row.get("owner_boot_id")
    owner_ticks = row.get("owner_starttime_ticks")
    if (
        not isinstance(owner_pid, int)
        or isinstance(owner_pid, bool)
        or not isinstance(owner_boot_id, str)
        or not isinstance(owner_ticks, int)
        or isinstance(owner_ticks, bool)
    ):
        return None
    if _owner_is_dead(owner_pid, owner_boot_id, owner_ticks) is not True:
        return None
    daemon_boot_id = read_boot_id()
    daemon_ticks = read_starttime_ticks(pid)
    if not daemon_boot_id or _BOOT_ID_RE.fullmatch(daemon_boot_id) is None or not daemon_ticks:
        return None
    return OrphanedAutoSkillitDaemon(
        pid=pid,
        launch_id=launch_id,
        state_root=state_root,
        boot_id=daemon_boot_id,
        starttime_ticks=daemon_ticks,
        owner_pid=owner_pid,
        owner_boot_id=owner_boot_id,
        owner_starttime_ticks=owner_ticks,
    )


def find_orphaned_autoskillit_daemons() -> list[OrphanedAutoSkillitDaemon]:
    """Return only registered-stdio daemons with affirmatively dead owners."""
    if not sys.platform.startswith("linux"):
        return []
    return [candidate for pid in _iter_proc_pids() if (candidate := _candidate_for_pid(pid))]


def reap_orphaned_autoskillit_daemons(
    candidates: Sequence[OrphanedAutoSkillitDaemon],
) -> list[DaemonOrphanReapResult]:
    """Revalidate all predicates and terminate matching daemon incarnations."""
    results: list[DaemonOrphanReapResult] = []
    for candidate in candidates:
        if _candidate_for_pid(candidate.pid) != candidate:
            logger.info("daemon_orphan_reap_skipped", pid=candidate.pid)
            results.append(DaemonOrphanReapResult(candidate.pid, "skipped"))
            continue
        cleanup = kill_process_tree(
            candidate.pid,
            expected_boot_id=candidate.boot_id,
            expected_starttime_ticks=candidate.starttime_ticks,
        )
        if cleanup.identity_refused:
            results.append(DaemonOrphanReapResult(candidate.pid, "skipped"))
        elif cleanup.complete:
            results.append(DaemonOrphanReapResult(candidate.pid, "terminated"))
        else:
            results.append(
                DaemonOrphanReapResult(
                    candidate.pid,
                    "incomplete",
                    survivor_pids=cleanup.survivor_pids,
                    access_denied_pids=cleanup.access_denied_pids,
                )
            )
    return results
