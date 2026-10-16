"""Load and materialize captured E2E session evidence fixtures."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

_PROJECTS_TOKEN = "@CLAUDE_PROJECTS@"


def _fixture_dir(name: str) -> Path:
    return Path(__file__).with_name("fixtures") / "e2e_sessions" / name


def load_provenance(name: str) -> dict[str, Any]:
    """Read the provenance record for a captured E2E run."""
    return json.loads((_fixture_dir(name) / "PROVENANCE.json").read_text(encoding="utf-8"))


def _replace_projects_token(path: Path, projects: Path) -> None:
    text = path.read_text(encoding="utf-8")
    path.write_text(text.replace(_PROJECTS_TOKEN, str(projects)), encoding="utf-8")


def materialize_run(
    name: str,
    *,
    trace: Path,
    log_root: Path,
    projects: Path,
) -> dict[str, Any]:
    """Copy a captured run to the given locations and return its envelope."""
    fixture = _fixture_dir(name)
    trace.parent.mkdir(parents=True, exist_ok=True)
    log_root.mkdir(parents=True, exist_ok=True)
    projects.mkdir(parents=True, exist_ok=True)

    shutil.copyfile(fixture / "out/session-events.jsonl", trace)
    shutil.copytree(fixture / "logs", log_root, dirs_exist_ok=True)
    shutil.copytree(fixture / "projects", projects, dirs_exist_ok=True)

    _replace_projects_token(trace, projects)
    _replace_projects_token(log_root / "sessions.jsonl", projects)
    for summary in (log_root / "sessions").glob("*/summary.json"):
        _replace_projects_token(summary, projects)

    return json.loads((fixture / "out/envelope.json").read_text(encoding="utf-8"))
