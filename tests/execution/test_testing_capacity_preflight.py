"""B5: DefaultTestRunner.check_infrastructure detects capacity exhaustion via an injected
SpaceProbe."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from autoskillit.execution.testing import DefaultTestRunner

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]


def _low_space_probe(path: Path) -> tuple[int, int, int]:
    return (21_000_000_000, 20_999_999_000, 1_000)


def _plenty_space_probe(path: Path) -> tuple[int, int, int]:
    return (21_000_000_000, 1_000_000_000, 20_000_000_000)


def _runner(tmp_path: Path) -> DefaultTestRunner:
    config = MagicMock()
    config.test_check.effective_commands = [["task", "test-check"]]
    (tmp_path / "Taskfile.yml").write_text("version: '3'\n")
    return DefaultTestRunner(config, MagicMock())


def test_check_infrastructure_reports_capacity_exhaustion(tmp_path: Path) -> None:
    runner = _runner(tmp_path)

    result = runner.check_infrastructure(tmp_path, space_probe=_low_space_probe)

    assert result is not None
    assert "1000" in result
    assert "21000000000" in result


def test_check_infrastructure_passes_when_space_is_available(tmp_path: Path) -> None:
    runner = _runner(tmp_path)

    result = runner.check_infrastructure(tmp_path, space_probe=_plenty_space_probe)

    assert result is None
