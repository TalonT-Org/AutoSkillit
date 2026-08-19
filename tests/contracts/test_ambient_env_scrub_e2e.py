"""End-to-end proof that the real root conftest.py scrubs ambient env leakage.

Unlike tests/test_conftest_feature_summary.py and tests/test_test_filter_plugin.py
(which write a hardcoded shadow reimplementation of conftest.py logic so the
subject under test can be exercised without importing the real conftest.py),
these two tests specifically need the REAL, production ``_scrub_ambient_env``
autouse fixture wired up end to end — a shadow copy would only prove the copy
works, not the production fixture. So the generated inner conftest.py instead
inserts the real repo root onto ``sys.path`` (computed from THIS file's own
real on-disk location, not the throwaway pytester file's location — the
generated conftest.py lives under pytester's own tmp rootdir, so a
``Path(__file__)`` evaluated inside it would resolve nowhere near the real
repo) and registers ``tests.conftest`` as a pytest plugin via
``pytest_plugins``. Plugin registration is required rather than
``from tests.conftest import *``: every autouse fixture in tests/conftest.py
is underscore-prefixed, and a wildcard import silently drops
underscore-prefixed names, so ``_scrub_ambient_env`` would never be
discovered that way.

Both tests use ``runpytest_subprocess`` (not the in-process ``runpytest``) —
the only way to observe a value injected into a REAL child process's
inherited environment, rather than the in-process test-runner's own
environment, get scrubbed or preserved.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest_plugins = ["pytester"]

pytestmark = [pytest.mark.medium]

_REPO_ROOT = str(Path(__file__).resolve().parents[2])


def _root_conftest_source() -> str:
    return f"import sys\nsys.path.insert(0, {_REPO_ROOT!r})\npytest_plugins = ['tests.conftest']\n"


def test_ambient_env_leak_is_scrubbed_end_to_end(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CLAUDE_CODE_EXECPATH", "/fake/claude")
    pytester.makeconftest(_root_conftest_source())
    pytester.makepyfile(
        test_leak="""
        import os

        def test_execpath_not_visible():
            assert os.environ.get("CLAUDE_CODE_EXECPATH") is None
        """
    )
    result = pytester.runpytest_subprocess(
        "-p",
        "no:cacheprovider",
    )
    result.assert_outcomes(passed=1)


def test_ambient_env_marker_suppresses_scrub(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CLAUDE_CODE_EXECPATH", "/fake/claude")
    pytester.makeconftest(_root_conftest_source())
    pytester.makepyfile(
        test_preserved="""
        import os
        import pytest

        @pytest.mark.ambient_env("CLAUDE_CODE_EXECPATH")
        def test_execpath_still_visible():
            assert os.environ.get("CLAUDE_CODE_EXECPATH") is not None
        """
    )
    result = pytester.runpytest_subprocess("-p", "no:cacheprovider")
    result.assert_outcomes(passed=1)
