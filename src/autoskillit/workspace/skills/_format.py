"""SKILL.md frontmatter validation per agentskills.io specification."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import regex as re

from autoskillit.core import SkillExecutionRole, YAMLError, get_logger, load_yaml

logger = get_logger(__name__)

__all__ = [
    "SkillFrontmatterParseError",
    "SkillFrontmatterParseResult",
    "parse_frontmatter_content",
    "read_skill_frontmatter",
    "validate_skill_frontmatter",
]

_NAME_PATTERN = re.compile(r"^[a-z0-9-]+$")
_NAME_MAX_LEN = 64
_DESCRIPTION_MAX_LEN = 1024


def _normalize_exploration_vector_body(value: str) -> str:
    """Return the canonical newline form for exploration vector bodies."""
    return value.replace("\r\n", "\n").replace("\r", "\n").strip("\n")


SkillFrontmatterParseError = Literal[
    "unreadable",
    "missing_opening_delimiter",
    "missing_closing_delimiter",
    "malformed_yaml",
    "non_mapping",
    "invalid_execution_role",
]


@dataclass(frozen=True, slots=True)
class SkillFrontmatterParseResult:
    """Lossless result of parsing one SKILL.md machine contract."""

    content: str
    data: dict[str, Any] | None
    execution_role: SkillExecutionRole | None = None
    frontmatter_text: str = ""
    body: str = ""
    error: SkillFrontmatterParseError | None = None

    @property
    def is_valid(self) -> bool:
        return self.error is None and self.data is not None


def _parse_failure(
    content: str,
    error: SkillFrontmatterParseError,
    *,
    frontmatter_text: str = "",
    body: str = "",
) -> SkillFrontmatterParseResult:
    return SkillFrontmatterParseResult(
        content=content,
        data=None,
        frontmatter_text=frontmatter_text,
        body=body,
        error=error,
    )


def parse_frontmatter_content(content: str) -> SkillFrontmatterParseResult:
    """Parse YAML frontmatter without collapsing distinct failure modes."""
    stripped = content.lstrip()
    lines = stripped.splitlines(keepends=True)
    if not lines or lines[0].rstrip("\r\n") != "---":
        return _parse_failure(content, "missing_opening_delimiter")

    close_idx: int | None = None
    for i, line in enumerate(lines[1:], start=1):
        if line.rstrip("\r\n") == "---":
            close_idx = i
            break
    if close_idx is None:
        return _parse_failure(content, "missing_closing_delimiter")

    yaml_block_with_newline = "".join(lines[1:close_idx])
    yaml_block = yaml_block_with_newline.rstrip("\r\n")
    body = "".join(lines[close_idx + 1 :])
    try:
        loaded: Any = load_yaml(yaml_block)
    except YAMLError:
        logger.warning("parse_frontmatter_malformed_yaml", exc_info=True)
        return _parse_failure(
            content,
            "malformed_yaml",
            frontmatter_text=yaml_block,
            body=body,
        )
    if loaded is None:
        loaded = {}
    if not isinstance(loaded, dict):
        return _parse_failure(
            content,
            "non_mapping",
            frontmatter_text=yaml_block,
            body=body,
        )
    role_raw = loaded.get("execution_role", SkillExecutionRole.SESSION.value)
    try:
        execution_role = SkillExecutionRole(role_raw)
    except (TypeError, ValueError):
        return _parse_failure(
            content,
            "invalid_execution_role",
            frontmatter_text=yaml_block,
            body=body,
        )
    return SkillFrontmatterParseResult(
        content=content,
        data=loaded,
        execution_role=execution_role,
        frontmatter_text=yaml_block,
        body=body,
    )


def read_skill_frontmatter(path: Path) -> SkillFrontmatterParseResult:
    """Read and parse one SKILL.md, preserving unreadable as a typed failure."""
    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return _parse_failure("", "unreadable")
    return parse_frontmatter_content(content)


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

    write_paths = frontmatter.get("write_paths")
    if write_paths is not None:
        if not isinstance(write_paths, list):
            errors.append("'write_paths' must be a list of strings")
        else:
            for i, wp in enumerate(write_paths):
                if not isinstance(wp, str) or not wp:
                    errors.append(f"'write_paths[{i}]' must be a non-empty string")
                elif ".." in wp:
                    errors.append(f"'write_paths[{i}]' must not contain '..' (path traversal)")
                elif not (
                    wp.startswith("{{AUTOSKILLIT_TEMP}}/") or wp.startswith(".autoskillit/temp/")
                ):
                    errors.append(
                        f"'write_paths[{i}]' must start with "
                        "'{{AUTOSKILLIT_TEMP}}/' "
                        f"(got {wp!r})"
                    )

    return errors
