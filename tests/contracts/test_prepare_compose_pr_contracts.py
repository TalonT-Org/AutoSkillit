"""Contract tests for prepare-pr and compose-pr skills."""

from pathlib import Path

SKILLS_DIR = Path(__file__).parents[2] / "src/autoskillit/skills_extended"
PREPARE_PR = SKILLS_DIR / "prepare-pr/SKILL.md"
COMPOSE_PR = SKILLS_DIR / "compose-pr/SKILL.md"


def test_prepare_pr_skill_exists():
    assert PREPARE_PR.exists()


def test_compose_pr_skill_exists():
    assert COMPOSE_PR.exists()


def test_prepare_pr_outputs_prep_path():
    text = PREPARE_PR.read_text()
    assert "prep_path" in text


def test_prepare_pr_outputs_selected_lenses():
    text = PREPARE_PR.read_text()
    assert "selected_lenses" in text


def test_prepare_pr_outputs_lens_context_paths():
    text = PREPARE_PR.read_text()
    assert "lens_context_paths" in text


def test_prepare_pr_never_invokes_arch_lens():
    """prepare-pr must explicitly state it does NOT invoke arch-lens skills."""
    text = PREPARE_PR.read_text()
    assert "NOT invoke" in text or "Does NOT" in text or "NEVER" in text


def test_prepare_pr_classifies_new_vs_modified():
    """prepare-pr must classify files as new (★) vs modified (●)."""
    text = PREPARE_PR.read_text()
    assert "★" in text and "●" in text or ("new_files" in text and "modified_files" in text)


def test_compose_pr_outputs_pr_url():
    text = COMPOSE_PR.read_text()
    assert "pr_url" in text


def test_compose_pr_validates_diagrams_with_markers():
    """compose-pr must validate that diagrams contain ★ or ●."""
    text = COMPOSE_PR.read_text()
    assert "★" in text and "●" in text


def test_compose_pr_degrades_gracefully_without_diagrams():
    """compose-pr must handle empty diagram list gracefully."""
    text = COMPOSE_PR.read_text()
    assert (
        "empty" in text.lower()
        or "no diagrams" in text.lower()
        or "all_diagram_paths is empty" in text
    )


def test_compose_pr_never_invokes_sub_skills():
    text = COMPOSE_PR.read_text()
    assert "NOT invoke" in text or "Does NOT" in text or "NEVER" in text


def test_compose_pr_gh_degrades_gracefully():
    text = COMPOSE_PR.read_text()
    assert "gh auth status" in text
    assert "empty" in text.lower() or "pr_url =" in text
