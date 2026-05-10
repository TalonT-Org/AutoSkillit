"""Figure spec contracts between producer (vis-lens) and consumer (bundle-local-report)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from autoskillit.core import PRODUCER_SCHEMA_FIELDS, REQUIRED_CONSUMER_FIELDS, FigureSpec
from autoskillit.core.paths import pkg_root

_VIS_LENS_DIR = pkg_root() / "skills_extended"


def _figure_spec_blocks_in(skill_md: Path) -> list[tuple[str, str]]:
    """Return list of (block_content, fence_format) for each yaml:figure-spec block."""
    text = skill_md.read_text()
    blocks = []

    pattern_a = re.compile(
        r"```yaml:figure-spec\n(.*?)```",
        re.DOTALL,
    )
    for m in pattern_a.finditer(text):
        blocks.append((m.group(1), "yaml:figure-spec"))

    pattern_b = re.compile(
        r"```yaml\n# yaml:figure-spec[^\n]*\n(.*?)```",
        re.DOTALL,
    )
    for m in pattern_b.finditer(text):
        blocks.append((m.group(1), "yaml (with # yaml:figure-spec comment)"))

    return blocks


def _field_names_from_yaml_block(block_text: str) -> frozenset[str]:
    """Parse field names from a figure-spec YAML block (top-level scalar keys only)."""
    fields = set()
    for line in block_text.splitlines():
        line = line.strip()
        if line.startswith("#") or not line:
            continue
        if ":" in line:
            key = line.split(":")[0].strip()
            if key:
                fields.add(key)
    return frozenset(fields)


def _non_composite_vis_lens_skills() -> list[Path]:
    """Return SKILL.md paths for all non-composite vis-lens skills.

    vis-lens-always-on is excluded because it is a composite skill that emits
    yaml:spec-index (a triage summary), not yaml:figure-spec per-figure blocks.
    """
    skills = []
    composite_skills = {"vis-lens-always-on"}
    for vis_dir in _VIS_LENS_DIR.glob("vis-lens-*"):
        if vis_dir.name in composite_skills:
            continue
        skill_md = vis_dir / "SKILL.md"
        if skill_md.exists():
            skills.append(skill_md)
    return sorted(skills)


def test_figure_spec_schema_completeness() -> None:
    """FigureSpec TypedDict must include all PRODUCER_SCHEMA_FIELDS and cover REQUIRED_CONSUMER_FIELDS.

    This ensures the shared schema is self-consistent: every field a producer may emit
    is declared in the TypedDict, and every field a consumer requires is present.
    """
    figure_spec_keys = frozenset(FigureSpec.__annotations__.keys())

    missing_from_typed_dict = PRODUCER_SCHEMA_FIELDS - figure_spec_keys
    assert not missing_from_typed_dict, (
        f"PRODUCER_SCHEMA_FIELDS fields missing from FigureSpec TypedDict: {sorted(missing_from_typed_dict)}"
    )

    missing_consumer_coverage = REQUIRED_CONSUMER_FIELDS - PRODUCER_SCHEMA_FIELDS
    assert not missing_consumer_coverage, (
        f"REQUIRED_CONSUMER_FIELDS not covered by PRODUCER_SCHEMA_FIELDS: {sorted(missing_consumer_coverage)}"
    )


@pytest.mark.parametrize(
    "skill_md",
    _non_composite_vis_lens_skills(),
    ids=[p.parent.name for p in _non_composite_vis_lens_skills()],
)
def test_figure_spec_producer_includes_consumer_required_fields(skill_md: Path) -> None:
    """Each vis-lens skill's figure-spec output must include all fields bundle-local-report needs.

    Consumer (bundle-local-report) reads: image_path, report_section, figure_title, figure_id.
    Producer (vis-lens) must emit all of these in its output template.
    """
    blocks = _figure_spec_blocks_in(skill_md)
    assert blocks, (
        f"{skill_md.parent.name}/SKILL.md has no yaml:figure-spec blocks. "
        "Add output templates with figure-spec YAML blocks to validate the contract."
    )

    missing_per_block = []
    for block_text, fence_format in blocks:
        fields = _field_names_from_yaml_block(block_text)
        missing = REQUIRED_CONSUMER_FIELDS - fields
        if missing:
            missing_per_block.append(f"  [{fence_format}] missing: {sorted(missing)}")

    if missing_per_block:
        pytest.fail(
            f"{skill_md.parent.name}/SKILL.md figure-spec blocks do not include "
            f"all fields required by bundle-local-report.\n"
            f"Required by consumer: {sorted(REQUIRED_CONSUMER_FIELDS)}\n"
            + "\n".join(missing_per_block)
        )
