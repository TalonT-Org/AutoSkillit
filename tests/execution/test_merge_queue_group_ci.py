"""Tests for _query_merge_group_ci — regression guard for conclusion vs state field."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from autoskillit.execution.merge_queue._merge_queue_group_ci import _query_merge_group_ci

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]


def _make_mock_proc(stdout: bytes, returncode: int = 0) -> AsyncMock:
    """Build an asyncio subprocess mock that returns the given stdout bytes."""
    proc = AsyncMock()
    proc.communicate = AsyncMock(return_value=(stdout, b""))
    proc.returncode = returncode
    return proc


class TestQueryMergeGroupCi:
    """Regression guard: _query_merge_group_ci must use .conclusion, not .state."""

    @pytest.mark.anyio
    async def test_conclusion_success_returns_SUCCESS(self) -> None:
        """gh run list output with conclusion=success returns SUCCESS."""
        stdout = (
            b' [{"conclusion": "success", "headBranch": "gh-readonly-queue/main/pr-42-abc123"}]'
        )
        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
            mock_exec.return_value = _make_mock_proc(stdout)
            result = await _query_merge_group_ci(
                repo="owner/repo",
                pr_number=42,
                base_branch="main",
                github_token=None,
            )
        assert result == "SUCCESS"

    @pytest.mark.anyio
    async def test_conclusion_failure_returns_FAILURE(self) -> None:
        """gh run list output with conclusion=failure returns FAILURE."""
        stdout = (
            b' [{"conclusion": "failure", "headBranch": "gh-readonly-queue/main/pr-42-abc123"}]'
        )
        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
            mock_exec.return_value = _make_mock_proc(stdout)
            result = await _query_merge_group_ci(
                repo="owner/repo",
                pr_number=42,
                base_branch="main",
                github_token=None,
            )
        assert result == "FAILURE"

    @pytest.mark.anyio
    async def test_conclusion_cancelled_returns_FAILURE(self) -> None:
        """gh run list output with conclusion=cancelled returns FAILURE."""
        stdout = (
            b' [{"conclusion": "cancelled", "headBranch": "gh-readonly-queue/main/pr-42-abc123"}]'
        )
        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
            mock_exec.return_value = _make_mock_proc(stdout)
            result = await _query_merge_group_ci(
                repo="owner/repo",
                pr_number=42,
                base_branch="main",
                github_token=None,
            )
        assert result == "FAILURE"

    @pytest.mark.anyio
    async def test_conclusion_timed_out_returns_FAILURE(self) -> None:
        """gh run list output with conclusion=timed_out returns FAILURE."""
        stdout = (
            b' [{"conclusion": "timed_out", "headBranch": "gh-readonly-queue/main/pr-42-abc123"}]'
        )
        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
            mock_exec.return_value = _make_mock_proc(stdout)
            result = await _query_merge_group_ci(
                repo="owner/repo",
                pr_number=42,
                base_branch="main",
                github_token=None,
            )
        assert result == "FAILURE"

    @pytest.mark.anyio
    async def test_conclusion_empty_returns_None(self) -> None:
        """gh run list output with conclusion='' (in-progress run) returns None."""
        stdout = b' [{"conclusion": "", "headBranch": "gh-readonly-queue/main/pr-42-abc123"}]'
        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
            mock_exec.return_value = _make_mock_proc(stdout)
            result = await _query_merge_group_ci(
                repo="owner/repo",
                pr_number=42,
                base_branch="main",
                github_token=None,
            )
        assert result is None

    @pytest.mark.anyio
    async def test_state_field_ignored(self) -> None:
        """Output with state=completed but conclusion=success uses conclusion, not state.

        This is the key regression test: if someone changes 'conclusion' to 'state',
        this test fails.
        """
        stdout = (
            b' [{"state": "completed", "conclusion": "success", '
            b'"headBranch": "gh-readonly-queue/main/pr-42-abc123"}]'
        )
        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
            mock_exec.return_value = _make_mock_proc(stdout)
            result = await _query_merge_group_ci(
                repo="owner/repo",
                pr_number=42,
                base_branch="main",
                github_token=None,
            )
        assert result == "SUCCESS"

    @pytest.mark.anyio
    async def test_wrong_branch_prefix_skipped(self) -> None:
        """Matching conclusion but wrong headBranch prefix returns None."""
        stdout = b' [{"conclusion": "success", "headBranch": "other-branch/pr-42-abc123"}]'
        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
            mock_exec.return_value = _make_mock_proc(stdout)
            result = await _query_merge_group_ci(
                repo="owner/repo",
                pr_number=42,
                base_branch="main",
                github_token=None,
            )
        assert result is None

    @pytest.mark.anyio
    async def test_subprocess_timeout_returns_None(self) -> None:
        """asyncio.wait_for timeout on communicate() returns None without raising."""

        async def hang(*args, **kwargs):
            raise TimeoutError()

        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
            proc = AsyncMock()
            proc.communicate = hang
            proc.returncode = -1
            mock_exec.return_value = proc
            result = await _query_merge_group_ci(
                repo="owner/repo",
                pr_number=42,
                base_branch="main",
                github_token=None,
            )
        assert result is None

    @pytest.mark.anyio
    async def test_subprocess_error_returns_None(self) -> None:
        """Subprocess raising an exception returns None without raising."""

        async def raise_err(*args, **kwargs):
            raise OSError("gh not found")

        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
            proc = AsyncMock()
            proc.communicate = raise_err
            mock_exec.return_value = proc
            result = await _query_merge_group_ci(
                repo="owner/repo",
                pr_number=42,
                base_branch="main",
                github_token=None,
            )
        assert result is None

    @pytest.mark.anyio
    async def test_empty_runs_returns_None(self) -> None:
        """Empty JSON array from gh run list returns None."""
        stdout = b"[]"
        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
            mock_exec.return_value = _make_mock_proc(stdout)
            result = await _query_merge_group_ci(
                repo="owner/repo",
                pr_number=42,
                base_branch="main",
                github_token=None,
            )
        assert result is None
