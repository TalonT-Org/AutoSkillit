"""Tests for the SkillMigrationAdapter and skill-specific validation paths."""

from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit.core import ALL_PROJECT_LOCAL_SKILL_SEARCH_DIRS
from autoskillit.migration.adapters_skill import SkillMigrationAdapter
from autoskillit.migration.engine import (
    DeterministicMigrationAdapter,
    MigrationFile,
)
from autoskillit.migration.service import default_migration_engine

pytestmark = [pytest.mark.layer("migration"), pytest.mark.small]

_SKILL_CORPUS_DIR: Path = (
    Path(__file__).resolve().parent.parent / "contracts" / "fixtures" / "skill_contract_corpus"
)


def test_skill_validation_checks_deterministic_contract(tmp_path: Path) -> None:
    skill_dir = tmp_path / ".claude" / "skills" / "missing-declaration"
    skill_dir.mkdir(parents=True)
    skill_path = skill_dir / "SKILL.md"
    skill_path.write_text("---\nname: missing-declaration\n---\nRead .claude/settings.json.\n")

    is_valid, error = SkillMigrationAdapter().validate(skill_path)

    assert is_valid is False
    assert "deterministic skill invalidities remain" in error
    assert "claude_dir" in error


@pytest.mark.anyio
async def test_skill_migration_rejects_non_list_capabilities(tmp_path: Path) -> None:
    skill_dir = tmp_path / ".claude" / "skills" / "malformed-capabilities"
    skill_dir.mkdir(parents=True)
    skill_path = skill_dir / "SKILL.md"
    skill_path.write_text(
        "---\n"
        "name: malformed-capabilities\n"
        "uses_capabilities: claude_dir\n"
        "---\n"
        "Read .claude/settings.json.\n"
    )
    file = MigrationFile(
        name="malformed-capabilities",
        path=skill_path,
        file_type="skill",
        current_version=None,
    )

    result = await SkillMigrationAdapter().migrate(file, temp_dir=tmp_path / "temp")

    assert result.success is False
    assert result.error == "uses_capabilities must be a list before deterministic migration"


class TestSkillMigrationAdapter:
    def test_default_adapter_registered_in_engine(self) -> None:
        assert default_migration_engine().get_adapter("skill") is not None
        assert isinstance(SkillMigrationAdapter(), DeterministicMigrationAdapter)

    def test_discover_finds_files_across_all_search_dirs(self, tmp_path: Path) -> None:
        for index, search_dir in enumerate(ALL_PROJECT_LOCAL_SKILL_SEARCH_DIRS):
            skill_dir = tmp_path / search_dir / f"skill-{index}"
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text(
                f"---\nname: skill-{index}\ndescription: test\n---\nbody\n",
                encoding="utf-8",
            )

        files = SkillMigrationAdapter().discover(tmp_path)

        expected = {f"skill-{i}" for i in range(len(ALL_PROJECT_LOCAL_SKILL_SEARCH_DIRS))}
        assert {f.name for f in files} == expected
        assert all(f.file_type == "skill" for f in files)

    def test_needs_migration_true_for_corpus_false_for_valid(self, tmp_path: Path) -> None:
        stale_dir = tmp_path / ".claude" / "skills" / "audit-bugs"
        stale_dir.mkdir(parents=True)
        stale_path = stale_dir / "SKILL.md"
        stale_path.write_text(
            (_SKILL_CORPUS_DIR / "precontract_audit_bugs.md").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        valid_dir = tmp_path / ".claude" / "skills" / "valid-skill"
        valid_dir.mkdir(parents=True)
        valid_path = valid_dir / "SKILL.md"
        valid_path.write_text(
            "---\nname: valid-skill\ndescription: a clean skill\n---\nbody\n",
            encoding="utf-8",
        )

        adapter = SkillMigrationAdapter()
        stale_file = MigrationFile(
            name="audit-bugs", path=stale_path, file_type="skill", current_version=None
        )
        valid_file = MigrationFile(
            name="valid-skill", path=valid_path, file_type="skill", current_version=None
        )
        assert adapter.needs_migration(stale_file) is True
        assert adapter.needs_migration(valid_file) is False

    @pytest.mark.anyio
    async def test_migrate_inserts_missing_capability_preserving_body(
        self, tmp_path: Path
    ) -> None:
        skill_dir = tmp_path / ".claude" / "skills" / "audit-bugs"
        skill_dir.mkdir(parents=True)
        skill_path = skill_dir / "SKILL.md"
        original = (_SKILL_CORPUS_DIR / "precontract_audit_bugs.md").read_text(encoding="utf-8")
        skill_path.write_text(original, encoding="utf-8")

        adapter = SkillMigrationAdapter()
        file = MigrationFile(
            name="audit-bugs", path=skill_path, file_type="skill", current_version=None
        )
        result = await adapter.migrate(file, temp_dir=tmp_path / "temp")

        assert result.success
        assert result.migrated_content is not None
        assert "claude_dir" in result.migrated_content
        original_body = original.split("---", 2)[2]
        migrated_body = result.migrated_content.split("---", 2)[2]
        assert migrated_body == original_body

    @pytest.mark.anyio
    async def test_migrate_stamps_missing_semantic_version(self, tmp_path: Path) -> None:
        skill_dir = tmp_path / ".claude" / "skills" / "research-helper"
        skill_dir.mkdir(parents=True)
        skill_path = skill_dir / "SKILL.md"
        skill_path.write_text(
            (_SKILL_CORPUS_DIR / "missing_semantic_version.md").read_text(encoding="utf-8"),
            encoding="utf-8",
        )

        adapter = SkillMigrationAdapter()
        file = MigrationFile(
            name="research-helper", path=skill_path, file_type="skill", current_version=None
        )
        result = await adapter.migrate(file, temp_dir=tmp_path / "temp")

        assert result.success
        assert result.migrated_content is not None
        assert "semantic_version" in result.migrated_content

    @pytest.mark.anyio
    async def test_migrate_no_op_when_nothing_deterministic(self, tmp_path: Path) -> None:
        """A skill with no invalidity at all is a MigrationResult(success=True) no-op."""
        skill_dir = tmp_path / ".claude" / "skills" / "valid-skill"
        skill_dir.mkdir(parents=True)
        skill_path = skill_dir / "SKILL.md"
        skill_path.write_text(
            "---\nname: valid-skill\ndescription: a clean skill\n---\nbody\n",
            encoding="utf-8",
        )

        adapter = SkillMigrationAdapter()
        file = MigrationFile(
            name="valid-skill", path=skill_path, file_type="skill", current_version=None
        )
        result = await adapter.migrate(file, temp_dir=tmp_path / "temp")

        assert result.success
        assert result.migrated_content is None
