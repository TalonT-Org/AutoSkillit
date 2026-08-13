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
        observation_complete=True,
        identity_refused=True,
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
        "observation_complete": True,
        "identity_refused": True,
        "complete": False,
    }


@pytest.mark.parametrize(
    ("result", "expected_complete"),
    [
        (ProcessCleanupResult(root_pid=101, observation_complete=True), True),
        (
            ProcessCleanupResult(root_pid=101, observation_complete=True, identity_refused=True),
            False,
        ),
        (
            ProcessCleanupResult(root_pid=101, observation_complete=True, survivor_pids=(101,)),
            False,
        ),
        (
            ProcessCleanupResult(
                root_pid=101, observation_complete=True, access_denied_pids=(101,)
            ),
            False,
        ),
    ],
)
def test_process_cleanup_result_complete_requires_verified_absence(
    result: ProcessCleanupResult, expected_complete: bool
) -> None:
    assert result.complete is expected_complete


def test_process_cleanup_result_is_frozen() -> None:
    result = ProcessCleanupResult(root_pid=101)

    with pytest.raises(FrozenInstanceError):
        result.root_pid = 202  # type: ignore[misc]


@pytest.mark.parametrize(
    ("observation_complete", "survivors", "denied", "expected"),
    [
        (True, (), (), True),
        (True, (102,), (), False),
        (True, (), (102,), False),
        (False, (), (), False),
    ],
)
def test_process_cleanup_result_complete_is_fail_closed(
    observation_complete: bool,
    survivors: tuple[int, ...],
    denied: tuple[int, ...],
    expected: bool,
) -> None:
    result = ProcessCleanupResult(
        root_pid=101,
        survivor_pids=survivors,
        access_denied_pids=denied,
        observation_complete=observation_complete,
    )

    assert result.complete is expected
