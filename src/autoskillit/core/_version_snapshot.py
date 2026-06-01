"""Process-scoped version snapshot for session telemetry (IL-0).

collect_version_snapshot() is cached with lru_cache(maxsize=4) so that the
subprocess call to `claude --version` and filesystem reads happen once per
process lifetime, keyed by the supplied backend instance (or None for the
env-var-dispatched legacy fallback). Callers must call .cache_clear() in tests
that need isolation.

Never raises — all helpers silently return empty fallbacks on any error.
"""

from __future__ import annotations

import functools
import importlib.metadata
import json
import logging
import os
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ._install_detect import parse_direct_url
from .types._type_constants_env import (
    AGENT_BACKEND_CLAUDE_CODE,
    AGENT_BACKEND_CODEX,
    AGENT_BACKEND_ENV_VAR,
)

if TYPE_CHECKING:
    from .types._type_protocols_backend import CodingAgentBackend

logger = logging.getLogger(__name__)  # noqa: TID251 — IL-0 module, no autoskillit imports allowed


@functools.lru_cache(maxsize=4)
def collect_version_snapshot(
    backend: CodingAgentBackend | None = None,
) -> dict[str, Any]:
    """Return a static version snapshot for the current process.

    Fields:
        autoskillit_version: installed package version string.
        install_type: "git-vcs" | "local-editable" | "local-path" | "unknown".
        commit_id: git commit hash when install_type is "git-vcs", else None.
        claude_code_version: output of `claude --version`, or "".
        plugins: list of {"ref": ..., "version"?: ...} dicts
            read from ~/.claude/plugins/installed_plugins.json.
        codex_version: output of `codex --version`, or "".
        codex_plugins: list of plugin dicts from `codex plugin list --json`, or [].

    When ``backend`` is provided, version/plugin data is sourced from
    ``backend.version()`` and ``backend.list_plugins()`` directly. When
    ``backend`` is None, the env-var-dispatched legacy path is used (reads
    ``AUTOSKILLIT_AGENT_BACKEND``).
    """
    install = _install_info()
    result: dict[str, Any] = {
        "autoskillit_version": _autoskillit_version(),
        "install_type": install.get("install_type", "unknown"),
        "commit_id": install.get("commit_id"),
        "claude_code_version": "",
        "plugins": [],
        "codex_version": "",
        "codex_plugins": [],
    }

    if backend is not None:
        if backend.name == AGENT_BACKEND_CLAUDE_CODE:
            result["claude_code_version"] = backend.version()
            result["plugins"] = backend.list_plugins()
        elif backend.name == AGENT_BACKEND_CODEX:
            result["codex_version"] = backend.version()
            result["codex_plugins"] = backend.list_plugins()
        else:
            logger.warning(
                "Unknown backend name %r — version snapshot will have zero-value backend fields",
                backend.name,
            )
        return result

    active = os.environ.get(AGENT_BACKEND_ENV_VAR, AGENT_BACKEND_CLAUDE_CODE)
    if active == AGENT_BACKEND_CLAUDE_CODE:
        exec_path = os.environ.get("CLAUDE_CODE_EXECPATH") or "claude"
        try:
            proc = subprocess.run(
                [exec_path, "--version"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if proc.returncode != 0:
                logger.warning("claude --version exited with code %d", proc.returncode)
            result["claude_code_version"] = proc.stdout.strip() or proc.stderr.strip()
        except subprocess.TimeoutExpired:
            pass
        except Exception:
            logger.warning("Failed to run claude --version", exc_info=True)

        try:
            plugins_path = Path.home() / ".claude" / "plugins" / "installed_plugins.json"
            if plugins_path.exists():
                data = json.loads(plugins_path.read_text(encoding="utf-8"))
                plugins_map: dict[str, Any] = data.get("plugins", {})
                if isinstance(plugins_map, dict):
                    entries: list[dict[str, Any]] = []
                    for ref, installs in plugins_map.items():
                        if not isinstance(installs, list) or not installs:
                            continue
                        first = installs[0]
                        info = first if isinstance(first, dict) else {}
                        entry: dict[str, Any] = {"ref": ref}
                        if "version" in info:
                            entry["version"] = info["version"]
                        entries.append(entry)
                    result["plugins"] = entries
        except Exception:
            logger.warning("Failed to read installed_plugins.json", exc_info=True)
    elif active == AGENT_BACKEND_CODEX:
        try:
            proc = subprocess.run(
                ["codex", "--version"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            result["codex_version"] = proc.stdout.strip() or proc.stderr.strip()
        except subprocess.TimeoutExpired:
            pass
        except Exception:
            logger.warning("Failed to run codex --version", exc_info=True)

        try:
            proc = subprocess.run(
                ["codex", "plugin", "list", "--json"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if proc.stdout.strip():
                parsed = json.loads(proc.stdout)
                if isinstance(parsed, list):
                    result["codex_plugins"] = parsed
        except subprocess.TimeoutExpired:
            pass
        except Exception:
            logger.warning("Failed to run codex plugin list", exc_info=True)

    return result


def _autoskillit_version() -> str:
    try:
        return importlib.metadata.version("autoskillit")
    except Exception:
        logger.warning("Failed to read autoskillit version", exc_info=True)
        return ""


def _install_info() -> dict[str, Any]:
    """Classify the autoskillit install type and commit hash."""
    info = parse_direct_url()
    return {"install_type": info["install_type"], "commit_id": info["commit_id"]}
