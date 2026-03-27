"""Pre-deletion editable install guard for perform_merge().

Scans system Python site-packages for editable installs (PEP 610 direct_url.json)
whose source URL points into a given worktree path. If any are found, the merge
lifecycle is halted before the worktree directory is deleted.

Fail-open design: any read error, JSON parse error, or subprocess failure causes
that entry to be skipped — the guard never raises.

Zero autoskillit imports — only stdlib.
"""

from __future__ import annotations

import json
import logging
import shutil
import site
import subprocess
from pathlib import Path


def _collect_site_packages_for_interpreter(python: str, worktree_path: Path) -> list[Path]:
    """Return site-packages directories for the given Python interpreter.

    Skips interpreters whose executable path lives inside the worktree (i.e. the
    worktree's own venv) — we only want external / system Python interpreters.
    Returns [] on any subprocess failure.
    """
    try:
        python_real = Path(python).resolve()
    except Exception:
        logging.debug("_editable_guard: failed to resolve python path %s", python)
        return []

    if str(python_real).startswith(str(worktree_path)):
        return []

    try:
        result = subprocess.run(
            [python, "-c", "import json,site; print(json.dumps(site.getsitepackages()))"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            return []
        dirs = json.loads(result.stdout.strip())
        return [Path(d) for d in dirs if isinstance(d, str)]
    except Exception:
        logging.debug("_editable_guard: failed to query site-packages for %s", python)
        return []


def _discover_site_packages(worktree_path: Path) -> list[Path]:
    """Discover all candidate site-packages directories from Python interpreters on PATH.

    Checks python3, python, and python3.8 through python3.15. Also includes the
    current interpreter's user site-packages via site.getusersitepackages().
    Deduplicates results.
    """
    candidate_names = ["python3", "python"] + [f"python3.{x}" for x in range(8, 16)]
    seen: set[Path] = set()
    dirs: list[Path] = []

    for name in candidate_names:
        exe = shutil.which(name)
        if exe is None:
            continue
        for d in _collect_site_packages_for_interpreter(exe, worktree_path):
            if d not in seen:
                seen.add(d)
                dirs.append(d)

    # Also include current interpreter's user site-packages
    try:
        user_site = Path(site.getusersitepackages())
        if user_site not in seen:
            seen.add(user_site)
            dirs.append(user_site)
    except Exception:
        logging.debug("_editable_guard: failed to query user site-packages")

    return dirs


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
    return source_path.startswith(str(worktree_path))


def scan_editable_installs_for_worktree(
    worktree_path: Path,
    site_packages_dirs: list[Path] | None = None,
) -> list[str]:
    """Scan for editable installs whose source URL points into worktree_path.

    Args:
        worktree_path: Absolute path to the worktree being deleted.
        site_packages_dirs: If provided, scan only these directories (test path).
            If None (production path), auto-discover via Python interpreters on PATH.

    Returns:
        List of human-readable descriptions of poisoned installs.
        Empty list means the system is clean.
    """
    if site_packages_dirs is None:
        site_packages_dirs = _discover_site_packages(worktree_path)

    findings: list[str] = []

    for site_dir in site_packages_dirs:
        if not site_dir.is_dir():
            continue
        for direct_url_file in site_dir.glob("*.dist-info/direct_url.json"):
            try:
                data = json.loads(direct_url_file.read_text())
            except Exception:
                logging.debug("_editable_guard: failed to parse %s", direct_url_file)
                continue

            if not isinstance(data, dict):
                continue

            if _is_editable_in_worktree(data, worktree_path):
                dist_info_name = direct_url_file.parent.name
                pkg_name = (
                    dist_info_name.split("-")[0] if "-" in dist_info_name else dist_info_name
                )
                url = data.get("url", "")
                findings.append(f"{pkg_name} editable at {url} ({dist_info_name})")

    return findings
