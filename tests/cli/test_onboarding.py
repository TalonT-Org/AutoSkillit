"""Tests for first-run detection and guided onboarding menu."""

from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit.cli._onboarding import (
    OnboardingIntel,
    _detect_build_tools,
    gather_intel,
    is_first_run,
    mark_onboarded,
    run_onboarding_menu,
)

pytestmark = [pytest.mark.layer("cli"), pytest.mark.small]


def _make_initialized_project(base: Path) -> Path:
    """Create a minimal initialized project dir (config.yaml present)."""
    config_dir = base / ".autoskillit"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "config.yaml").write_text("test_check:\n  command: [task, test-check]\n")
    return base


# ON-1
def test_is_first_run_false_when_no_config(tmp_path: Path) -> None:
    """is_first_run() returns False when .autoskillit/config.yaml does not exist."""
    assert is_first_run(tmp_path) is False


# ON-2
def test_is_first_run_false_when_onboarded_marker_exists(tmp_path: Path) -> None:
    """Marker file .autoskillit/.onboarded present → False."""
    _make_initialized_project(tmp_path)
    (tmp_path / ".autoskillit" / ".onboarded").write_text("")
    assert is_first_run(tmp_path) is False


# ON-3
def test_is_first_run_false_when_recipes_dir_non_empty(tmp_path: Path) -> None:
    """.autoskillit/recipes/ contains a .yaml file → False."""
    _make_initialized_project(tmp_path)
    recipes_dir = tmp_path / ".autoskillit" / "recipes"
    recipes_dir.mkdir()
    (recipes_dir / "my-recipe.yaml").write_text("name: test\n")
    assert is_first_run(tmp_path) is False


# ON-4
def test_is_first_run_false_when_skill_overrides_exist(tmp_path: Path) -> None:
    """.claude/skills/investigate/SKILL.md exists → False."""
    _make_initialized_project(tmp_path)
    skill_dir = tmp_path / ".claude" / "skills" / "investigate"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("# Investigate\n")
    assert is_first_run(tmp_path) is False


# ON-5
def test_is_first_run_true_when_fresh_project(tmp_path: Path) -> None:
    """config.yaml exists, no marker, no recipes, no overrides → True."""
    _make_initialized_project(tmp_path)
    assert is_first_run(tmp_path) is True


# ON-6
def test_is_first_run_true_empty_recipes_dir(tmp_path: Path) -> None:
    """.autoskillit/recipes/ exists but is empty → still True."""
    _make_initialized_project(tmp_path)
    (tmp_path / ".autoskillit" / "recipes").mkdir()
    assert is_first_run(tmp_path) is True


# ON-7
def test_mark_onboarded_creates_marker_file(tmp_path: Path) -> None:
    """mark_onboarded(project_dir) writes .autoskillit/.onboarded. Idempotent."""
    _make_initialized_project(tmp_path)
    marker = tmp_path / ".autoskillit" / ".onboarded"
    assert not marker.exists()
    mark_onboarded(tmp_path)
    assert marker.exists()
    # second call must not raise
    mark_onboarded(tmp_path)
    assert marker.exists()


# ON-8
def test_run_onboarding_menu_decline_returns_none_and_marks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """User inputs 'n' to the initial prompt. Returns None and marker is created."""
    _make_initialized_project(tmp_path)
    inputs = iter(["n"])
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(inputs))
    result = run_onboarding_menu(tmp_path, color=False)
    assert result is None
    assert (tmp_path / ".autoskillit" / ".onboarded").exists()


# ON-9
def test_run_onboarding_menu_skip_e_returns_none_and_marks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """User inputs 'y', then 'E'. Returns None and marker is created."""
    _make_initialized_project(tmp_path)
    inputs = iter(["y", "E"])
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(inputs))
    monkeypatch.setattr("autoskillit.cli._onboarding.gather_intel", lambda _p: OnboardingIntel())
    result = run_onboarding_menu(tmp_path, color=False)
    assert result is None
    assert (tmp_path / ".autoskillit" / ".onboarded").exists()


# ON-10
def test_run_onboarding_menu_option_a_returns_setup_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """User inputs 'y', then 'A'. Returns /autoskillit:setup-project. Marker NOT created yet."""
    _make_initialized_project(tmp_path)
    inputs = iter(["y", "A"])
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(inputs))
    monkeypatch.setattr("autoskillit.cli._onboarding.gather_intel", lambda _p: OnboardingIntel())
    result = run_onboarding_menu(tmp_path, color=False)
    assert result is not None
    assert "/autoskillit:setup-project" in result
    assert not (tmp_path / ".autoskillit" / ".onboarded").exists()


# ON-11
def test_run_onboarding_menu_option_b_with_url_returns_prepare_issue(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """User inputs 'y', then 'B', then a URL. Returns string with /autoskillit:prepare-issue."""
    _make_initialized_project(tmp_path)
    inputs = iter(["y", "B", "https://github.com/org/repo/issues/42"])
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(inputs))
    monkeypatch.setattr("autoskillit.cli._onboarding.gather_intel", lambda _p: OnboardingIntel())
    result = run_onboarding_menu(tmp_path, color=False)
    assert result is not None
    assert "/autoskillit:prepare-issue" in result


# ON-12
def test_run_onboarding_menu_option_d_returns_write_recipe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """User inputs 'y', then 'D'. Returns string with /autoskillit:write-recipe."""
    _make_initialized_project(tmp_path)
    inputs = iter(["y", "D"])
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(inputs))
    monkeypatch.setattr("autoskillit.cli._onboarding.gather_intel", lambda _p: OnboardingIntel())
    result = run_onboarding_menu(tmp_path, color=False)
    assert result is not None
    assert "/autoskillit:write-recipe" in result


# ON-13
def test_run_onboarding_menu_option_c_returns_setup_project_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """User inputs 'y', then 'C'. Returns string starting with /autoskillit:setup-project."""
    _make_initialized_project(tmp_path)
    inputs = iter(["y", "C"])
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(inputs))
    monkeypatch.setattr("autoskillit.cli._onboarding.gather_intel", lambda _p: OnboardingIntel())
    result = run_onboarding_menu(tmp_path, color=False)
    assert result is not None
    assert result.startswith("/autoskillit:setup-project")


# ON-14
def test_gather_intel_returns_onboarding_intel_dataclass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """gather_intel(project_dir) returns OnboardingIntel with correct fields."""
    monkeypatch.setattr(
        "autoskillit.cli._onboarding._fetch_good_first_issues",
        lambda _p: ["#1: some issue"],
    )
    intel = gather_intel(tmp_path)
    assert isinstance(intel, OnboardingIntel)
    assert isinstance(intel.scanner_found, (str, type(None)))
    assert isinstance(intel.build_tools, list)
    assert intel.github_issues == ["#1: some issue"]


# ON-15
def test_detect_build_tools_finds_taskfile(tmp_path: Path) -> None:
    """Project dir has Taskfile.yml → 'Taskfile' in build_tools."""
    (tmp_path / "Taskfile.yml").write_text("version: '3'\n")
    tools = _detect_build_tools(tmp_path)
    assert "Taskfile" in tools


# ON-16
def test_detect_build_tools_finds_makefile(tmp_path: Path) -> None:
    """Project dir has Makefile → 'Makefile' in build_tools."""
    (tmp_path / "Makefile").write_text("all:\n\techo hello\n")
    tools = _detect_build_tools(tmp_path)
    assert "Makefile" in tools


# ON-17
def test_onboarding_intel_importable_from_cli_onboarding() -> None:
    """OnboardingIntel is importable from autoskillit.cli._onboarding."""
    from autoskillit.cli._onboarding import OnboardingIntel  # noqa: F401
