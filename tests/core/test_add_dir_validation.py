"""Tests for ValidatedAddDir and validate_add_dir."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from autoskillit.core import ValidatedAddDir
from autoskillit.core.claude_conventions import (
    LayoutError,
    validate_add_dir,
    validate_project_local_skill_dir,
)

pytestmark = [pytest.mark.layer("core"), pytest.mark.small]


class TestValidatedAddDir:
    """ValidatedAddDir is an opaque wrapper with str/fspath support."""

    def test_str_returns_path(self) -> None:
        vd = ValidatedAddDir(path="/some/dir")
        assert str(vd) == "/some/dir"

    def test_fspath_returns_path(self) -> None:
        vd = ValidatedAddDir(path="/some/dir")
        assert os.fspath(vd) == "/some/dir"

    def test_frozen(self) -> None:
        vd = ValidatedAddDir(path="/some/dir")
        with pytest.raises(AttributeError):
            vd.path = "/other"  # type: ignore[misc]


class TestValidateAddDir:
    """validate_add_dir enforces the .claude/skills/<name>/SKILL.md convention."""

    def test_valid_layout_returns_validated_add_dir(self, tmp_path: Path) -> None:
        skill_dir = tmp_path / ".claude" / "skills" / "test-skill"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("# Test")

        result = validate_add_dir(tmp_path)
        assert isinstance(result, ValidatedAddDir)
        assert result.path == str(tmp_path)

    def test_missing_claude_skills_raises_layout_error(self, tmp_path: Path) -> None:
        with pytest.raises(LayoutError, match="does not contain .claude/skills/"):
            validate_add_dir(tmp_path)

    def test_empty_claude_skills_raises_layout_error(self, tmp_path: Path) -> None:
        (tmp_path / ".claude" / "skills").mkdir(parents=True)
        with pytest.raises(LayoutError, match="contains no SKILL.md files"):
            validate_add_dir(tmp_path)

    def test_skills_extended_flat_layout_raises(self) -> None:
        """skills_extended/ has flat layout — validate_add_dir must reject it."""
        from autoskillit.core.paths import pkg_root

        skills_ext = pkg_root() / "skills_extended"
        with pytest.raises(LayoutError):
            validate_add_dir(skills_ext)


class TestValidateProjectLocalSkillDir:
    """validate_project_local_skill_dir: None when not project_local_skills_capable,
    delegates to validate_add_dir otherwise."""

    @staticmethod
    def _make_backend(*, project_local_skills_capable: bool) -> MagicMock:
        b = MagicMock()
        b.capabilities.project_local_skills_capable = project_local_skills_capable
        return b

    def test_claude_backend_claude_layout_returns_validated_add_dir(self, tmp_path: Path) -> None:
        skill_dir = tmp_path / ".claude" / "skills" / "test-skill"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("# Test")
        result = validate_project_local_skill_dir(
            tmp_path, self._make_backend(project_local_skills_capable=True)
        )
        assert isinstance(result, ValidatedAddDir)

    def test_codex_backend_returns_none(self, tmp_path: Path) -> None:
        skill_dir = tmp_path / ".claude" / "skills" / "test-skill"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("# Test")
        result = validate_project_local_skill_dir(
            tmp_path, self._make_backend(project_local_skills_capable=False)
        )
        assert result is None

    def test_claude_backend_codex_layout_returns_none(self, tmp_path: Path) -> None:
        codex_skill = tmp_path / ".codex" / "skills" / "test-skill"
        codex_skill.mkdir(parents=True)
        (codex_skill / "SKILL.md").write_text("# Test")
        result = validate_project_local_skill_dir(
            tmp_path, self._make_backend(project_local_skills_capable=True)
        )
        assert result is None

    def test_codex_backend_claude_layout_returns_none(self, tmp_path: Path) -> None:
        skill_dir = tmp_path / ".claude" / "skills" / "test-skill"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("# Test")
        result = validate_project_local_skill_dir(
            tmp_path, self._make_backend(project_local_skills_capable=False)
        )
        assert result is None

    def test_empty_dir_returns_none_claude(self, tmp_path: Path) -> None:
        result = validate_project_local_skill_dir(
            tmp_path, self._make_backend(project_local_skills_capable=True)
        )
        assert result is None

    def test_empty_dir_returns_none_codex(self, tmp_path: Path) -> None:
        result = validate_project_local_skill_dir(
            tmp_path, self._make_backend(project_local_skills_capable=False)
        )
        assert result is None
