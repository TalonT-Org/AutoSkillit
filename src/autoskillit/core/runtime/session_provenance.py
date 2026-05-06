"""Session provenance store — records ownership tuples for L2 food truck sessions."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ProvenanceRecord:
    """Immutable ownership tuple written when a food truck session is dispatched."""

    session_id: str
    caller_session_id: str
    kitchen_id: str
    dispatch_id: str
    recipe_name: str
    step_name: str
    timestamp: str


def provenance_path(project_dir: Path | None = None) -> Path:
    """Resolve the provenance JSONL path.

    Resolution order:
    1. AUTOSKILLIT_STATE_DIR / "session_provenance.jsonl" if set
    2. {project_dir}/.autoskillit/temp/session_provenance.jsonl
       (or .autoskillit/temp/{campaign}/session_provenance.jsonl when
       AUTOSKILLIT_CAMPAIGN_ID is set)
    """
    override = os.environ.get("AUTOSKILLIT_STATE_DIR", "")
    if override:
        return Path(override) / "session_provenance.jsonl"

    base = (project_dir or Path.cwd()) / ".autoskillit" / "temp"
    campaign = os.environ.get("AUTOSKILLIT_CAMPAIGN_ID", "")
    if campaign:
        base = base / campaign
    return base / "session_provenance.jsonl"


def write_provenance_record(record: ProvenanceRecord, project_dir: Path | None = None) -> None:
    """Append one JSON line to the provenance file.

    Creates parent directories if needed. Catches and logs OSError — never raises.
    """
    path = provenance_path(project_dir)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record.__dict__, sort_keys=True) + "\n")
    except OSError:
        pass


def read_provenance_for_session(
    session_id: str, project_dir: Path | None = None
) -> dict[str, str] | None:
    """Scan the provenance JSONL for a matching session_id.

    Returns the parsed record dict or None. Catches OSError and JSONDecodeError —
    never raises.
    """
    path = provenance_path(project_dir)
    if not path.is_file():
        return None
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
                if record.get("session_id") == session_id:
                    return record
            except json.JSONDecodeError:
                continue
    except OSError:
        pass
    return None
