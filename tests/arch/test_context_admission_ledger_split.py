"""Structural guards for the test_context_admission_ledger.py split (issue #4606)."""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]

PIPELINE = Path(__file__).resolve().parent.parent / "pipeline"
SPLIT_FILES = (
    "test_context_admission_ledger_recovery.py",
    "test_context_admission_ledger_projection.py",
    "test_context_admission_ledger_journal.py",
    "test_context_admission_ledger_contention.py",
    "test_context_admission_ledger_sticky_health.py",
)
ORIGINAL_MEGA_FILE = "test_context_admission_ledger.py"
_MAX_LINES = 1000


def test_context_admission_ledger_split_files_exist() -> None:
    """Issue #4606: each concern must remain in its own module."""
    for name in SPLIT_FILES:
        assert (PIPELINE / name).exists(), (
            f"Missing split file: {name}. The audit-prescribed concerns "
            f"(recovery, projection, journal, contention, sticky_health) "
            f"must each have their own module per issue #4606."
        )


def test_context_admission_ledger_mega_file_was_removed() -> None:
    """Issue #4606: the 2620-line mega-file must not be re-introduced."""
    assert not (PIPELINE / ORIGINAL_MEGA_FILE).exists(), (
        f"{ORIGINAL_MEGA_FILE} (the 2620-line pre-split file) must remain "
        f"removed. Re-merging the split files undoes the audit fix."
    )


@pytest.mark.parametrize("name", SPLIT_FILES)
def test_context_admission_ledger_split_files_stay_small(name: str) -> None:
    """Issue #4606: each split file stays under the oversized-file limit."""
    path = PIPELINE / name
    line_count = len(path.read_text().splitlines())
    assert line_count <= _MAX_LINES, (
        f"{name} has {line_count} lines, over the {_MAX_LINES}-line "
        f"oversized-test-file limit. Split it further along concern "
        f"boundaries rather than letting concerns re-merge."
    )
