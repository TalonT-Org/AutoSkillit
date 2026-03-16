"""Contract tests for Claude Code external interface conventions.

ALL path components and string values in this file are HARDCODED STRING LITERALS.
NEVER replace with imports from core/claude_conventions.py — that re-creates
the "tests mirror implementation" failure mode this module prevents.

Reference: temp/investigation-ephemeral-skill-dir-layout-bug.md
Governance model mirrors: tests/execution/test_flag_contracts.py
"""

from __future__ import annotations

from pathlib import Path

# ---------------------------------------------------------------------------
# CC-DIR: ClaudeDirectoryConventions value pinning
# Mirror of TestClaudeFlagValues in test_flag_contracts.py — same pattern.
# ---------------------------------------------------------------------------


class TestClaudeDirectoryConventions:
    """Pin each ClaudeDirectoryConventions constant to a hardcoded string literal.

    CRITICAL: These assertions use string literals, NOT re-imports of the constants.
    If a constant's string value changes, the production code drifts from the
    Claude Code specification, and these tests catch it at the first CI run.
    """

    def test_add_dir_skills_subdir_value(self) -> None:
        """--add-dir root: Claude Code discovers skills at .claude/skills/<name>/SKILL.md."""
        from autoskillit.core.claude_conventions import ClaudeDirectoryConventions

        assert str(ClaudeDirectoryConventions.ADD_DIR_SKILLS_SUBDIR) == ".claude/skills"

    def test_plugin_dir_skills_subdir_value(self) -> None:
        """--plugin-dir root: Claude Code discovers skills at skills/<name>/SKILL.md."""
        from autoskillit.core.claude_conventions import ClaudeDirectoryConventions

        assert str(ClaudeDirectoryConventions.PLUGIN_DIR_SKILLS_SUBDIR) == "skills"

    def test_skill_filename_value(self) -> None:
        """Each skill directory contains a SKILL.md file."""
        from autoskillit.core.claude_conventions import ClaudeDirectoryConventions

        assert ClaudeDirectoryConventions.SKILL_FILENAME == "SKILL.md"

    def test_add_dir_full_pattern_is_dot_claude_skills_name_skill_md(self) -> None:
        """Composed path for a skill at an --add-dir root matches the literal pattern."""
        from autoskillit.core.claude_conventions import ClaudeDirectoryConventions

        composed = (
            ClaudeDirectoryConventions.ADD_DIR_SKILLS_SUBDIR
            / "my-skill"
            / ClaudeDirectoryConventions.SKILL_FILENAME
        )
        # String literal — not constructed from the constants themselves
        assert str(composed) == ".claude/skills/my-skill/SKILL.md"


# ---------------------------------------------------------------------------
# CC-001: --add-dir layout behavioral guard
# Path components are HARDCODED STRING LITERALS — do NOT replace with constants.
# ---------------------------------------------------------------------------


class TestAddDirLayoutContract:
    """Guard: init_session must write .claude/skills/<name>/SKILL.md.

    CRITICAL: Path components (".claude", "skills", "SKILL.md") are literal
    strings here. Do NOT replace them with _SKILLS_SUBDIR or
    ClaudeDirectoryConventions.ADD_DIR_SKILLS_SUBDIR — that would re-create
    the "tests mirror implementation" failure mode this guard was designed
    to prevent. If the constant drifts, TestClaudeDirectoryConventions catches
    it; if the behavior drifts, this test catches it. Both layers are needed.

    Claude Code --add-dir discovery convention (external spec):
        <add_dir_root>/.claude/skills/<name>/SKILL.md
    """

    def test_init_session_writes_skills_at_add_dir_convention_path(self, tmp_path: Path) -> None:
        from autoskillit.workspace.session_skills import (
            DefaultSessionSkillManager,
            SkillsDirectoryProvider,
        )

        provider = SkillsDirectoryProvider()
        mgr = DefaultSessionSkillManager(provider, ephemeral_root=tmp_path)
        session_dir = mgr.init_session("cc001-contract-test", cook_session=True)

        # ".claude", "skills", "SKILL.md" are literal strings — NOT from any constant.
        discovered = list(session_dir.glob(".claude/skills/*/SKILL.md"))
        assert len(discovered) > 0, (
            "init_session must write skills to .claude/skills/<name>/SKILL.md "
            "(Claude Code --add-dir convention). "
            "If this fails, session_skills._SKILLS_SUBDIR has regressed."
        )

    def test_init_session_no_flat_skills_at_session_root(self, tmp_path: Path) -> None:
        """Anti-regression: the pre-fix flat layout must not reappear."""
        from autoskillit.workspace.session_skills import (
            DefaultSessionSkillManager,
            SkillsDirectoryProvider,
        )

        provider = SkillsDirectoryProvider()
        mgr = DefaultSessionSkillManager(provider, ephemeral_root=tmp_path)
        session_dir = mgr.init_session("cc001-flat-regression", cook_session=True)

        # Only .claude/ is allowed as a top-level child of session_dir
        flat_skills = list(session_dir.glob("*/SKILL.md"))
        flat_non_claude = [f for f in flat_skills if f.parts[-3] != ".claude"]
        assert not flat_non_claude, (
            f"Flat layout detected: {flat_non_claude}. "
            "Skills must be nested under .claude/skills/, not at session root. "
            "This is the CC-001 regression pattern (pre-v0.5.1 bug)."
        )


# ---------------------------------------------------------------------------
# CC-002: --plugin-dir layout behavioral guard
# Path components are HARDCODED STRING LITERALS.
# ---------------------------------------------------------------------------


class TestPluginDirLayoutContract:
    """Guard: bundled skills must be at pkg_root()/skills/<name>/SKILL.md.

    CRITICAL: Path components ("skills", "open-kitchen", "SKILL.md") are
    literal strings. Do NOT replace with bundled_skills_dir() or
    ClaudeDirectoryConventions.PLUGIN_DIR_SKILLS_SUBDIR — those would create
    the same mirror-test vulnerability.

    Claude Code --plugin-dir discovery convention (external spec):
        <plugin_dir_root>/skills/<name>/SKILL.md
    """

    def test_bundled_skills_subdir_exists_at_plugin_dir_path(self) -> None:
        from autoskillit.core.paths import pkg_root

        # "skills" is a literal — not from any resolver or convention constant
        skills_subdir = pkg_root() / "skills"
        assert skills_subdir.is_dir(), (
            f"--plugin-dir root {pkg_root()} has no 'skills/' subdirectory. "
            "Claude Code --plugin-dir convention requires <root>/skills/<name>/SKILL.md."
        )

    def test_plugin_dir_skills_contain_skill_md_files(self) -> None:
        from autoskillit.core.paths import pkg_root

        # "skills" and "SKILL.md" are literals
        skill_files = list((pkg_root() / "skills").glob("*/SKILL.md"))
        assert len(skill_files) > 0, (
            "No SKILL.md files found at pkg_root()/skills/<name>/SKILL.md. "
            "The --plugin-dir convention requires skills at this exact path."
        )

    def test_open_kitchen_skill_at_plugin_dir_convention_path(self) -> None:
        """Spot-check Tier 1: open-kitchen must be at the literal plugin-dir path."""
        from autoskillit.core.paths import pkg_root

        # "skills", "open-kitchen", "SKILL.md" are all literals
        path = pkg_root() / "skills" / "open-kitchen" / "SKILL.md"
        assert path.exists(), (
            f"open-kitchen SKILL.md not found at {path}. "
            "If this fails, the --plugin-dir Tier 1 skill layout has changed."
        )
