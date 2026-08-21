"""B-1 regression guard: InstallBinding is sealed by import time (issue #4597).

``hook_registry.py:539``'s module-scope ``HOOKS_DIR = pkg_root() / "hooks"``
forces the seal early on every process kind — reached at import time via
``cli/__init__.py`` -> ``cli/_hooks.py`` and ``execution/backends/__init__.py``
-> ``execution/backends/_codex_hooks.py``. Because that property is
incidental to ``HOOKS_DIR``'s own purpose rather than something either module
declares, a future refactor that makes ``HOOKS_DIR`` lazy could silently
reintroduce late sealing without touching anything that looks related. This
guard exists so that refactor breaks a test instead of shipping quietly.

Must run in a subprocess: by the time an in-process pytest test executes,
``core.paths`` has already been imported by collection, so "is the seal
populated" is unobservable from inside the worker. Mirrors the
subprocess-isolation idiom established by ``tests/core/test_import_isolation.py``
and ``tests/fleet/test_startup_warm.py``.
"""

from __future__ import annotations

import pytest

from tests.core.test_import_isolation import _run_import_subprocess

pytestmark = [pytest.mark.layer("core"), pytest.mark.medium]


@pytest.mark.parametrize("module", ["cli", "server", "execution"])
def test_install_binding_seal_forced_by_early_import(module: str) -> None:
    code = (
        f"import autoskillit.{module}\n"
        "from autoskillit.core._install_binding import resolve_install_binding\n"
        "print(resolve_install_binding.cache_info().currsize)\n"
    )
    result = _run_import_subprocess(code)
    assert result.returncode == 0, (
        f"Subprocess crashed (rc={result.returncode}):\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert result.stdout.strip() == "1", (
        f"Importing autoskillit.{module} did not seal InstallBinding at import time "
        f"(cache_info().currsize={result.stdout.strip()!r})"
    )
