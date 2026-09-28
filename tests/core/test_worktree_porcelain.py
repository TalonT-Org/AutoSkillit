"""Tests for the NUL-delimited Git worktree inventory parser."""

from __future__ import annotations

import pytest

from autoskillit.core import WorktreeRecord, parse_worktree_porcelain

pytestmark = [pytest.mark.layer("core"), pytest.mark.small]


def test_parse_worktree_porcelain_preserves_records_and_flags() -> None:
    main_head = "a" * 40
    detached_head = "b" * 40
    locked_head = "c" * 40
    newline_path_head = "d" * 40
    stdout = "\0".join(
        (
            "worktree /repo",
            f"HEAD {main_head}",
            "branch refs/heads/main",
            "",
            "worktree /repo with spaces",
            f"HEAD {detached_head}",
            "detached",
            "",
            "worktree /bare",
            "bare",
            "locked",
            "",
            "worktree /locked",
            f"HEAD {locked_head}",
            "locked reason\nwith newline",
            "prunable reason text",
            "",
            "worktree /path\nworktree /elsewhere",
            f"HEAD {newline_path_head}",
        )
    )

    assert parse_worktree_porcelain(stdout) == (
        WorktreeRecord("/repo", main_head, "main", True, False),
        WorktreeRecord("/repo with spaces", detached_head, None, False, False),
        WorktreeRecord("/bare", "", None, False, False),
        WorktreeRecord("/locked", locked_head, None, False, True),
        WorktreeRecord("/path\nworktree /elsewhere", newline_path_head, None, False, False),
    )


@pytest.mark.parametrize(
    "stdout,expected",
    [
        ("", ()),
        (
            "worktree /last\0HEAD deadbeef",
            (WorktreeRecord("/last", "deadbeef", None, True, False),),
        ),
        (
            "worktree /last\0HEAD deadbeef\0",
            (WorktreeRecord("/last", "deadbeef", None, True, False),),
        ),
    ],
)
def test_parse_worktree_porcelain_flushes_last_record(
    stdout: str, expected: tuple[WorktreeRecord, ...]
) -> None:
    assert parse_worktree_porcelain(stdout) == expected
