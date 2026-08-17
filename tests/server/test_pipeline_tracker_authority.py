"""Tests for tracker authority selection, restoration, and target immutability."""

from __future__ import annotations

import json

import pytest

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


class TestCheckPipelineDepsImmutableTarget:
    @pytest.mark.anyio
    async def test_kitchen_target_ignores_multiple_ambient_pipelines(
        self, tool_ctx_kitchen_open, tmp_path
    ):
        from autoskillit.server.tools.tools_execution import _check_pipeline_deps

        tool_ctx_kitchen_open.project_dir = tmp_path
        tool_ctx_kitchen_open.kitchen_id = "kitchen-xyz"

        tracker_dir = tmp_path / ".autoskillit" / "temp" / "pipeline_tracker"
        tracker_dir.mkdir(parents=True)
        for oid in ("AB", "CD"):
            tracker_dir.joinpath(f"{oid}.json").write_text(
                json.dumps(
                    {
                        "pipeline_id": oid,
                        "kitchen_id": "kitchen-xyz",
                        "steps": {"a": {"status": "pending"}, "b": {"status": "pending"}},
                        "dependencies": {"b": ["a"]},
                    }
                )
            )

        from autoskillit.server.tools.tools_execution import _select_tracker_authority

        _target, authority, key, _lease = _select_tracker_authority(
            tool_ctx_kitchen_open,
            "",
        )
        try:
            result = _check_pipeline_deps("b", authority)
        finally:
            if key is not None:
                from autoskillit.server.tools.tools_pipeline_tracker import (
                    _release_context_tracker,
                )

                _release_context_tracker(tool_ctx_kitchen_open, key)
        assert _target is not None
        assert _target.target_order_id == "kitchen-xyz"
        assert result is None


class TestSelectTrackerAuthority:
    def test_read_failure_releases_retained_lease(
        self, tool_ctx_kitchen_open, monkeypatch, tmp_path
    ):
        from autoskillit.server.tools import tools_pipeline_tracker

        tool_ctx_kitchen_open.project_dir = tmp_path
        tool_ctx_kitchen_open.kitchen_id = "kitchen-xyz"
        retained = {}

        def fail_read(_target, lease):
            retained["lease"] = lease
            raise OSError("read failed")

        monkeypatch.setattr(tools_pipeline_tracker, "read_tracker_authority", fail_read)

        with pytest.raises(OSError, match="read failed"):
            tools_pipeline_tracker._select_tracker_authority(tool_ctx_kitchen_open, "")

        assert retained["lease"].closed
        assert tool_ctx_kitchen_open.tracker_leases == {}

    def test_scoped_selection_cannot_release_kitchen_lifetime_lease(
        self, tool_ctx_kitchen_open, tmp_path
    ):
        from autoskillit.server.tools import tools_kitchen, tools_pipeline_tracker

        tool_ctx_kitchen_open.project_dir = tmp_path
        tool_ctx_kitchen_open.kitchen_id = "kitchen-xyz"
        kitchen_key, kitchen_lease = tools_kitchen._retain_kitchen_tracker_authority(
            tool_ctx_kitchen_open
        )

        _target, _authority, scoped_key, scoped_lease = (
            tools_pipeline_tracker._select_tracker_authority(tool_ctx_kitchen_open, "")
        )
        assert scoped_key is not None
        assert scoped_lease is not None
        assert scoped_key != kitchen_key

        tools_pipeline_tracker._release_context_tracker(tool_ctx_kitchen_open, scoped_key)
        assert not kitchen_lease.closed
        assert tool_ctx_kitchen_open.tracker_leases == {kitchen_key: kitchen_lease}
        tools_kitchen._release_kitchen_tracker_authority(
            tool_ctx_kitchen_open, unregister=False, retire=False
        )

    def test_completion_binding_read_exception_releases_lease(
        self, tool_ctx_kitchen_open, monkeypatch, tmp_path
    ):
        from autoskillit.core import TrackerAuthorityTarget
        from autoskillit.server.tools import tools_execution, tools_pipeline_tracker

        tool_ctx_kitchen_open.project_dir = tmp_path
        target = TrackerAuthorityTarget.for_project(tmp_path, "AB", expected=True)
        target.path.parent.mkdir(parents=True, exist_ok=True)
        target.path.write_text(json.dumps({"steps": {}, "dependencies": {}}))

        def fail_read(_target, _lease):
            raise OSError("identity read failed")

        monkeypatch.setattr(tools_pipeline_tracker, "read_tracker_identity", fail_read)
        with pytest.raises(OSError, match="identity read failed"):
            tools_execution._completion_tracker_binding(
                tool_ctx_kitchen_open, "AB", tracker_target=target
            )

        assert tool_ctx_kitchen_open.tracker_leases == {}


class TestRestoreReservedTrackerAuthority:
    def test_same_participant_keeps_existing_lease(self, tool_ctx_kitchen_open, tmp_path):
        from types import SimpleNamespace
        from typing import cast

        from autoskillit.core import AuditIdentityReservation, TrackerAuthorityTarget
        from autoskillit.server.tools.tools_pipeline_tracker import (
            _release_context_tracker,
            _restore_reserved_tracker_authority,
            _retain_context_tracker,
        )

        tool_ctx_kitchen_open.project_dir = tmp_path
        tool_ctx_kitchen_open.kitchen_id = "kitchen-xyz"
        target = TrackerAuthorityTarget.for_project(tmp_path, "AB", expected=True)
        key, lease = _retain_context_tracker(
            tool_ctx_kitchen_open,
            target,
            owner_kind="kitchen",
            owner_id="kitchen-xyz",
        )
        reservation = cast(
            AuditIdentityReservation,
            SimpleNamespace(tracker_target_order_id="AB", tracker_expected=True),
        )

        _target, _authority, restored_key, restored_lease = _restore_reserved_tracker_authority(
            tool_ctx_kitchen_open,
            reservation,
            key,
        )

        assert restored_key == key
        assert restored_lease is lease
        assert not lease.closed
        assert list(tool_ctx_kitchen_open.tracker_leases) == [key]
        _release_context_tracker(tool_ctx_kitchen_open, key)

    def test_replacement_read_failure_preserves_current_lease(
        self, tool_ctx_kitchen_open, monkeypatch, tmp_path
    ):
        from types import SimpleNamespace
        from typing import cast

        from autoskillit.core import AuditIdentityReservation, TrackerAuthorityTarget
        from autoskillit.server.tools import tools_pipeline_tracker

        tool_ctx_kitchen_open.project_dir = tmp_path
        tool_ctx_kitchen_open.kitchen_id = "kitchen-xyz"
        current_target = TrackerAuthorityTarget.for_project(tmp_path, "AB", expected=True)
        current_key, current_lease = tools_pipeline_tracker._retain_context_tracker(
            tool_ctx_kitchen_open,
            current_target,
            owner_kind="kitchen",
            owner_id="kitchen-xyz",
        )
        reservation = cast(
            AuditIdentityReservation,
            SimpleNamespace(tracker_target_order_id="CD", tracker_expected=True),
        )

        def fail_read(_target, _lease):
            raise OSError("read failed")

        monkeypatch.setattr(tools_pipeline_tracker, "read_tracker_authority", fail_read)

        with pytest.raises(OSError, match="read failed"):
            tools_pipeline_tracker._restore_reserved_tracker_authority(
                tool_ctx_kitchen_open,
                reservation,
                current_key,
            )

        assert not current_lease.closed
        assert tool_ctx_kitchen_open.tracker_leases == {current_key: current_lease}
        tools_pipeline_tracker._release_context_tracker(tool_ctx_kitchen_open, current_key)


class TestSelectTrackerTarget:
    def test_kitchen_scoped_fallback_never_scans_ambient_candidates(
        self, tool_ctx_kitchen_open, tmp_path
    ):
        """The caller-selected kitchen target is immutable despite ambient files."""
        from autoskillit.server.tools.tools_pipeline_tracker import (
            select_tracker_target,
        )

        tool_ctx_kitchen_open.project_dir = tmp_path
        tool_ctx_kitchen_open.kitchen_id = "kitchen-xyz"

        tracker_dir = tmp_path / ".autoskillit" / "temp" / "pipeline_tracker"
        tracker_dir.mkdir(parents=True)
        tracker_dir.joinpath("AB.json").write_text(
            json.dumps(
                {
                    "pipeline_id": "AB",
                    "kitchen_id": "kitchen-xyz",
                    "steps": {"a": {"status": "pending"}},
                    "dependencies": {},
                }
            )
        )

        result = select_tracker_target(tool_ctx_kitchen_open, "", expected=False)

        assert result is not None
        assert result.target_order_id == "kitchen-xyz"
        assert result.path == tracker_dir / "kitchen-xyz.json"
