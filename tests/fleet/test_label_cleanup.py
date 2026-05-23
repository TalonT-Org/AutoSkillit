"""Tests for fleet._label_cleanup module — infra-level label cleanup (Group J)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from tests.fleet._helpers import _no_sleep_quota_checker, _noop_quota_refresher, _setup_dispatch

pytestmark = [pytest.mark.layer("fleet"), pytest.mark.small, pytest.mark.feature("fleet")]


class TestDispatchSidecarCleanupOnCrash:
    """Tests for label cleanup injected into _run_dispatch finally block."""

    @pytest.mark.anyio
    async def test_dispatch_sidecar_cleanup_on_cancellation(
        self, tool_ctx, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """_run_dispatch finally block calls swap_labels when CancelledError occurs."""
        from autoskillit.fleet import execute_dispatch
        from autoskillit.fleet.sidecar import sidecar_path as make_sidecar_path

        _setup_dispatch(tool_ctx, monkeypatch)
        swap_labels_mock = AsyncMock(return_value={"success": True})
        github_client = AsyncMock()
        github_client.swap_labels = swap_labels_mock
        tool_ctx.github_client = github_client

        async def _write_sidecar_then_cancel(**kwargs):
            sidecar = make_sidecar_path(kwargs["dispatch_id"], tool_ctx.project_dir)
            sidecar.write_text(
                json.dumps(
                    {
                        "issue_url": "https://github.com/owner/repo/issues/1",
                        "status": "completed",
                        "ts": "2026-01-01T00:00:00Z",
                    }
                )
                + "\n"
            )
            raise asyncio.CancelledError

        tool_ctx.executor.dispatch_food_truck = _write_sidecar_then_cancel

        with pytest.raises(asyncio.CancelledError):
            await execute_dispatch(
                tool_ctx=tool_ctx,
                recipe="test-recipe",
                task="t",
                ingredients=None,
                dispatch_name=None,
                timeout_sec=None,
                prompt_builder=lambda **kw: "prompt",
                quota_checker=_no_sleep_quota_checker,
                quota_refresher=_noop_quota_refresher,
            )

        swap_labels_mock.assert_called_once()
        call = swap_labels_mock.call_args
        assert call.args[0] == "owner"
        assert call.args[1] == "repo"
        assert call.args[2] == 1
        assert "in-progress" in call.kwargs["remove_labels"]
        assert "fail" in call.kwargs["add_labels"]

    @pytest.mark.anyio
    async def test_dispatch_sidecar_cleanup_on_runtime_exception(
        self, tool_ctx, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """_run_dispatch finally block calls swap_labels when RuntimeError occurs."""
        from autoskillit.fleet import execute_dispatch
        from autoskillit.fleet.sidecar import sidecar_path as make_sidecar_path

        _setup_dispatch(tool_ctx, monkeypatch)
        swap_labels_mock = AsyncMock(return_value={"success": True})
        github_client = AsyncMock()
        github_client.swap_labels = swap_labels_mock
        tool_ctx.github_client = github_client

        async def _write_sidecar_then_raise(**kwargs):
            sidecar = make_sidecar_path(kwargs["dispatch_id"], tool_ctx.project_dir)
            sidecar.write_text(
                json.dumps(
                    {
                        "issue_url": "https://github.com/owner/repo/issues/2",
                        "status": "completed",
                        "ts": "2026-01-01T00:00:00Z",
                    }
                )
                + "\n"
            )
            raise RuntimeError("infra crash")

        tool_ctx.executor.dispatch_food_truck = _write_sidecar_then_raise

        result = await execute_dispatch(
            tool_ctx=tool_ctx,
            recipe="test-recipe",
            task="t",
            ingredients=None,
            dispatch_name=None,
            timeout_sec=None,
            prompt_builder=lambda **kw: "prompt",
            quota_checker=_no_sleep_quota_checker,
            quota_refresher=_noop_quota_refresher,
        )

        envelope = json.loads(result.outcome.to_envelope())
        assert envelope.get("success") is False
        swap_labels_mock.assert_called_once()

    @pytest.mark.anyio
    async def test_dispatch_sidecar_cleanup_skips_when_no_sidecar(
        self, tool_ctx, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """swap_labels NOT called when no sidecar file exists."""
        from autoskillit.fleet import execute_dispatch

        _setup_dispatch(tool_ctx, monkeypatch)
        swap_labels_mock = AsyncMock(return_value={"success": True})
        github_client = AsyncMock()
        github_client.swap_labels = swap_labels_mock
        tool_ctx.github_client = github_client

        async def _cancel_without_sidecar(**kwargs):
            raise asyncio.CancelledError

        tool_ctx.executor.dispatch_food_truck = _cancel_without_sidecar

        with pytest.raises(asyncio.CancelledError):
            await execute_dispatch(
                tool_ctx=tool_ctx,
                recipe="test-recipe",
                task="t",
                ingredients=None,
                dispatch_name=None,
                timeout_sec=None,
                prompt_builder=lambda **kw: "prompt",
                quota_checker=_no_sleep_quota_checker,
                quota_refresher=_noop_quota_refresher,
            )

        swap_labels_mock.assert_not_called()

    @pytest.mark.anyio
    async def test_dispatch_sidecar_cleanup_skips_when_github_client_none(
        self, tool_ctx, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No AttributeError when github_client is None and sidecar exists."""
        from autoskillit.fleet import execute_dispatch
        from autoskillit.fleet.sidecar import sidecar_path as make_sidecar_path

        _setup_dispatch(tool_ctx, monkeypatch)
        tool_ctx.github_client = None

        async def _write_sidecar_then_cancel(**kwargs):
            sidecar = make_sidecar_path(kwargs["dispatch_id"], tool_ctx.project_dir)
            sidecar.write_text(
                json.dumps(
                    {
                        "issue_url": "https://github.com/owner/repo/issues/1",
                        "status": "completed",
                        "ts": "2026-01-01T00:00:00Z",
                    }
                )
                + "\n"
            )
            raise asyncio.CancelledError

        tool_ctx.executor.dispatch_food_truck = _write_sidecar_then_cancel

        with pytest.raises(asyncio.CancelledError):
            await execute_dispatch(
                tool_ctx=tool_ctx,
                recipe="test-recipe",
                task="t",
                ingredients=None,
                dispatch_name=None,
                timeout_sec=None,
                prompt_builder=lambda **kw: "prompt",
                quota_checker=_no_sleep_quota_checker,
                quota_refresher=_noop_quota_refresher,
            )

    @pytest.mark.anyio
    async def test_dispatch_sidecar_cleanup_handles_multiple_issues(
        self, tool_ctx, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """swap_labels called once per issue_url in sidecar."""
        from autoskillit.fleet import execute_dispatch
        from autoskillit.fleet.sidecar import sidecar_path as make_sidecar_path

        _setup_dispatch(tool_ctx, monkeypatch)
        swap_labels_mock = AsyncMock(return_value={"success": True})
        github_client = AsyncMock()
        github_client.swap_labels = swap_labels_mock
        tool_ctx.github_client = github_client

        async def _write_three_issues_then_cancel(**kwargs):
            sidecar = make_sidecar_path(kwargs["dispatch_id"], tool_ctx.project_dir)
            lines = [
                json.dumps(
                    {
                        "issue_url": f"https://github.com/owner/repo/issues/{i}",
                        "status": "completed",
                        "ts": "2026-01-01T00:00:00Z",
                    }
                )
                for i in [1, 2, 3]
            ]
            sidecar.write_text("\n".join(lines) + "\n")
            raise asyncio.CancelledError

        tool_ctx.executor.dispatch_food_truck = _write_three_issues_then_cancel

        with pytest.raises(asyncio.CancelledError):
            await execute_dispatch(
                tool_ctx=tool_ctx,
                recipe="test-recipe",
                task="t",
                ingredients=None,
                dispatch_name=None,
                timeout_sec=None,
                prompt_builder=lambda **kw: "prompt",
                quota_checker=_no_sleep_quota_checker,
                quota_refresher=_noop_quota_refresher,
            )

        assert swap_labels_mock.call_count == 3


class TestDispatchSidecarCleanupOnNormalFailure:
    """Tests for label cleanup triggered by outcome classification (not exception)."""

    @pytest.mark.anyio
    async def test_normal_exit_failure_triggers_label_cleanup(
        self, tool_ctx, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """swap_labels called when dispatch_food_truck returns success=False without raising."""
        import dataclasses

        from autoskillit.fleet import execute_dispatch
        from autoskillit.fleet.sidecar import sidecar_path as make_sidecar_path
        from tests.fakes import _DEFAULT_SKILL_RESULT

        _setup_dispatch(tool_ctx, monkeypatch)
        swap_labels_mock = AsyncMock(return_value={"success": True})
        github_client = AsyncMock()
        github_client.swap_labels = swap_labels_mock
        tool_ctx.github_client = github_client

        failure_result = dataclasses.replace(
            _DEFAULT_SKILL_RESULT,
            success=False,
            result='{"success": false, "reason": "context_exhaustion"}',
            subtype="success",
            is_error=False,
            exit_code=0,
        )

        async def _return_failure(**kwargs):
            sidecar = make_sidecar_path(kwargs["dispatch_id"], tool_ctx.project_dir)
            sidecar.write_text(
                json.dumps(
                    {
                        "issue_url": "https://github.com/owner/repo/issues/1",
                        "status": "completed",
                        "ts": "2026-01-01T00:00:00Z",
                    }
                )
                + "\n"
            )
            if kwargs.get("on_spawn"):
                kwargs["on_spawn"](12345, 1000)
            return failure_result

        tool_ctx.executor.dispatch_food_truck = _return_failure
        monkeypatch.setattr(
            "autoskillit.fleet._api.parse_l3_result_block",
            lambda **_: None,
        )

        result = await execute_dispatch(
            tool_ctx=tool_ctx,
            recipe="test-recipe",
            task="t",
            ingredients=None,
            dispatch_name=None,
            timeout_sec=None,
            prompt_builder=lambda **kw: "prompt",
            quota_checker=_no_sleep_quota_checker,
            quota_refresher=_noop_quota_refresher,
        )

        swap_labels_mock.assert_called_once()
        call = swap_labels_mock.call_args
        assert call.args[0] == "owner"
        assert call.args[1] == "repo"
        assert call.args[2] == 1
        assert "in-progress" in call.kwargs["remove_labels"]
        assert "fail" in call.kwargs["add_labels"]
        envelope = json.loads(result.outcome.to_envelope())
        assert envelope.get("success") is False


class TestCleanupOrphanedLabelsUnit:
    """Direct unit tests for cleanup_orphaned_labels helper."""

    @pytest.mark.anyio
    async def test_cleanup_orphaned_labels_uses_registry_transitions(self, tmp_path: Path) -> None:
        """remove_labels must be derived from LABEL_LIFECYCLE_REGISTRY, not hardcoded."""
        from autoskillit.core import LABEL_LIFECYCLE_REGISTRY, IssueLabelState
        from autoskillit.fleet._label_cleanup import cleanup_orphaned_labels

        sidecar = tmp_path / "test_issues.jsonl"
        sidecar.write_text(
            json.dumps(
                {
                    "issue_url": "https://github.com/owner/repo/issues/1",
                    "status": "completed",
                    "ts": "2026-01-01T00:00:00Z",
                }
            )
            + "\n"
        )

        swap_labels_mock = AsyncMock(return_value={"success": True})
        github_client = AsyncMock()
        github_client.swap_labels = swap_labels_mock

        await cleanup_orphaned_labels(str(sidecar), github_client)

        swap_labels_mock.assert_called_once()
        call = swap_labels_mock.call_args
        expected_remove = sorted(
            s.value
            for s in LABEL_LIFECYCLE_REGISTRY[IssueLabelState.FAIL].removes_on_entry
            | {IssueLabelState.IN_PROGRESS}
        )
        assert sorted(call.kwargs["remove_labels"]) == expected_remove
        assert call.kwargs["add_labels"] == [IssueLabelState.FAIL.value]

    @pytest.mark.anyio
    async def test_cleanup_returns_false_when_swap_labels_raises(self, tmp_path: Path) -> None:
        """cleanup_orphaned_labels returns False when swap_labels raises."""
        from autoskillit.fleet._label_cleanup import cleanup_orphaned_labels

        sidecar = tmp_path / "test_issues.jsonl"
        sidecar.write_text(
            json.dumps(
                {
                    "issue_url": "https://github.com/owner/repo/issues/1",
                    "status": "completed",
                    "ts": "2026-01-01T00:00:00Z",
                }
            )
            + "\n"
        )

        github_client = AsyncMock()
        github_client.swap_labels = AsyncMock(side_effect=Exception("rate limited"))

        result = await cleanup_orphaned_labels(str(sidecar), github_client)

        assert result is False

    @pytest.mark.anyio
    async def test_cleanup_returns_true_when_all_succeed(self, tmp_path: Path) -> None:
        """cleanup_orphaned_labels returns True when all swap_labels succeed."""
        from autoskillit.fleet._label_cleanup import cleanup_orphaned_labels

        sidecar = tmp_path / "test_issues.jsonl"
        sidecar.write_text(
            json.dumps(
                {
                    "issue_url": "https://github.com/owner/repo/issues/1",
                    "status": "completed",
                    "ts": "2026-01-01T00:00:00Z",
                }
            )
            + "\n"
        )

        github_client = AsyncMock()
        github_client.swap_labels = AsyncMock(return_value={"success": True})

        result = await cleanup_orphaned_labels(str(sidecar), github_client)

        assert result is True

    @pytest.mark.anyio
    async def test_cleanup_returns_false_on_partial_failure(self, tmp_path: Path) -> None:
        """cleanup_orphaned_labels returns False when any swap_labels call fails."""
        from autoskillit.fleet._label_cleanup import cleanup_orphaned_labels

        sidecar = tmp_path / "test_issues.jsonl"
        lines = [
            json.dumps(
                {
                    "issue_url": f"https://github.com/owner/repo/issues/{i}",
                    "status": "completed",
                    "ts": "2026-01-01T00:00:00Z",
                }
            )
            for i in [1, 2]
        ]
        sidecar.write_text("\n".join(lines) + "\n")

        call_count = 0

        async def _succeed_then_raise(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return {"success": True}
            raise Exception("rate limited")

        github_client = AsyncMock()
        github_client.swap_labels = _succeed_then_raise

        result = await cleanup_orphaned_labels(str(sidecar), github_client)

        assert result is False

    @pytest.mark.anyio
    async def test_cleanup_returns_false_when_swap_returns_failure(self, tmp_path: Path) -> None:
        """cleanup_orphaned_labels returns False when swap_labels returns success=False."""
        from autoskillit.fleet._label_cleanup import cleanup_orphaned_labels

        sidecar = tmp_path / "test_issues.jsonl"
        sidecar.write_text(
            json.dumps(
                {
                    "issue_url": "https://github.com/owner/repo/issues/1",
                    "status": "completed",
                    "ts": "2026-01-01T00:00:00Z",
                }
            )
            + "\n"
        )

        github_client = AsyncMock()
        github_client.swap_labels = AsyncMock(
            return_value={"success": False, "error": "rate limited"}
        )

        result = await cleanup_orphaned_labels(str(sidecar), github_client)

        assert result is False

    @pytest.mark.anyio
    async def test_cleanup_returns_false_when_sidecar_file_missing_on_disk(
        self, tmp_path: Path
    ) -> None:
        """cleanup_orphaned_labels returns False when sidecar_path points to nonexistent file."""
        from autoskillit.fleet._label_cleanup import cleanup_orphaned_labels

        missing_path = str(tmp_path / "deleted_issues.jsonl")
        github_client = AsyncMock()
        github_client.swap_labels = AsyncMock(return_value={"success": True})

        result = await cleanup_orphaned_labels(missing_path, github_client)

        assert result is False
        github_client.swap_labels.assert_not_called()
