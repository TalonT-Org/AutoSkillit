"""Snapshot and restore ephemeral skill directories for record/replay sessions."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from autoskillit.core import ValidatedAddDir

SKILLS_SNAPSHOT_DIR = "skill_snapshots"
_EPHEMERAL_SESSION_PATTERN = "autoskillit-sessions"
_GATED_PATTERN = re.compile(r"disable-model-invocation\s*:\s*true", re.IGNORECASE)


def _extract_ephemeral_add_dir(cmd: list[str]) -> Path | None:
    """Extract the ephemeral skill dir path from --add-dir CLI args.

    Identifies ephemeral dirs by the 'autoskillit-sessions' path component.
    Returns the first matching --add-dir path, or None.
    """
    for i, token in enumerate(cmd):
        if token == "--add-dir" and i + 1 < len(cmd):
            candidate = cmd[i + 1]
            if _EPHEMERAL_SESSION_PATTERN in candidate:
                return Path(candidate)
    return None


def build_skills_manifest(skills_dir: Path) -> dict[str, Any]:
    """Build a manifest dict from a .claude/skills/ directory."""
    skills: dict[str, Any] = {}
    for skill_dir in sorted(skills_dir.iterdir()):
        if not skill_dir.is_dir():
            continue
        skill_md = skill_dir / "SKILL.md"
        if not skill_md.exists():
            continue
        content = skill_md.read_bytes()
        sha256 = hashlib.sha256(content).hexdigest()
        gated = bool(_GATED_PATTERN.search(content.decode("utf-8", errors="replace")))
        skills[skill_dir.name] = {
            "content_sha256": sha256,
            "size_bytes": len(content),
            "gated": gated,
        }
    return {
        "schema_version": 1,
        "captured_at": datetime.now(tz=timezone.utc).isoformat(),
        "skill_count": len(skills),
        "skills": skills,
    }


def snapshot_skill_dir(
    scenario_dir: Path, step_name: str, add_dir_path: Path
) -> Path | None:
    """Copy the ephemeral skill dir tree into the scenario dir.

    Copies {add_dir_path}/.claude/skills/ →
           {scenario_dir}/skill_snapshots/{step_name}/.claude/skills/
    Writes manifest.json alongside the .claude/ dir.
    Returns the snapshot dir path, or None if no skills to snapshot.
    """
    skills_src = add_dir_path / ".claude" / "skills"
    if not skills_src.exists() or not skills_src.is_dir():
        return None

    skill_subdirs = [d for d in skills_src.iterdir() if d.is_dir()]
    if not skill_subdirs:
        return None

    snapshot_dir = scenario_dir / SKILLS_SNAPSHOT_DIR / step_name
    snapshot_dir.mkdir(parents=True, exist_ok=True)

    dest_skills = snapshot_dir / ".claude" / "skills"
    if dest_skills.exists():
        shutil.rmtree(dest_skills)
    shutil.copytree(skills_src, dest_skills)

    manifest = build_skills_manifest(skills_src)
    (snapshot_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )

    return snapshot_dir


def restore_skill_snapshot(
    snapshot_path: Path, ephemeral_root: Path, session_id: str
) -> ValidatedAddDir | None:
    """Restore skill dir from a snapshot into a new ephemeral session dir.

    Copies {snapshot_path}/.claude/skills/ →
           {ephemeral_root}/{session_id}/.claude/skills/
    Returns ValidatedAddDir pointing to {ephemeral_root}/{session_id}.
    """
    skills_src = snapshot_path / ".claude" / "skills"
    if not skills_src.exists():
        return None

    session_dir = ephemeral_root / session_id
    dest_skills = session_dir / ".claude" / "skills"
    dest_skills.mkdir(parents=True, exist_ok=True)

    shutil.copytree(skills_src, dest_skills, dirs_exist_ok=True)
    return ValidatedAddDir(path=str(session_dir))


def scan_skill_snapshots(scenario_dir: Path) -> dict[str, Path]:
    """Scan {scenario_dir}/skill_snapshots/ for per-step snapshot dirs.

    Returns {step_name: snapshot_path} for each subdirectory.
    """
    snapshots_root = scenario_dir / SKILLS_SNAPSHOT_DIR
    if not snapshots_root.exists() or not snapshots_root.is_dir():
        return {}
    return {
        entry.name: entry
        for entry in sorted(snapshots_root.iterdir())
        if entry.is_dir()
    }
