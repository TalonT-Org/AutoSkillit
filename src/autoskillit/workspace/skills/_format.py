"""SKILL.md frontmatter validation per agentskills.io specification."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import regex as re

from autoskillit.core import SkillExecutionRole, YAMLError, get_logger, load_yaml
from autoskillit.hooks._write_scope import WriteScope, WriteScopeError, decode_write_scope

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

WriteScopeIssueKind = Literal["undeclared", "invalid"]

_UNDECLARED_WRITE_SCOPE_DETAIL = (
    "write_paths is required: declare a non-empty list of AutoSkillit temp directories, "
    "`unrestricted`, or `inherit`"
)


@dataclass(frozen=True, slots=True)
class SkillFrontmatterParseResult:
    """Lossless result of parsing one SKILL.md machine contract."""

    content: str
    data: dict[str, Any] | None
    write_scope: WriteScope | None = None
    write_scope_issue: tuple[WriteScopeIssueKind, str] | None = None
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
    write_scope, write_scope_issue = _decode_frontmatter_write_scope(loaded)
    return SkillFrontmatterParseResult(
        content=content,
        data=loaded,
        write_scope=write_scope,
        write_scope_issue=write_scope_issue,
        execution_role=execution_role,
        frontmatter_text=yaml_block,
        body=body,
    )


def _decode_frontmatter_write_scope(
    frontmatter: dict[str, Any],
) -> tuple[WriteScope | None, tuple[WriteScopeIssueKind, str] | None]:
    if "write_paths" not in frontmatter:
        return None, ("undeclared", _UNDECLARED_WRITE_SCOPE_DETAIL)
    try:
        return decode_write_scope(frontmatter["write_paths"]), None
    except WriteScopeError as exc:
        return None, ("invalid", str(exc))


def read_skill_frontmatter(path: Path) -> SkillFrontmatterParseResult:
    """Read and parse one SKILL.md, preserving unreadable as a typed failure."""
    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return _parse_failure("", "unreadable")
    return parse_frontmatter_content(content)


def _validate_frontmatter_name(name: object, skill_name: str) -> list[str]:
    errors: list[str] = []
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
    return errors


def _validate_frontmatter_description(description: object) -> list[str]:
    errors: list[str] = []
    if not isinstance(description, str) or not description:
        errors.append("frontmatter missing required 'description' field")
    else:
        if len(description) > _DESCRIPTION_MAX_LEN:
            errors.append(
                f"'description' exceeds {_DESCRIPTION_MAX_LEN} character limit "
                f"(got {len(description)})"
            )
        if "<" in description or ">" in description:
            errors.append("'description' must not contain '<' or '>' characters")
    return errors


def validate_skill_frontmatter(frontmatter: dict[str, Any], skill_name: str) -> list[str]:
    """Validate a parsed SKILL.md frontmatter dict against agentskills.io spec.

    Returns an empty list when valid, or a list of human-readable error strings.
    """
    errors: list[str] = []
    errors.extend(_validate_frontmatter_name(frontmatter.get("name"), skill_name))
    errors.extend(_validate_frontmatter_description(frontmatter.get("description")))
    _, write_scope_issue = _decode_frontmatter_write_scope(frontmatter)
    if write_scope_issue is not None:
        errors.append(write_scope_issue[1])

    return errors
