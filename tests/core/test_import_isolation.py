"""Fleet package must not be imported at server startup via lazy-import structure."""

from __future__ import annotations

import pytest

from tests._subprocess_helpers import run_import_subprocess

pytestmark = [pytest.mark.layer("core"), pytest.mark.medium]


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
    result = run_import_subprocess(code)
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
    result = run_import_subprocess(code)
    assert result.returncode == 0, (
        f"Subprocess crashed (rc={result.returncode}):\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert result.stdout.strip() == "[]", f"Fleet modules leaked: {result.stdout}"
