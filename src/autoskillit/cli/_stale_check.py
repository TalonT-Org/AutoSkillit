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
from datetime import UTC, datetime, timedelta
from pathlib import Path

from autoskillit.cli._terminal import terminal_guard
from autoskillit.core import get_logger, pkg_root

logger = get_logger(__name__)

_DISMISS_FILE = "update_check.json"
_DISMISS_WINDOW = timedelta(hours=12)
_SNOOZE_WINDOW = timedelta(hours=1)
_FETCH_TIMEOUT = 5

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
    from autoskillit.core import atomic_write

    state_file = home / ".autoskillit" / _DISMISS_FILE
    state_file.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(state_file, json.dumps(state))


def _fetch_latest_version(dev_mode: bool) -> str | None:
    """Fetch the latest available version from GitHub.

    Dev mode: reads pyproject.toml from the ``integration`` branch.
    Normal mode: reads the latest GitHub release tag.

    Returns ``None`` on any network error or timeout.
    """
    import urllib.request

    try:
        if dev_mode:
            req = urllib.request.Request(
                _GITHUB_INTEGRATION_PYPROJECT_URL,
                headers={"Accept": "application/vnd.github.v3+json"},
            )
            with urllib.request.urlopen(req, timeout=_FETCH_TIMEOUT) as resp:
                data = json.loads(resp.read())
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
        else:
            req = urllib.request.Request(
                _GITHUB_RELEASES_URL,
                headers={"Accept": "application/vnd.github.v3+json"},
            )
            with urllib.request.urlopen(req, timeout=_FETCH_TIMEOUT) as resp:
                data = json.loads(resp.read())
            tag = data.get("tag_name", "")
            return tag.lstrip("v") if tag else None
    except Exception:
        logger.debug("Failed to fetch latest version from GitHub", exc_info=True)
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

    _skip_env = {**os.environ, "AUTOSKILLIT_SKIP_STALE_CHECK": "1"}

    import autoskillit as _pkg
    from autoskillit.cli._hooks import _claude_settings_path

    _home = home or Path.home()
    dev_mode = is_dev_mode(home=_home)
    state = _read_dismiss_state(_home)
    latest = _fetch_latest_version(dev_mode)

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

    # Hook drift check
    from autoskillit.cli import _count_hook_registry_drift

    settings_path = _claude_settings_path("user")
    drift = _count_hook_registry_drift(settings_path)
    if (drift.missing > 0 or drift.orphaned > 0) and not _is_dismissed(state, "hooks", None):
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
        else:
            state["hooks"] = {
                "dismissed_at": datetime.now(UTC).isoformat(),
                "dismissed_version": current,
            }
            _write_dismiss_state(_home, state)
