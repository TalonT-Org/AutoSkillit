"""Severity parity for the generation-prune and projection-cache reconcile loggers."""

from __future__ import annotations

from collections.abc import Callable
from enum import StrEnum
from pathlib import Path
from typing import Any

import pytest
from structlog.testing import capture_logs

from autoskillit.workspace._installed._projection_cache import (
    ProjectionReconcileDisposition,
    _log_projection_reconcile,
)
from autoskillit.workspace._projected_artifact._generation_prune import (
    _GenerationPruneDisposition,
    _log_generation_prune_reconcile,
)

pytestmark = [pytest.mark.layer("workspace"), pytest.mark.small]


def _levels(log_fn: Callable[..., None], enum: type[StrEnum], **kw: Any) -> dict[str, str]:
    levels: dict[str, str] = {}
    for member in enum:
        with capture_logs() as logs:
            log_fn(disposition=member, **kw)
        assert len(logs) == 1, member
        levels[member.value] = logs[0]["log_level"]
    return levels


def _generation_prune_levels(tmp_path: Path) -> dict[str, str]:
    return _levels(
        lambda **kw: _log_generation_prune_reconcile(tmp_path / "gen", **kw),
        _GenerationPruneDisposition,
    )


def _projection_reconcile_levels(tmp_path: Path) -> dict[str, str]:
    return _levels(
        lambda **kw: _log_projection_reconcile(tmp_path / "entry", **kw),
        ProjectionReconcileDisposition,
        active_key="k",
    )


def _warning_members(levels: dict[str, str]) -> set[str]:
    assert set(levels.values()) <= {"warning", "debug"}
    return {value for value, level in levels.items() if level == "warning"}


def test_generation_prune_levels(tmp_path: Path) -> None:
    assert _warning_members(_generation_prune_levels(tmp_path)) == {
        "deferred_io_error",
        "deferred_unavailable",
        "deferred_queue_unreadable",
    }


def test_projection_reconcile_levels(tmp_path: Path) -> None:
    assert _warning_members(_projection_reconcile_levels(tmp_path)) == {
        "deferred_unclassified",
        "deferred_io_error",
        "deferred_unavailable",
        "deferred_queue_unreadable",
    }


def test_counterpart_reconcile_levels_agree(tmp_path: Path) -> None:
    generation = _generation_prune_levels(tmp_path)
    projection = _projection_reconcile_levels(tmp_path)
    shared = generation.keys() & projection.keys()
    assert shared
    assert {value: generation[value] for value in shared} == {
        value: projection[value] for value in shared
    }
