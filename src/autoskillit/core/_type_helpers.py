"""Core skill name resolution and text-processing helpers.

Zero autoskillit imports outside this sub-package. Provides extract_skill_name,
resolve_target_skill, and truncate_text.
"""

from __future__ import annotations

import re

from ._type_constants import AUTOSKILLIT_SKILL_PREFIX, SKILL_COMMAND_PREFIX
from ._type_enums import SkillSource
from ._type_protocols import TargetSkillResolver

__all__ = [
    "extract_skill_name",
    "resolve_target_skill",
    "truncate_text",
]

_SKILL_CMD_RE = re.compile(r"^/(?:autoskillit:)?([\w-]+)")


def extract_skill_name(skill_command: str) -> str | None:
    """Extract the bare skill name from a skill_command string.

    Handles both ``/autoskillit:make-plan ...`` and ``/make-plan ...`` forms.
    Returns None if the command is not a slash-command.
    """
    m = _SKILL_CMD_RE.match(skill_command.strip())
    return m.group(1) if m else None


def resolve_target_skill(
    skill_command: str,
    resolver: TargetSkillResolver,
) -> tuple[str, str | None]:
    """Resolve a skill_command to the correct invocation namespace.

    Returns (resolved_command, skill_name).
    skill_name is None if skill_command is not a slash command.

    - Skills in ``skills/`` (BUNDLED) → ``/autoskillit:name`` namespace
    - Skills in ``skills_extended/`` (BUNDLED_EXTENDED) → ``/name`` namespace
    """
    name = extract_skill_name(skill_command)
    if name is None:
        return skill_command, None

    info = resolver.resolve(name)
    if info is None:
        return skill_command, name

    # Determine correct prefix based on physical location
    if info.source == SkillSource.BUNDLED:
        correct_prefix = AUTOSKILLIT_SKILL_PREFIX + name
    else:
        correct_prefix = SKILL_COMMAND_PREFIX + name

    # Reconstruct: replace the skill reference, preserve trailing arguments
    stripped = skill_command.strip()
    m = _SKILL_CMD_RE.match(stripped)
    if m is None:
        raise RuntimeError(f"regex failed after extract_skill_name succeeded: {stripped!r}")
    remainder = stripped[m.end() :]
    return correct_prefix + remainder, name


def truncate_text(text: str, max_len: int = 5000) -> str:
    """Truncate text to max_len, appending a count of truncated chars."""
    if len(text) <= max_len:
        return text
    return f"...[truncated {len(text) - max_len} chars]...\n" + text[-max_len:]
