"""Tests for tests/_test_filter.py — conftest filter plugin and shadow-diff tests."""

from __future__ import annotations

import json
import pathlib
import warnings
from pathlib import Path

import pytest

import tests.conftest as production_conftest
from tests._test_filter import FilterMode, FullRunReason, build_test_scope

pytest_plugins = ["pytester"]

pytestmark = [pytest.mark.medium]


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

# Module-level accumulator for xdist worker-to-controller IPC.
# Populated by pytest_testnodedown (controller); cleared by pytest_configure
# at session start so in-process pytester reruns don't leak stale data.
_worker_filter_counts: dict[str, int | None] = {}

def pytest_addoption(parser):
    parser.addoption("--filter-mode", default=None,
                     choices=("none", "conservative", "aggressive"))
    parser.addoption("--filter-base-ref", default=None)

def pytest_configure(config):
    _worker_filter_counts.clear()
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
        root = config.rootpath
        scope_abs = {sp if sp.is_absolute() else root / sp for sp in scope}
        selected, deselected = [], []
        file_scopes, ancestor_scopes = set(), set()
        for sp in scope_abs:
            (file_scopes if sp.is_file() else ancestor_scopes).add(sp)
        for item in items:
            matched = item.path in file_scopes or not ancestor_scopes.isdisjoint(
                item.path.parents
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
        session.config.workeroutput["filter_selected"] = session.config.stash.get(
            _selected_count_key, None
        )
        session.config.workeroutput["filter_deselected"] = session.config.stash.get(
            _deselected_count_key, None
        )
        return
    out_path = os.environ.get("AUTOSKILLIT_FILTER_STATS_FILE")
    if not out_path:
        return
    filter_mode = session.config.stash.get(_filter_mode_key, None)
    selected = session.config.stash.get(_selected_count_key, None)
    deselected = session.config.stash.get(_deselected_count_key, None)
    if selected is None and _worker_filter_counts:
        selected = _worker_filter_counts.get("selected")
    if deselected is None and _worker_filter_counts:
        deselected = _worker_filter_counts.get("deselected")
    if filter_mode is None:
        return
    Path(out_path).write_text(json.dumps({
        "filter_mode": filter_mode,
        "tests_selected": selected,
        "tests_deselected": deselected,
    }))

@pytest.hookimpl(optionalhook=True)
def pytest_testnodedown(node, error):
    '''Aggregate filter counts from the first xdist worker that reports.

    Mirrors production conftest.py: captures the first worker that reports both
    selected and deselected counts as non-None.
    '''
    if _worker_filter_counts:
        return
    wo = getattr(node, "workeroutput", {})
    selected = wo.get("filter_selected")
    deselected = wo.get("filter_deselected")
    if selected is not None and deselected is not None:
        _worker_filter_counts["selected"] = selected
        _worker_filter_counts["deselected"] = deselected
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
        # Use out-of-process subprocess mode (not runpytest_inprocess): xdist spawns
        # real worker subprocesses that communicate via workeroutput IPC, which cannot
        # be exercised in-process.  Resource contention risk is low because pytester
        # runs its own isolated pytest session in a temp directory.
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


# ---------------------------------------------------------------------------
# Real production-hook selection tests (T3–T5)
# ---------------------------------------------------------------------------


class _RecordingHook:
    """Records each pytest_deselected batch in call order."""

    def __init__(self) -> None:
        self.batches: list[list[str]] = []

    def pytest_deselected(self, items: list) -> None:
        self.batches.append([item.nodeid for item in items])


class _FakeConfig:
    """Minimal pytest.Config double for direct pytest_collection_modifyitems calls.

    Without a ``workerinput`` attribute the controller-only layer-marker pass also runs.
    """

    def __init__(self, rootpath: pathlib.Path) -> None:
        self.rootpath = rootpath
        self.stash = pytest.Stash()
        self.hook = _RecordingHook()


class _FakeMark:
    def __init__(self, name: str, args: tuple = (), kwargs: dict | None = None) -> None:
        self.name = name
        self.args = args
        self.kwargs = kwargs or {}


class _FakeItem:
    def __init__(self, path: pathlib.Path, nodeid: str, marks: tuple = ()) -> None:
        self.path = path
        self.nodeid = nodeid
        self.own_markers = list(marks)

    def iter_markers(self, name: str | None = None):
        for mark in self.own_markers:
            if name is None or mark.name == name:
                yield mark

    def get_closest_marker(self, name: str):
        for mark in self.own_markers:
            if mark.name == name:
                return mark
        return None

    def add_marker(self, marker) -> None:
        self.own_markers.append(marker)

    def skip_reasons(self) -> list[str]:
        return [
            getattr(mark, "kwargs", {}).get("reason")
            for mark in self.own_markers
            if mark.name == "skip"
        ]


def _normalize_scope(scope, rootpath: pathlib.Path) -> set[pathlib.Path]:
    return {p if p.is_absolute() else rootpath / p for p in scope}


def _legacy_path_partition(
    all_items: list[_FakeItem],
    scope,
    rootpath: pathlib.Path,
) -> tuple[list[str], list[str]]:
    """The pre-optimization per-pair path predicate, kept as a test-only reference."""
    scope_abs = _normalize_scope(scope, rootpath)
    selected: list[str] = []
    deselected: list[str] = []
    for item in all_items:
        matched = False
        for sp in scope_abs:
            if sp.is_file():
                if item.path == sp:
                    matched = True
                    break
            else:
                try:
                    item.path.relative_to(sp)
                    matched = True
                    break
                except ValueError:
                    continue
        (selected if matched else deselected).append(item.nodeid)
    return selected, deselected


def _run_hook(
    rootpath: pathlib.Path,
    all_items: list[_FakeItem],
    *,
    scope,
    mode: str,
    full_run_reason: str | None = None,
) -> dict:
    """Call the real production hook with fresh config/stash and observe the outcome."""
    items = list(all_items)
    config = _FakeConfig(rootpath)
    config.stash[production_conftest._scope_key] = scope
    if full_run_reason is not None:
        config.stash[production_conftest._full_run_reason_key] = full_run_reason
    config.stash[production_conftest._filter_mode_key] = mode

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        production_conftest.pytest_collection_modifyitems(items=items, config=config)

    return {
        "selected": [item.nodeid for item in items],
        "deselect_batches": list(config.hook.batches),
        "selected_count": config.stash.get(production_conftest._selected_count_key, None),
        "deselected_count": config.stash.get(production_conftest._deselected_count_key, None),
        "full_run_reason": config.stash.get(production_conftest._full_run_reason_key, None),
        "skips": {item.nodeid: item.skip_reasons() for item in all_items},
        "warnings": [str(w.message) for w in caught],
    }


@pytest.fixture
def scope_is_file_calls(monkeypatch: pytest.MonkeyPatch) -> list[pathlib.Path]:
    """Record every Path.is_file receiver; callers filter to normalized scope entries."""
    calls: list[pathlib.Path] = []
    original = pathlib.Path.is_file

    def _counting_is_file(self, *args, **kwargs):
        calls.append(self)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(pathlib.Path, "is_file", _counting_is_file)
    return calls


class _HookTree:
    """A concrete tests/ tree plus the fixed scope used by the real-hook tests."""

    LAYER_DIRS = (
        "core",
        "config",
        "execution",
        "pipeline",
        "workspace",
        "recipe",
        "migration",
        "server",
        "cli",
        "hooks",
        "skills",
        "arch",
        "contracts",
        "infra",
        "docs",
    )
    FILES = (
        "tests/core/test_io.py",
        "tests/core/test_other.py",
        "tests/core/test_large.py",
        "tests/core/test_unannotated.py",
        "tests/config/test_settings.py",
        "tests/config/test_other.py",
        "tests/docs/test_out.py",
        "tests/docs/test_doc_counts.py",
        "tests/arch/test_guard.py",
        "tests/contracts/test_contract.py",
        "tests/infra/test_manifest_completeness.py",
        "tests/hooks/test_hook_registry.py",
        "tests/server/test_server.py",
    )

    def __init__(self, root: pathlib.Path) -> None:
        self.root = root
        self.tests = root / "tests"
        for name in self.LAYER_DIRS:
            (self.tests / name).mkdir(parents=True, exist_ok=True)
        for rel in self.FILES:
            (root / rel).write_text("")

    @property
    def scope(self) -> set[pathlib.Path]:
        """Relative/absolute duplicates, overlapping dir/file scopes, a missing target."""
        return {
            Path("tests/core"),
            self.tests / "core",
            self.tests / "core" / "test_io.py",
            self.tests / "config" / "test_settings.py",
            Path("tests/missing"),
        }

    @property
    def scope_abs(self) -> set[pathlib.Path]:
        return _normalize_scope(self.scope, self.root)

    def items(self, specs) -> list[_FakeItem]:
        return [_FakeItem(self.root / rel, f"{rel}::{name}", marks) for rel, name, marks in specs]


@pytest.fixture
def hook_tree(tmp_path: pathlib.Path) -> _HookTree:
    return _HookTree(tmp_path)


_SMALL = (_FakeMark("small"),)
_MEDIUM = (_FakeMark("medium"),)
_LARGE = (_FakeMark("large"),)

# Self and descendant items for the missing target, a synthetic descendant of a
# regular-file scope, and out-of-scope siblings.
_T3_SPECS = (
    ("tests/core/test_io.py", "test_a", _SMALL),
    ("tests/core/test_other.py", "test_b", _SMALL),
    ("tests/config/test_settings.py", "test_c", _SMALL),
    ("tests/config/test_settings.py/sub/test_nested.py", "test_d", _SMALL),
    ("tests/missing", "test_e", _SMALL),
    ("tests/missing/test_f.py", "test_f", _SMALL),
    ("tests/docs/test_out.py", "test_g", _SMALL),
    ("tests/config/test_other.py", "test_h", _SMALL),
)

_T3_EXPECTED_SELECTED = [
    "tests/core/test_io.py::test_a",
    "tests/core/test_other.py::test_b",
    "tests/config/test_settings.py::test_c",
    "tests/missing::test_e",
    "tests/missing/test_f.py::test_f",
]
_T3_EXPECTED_DESELECTED = [
    "tests/config/test_settings.py/sub/test_nested.py::test_d",
    "tests/docs/test_out.py::test_g",
    "tests/config/test_other.py::test_h",
]


class TestRealHookScopeSelection:
    """T3 — selection equivalence and per-invocation classification work."""

    def test_selection_matches_legacy_predicate(self, hook_tree: _HookTree) -> None:
        items = hook_tree.items(_T3_SPECS)
        legacy_selected, legacy_deselected = _legacy_path_partition(
            items, hook_tree.scope, hook_tree.root
        )

        observation = _run_hook(hook_tree.root, items, scope=hook_tree.scope, mode="conservative")

        assert observation["selected"] == _T3_EXPECTED_SELECTED
        assert observation["deselect_batches"] == [_T3_EXPECTED_DESELECTED]
        assert legacy_selected == _T3_EXPECTED_SELECTED
        assert legacy_deselected == _T3_EXPECTED_DESELECTED
        assert observation["selected_count"] == len(_T3_EXPECTED_SELECTED)
        assert observation["deselected_count"] == len(_T3_EXPECTED_DESELECTED)

    @pytest.mark.parametrize("extra_items", [0, 16], ids=["base", "wide"])
    def test_classification_count_is_independent_of_item_count(
        self,
        hook_tree: _HookTree,
        scope_is_file_calls: list[pathlib.Path],
        extra_items: int,
    ) -> None:
        specs = list(_T3_SPECS) + [
            ("tests/docs/test_out.py", f"test_extra_{i}", _SMALL) for i in range(extra_items)
        ]
        items = hook_tree.items(specs)
        scope = hook_tree.scope
        scope_abs = hook_tree.scope_abs

        observation = _run_hook(hook_tree.root, items, scope=scope, mode="conservative")

        classified = [p for p in scope_is_file_calls if p in scope_abs]
        assert len(classified) == len(scope_abs)
        assert len(scope_abs) != len(scope), (
            "fixture must contain duplicate relative/absolute spellings"
        )
        assert f"({len(scope)} scope paths)" in observation["warnings"][0]
        assert observation["selected"] == _T3_EXPECTED_SELECTED
        assert observation["deselected_count"] == len(_T3_EXPECTED_DESELECTED) + extra_items

    def test_empty_concrete_scope_deselects_everything(
        self, hook_tree: _HookTree, scope_is_file_calls: list[pathlib.Path]
    ) -> None:
        items = hook_tree.items(_T3_SPECS)

        observation = _run_hook(hook_tree.root, items, scope=set(), mode="conservative")

        assert scope_is_file_calls == []
        assert observation["selected"] == []
        assert observation["deselect_batches"] == [[item.nodeid for item in items]]
        assert observation["selected_count"] == 0
        assert observation["deselected_count"] == len(_T3_SPECS)


_GATED_FEATURE = "gated_feature"

_T4_SPECS = (
    ("tests/core/test_io.py", "test_a", _SMALL),
    ("tests/core/test_other.py", "test_b", _MEDIUM),
    ("tests/core/test_large.py", "test_c", _LARGE),
    ("tests/core/test_unannotated.py", "test_d", ()),
    ("tests/arch/test_guard.py", "test_e", ()),
    ("tests/contracts/test_contract.py", "test_f", ()),
    ("tests/docs/test_out.py", "test_g", _SMALL),
    ("tests/config/test_other.py", "test_h", _SMALL),
    (
        "tests/core/test_feature.py",
        "test_i",
        (_FakeMark("feature", (_GATED_FEATURE,)), _FakeMark("small")),
    ),
)

_T4_ALL = [f"{rel}::{name}" for rel, name, _ in _T4_SPECS]
_T4_FEATURE_ID = "tests/core/test_feature.py::test_i"
_T4_SKIP_REASON = f"feature '{_GATED_FEATURE}' disabled via config resolution"
_T4_PATH_DESELECTED = ["tests/config/test_other.py::test_h"]
_T4_SIZE_DESELECTED = [
    "tests/core/test_large.py::test_c",
    "tests/core/test_unannotated.py::test_d",
]
_T4_CONSERVATIVE_SELECTED = [i for i in _T4_ALL if i not in _T4_PATH_DESELECTED]
_T4_AGGRESSIVE_SELECTED = [i for i in _T4_CONSERVATIVE_SELECTED if i not in _T4_SIZE_DESELECTED]


@pytest.fixture
def gated_feature(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Disable exactly one feature on the production module and record every query."""
    monkeypatch.delenv("AUTOSKILLIT_TEST_FEATURES", raising=False)
    queried: list[str] = []

    def _fake_enabled(feature_name: str, *, env_val: str | None = None) -> bool:
        queried.append(feature_name)
        return feature_name != _GATED_FEATURE

    monkeypatch.setattr(production_conftest, "_is_test_feature_enabled", _fake_enabled)
    return queried


def _t4_scope(tree: _HookTree) -> set[pathlib.Path]:
    return tree.scope | {tree.tests / "arch", tree.tests / "contracts", tree.tests / "docs"}


class TestRealHookConfigurationBoundaries:
    """T4 — configuration, feature, size, and failure boundaries."""

    def test_conservative_keeps_all_path_selected_items(
        self, hook_tree: _HookTree, gated_feature: list[str]
    ) -> None:
        items = hook_tree.items(_T4_SPECS)

        observation = _run_hook(
            hook_tree.root, items, scope=_t4_scope(hook_tree), mode="conservative"
        )

        assert observation["selected"] == _T4_CONSERVATIVE_SELECTED
        assert observation["deselect_batches"] == [_T4_PATH_DESELECTED]
        assert observation["selected_count"] == len(_T4_CONSERVATIVE_SELECTED)
        assert observation["deselected_count"] == len(_T4_PATH_DESELECTED)
        assert observation["skips"][_T4_FEATURE_ID] == [_T4_SKIP_REASON]

    def test_aggressive_applies_size_filter_after_path_filter(
        self, hook_tree: _HookTree, gated_feature: list[str]
    ) -> None:
        from autoskillit.core import FEATURE_REGISTRY

        items = hook_tree.items(_T4_SPECS)

        observation = _run_hook(
            hook_tree.root, items, scope=_t4_scope(hook_tree), mode="aggressive"
        )

        assert observation["deselect_batches"] == [_T4_PATH_DESELECTED, _T4_SIZE_DESELECTED]
        assert observation["selected"] == _T4_AGGRESSIVE_SELECTED
        assert observation["selected_count"] == len(_T4_AGGRESSIVE_SELECTED)
        assert observation["deselected_count"] == len(_T4_PATH_DESELECTED) + len(
            _T4_SIZE_DESELECTED
        )
        assert set(FEATURE_REGISTRY) <= set(gated_feature)
        assert observation["skips"][_T4_FEATURE_ID] == [_T4_SKIP_REASON]

    def test_scope_none_marks_features_without_filtering(
        self, hook_tree: _HookTree, gated_feature: list[str]
    ) -> None:
        items = hook_tree.items(_T4_SPECS)

        observation = _run_hook(
            hook_tree.root,
            items,
            scope=None,
            mode="aggressive",
            full_run_reason=FullRunReason.BUCKET_A.value,
        )

        assert observation["selected"] == _T4_ALL
        assert observation["deselect_batches"] == []
        assert observation["selected_count"] is None
        assert observation["deselected_count"] is None
        assert observation["full_run_reason"] == FullRunReason.BUCKET_A.value
        assert observation["skips"][_T4_FEATURE_ID] == [_T4_SKIP_REASON]

    @pytest.mark.parametrize(
        "mode,expected_selected,expected_batches",
        [
            ("conservative", _T4_ALL, []),
            (
                "aggressive",
                [i for i in _T4_ALL if i not in _T4_SIZE_DESELECTED],
                [_T4_SIZE_DESELECTED],
            ),
        ],
        ids=["conservative", "aggressive"],
    )
    def test_classification_failure_fails_open(
        self,
        hook_tree: _HookTree,
        gated_feature: list[str],
        monkeypatch: pytest.MonkeyPatch,
        mode: str,
        expected_selected: list[str],
        expected_batches: list[list[str]],
    ) -> None:
        """A classification error leaves path selection untouched; size filtering still runs."""
        original = pathlib.Path.is_file
        target = hook_tree.tests / "core" / "test_io.py"

        def _raising_is_file(self, *args, **kwargs):
            if self == target:
                raise OSError("classification failed")
            return original(self, *args, **kwargs)

        monkeypatch.setattr(pathlib.Path, "is_file", _raising_is_file)
        items = hook_tree.items(_T4_SPECS)

        observation = _run_hook(hook_tree.root, items, scope=_t4_scope(hook_tree), mode=mode)

        assert any(
            "Test filter deselection failed, running all tests" in message
            for message in observation["warnings"]
        )
        assert observation["selected"] == expected_selected
        assert observation["deselect_batches"] == expected_batches

    def test_target_type_change_is_not_carried_between_configurations(
        self, hook_tree: _HookTree
    ) -> None:
        """A missing target becoming a regular file switches to exact-only matching."""
        scope = hook_tree.scope
        before = _run_hook(
            hook_tree.root, hook_tree.items(_T3_SPECS), scope=scope, mode="conservative"
        )
        assert before["selected"] == _T3_EXPECTED_SELECTED

        (hook_tree.tests / "missing").write_text("")

        legacy_selected, legacy_deselected = _legacy_path_partition(
            hook_tree.items(_T3_SPECS), scope, hook_tree.root
        )
        after = _run_hook(
            hook_tree.root, hook_tree.items(_T3_SPECS), scope=scope, mode="conservative"
        )

        descendant = "tests/missing/test_f.py::test_f"
        assert after["selected"] == [i for i in _T3_EXPECTED_SELECTED if i != descendant]
        assert after["selected"] == legacy_selected
        assert after["deselect_batches"] == [legacy_deselected]
        assert descendant in legacy_deselected
        assert "tests/missing::test_e" in after["selected"]


_T5_ARTIFACT_MANIFEST = {"docs/**/*.md": ["docs"], "docs/README.md": ["config"]}

_T5_SPECS = (
    ("tests/core/test_io.py", "test_core_direct", _SMALL),
    ("tests/core/test_other.py", "test_core_sibling", _SMALL),
    ("tests/config/test_settings.py", "test_config", _SMALL),
    ("tests/docs/test_doc_counts.py", "test_docs_guard", _SMALL),
    ("tests/infra/test_manifest_completeness.py", "test_infra_guard", _SMALL),
    ("tests/hooks/test_hook_registry.py", "test_hooks_guard", _SMALL),
    ("tests/arch/test_guard.py", "test_arch_guard", _SMALL),
    ("tests/contracts/test_contract.py", "test_contracts_guard", _SMALL),
    ("tests/server/test_server.py", "test_server", _SMALL),
)

_T5_ALL = [f"{rel}::{name}" for rel, name, _ in _T5_SPECS]

# (changed_files, manifest, mode, expected_reason, required root-relative scope entries)
_T5_ROWS = [
    (
        {"src/autoskillit/core/io.py"},
        None,
        FilterMode.CONSERVATIVE,
        None,
        {"tests/core", "tests/config", "tests/arch", "tests/contracts"},
    ),
    (
        {"src/autoskillit/core/io.py"},
        None,
        FilterMode.AGGRESSIVE,
        None,
        {"tests/core", "tests/arch", "tests/contracts"},
    ),
    (
        {"tests/core/test_io.py"},
        None,
        FilterMode.CONSERVATIVE,
        None,
        {"tests/core/test_io.py", "tests/arch", "tests/contracts"},
    ),
    (
        {"tests/core/test_io.py"},
        None,
        FilterMode.AGGRESSIVE,
        None,
        {"tests/core/test_io.py", "tests/arch", "tests/contracts"},
    ),
    (
        {"docs/README.md", "docs/deep/guide.md"},
        _T5_ARTIFACT_MANIFEST,
        FilterMode.CONSERVATIVE,
        None,
        {"tests/docs", "tests/config", "tests/arch", "tests/contracts"},
    ),
    (
        {"docs/README.md", "docs/deep/guide.md"},
        _T5_ARTIFACT_MANIFEST,
        FilterMode.AGGRESSIVE,
        None,
        {"tests/docs", "tests/config", "tests/arch", "tests/contracts"},
    ),
    (
        {"pyproject.toml"},
        _T5_ARTIFACT_MANIFEST,
        FilterMode.CONSERVATIVE,
        FullRunReason.BUCKET_A,
        set(),
    ),
    (
        {"some/unknown/file.txt"},
        _T5_ARTIFACT_MANIFEST,
        FilterMode.CONSERVATIVE,
        FullRunReason.UNMAPPED_FILE,
        set(),
    ),
    (
        {"some/unknown/file.txt"},
        _T5_ARTIFACT_MANIFEST,
        FilterMode.AGGRESSIVE,
        FullRunReason.UNMAPPED_FILE,
        set(),
    ),
]

_T5_IDS = [
    "core_cascade_conservative",
    "core_cascade_aggressive",
    "changed_test_conservative",
    "changed_test_aggressive",
    "manifest_artifact_conservative",
    "manifest_artifact_aggressive",
    "bucket_a_conservative",
    "unmapped_conservative",
    "unmapped_aggressive",
]


class TestRealHookSelectorParity:
    """T5 — representative end-to-end selector parity through the real hook."""

    @pytest.mark.parametrize(
        "changed_files,manifest,mode,expected_reason,required_entries",
        _T5_ROWS,
        ids=_T5_IDS,
    )
    def test_selector_route_drives_equivalent_selection(
        self,
        hook_tree: _HookTree,
        changed_files: set[str],
        manifest: dict | None,
        mode: FilterMode,
        expected_reason: FullRunReason | None,
        required_entries: set[str],
    ) -> None:
        result = build_test_scope(
            changed_files=changed_files,
            mode=mode,
            manifest=manifest,
            tests_root=hook_tree.tests,
        )

        if expected_reason is not None:
            assert result is expected_reason
            observation = _run_hook(
                hook_tree.root,
                hook_tree.items(_T5_SPECS),
                scope=None,
                mode=mode.value,
                full_run_reason=result.value,
            )
            assert observation["full_run_reason"] == expected_reason.value
            assert observation["selected"] == _T5_ALL
            assert observation["deselect_batches"] == []
            return

        assert isinstance(result, set)
        normalized = _normalize_scope(result, hook_tree.root)
        assert {hook_tree.root / rel for rel in required_entries} <= normalized

        legacy_selected, legacy_deselected = _legacy_path_partition(
            hook_tree.items(_T5_SPECS), result, hook_tree.root
        )
        observation = _run_hook(
            hook_tree.root, hook_tree.items(_T5_SPECS), scope=result, mode=mode.value
        )

        assert observation["full_run_reason"] is None
        assert observation["selected"] == legacy_selected
        assert observation["deselect_batches"] == (
            [legacy_deselected] if legacy_deselected else []
        )
        assert observation["selected_count"] == len(legacy_selected)
        assert observation["deselected_count"] == len(legacy_deselected)
