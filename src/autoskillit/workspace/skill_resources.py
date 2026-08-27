"""Canonical static resources compiled into projected skill documents."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from functools import cache
from pathlib import Path

import regex as re

from autoskillit.core import SkillContractError, pkg_root
from autoskillit.workspace.skill_format import read_skill_frontmatter

_RESOURCE_ID_PATTERN = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")
_RESOURCE_KEYS = frozenset({"id", "title", "summary"})
_GFM_ROW_PATTERN = re.compile(r"^\|.*\|\s*$")
_GFM_SEPARATOR_PATTERN = re.compile(r"^\|(?:\s*:?-{3,}:?\s*\|)+\s*$")


@dataclass(frozen=True, slots=True)
class SkillResourceDef:
    """One immutable packaged policy/data resource."""

    id: str
    title: str
    summary: str
    body: str
    digest: str
    table_row_count: int | None


def _table_row_count(body: str) -> int | None:
    tables: list[list[str]] = []
    current: list[str] = []
    for line in body.splitlines():
        if _GFM_ROW_PATTERN.fullmatch(line):
            current.append(line)
        elif current:
            tables.append(current)
            current = []
    if current:
        tables.append(current)
    if len(tables) != 1:
        return None
    table = tables[0]
    if len(table) < 2 or not _GFM_SEPARATOR_PATTERN.fullmatch(table[1]):
        return None
    return len(table) - 2


def _load_resource_file(path: Path) -> SkillResourceDef:
    try:
        source_bytes = path.read_bytes()
    except OSError as exc:
        raise SkillContractError(f"cannot read skill resource {path}: {exc}") from exc
    parsed = read_skill_frontmatter(path)
    if not parsed.is_valid or parsed.data is None:
        raise SkillContractError(f"invalid skill resource frontmatter in {path}: {parsed.error}")
    unknown_keys = set(parsed.data) - _RESOURCE_KEYS
    if "digest" in parsed.data:
        raise SkillContractError(f"skill resource {path} digest is source-derived")
    if unknown_keys:
        raise SkillContractError(
            f"skill resource {path} has unknown frontmatter keys: {sorted(unknown_keys)}"
        )
    values: dict[str, str] = {}
    for field_name in ("id", "title", "summary"):
        value = parsed.data.get(field_name)
        if not isinstance(value, str) or not value.strip():
            raise SkillContractError(
                f"skill resource {path} field {field_name!r} must be a non-empty string"
            )
        values[field_name] = value.strip()
    resource_id = values["id"]
    if not _RESOURCE_ID_PATTERN.fullmatch(resource_id):
        raise SkillContractError(f"invalid skill resource id {resource_id!r}")
    if resource_id != path.stem:
        raise SkillContractError(
            f"skill resource id {resource_id!r} must match filename stem {path.stem!r}"
        )
    if not parsed.body.strip():
        raise SkillContractError(f"skill resource {resource_id!r} body must not be empty")
    return SkillResourceDef(
        id=resource_id,
        title=values["title"],
        summary=values["summary"],
        body=parsed.body,
        digest=hashlib.sha256(source_bytes).hexdigest(),
        table_row_count=_table_row_count(parsed.body),
    )


@cache
def load_skill_resource(resource_id: str) -> SkillResourceDef:
    """Resolve one registered resource id from the installed package root."""
    if not isinstance(resource_id, str) or not _RESOURCE_ID_PATTERN.fullmatch(resource_id):
        raise SkillContractError(f"invalid skill resource id {resource_id!r}")
    resources: dict[str, SkillResourceDef] = {}
    resource_dir = pkg_root() / "skill_resources"
    for path in sorted(resource_dir.glob("*.md")):
        resource = _load_resource_file(path)
        if resource.id in resources:
            raise SkillContractError(f"duplicate skill resource id {resource.id!r}")
        resources[resource.id] = resource
    try:
        return resources[resource_id]
    except KeyError as exc:
        raise SkillContractError(f"unknown skill resource id {resource_id!r}") from exc


__all__ = ["SkillResourceDef", "load_skill_resource"]
