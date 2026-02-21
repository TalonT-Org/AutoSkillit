"""Pipeline script discovery from .autoskillit/scripts/."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from autoskillit.types import LoadReport, LoadResult


@dataclass
class ScriptInfo:
    name: str
    description: str
    summary: str
    path: Path


def _parse_script_metadata(path: Path) -> ScriptInfo:
    """Extract script metadata from a YAML file.

    Handles both single-document YAML and frontmatter format
    (multi-document YAML with --- delimiters).
    """
    text = path.read_text()
    docs = list(yaml.safe_load_all(text))
    if not docs:
        raise ValueError(f"Empty YAML file: {path}")
    data = docs[0]
    if not isinstance(data, dict):
        raise ValueError(f"First YAML document must be a mapping: {path}")
    name = data.get("name", "")
    if not name:
        raise ValueError(f"Script missing required 'name' field: {path}")
    return ScriptInfo(
        name=name,
        description=data.get("description", ""),
        summary=data.get("summary", ""),
        path=path,
    )


def list_scripts(project_dir: Path) -> LoadResult[ScriptInfo]:
    """Discover pipeline scripts from .autoskillit/scripts/."""
    scripts_dir = project_dir / ".autoskillit" / "scripts"
    if not scripts_dir.is_dir():
        return LoadResult(items=[], errors=[])

    items: list[ScriptInfo] = []
    errors: list[LoadReport] = []
    for f in sorted(scripts_dir.iterdir()):
        if f.suffix in (".yaml", ".yml") and f.is_file():
            try:
                info = _parse_script_metadata(f)
                items.append(info)
            except Exception as exc:
                errors.append(LoadReport(path=f, error=str(exc)))
    return LoadResult(items=items, errors=errors)


def load_script(project_dir: Path, name: str) -> str | None:
    """Load a pipeline script by name, returning raw YAML content."""
    result = list_scripts(project_dir)
    match = next((s for s in result.items if s.name == name), None)
    if match is None:
        return None
    return match.path.read_text()
