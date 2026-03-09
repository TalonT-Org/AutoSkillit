"""Gate policy constants for AutoSkillit MCP tools.

L1 pipeline module. Declares which tools are gated vs. ungated and provides
the canonical error response for a closed gate.

GATED_TOOLS and UNGATED_TOOLS are sourced from autoskillit.core.types (L0)
so that L2 modules (recipe, migration) can also reference the tool registry
without violating layer ordering.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Final, NamedTuple

from autoskillit.core import GATED_TOOLS, UNGATED_TOOLS  # noqa: F401

GATE_FILENAME: Final[str] = ".kitchen_gate"
HOOK_CONFIG_FILENAME: Final[str] = ".autoskillit_hook_config.json"
LEASE_MAX_AGE_HOURS: Final[int] = 24
LEASE_FIELDS: Final[frozenset[str]] = frozenset({"pid", "starttime_ticks", "boot_id", "opened_at"})


class LeaseStatus(NamedTuple):
    """Result of a gate lease validation check."""

    valid: bool
    reason: str
    removed: bool

# Directory components for the gate state directory, relative to project root.
# Hook scripts (stdlib-only) duplicate this as a local constant validated by arch tests.
GATE_DIR_COMPONENTS: Final[tuple[str, ...]] = (".autoskillit", "temp")


def gate_file_path(project_root: Path) -> Path:
    """Return the canonical path to the kitchen gate lease file."""
    return project_root.joinpath(*GATE_DIR_COMPONENTS, GATE_FILENAME)


def hook_config_path(project_root: Path) -> Path:
    """Return the canonical path to the hook configuration JSON file."""
    return project_root.joinpath(*GATE_DIR_COMPONENTS, HOOK_CONFIG_FILENAME)


def is_pid_alive(pid: int) -> bool:
    """Check if a process with the given PID is running."""
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # process exists but we can't signal it


def read_starttime_ticks(pid: int) -> int | None:
    """Read the raw starttime ticks (field 22) from /proc/{pid}/stat.

    Parses after the last ')' to handle comm fields containing spaces or ')'.
    Returns None on any read/parse failure (process dead, WSL2 race, non-Linux).
    """
    try:
        raw = Path(f"/proc/{pid}/stat").read_text()
    except (FileNotFoundError, OSError):
        return None
    try:
        after_comm = raw[raw.rfind(")") + 1 :]
        parts = after_comm.split()
        return int(parts[19])  # field 22 = index 19 after ')'
    except (IndexError, ValueError):
        return None


def read_boot_id() -> str | None:
    """Read /proc/sys/kernel/random/boot_id. Returns None on failure."""
    try:
        return Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    except (FileNotFoundError, OSError):
        return None


def _remove_lease_files(gate_path: Path, companion_path: Path | None) -> None:
    """Remove gate file and optional companion. Silently ignores missing files."""
    try:
        gate_path.unlink(missing_ok=True)
    except OSError:
        pass
    if companion_path is not None:
        try:
            companion_path.unlink(missing_ok=True)
        except OSError:
            pass


def verify_lease(gate_path: Path, companion_path: Path | None = None) -> LeaseStatus:
    """Validate a gate lease file with three-factor identity + TTL.

    Checks: file existence → JSON validity → PID liveness → boot_id match →
    starttime_ticks match → TTL expiry. Auto-removes invalid leases.
    """
    if not gate_path.exists():
        return LeaseStatus(False, "no_file", False)

    try:
        data = json.loads(gate_path.read_text())
        pid = data["pid"]
    except (json.JSONDecodeError, KeyError, TypeError, OSError, ValueError):
        _remove_lease_files(gate_path, companion_path)
        return LeaseStatus(False, "malformed", True)

    if not is_pid_alive(pid):
        _remove_lease_files(gate_path, companion_path)
        return LeaseStatus(False, "dead_pid", True)

    lease_boot_id = data.get("boot_id")
    if lease_boot_id is not None:
        current_boot_id = read_boot_id()
        if current_boot_id is not None and lease_boot_id != current_boot_id:
            _remove_lease_files(gate_path, companion_path)
            return LeaseStatus(False, "boot_id_mismatch", True)

    lease_ticks = data.get("starttime_ticks")
    if lease_ticks is not None:
        current_ticks = read_starttime_ticks(pid)
        if current_ticks is None:
            _remove_lease_files(gate_path, companion_path)
            return LeaseStatus(False, "dead_pid", True)
        if lease_ticks != current_ticks:
            _remove_lease_files(gate_path, companion_path)
            return LeaseStatus(False, "pid_reuse", True)

    opened_at_str = data.get("opened_at")
    if opened_at_str is not None:
        try:
            opened_at = datetime.fromisoformat(opened_at_str)
            if opened_at.tzinfo is None:
                opened_at = opened_at.replace(tzinfo=UTC)
            age_hours = (datetime.now(UTC) - opened_at).total_seconds() / 3600
            if age_hours > LEASE_MAX_AGE_HOURS:
                _remove_lease_files(gate_path, companion_path)
                return LeaseStatus(False, "ttl_expired", True)
        except (ValueError, TypeError):
            pass

    return LeaseStatus(True, "valid", False)


@dataclass(slots=True)
class DefaultGateState:
    """Gate enable/disable state consumed by ToolContext (_context.py)."""

    enabled: bool = False

    def enable(self) -> None:
        """Transition gate to enabled state in-place."""
        self.enabled = True

    def disable(self) -> None:
        """Transition gate to disabled state in-place."""
        self.enabled = False


_DEFAULT_GATE_MESSAGE = (
    "AutoSkillit tools are not enabled. "
    "User must type the open_kitchen prompt to activate. "
    "Check the MCP prompt list for the exact name."
)


def gate_error_result(message: str | None = None) -> str:
    """Return the canonical JSON error string for a closed or blocked gate.

    message: Optional custom error text. When omitted, returns the default
    'tools not enabled' message for gate-closed errors.

    Hardcodes retry_reason as "none" (the StrEnum value of RetryReason.NONE)
    to preserve the L0 zero-internal-imports constraint.
    """
    return json.dumps(
        {
            "success": False,
            "result": message if message is not None else _DEFAULT_GATE_MESSAGE,
            "session_id": "",
            "subtype": "gate_error",
            "is_error": True,
            "exit_code": -1,
            "needs_retry": False,
            "retry_reason": "none",
            "stderr": "",
            "token_usage": None,
        }
    )
