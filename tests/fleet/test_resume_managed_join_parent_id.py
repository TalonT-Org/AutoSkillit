"""Direct coverage for ``resume_managed_join_parent_id`` identity restoration."""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("fleet"), pytest.mark.feature("fleet"), pytest.mark.small]


def _state_path(tmp_path: Path) -> Path:
    return tmp_path / ".autoskillit" / "fleet" / "campaign_state" / "campaign.json"


def _seed_dispatch(
    state_path: Path,
    *,
    name: str,
    launch_id: str | None,
) -> None:
    from autoskillit.core import ManagedHeadlessSessionLineageRef
    from autoskillit.fleet.campaign_state.state import (
        DispatchRecord,
        write_initial_state,
    )

    reference = (
        ManagedHeadlessSessionLineageRef(
            launch_id=launch_id,
            lineage_digest="d" * 64,
            lineage_anchor=str(state_path.resolve()),
            anchor_device=1,
            anchor_inode=2,
        )
        if launch_id is not None
        else None
    )
    write_initial_state(
        state_path,
        "cid",
        "test-campaign",
        "/m.yaml",
        [
            DispatchRecord(
                name=name,
                campaign_id="cid",
                caller_session_id="caller",
                managed_lineage_ref=reference,
            )
        ],
    )


def test_resume_returns_launch_id_when_dispatch_has_managed_lineage(tmp_path: Path) -> None:
    from autoskillit.fleet.dispatch._lineage import resume_managed_join_parent_id

    state_path = _state_path(tmp_path)
    _seed_dispatch(state_path, name="phase-one", launch_id="a" * 32)

    assert resume_managed_join_parent_id(state_path, "phase-one") == "a" * 32


def test_resume_returns_none_when_state_file_missing(tmp_path: Path) -> None:
    from autoskillit.fleet.dispatch._lineage import resume_managed_join_parent_id

    state_path = _state_path(tmp_path)

    assert resume_managed_join_parent_id(state_path, "any-dispatch") is None


def test_resume_returns_none_when_dispatch_name_unknown(tmp_path: Path) -> None:
    from autoskillit.fleet.dispatch._lineage import resume_managed_join_parent_id

    state_path = _state_path(tmp_path)
    _seed_dispatch(state_path, name="phase-one", launch_id="a" * 32)

    assert resume_managed_join_parent_id(state_path, "unknown-phase") is None


def test_resume_returns_none_when_dispatch_has_no_lineage_ref(tmp_path: Path) -> None:
    from autoskillit.fleet.dispatch._lineage import resume_managed_join_parent_id

    state_path = _state_path(tmp_path)
    _seed_dispatch(state_path, name="phase-one", launch_id=None)

    assert resume_managed_join_parent_id(state_path, "phase-one") is None


def test_resume_returns_first_matching_dispatch(tmp_path: Path) -> None:
    from autoskillit.fleet.dispatch._lineage import resume_managed_join_parent_id

    state_path = _state_path(tmp_path)
    _seed_dispatch(state_path, name="phase-one", launch_id="a" * 32)

    assert resume_managed_join_parent_id(state_path, "phase-one") == "a" * 32


def test_resume_returns_none_when_state_file_corrupt(tmp_path: Path) -> None:
    from autoskillit.fleet.dispatch._lineage import resume_managed_join_parent_id

    state_path = _state_path(tmp_path)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text("{not valid json", encoding="utf-8")

    assert resume_managed_join_parent_id(state_path, "phase-one") is None
