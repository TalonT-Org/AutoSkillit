"""Pre-deletion editable install guard for perform_merge().

Scans system Python site-packages for editable installs (PEP 610 direct_url.json)
whose source URL points into a given worktree path. If any are found, the merge
lifecycle is halted before the worktree directory is deleted.

Anticipated discovery, read, decode, and metadata failures skip the affected
probe and are returned as unverified reasons. Unanticipated exceptions propagate
to the merge tool boundary so cleanup cannot silently proceed after a scanner bug.
"""

from __future__ import annotations

import dataclasses
import json
import shutil
import site
import subprocess
from pathlib import Path

from autoskillit.core import get_logger

logger = get_logger(__name__)


@dataclasses.dataclass(frozen=True, slots=True)
class EditableScanResult:
    """Editable installs found and probes that could not be completed."""

    findings: tuple[str, ...] = ()
    unverified: tuple[str, ...] = ()


def _collect_site_packages_for_interpreter(
    python: str, worktree_path: Path
) -> tuple[list[Path], list[str]]:
    """Return site-packages directories for the given Python interpreter.

    Skips interpreters whose executable path lives inside the worktree (i.e. the
    worktree's own venv) — we only want external / system Python interpreters.
    Returns an unverified reason for anticipated resolution or probe failures.
    """
    try:
        python_real = Path(python).resolve()
    except (OSError, RuntimeError) as exc:
        # Path.resolve() raises RuntimeError for a symlink loop on Python 3.11.
        reason = f"interpreter path could not be resolved: {python} ({type(exc).__name__}: {exc})"
        logger.debug(
            "editable_guard_interpreter_resolve_failed",
            interpreter=python,
            error=str(exc),
            error_type=type(exc).__name__,
        )
        return [], [reason]

    if python_real.is_relative_to(worktree_path):
        return [], []

    try:
        result = subprocess.run(
            [python, "-c", "import json,site; print(json.dumps(site.getsitepackages()))"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            reason = f"interpreter probe exited non-zero: {python} (exit {result.returncode})"
            logger.debug(
                "editable_guard_interpreter_probe_nonzero",
                interpreter=python,
                returncode=result.returncode,
            )
            return [], [reason]
        dirs = json.loads(result.stdout.strip())
        return [Path(d) for d in dirs if isinstance(d, str)], []
    except (
        OSError,
        subprocess.SubprocessError,
        UnicodeDecodeError,
        json.JSONDecodeError,
    ) as exc:
        reason = f"interpreter probe failed: {python} ({type(exc).__name__}: {exc})"
        logger.debug(
            "editable_guard_interpreter_probe_failed",
            interpreter=python,
            error=str(exc),
            error_type=type(exc).__name__,
        )
        return [], [reason]


def _discover_site_packages(worktree_path: Path) -> tuple[list[Path], list[str]]:
    """Discover all candidate site-packages directories from Python interpreters on PATH.

    Checks python3, python, and python3.8 through python3.15. Also includes the
    current interpreter's user site-packages via site.getusersitepackages().
    Deduplicates results.
    """
    candidate_names = ["python3", "python"] + [f"python3.{x}" for x in range(8, 16)]
    seen: set[Path] = set()
    dirs: list[Path] = []
    unverified: list[str] = []

    for name in candidate_names:
        exe = shutil.which(name)
        if exe is None:
            continue
        interpreter_dirs, interpreter_unverified = _collect_site_packages_for_interpreter(
            exe, worktree_path
        )
        unverified.extend(interpreter_unverified)
        for d in interpreter_dirs:
            if d not in seen:
                seen.add(d)
                dirs.append(d)

    # Also include current interpreter's user site-packages
    try:
        user_site = Path(site.getusersitepackages())
        if user_site not in seen:
            seen.add(user_site)
            dirs.append(user_site)
    except (AttributeError, OSError) as exc:
        # Some virtualenv shims omit site.getusersitepackages entirely.
        reason = f"user site-packages probe failed ({type(exc).__name__}: {exc})"
        logger.debug(
            "editable_guard_user_site_probe_failed",
            error=str(exc),
            error_type=type(exc).__name__,
        )
        unverified.append(reason)

    return dirs, unverified


def _is_editable_in_worktree(direct_url: dict, worktree_path: Path) -> bool:
    """Return True if direct_url.json describes an editable install inside worktree_path.

    Supports both PEP 610 formats:
    - Old: {"url": "file://...", "dir_info": {"editable": true}}
    - New: {"url": "file://...", "editable": true}
    """
    url = direct_url.get("url", "")
    if not isinstance(url, str) or not url.startswith("file://"):
        return False

    # Check editable flag in either format
    dir_info = direct_url.get("dir_info")
    if isinstance(dir_info, dict):
        editable = dir_info.get("editable", False)
    else:
        editable = direct_url.get("editable", False)

    if not editable:
        return False

    # Strip file:// prefix and check if the path is inside the worktree
    source_path = url[len("file://") :]
    return Path(source_path).is_relative_to(worktree_path)


def scan_editable_installs_for_worktree(
    worktree_path: Path,
    site_packages_dirs: list[Path] | None = None,
) -> EditableScanResult:
    """Scan for editable installs whose source URL points into worktree_path.

    Args:
        worktree_path: Absolute path to the worktree being deleted.
        site_packages_dirs: If provided, scan only these directories (test path).
            If None (production path), auto-discover via Python interpreters on PATH.

    Returns:
        Findings and human-readable reasons for probes that could not be completed.
    """
    if site_packages_dirs is None:
        site_packages_dirs, unverified = _discover_site_packages(worktree_path)
    else:
        unverified = []

    findings: list[str] = []

    for site_dir in site_packages_dirs:
        try:
            site_dir_exists = site_dir.is_dir()
        except OSError as exc:
            reason = (
                f"site-packages directory probe failed: {site_dir} ({type(exc).__name__}: {exc})"
            )
            logger.debug(
                "editable_guard_site_directory_probe_failed",
                path=str(site_dir),
                error=str(exc),
                error_type=type(exc).__name__,
            )
            unverified.append(reason)
            continue
        if not site_dir_exists:
            reason = f"site-packages directory vanished: {site_dir}"
            logger.debug("editable_guard_site_directory_missing", path=str(site_dir))
            unverified.append(reason)
            continue
        for direct_url_file in site_dir.glob("*.dist-info/direct_url.json"):
            try:
                data = json.loads(direct_url_file.read_text())
            except OSError as exc:
                condition = "vanished" if isinstance(exc, FileNotFoundError) else "failed"
                reason = (
                    f"metadata read {condition}: {direct_url_file} ({type(exc).__name__}: {exc})"
                )
                logger.debug(
                    "editable_guard_metadata_read_failed",
                    path=str(direct_url_file),
                    error=str(exc),
                    error_type=type(exc).__name__,
                )
                unverified.append(reason)
                continue
            except UnicodeDecodeError as exc:
                reason = (
                    f"could not decode metadata: {direct_url_file} ({type(exc).__name__}: {exc})"
                )
                logger.warning(
                    "editable_guard_metadata_invalid",
                    path=str(direct_url_file),
                    error=str(exc),
                    error_type=type(exc).__name__,
                )
                unverified.append(reason)
                continue
            except json.JSONDecodeError as exc:
                reason = f"malformed metadata: {direct_url_file} ({type(exc).__name__}: {exc})"
                logger.warning(
                    "editable_guard_metadata_invalid",
                    path=str(direct_url_file),
                    error=str(exc),
                    error_type=type(exc).__name__,
                )
                unverified.append(reason)
                continue

            if not isinstance(data, dict):
                reason = f"metadata is not a JSON object: {direct_url_file}"
                logger.warning(
                    "editable_guard_metadata_invalid",
                    path=str(direct_url_file),
                    error="top-level JSON value is not an object",
                    error_type=type(data).__name__,
                )
                unverified.append(reason)
                continue

            if _is_editable_in_worktree(data, worktree_path):
                dist_info_name = direct_url_file.parent.name
                pkg_name = (
                    dist_info_name.split("-")[0] if "-" in dist_info_name else dist_info_name
                )
                url = data.get("url", "")
                findings.append(f"{pkg_name} editable at {url} ({dist_info_name})")

    return EditableScanResult(findings=tuple(findings), unverified=tuple(unverified))
