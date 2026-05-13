"""Contract tests for plan-visualization SKILL.md — experiment type vocabulary."""

import re
from pathlib import Path

SKILL_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "src"
    / "autoskillit"
    / "skills_extended"
    / "plan-visualization"
    / "SKILL.md"
)
EXPERIMENT_TYPES_DIR = (
    Path(__file__).resolve().parent.parent.parent
    / "src"
    / "autoskillit"
    / "recipes"
    / "experiment-types"
)


def _extract_lens_table_section(text: str) -> str:
    """Extract the Tier B lens selection table section from SKILL.md."""
    m = re.search(
        r"Experiment-type table[^\n]*\n(\|[^\n]*\n)+",
        text,
        re.IGNORECASE,
    )
    assert m, "Tier B experiment-type table not found in plan-visualization SKILL.md"
    return m.group(0).lower()


def test_plan_visualization_experiment_types_use_canonical_names() -> None:
    """Tier B lens selection table must use canonical experiment type names from registry."""
    registry_types = {p.stem for p in EXPERIMENT_TYPES_DIR.glob("*.yaml")}
    assert len(registry_types) > 0, "experiment-types registry is empty"

    table_section = _extract_lens_table_section(SKILL_PATH.read_text())
    for rtype in registry_types:
        assert rtype in table_section, (
            f"plan-visualization Tier B lens selection table does not reference "
            f"canonical experiment type '{rtype}' from the registry"
        )
