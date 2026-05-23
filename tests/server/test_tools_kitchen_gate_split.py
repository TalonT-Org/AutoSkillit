"""Structural guard: validates kitchen gate test file split imports."""

import pytest

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


def test_open_kitchen_importable():
    from autoskillit.server.tools.tools_kitchen import open_kitchen

    assert callable(open_kitchen)


def test_close_kitchen_importable():
    from autoskillit.server.tools.tools_kitchen import close_kitchen

    assert callable(close_kitchen)
