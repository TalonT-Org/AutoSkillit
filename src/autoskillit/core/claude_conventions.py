"""Canonical Claude Code directory and file layout conventions.

These constants represent external specifications that Claude Code enforces
when discovering skills. They are NOT internal autoskillit choices.

IMPORTANT: Tests in tests/contracts/test_claude_code_interface_contracts.py
use HARDCODED STRING LITERALS to assert these values — never imports of these
constants. This is intentional: it makes tests independent of this module,
so constant drift is caught rather than silently propagated.

See also: core/types.py ClaudeFlags — the equivalent registry for CLI flags.
"""

from __future__ import annotations

from pathlib import Path

from autoskillit.core.types import ValidatedAddDir


class LayoutError(ValueError):
    """Raised when a directory does not satisfy Claude Code --add-dir layout conventions."""


class ClaudeDirectoryConventions:
    """Claude Code skill discovery directory layout conventions.

    Two conventions exist, one per Claude Code flag:

    ``--add-dir <root>``
        Skills are discovered at ``<root>/.claude/skills/<name>/SKILL.md``.
        Used for ephemeral session directories created by
        ``DefaultSessionSkillManager.init_session()``.

    ``--plugin-dir <root>``
        Skills are discovered at ``<root>/skills/<name>/SKILL.md``.
        Used for the autoskillit package root (``pkg_root()``).
    """

    #: Subpath appended to an ``--add-dir`` root to locate skills.
    ADD_DIR_SKILLS_SUBDIR: Path = Path(".claude") / "skills"

    #: Subpath appended to a ``--plugin-dir`` root to locate skills.
    PLUGIN_DIR_SKILLS_SUBDIR: Path = Path("skills")

    #: Filename expected inside each ``<name>/`` directory.
    SKILL_FILENAME: str = "SKILL.md"


def validate_add_dir(path: Path) -> ValidatedAddDir:
    """Validate that a directory satisfies the --add-dir convention.

    Raises LayoutError if ``path/.claude/skills/`` does not exist or
    contains no ``SKILL.md`` files.
    """
    skills_subdir = path / ClaudeDirectoryConventions.ADD_DIR_SKILLS_SUBDIR
    if not skills_subdir.is_dir():
        raise LayoutError(f"{path} does not contain .claude/skills/ subdirectory")
    skill_files = list(skills_subdir.glob("*/SKILL.md"))
    if not skill_files:
        raise LayoutError(f"{path}/.claude/skills/ contains no SKILL.md files")
    return ValidatedAddDir(path=str(path))
