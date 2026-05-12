"""Contract tests for plan-visualization SKILL.md — experiment type vocabulary."""

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


def test_plan_visualization_experiment_types_use_canonical_names() -> None:
    """Tier B lens selection table must use canonical experiment type names from registry."""
    registry_types = {p.stem for p in EXPERIMENT_TYPES_DIR.glob("*.yaml")}
    assert len(registry_types) > 0, "experiment-types registry is empty"

    text = SKILL_PATH.read_text()
    lower = text.lower()
    for rtype in registry_types:
        assert rtype in lower, (
            f"plan-visualization SKILL.md does not reference canonical "
            f"experiment type '{rtype}' from the registry"
        )
