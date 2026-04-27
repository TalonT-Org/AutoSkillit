"""Tests for cli/_restart.py — NoReturn process restart contract."""

from __future__ import annotations

import os
import sys
from unittest.mock import patch

import pytest

pytestmark = [pytest.mark.layer("cli"), pytest.mark.small]


def test_perform_restart_sets_skip_env_and_execs() -> None:
    """perform_restart must set AUTOSKILLIT_SKIP_UPDATE_CHECK=1 then call os.execv."""
    from autoskillit.cli._restart import perform_restart

    captured: dict[str, object] = {}

    def fake_execv(exe: str, args: list[str]) -> None:
        assert os.environ.get("AUTOSKILLIT_SKIP_UPDATE_CHECK") == "1"
        captured["exe"] = exe
        captured["args"] = args
        raise SystemExit(0)

    with patch.dict(os.environ, {}, clear=False):
        with patch("autoskillit.cli._restart.os.execv", fake_execv):
            try:
                perform_restart()
            except SystemExit:
                pass

        assert captured["exe"] == sys.executable
        assert captured["args"] == [sys.executable] + sys.argv


def test_perform_restart_is_noreturn_typed() -> None:
    """perform_restart must be annotated as -> NoReturn."""
    import typing

    from autoskillit.cli._restart import perform_restart

    hints = typing.get_type_hints(perform_restart)
    assert hints.get("return") is typing.NoReturn
