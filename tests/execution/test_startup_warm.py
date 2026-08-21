"""Tests for the startup-warm eager-import mechanism (A-10, issue #4597).

``autoskillit.fleet.warm_failure_path_imports()`` is called from
the cook, headless, and fleet-dispatch entry points (``cli/app.py``,
``server/tools/tools_execution.py``, ``fleet/_api.py``) to preload every
module a genuine except/finally-scoped ``autoskillit`` import can reach, so a
crash-handling path is never itself the first resolution of a fresh
``sys.path`` lookup after the install tree has been replaced mid-session.

This must be verified in a subprocess with a *fresh* interpreter. By the time
an in-process pytest test runs, collection has already imported most of
autoskillit's tree (very likely all of ``WARM_MODULE_NAMES``), so an
in-process assertion that "these modules are in ``sys.modules`` after calling
``warm_failure_path_imports()``" would trivially pass even against a broken
or no-op implementation. Mirrors the subprocess-isolation idiom established by
``tests/core/test_import_isolation.py``.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time

import pytest

from tests.core.test_import_isolation import _clean_subprocess_env

pytestmark = [pytest.mark.layer("execution"), pytest.mark.medium]

_WARM_SUBPROCESS_SCRIPT = """
import json
import sys

from autoskillit.fleet import WARM_MODULE_NAMES, warm_failure_path_imports
for module_name in WARM_MODULE_NAMES:
    sys.modules.pop(module_name, None)
before = set(sys.modules)
warm_failure_path_imports()
after = set(sys.modules)

print(json.dumps({
    "warm_module_names": list(WARM_MODULE_NAMES),
    "newly_imported": sorted(n for n in WARM_MODULE_NAMES if n not in before and n in after),
    "present_after": sorted(n for n in WARM_MODULE_NAMES if n in after),
}))
"""


def _run_warm_subprocess() -> subprocess.CompletedProcess[str]:
    """Run the startup-warm probe script in a subprocess resilient to venv churn.

    Under xdist, sibling workers may trigger ``uv sync`` mid-run, swapping
    ``.dist-info`` directories in the shared ``.venv`` and causing
    ``PackageNotFoundError`` in third-party ``__init__.py`` import-time
    metadata lookups. Retry once, matching ``test_import_isolation.py``.
    """
    env = _clean_subprocess_env()
    args = [sys.executable, "-c", _WARM_SUBPROCESS_SCRIPT]
    result = subprocess.run(args, capture_output=True, text=True, env=env, timeout=30)
    if result.returncode != 0 and (
        "PackageNotFoundError" in result.stderr or "No module named" in result.stderr
    ):
        time.sleep(1)
        result = subprocess.run(args, capture_output=True, text=True, env=env, timeout=30)
    return result


def test_failure_path_modules_are_preloaded_at_startup() -> None:
    """warm_failure_path_imports() must genuinely import every WARM_MODULE_NAMES entry.

    Two assertions:

    1. At least one warm module was absent from ``sys.modules`` before the
       entry-point import/call and present after.
    2. Every module named in ``WARM_MODULE_NAMES`` is present in
       ``sys.modules`` after the call, regardless of before/after diff
       specifics -- the actual load-bearing contract this function exists
       to satisfy.
    """
    result = _run_warm_subprocess()
    assert result.returncode == 0, (
        f"Subprocess crashed (rc={result.returncode}):\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise AssertionError(
            f"Subprocess did not print valid JSON: {exc}\nstdout: {result.stdout}\n"
            f"stderr: {result.stderr}"
        ) from exc

    warm_module_names = payload["warm_module_names"]
    assert warm_module_names, "WARM_MODULE_NAMES is empty -- nothing to warm"

    assert payload["newly_imported"], (
        "warm_failure_path_imports() did not newly import any WARM_MODULE_NAMES "
        f"entry -- the minimal baseline import already satisfied all of them, so "
        f"this run cannot prove the call itself does the work: {payload}"
    )

    missing = sorted(set(warm_module_names) - set(payload["present_after"]))
    assert not missing, (
        f"warm_failure_path_imports() left these WARM_MODULE_NAMES entries out of "
        f"sys.modules after the call: {missing}"
    )
