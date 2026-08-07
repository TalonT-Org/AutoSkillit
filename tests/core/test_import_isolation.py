"""Fleet package must not be imported at server startup via lazy-import structure."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("core"), pytest.mark.medium]


def _clean_subprocess_env() -> dict[str, str]:
    """Build a minimal env for import-isolation subprocesses.

    The parent process (MCP server or xdist worker) may carry env vars that
    interfere with clean Python imports in a freshly-created venv (e.g.,
    stale ``PYTHONPATH`` or MCP transport vars that trigger circular imports
    in dependency packages). Pass only what the subprocess needs: ``PATH``
    for executable resolution, ``HOME`` for site-packages discovery,
    ``VIRTUAL_ENV`` for venv activation (required when install-worktree
    rebuilds the venv mid-run), and ``PYTHONDONTWRITEBYTECODE`` to match
    the test-suite policy.
    """
    env: dict[str, str] = {}
    for key in ("PATH", "HOME", "USER", "LANG", "LC_ALL", "VIRTUAL_ENV"):
        val = os.environ.get(key)
        if val is not None:
            env[key] = val
    if "VIRTUAL_ENV" not in env:
        venv_dir = str(Path(sys.executable).resolve().parent.parent)
        if (Path(venv_dir) / "pyvenv.cfg").exists():
            env["VIRTUAL_ENV"] = venv_dir
    # Sourced from the harness env override registry (tests/_test_env_parity.py)
    # rather than hardcoded, so the parity contract pincer catches drift.
    from tests._test_env_parity import TEST_HARNESS_ENV_OVERRIDES

    for var, override in TEST_HARNESS_ENV_OVERRIDES.items():
        if override.parity_fixture is None:
            env[var] = override.value
    return env


def _run_import_subprocess(code: str) -> subprocess.CompletedProcess[str]:
    """Run import-checking code in a subprocess resilient to venv churn.

    Under xdist, sibling workers may trigger ``uv run`` (e.g., ruff check
    in test_ci_dev_config) which calls ``uv sync``, swapping ``.dist-info``
    directories in the shared ``.venv`` mid-run. This causes
    ``PackageNotFoundError`` in third-party ``__init__.py`` files (e.g.,
    fastmcp) that call ``importlib.metadata.version()`` at import time.

    Retry once on import-infrastructure errors since the race window is
    short (typically < 1 s while ``uv sync`` replaces metadata dirs).
    """
    env = _clean_subprocess_env()
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


def test_server_import_loads_fleet_package_eagerly() -> None:
    """
    Fleet imports in server/ are hoisted to module level so they bind at startup
    before core's lazy_loader freezes the symbol map. Importing autoskillit.server
    must pull in autoskillit.fleet.
    """
    code = (
        "import sys; import autoskillit.server; "
        "fleet_modules = [k for k in sys.modules if k.startswith('autoskillit.fleet')]; "
        "print(bool(fleet_modules))"
    )
    result = _run_import_subprocess(code)
    assert result.returncode == 0, (
        f"Subprocess crashed (rc={result.returncode}):\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert result.stdout.strip() == "True", f"Fleet modules not loaded: {result.stdout}"


def test_cli_app_import_does_not_load_fleet_package() -> None:
    """Importing autoskillit.cli.app must not trigger fleet package init."""
    code = (
        "import sys; import autoskillit.cli.app; "
        "fleet_modules = [k for k in sys.modules if k.startswith('autoskillit.fleet')]; "
        "print(fleet_modules)"
    )
    result = _run_import_subprocess(code)
    assert result.returncode == 0, (
        f"Subprocess crashed (rc={result.returncode}):\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert result.stdout.strip() == "[]", f"Fleet modules leaked: {result.stdout}"
