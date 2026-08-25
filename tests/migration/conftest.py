"""Shared helpers for tests/migration/."""

from __future__ import annotations

from pathlib import Path

from autoskillit.core.types import RetryReason
from autoskillit.execution.session import SkillResult
from autoskillit.migration.loader import MigrationChange, MigrationNote


def make_skill_result(success: bool, result: str = "") -> SkillResult:
    """Build a minimal SkillResult for mocking headless return values."""
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
) -> MigrationNote:
    """Build a MigrationNote with one MigrationChange for migration tests."""
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
