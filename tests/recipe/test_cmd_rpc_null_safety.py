"""Null-safety tests for run_python callables in recipe/_cmd_rpc.py."""

from __future__ import annotations

import pytest

from autoskillit.recipe._cmd_rpc import check_dropped_healthy_loop, check_eject_limit

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]


def test_check_eject_limit_none_max(tmp_path) -> None:
    counter = tmp_path / "counter"
    result = check_eject_limit(counter_file=str(counter), max_ejects=None)  # type: ignore[arg-type]
    assert "status" in result


def test_check_dropped_healthy_loop_none_max(tmp_path) -> None:
    counter = tmp_path / "counter"
    result = check_dropped_healthy_loop(counter_file=str(counter), max_drops=None)  # type: ignore[arg-type]
    assert "status" in result
