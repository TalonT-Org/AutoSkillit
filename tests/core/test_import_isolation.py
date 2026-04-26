"""Fleet package must not be imported at server startup via lazy-import structure."""

from __future__ import annotations

import subprocess
import sys

import pytest

pytestmark = [pytest.mark.layer("core"), pytest.mark.medium]


def test_server_import_does_not_load_fleet_package_lazily() -> None:
    """
    Fleet module loading is deferred to lifespan/tool-call time via lazy imports.
    A bare `import autoskillit.server` must not pull in autoskillit.fleet.* —
    not because of an env-var gate, but because no top-level fleet import exists.
    """
    code = (
        "import sys; import autoskillit.server; "
        "fleet_modules = [k for k in sys.modules if k.startswith('autoskillit.fleet')]; "
        "print(fleet_modules)"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    )
    assert result.stdout.strip() == "[]", f"Fleet modules leaked: {result.stdout}"


def test_cli_app_import_does_not_load_fleet_package() -> None:
    """Importing autoskillit.cli.app must not trigger fleet package init."""
    code = (
        "import sys; import autoskillit.cli.app; "
        "fleet_modules = [k for k in sys.modules if k.startswith('autoskillit.fleet')]; "
        "print(fleet_modules)"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    )
    assert result.stdout.strip() == "[]", f"Fleet modules leaked: {result.stdout}"
