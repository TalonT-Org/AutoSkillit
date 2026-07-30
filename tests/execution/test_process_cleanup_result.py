"""Behavioral tests for process-tree cleanup evidence."""

from __future__ import annotations

import pytest

from autoskillit.execution import async_kill_process_tree, kill_process_tree

pytestmark = [pytest.mark.layer("execution"), pytest.mark.medium]


def test_kill_nonexistent_process_returns_complete_evidence() -> None:
    result = kill_process_tree(999_999_999)

    assert result.root_pid == 999_999_999
    assert result.complete is True
    assert result.process_identities == ()
    assert result.survivor_pids == ()


@pytest.mark.asyncio
async def test_async_kill_returns_same_typed_evidence() -> None:
    result = await async_kill_process_tree(999_999_999)

    assert result.root_pid == 999_999_999
    assert result.complete is True
