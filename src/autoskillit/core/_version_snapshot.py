"""Process-scoped version snapshot for session telemetry (L0).

collect_version_snapshot() is cached with lru_cache(maxsize=1) so that the
subprocess call to `claude --version` and filesystem reads happen once per
process lifetime. Callers must call .cache_clear() in tests that need isolation.

Never raises — all helpers silently return empty fallbacks on any error.
"""

from __future__ import annotations

import functools
import importlib.metadata
import json
import logging
import subprocess
from pathlib import Path
from typing import Any

_logger = logging.getLogger(__name__)  # noqa: TID251 — L0 module, no autoskillit imports allowed


@functools.lru_cache(maxsize=1)
def collect_version_snapshot() -> dict[str, Any]:
    """Return a static version snapshot for the current process.

    Fields:
        autoskillit_version: installed package version string.
        install_type: "git-vcs" | "local-editable" | "local-path" | "unknown".
        commit_id: git commit hash when install_type is "git-vcs", else None.
        claude_code_version: output of `claude --version`, or "".
        plugins: list of {"ref": ..., "version"?: ...} dicts
            read from ~/.claude/plugins/installed_plugins.json.
    """
    install = _install_info()
    return {
        "autoskillit_version": _autoskillit_version(),
        "install_type": install.get("install_type", "unknown"),
        "commit_id": install.get("commit_id"),
        "claude_code_version": _claude_code_version(),
        "plugins": _plugins(),
    }


def _autoskillit_version() -> str:
    try:
        return importlib.metadata.version("autoskillit")
    except Exception:
        _logger.warning("Failed to read autoskillit version", exc_info=True)
        return ""


def _install_info() -> dict[str, Any]:
    """Parse direct_url.json to classify the autoskillit install."""
    try:
        dist = importlib.metadata.Distribution.from_name("autoskillit")
        raw = dist.read_text("direct_url.json")
        if not raw:
            return {"install_type": "unknown", "commit_id": None}
        data = json.loads(raw)
        vcs = data.get("vcs_info", {})
        if isinstance(vcs, dict) and vcs.get("vcs") == "git":
            return {
                "install_type": "git-vcs",
                "commit_id": vcs.get("commit_id") or None,
            }
        dir_info = data.get("dir_info", {})
        url = data.get("url", "")
        if isinstance(dir_info, dict) and dir_info.get("editable") is True:
            return {"install_type": "local-editable", "commit_id": None}
        if isinstance(url, str) and url.startswith("file://"):
            return {"install_type": "local-path", "commit_id": None}
        return {"install_type": "unknown", "commit_id": None}
    except Exception:
        _logger.warning("Failed to parse install info from direct_url.json", exc_info=True)
        return {"install_type": "unknown", "commit_id": None}


def _claude_code_version() -> str:
    """Run `claude --version` and return the stripped output, or "" on error.

    Prefers CLAUDE_CODE_EXECPATH (injected by Claude Code into the MCP server
    environment) over bare `claude` on PATH so that the binary that actually
    launched the server is queried rather than whatever `claude` resolves to in
    the current PATH.
    """
    import os

    exec_path = os.environ.get("CLAUDE_CODE_EXECPATH") or "claude"
    try:
        result = subprocess.run(
            [exec_path, "--version"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return result.stdout.strip() or result.stderr.strip()
    except subprocess.TimeoutExpired:
        return ""
    except Exception:
        _logger.warning("Failed to run claude --version", exc_info=True)
        return ""


def _plugins() -> list[dict[str, Any]]:
    """Read installed_plugins.json and return a list of plugin descriptors.

    The real schema produced by `claude plugin install` is:
        {"version": 2, "plugins": {"<ref>": [{"version": "...", ...}, ...]}}

    Each ref maps to a **list** of install-scope objects; each object carries a
    "version" field. We use the first entry (index 0) per ref.
    """
    try:
        path = Path.home() / ".claude" / "plugins" / "installed_plugins.json"
        if not path.exists():
            return []
        data = json.loads(path.read_text(encoding="utf-8"))
        plugins: dict[str, Any] = data.get("plugins", {})
        if not isinstance(plugins, dict):
            return []
        entries = []
        for ref, installs in plugins.items():
            if not isinstance(installs, list) or not installs:
                continue
            info = installs[0] if isinstance(installs[0], dict) else {}
            entry: dict[str, Any] = {"ref": ref}
            if "version" in info:
                entry["version"] = info["version"]
            entries.append(entry)
        return entries
    except Exception:
        _logger.warning("Failed to read installed_plugins.json", exc_info=True)
        return []
