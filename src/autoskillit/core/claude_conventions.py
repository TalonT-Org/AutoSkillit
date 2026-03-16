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
