"""Git remote manipulation: URL probing, origin isolation, upstream tracking."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from autoskillit.core import get_logger
from autoskillit.workspace._clone_detect import _is_not_file_url

logger = get_logger(__name__)


def _add_or_set_upstream(clone_path: Path, url: str) -> None:
    """Add or update the upstream remote in the clone.

    Handles the case where upstream already exists (clone_local copies .git as-is
    and the source may already have an upstream remote).
    """
    result = subprocess.run(
        ["git", "remote", "add", "upstream", url],
        cwd=str(clone_path),
        capture_output=True,
    )
    if result.returncode != 0:
        stderr = (
            result.stderr.decode(errors="replace")
            if isinstance(result.stderr, bytes)
            else result.stderr
        )
        if "already exists" not in stderr:
            raise RuntimeError(
                f"git remote add upstream failed: {stderr.strip()}"
                f"\nclone_path={clone_path}, url={url}"
            )
        # upstream already exists (e.g. clone_local copied it from source); update the URL
        set_url = subprocess.run(
            ["git", "remote", "set-url", "upstream", url],
            cwd=str(clone_path),
            capture_output=True,
        )
        if set_url.returncode != 0:
            set_stderr = (
                set_url.stderr.decode(errors="replace")
                if isinstance(set_url.stderr, bytes)
                else set_url.stderr
            )
            raise RuntimeError(
                f"git remote set-url upstream failed: {set_stderr.strip()}"
                f"\nclone_path={clone_path}, url={url}"
            )


def _ensure_origin_isolated(clone_path: Path, known_url: str) -> None:
    """Rewrite origin to a self-referential file:// URL unconditionally.

    Claude Code reads 'origin' to resolve the project root; a file:// URL
    cannot match any registered GitHub project, so the clone is treated as
    a fresh project rooted at clone_path — not aliased to the source repo.

    This must fire for every successful clone regardless of URL availability.
    The conditional guard that previously skipped this for clone_local and
    empty-URL cases was the #377 compounding regression.

    After setting origin, calls _add_or_set_upstream with known_url only
    if known_url is truthy — the real remote URL is stored in 'upstream'
    when available.
    """
    file_url = f"file://{clone_path}"
    add_origin = subprocess.run(
        ["git", "remote", "add", "origin", file_url],
        cwd=str(clone_path),
        capture_output=True,
    )
    if add_origin.returncode != 0:
        add_stderr = (
            add_origin.stderr.decode(errors="replace")
            if isinstance(add_origin.stderr, bytes)
            else add_origin.stderr
        )
        if "already exists" not in add_stderr:
            raise RuntimeError(
                f"git remote add origin failed: {add_stderr.strip()}\nclone_path={clone_path}"
            )
        # origin already exists (cloned from remote); rewrite it
        set_origin = subprocess.run(
            ["git", "remote", "set-url", "origin", file_url],
            cwd=str(clone_path),
            capture_output=True,
        )
        if set_origin.returncode != 0:
            set_origin_stderr = (
                set_origin.stderr.decode(errors="replace")
                if isinstance(set_origin.stderr, bytes)
                else set_origin.stderr
            )
            raise RuntimeError(
                f"git remote set-url origin failed: {set_origin_stderr.strip()}"
                f"\nclone_path={clone_path}"
            )
    if known_url:
        _add_or_set_upstream(clone_path, known_url)


@dataclass(frozen=True)
class CloneSourceResolution:
    """Result of probing a source directory for its clone-source remote URL.

    Tries 'upstream' first (real remote when source_dir is a previous autoskillit
    clone), then 'origin'. Returns the first non-file:// URL found.

    reason:
        "ok"        — a usable remote URL was found and returned
        "no_origin" — no origin remote is configured in this repo
        "timeout"   — subprocess.run timed out (30 s)
        "error"     — subprocess returned non-zero exit code
    url: the remote URL (empty string unless reason == "ok")
    stderr: subprocess stderr (empty unless reason == "error")
    """

    reason: Literal["ok", "no_origin", "timeout", "error"]
    url: str
    stderr: str


def _probe_single_remote(source: Path, remote_name: str) -> CloneSourceResolution:
    """Run ``git remote get-url <remote_name>`` in source and return a typed result.

    Shared by _probe_clone_source_url for each candidate remote name.
    """
    _no_remote_markers = ("no such remote",)
    try:
        result = subprocess.run(
            ["git", "remote", "get-url", remote_name],
            cwd=str(source),
            capture_output=True,
            text=True,
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        return CloneSourceResolution(reason="timeout", url="", stderr="")
    except OSError as exc:
        return CloneSourceResolution(reason="error", url="", stderr=str(exc))

    if result.returncode != 0:
        stderr = result.stderr.strip()
        if any(m in stderr.lower() for m in _no_remote_markers):
            return CloneSourceResolution(reason="no_origin", url="", stderr=stderr)
        return CloneSourceResolution(reason="error", url="", stderr=stderr)

    url = result.stdout.strip()
    if not url:
        return CloneSourceResolution(reason="no_origin", url="", stderr="")
    return CloneSourceResolution(reason="ok", url=url, stderr="")


def _probe_clone_source_url(source: Path) -> CloneSourceResolution:
    """Probe source for the best remote URL to use as the git clone source.

    Avoids cloning from stale file:// local state:
    1. Try 'upstream' — when source_dir is a previous autoskillit clone,
       _ensure_origin_isolated sets upstream to the real remote and
       origin to a self-referential file:// URL. Using upstream avoids cloning
       from the stale local filesystem (fix for #817).
    2. Try 'origin' — if upstream is absent or is itself a file:// URL, falls
       back to origin, which is the real remote in a fresh dev checkout.
    3. If neither yields a non-file:// URL, returns origin's result so callers
       get the appropriate error (no_origin / error / local path as-is).

    file:// URLs are explicitly excluded because _ensure_origin_isolated writes
    them to point at the clone directory itself — a stale local reference.
    Local bare paths (no scheme) and real network URLs are both accepted.
    """
    last_result: CloneSourceResolution | None = None
    for remote_name in ("upstream", "origin"):
        result = _probe_single_remote(source, remote_name)
        last_result = result
        if result.reason == "ok" and _is_not_file_url(result.url):
            return result

    # No non-file:// URL found — return the last probed result (origin) rather
    # than re-invoking _probe_single_remote, which would be a redundant subprocess.
    if last_result is not None:
        return last_result
    return _probe_single_remote(source, "origin")
