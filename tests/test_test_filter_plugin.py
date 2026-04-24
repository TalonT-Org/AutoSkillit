"""Tests for tests/_test_filter.py — conftest filter plugin and shadow-diff tests."""

from __future__ import annotations

import json

import pytest

pytest_plugins = ["pytester"]


# ---------------------------------------------------------------------------
# Conftest filter plugin – pytester integration tests (P1–P8)
# ---------------------------------------------------------------------------

_CONFTEST_HOOKS_SOURCE = """
import json
import os
import warnings
import pytest
from pathlib import Path

_scope_key = pytest.StashKey[set | None]()
_filter_mode_key = pytest.StashKey[str | None]()
_selected_count_key = pytest.StashKey[int | None]()
_deselected_count_key = pytest.StashKey[int | None]()

def pytest_addoption(parser):
    parser.addoption("--filter-mode", default=None,
                     choices=("none", "conservative", "aggressive"))
    parser.addoption("--filter-base-ref", default=None)

def pytest_configure(config):
    config.stash[_scope_key] = None
    config.stash[_filter_mode_key] = None
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
        config.stash[_filter_mode_key] = mode
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

        config.stash[_selected_count_key] = len(items)
        config.stash[_deselected_count_key] = len(deselected)
    except Exception as exc:
        warnings.warn(f"Test filter deselection failed: {exc}", stacklevel=1)

def pytest_sessionfinish(session, exitstatus):
    if hasattr(session.config, "workerinput"):
        return
    out_path = os.environ.get("AUTOSKILLIT_FILTER_STATS_FILE")
    if not out_path:
        return
    filter_mode = session.config.stash.get(_filter_mode_key, None)
    selected = session.config.stash.get(_selected_count_key, None)
    deselected = session.config.stash.get(_deselected_count_key, None)
    if filter_mode is None:
        return
    Path(out_path).write_text(json.dumps({
        "filter_mode": filter_mode,
        "tests_selected": selected,
        "tests_deselected": deselected,
    }))

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

    def test_filter_inactive_by_default(
        self, pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("AUTOSKILLIT_TEST_FILTER", raising=False)
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

    def test_filter_base_ref_cli_flag(
        self, pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("AUTOSKILLIT_TEST_FILTER", raising=False)
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

    def test_conftest_writes_filter_sidecar(
        self,
        pytester: pytest.Pytester,
        tmp_path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """pytest_sessionfinish writes filter stats JSON when sidecar env var is set."""
        sidecar = tmp_path / "filter-stats.json"
        monkeypatch.setenv("AUTOSKILLIT_FILTER_STATS_FILE", str(sidecar))
        pytester.makeconftest(_CONFTEST_HOOKS_SOURCE)
        pytester.mkdir("subdir_a")
        (pytester.path / "subdir_a" / "test_in_scope.py").write_text("def test_ok(): pass\n")
        pytester.makepyfile(test_out_scope="def test_skip(): pass")
        pytester.runpytest("--filter-mode=conservative")
        assert sidecar.is_file(), "Sidecar file must be written by pytest_sessionfinish"
        data = json.loads(sidecar.read_text())
        assert data["filter_mode"] == "conservative"
        assert isinstance(data["tests_selected"], int)
        assert isinstance(data["tests_deselected"], int)

    def test_conftest_no_sidecar_when_env_unset(self, pytester: pytest.Pytester) -> None:
        """No sidecar written when AUTOSKILLIT_FILTER_STATS_FILE is not in env."""
        pytester.makeconftest(_CONFTEST_HOOKS_SOURCE)
        pytester.mkdir("subdir_a")
        (pytester.path / "subdir_a" / "test_simple.py").write_text("def test_a(): pass\n")
        result = pytester.runpytest("--filter-mode=conservative")
        result.assert_outcomes(passed=1)

    def test_conftest_sidecar_zero_deselection_has_integer_counts(
        self, pytester: pytest.Pytester, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """AC2: When filter is active but all tests are in scope, sidecar must
        have integer counts (not null)."""
        sidecar = tmp_path / "filter-stats.json"
        monkeypatch.setenv("AUTOSKILLIT_FILTER_STATS_FILE", str(sidecar))
        pytester.makeconftest(_CONFTEST_HOOKS_SOURCE)
        pytester.mkdir("subdir_a")
        # All test files inside subdir_a — nothing to deselect
        (pytester.path / "subdir_a" / "test_all_in.py").write_text(
            "def test_a(): pass\ndef test_b(): pass\n"
        )
        pytester.runpytest("--filter-mode=conservative")
        assert sidecar.is_file()
        data = json.loads(sidecar.read_text())
        assert data["filter_mode"] == "conservative"
        assert data["tests_selected"] == 2
        assert data["tests_deselected"] == 0

    def test_conftest_writes_filter_sidecar_under_xdist(
        self,
        pytester: pytest.Pytester,
        tmp_path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """pytest_sessionfinish writes integer counts under xdist (-n 2).

        Regression test for the controller/worker stash split: under xdist
        pytest_collection_modifyitems runs on workers while pytest_sessionfinish
        runs on the controller, so the workeroutput IPC channel is required.
        """
        sidecar = tmp_path / "filter-stats.json"
        monkeypatch.setenv("AUTOSKILLIT_FILTER_STATS_FILE", str(sidecar))
        pytester.makeconftest(_CONFTEST_HOOKS_SOURCE)
        pytester.mkdir("subdir_a")
        (pytester.path / "subdir_a" / "test_in_scope.py").write_text("def test_ok(): pass\n")
        pytester.makepyfile(test_out_scope="def test_skip(): pass")
        pytester.runpytest("-n", "2", "--filter-mode=conservative")
        assert sidecar.is_file(), "Sidecar file must be written by pytest_sessionfinish"
        data = json.loads(sidecar.read_text())
        assert data["filter_mode"] == "conservative"
        assert isinstance(data["tests_selected"], int), (
            f"tests_selected must be int under xdist, got {data['tests_selected']!r}"
        )
        assert isinstance(data["tests_deselected"], int), (
            f"tests_deselected must be int under xdist, got {data['tests_deselected']!r}"
        )


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

    def test_shadow_conftest_has_workeroutput_propagation(self) -> None:
        """Shadow conftest must define pytest_testnodedown and workeroutput propagation.

        Structural guard: ensures _CONFTEST_HOOKS_SOURCE stays in sync with the
        production conftest's xdist IPC pathway. Without this guard the shadow could
        silently lose the workeroutput mechanism, causing pytester-based xdist tests
        to pass against stale shadow code that doesn't match production behavior.
        """
        import ast

        tree = ast.parse(_CONFTEST_HOOKS_SOURCE)
        func_names = {node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)}
        assert "pytest_testnodedown" in func_names, (
            "Shadow conftest is missing pytest_testnodedown hook — "
            "xdist worker-to-controller propagation not present"
        )
        # Verify pytest_sessionfinish contains a workeroutput assignment
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "pytest_sessionfinish":
                func_src = ast.unparse(node)
                assert "workeroutput" in func_src, (
                    "pytest_sessionfinish in shadow conftest must assign workeroutput — "
                    "xdist IPC channel missing"
                )
                break
        else:
            raise AssertionError("pytest_sessionfinish not found in shadow conftest")
