"""Contracts for local process-tree cleanup evidence."""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from autoskillit.core import ProcessCleanupResult

pytestmark = [pytest.mark.layer("core"), pytest.mark.small]


def test_process_cleanup_result_serializes_survivor_evidence() -> None:
    result = ProcessCleanupResult(
        root_pid=101,
        process_identities=((101, 1.5), (102, 2.5)),
        terminated_pids=(101,),
        survivor_pids=(102,),
        access_denied_pids=(102,),
    )

    assert result.complete is False
    assert result.to_dict() == {
        "root_pid": 101,
        "process_identities": [
            {"pid": 101, "create_time": 1.5},
            {"pid": 102, "create_time": 2.5},
        ],
        "terminated_pids": [101],
        "survivor_pids": [102],
        "access_denied_pids": [102],
        "complete": False,
    }


def test_process_cleanup_result_is_frozen() -> None:
    result = ProcessCleanupResult(root_pid=101)

    with pytest.raises(FrozenInstanceError):
        result.root_pid = 202  # type: ignore[misc]
