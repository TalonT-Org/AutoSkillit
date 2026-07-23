"""Snapshot and restore ephemeral skill directories for record/replay sessions."""

from __future__ import annotations

import hashlib
import shutil
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import regex as re

from autoskillit.core import ValidatedAddDir, load_yaml, write_versioned_json

SKILLS_SNAPSHOT_DIR = "skill-snapshots"
_EPHEMERAL_SESSION_PATTERN = "autoskillit-sessions"
_GATED_PATTERN = re.compile(r"disable-model-invocation\s*:\s*true", re.IGNORECASE)
_FRONTMATTER_PATTERN = re.compile(r"\A---\r?\n(.*?)\r?\n---(?:\r?\n|\Z)", re.DOTALL)
_MACHINE_ONLY_KEYS = frozenset({"uses_capabilities", "execution_role", "backend_requirements"})


def _assert_agent_safe_skill_tree(skills_dir: Path) -> None:
    """Reject snapshots that could restore machine-only authority to an agent."""
    for entry in skills_dir.rglob("*"):
        if entry.is_symlink():
            raise ValueError(f"agent-safe skill snapshots must not contain symlinks: {entry}")
    for skill_dir in skills_dir.iterdir():
        if not skill_dir.is_dir():
            raise ValueError(
                f"agent-safe skill snapshots must contain only skill directories: {skill_dir}"
            )
        children = {child.name for child in skill_dir.iterdir()}
        if children != {"SKILL.md"} or not (skill_dir / "SKILL.md").is_file():
            raise ValueError(f"agent-safe skill directory must contain only SKILL.md: {skill_dir}")
    for skill_md in sorted(skills_dir.rglob("SKILL.md")):
        try:
            content = skill_md.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise ValueError(f"agent-safe SKILL.md is unreadable: {skill_md}") from exc
        match = _FRONTMATTER_PATTERN.match(content)
        if match is None:
            if content.startswith("---"):
                raise ValueError(f"agent-safe SKILL.md has malformed frontmatter: {skill_md}")
            continue
        try:
            frontmatter = load_yaml(match.group(1))
        except Exception as exc:
            raise ValueError(f"agent-safe SKILL.md has invalid YAML: {skill_md}") from exc
        if frontmatter is None:
            frontmatter = {}
        if not isinstance(frontmatter, Mapping):
            raise ValueError(f"agent-safe SKILL.md frontmatter must be a mapping: {skill_md}")
        leaked = sorted(_MACHINE_ONLY_KEYS & frontmatter.keys())
        if leaked:
            raise ValueError(
                f"agent-safe SKILL.md contains machine-only fields {leaked!r}: {skill_md}"
            )


def validate_skill_snapshot_members(
    snapshot_path: Path,
    expected_names: frozenset[str],
) -> None:
    """Validate a replay snapshot against its fresh resolved invocation."""
    skills_dir = snapshot_path / ".claude" / "skills"
    if not skills_dir.is_dir():
        raise ValueError("skill snapshot has no projected skills directory")
    _assert_agent_safe_skill_tree(skills_dir)
    actual_names = frozenset(entry.name for entry in skills_dir.iterdir())
    if actual_names != expected_names:
        raise ValueError(
            "skill snapshot inventory does not match the effective invocation: "
            f"missing={sorted(expected_names - actual_names)!r}, "
            f"unexpected={sorted(actual_names - expected_names)!r}"
        )


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
    if not skills_dir.is_dir():
        return {
            "schema_version": 1,
            "captured_at": datetime.now(tz=UTC).isoformat(),
            "skill_count": 0,
            "skills": {},
        }
    _assert_agent_safe_skill_tree(skills_dir)
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
        "captured_at": datetime.now(tz=UTC).isoformat(),
        "skill_count": len(skills),
        "skills": skills,
    }


def snapshot_skill_dir(scenario_dir: Path, step_name: str, add_dir_path: Path) -> Path | None:
    """Copy the ephemeral skill dir tree into the scenario dir.

    Copies {add_dir_path}/.claude/skills/ →
           {scenario_dir}/skill-snapshots/{step_name}/.claude/skills/
    Writes manifest.json alongside the .claude/ dir.
    Returns the snapshot dir path, or None if no skills to snapshot.
    """
    if not step_name or "/" in step_name or ".." in step_name:
        raise ValueError(f"Invalid step_name for path construction: {step_name!r}")
    skills_src = add_dir_path / ".claude" / "skills"
    if not skills_src.exists() or not skills_src.is_dir():
        return None

    skill_subdirs = [d for d in skills_src.iterdir() if d.is_dir()]
    if not skill_subdirs:
        return None
    _assert_agent_safe_skill_tree(skills_src)

    snapshot_dir = scenario_dir / SKILLS_SNAPSHOT_DIR / step_name
    snapshot_dir.mkdir(parents=True, exist_ok=True)

    dest_skills = snapshot_dir / ".claude" / "skills"
    if dest_skills.exists():
        shutil.rmtree(dest_skills)
    try:
        shutil.copytree(skills_src, dest_skills)
        _assert_agent_safe_skill_tree(dest_skills)
    except Exception:
        shutil.rmtree(snapshot_dir, ignore_errors=True)
        raise

    manifest = build_skills_manifest(dest_skills)
    write_versioned_json(snapshot_dir / "manifest.json", manifest, 1)

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
    _assert_agent_safe_skill_tree(skills_src)

    session_dir = ephemeral_root / session_id
    dest_skills = session_dir / ".claude" / "skills"
    shutil.rmtree(dest_skills, ignore_errors=True)
    dest_skills.parent.mkdir(parents=True, exist_ok=True)

    try:
        shutil.copytree(skills_src, dest_skills)
        _assert_agent_safe_skill_tree(dest_skills)
    except Exception:
        shutil.rmtree(dest_skills, ignore_errors=True)
        raise
    return ValidatedAddDir(path=str(session_dir))


def scan_skill_snapshots(scenario_dir: Path) -> dict[str, Path]:
    """Scan {scenario_dir}/skill-snapshots/ for per-step snapshot dirs.

    Returns {step_name: snapshot_path} for each subdirectory.
    """
    snapshots_root = scenario_dir / SKILLS_SNAPSHOT_DIR
    if not snapshots_root.exists() or not snapshots_root.is_dir():
        return {}
    return {entry.name: entry for entry in sorted(snapshots_root.iterdir()) if entry.is_dir()}
