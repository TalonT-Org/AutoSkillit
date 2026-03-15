"""Resolve auto-detect ingredient values from the project environment."""

from __future__ import annotations

import subprocess
from pathlib import Path

from autoskillit.core import get_logger

logger = get_logger(__name__)


def resolve_ingredient_defaults(project_dir: Path) -> dict[str, str]:
    """Resolve auto-detect ingredient values from the project environment."""
    from autoskillit.config.settings import load_config
    from autoskillit.execution import REMOTE_PRECEDENCE

    resolved: dict[str, str] = {}

    try:
        for remote in REMOTE_PRECEDENCE:
            proc = subprocess.run(
                ["git", "remote", "get-url", remote],
                cwd=str(project_dir),
                capture_output=True,
                text=True,
                timeout=5,
            )
            if proc.returncode == 0 and proc.stdout.strip():
                resolved["source_dir"] = proc.stdout.strip()
                break
    except (OSError, subprocess.TimeoutExpired):
        pass

    try:
        cfg = load_config(project_dir)
        resolved["base_branch"] = cfg.branching.default_base_branch
    except Exception:
        logger.warning("resolve_base_branch_failed", exc_info=True)
        resolved["base_branch"] = "main"

    return resolved
