"""Tests for fleet.state.build_protected_campaign_ids (PROT_1–PROT_9)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from autoskillit.fleet.state import build_protected_campaign_ids

pytestmark = [pytest.mark.layer("fleet"), pytest.mark.feature("fleet"), pytest.mark.small]


def _write_dispatch_file(dispatches_dir: Path, filename: str, data: dict) -> None:
    dispatches_dir.mkdir(parents=True, exist_ok=True)
    (dispatches_dir / filename).write_text(json.dumps(data), encoding="utf-8")


def test_build_protected_ids_missing_dispatches_dir(tmp_path: Path) -> None:
    """PROT_1: Returns frozenset() when dispatches dir does not exist."""
    result = build_protected_campaign_ids(tmp_path)
    assert result == frozenset()


def test_build_protected_ids_pending_dispatch(tmp_path: Path) -> None:
    """PROT_2: Campaign with a pending dispatch is protected."""
    dispatches_dir = tmp_path / ".autoskillit" / "temp" / "dispatches"
    _write_dispatch_file(
        dispatches_dir,
        "campaign-abc.json",
        {
            "campaign_id": "campaign-abc",
            "dispatches": [{"name": "d1", "status": "pending"}],
        },
    )
    result = build_protected_campaign_ids(tmp_path)
    assert "campaign-abc" in result


def test_build_protected_ids_running_dispatch(tmp_path: Path) -> None:
    """PROT_3: Campaign with a running dispatch is protected."""
    dispatches_dir = tmp_path / ".autoskillit" / "temp" / "dispatches"
    _write_dispatch_file(
        dispatches_dir,
        "campaign-run.json",
        {
            "campaign_id": "campaign-run",
            "dispatches": [{"name": "d1", "status": "running"}],
        },
    )
    result = build_protected_campaign_ids(tmp_path)
    assert "campaign-run" in result


def test_build_protected_ids_interrupted_dispatch(tmp_path: Path) -> None:
    """PROT_4: Campaign with an interrupted dispatch is protected."""
    dispatches_dir = tmp_path / ".autoskillit" / "temp" / "dispatches"
    _write_dispatch_file(
        dispatches_dir,
        "campaign-int.json",
        {
            "campaign_id": "campaign-int",
            "dispatches": [{"name": "d1", "status": "interrupted"}],
        },
    )
    result = build_protected_campaign_ids(tmp_path)
    assert "campaign-int" in result


def test_build_protected_ids_all_terminal_not_protected(tmp_path: Path) -> None:
    """PROT_5: Campaign where all dispatches are terminal is NOT protected."""
    dispatches_dir = tmp_path / ".autoskillit" / "temp" / "dispatches"
    _write_dispatch_file(
        dispatches_dir,
        "campaign-done.json",
        {
            "campaign_id": "campaign-done",
            "dispatches": [
                {"name": "d1", "status": "success"},
                {"name": "d2", "status": "failure"},
                {"name": "d3", "status": "skipped"},
                {"name": "d4", "status": "released"},
            ],
        },
    )
    result = build_protected_campaign_ids(tmp_path)
    assert "campaign-done" not in result


def test_build_protected_ids_no_dispatches_list_protects(tmp_path: Path) -> None:
    """PROT_6: State file with campaign_id but empty dispatches list → protected conservatively."""
    dispatches_dir = tmp_path / ".autoskillit" / "temp" / "dispatches"
    _write_dispatch_file(
        dispatches_dir,
        "campaign-empty.json",
        {
            "campaign_id": "campaign-empty",
            "dispatches": [],
        },
    )
    result = build_protected_campaign_ids(tmp_path)
    assert "campaign-empty" in result


def test_build_protected_ids_corrupt_file_skipped(tmp_path: Path) -> None:
    """PROT_7: Corrupt JSON file is skipped without exception."""
    dispatches_dir = tmp_path / ".autoskillit" / "temp" / "dispatches"
    dispatches_dir.mkdir(parents=True, exist_ok=True)
    (dispatches_dir / "corrupt.json").write_text("{not valid json", encoding="utf-8")
    result = build_protected_campaign_ids(tmp_path)
    assert result == frozenset()


def test_build_protected_ids_missing_campaign_id_skipped(tmp_path: Path) -> None:
    """PROT_8: File with no campaign_id key is skipped."""
    dispatches_dir = tmp_path / ".autoskillit" / "temp" / "dispatches"
    _write_dispatch_file(
        dispatches_dir,
        "no-cid.json",
        {"dispatches": [{"name": "d1", "status": "running"}]},
    )
    result = build_protected_campaign_ids(tmp_path)
    assert result == frozenset()


def test_build_protected_ids_exported_from_fleet(tmp_path: Path) -> None:
    """PROT_9: build_protected_campaign_ids is importable from autoskillit.fleet."""
    from autoskillit.fleet import build_protected_campaign_ids as fn

    assert callable(fn)
    assert fn(tmp_path) == frozenset()
