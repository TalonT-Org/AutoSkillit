"""Parsed-behavior inventory for join-required declarations.

The plan (§Step 6) requires a failing inventory test that uses parsed
behavior — not prose markers — to assert every child-spawning /
exploration consumer that joins results declares
``semantic_requirements.join.required: true``.

The test enumerates all bundled skill SKILL.md files plus exploration
sidecars, parses the YAML frontmatter, and walks the semantic_requirements
block. Any skill whose ``child_spawns`` block is non-empty, or whose body
matches a structural child-spawning marker (Agent(, Task(, spawn_agent,
Dispatch), must declare ``join.required: true``.

This guards against the regression path that left 45 skills enforcing
join only via prose.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from autoskillit.core.io import load_yaml

SKILLS_DIR = Path(__file__).parents[2] / "src/autoskillit/skills_extended"

CHILD_SPAWN_BODY_MARKERS: tuple[str, ...] = (
    "Agent(",
    "Task(",
    "spawn_agent",
    "Dispatch",
    "child delegations",
    "delegate",
)

_FRONT_RE = re.compile(r"^---\s*$")


pytestmark = [pytest.mark.small]


def _frontmatter(text: str) -> dict:
    """Parse YAML frontmatter between the first pair of ``---`` delimiters."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    end = next(
        (i for i, ln in enumerate(lines[1:], 1) if ln.strip() == "---"),
        None,
    )
    if end is None:
        return {}
    return load_yaml("\n".join(lines[1:end]))


def _has_child_spawn_markers(body: str) -> bool:
    """True when the body uses child-dispatch markers (not prose phrases)."""
    return any(marker in body for marker in CHILD_SPAWN_BODY_MARKERS)


def _iter_skill_files() -> list[Path]:
    return sorted(SKILLS_DIR.rglob("SKILL.md"))


@pytest.mark.layer("skills")
@pytest.mark.small
def test_child_spawning_skills_declare_join_required() -> None:
    """Every skill that spawns children must declare ``join.required: true``."""
    offenders: list[str] = []
    for path in _iter_skill_files():
        text = path.read_text(encoding="utf-8")
        fm = _frontmatter(text)
        sem = fm.get("semantic_requirements", {})
        if not isinstance(sem, dict):
            sem = {}
        child_spawns = sem.get("child_spawns") or []
        join = sem.get("join") or {}
        if isinstance(join, dict):
            join_required = bool(join.get("required", False))
        else:
            join_required = False
        if child_spawns:
            if not join_required:
                offenders.append(
                    f"{path.relative_to(SKILLS_DIR)} declares child_spawns without join.required"
                )
            continue
        body_after_fm = text.split("---", 2)[-1] if _FRONT_RE.match(text) else text
        if _has_child_spawn_markers(body_after_fm) and not join_required:
            offenders.append(
                f"{path.relative_to(SKILLS_DIR)} uses child-dispatch markers without join.required"
            )
    assert not offenders, (
        "Skills must declare semantic_requirements.join.required: true "
        "when they spawn children:\n  - " + "\n  - ".join(offenders)
    )


@pytest.mark.layer("skills")
@pytest.mark.small
def test_zero_prose_only_join_consumers() -> None:
    """No skill declares join in prose without the structured authority."""
    offenders: list[str] = []
    for path in _iter_skill_files():
        text = path.read_text(encoding="utf-8")
        fm = _frontmatter(text)
        sem = fm.get("semantic_requirements", {})
        if not isinstance(sem, dict):
            sem = {}
        join = sem.get("join") or {}
        join_required = bool(join.get("required", False)) if isinstance(join, dict) else False
        body = text.split("---", 2)[-1] if _FRONT_RE.match(text) else text
        has_prose_join = (
            "joining every child is required" in body or "join every spawned child" in body.lower()
        )
        if has_prose_join and not join_required:
            offenders.append(path.relative_to(SKILLS_DIR).as_posix())
    assert not offenders, (
        "Prose-only join consumers (must declare join.required: true): " + ", ".join(offenders)
    )
