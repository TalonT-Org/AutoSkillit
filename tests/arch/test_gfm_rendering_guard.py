"""Architectural guard: GFM table rendering must route through _render_gfm_table.

Prevents regression to ad-hoc inline width math in format_ingredients_table and
ensures all GFM ingredient column specs declare bounded max_width.
"""

from __future__ import annotations

import inspect


def test_format_ingredients_table_delegates_to_render_gfm_table():
    """format_ingredients_table must call _render_gfm_table (not inline width math).

    Fails if someone reverts to ad-hoc max(len(...)) computation inside the function.
    """
    from autoskillit.recipe._api import format_ingredients_table

    src = inspect.getsource(format_ingredients_table)
    assert "_render_gfm_table" in src, (
        "format_ingredients_table must delegate to _render_gfm_table. "
        "Reverting to inline width math is prohibited — it bypasses the L0 cap contract."
    )
    assert "max(len(" not in src, (
        "format_ingredients_table must not contain ad-hoc max(len(...)) width computation. "
        "Width capping belongs in _render_gfm_table at L0."
    )


def test_render_gfm_table_importable_from_core():
    """_render_gfm_table must be importable from autoskillit.core (exported surface check)."""
    from autoskillit.core import _render_gfm_table  # noqa: PLC0415

    assert callable(_render_gfm_table)


def test_gfm_ingredient_columns_all_have_bounded_max_width():
    """All _GFM_INGREDIENT_COLUMNS entries must declare a non-None max_width.

    Fails if any column is declared unbounded (max_width=None), which would allow
    overflow from long ingredient values.
    """
    from autoskillit.recipe._api import _GFM_INGREDIENT_COLUMNS
    from autoskillit.core import TerminalColumn

    assert len(_GFM_INGREDIENT_COLUMNS) > 0, "_GFM_INGREDIENT_COLUMNS must not be empty"
    for col in _GFM_INGREDIENT_COLUMNS:
        assert isinstance(col, TerminalColumn), (
            f"Column {col!r} is not a TerminalColumn instance"
        )
        assert col.max_width is not None, (
            f"Column '{col.label}' has max_width=None — all GFM ingredient columns "
            "must declare a bounded max_width to prevent overflow."
        )
