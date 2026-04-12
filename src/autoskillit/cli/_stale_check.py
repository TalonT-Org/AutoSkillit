"""CLI startup stale-install check.

Non-blocking check that runs on every interactive CLI command. Compares the
running binary version against the latest available (GitHub API) and counts
HOOK_REGISTRY drift. Shows a dismissable prompt (7-day cooldown) offering to
run the update command automatically.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

from autoskillit.cli._terminal import terminal_guard
from autoskillit.core import AUTOSKILLIT_INSTALLED_VERSION, atomic_write, get_logger, pkg_root

logger = get_logger(__name__)

_DISMISS_FILE = "update_check.json"
_DISMISS_WINDOW = timedelta(hours=12)
_SNOOZE_WINDOW = timedelta(hours=1)

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
_INSTALL_FROM_INTEGRATION = "git+https://github.com/TalonT-Org/AutoSkillit.git@integration"


def is_dev_mode(home: Path | None = None) -> bool:
    """Return True if running in dev mode.

    Dev mode is detected via:
    1. ``~/.autoskillit/dev`` marker file
    2. ``<cwd>/.autoskillit/dev`` marker file
    3. ``pkg_root()`` is inside a git main checkout (has .git *directory*, not file)
    """
    _home = home or Path.home()

    # Home-level dev marker
    if (_home / ".autoskillit" / "dev").exists():
        return True

    # Project-level dev marker (CWD)
    if (Path.cwd() / ".autoskillit" / "dev").exists():
        return True

    # pkg_root() inside a git main checkout
    # Delegate to the canonical implementation in core/paths.py.
    # is_git_main_checkout() returns True for .git-dir (main checkout) = dev mode.
    # is_git_main_checkout() returns False for .git-file (worktree) and no-repo = not dev mode.
    from autoskillit.core import is_git_main_checkout

    return is_git_main_checkout(pkg_root())


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


def _scrub_auth(text: str) -> str:
    """Remove any GITHUB_TOKEN value or ``Bearer …`` token from a string."""
    if not text:
        return text
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        text = text.replace(token, "***")
    # Belt-and-suspenders: any literal "Bearer <something>" gets masked too,
    # in case httpx echoes a request repr that includes the header value.
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
            }
            _write_fetch_cache(home, cache)
            return body
        logger.debug("fetch %s returned status %d", url, response.status_code)
        return None
    except Exception as exc:
        logger.debug("fetch parse failed for %s: %s", url, _scrub_auth(str(exc)))
        return None


def _fetch_latest_version(dev_mode: bool, home: Path) -> str | None:
    """Fetch the latest available version from GitHub via the cached client.

    Dev mode: reads pyproject.toml from the ``integration`` branch.
    Normal mode: reads the latest GitHub release tag.

    Returns ``None`` on any network error or timeout.
    """
    try:
        if dev_mode:
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


def _is_dismissed(state: dict[str, object], check_type: str, latest: str | None) -> bool:
    """Return True if the given check_type has been dismissed within the window.

    Dismissal expires when:
    - The ``dismissed_at`` timestamp is older than 7 days, OR
    - ``latest`` is a higher version than ``dismissed_version``

    State format: ``{"binary": {"dismissed_at": ..., "dismissed_version": ...}, "hooks": {...}}``
    """
    try:
        entry = state.get(check_type)
        if not isinstance(entry, dict):
            return False
        dismissed_at = datetime.fromisoformat(entry["dismissed_at"])
        if datetime.now(UTC) - dismissed_at >= _DISMISS_WINDOW:
            return False
        if latest is not None:
            from packaging.version import Version

            if Version(latest) > Version(entry["dismissed_version"]):
                return False
        return True
    except Exception:
        logger.debug("Failed to parse dismiss state for check_type=%s", check_type, exc_info=True)
        return False


def _is_drift_dismissed(state: dict[str, object], installed_sha: str, reference_sha: str) -> bool:
    """Return True if source drift has been dismissed within the window for the given SHA pair.

    Dismissal is SHA-keyed: if either the ``installed_sha`` or ``reference_sha`` changes
    (e.g. after a git pull on the source repo or a reinstall), the dismissal expires
    immediately regardless of whether the 12-hour window has passed.
    """
    try:
        entry = state.get("source_drift")
        if not isinstance(entry, dict):
            return False
        dismissed_at = datetime.fromisoformat(entry["dismissed_at"])
        if datetime.now(UTC) - dismissed_at >= _DISMISS_WINDOW:
            return False
        if entry.get("installed_sha") != installed_sha:
            return False
        if entry.get("reference_sha") != reference_sha:
            return False
        return True
    except Exception:
        logger.debug("Failed to parse source drift dismiss state", exc_info=True)
        return False


def _is_snoozed(state: dict[str, object], check_type: str) -> bool:
    """Return True if an update attempt for check_type is within the snooze window.

    Snooze encodes "update attempted but outcome unverified; retry soon."
    It expires by time only — there is no version comparison.

    State key: ``"{check_type}_snoozed"`` (e.g. ``"binary_snoozed"``).
    """
    try:
        entry = state.get(f"{check_type}_snoozed")
        if not isinstance(entry, dict):
            return False
        snoozed_at = datetime.fromisoformat(entry["snoozed_at"])
        return datetime.now(UTC) - snoozed_at < _SNOOZE_WINDOW
    except Exception:
        logger.debug("Failed to parse snooze state for check_type=%s", check_type, exc_info=True)
        return False


def _verify_update_result(
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
    On False, writes a binary_snoozed record and prints an actionable warning.
    """
    import importlib.metadata

    try:
        new_version = importlib.metadata.version("autoskillit")
    except Exception:
        logger.debug("Failed to read version after update attempt", exc_info=True)
        new_version = current  # Treat as unchanged if unreadable

    if new_version != current:
        return True

    # Version unchanged — record a snooze and surface a warning
    state["binary_snoozed"] = {
        "snoozed_at": datetime.now(UTC).isoformat(),
        "attempted_version": latest,
    }
    _write_dismiss_state(home, state)
    print(
        f"\nUpdate attempted but version is still {current}. "
        f"If you have an editable install, run:\n"
        f"  uv pip install -e <project_dir>\n"
        f"Or for a tool install:\n"
        f"  uv tool upgrade autoskillit",
        flush=True,
    )
    return False


def _verify_hooks_result(
    home: Path,
    state: dict[str, object],
    settings_path: Path,
    current: str,
) -> bool:
    """Verify that hooks install resolved all drift.

    Returns True if drift is now zero.
    Returns False if drift persists, writing a hooks_snoozed record.
    """
    try:
        from autoskillit.hook_registry import _count_hook_registry_drift
    except ImportError:
        logger.debug("hook_registry unavailable — cannot verify hooks result")
        return False

    drift = _count_hook_registry_drift(settings_path)
    if drift.missing == 0 and drift.orphaned == 0:
        return True

    state["hooks_snoozed"] = {
        "snoozed_at": datetime.now(UTC).isoformat(),
        "attempted_version": current,
    }
    _write_dismiss_state(home, state)
    return False


def _detect_install_type() -> tuple[str, str | None]:
    """Detect whether autoskillit is installed as an editable or tool install.

    Returns:
        ("editable", project_dir_str) — for editable installs (pip install -e)
        ("tool", None) — for uv tool or other non-editable installs
    """
    import importlib.metadata

    try:
        dist = importlib.metadata.Distribution.from_name("autoskillit")
        raw = dist.read_text("direct_url.json")
        if raw:
            import json as _json

            data = _json.loads(raw)
            if data.get("dir_info", {}).get("editable"):
                url = data.get("url", "")
                # file:// URL → strip scheme
                if url.startswith("file://"):
                    return ("editable", url[7:])
    except Exception:
        logger.debug("Failed to detect install type from direct_url.json", exc_info=True)

    return ("tool", None)


def run_stale_check(home: Path | None = None) -> None:
    """Run the stale-install check on interactive CLI invocations.

    Guards:
    - Skips when CLAUDECODE=1 (headless/MCP session)
    - Skips when stdin or stdout is not a TTY
    """
    if (
        os.environ.get("CLAUDECODE")
        or os.environ.get("AUTOSKILLIT_SKIP_STALE_CHECK")
        or not sys.stdin.isatty()
        or not sys.stdout.isatty()
    ):
        return

    _skip_env = {
        **os.environ,
        "AUTOSKILLIT_SKIP_STALE_CHECK": "1",
        "AUTOSKILLIT_SKIP_SOURCE_DRIFT_CHECK": "1",
    }

    import autoskillit as _pkg
    from autoskillit.cli._hooks import _claude_settings_path

    _home = home or Path.home()
    dev_mode = is_dev_mode(home=_home)
    state = _read_dismiss_state(_home)
    latest = _fetch_latest_version(dev_mode, _home)

    current: str = getattr(_pkg, "__version__", "0.0.0")

    # Binary version check
    if latest is not None:
        from packaging.version import Version

        if (
            Version(latest) > Version(current)
            and not _is_dismissed(state, "binary", latest)
            and not _is_snoozed(state, "binary")
        ):
            print(
                f"\nAutoSkillit {latest} is available (you have {current}).",
                flush=True,
            )
            answer = input("Update now? [Y/n] ").strip().lower()
            if answer in ("", "y", "yes"):
                install_type, project_dir = _detect_install_type()
                with terminal_guard():
                    if dev_mode and install_type == "editable" and project_dir:
                        subprocess.run(
                            ["uv", "pip", "install", "-e", project_dir],
                            check=False,
                            env=_skip_env,
                        )
                    elif dev_mode:
                        subprocess.run(
                            ["uv", "tool", "install", "--force", _INSTALL_FROM_INTEGRATION],
                            check=False,
                            env=_skip_env,
                        )
                    else:
                        subprocess.run(
                            ["uv", "tool", "upgrade", "autoskillit"],
                            check=False,
                            env=_skip_env,
                        )
                    subprocess.run(["autoskillit", "install"], check=False, env=_skip_env)
                _verify_update_result(current, latest, _home, state)
                return
            else:
                state["binary"] = {
                    "dismissed_at": datetime.now(UTC).isoformat(),
                    "dismissed_version": latest,
                }
                _write_dismiss_state(_home, state)

    # Hook drift check — all scopes
    from autoskillit.cli import _count_hook_registry_drift
    from autoskillit.hook_registry import HookDriftResult, iter_all_scope_paths

    total_missing = 0
    total_orphaned = 0
    orphaned_cmds_all: frozenset[str] = frozenset()
    for _scope_label, _scope_path in iter_all_scope_paths(Path.cwd()):
        _d = _count_hook_registry_drift(_scope_path)
        total_missing = max(total_missing, _d.missing)
        total_orphaned += _d.orphaned
        orphaned_cmds_all = orphaned_cmds_all | _d.orphaned_cmds
    drift = HookDriftResult(
        missing=total_missing, orphaned=total_orphaned, orphaned_cmds=orphaned_cmds_all
    )
    settings_path = _claude_settings_path("user")
    if (
        (drift.missing > 0 or drift.orphaned > 0)
        and not _is_dismissed(state, "hooks", None)
        and not _is_snoozed(state, "hooks")
    ):
        if drift.orphaned > 0:
            prompt_msg = (
                f"\n\u26a0\ufe0f  {drift.orphaned} orphaned hook entry(ies) in settings.json "
                f"will block tool calls with ENOENT. Run 'autoskillit install'? [Y/n] "
            )
        else:
            prompt_msg = (
                f"\n{drift.missing} new/changed hook(s) detected since last install.\n"
                f"Run 'autoskillit install' to sync hooks? [Y/n] "
            )
        print(prompt_msg, end="", flush=True)
        answer = input("").strip().lower()
        if answer in ("", "y", "yes"):
            with terminal_guard():
                subprocess.run(["autoskillit", "install"], check=False, env=_skip_env)
            _verify_hooks_result(_home, state, settings_path, current)
            return
        else:
            state["hooks"] = {
                "dismissed_at": datetime.now(UTC).isoformat(),
                "dismissed_version": current,
            }
            _write_dismiss_state(_home, state)
