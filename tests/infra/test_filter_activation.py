"""Infrastructure tests: verify test path filtering is activated in project config."""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent.parent


def test_project_config_has_filter_mode_conservative():
    """AC1: .autoskillit/config.yaml must set filter_mode to conservative."""
    from autoskillit.core.io import load_yaml

    cfg = load_yaml(REPO_ROOT / ".autoskillit/config.yaml")
    assert cfg["test_check"]["filter_mode"] == "conservative"


def test_project_config_has_base_ref():
    """AC1: .autoskillit/config.yaml must set base_ref to integration."""
    from autoskillit.core.io import load_yaml

    cfg = load_yaml(REPO_ROOT / ".autoskillit/config.yaml")
    assert cfg["test_check"]["base_ref"] == "integration"


def test_hook_registry_tests_in_infra():
    """AC4: test_hook_registry.py must live in tests/infra/."""
    assert (REPO_ROOT / "tests/infra/test_hook_registry.py").is_file()
    assert not (REPO_ROOT / "tests/test_hook_registry.py").is_file()


def test_phase2_skills_in_skills():
    """AC4: test_phase2_skills.py must live in tests/skills/."""
    assert (REPO_ROOT / "tests/skills/test_phase2_skills.py").is_file()
    assert not (REPO_ROOT / "tests/test_phase2_skills.py").is_file()


def test_skill_preambles_in_skills():
    """AC4: test_skill_preambles.py must live in tests/skills/."""
    assert (REPO_ROOT / "tests/skills/test_skill_preambles.py").is_file()
    assert not (REPO_ROOT / "tests/test_skill_preambles.py").is_file()
