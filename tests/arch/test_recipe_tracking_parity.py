"""Parity tests for the git-tracked recipe enumeration used by test parametrization.

Guards the invariant that recipe-parametrized tests source their matrix from
``tests._tracked_recipes`` (git's index) rather than the live working tree, so
an untracked or gitignored stray ``.yaml`` file can never silently inflate a
parametrized test matrix locally while remaining invisible to CI.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

import autoskillit.recipe.io as recipe_io
from autoskillit.core import RecipeSource
from autoskillit.recipe import all_validated_recipe_names
from autoskillit.recipe.io import RECIPE_SCAN_DIRS, list_recipes, load_recipe
from tests._git_inventory import git_ls_files
from tests._tracked_recipes import (
    analyze_untracked_recipes,
    format_untracked_recipe_report,
    tracked_recipe_load_result,
    tracked_recipe_names,
    tracked_recipe_paths,
)

pytestmark = [pytest.mark.layer("arch"), pytest.mark.medium]

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

_GIT_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "test",
    "GIT_AUTHOR_EMAIL": "test@test.local",
    "GIT_COMMITTER_NAME": "test",
    "GIT_COMMITTER_EMAIL": "test@test.local",
}


def _minimal_recipe_yaml(name: str) -> str:
    return (
        f"name: {name}\n"
        "description: minimal recipe for tracked-recipe parity test\n"
        "steps:\n"
        "  done:\n"
        "    action: stop\n"
        "    message: Done.\n"
    )


_INVALID_RECIPE_YAML = "steps: not-a-mapping\n"


def _patch_pkg_root(monkeypatch: pytest.MonkeyPatch, builtin_root: Path) -> None:
    from tests import _tracked_recipes

    monkeypatch.setattr(_tracked_recipes, "pkg_root", lambda: builtin_root)
    monkeypatch.setattr(recipe_io, "pkg_root", lambda: builtin_root)


def test_untracked_recipe_analysis_has_no_errors_in_this_checkout() -> None:
    analysis = analyze_untracked_recipes(_PROJECT_ROOT)
    assert not analysis.errors, "\n".join(analysis.errors)


def test_report_header_skips_recipe_analysis_outside_git(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests import _tracked_recipes
    from tests import conftest as root_conftest

    monkeypatch.setattr(root_conftest, "_AMBIENT_ENV_AT_STARTUP", {})

    def unexpected_recipe_analysis(_project_root: Path) -> None:
        pytest.fail("recipe analysis must not run outside a Git checkout")

    monkeypatch.setattr(_tracked_recipes, "analyze_untracked_recipes", unexpected_recipe_analysis)

    config = cast(pytest.Config, SimpleNamespace(rootpath=tmp_path))
    assert root_conftest.pytest_report_header(config) is None


def test_report_header_surfaces_recipe_analysis_failure_inside_git(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tests import _tracked_recipes
    from tests import conftest as root_conftest

    def failed_recipe_analysis(_project_root: Path) -> None:
        raise RuntimeError("recipe inventory failed")

    monkeypatch.setattr(_tracked_recipes, "analyze_untracked_recipes", failed_recipe_analysis)

    config = cast(pytest.Config, SimpleNamespace(rootpath=_PROJECT_ROOT))
    with pytest.raises(RuntimeError, match="recipe inventory failed"):
        root_conftest.pytest_report_header(config)


def _init_git_repo(repo_dir: Path) -> None:
    subprocess.run(
        ["git", "init", "-q"], cwd=repo_dir, check=True, capture_output=True, env=_GIT_ENV
    )


def _git_commit(repo_dir: Path, *paths: Path, message: str) -> None:
    subprocess.run(
        ["git", "add", *(str(p) for p in paths)],
        cwd=repo_dir,
        check=True,
        capture_output=True,
        env=_GIT_ENV,
    )
    subprocess.run(
        ["git", "commit", "-q", "-m", message],
        cwd=repo_dir,
        check=True,
        capture_output=True,
        env=_GIT_ENV,
    )


def test_tracked_recipe_accessors_default_to_all_sources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    builtin_root = tmp_path / "fake_pkg"
    _patch_pkg_root(monkeypatch, builtin_root)
    project_recipe = tmp_path / ".autoskillit" / "recipes" / "t19-project.yaml"
    builtin_recipe = builtin_root / "recipes" / "t19-builtin.yaml"
    for path, name in (
        (project_recipe, "t19-project"),
        (builtin_recipe, "t19-builtin"),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_minimal_recipe_yaml(name), encoding="utf-8")
    _init_git_repo(tmp_path)
    _git_commit(tmp_path, project_recipe, builtin_recipe, message="add tracked recipes")

    assert git_ls_files(tmp_path, ".autoskillit/recipes") == (
        ".autoskillit/recipes/t19-project.yaml",
    )
    assert tracked_recipe_paths(tmp_path) == tuple(
        sorted((project_recipe.resolve(), builtin_recipe.resolve()))
    )
    assert tracked_recipe_names(tmp_path) == ("t19-builtin", "t19-project")


def test_tracked_recipe_builtin_accessors_exclude_nested_recipes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    builtin_root = tmp_path / "fake_pkg"
    _patch_pkg_root(monkeypatch, builtin_root)
    builtin_recipe = builtin_root / "recipes" / "t19-builtin.yaml"
    nested_recipe = builtin_root / "recipes" / "campaigns" / "t19-campaign.yaml"
    for path, name in (
        (builtin_recipe, "t19-builtin"),
        (nested_recipe, "t19-campaign"),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_minimal_recipe_yaml(name), encoding="utf-8")
    _init_git_repo(tmp_path)
    _git_commit(tmp_path, builtin_recipe, nested_recipe, message="add bundled recipes")

    assert tracked_recipe_paths(tmp_path, source=RecipeSource.BUILTIN, scan_dirs=(".",)) == (
        builtin_recipe.resolve(),
    )
    assert tracked_recipe_names(tmp_path, source=RecipeSource.BUILTIN, scan_dirs=(".",)) == (
        "t19-builtin",
    )


def test_tracked_recipe_project_accessors_include_every_scan_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_pkg_root(monkeypatch, tmp_path / "fake_pkg")
    recipes_dir = tmp_path / ".autoskillit" / "recipes"
    project_recipes: list[Path] = []
    for scan_dir in RECIPE_SCAN_DIRS:
        suffix = "root" if scan_dir == "." else scan_dir
        recipe = recipes_dir / scan_dir / f"t19-project-{suffix}.yaml"
        recipe.parent.mkdir(parents=True, exist_ok=True)
        recipe.write_text(_minimal_recipe_yaml(f"t19-project-{suffix}"), encoding="utf-8")
        project_recipes.append(recipe)
    _init_git_repo(tmp_path)
    _git_commit(tmp_path, *project_recipes, message="add project recipes")

    assert tracked_recipe_paths(tmp_path, source=RecipeSource.PROJECT) == tuple(
        sorted(recipe.resolve() for recipe in project_recipes)
    )
    assert tracked_recipe_names(tmp_path, source=RecipeSource.PROJECT) == tuple(
        sorted(
            f"t19-project-{'root' if scan_dir == '.' else scan_dir}"
            for scan_dir in RECIPE_SCAN_DIRS
        )
    )


def test_untracked_duplicate_name_cannot_evict_tracked_project_recipe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_pkg_root(monkeypatch, tmp_path / "fake_pkg")
    recipes_dir = tmp_path / ".autoskillit" / "recipes"
    recipes_dir.mkdir(parents=True)
    tracked_recipe = recipes_dir / "t19-tracked.yaml"
    tracked_recipe.write_text(_minimal_recipe_yaml("t19-shared"), encoding="utf-8")
    _init_git_repo(tmp_path)
    _git_commit(tmp_path, tracked_recipe, message="add tracked project recipe")

    (recipes_dir / "t19-a-stray.yaml").write_text(
        _minimal_recipe_yaml("t19-shared"), encoding="utf-8"
    )

    assert tracked_recipe_paths(tmp_path) == (tracked_recipe.resolve(),)
    assert "t19-shared" in tracked_recipe_names(tmp_path)


def test_untracked_project_recipe_cannot_evict_tracked_bundled_recipe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    builtin_root = tmp_path / "fake_pkg"
    _patch_pkg_root(monkeypatch, builtin_root)
    builtin_recipe = builtin_root / "recipes" / "t19-bundled.yaml"
    builtin_recipe.parent.mkdir(parents=True)
    builtin_recipe.write_text(_minimal_recipe_yaml("t19-shared"), encoding="utf-8")
    _init_git_repo(tmp_path)
    _git_commit(tmp_path, builtin_recipe, message="add tracked bundled recipe")

    project_recipe = tmp_path / ".autoskillit" / "recipes" / "t19-z-stray.yaml"
    project_recipe.parent.mkdir(parents=True)
    project_recipe.write_text(_minimal_recipe_yaml("t19-shared"), encoding="utf-8")

    assert "t19-shared" in tracked_recipe_names(tmp_path)


def test_untracked_valid_recipe_does_not_inflate_tracked_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_pkg_root(monkeypatch, tmp_path / "fake_pkg")
    recipes_dir = tmp_path / ".autoskillit" / "recipes"
    recipes_dir.mkdir(parents=True)
    tracked_recipe = recipes_dir / "t19-tracked.yaml"
    tracked_recipe.write_text(_minimal_recipe_yaml("t19-tracked"), encoding="utf-8")
    _init_git_repo(tmp_path)
    _git_commit(tmp_path, tracked_recipe, message="add tracked project recipe")

    (recipes_dir / "t19-stray.yaml").write_text(
        _minimal_recipe_yaml("t19-local-only"), encoding="utf-8"
    )

    assert "t19-local-only" not in tracked_recipe_names(tmp_path)


def test_tracked_enumeration_cache_key_covers_the_builtin_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    builtin_a = tmp_path / "fake_pkg_a"
    builtin_b = tmp_path / "fake_pkg_b"
    recipe_a = builtin_a / "recipes" / "t19-a.yaml"
    recipe_b = builtin_b / "recipes" / "t19-b.yaml"
    recipe_a.parent.mkdir(parents=True)
    recipe_b.parent.mkdir(parents=True)
    recipe_a.write_text(_minimal_recipe_yaml("t19-builtin-a"), encoding="utf-8")
    recipe_b.write_text(_minimal_recipe_yaml("t19-builtin-b"), encoding="utf-8")
    _init_git_repo(tmp_path)
    _git_commit(tmp_path, recipe_a, message="add first bundled recipe")
    _git_commit(tmp_path, recipe_b, message="add second bundled recipe")

    _patch_pkg_root(monkeypatch, builtin_a)
    first = tracked_recipe_names(tmp_path)
    _patch_pkg_root(monkeypatch, builtin_b)
    second = tracked_recipe_names(tmp_path)

    assert first == ("t19-builtin-a",)
    assert second == ("t19-builtin-b",)


def test_untracked_recipe_colliding_with_tracked_name_is_an_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_pkg_root(monkeypatch, tmp_path / "fake_pkg")
    recipes_dir = tmp_path / ".autoskillit" / "recipes"
    recipes_dir.mkdir(parents=True)
    tracked_recipe = recipes_dir / "t19-tracked.yaml"
    tracked_recipe.write_text(_minimal_recipe_yaml("t19-shared"), encoding="utf-8")
    _init_git_repo(tmp_path)
    _git_commit(tmp_path, tracked_recipe, message="add tracked project recipe")
    stray_recipe = recipes_dir / "t19-stray.yaml"
    stray_recipe.write_text(_minimal_recipe_yaml("t19-shared"), encoding="utf-8")

    analysis = analyze_untracked_recipes(tmp_path)

    assert len(analysis.errors) == 1
    assert str(stray_recipe.resolve()) in analysis.errors[0]
    assert str(tracked_recipe.resolve()) in analysis.errors[0]
    assert "git check-ignore -v" in analysis.errors[0]


def test_untracked_non_colliding_recipe_is_reported_but_not_an_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_pkg_root(monkeypatch, tmp_path / "fake_pkg")
    recipes_dir = tmp_path / ".autoskillit" / "recipes"
    recipes_dir.mkdir(parents=True)
    tracked_recipe = recipes_dir / "t19-tracked.yaml"
    tracked_recipe.write_text(_minimal_recipe_yaml("t19-tracked"), encoding="utf-8")
    _init_git_repo(tmp_path)
    _git_commit(tmp_path, tracked_recipe, message="add tracked project recipe")
    stray_recipe = recipes_dir / "t19-local-only.yaml"
    stray_recipe.write_text(_minimal_recipe_yaml("t19-local-only"), encoding="utf-8")

    analysis = analyze_untracked_recipes(tmp_path)
    report = "\n".join(format_untracked_recipe_report(analysis))

    assert not analysis.errors
    assert analysis.report_paths == (stray_recipe.resolve(),)
    assert str(stray_recipe.resolve()) in report
    assert "git check-ignore -v" in report
    assert "git add -f" in report


def test_untracked_invalid_recipe_is_an_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_pkg_root(monkeypatch, tmp_path / "fake_pkg")
    recipes_dir = tmp_path / ".autoskillit" / "recipes"
    recipes_dir.mkdir(parents=True)
    _init_git_repo(tmp_path)
    stray_recipe = recipes_dir / "t19-invalid.yaml"
    stray_recipe.write_text(_INVALID_RECIPE_YAML, encoding="utf-8")

    analysis = analyze_untracked_recipes(tmp_path)

    assert len(analysis.errors) == 1
    assert str(stray_recipe.resolve()) in analysis.errors[0]


def test_untracked_recipe_outside_scan_shape_is_ignored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_pkg_root(monkeypatch, tmp_path / "fake_pkg")
    nested_recipe = tmp_path / ".autoskillit" / "recipes" / "scripts" / "t19-deep.yaml"
    nested_recipe.parent.mkdir(parents=True)
    nested_recipe.write_text(_INVALID_RECIPE_YAML, encoding="utf-8")
    _init_git_repo(tmp_path)

    analysis = analyze_untracked_recipes(tmp_path)

    assert not analysis.errors
    assert not analysis.report_paths


def test_tracked_enumeration_reports_no_load_errors_on_this_checkout() -> None:
    assert not tracked_recipe_load_result(_PROJECT_ROOT).errors


def test_tracked_enumeration_reports_missing_indexed_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_pkg_root(monkeypatch, tmp_path / "fake_pkg")
    recipes_dir = tmp_path / ".autoskillit" / "recipes"
    recipes_dir.mkdir(parents=True)
    tracked_recipe = recipes_dir / "t19-tracked.yaml"
    tracked_recipe.write_text(_minimal_recipe_yaml("t19-tracked"), encoding="utf-8")
    _init_git_repo(tmp_path)
    _git_commit(tmp_path, tracked_recipe, message="add tracked project recipe")
    tracked_recipe.unlink()

    result = tracked_recipe_load_result(tmp_path)

    assert not result.items
    assert [report.path for report in result.errors] == [tracked_recipe.resolve()]


def test_tracked_enumeration_observes_a_mid_session_index_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_pkg_root(monkeypatch, tmp_path / "fake_pkg")
    recipes_dir = tmp_path / ".autoskillit" / "recipes"
    recipes_dir.mkdir(parents=True)
    first_recipe = recipes_dir / "t19-first.yaml"
    first_recipe.write_text(_minimal_recipe_yaml("t19-first"), encoding="utf-8")
    _init_git_repo(tmp_path)
    _git_commit(tmp_path, first_recipe, message="add first tracked recipe")

    first = tracked_recipe_names(tmp_path)
    second_recipe = recipes_dir / "t19-second.yaml"
    second_recipe.write_text(_minimal_recipe_yaml("t19-second"), encoding="utf-8")
    _git_commit(tmp_path, second_recipe, message="add second tracked recipe")

    assert first == ("t19-first",)
    assert tracked_recipe_names(tmp_path) == ("t19-first", "t19-second")


def test_tracked_paths_are_identical_objects_to_loader_paths() -> None:
    tracked_paths = set(tracked_recipe_paths(_PROJECT_ROOT))
    loader_paths = {recipe.path for recipe in list_recipes(_PROJECT_ROOT).items}
    assert tracked_paths <= loader_paths


def test_tracked_set_is_a_loader_subset_with_reported_local_only_recipes() -> None:
    tracked = set(tracked_recipe_names(_PROJECT_ROOT))
    loader = set(all_validated_recipe_names(_PROJECT_ROOT))
    analysis = analyze_untracked_recipes(_PROJECT_ROOT)
    report_names = {load_recipe(path).name for path in analysis.report_paths}

    assert tracked <= loader
    assert loader - tracked <= report_names
