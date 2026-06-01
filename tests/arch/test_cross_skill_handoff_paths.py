"""Architectural test: cross-skill artifact read paths must match producer output_dir.

Tests that consumer skills (e.g., resolve-review) read artifacts from paths
compatible with the producer skill's (e.g., review-pr) recipe output_dir.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from autoskillit.core.io import load_yaml

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]

_SRC_ROOT = Path(__file__).parent.parent.parent / "src" / "autoskillit"
_SKILLS_ROOT = _SRC_ROOT / "skills_extended"
_RECIPES_DIR = _SRC_ROOT / "recipes"

_AUTOSKILLIT_TEMP_PREFIX_RE = re.compile(
    r"\{\{AUTOSKILLIT_TEMP\}\}/review-pr/(?P<filename>[^\s`\"']+)"
)

_CONTEXT_TEMPLATE_RE = re.compile(r"\$\{\{\s*context\.[^}]+\}\}")


def _read_skill_md(skill_name: str) -> str:
    path = _SKILLS_ROOT / skill_name / "SKILL.md"
    return path.read_text(encoding="utf-8")


def _get_review_pr_output_dir_from_recipe(recipe_path: Path) -> str | None:
    """Return the output_dir used for review-pr invocation in a recipe."""
    data = load_yaml(recipe_path)
    if not isinstance(data, dict):
        return None
    steps = data.get("steps", {})
    if not isinstance(steps, dict):
        return None
    for step in steps.values():
        if not isinstance(step, dict):
            continue
        if step.get("tool") != "run_skill":
            continue
        with_block = step.get("with", {}) or {}
        skill_cmd = with_block.get("skill_command", "") or ""
        if "review-pr" not in skill_cmd:
            continue
        output_dir = with_block.get("output_dir", "") or ""
        if output_dir:
            return output_dir
    return None


def _recipe_invokes_both(recipe_path: Path, producer: str, consumer: str) -> bool:
    """Return True if the recipe calls both the producer and consumer skills."""
    data = load_yaml(recipe_path)
    if not isinstance(data, dict):
        return False
    steps = data.get("steps", {})
    if not isinstance(steps, dict):
        return False
    has_producer = False
    has_consumer = False
    for step in steps.values():
        if not isinstance(step, dict):
            continue
        if step.get("tool") != "run_skill":
            continue
        with_block = step.get("with", {}) or {}
        skill_cmd = with_block.get("skill_command", "") or ""
        if producer in skill_cmd:
            has_producer = True
        if consumer in skill_cmd:
            has_consumer = True
    return has_producer and has_consumer


def _has_dynamic_read_variable(skill_md: str) -> bool:
    """Return True if the SKILL.md uses a dynamic env variable to derive review-pr paths."""
    return (
        "${AUTOSKILLIT_ALLOWED_WRITE_PREFIX}" in skill_md
        or "${REVIEW_PR_DIR}" in skill_md
        or "${REVIEW_PR_OUTPUT}" in skill_md
    )


def test_resolve_review_reads_from_review_pr_output_dir() -> None:
    """resolve-review read paths must be compatible with review-pr recipe output_dir.

    For each recipe invoking both review-pr (producer) and resolve-review (consumer),
    verify that the consumer's read paths are compatible with the producer's output_dir.

    When review-pr uses an iteration-scoped output_dir, resolve-review must use
    dynamic path resolution (not hardcoded flat paths) so artifacts are found.
    """
    consumer_md = _read_skill_md("resolve-review")

    if _has_dynamic_read_variable(consumer_md):
        pytest.skip("resolve-review uses dynamic path resolution; no flat-path violation possible")

    flat_read_paths = _AUTOSKILLIT_TEMP_PREFIX_RE.findall(consumer_md)
    if not flat_read_paths:
        pytest.skip("no flat read paths found in resolve-review SKILL.md")

    violations: list[str] = []
    for recipe_path in sorted(_RECIPES_DIR.glob("*.yaml")):
        if not _recipe_invokes_both(recipe_path, "review-pr", "resolve-review"):
            continue
        output_dir = _get_review_pr_output_dir_from_recipe(recipe_path)
        if output_dir is None:
            continue
        if not _CONTEXT_TEMPLATE_RE.search(output_dir):
            continue
        for filename in flat_read_paths:
            violations.append(
                f"{recipe_path.name}: review-pr writes under iter_N/ scoped output_dir "
                f"'{output_dir}' but resolve-review reads from flat "
                f"'{{{{AUTOSKILLIT_TEMP}}}}/review-pr/{filename}'"
            )

    assert not violations, (
        "Cross-skill handoff path mismatch — consumer reads from flat path but "
        "producer writes under iteration-scoped path:\n" + "\n".join(f"  {v}" for v in violations)
    )
