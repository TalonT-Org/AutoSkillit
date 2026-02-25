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
    version: str | None = None


def _extract_frontmatter(text: str) -> str:
    """Extract YAML metadata text from a frontmatter document.

    If the text starts with ``---``, returns only the text between
    the opening and closing ``---`` delimiters.  Otherwise returns
    the full text unchanged (plain YAML, no frontmatter).
    """
    if not text.startswith("---"):
        return text
    # Skip the opening "---\n"
    after_open = text.index("\n", 0) + 1
    # Find the closing "---"
    close = text.index("\n---", after_open)
    return text[after_open:close]


def _parse_script_metadata(path: Path) -> ScriptInfo:
    """Extract script metadata from a YAML file.

    Handles both single-document YAML and frontmatter format
    (YAML between --- delimiters, followed by arbitrary content).
    """
    text = path.read_text()
    metadata_text = _extract_frontmatter(text)
    data = yaml.safe_load(metadata_text)
    if not isinstance(data, dict):
        raise ValueError(f"YAML metadata must be a mapping: {path}")
    name = data.get("name", "")
    if not name:
        raise ValueError(f"Script missing required 'name' field: {path}")
    return ScriptInfo(
        name=name,
        description=data.get("description", ""),
        summary=data.get("summary", ""),
        path=path,
        version=data.get("autoskillit_version"),
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


def _bundled_workflows_dir() -> Path:
    import autoskillit

    return Path(autoskillit.__file__).parent / "workflows"


def sync_bundled_scripts(project_dir: Path) -> None:
    """Overwrite .autoskillit/scripts/ with bundled workflow YAMLs of the same name.

    Only runs if .autoskillit/ already exists in the project directory.
    Project-specific scripts with no bundled counterpart are left untouched.
    """
    autoskillit_dir = project_dir / ".autoskillit"
    if not autoskillit_dir.is_dir():
        return
    scripts_dir = autoskillit_dir / "scripts"
    scripts_dir.mkdir(exist_ok=True)
    workflows_dir = _bundled_workflows_dir()
    if not workflows_dir.is_dir():
        return
    for src in workflows_dir.glob("*.yaml"):
        (scripts_dir / src.name).write_text(src.read_text())
