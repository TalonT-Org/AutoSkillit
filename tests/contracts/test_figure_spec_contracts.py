"""Figure spec contracts between producer (vis-lens) and consumer (bundle-local-report)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from autoskillit.core.paths import pkg_root

# Fields that bundle-local-report reads from yaml:figure-spec blocks.
# If a producer omits any of these, image insertion silently fails.
REQUIRED_CONSUMER_FIELDS: frozenset[str] = frozenset(
    {
        "image_path",
        "report_section",
        "figure_title",
        "figure_id",
    }
)

_VIS_LENS_DIR = pkg_root() / "skills_extended"


def _figure_spec_blocks_in(skill_md: Path) -> list[tuple[str, str]]:
    """Return list of (block_content, fence_format) for each yaml:figure-spec block."""
    text = skill_md.read_text()
    blocks = []

    # Pattern A: ```yaml:figure-spec fence (fence tag includes "figure-spec")
    pattern_a = re.compile(
        r"```yaml:figure-spec\n(.*?)```",
        re.DOTALL,
    )
    for m in pattern_a.finditer(text):
        blocks.append((m.group(1), "yaml:figure-spec"))

    # Pattern B: ```yaml fence with "# yaml:figure-spec" (possibly with trailing text) inside
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
    # Strip comment lines and find top-level key: value lines
    for line in block_text.splitlines():
        line = line.strip()
        if line.startswith("#") or not line:
            continue
        if ":" in line:
            key = line.split(":")[0].strip()
            if key:
                fields.add(key)
    return frozenset(fields)


@pytest.mark.parametrize(
    "skill_md",
    [_VIS_LENS_DIR / "vis-lens-chart-select" / "SKILL.md"],
    ids=["vis-lens-chart-select"],
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
