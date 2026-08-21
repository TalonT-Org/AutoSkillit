"""Shared subprocess helpers for fresh-interpreter tests."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path


def clean_subprocess_env() -> dict[str, str]:
    """Build a minimal environment for import-isolation subprocesses."""
    env: dict[str, str] = {}
    for key in ("PATH", "HOME", "USER", "LANG", "LC_ALL", "VIRTUAL_ENV"):
        value = os.environ.get(key)
        if value is not None:
            env[key] = value
    if "VIRTUAL_ENV" not in env:
        venv_dir = str(Path(sys.executable).resolve().parent.parent)
        if (Path(venv_dir) / "pyvenv.cfg").exists():
            env["VIRTUAL_ENV"] = venv_dir

    from tests._test_env_parity import TEST_HARNESS_ENV_OVERRIDES

    for var, override in TEST_HARNESS_ENV_OVERRIDES.items():
        env[var] = override.value
    return env


def run_import_subprocess(code: str) -> subprocess.CompletedProcess[str]:
    """Run import-checking code, retrying once after transient venv churn."""
    env = clean_subprocess_env()
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        env=env,
    )
    if result.returncode != 0 and (
        "PackageNotFoundError" in result.stderr or "No module named" in result.stderr
    ):
        time.sleep(1)
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            env=env,
        )
    return result
