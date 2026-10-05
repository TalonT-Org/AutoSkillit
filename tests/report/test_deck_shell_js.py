"""Browser-free contract tests for the observability deck's shell."""

from typing import Any

import pytest
from py_mini_racer import MiniRacer

from autoskillit.report.deck._html import deck_script_assets
from autoskillit.report.deck._registry import DECK_VIEWS

pytestmark = [pytest.mark.small]


def test_shell_registers_built_views_without_document(deck_asset: Any) -> None:
    with MiniRacer() as ctx:
        assert ctx.eval("typeof document") == "undefined"
        for rel in deck_script_assets():
            ctx.eval(deck_asset(rel))
        registered = ctx.call("DeckShell.registeredViews")

    expected = [view.view_id for view in DECK_VIEWS if view.planned_issue is None]
    assert sorted(registered) == sorted(expected)
