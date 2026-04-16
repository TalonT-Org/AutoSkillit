"""Tests for ValidatedAddDir and validate_add_dir."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from autoskillit.core import ValidatedAddDir
from autoskillit.core.claude_conventions import LayoutError, validate_add_dir

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
