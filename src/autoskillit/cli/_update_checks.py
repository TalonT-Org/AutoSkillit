"""Unified startup update check for autoskillit CLI.

Consolidates version-staleness, hook-drift, and source-drift signals into a
single dismissable prompt per CLI invocation.

Branch-aware dismissal windows:

- ``stable`` / ``main`` / release-tag / ``UNKNOWN`` installs: ``timedelta(days=7)``
- ``integration`` / ``LOCAL_EDITABLE`` installs: ``timedelta(hours=12)``

Dismissal expires on two axes (see ``_is_dismissed``):
1. Time window elapsed (branch-aware, not SHA-keyed).
2. Version advanced past the ``dismissed_version`` recorded at dismiss time.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

import httpx

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
from autoskillit.cli._terminal import terminal_guard
from autoskillit.core import AUTOSKILLIT_INSTALLED_VERSION, atomic_write, get_logger
from autoskillit.hook_registry import _count_hook_registry_drift

logger = get_logger(__name__)

_DISMISS_FILE = "update_check.json"
_STABLE_DISMISS_WINDOW = timedelta(days=7)
_DEV_DISMISS_WINDOW = timedelta(hours=12)

# Disposable on-disk cache for GitHub API GETs.  This is intentionally a plain
# JSON dict (not ``write_versioned_json``) — it is a transient performance
# cache, not a persisted schema artifact, so a stray pre-existing file at the
# same path is safely ignored on a JSON parse failure.
_FETCH_CACHE_FILE = "github_fetch_cache.json"
_DEFAULT_FETCH_TTL_SECONDS = 30 * 60

# connect=2s buffers for cold DNS (httpx's connect timeout does NOT cover the
# OS resolver itself); read=1s is tight because 304 responses are tiny.  The
# 30-min disk TTL above is the **only reliable quota defense** — GitHub's
# x-ratelimit-used counter has been observed to increment on 304 responses
# despite their docs claiming otherwise, so ETag/If-None-Match is a
# bandwidth/latency optimization, not a rate-limit optimization.
_HTTP_TIMEOUT = httpx.Timeout(connect=2.0, read=1.0, write=5.0, pool=1.0)
_GITHUB_API_VERSION = "2022-11-28"

_GITHUB_RELEASES_URL = "https://api.github.com/repos/TalonT-Org/AutoSkillit/releases/latest"
_GITHUB_INTEGRATION_PYPROJECT_URL = (
    "https://api.github.com/repos/TalonT-Org/AutoSkillit/contents/pyproject.toml?ref=integration"
)


@dataclass(frozen=True)
class Signal:
    """A single firing update condition."""

    kind: Literal["binary", "hooks", "source_drift", "dual_mcp"]
    message: str


# ---------------------------------------------------------------------------
# Dismiss state I/O (ported from _stale_check.py)
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Fetch cache (ported from _stale_check.py)
# ---------------------------------------------------------------------------


def _fetch_cache_path(home: Path) -> Path:
    return home / ".autoskillit" / _FETCH_CACHE_FILE


def _read_fetch_cache(home: Path) -> dict[str, Any]:
    """Read the GitHub fetch cache; tolerate any read/parse failure."""
    try:
        data = json.loads(_fetch_cache_path(home).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        logger.debug("Failed to read fetch cache", exc_info=True)
        return {}


def _write_fetch_cache(home: Path, data: dict[str, Any]) -> None:
    """Write the GitHub fetch cache atomically."""
    target = _fetch_cache_path(home)
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        atomic_write(target, json.dumps(data))
    except Exception:
        logger.debug("Failed to write fetch cache", exc_info=False)


def invalidate_fetch_cache(home: Path) -> None:
    """Delete the GitHub fetch cache. Call after install/update."""
    try:
        _fetch_cache_path(home).unlink(missing_ok=True)
    except OSError:
        logger.debug("Failed to invalidate fetch cache", exc_info=True)


def _scrub_auth(text: str) -> str:
    """Remove any GITHUB_TOKEN value or ``Bearer …`` token from a string."""
    if not text:
        return text
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        text = text.replace(token, "***")
    import re

    text = re.sub(r"(?i)Bearer\s+\S+", "Bearer ***", text)
    return text


def _resolve_fetch_ttl() -> int:
    raw = os.environ.get("AUTOSKILLIT_FETCH_CACHE_TTL_SECONDS")
    if raw is None:
        return _DEFAULT_FETCH_TTL_SECONDS
    try:
        return int(raw)
    except ValueError:
        return _DEFAULT_FETCH_TTL_SECONDS


def _fetch_with_cache(url: str, *, home: Path, ttl: int | None = None) -> dict[str, Any] | None:
    """Fetch ``url`` via httpx with a disk-backed cache and conditional GETs.

    Returns the parsed JSON dict on success (200 or 304), or ``None`` on any
    error.  ``Authorization`` headers are scrubbed from log output to prevent
    token leakage via DEBUG-level error reporting.

    The 30-minute disk TTL is the only reliable quota defense — see
    ``_DEFAULT_FETCH_TTL_SECONDS`` for the rationale.
    """
    effective_ttl = ttl if ttl is not None else _resolve_fetch_ttl()
    cache = _read_fetch_cache(home)
    entry = cache.get(url) if isinstance(cache.get(url), dict) else None
    now = time.time()
    if entry is not None:
        cached_at = entry.get("cached_at")
        body = entry.get("body")
        if isinstance(cached_at, (int, float)) and isinstance(body, dict):
            if now - cached_at < effective_ttl:
                if entry.get("installed_version") == AUTOSKILLIT_INSTALLED_VERSION:
                    return body

    headers: dict[str, str] = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": _GITHUB_API_VERSION,
        "User-Agent": f"autoskillit/{AUTOSKILLIT_INSTALLED_VERSION}",
    }
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if entry is not None and isinstance(entry.get("etag"), str):
        headers["If-None-Match"] = entry["etag"]

    try:
        with httpx.Client(timeout=_HTTP_TIMEOUT) as client:
            response = client.get(url, headers=headers)
    except Exception as exc:
        logger.debug("fetch failed for %s: %s", url, _scrub_auth(str(exc)))
        return None

    try:
        if response.status_code == 304 and entry is not None:
            body = entry.get("body")
            if isinstance(body, dict):
                cache[url] = {
                    "body": body,
                    "etag": entry.get("etag"),
                    "cached_at": now,
                    "installed_version": AUTOSKILLIT_INSTALLED_VERSION,
                }
                _write_fetch_cache(home, cache)
                return body
            return None
        if response.status_code == 200:
            body = response.json()
            if not isinstance(body, dict):
                return None
            etag = response.headers.get("ETag")
            cache[url] = {
                "body": body,
                "etag": etag,
                "cached_at": now,
                "installed_version": AUTOSKILLIT_INSTALLED_VERSION,
            }
            _write_fetch_cache(home, cache)
            return body
        logger.debug("fetch %s returned status %d", url, response.status_code)
        return None
    except Exception as exc:
        logger.debug("fetch parse failed for %s: %s", url, _scrub_auth(str(exc)))
        return None


def _fetch_latest_version(target: str, home: Path) -> str | None:
    """Fetch the latest available version for the given target branch.

    ``target`` is either ``"releases/latest"`` (for stable/main installs) or
    ``"integration"`` (for integration installs).

    Returns ``None`` on any network error or timeout.
    """
    try:
        if target == "integration":
            data = _fetch_with_cache(_GITHUB_INTEGRATION_PYPROJECT_URL, home=home)
            if data is None:
                return None
            import base64

            raw_content = data.get("content")
            if not isinstance(raw_content, str):
                return None
            content = base64.b64decode(raw_content).decode("utf-8")
            for line in content.splitlines():
                line = line.strip()
                if line.split("=", 1)[0].strip() == "version":
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
            return None
        data = _fetch_with_cache(_GITHUB_RELEASES_URL, home=home)
        if data is None:
            return None
        tag = data.get("tag_name", "")
        return tag.lstrip("v") if isinstance(tag, str) and tag else None
    except Exception as exc:
        logger.debug("Failed to fetch latest version from GitHub: %s", _scrub_auth(str(exc)))
        return None


# ---------------------------------------------------------------------------
# Source-repo discovery and SHA resolution (ported from _source_drift.py)
# ---------------------------------------------------------------------------


def find_source_repo() -> Path | None:
    """Locate the autoskillit source repository root.

    Resolution order:
    1. ``AUTOSKILLIT_SOURCE_REPO`` env var (must exist and contain ``src/autoskillit/``).
    2. Walk upward from ``Path.cwd()``, match ``pyproject.toml``
       ``[project].name == "autoskillit"`` AND ``src/autoskillit/`` present.

    Returns ``None`` if no match found or on any error.
    """
    try:
        env_val = os.environ.get("AUTOSKILLIT_SOURCE_REPO")
        if env_val:
            candidate = Path(env_val)
            if candidate.exists() and (candidate / "src" / "autoskillit").exists():
                return candidate
            logger.debug(
                "AUTOSKILLIT_SOURCE_REPO=%s not usable (missing or no src/autoskillit/), "
                "falling through to CWD walk",
                env_val,
            )

        import tomllib

        current = Path.cwd()
        while True:
            pyproject = current / "pyproject.toml"
            if pyproject.is_file():
                try:
                    with open(pyproject, "rb") as fh:
                        data = tomllib.load(fh)
                    project_name = data.get("project", {}).get("name")
                    if (
                        project_name == "autoskillit"
                        and (current / "src" / "autoskillit").exists()
                    ):
                        return current
                except Exception:
                    logger.debug("drift check: could not parse %s", pyproject, exc_info=True)

            parent = current.parent
            if parent == current:  # Filesystem root
                break
            current = parent

        return None
    except Exception:
        logger.debug("drift check: find_source_repo failed", exc_info=True)
        return None


def resolve_reference_sha(
    info: InstallInfo,
    home: Path,
    *,
    network: bool = True,
) -> str | None:
    """Resolve the current HEAD SHA of the branch the install was tracking.

    Returns ``None`` when the SHA cannot be determined (network offline, no
    source repo, unknown revision).  The caller treats ``None`` as "skip check"
    (fail-open).

    Args:
        info: Install classification from ``detect_install()``.
        home: User home directory (used by the disk-backed fetch cache).
        network: When ``False``, only the local git or disk cache is consulted.
            The doctor check passes ``network=True`` so it can resolve remote refs.
    """
    try:
        if info.requested_revision is None:
            logger.debug("drift check skipped: no requested_revision in direct_url.json")
            return None

        rev = info.requested_revision

        # Short-circuit: exact SHA equality means no drift is possible.
        # IMPORTANT: use == not startswith — a branch named after a hex prefix
        # of the commit SHA must NOT false-positive here.
        if rev == info.commit_id:
            return info.commit_id

        sha: str | None = None

        source_repo = find_source_repo()
        if source_repo is not None and source_repo.exists():
            sha = _git_ls_remote_sha(source_repo, rev)

        if sha is None:
            sha = _api_sha(rev, home, network=network)

        return sha

    except Exception:
        logger.debug("drift check skipped: resolve_reference_sha error", exc_info=True)
        return None


def _git_ls_remote_sha(source_repo: Path, rev: str) -> str | None:
    """Run git ls-remote to resolve a branch or tag ref SHA.

    Tries ``refs/heads/<rev>`` first, then ``refs/tags/<rev>^{}`` for peeled
    tag objects.  Returns ``None`` on empty output or any subprocess error.
    """
    for ref in (f"refs/heads/{rev}", f"refs/tags/{rev}^{{}}"):
        try:
            result = subprocess.run(
                ["git", "-C", str(source_repo), "ls-remote", "origin", ref],
                capture_output=True,
                text=True,
                timeout=5,
                env=os.environ,
            )
            first_line = result.stdout.strip().splitlines()[0] if result.stdout.strip() else ""
            if first_line:
                sha = first_line.split()[0]
                if sha:
                    return sha
        except (subprocess.SubprocessError, FileNotFoundError, OSError, IndexError):
            logger.debug("git ls-remote failed for ref=%s", ref, exc_info=True)
    return None


def _api_sha(rev: str, home: Path, *, network: bool = True) -> str | None:
    """Return the commit SHA for ``rev`` from the GitHub API or disk cache.

    When ``network=False`` (doctor mode), reads the existing cache without
    any TTL check and makes no outbound HTTP request.  Returns ``None`` if the
    cache has no entry for the URL.
    """
    # Try refs/heads first; fall back to refs/tags for tag revisions.
    ref_prefix = "refs/tags" if rev.startswith("v") else "refs/heads"
    url = f"https://api.github.com/repos/TalonT-Org/AutoSkillit/git/{ref_prefix}/{rev}"

    if network:
        data: Any = _fetch_with_cache(url, home=home)
    else:
        cache = _read_fetch_cache(home)
        entry = cache.get(url) if isinstance(cache.get(url), dict) else None
        data = entry.get("body") if isinstance(entry, dict) else None

    if not isinstance(data, dict):
        return None
    obj = data.get("object")
    if not isinstance(obj, dict):
        return None
    sha = obj.get("sha")
    return sha if isinstance(sha, str) and sha else None


# ---------------------------------------------------------------------------
# Update result verification (ported from _stale_check.py)
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Signal gatherers
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Unified dismissal
# ---------------------------------------------------------------------------


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
       12 hours for integration/LOCAL_EDITABLE installs.
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


# ---------------------------------------------------------------------------
# Update sequence
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def run_update_checks(home: Path | None = None) -> None:
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

    from autoskillit.core import any_kitchen_open  # noqa: PLC0415

    if any_kitchen_open():
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
        from autoskillit.cli._timed_input import status_line, timed_prompt

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
