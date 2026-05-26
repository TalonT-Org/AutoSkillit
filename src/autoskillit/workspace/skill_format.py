"""SKILL.md frontmatter validation per agentskills.io specification."""

from __future__ import annotations

from typing import Any

import regex as re

from autoskillit.core import get_logger, load_yaml

logger = get_logger(__name__)

__all__ = ["parse_frontmatter_content", "validate_skill_frontmatter"]

_NAME_PATTERN = re.compile(r"^[a-z0-9-]+$")
_NAME_MAX_LEN = 64
_DESCRIPTION_MAX_LEN = 1024


def parse_frontmatter_content(content: str) -> dict[str, Any]:
    """Parse YAML frontmatter from a SKILL.md content string.

    Returns ``{}`` when frontmatter is absent or malformed.
    """
    stripped = content.lstrip()
    if not stripped.startswith("---"):
        return {}
    lines = stripped.split("\n")
    if len(lines) < 2:
        return {}
    close_idx = None
    for i, line in enumerate(lines[1:], start=1):
        if line.rstrip("\r") == "---":
            close_idx = i
            break
    if close_idx is None:
        return {}
    yaml_block = "\n".join(lines[1:close_idx])
    try:
        return load_yaml(yaml_block) or {}
    except Exception:
        logger.warning("parse_frontmatter_malformed_yaml", exc_info=True)
        return {}


def validate_skill_frontmatter(frontmatter: dict[str, Any], skill_name: str) -> list[str]:
    """Validate a parsed SKILL.md frontmatter dict against agentskills.io spec.

    Returns an empty list when valid, or a list of human-readable error strings.
    """
    errors: list[str] = []

    name = frontmatter.get("name")
    if not isinstance(name, str) or not name:
        errors.append("frontmatter missing required 'name' field")
    else:
        if name != skill_name:
            errors.append(f"frontmatter 'name' is {name!r} but directory is {skill_name!r}")
        if len(name) > _NAME_MAX_LEN:
            errors.append(f"'name' exceeds {_NAME_MAX_LEN} character limit (got {len(name)})")
        if not _NAME_PATTERN.match(name):
            errors.append(
                f"'name' {name!r} must match ^[a-z0-9-]+$"
                " (lowercase letters, digits, hyphens only)"
            )

    desc = frontmatter.get("description")
    if not isinstance(desc, str) or not desc:
        errors.append("frontmatter missing required 'description' field")
    else:
        if len(desc) > _DESCRIPTION_MAX_LEN:
            errors.append(
                f"'description' exceeds {_DESCRIPTION_MAX_LEN} character limit (got {len(desc)})"
            )
        if "<" in desc or ">" in desc:
            errors.append("'description' must not contain '<' or '>' characters")

    return errors
