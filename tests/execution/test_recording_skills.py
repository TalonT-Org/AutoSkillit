"""Tests for _recording_skills snapshot/restore helpers."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from autoskillit.execution._recording_skills import (
    _extract_ephemeral_add_dir,
    build_skills_manifest,
    restore_skill_snapshot,
    scan_skill_snapshots,
    snapshot_skill_dir,
)

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]


def _make_skill(skills_dir: Path, name: str, content: str = "# SKILL\n") -> Path:
    skill_dir = skills_dir / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(content, encoding="utf-8")
    return skill_dir


def _make_ephemeral_dir(tmp_path: Path, *skill_names: str) -> Path:
    add_dir = tmp_path / "autoskillit-sessions" / "headless-abc123"
    skills_dir = add_dir / ".claude" / "skills"
    skills_dir.mkdir(parents=True, exist_ok=True)
    for name in skill_names:
        _make_skill(skills_dir, name)
    return add_dir


# --- T-SNAP-1: snapshot copies full skill tree ---


def test_snapshot_skill_dir_copies_tree(tmp_path: Path) -> None:
    add_dir = _make_ephemeral_dir(tmp_path, "investigate", "implement")
    scenario_dir = tmp_path / "scenario"
    scenario_dir.mkdir()

    result = snapshot_skill_dir(scenario_dir, "investigate", add_dir)

    assert result is not None
    assert (result / ".claude" / "skills" / "investigate" / "SKILL.md").exists()
    assert (result / ".claude" / "skills" / "implement" / "SKILL.md").exists()


# --- T-SNAP-2: snapshot writes manifest.json ---


def test_snapshot_skill_dir_writes_manifest(tmp_path: Path) -> None:
    add_dir = _make_ephemeral_dir(tmp_path, "investigate")
    scenario_dir = tmp_path / "scenario"
    scenario_dir.mkdir()

    result = snapshot_skill_dir(scenario_dir, "investigate", add_dir)

    assert result is not None
    manifest_path = result / "manifest.json"
    assert manifest_path.exists()
    manifest = json.loads(manifest_path.read_text())
    assert manifest["schema_version"] == 1
    assert "captured_at" in manifest
    assert manifest["skill_count"] == 1
    assert "investigate" in manifest["skills"]
    entry = manifest["skills"]["investigate"]
    assert "content_sha256" in entry
    assert "size_bytes" in entry


# --- T-SNAP-3: empty skills dir returns None ---


def test_snapshot_skill_dir_empty_skills_returns_none(tmp_path: Path) -> None:
    add_dir = tmp_path / "autoskillit-sessions" / "headless-abc"
    (add_dir / ".claude" / "skills").mkdir(parents=True)
    scenario_dir = tmp_path / "scenario"
    scenario_dir.mkdir()

    result = snapshot_skill_dir(scenario_dir, "step", add_dir)

    assert result is None


# --- T-SNAP-4: missing .claude/skills returns None ---


def test_snapshot_skill_dir_no_skills_subdir_returns_none(tmp_path: Path) -> None:
    add_dir = tmp_path / "autoskillit-sessions" / "headless-abc"
    add_dir.mkdir(parents=True)
    scenario_dir = tmp_path / "scenario"
    scenario_dir.mkdir()

    result = snapshot_skill_dir(scenario_dir, "step", add_dir)

    assert result is None


# --- T-REST-1: restore populates target dir and returns ValidatedAddDir ---


def test_restore_skill_snapshot_populates_target(tmp_path: Path) -> None:
    snapshot_dir = tmp_path / "snapshot"
    skills_src = snapshot_dir / ".claude" / "skills" / "investigate"
    skills_src.mkdir(parents=True)
    (skills_src / "SKILL.md").write_text("# investigate\n", encoding="utf-8")

    ephemeral_root = tmp_path / "sessions"
    result = restore_skill_snapshot(snapshot_dir, ephemeral_root, "headless-test123")

    assert result is not None
    session_dir = ephemeral_root / "headless-test123"
    assert (session_dir / ".claude" / "skills" / "investigate" / "SKILL.md").exists()
    assert result.path == str(session_dir)


# --- T-REST-2: snapshot path with no .claude/skills returns None ---


def test_restore_skill_snapshot_no_snapshot_returns_none(tmp_path: Path) -> None:
    snapshot_dir = tmp_path / "empty-snapshot"
    snapshot_dir.mkdir()
    ephemeral_root = tmp_path / "sessions"

    result = restore_skill_snapshot(snapshot_dir, ephemeral_root, "headless-missing")

    assert result is None


# --- T-REST-3: restore preserves gated and ungated skill content byte-for-byte ---


def test_restore_preserves_gated_state(tmp_path: Path) -> None:
    snapshot_dir = tmp_path / "snapshot"
    skills_src = snapshot_dir / ".claude" / "skills"
    skills_src.mkdir(parents=True)

    ungated_dir = skills_src / "open-skill"
    ungated_dir.mkdir()
    ungated_content = "# open skill\nNormal skill."
    (ungated_dir / "SKILL.md").write_text(ungated_content, encoding="utf-8")

    gated_dir = skills_src / "gated-skill"
    gated_dir.mkdir()
    gated_content = "# gated skill\ndisable-model-invocation: true\n"
    (gated_dir / "SKILL.md").write_text(gated_content, encoding="utf-8")

    ephemeral_root = tmp_path / "sessions"
    result = restore_skill_snapshot(snapshot_dir, ephemeral_root, "headless-abc")

    assert result is not None
    session_dir = Path(result.path)
    restored_open = (session_dir / ".claude" / "skills" / "open-skill" / "SKILL.md").read_text()
    restored_gated = (session_dir / ".claude" / "skills" / "gated-skill" / "SKILL.md").read_text()
    assert restored_open == ungated_content
    assert restored_gated == gated_content


# --- T-EXTRACT-1: extracts ephemeral --add-dir path ---


def test_extract_ephemeral_add_dir_finds_shm_path() -> None:
    cmd = ["claude", "--add-dir", "/dev/shm/autoskillit-sessions/headless-abc123", "--print", "go"]
    result = _extract_ephemeral_add_dir(cmd)
    assert result == Path("/dev/shm/autoskillit-sessions/headless-abc123")


# --- T-EXTRACT-2: skips non-ephemeral --add-dir ---


def test_extract_ephemeral_add_dir_skips_non_ephemeral() -> None:
    cmd = ["claude", "--add-dir", "/home/user/project", "--print", "go"]
    result = _extract_ephemeral_add_dir(cmd)
    assert result is None


# --- T-EXTRACT-3: multiple --add-dir, returns only ephemeral ---


def test_extract_ephemeral_add_dir_multiple_dirs() -> None:
    cmd = [
        "claude",
        "--add-dir",
        "/home/user/project",
        "--add-dir",
        "/tmp/autoskillit-sessions/headless-xyz",
        "--print",
        "go",
    ]
    result = _extract_ephemeral_add_dir(cmd)
    assert result == Path("/tmp/autoskillit-sessions/headless-xyz")


# --- T-MANIFEST-1: build_skills_manifest structure ---


def test_build_skills_manifest_structure(tmp_path: Path) -> None:
    skills_dir = tmp_path / "skills"
    _make_skill(skills_dir, "investigate")
    _make_skill(skills_dir, "implement")

    manifest = build_skills_manifest(skills_dir)

    assert manifest["schema_version"] == 1
    assert manifest["skill_count"] == 2
    assert "investigate" in manifest["skills"]
    assert "implement" in manifest["skills"]
    assert "content_sha256" in manifest["skills"]["investigate"]
    assert "size_bytes" in manifest["skills"]["investigate"]


# --- T-MANIFEST-2: manifest detects gated skills ---


def test_manifest_detects_gated_skills(tmp_path: Path) -> None:
    skills_dir = tmp_path / "skills"
    _make_skill(skills_dir, "normal", "# normal skill")
    _make_skill(skills_dir, "gated", "# gated\ndisable-model-invocation: true\n")

    manifest = build_skills_manifest(skills_dir)

    assert manifest["skills"]["normal"]["gated"] is False
    assert manifest["skills"]["gated"]["gated"] is True


# --- T-SCAN-1: scan finds step subdirectories ---


def test_scan_skill_snapshots_finds_steps(tmp_path: Path) -> None:
    scenario_dir = tmp_path / "scenario"
    (scenario_dir / "skill_snapshots" / "step_a").mkdir(parents=True)
    (scenario_dir / "skill_snapshots" / "step_b").mkdir(parents=True)

    result = scan_skill_snapshots(scenario_dir)

    assert "step_a" in result
    assert "step_b" in result
    assert result["step_a"] == scenario_dir / "skill_snapshots" / "step_a"


# --- T-SCAN-2: scan returns empty dict when no skill_snapshots dir ---


def test_scan_skill_snapshots_empty_returns_empty_dict(tmp_path: Path) -> None:
    scenario_dir = tmp_path / "scenario"
    scenario_dir.mkdir()

    result = scan_skill_snapshots(scenario_dir)

    assert result == {}
