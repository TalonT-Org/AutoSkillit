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
_DISMISS_WINDOW = timedelta(days=7)
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
    # Walk from pkg_root() upward; stop at the first .git entry found
    candidate = pkg_root()
    while True:
        git_path = candidate / ".git"
        if git_path.is_dir():
            # .git directory = main checkout = dev mode
            return True
        if git_path.is_file():
            # .git file = worktree = not dev mode
            return False
        parent = candidate.parent
        if parent == candidate:
            # Reached filesystem root without finding .git
            return False
        candidate = parent


def _read_dismiss_state(home: Path) -> dict:
    """Read dismissal state from ~/.autoskillit/update_check.json.

    Returns empty dict on any error (missing file, malformed JSON, etc.).
    """
    state_file = home / ".autoskillit" / _DISMISS_FILE
    try:
        return json.loads(state_file.read_text(encoding="utf-8"))
    except Exception:
        logger.debug("Failed to read dismiss state from %s", state_file, exc_info=True)
        return {}


def _write_dismiss_state(home: Path, state: dict) -> None:
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

            content = base64.b64decode(data["content"]).decode("utf-8")
            for line in content.splitlines():
                line = line.strip()
                if line.startswith("version") and "=" in line:
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


def _is_dismissed(state: dict, check_type: str, latest: str | None) -> bool:
    """Return True if the given check_type has been dismissed within the window.

    Dismissal expires when:
    - The ``dismissed_at`` timestamp is older than 7 days, OR
    - ``latest`` is a higher version than ``dismissed_version``
    """
    try:
        if state.get("check_type") != check_type:
            return False
        dismissed_at = datetime.fromisoformat(state["dismissed_at"])
        if datetime.now(UTC) - dismissed_at >= _DISMISS_WINDOW:
            return False
        if latest is not None:
            from packaging.version import Version

            if Version(latest) > Version(state["dismissed_version"]):
                return False
        return True
    except Exception:
        logger.debug("Failed to parse dismiss state for check_type=%s", check_type, exc_info=True)
        return False


def _count_hook_registry_drift_for_path(settings_path: Path) -> int:
    """Thin delegator to _doctor._count_hook_registry_drift.

    Extracted as a separate callable so tests can monkeypatch it at the
    _stale_check boundary without importing _doctor.
    """
    from autoskillit.cli._doctor import _count_hook_registry_drift

    return _count_hook_registry_drift(settings_path)


def run_stale_check(home: Path | None = None) -> None:
    """Run the stale-install check on interactive CLI invocations.

    Guards:
    - Skips when CLAUDECODE=1 (headless/MCP session)
    - Skips when stdin or stdout is not a TTY
    """
    if os.environ.get("CLAUDECODE") or not sys.stdin.isatty() or not sys.stdout.isatty():
        return

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

        if Version(latest) > Version(current) and not _is_dismissed(state, "binary", latest):
            print(
                f"\nAutoSkillit {latest} is available (you have {current}).",
                flush=True,
            )
            answer = input("Update now? [Y/n] ").strip().lower()
            if answer in ("", "y", "yes"):
                with terminal_guard():
                    if dev_mode:
                        subprocess.run(
                            ["uv", "tool", "install", "--force", _INSTALL_FROM_INTEGRATION],
                            check=False,
                        )
                    else:
                        subprocess.run(
                            ["uv", "tool", "upgrade", "autoskillit"],
                            check=False,
                        )
                    subprocess.run(["autoskillit", "install"], check=False)
                return
            else:
                _write_dismiss_state(
                    _home,
                    {
                        "dismissed_at": datetime.now(UTC).isoformat(),
                        "dismissed_version": latest,
                        "check_type": "binary",
                    },
                )

    # Hook drift check
    settings_path = _claude_settings_path("user")
    n_drift = _count_hook_registry_drift_for_path(settings_path)
    if n_drift > 0 and not _is_dismissed(state, "hooks", None):
        print(
            f"\n{n_drift} new/changed hook(s) detected since last install.",
            flush=True,
        )
        answer = input("Run 'autoskillit install' to sync hooks? [Y/n] ").strip().lower()
        if answer in ("", "y", "yes"):
            with terminal_guard():
                subprocess.run(["autoskillit", "install"], check=False)
        else:
            _write_dismiss_state(
                _home,
                {
                    "dismissed_at": datetime.now(UTC).isoformat(),
                    "dismissed_version": current,
                    "check_type": "hooks",
                },
            )
