"""Contract tests for prepare-research-pr SKILL.md — experiment type vocabulary."""

import re
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("contracts"), pytest.mark.medium]

SKILL_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "src"
    / "autoskillit"
    / "skills_extended"
    / "prepare-research-pr"
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
    """Extract the Lens Selection Table section from SKILL.md."""
    m = re.search(
        r"## Lens Selection Table\s*\n(\|[^\n]*\n)+",
        text,
    )
    assert m, "Lens Selection Table not found in prepare-research-pr SKILL.md"
    return m.group(0).lower()


def test_prepare_research_pr_experiment_types_complete() -> None:
    """Lens Selection Table must cover every experiment type in the registry."""
    registry_types = {p.stem for p in EXPERIMENT_TYPES_DIR.glob("*.yaml")}
    assert len(registry_types) > 0, "experiment-types registry is empty"

    table_section = _extract_lens_table_section(SKILL_PATH.read_text())
    missing = [rt for rt in registry_types if rt not in table_section]
    assert not missing, (
        f"prepare-research-pr Lens Selection Table missing experiment types: {missing}. "
        "Every registry type must have a lens mapping."
    )
