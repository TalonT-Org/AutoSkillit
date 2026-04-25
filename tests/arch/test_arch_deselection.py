"""Tests for diff-aware parametrized deselection — REQ-ARCH-004."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from tests.arch._deselection import deselect_arch_items

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]

_ARCH_DIR = Path(__file__).parent
_SRC_ROOT = Path(__file__).parent.parent.parent / "src" / "autoskillit"


def _src(rel: str) -> Path:
    return (_SRC_ROOT / rel).resolve()


def _make_arch_item(source_file: Path) -> SimpleNamespace:
    item = SimpleNamespace()
    item.fspath = str(_ARCH_DIR / "test_ast_rules.py")
    item.callspec = SimpleNamespace(params={"source_file": source_file})
    return item


def _make_oneshot_item() -> SimpleNamespace:
    item = SimpleNamespace()
    item.fspath = str(_ARCH_DIR / "test_import_linter_contracts.py")
    return item  # no callspec


def _make_nonarch_item() -> SimpleNamespace:
    item = SimpleNamespace()
    item.fspath = str(_ARCH_DIR.parent / "core" / "test_paths.py")
    item.callspec = SimpleNamespace(params={"source_file": _src("core/paths.py")})
    return item


class TestDeselectArchItems:
    def test_only_changed_source_files_selected(self) -> None:
        a, b, c = _src("core/io.py"), _src("core/paths.py"), _src("core/logging.py")
        all_files = [a, b, c, _src("core/types.py"), _src("core/feature_flags.py")]
        items = [_make_arch_item(f) for f in all_files]
        selected, deselected = deselect_arch_items(items, {a, b, c}, _ARCH_DIR)
        assert len(selected) == 3
        assert len(deselected) == 2
        assert {i.callspec.params["source_file"] for i in selected} == {a, b, c}

    def test_oneshot_always_selected(self) -> None:
        oneshot = _make_oneshot_item()
        param_item = _make_arch_item(_src("core/io.py"))
        selected, deselected = deselect_arch_items([oneshot, param_item], set(), _ARCH_DIR)
        assert oneshot in selected
        assert param_item in deselected

    def test_nonarch_items_pass_through(self) -> None:
        nonarch = _make_nonarch_item()
        selected, deselected = deselect_arch_items([nonarch], set(), _ARCH_DIR)
        assert nonarch in selected
        assert not deselected

    def test_empty_changed_deselects_all_param_items(self) -> None:
        items = [_make_arch_item(_src(f"core/{n}.py")) for n in ("io", "paths", "logging")]
        selected, deselected = deselect_arch_items(items, set(), _ARCH_DIR)
        assert not selected
        assert len(deselected) == 3


class TestPytestCollectionModifyItemsHook:
    def test_filter_inactive_is_noop(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from tests.arch import conftest as arch_conftest

        monkeypatch.delenv("AUTOSKILLIT_TEST_FILTER", raising=False)
        items = [_make_arch_item(_src("core/io.py"))]
        original = list(items)
        mock_config = MagicMock()
        arch_conftest.pytest_collection_modifyitems(mock_config, items)
        mock_config.hook.pytest_deselected.assert_not_called()
        assert items == original

    def test_failopen_on_git_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from tests.arch import conftest as arch_conftest

        monkeypatch.setenv("AUTOSKILLIT_TEST_FILTER", "conservative")
        items = [_make_arch_item(_src("core/io.py"))]
        original = list(items)
        mock_config = MagicMock()
        with patch.object(arch_conftest, "git_changed_files", return_value=None):
            arch_conftest.pytest_collection_modifyitems(mock_config, items)
        mock_config.hook.pytest_deselected.assert_not_called()
        assert items == original

    def test_changed_files_deselects_unchanged_param_items(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from tests.arch import conftest as arch_conftest

        monkeypatch.setenv("AUTOSKILLIT_TEST_FILTER", "conservative")
        changed_rel = "src/autoskillit/core/io.py"
        changed = _src("core/io.py")
        unchanged = _src("core/paths.py")
        changed_item = _make_arch_item(changed)
        unchanged_item = _make_arch_item(unchanged)
        items = [changed_item, unchanged_item]
        mock_config = MagicMock()
        with patch.object(arch_conftest, "git_changed_files", return_value={changed_rel}):
            arch_conftest.pytest_collection_modifyitems(mock_config, items)
        mock_config.hook.pytest_deselected.assert_called_once_with(items=[unchanged_item])
        assert len(items) == 1
        assert items[0].callspec.params["source_file"] == changed
