"""SKILL.md frontmatter validation per agentskills.io specification."""

from __future__ import annotations

import regex as re
from typing import Any

from autoskillit.core import load_yaml

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
    parts = stripped.split("---", 2)
    if len(parts) < 3:
        return {}
    yaml_block = parts[1]
    try:
        return load_yaml(yaml_block) or {}
    except Exception:
        return {}


def validate_skill_frontmatter(frontmatter: dict[str, Any], skill_name: str) -> list[str]:
    """Validate a parsed SKILL.md frontmatter dict against agentskills.io spec.

    Returns an empty list when valid, or a list of human-readable error strings.
    """
    errors: list[str] = []

    # --- name ---
    name = frontmatter.get("name")
    if not isinstance(name, str) or not name:
        errors.append("frontmatter missing required 'name' field")
    elif name != skill_name:
        errors.append(f"frontmatter 'name' is {name!r} but directory is {skill_name!r}")
    elif len(name) > _NAME_MAX_LEN:
        errors.append(f"'name' exceeds {_NAME_MAX_LEN} character limit (got {len(name)})")
    elif not _NAME_PATTERN.match(name):
        errors.append(
            f"'name' {name!r} must match ^[a-z0-9-]+$ (lowercase letters, digits, hyphens only)"
        )

    # --- description ---
    desc = frontmatter.get("description")
    if not isinstance(desc, str) or not desc:
        errors.append("frontmatter missing required 'description' field")
    elif len(desc) > _DESCRIPTION_MAX_LEN:
        errors.append(
            f"'description' exceeds {_DESCRIPTION_MAX_LEN} character limit (got {len(desc)})"
        )
    elif "<" in desc or ">" in desc:
        errors.append("'description' must not contain '<' or '>' characters")

    return errors
