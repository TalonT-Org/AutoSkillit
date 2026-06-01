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
    assert "NOT invoke arch-lens" in text or "Does NOT invoke any arch-lens" in text


def test_prepare_pr_classifies_new_vs_modified():
    """prepare-pr must classify files as new (★) vs modified (●)."""
    text = PREPARE_PR.read_text()
    assert "★" in text and "●" in text


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
    assert "NOT invoke any sub-skills" in text or "Does NOT invoke sub-skills" in text


def test_compose_pr_gh_degrades_gracefully():
    text = COMPOSE_PR.read_text()
    assert "gh auth status" in text
    assert "empty" in text.lower() or "pr_url =" in text


RECIPES_DIR = Path(__file__).parents[2] / "src" / "autoskillit" / "recipes"


def test_compose_pr_skill_command_includes_issue_number() -> None:
    """compose_pr skill_command must structurally include context.issue_number."""
    from autoskillit.core.io import load_yaml

    data = load_yaml(RECIPES_DIR / "implementation.yaml")
    skill_command = data["steps"]["compose_pr"]["with"]["skill_command"]
    assert "${{ context.issue_number }}" in skill_command, (
        f"compose_pr skill_command must include"
        f" ${{{{ context.issue_number }}}} as a positional arg, "
        f"not rely on with: metadata. Got: {skill_command!r}"
    )


def test_prepare_pr_skill_command_includes_issue_number() -> None:
    """prepare_pr skill_command must structurally include context.issue_number."""
    from autoskillit.core.io import load_yaml

    data = load_yaml(RECIPES_DIR / "implementation.yaml")
    skill_command = data["steps"]["prepare_pr"]["with"]["skill_command"]
    assert "${{ context.issue_number }}" in skill_command, (
        f"prepare_pr skill_command must include"
        f" ${{{{ context.issue_number }}}} as a positional arg, "
        f"not rely on with: metadata. Got: {skill_command!r}"
    )


def test_prepare_pr_plan_summary_prohibits_nested_heading():
    """Plan Summary placeholder must prohibit including the ## Summary heading.

    Checks that a single sentence contains both the prohibition verb and the
    heading reference — a proximity anchor that prevents false passes from
    unrelated occurrences scattered across the file.
    """
    import regex as re

    text = PREPARE_PR.read_text()
    pattern = re.compile(
        r"(?:NOT include|NEVER.*include).*## Summary.*heading"
        r"|heading.*## Summary.*(?:NOT include|NEVER)",
        re.IGNORECASE,
    )
    assert pattern.search(text), (
        "prepare-pr/SKILL.md must contain a proximity-anchored prohibition: "
        "'do NOT include the ## Summary heading' or 'NEVER include the ## Summary heading'"
    )
