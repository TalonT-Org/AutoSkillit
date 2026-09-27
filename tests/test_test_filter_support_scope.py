"""Changed tests/ support modules select their static test dependents (#5195)."""

from pathlib import Path

import pytest

from tests._test_filter import FilterMode, FullRunReason, build_test_scope

pytestmark = [pytest.mark.medium]

REPO_TESTS_ROOT = Path(__file__).resolve().parent


def _write(tests_root: Path, rel: str, source: str = "") -> Path:
    path = tests_root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")
    return path


def _package_tree(tmp_path: Path) -> Path:
    """Minimal packaged tests/ tree with an unrelated test in another package."""
    tests_root = tmp_path / "tests"
    _write(tests_root, "__init__.py")
    _write(tests_root, "conftest.py")
    _write(tests_root, "pkg/__init__.py")
    _write(tests_root, "other/__init__.py")
    _write(tests_root, "other/test_unrelated.py", "def test_unrelated():\n    pass\n")
    return tests_root


def test_filter_helper_change_selects_root_validators() -> None:
    result = build_test_scope(
        {"tests/_test_filter.py"}, FilterMode.CONSERVATIVE, tests_root=REPO_TESTS_ROOT
    )
    assert isinstance(result, set)
    assert REPO_TESTS_ROOT / "test_test_filter_core_cascade.py" in result
    assert set(REPO_TESTS_ROOT.glob("test_test_filter*.py")) <= result
    assert Path("tests/_test_filter.py") not in result


@pytest.mark.parametrize("mode", [FilterMode.CONSERVATIVE, FilterMode.AGGRESSIVE])
def test_undeclared_helper_selects_importing_tests(tmp_path: Path, mode: FilterMode) -> None:
    tests_root = _package_tree(tmp_path)
    helper = _write(tests_root, "pkg/_support.py", "VALUE = 1")
    target = _write(tests_root, "pkg/test_uses_support.py", "from tests.pkg._support import VALUE")
    result = build_test_scope({"tests/pkg/_support.py"}, mode, tests_root=tests_root)
    assert isinstance(result, set)
    assert target in result
    assert tests_root / "other/test_unrelated.py" not in result
    assert Path("tests/pkg/_support.py") not in result
    assert helper not in result


@pytest.mark.parametrize(
    "source",
    [
        "from tests.pkg._support import VALUE",
        "import tests.pkg._support",
        "from tests.pkg import _support",
        "from ._support import VALUE",
        "from . import _support",
        "def test_x():\n    from tests.pkg._support import VALUE\n",
        'TARGET = "tests.pkg._support.VALUE"',
    ],
    ids=[
        "absolute_from",
        "absolute_import",
        "package_from",
        "relative_from",
        "relative_package",
        "deferred",
        "dotted_literal",
    ],
)
def test_reference_forms_select_dependent(tmp_path: Path, source: str) -> None:
    tests_root = _package_tree(tmp_path)
    _write(tests_root, "pkg/_support.py", "VALUE = 1")
    target = _write(tests_root, "pkg/test_ref.py", source)
    result = build_test_scope(
        {"tests/pkg/_support.py"}, FilterMode.CONSERVATIVE, tests_root=tests_root
    )
    assert isinstance(result, set)
    assert target in result


def test_transitive_support_chain_selects_leaf_test(tmp_path: Path) -> None:
    tests_root = _package_tree(tmp_path)
    _write(tests_root, "pkg/_base.py", "X = 1")
    _write(tests_root, "pkg/_mid.py", "from tests.pkg._base import X\nY = X")
    target = _write(tests_root, "pkg/test_leaf.py", "from tests.pkg._mid import Y")
    result = build_test_scope(
        {"tests/pkg/_base.py"}, FilterMode.CONSERVATIVE, tests_root=tests_root
    )
    assert isinstance(result, set)
    assert target in result


def test_nested_conftest_dependent_selects_its_subtree(tmp_path: Path) -> None:
    tests_root = _package_tree(tmp_path)
    _write(tests_root, "pkg/_support.py", "VALUE = 1")
    _write(tests_root, "other/conftest.py", "from tests.pkg._support import VALUE")
    result = build_test_scope(
        {"tests/pkg/_support.py"}, FilterMode.CONSERVATIVE, tests_root=tests_root
    )
    assert isinstance(result, set)
    assert tests_root / "other" in result


def test_declared_dependent_contributes_declared_scope(tmp_path: Path) -> None:
    tests_root = _package_tree(tmp_path)
    _write(tests_root, "pkg/_support.py", "VALUE = 1")
    _write(tests_root, "fleet/__init__.py")
    _write(tests_root, "fleet/_reaper_test_support.py", "from tests.pkg._support import VALUE")
    _write(tests_root, "fleet/test_dispatch_reaper.py")
    result = build_test_scope(
        {"tests/pkg/_support.py"}, FilterMode.CONSERVATIVE, tests_root=tests_root
    )
    assert isinstance(result, set)
    assert tests_root / "fleet" in result


@pytest.mark.parametrize("namespace", [False, True], ids=["module", "namespace"])
def test_package_init_selects_member_dependents(tmp_path: Path, namespace: bool) -> None:
    tests_root = _package_tree(tmp_path)
    initializer = _write(tests_root, "fixtures/__init__.py")
    if namespace:
        (tests_root / "fixtures/namespace").mkdir()
        source = "import tests.fixtures.namespace"
    else:
        _write(tests_root, "fixtures/_data.py", "X = 1")
        source = "from tests.fixtures._data import X"
    target = _write(tests_root, "pkg/test_uses_fixture.py", source)
    result = build_test_scope(
        {"tests/fixtures/__init__.py"}, FilterMode.CONSERVATIVE, tests_root=tests_root
    )
    assert isinstance(result, set)
    assert target in result
    assert initializer not in result
    assert Path("tests/fixtures/__init__.py") not in result


@pytest.mark.parametrize("rel", ["pkg/test_member.py", "pkg/namespace/test_member.py"])
def test_package_init_selects_member_tests(tmp_path: Path, rel: str) -> None:
    tests_root = _package_tree(tmp_path)
    target = _write(tests_root, rel, "def test_member():\n    pass\n")
    result = build_test_scope(
        {"tests/pkg/__init__.py"}, FilterMode.CONSERVATIVE, tests_root=tests_root
    )
    assert isinstance(result, set)
    assert target in result


def test_helper_without_dependents_fails_open(tmp_path: Path) -> None:
    tests_root = _package_tree(tmp_path)
    _write(tests_root, "pkg/_orphan.py")
    result = build_test_scope(
        {"tests/pkg/_orphan.py"}, FilterMode.CONSERVATIVE, tests_root=tests_root
    )
    assert result is FullRunReason.UNSCOPED_TEST_SUPPORT


def test_helper_used_by_root_conftest_fails_open(tmp_path: Path) -> None:
    tests_root = _package_tree(tmp_path)
    _write(tests_root, "pkg/_support.py", "VALUE = 1")
    _write(tests_root, "conftest.py", "from tests.pkg._support import VALUE")
    result = build_test_scope(
        {"tests/pkg/_support.py"}, FilterMode.CONSERVATIVE, tests_root=tests_root
    )
    assert result is FullRunReason.UNSCOPED_TEST_SUPPORT


def test_root_package_init_fails_open(tmp_path: Path) -> None:
    tests_root = _package_tree(tmp_path)
    result = build_test_scope(
        {"tests/__init__.py"}, FilterMode.CONSERVATIVE, tests_root=tests_root
    )
    assert result is FullRunReason.UNSCOPED_TEST_SUPPORT


def test_unparseable_tree_fails_open(tmp_path: Path) -> None:
    tests_root = _package_tree(tmp_path)
    _write(tests_root, "pkg/_support.py", "VALUE = 1")
    _write(tests_root, "pkg/test_uses_support.py", "from tests.pkg._support import VALUE")
    _write(tests_root, "pkg/test_broken.py", "def (:")
    result = build_test_scope(
        {"tests/pkg/_support.py"}, FilterMode.CONSERVATIVE, tests_root=tests_root
    )
    assert result is FullRunReason.UNSCOPED_TEST_SUPPORT
