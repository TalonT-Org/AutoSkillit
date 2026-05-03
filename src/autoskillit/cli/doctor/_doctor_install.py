"""Installation, entry points, and version drift doctor checks."""

from __future__ import annotations

import json
import shutil
import subprocess
import urllib.parse
from pathlib import Path

from autoskillit.core import Severity, get_logger

from ._doctor_types import DoctorResult

logger = get_logger(__name__)


def _format_upgrade_cmd(info: object) -> str:
    from autoskillit.cli._install_info import upgrade_command

    cmd = upgrade_command(info)  # type: ignore[arg-type]
    return " ".join(cmd) if cmd else "autoskillit update"


def _check_autoskillit_on_path() -> DoctorResult:
    """Check that the autoskillit command is available on PATH."""
    if shutil.which("autoskillit") is None:
        return DoctorResult(
            Severity.WARNING,
            "autoskillit_on_path",
            "'autoskillit' command not found on PATH.",
        )
    return DoctorResult(Severity.OK, "autoskillit_on_path", "autoskillit command found on PATH")


def _check_editable_install_source_exists() -> DoctorResult:
    """Detect editable autoskillit installs whose source directory no longer exists."""
    import importlib.metadata as meta

    check_name = "editable_install_source_exists"
    try:
        dist = meta.Distribution.from_name("autoskillit")
    except meta.PackageNotFoundError:
        return DoctorResult(Severity.OK, check_name, "autoskillit not installed in this env")

    direct_url_text = dist.read_text("direct_url.json")
    if not direct_url_text:
        return DoctorResult(Severity.OK, check_name, "Not an editable install")

    try:
        direct_url = json.loads(direct_url_text)
    except json.JSONDecodeError:
        return DoctorResult(Severity.OK, check_name, "direct_url.json unreadable — skipped")

    is_editable = (
        direct_url.get("dir_info", {}).get("editable") is True
        or direct_url.get("editable") is True
    )
    if not is_editable:
        return DoctorResult(Severity.OK, check_name, "Not an editable install")

    url = direct_url.get("url", "")
    src_path = urllib.parse.urlparse(url).path if url.startswith("file://") else ""
    if not src_path or Path(src_path).exists():
        return DoctorResult(Severity.OK, check_name, "Editable install source directory exists")

    return DoctorResult(
        Severity.ERROR,
        check_name,
        f"autoskillit is installed from a deleted directory: {src_path}. "
        f"Fix: restore the editable source directory or re-run installation.",
    )


def _check_stale_entry_points() -> DoctorResult:
    """Detect autoskillit binaries on PATH outside ~/.local/bin (stale/poisoned installs)."""
    check_name = "stale_entry_points"
    primary = shutil.which("autoskillit")
    if not primary:
        return DoctorResult(Severity.OK, check_name, "autoskillit not found on PATH")

    try:
        result = subprocess.run(
            ["which", "-a", "autoskillit"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        all_paths = [p.strip() for p in result.stdout.splitlines() if p.strip()]
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        all_paths = [primary]

    expected_prefix = Path.home() / ".local"
    stale = [p for p in all_paths if not Path(p).is_relative_to(expected_prefix)]
    if not stale:
        return DoctorResult(Severity.OK, check_name, "No stale autoskillit entry points found")

    stale_list = ", ".join(stale)
    from autoskillit.cli._install_info import detect_install

    _info = detect_install()
    _cmd_str = _format_upgrade_cmd(_info)
    return DoctorResult(
        Severity.WARNING,
        check_name,
        f"Found autoskillit entry point(s) outside ~/.local/bin: {stale_list}. "
        f"These may be stale editable installs. "
        f"Fix: {_cmd_str} && autoskillit install",
    )


def _check_source_version_drift(home: Path | None = None) -> DoctorResult:
    """Network source-drift check.

    Compares the installed commit SHA against the current HEAD of the branch
    the binary was installed from.  Uses a network request to get the latest
    SHA (with disk-cache TTL fallback).
    """
    check_name = "source_version_drift"
    _home = home or Path.home()

    try:
        from autoskillit.cli._install_info import InstallType, detect_install
        from autoskillit.cli.update._update_checks import resolve_reference_sha

        info = detect_install()

        if info.install_type == InstallType.LOCAL_EDITABLE:
            return DoctorResult(
                Severity.OK, check_name, "Local editable install — drift check not applicable"
            )

        if info.install_type in (InstallType.UNKNOWN, InstallType.LOCAL_PATH):
            return DoctorResult(
                Severity.OK,
                check_name,
                "Not a source-tracked install — drift check not applicable",
            )

        ref_sha = resolve_reference_sha(info, _home, network=True)

        if ref_sha is None:
            return DoctorResult(
                Severity.OK,
                check_name,
                "Source drift reference SHA unavailable — check network connectivity",
            )

        if info.commit_id == ref_sha:
            return DoctorResult(Severity.OK, check_name, "No source drift detected")

        installed_short = (info.commit_id or "unknown")[:8]
        ref_short = ref_sha[:8]
        _cmd_str = _format_upgrade_cmd(info)
        return DoctorResult(
            Severity.WARNING,
            check_name,
            f"Source drift: installed={installed_short}, reference={ref_short}. "
            f"Run: {_cmd_str} && autoskillit install",
        )

    except Exception:
        logger.debug("Source drift check failed", exc_info=True)
        return DoctorResult(
            Severity.OK, check_name, "Source drift check skipped (unexpected error)"
        )


def _check_install_classification() -> DoctorResult:
    """Classify the current autoskillit install type via direct_url.json."""
    check_name = "install_classification"
    try:
        from autoskillit.cli._install_info import InstallType, detect_install

        info = detect_install()
        if info.install_type == InstallType.UNKNOWN:
            return DoctorResult(
                Severity.WARNING,
                check_name,
                "install type could not be detected from direct_url.json",
            )
        commit_short = (info.commit_id or "")[:8]
        return DoctorResult(
            Severity.OK,
            check_name,
            f"install_type={info.install_type}, "
            f"requested_revision={info.requested_revision}, "
            f"commit_id={commit_short}",
        )
    except Exception:
        logger.debug("Install classification check failed", exc_info=True)
        return DoctorResult(
            Severity.OK, check_name, "Install classification check skipped (unexpected error)"
        )


def _check_update_dismissal_state(home: Path | None = None) -> DoctorResult:
    """Report the current update-prompt dismissal state."""
    check_name = "update_dismissal_state"
    _home = home or Path.home()
    try:
        from autoskillit.cli._install_info import detect_install, dismissal_window
        from autoskillit.cli.update._update_checks import _read_dismiss_state

        state = _read_dismiss_state(_home)
        entry = state.get("update_prompt")
        if not isinstance(entry, dict) or "dismissed_at" not in entry:
            return DoctorResult(Severity.OK, check_name, "No active dismissal")

        from datetime import datetime

        info = detect_install()
        window = dismissal_window(info)
        dismissed_at = datetime.fromisoformat(str(entry["dismissed_at"]))
        expiry = (dismissed_at + window).strftime("%Y-%m-%d")
        conditions = entry.get("conditions", [])
        return DoctorResult(
            Severity.OK,
            check_name,
            f"update_prompt dismissed until {expiry}; conditions={conditions}",
        )
    except Exception:
        logger.debug("Update dismissal state check failed", exc_info=True)
        return DoctorResult(
            Severity.OK, check_name, "Update dismissal state check skipped (unexpected error)"
        )
