"""Pipeline script discovery from .autoskillit/scripts/."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from autoskillit.workflow_loader import load_workflow


@dataclass
class ScriptInfo:
    name: str
    description: str
    summary: str
    path: Path


def list_scripts(project_dir: Path) -> list[ScriptInfo]:
    """Discover pipeline scripts from .autoskillit/scripts/."""
    scripts_dir = project_dir / ".autoskillit" / "scripts"
    if not scripts_dir.is_dir():
        return []

    result: list[ScriptInfo] = []
    for f in sorted(scripts_dir.iterdir()):
        if f.suffix in (".yaml", ".yml") and f.is_file():
            try:
                wf = load_workflow(f)
                if wf.name:
                    result.append(
                        ScriptInfo(
                            name=wf.name,
                            description=wf.description,
                            summary=wf.summary,
                            path=f,
                        )
                    )
            except Exception:
                pass  # skip malformed files
    return result


def load_script(project_dir: Path, name: str) -> str | None:
    """Load a pipeline script by name, returning raw YAML content."""
    scripts = list_scripts(project_dir)
    match = next((s for s in scripts if s.name == name), None)
    if match is None:
        return None
    return match.path.read_text()
