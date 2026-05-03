"""Unified startup update check for autoskillit CLI.

Consolidates version-staleness, hook-drift, and source-drift signals into a
single dismissable prompt per CLI invocation.

Branch-aware dismissal windows:
- stable/main/release-tag/UNKNOWN: timedelta(days=7)
- develop/LOCAL_EDITABLE: timedelta(hours=12)

Dismissal expires on two axes: time window elapsed, or version advanced past
the dismissed_version recorded at dismiss time.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

from autoskillit.cli._hooks import _claude_settings_path
from autoskillit.cli._install_info import (
    InstallInfo,
    InstallType,
    comparison_branch,
    detect_install,
    dismissal_window,
    upgrade_command,
)
from autoskillit.cli._restart import perform_restart
from autoskillit.cli.ui._terminal import terminal_guard
from autoskillit.cli.update._update_checks_fetch import (
    _fetch_latest_version,
    invalidate_fetch_cache,
)
from autoskillit.cli.update._update_checks_source import (
    resolve_reference_sha,
)
from autoskillit.core import atomic_write, get_logger
from autoskillit.hook_registry import _count_hook_registry_drift

logger = get_logger(__name__)

_DISMISS_FILE = "update_check.json"

KITCHEN_GUARDED_COMMANDS: frozenset[str] = frozenset({"update", "install", "init"})


@dataclass(frozen=True)
class Signal:
    """A single firing update condition."""

    kind: Literal["binary", "hooks", "source_drift", "dual_mcp"]
    message: str


def _read_dismiss_state(home: Path) -> dict[str, object]:
    """Read dismissal state from ~/.autoskillit/update_check.json.

    Returns empty dict on any error (missing file, malformed JSON, etc.).
    """
    state_file = home / ".autoskillit" / _DISMISS_FILE
    try:
        data = json.loads(state_file.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        logger.debug("Failed to read dismiss state from %s", state_file, exc_info=True)
        return {}


def _write_dismiss_state(home: Path, state: dict[str, object]) -> None:
    """Write dismissal state atomically to ~/.autoskillit/update_check.json."""
    state_file = home / ".autoskillit" / _DISMISS_FILE
    state_file.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(state_file, json.dumps(state))


def _verify_update_result(
    info: InstallInfo,
    current: str,
    latest: str,
    home: Path,
    state: dict[str, object],
) -> bool:
    """Verify that the update subprocess advanced the installed version.

    Calls importlib.metadata.version() directly to bypass the lru_cache in
    version_info() and the process-lifetime cache in autoskillit.__version__.

    Returns True if the version advanced (update succeeded).
    Returns False if the version is unchanged (update silently failed).
    """
    import importlib.metadata

    try:
        new_version = importlib.metadata.version("autoskillit")
    except Exception:
        logger.debug("Failed to read version after update attempt", exc_info=True)
        new_version = current

    if new_version != current:
        return True

    from autoskillit.cli._install_info import upgrade_command

    cmd = upgrade_command(info)
    cmd_str = " ".join(cmd) if cmd else "autoskillit update"
    print(
        f"\nUpdate attempted but version is still {current}. "
        f"To upgrade, run:\n"
        f"  autoskillit update\n"
        f"Or manually:\n"
        f"  {cmd_str}\n"
        f"  autoskillit install",
        flush=True,
    )
    return False


def _binary_signal(info: InstallInfo, home: Path, current: str) -> Signal | None:
    """Return a Signal if a newer binary release is available, else None."""
    target = comparison_branch(info)
    if target is None:
        return None
    latest = _fetch_latest_version(target, home)
    if latest is None:
        return None
    try:
        from packaging.version import Version

        if Version(latest) > Version(current):
            return Signal("binary", f"New release: {latest} (you have {current})")
    except Exception:
        logger.debug("binary signal version comparison failed", exc_info=True)
    return None


def _hooks_signal(settings_path: Path) -> Signal | None:
    """Return a Signal if hook registry drift is detected, else None."""
    try:
        drift = _count_hook_registry_drift(settings_path)
        if drift.orphaned > 0:
            return Signal(
                "hooks",
                f"\u26a0\ufe0f  {drift.orphaned} orphaned hook entry(ies) in settings.json "
                f"will block tool calls with ENOENT — run 'autoskillit install' to fix",
            )
        if drift.missing > 0:
            return Signal(
                "hooks",
                f"{drift.missing} new/changed hook(s) detected since last install",
            )
    except Exception:
        logger.debug("hooks signal check failed", exc_info=True)
    return None


def _source_drift_signal(info: InstallInfo, home: Path) -> Signal | None:
    """Return a Signal if the installed commit lags the branch HEAD, else None."""
    try:
        ref_sha = resolve_reference_sha(info, home, network=True)
        if ref_sha is None:
            return None
        if ref_sha == info.commit_id:
            return None
        rev = info.requested_revision or str(info.install_type)
        installed_short = (info.commit_id or "unknown")[:8]
        ref_short = ref_sha[:8]
        return Signal(
            "source_drift",
            f"A newer version is available on the {rev} branch ({installed_short}..{ref_short})",
        )
    except Exception:
        logger.debug("source drift signal check failed", exc_info=True)
    return None


def _is_dual_mcp_registered(home: Path) -> bool:
    """Return True if both direct mcpServers entry and marketplace plugin are active."""
    from autoskillit.cli._init_helpers import _check_dual_mcp_files

    return _check_dual_mcp_files(
        home / ".claude.json",
        home / ".claude" / "plugins" / "installed_plugins.json",
    )


def _dual_mcp_signal(home: Path | None = None) -> Signal | None:
    """Return a Signal if both direct mcpServers entry and marketplace plugin are registered.

    _is_dual_mcp_registered() delegates to _check_dual_mcp_files() which is
    fail-open (catches OSError and json.JSONDecodeError internally, never raises).
    """
    if _is_dual_mcp_registered(home or Path.home()):
        return Signal(
            "dual_mcp",
            "autoskillit is registered as both a direct MCP server (~/.claude.json) "
            "and a marketplace plugin — two server processes will spawn per session. "
            "Run `autoskillit install` to remove the stale direct entry.",
        )
    return None


def _is_dismissed(
    state: dict[str, object],
    *,
    window: timedelta,
    current_version: str,
    condition: str,
) -> bool:
    """Return True iff the ``update_prompt`` entry dismisses ``condition``.

    Dismissal is active when ALL of:

    1. ``dismissed_at`` is within the branch-aware ``window`` (time-based,
       never SHA-keyed — a new upstream commit does NOT break the window).
       Window values: 7 days for stable/main/release-tag/UNKNOWN installs;
       12 hours for develop/LOCAL_EDITABLE installs.
    2. ``current_version <= dismissed_version`` (version-delta expiry: when the
       running version advances past what was dismissed, the dismissal expires
       uniformly for all three conditions).
    3. ``condition in entry["conditions"]`` — a user who dismissed only
       ``"binary"`` still sees a fresh ``"hooks"`` prompt when it newly fires.

    The two expiry axes apply uniformly to all three condition kinds
    (``"binary"``, ``"hooks"``, ``"source_drift"``).
    """
    try:
        entry = state.get("update_prompt")
        if not isinstance(entry, dict):
            return False
        raw_dismissed = entry.get("dismissed_at")
        if raw_dismissed is None:
            return False
        dismissed_at = datetime.fromisoformat(raw_dismissed)
        if datetime.now(UTC) - dismissed_at >= window:
            return False
        from packaging.version import Version

        if Version(current_version) > Version(str(entry.get("dismissed_version", "0.0.0"))):
            return False
        conditions = entry.get("conditions", [])
        if not isinstance(conditions, list):
            return False
        return condition in conditions
    except Exception:
        logger.debug("Failed to parse dismiss state for condition=%s", condition, exc_info=True)
        return False


def _run_update_sequence(
    info: InstallInfo,
    current: str,
    home: Path,
    state: dict[str, object],
    skip_env: dict[str, str],
) -> None:
    """Run the upgrade command, then autoskillit install, then verify."""
    cmd = upgrade_command(info)
    if cmd is None:
        return
    target_branch = comparison_branch(info)
    latest: str = (
        _fetch_latest_version(target_branch, home) or current
        if target_branch is not None
        else current
    )
    install_result: subprocess.CompletedProcess[bytes] = subprocess.CompletedProcess(
        args=["autoskillit", "install"], returncode=0
    )
    with terminal_guard():
        subprocess.run(cmd, check=False, env=skip_env)
        install_result = subprocess.run(["autoskillit", "install"], check=False, env=skip_env)
    if install_result.returncode != 0:
        print(
            "\nautoskillit install exited with an error. "
            "Hooks and plugin cache may be stale. "
            "Run 'autoskillit install' manually to fix.",
            flush=True,
        )
    succeeded = _verify_update_result(info, current, latest, home, state)
    if succeeded:
        state.pop("update_prompt", None)
        state.pop("binary_snoozed", None)
        _write_dismiss_state(home, state)
        invalidate_fetch_cache(home)
        perform_restart()


def run_update_checks(home: Path | None = None, *, command: str = "") -> None:
    """Run the unified update check on interactive CLI invocations.

    At most one ``[Y/n]`` prompt is shown per invocation.  Guards:

    - ``CLAUDECODE=1`` — headless/MCP session
    - ``CI=1`` — generic CI environment
    - ``AUTOSKILLIT_SKIP_STALE_CHECK=1`` — explicit bypass
    - ``AUTOSKILLIT_SKIP_UPDATE_CHECK=1`` — explicit bypass (preferred name)
    - ``AUTOSKILLIT_SKIP_SOURCE_DRIFT_CHECK=1`` — deprecated alias for the above
    - Non-TTY stdin or stdout

    Install types ``LOCAL_EDITABLE``, ``LOCAL_PATH``, and ``UNKNOWN`` are
    silently skipped after classification.
    """
    if (
        os.environ.get("CLAUDECODE")
        or os.environ.get("CI")
        or os.environ.get("AUTOSKILLIT_SKIP_STALE_CHECK")
        or os.environ.get("AUTOSKILLIT_SKIP_UPDATE_CHECK")
        or os.environ.get("AUTOSKILLIT_SKIP_SOURCE_DRIFT_CHECK")
        or not sys.stdin.isatty()
        or not sys.stdout.isatty()
    ):
        return

    if command in KITCHEN_GUARDED_COMMANDS:
        from autoskillit.core import any_kitchen_open  # noqa: PLC0415

        if any_kitchen_open(project_path=str(Path.cwd())):
            print(
                "Skipping update check: a kitchen is open for this project. "
                "Run 'autoskillit update' manually after the pipeline finishes.",
            )
            return

    _skip_env: dict[str, str] = {
        **os.environ,
        "AUTOSKILLIT_SKIP_STALE_CHECK": "1",
        "AUTOSKILLIT_SKIP_UPDATE_CHECK": "1",
        "AUTOSKILLIT_SKIP_SOURCE_DRIFT_CHECK": "1",
    }

    info = detect_install()
    if info.install_type in (
        InstallType.UNKNOWN,
        InstallType.LOCAL_PATH,
        InstallType.LOCAL_EDITABLE,
    ) and not os.environ.get("AUTOSKILLIT_FORCE_UPDATE_CHECK"):
        return

    import autoskillit as _pkg

    current: str = getattr(_pkg, "__version__", "0.0.0")
    _home = home or Path.home()
    window = dismissal_window(info)
    state = _read_dismiss_state(_home)

    # Gather signals (network I/O happens here; status_line deferred until we know output exists)
    raw_signals: list[Signal | None] = [
        _binary_signal(info, _home, current),
        _hooks_signal(_claude_settings_path("user")),
        _source_drift_signal(info, _home),
        _dual_mcp_signal(_home),
    ]

    all_fired: list[Signal] = [s for s in raw_signals if s is not None]

    # Zero signals: return silently — no "Checking for updates..." printed (REQ-UX-006)
    if not all_fired:
        return

    # Partition into dismissed vs. non-dismissed (REQ-FLOW-001)
    undismissed: list[Signal] = [
        s
        for s in all_fired
        if not _is_dismissed(state, window=window, current_version=current, condition=s.kind)
    ]
    dismissed: list[Signal] = [
        s
        for s in all_fired
        if _is_dismissed(state, window=window, current_version=current, condition=s.kind)
    ]

    if undismissed:
        # Consolidated interactive prompt — behavior unchanged (REQ-FLOW-002)
        from autoskillit.cli.ui._timed_input import status_line, timed_prompt

        status_line("Checking for updates...")

        _TIMEOUT_SENTINEL = "__timeout__"
        bullet_lines = "\n".join(f"  - {s.message}" for s in undismissed)
        prompt_text = f"\nAutoSkillit has updates available:\n{bullet_lines}\nUpdate now? [Y/n]"
        answer = timed_prompt(
            prompt_text, default=_TIMEOUT_SENTINEL, timeout=30, label="update check"
        )

        # On timeout: proceed to app() without writing a dismissal record so the
        # prompt reappears on the next invocation.
        if answer == _TIMEOUT_SENTINEL:
            return

        if answer.lower() in ("", "y", "yes"):
            _run_update_sequence(info, current, _home, state, _skip_env)
            return

        # N path — write unified dismissal record
        state["update_prompt"] = {
            "dismissed_at": datetime.now(UTC).isoformat(),
            "dismissed_version": current,
            "conditions": [s.kind for s in undismissed],
        }
        _write_dismiss_state(_home, state)
        expiry = (datetime.now(UTC) + window).strftime("%Y-%m-%d")
        print(
            f"Dismissed until {expiry}. Run 'autoskillit update' to update sooner, "
            f"or set AUTOSKILLIT_SKIP_STALE_CHECK=1 to silence.",
            flush=True,
        )
    else:
        # All signals are dismissed — passive one-liner (REQ-UX-002 through REQ-UX-005)
        entry = state.get("update_prompt")
        try:
            if isinstance(entry, dict):
                dismissed_at_raw = entry.get("dismissed_at", "")
                dismissed_at = datetime.fromisoformat(str(dismissed_at_raw))
            else:
                raise ValueError("no entry")
            expiry = (dismissed_at + window).strftime("%Y-%m-%d")
        except Exception:
            logger.warning("Failed to parse dismissed_at for expiry calculation", exc_info=True)
            expiry = (datetime.now(UTC) + window).strftime("%Y-%m-%d")
        messages = "; ".join(s.message for s in dismissed)
        print(
            f"{messages}. Auto-prompt silenced until {expiry}. "
            f"Run 'autoskillit update' to upgrade.",
            flush=True,
        )
