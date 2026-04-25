"""REQ-IMP-006: fleet package must not be imported at startup when fleet is disabled."""

from __future__ import annotations

import subprocess
import sys


def _run_isolation_check(import_stmt: str) -> set[str]:
    """Return the set of autoskillit.fleet.* modules loaded after import_stmt runs."""
    code = (
        f"{import_stmt}\n"
        "import sys\n"
        "print('\\n'.join(k for k in sys.modules if k.startswith('autoskillit.fleet')))"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        env={
            **__import__("os").environ,
            "AUTOSKILLIT_FEATURES__FLEET": "false",
        },
    )
    assert result.returncode == 0, f"Import failed:\n{result.stderr}"
    loaded = {line.strip() for line in result.stdout.splitlines() if line.strip()}
    return loaded


def test_server_import_does_not_load_fleet_package() -> None:
    """Importing autoskillit.server must not trigger fleet package init when fleet=false."""
    loaded = _run_isolation_check("import autoskillit.server")
    assert not loaded, (
        f"autoskillit.server import triggered fleet package init. "
        f"Loaded fleet modules: {sorted(loaded)}"
    )


def test_cli_app_import_does_not_load_fleet_package() -> None:
    """Importing autoskillit.cli.app must not trigger fleet package init when fleet=false."""
    loaded = _run_isolation_check("import autoskillit.cli.app")
    assert not loaded, (
        f"autoskillit.cli.app import triggered fleet package init. "
        f"Loaded fleet modules: {sorted(loaded)}"
    )
