"""Contract tests for prepare-research-pr SKILL.md — experiment type vocabulary."""

from pathlib import Path

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


def test_prepare_research_pr_experiment_types_complete() -> None:
    """Lens Selection Table must cover every experiment type in the registry."""
    registry_types = {p.stem for p in EXPERIMENT_TYPES_DIR.glob("*.yaml")}
    assert len(registry_types) > 0, "experiment-types registry is empty"

    text = SKILL_PATH.read_text()
    lower = text.lower()
    missing = [rt for rt in registry_types if rt not in lower]
    assert not missing, (
        f"prepare-research-pr Lens Selection Table missing experiment types: {missing}. "
        "Every registry type must have a lens mapping."
    )
