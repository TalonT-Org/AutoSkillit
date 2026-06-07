"""Fleet package must not be imported at server startup via lazy-import structure."""

from __future__ import annotations

import os
import subprocess
import sys

import pytest

pytestmark = [pytest.mark.layer("core"), pytest.mark.medium]


def _clean_subprocess_env() -> dict[str, str]:
    """Build a minimal env for import-isolation subprocesses.

    The parent process (MCP server or xdist worker) may carry env vars that
    interfere with clean Python imports in a freshly-created venv (e.g.,
    stale ``VIRTUAL_ENV``, ``PYTHONPATH``, or MCP transport vars that trigger
    circular imports in dependency packages). Pass only what the subprocess
    needs: ``PATH`` for executable resolution, ``HOME`` for site-packages
    discovery, and ``PYTHONDONTWRITEBYTECODE`` to match the test-suite policy.
    """
    env: dict[str, str] = {}
    for key in ("PATH", "HOME", "USER", "LANG", "LC_ALL"):
        val = os.environ.get(key)
        if val is not None:
            env[key] = val
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


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
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        env=_clean_subprocess_env(),
    )
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
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        env=_clean_subprocess_env(),
    )
    assert result.returncode == 0, (
        f"Subprocess crashed (rc={result.returncode}):\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert result.stdout.strip() == "[]", f"Fleet modules leaked: {result.stdout}"
