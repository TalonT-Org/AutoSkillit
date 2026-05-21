"""Tests for fleet._label_cleanup.sweep_stale_dispatch_labels (Group J)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from autoskillit.fleet import DispatchStatus

pytestmark = [pytest.mark.layer("fleet"), pytest.mark.small, pytest.mark.feature("fleet")]


def _make_running_dispatch_state(
    state_path: Path,
    sidecar_path: Path,
    issue_url: str = "https://github.com/owner/repo/issues/1",
) -> None:
    """Create a campaign state file with one RUNNING dispatch and a sidecar."""
    from autoskillit.fleet import DispatchRecord, write_initial_state
    from autoskillit.fleet.state import mark_dispatch_running

    write_initial_state(
        state_path,
        campaign_id="test-campaign",
        campaign_name="test-campaign",
        manifest_path="/m.yaml",
        dispatches=[DispatchRecord(name="d1")],
    )
    sidecar_path.write_text(
        json.dumps(
            {
                "issue_url": issue_url,
                "status": "completed",
                "ts": "2026-01-01T00:00:00Z",
            }
        )
        + "\n"
    )
    mark_dispatch_running(
        state_path,
        "d1",
        dispatch_id="test-dispatch-id",
        dispatched_pid=99999,
        sidecar_path=str(sidecar_path),
    )


class TestStartupLabelRecoverySweep:
    @pytest.mark.anyio
    async def test_startup_sweep_cleans_label_for_dead_running_dispatch(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Sweep calls swap_labels and marks dispatch INTERRUPTED for dead RUNNING dispatch."""
        from autoskillit.fleet import read_state
        from autoskillit.fleet._label_cleanup import sweep_stale_dispatch_labels

        monkeypatch.setattr(
            "autoskillit.fleet._label_cleanup.is_dispatch_session_alive",
            lambda record: False,
        )

        state_path = tmp_path / "campaign.json"
        sidecar = tmp_path / "sidecar.jsonl"
        _make_running_dispatch_state(state_path, sidecar)

        swap_labels_mock = AsyncMock(return_value={"success": True})
        github_client = AsyncMock()
        github_client.swap_labels = swap_labels_mock

        await sweep_stale_dispatch_labels([state_path], github_client)

        swap_labels_mock.assert_called_once()
        state = read_state(state_path)
        assert state is not None
        assert state.dispatches[0].status.value == "interrupted"

    @pytest.mark.anyio
    async def test_startup_sweep_skips_alive_running_dispatch(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Sweep does NOT call swap_labels for an alive RUNNING dispatch."""
        from autoskillit.fleet import read_state
        from autoskillit.fleet._label_cleanup import sweep_stale_dispatch_labels

        monkeypatch.setattr(
            "autoskillit.fleet._label_cleanup.is_dispatch_session_alive",
            lambda record: True,
        )

        state_path = tmp_path / "campaign.json"
        sidecar = tmp_path / "sidecar.jsonl"
        _make_running_dispatch_state(state_path, sidecar)

        swap_labels_mock = AsyncMock(return_value={"success": True})
        github_client = AsyncMock()
        github_client.swap_labels = swap_labels_mock

        await sweep_stale_dispatch_labels([state_path], github_client)

        swap_labels_mock.assert_not_called()
        state = read_state(state_path)
        assert state is not None
        assert state.dispatches[0].status.value == "running"

    @pytest.mark.anyio
    async def test_startup_sweep_handles_missing_sidecar_gracefully(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Sweep handles RUNNING dispatch with sidecar_path=None without error."""
        from autoskillit.fleet import DispatchRecord, write_initial_state
        from autoskillit.fleet._label_cleanup import sweep_stale_dispatch_labels
        from autoskillit.fleet.state import mark_dispatch_running

        monkeypatch.setattr(
            "autoskillit.fleet._label_cleanup.is_dispatch_session_alive",
            lambda record: False,
        )

        state_path = tmp_path / "campaign_no_sidecar.json"
        write_initial_state(
            state_path,
            campaign_id="test",
            campaign_name="test",
            manifest_path="/m.yaml",
            dispatches=[DispatchRecord(name="d1")],
        )
        mark_dispatch_running(
            state_path,
            "d1",
            dispatch_id="test-dispatch-id",
            dispatched_pid=99999,
            sidecar_path=None,
        )

        swap_labels_mock = AsyncMock(return_value={"success": True})
        github_client = AsyncMock()
        github_client.swap_labels = swap_labels_mock

        await sweep_stale_dispatch_labels([state_path], github_client)

        swap_labels_mock.assert_not_called()

    @pytest.mark.anyio
    async def test_startup_sweep_handles_multiple_campaigns(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Sweep processes all campaign state files and cleans one label per dead dispatch."""
        from autoskillit.fleet._label_cleanup import sweep_stale_dispatch_labels

        monkeypatch.setattr(
            "autoskillit.fleet._label_cleanup.is_dispatch_session_alive",
            lambda record: False,
        )

        state_path_1 = tmp_path / "campaign1.json"
        sidecar_1 = tmp_path / "sidecar1.jsonl"
        _make_running_dispatch_state(
            state_path_1, sidecar_1, "https://github.com/owner/repo/issues/10"
        )

        state_path_2 = tmp_path / "campaign2.json"
        sidecar_2 = tmp_path / "sidecar2.jsonl"
        _make_running_dispatch_state(
            state_path_2, sidecar_2, "https://github.com/owner/repo/issues/11"
        )

        swap_labels_mock = AsyncMock(return_value={"success": True})
        github_client = AsyncMock()
        github_client.swap_labels = swap_labels_mock

        await sweep_stale_dispatch_labels([state_path_1, state_path_2], github_client)

        assert swap_labels_mock.call_count == 2

    @pytest.mark.anyio
    async def test_startup_sweep_cleans_terminal_dispatch_with_uncleaned_labels(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Sweep cleans labels for FAILURE dispatch with labels_cleaned=False."""
        from autoskillit.fleet import DispatchRecord, read_state, write_initial_state
        from autoskillit.fleet._label_cleanup import sweep_stale_dispatch_labels
        from autoskillit.fleet.state import upsert_dispatch_record_by_name

        monkeypatch.setattr(
            "autoskillit.fleet._label_cleanup.is_dispatch_session_alive",
            lambda record: False,
        )

        state_path = tmp_path / "campaign_terminal.json"
        sidecar = tmp_path / "sidecar_terminal.jsonl"
        sidecar.write_text(
            json.dumps(
                {
                    "issue_url": "https://github.com/owner/repo/issues/5",
                    "status": "completed",
                    "ts": "2026-01-01T00:00:00Z",
                }
            )
            + "\n"
        )
        write_initial_state(
            state_path,
            campaign_id="test",
            campaign_name="test",
            manifest_path="/m.yaml",
            dispatches=[DispatchRecord(name="d1")],
        )
        upsert_dispatch_record_by_name(
            state_path,
            DispatchRecord(
                name="d1",
                status=DispatchStatus.FAILURE,
                sidecar_path=str(sidecar),
                labels_cleaned=False,
            ),
        )

        swap_labels_mock = AsyncMock(return_value={"success": True})
        github_client = AsyncMock()
        github_client.swap_labels = swap_labels_mock

        await sweep_stale_dispatch_labels([state_path], github_client)

        swap_labels_mock.assert_called_once()
        state = read_state(state_path)
        assert state is not None
        assert state.dispatches[0].labels_cleaned is True

    @pytest.mark.anyio
    async def test_startup_sweep_skips_terminal_dispatch_with_labels_already_cleaned(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Sweep does NOT clean labels for FAILURE dispatch with labels_cleaned=True."""
        from autoskillit.fleet import DispatchRecord, write_initial_state
        from autoskillit.fleet._label_cleanup import sweep_stale_dispatch_labels
        from autoskillit.fleet.state import upsert_dispatch_record_by_name

        monkeypatch.setattr(
            "autoskillit.fleet._label_cleanup.is_dispatch_session_alive",
            lambda record: False,
        )

        state_path = tmp_path / "campaign_cleaned.json"
        sidecar = tmp_path / "sidecar_cleaned.jsonl"
        sidecar.write_text(
            json.dumps(
                {
                    "issue_url": "https://github.com/owner/repo/issues/6",
                    "status": "completed",
                    "ts": "2026-01-01T00:00:00Z",
                }
            )
            + "\n"
        )
        write_initial_state(
            state_path,
            campaign_id="test",
            campaign_name="test",
            manifest_path="/m.yaml",
            dispatches=[DispatchRecord(name="d1")],
        )
        upsert_dispatch_record_by_name(
            state_path,
            DispatchRecord(
                name="d1",
                status=DispatchStatus.FAILURE,
                sidecar_path=str(sidecar),
                labels_cleaned=True,
            ),
        )

        swap_labels_mock = AsyncMock(return_value={"success": True})
        github_client = AsyncMock()
        github_client.swap_labels = swap_labels_mock

        await sweep_stale_dispatch_labels([state_path], github_client)

        swap_labels_mock.assert_not_called()
