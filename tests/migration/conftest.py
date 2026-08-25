"""Shared helpers for tests/migration/."""

from __future__ import annotations

from pathlib import Path

from autoskillit.core.types import RetryReason
from autoskillit.execution.session import SkillResult
from autoskillit.migration.loader import MigrationChange, MigrationNote


def make_skill_result(success: bool, result: str = "") -> SkillResult:
    """Build a minimal SkillResult for mocking headless return values.

    Mirrors the original _make_skill_result helper from test_engine.py
    (lines 41–53). The retry_reason field uses RetryReason.NONE; the
    is_error/exit_code/subtype fields track success.
    """
    return SkillResult(
        success=success,
        result=result,
        session_id="",
        subtype="success" if success else "error",
        is_error=not success,
        exit_code=0 if success else 1,
        needs_retry=False,
        retry_reason=RetryReason.NONE,
        stderr="",
    )


def make_migration_note(
    from_version: str = "0.0.0",
    to_version: str = "1.0.0",
    tmp_path: Path | None = None,
) -> MigrationNote:
    """Build a MigrationNote with one MigrationChange for migration tests.

    Mirrors the original _make_migration_note helper from test_engine.py
    (lines 56–73). The defaults allow zero-arg calls from existing test
    sites; the tmp_path arg is accepted but unused (the placeholder path
    is hard-coded).
    """
    return MigrationNote(
        from_version=from_version,
        to_version=to_version,
        description="test migration",
        changes=[
            MigrationChange(
                id="CH1",
                description="test change",
                instruction="do something",
            )
        ],
        path=Path("/fake/migration.yaml"),
    )
