"""Tests for tests/_test_filter.py — conftest filter plugin and shadow-diff tests."""

from __future__ import annotations

import pytest

pytest_plugins = ["pytester"]


# ---------------------------------------------------------------------------
# Conftest filter plugin – pytester integration tests (P1–P8)
# ---------------------------------------------------------------------------

_CONFTEST_HOOKS_SOURCE = """
import os
import warnings
import pytest
from pathlib import Path

_scope_key = pytest.StashKey[set | None]()

def pytest_addoption(parser):
    parser.addoption("--filter-mode", default=None,
                     choices=("none", "conservative", "aggressive"))
    parser.addoption("--filter-base-ref", default=None)

def pytest_configure(config):
    config.stash[_scope_key] = None
    cli_mode = config.getoption("--filter-mode", default=None)
    env_val = os.environ.get("AUTOSKILLIT_TEST_FILTER", "")
    if not cli_mode and not env_val:
        return
    if not cli_mode and env_val.lower() in ("0", "false", "no"):
        return
    try:
        mode = cli_mode or ("conservative" if env_val.lower() in ("1", "true", "yes") else env_val)
        if mode == "none":
            return
        # Stub scope: only include files under subdir_a/
        config.stash[_scope_key] = {config.rootpath / "subdir_a"}
    except Exception as exc:
        warnings.warn(f"Test filter setup failed: {exc}", stacklevel=1)

def pytest_collection_modifyitems(items, config):
    scope = config.stash.get(_scope_key, None)
    if scope is None:
        return
    try:
        selected, deselected = [], []
        for item in items:
            matched = any(
                item.path == sp if sp.is_file() else _is_under(item.path, sp)
                for sp in scope
            )
            (selected if matched else deselected).append(item)
        if deselected:
            config.hook.pytest_deselected(items=deselected)
            items[:] = selected
            warnings.warn(
                f"Test filter: {len(selected)} selected, {len(deselected)} deselected",
                stacklevel=1,
            )
    except Exception as exc:
        warnings.warn(f"Test filter deselection failed: {exc}", stacklevel=1)

def _is_under(path, parent):
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False
"""

_CONFTEST_ERROR_CONFIGURE_SOURCE = """
import os
import warnings
import pytest

_scope_key = pytest.StashKey[set | None]()

def pytest_addoption(parser):
    parser.addoption("--filter-mode", default=None)
    parser.addoption("--filter-base-ref", default=None)

def pytest_configure(config):
    config.stash[_scope_key] = None
    env_val = os.environ.get("AUTOSKILLIT_TEST_FILTER", "")
    if not env_val:
        return
    try:
        raise RuntimeError("simulated configure failure")
    except Exception as exc:
        warnings.warn(f"Test filter setup failed: {exc}", stacklevel=1)
"""

_CONFTEST_ERROR_MODIFYITEMS_SOURCE = """
import os
import warnings
import pytest

_scope_key = pytest.StashKey[set | None]()

def pytest_addoption(parser):
    parser.addoption("--filter-mode", default=None)
    parser.addoption("--filter-base-ref", default=None)

def pytest_configure(config):
    config.stash[_scope_key] = None
    env_val = os.environ.get("AUTOSKILLIT_TEST_FILTER", "")
    if env_val:
        config.stash[_scope_key] = {"will_cause_error"}

def pytest_collection_modifyitems(items, config):
    scope = config.stash.get(_scope_key, None)
    if scope is None:
        return
    try:
        raise RuntimeError("simulated modifyitems failure")
    except Exception as exc:
        warnings.warn(f"Test filter deselection failed: {exc}", stacklevel=1)
"""


class TestConftestFilterPlugin:
    """pytester-based integration tests for conftest filter hook wiring."""

    def test_filter_inactive_by_default(self, pytester: pytest.Pytester) -> None:
        pytester.makeconftest(_CONFTEST_HOOKS_SOURCE)
        pytester.makepyfile(test_a="def test_one(): pass", test_b="def test_two(): pass")
        result = pytester.runpytest("-v")
        result.assert_outcomes(passed=2)

    def test_filter_activates_with_env_var(
        self, pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("AUTOSKILLIT_TEST_FILTER", "1")
        pytester.makeconftest(_CONFTEST_HOOKS_SOURCE)
        pytester.mkdir("subdir_a")
        pytester.makepyfile(**{"subdir_a/test_a": "def test_one(): pass"})
        pytester.makepyfile(test_b="def test_two(): pass")
        result = pytester.runpytest("-v")
        result.assert_outcomes(passed=1, deselected=1)

    def test_deselection_reports_correctly(
        self, pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("AUTOSKILLIT_TEST_FILTER", "1")
        pytester.makeconftest(_CONFTEST_HOOKS_SOURCE)
        pytester.makepyfile(test_keep="def test_keep(): pass")
        pytester.makepyfile(test_drop="def test_drop(): pass")
        result = pytester.runpytest("-v")
        # Both are at root level, not under subdir_a — both deselected
        result.assert_outcomes(deselected=2)

    def test_fail_open_on_configure_error(
        self, pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("AUTOSKILLIT_TEST_FILTER", "1")
        pytester.makeconftest(_CONFTEST_ERROR_CONFIGURE_SOURCE)
        pytester.makepyfile(test_a="def test_one(): pass")
        result = pytester.runpytest("-v")
        result.assert_outcomes(passed=1)

    def test_fail_open_on_modifyitems_error(
        self, pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("AUTOSKILLIT_TEST_FILTER", "1")
        pytester.makeconftest(_CONFTEST_ERROR_MODIFYITEMS_SOURCE)
        pytester.makepyfile(test_a="def test_one(): pass")
        result = pytester.runpytest("-v")
        result.assert_outcomes(passed=1)

    def test_filter_mode_cli_flag(self, pytester: pytest.Pytester) -> None:
        pytester.makeconftest(_CONFTEST_HOOKS_SOURCE)
        pytester.makepyfile(test_a="def test_one(): pass")
        result = pytester.runpytest("--filter-mode=none", "-v")
        result.assert_outcomes(passed=1)

    def test_filter_base_ref_cli_flag(self, pytester: pytest.Pytester) -> None:
        pytester.makeconftest(_CONFTEST_HOOKS_SOURCE)
        pytester.makepyfile(test_a="def test_one(): pass")
        result = pytester.runpytest("--filter-base-ref=main", "-v")
        result.assert_outcomes(passed=1)

    def test_summary_warning_emitted(
        self, pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("AUTOSKILLIT_TEST_FILTER", "1")
        pytester.makeconftest(_CONFTEST_HOOKS_SOURCE)
        pytester.mkdir("subdir_a")
        pytester.makepyfile(**{"subdir_a/test_keep": "def test_keep(): pass"})
        pytester.makepyfile(test_drop="def test_drop(): pass")
        result = pytester.runpytest("-v", "-W", "always")
        result.stdout.fnmatch_lines(["*Test filter:*selected*deselected*"])


# ---------------------------------------------------------------------------
# Shadow-diff verification tests (SD1)
# ---------------------------------------------------------------------------


class TestShadowDiff:
    """Shadow-diff verification tests (SD1)."""

    @staticmethod
    def _missed(full_ids: list[str], filtered_ids: list[str]) -> list[str]:
        """Return sorted IDs present in full but absent from filtered."""
        return sorted(set(full_ids) - set(filtered_ids))

    def test_shadow_diff_detects_missed_tests(self) -> None:
        """IDs in full but not in filtered are 'missed'."""
        full_ids = [
            "tests/core/test_core.py::test_a",
            "tests/core/test_core.py::test_b",
            "tests/execution/test_headless.py::test_c",
            "tests/pipeline/test_gate.py::test_d",
            "tests/server/test_init.py::test_e",
        ]
        filtered_ids = [
            "tests/core/test_core.py::test_a",
            "tests/core/test_core.py::test_b",
            "tests/server/test_init.py::test_e",
        ]
        assert self._missed(full_ids, filtered_ids) == [
            "tests/execution/test_headless.py::test_c",
            "tests/pipeline/test_gate.py::test_d",
        ]

    def test_shadow_diff_no_missed_tests(self) -> None:
        """When filtered is a superset of full, no missed tests."""
        ids = [
            "tests/core/test_core.py::test_a",
            "tests/core/test_core.py::test_b",
        ]
        assert self._missed(ids, ids) == []

    def test_shadow_diff_empty_filtered(self) -> None:
        """When filter selects nothing, all full IDs are missed."""
        full_ids = sorted(["tests/core/test_core.py::test_a", "tests/core/test_core.py::test_b"])
        assert self._missed(full_ids, []) == full_ids
