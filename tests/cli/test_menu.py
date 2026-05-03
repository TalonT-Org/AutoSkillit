"""Tests: shared selection menu primitive."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from autoskillit.cli.ui._menu import (
    SLOT_ZERO_SELECTED,
    render_numbered_menu,
    resolve_menu_input,
    run_selection_menu,
)

pytestmark = [pytest.mark.layer("cli"), pytest.mark.medium]


def _make_item(name: str, tag: str = "") -> MagicMock:
    item = MagicMock()
    item.name = name
    item.tag = tag
    return item


class TestRenderNumberedMenu:
    def test_flat_list_renders_numbered_items(self, capsys: pytest.CaptureFixture[str]) -> None:
        items = [_make_item("alpha"), _make_item("beta"), _make_item("gamma")]
        render_numbered_menu(items, header="Items:")
        out = capsys.readouterr().out
        assert "1. alpha" in out
        assert "2. beta" in out
        assert "3. gamma" in out
        assert "Items:" in out

    def test_flat_list_no_group_headers(self, capsys: pytest.CaptureFixture[str]) -> None:
        items = [_make_item("alpha"), _make_item("beta"), _make_item("gamma")]
        render_numbered_menu(items, header="Items:")
        out = capsys.readouterr().out
        assert "Primary" not in out
        assert "Secondary" not in out

    def test_grouped_list_renders_group_headers(self, capsys: pytest.CaptureFixture[str]) -> None:
        items = [_make_item("a"), _make_item("b"), _make_item("c"), _make_item("d")]
        classifier = lambda item: 0 if item.name in ("a", "b") else 1  # noqa: E731
        render_numbered_menu(
            items,
            header="Items:",
            group_classifier=classifier,
            group_labels={0: "Primary", 1: "Secondary"},
        )
        out = capsys.readouterr().out
        assert "Primary" in out
        assert "Secondary" in out
        primary_pos = out.index("Primary")
        secondary_pos = out.index("Secondary")
        assert primary_pos < secondary_pos

    def test_slot_zero_renders_when_label_provided(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        items = [_make_item("alpha")]
        render_numbered_menu(items, header="Items:", slot_zero_label="Open kitchen")
        out = capsys.readouterr().out
        assert "0. Open kitchen" in out
        assert "1. alpha" in out

    def test_slot_zero_omitted_when_label_is_none(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        items = [_make_item("alpha")]
        render_numbered_menu(items, header="Items:", slot_zero_label=None)
        out = capsys.readouterr().out
        assert "0." not in out

    def test_custom_display_fn(self, capsys: pytest.CaptureFixture[str]) -> None:
        items = [_make_item("alpha", tag="fast"), _make_item("beta", tag="slow")]
        render_numbered_menu(items, header="Items:", display_fn=lambda x: f"{x.name} ({x.tag})")
        out = capsys.readouterr().out
        assert "alpha (fast)" in out
        assert "beta (slow)" in out


class TestResolveMenuInput:
    def _items(self) -> list[MagicMock]:
        return [_make_item("alpha"), _make_item("beta"), _make_item("gamma")]

    def test_valid_number_returns_item(self) -> None:
        items = self._items()
        result = resolve_menu_input("2", items)
        assert result is items[1]

    def test_slot_zero_returns_sentinel(self) -> None:
        items = self._items()
        result = resolve_menu_input("0", items, slot_zero=True)
        assert result is SLOT_ZERO_SELECTED

    def test_slot_zero_disabled_returns_none(self) -> None:
        items = self._items()
        result = resolve_menu_input("0", items, slot_zero=False)
        assert result is None

    def test_out_of_range_returns_none(self) -> None:
        items = self._items()
        result = resolve_menu_input("99", items)
        assert result is None

    def test_name_match_returns_item(self) -> None:
        items = self._items()
        result = resolve_menu_input("beta", items, name_key=lambda x: x.name)
        assert result is items[1]

    def test_name_no_match_returns_none(self) -> None:
        items = self._items()
        result = resolve_menu_input("nonexistent", items)
        assert result is None

    def test_empty_string_returns_none(self) -> None:
        items = self._items()
        result = resolve_menu_input("", items)
        assert result is None


class TestRunSelectionMenu:
    def test_end_to_end_numeric_selection(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        items = [_make_item("alpha"), _make_item("beta")]
        monkeypatch.setattr("autoskillit.cli.ui._menu.timed_prompt", lambda *a, **kw: "1")
        result = run_selection_menu(items, header="Items:")
        assert result is items[0]

    def test_end_to_end_slot_zero(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        items = [_make_item("alpha")]
        monkeypatch.setattr("autoskillit.cli.ui._menu.timed_prompt", lambda *a, **kw: "0")
        result = run_selection_menu(items, header="Items:", slot_zero_label="Open kitchen")
        assert result is SLOT_ZERO_SELECTED

    def test_end_to_end_invalid_returns_none(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        items = [_make_item("alpha")]
        monkeypatch.setattr("autoskillit.cli.ui._menu.timed_prompt", lambda *a, **kw: "invalid")
        result = run_selection_menu(items, header="Items:")
        assert result is None
