"""Tests for recipe I/O — list_recipes discovery, builtin_recipes_dir, and pack fields."""

from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit.core.types import RecipeSource
from autoskillit.recipe.io import (
    builtin_recipes_dir,
    list_recipes,
    load_recipe,
)
from autoskillit.recipe.schema import RecipeKind

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.medium]


class TestListRecipes:
    """TestListRecipes: discovery from project and builtin sources."""

    def test_finds_builtins(self, tmp_path: Path) -> None:
        result = list_recipes(tmp_path)
        recipes = result.items
        names = {w.name for w in recipes}
        assert "implementation" in names
        assert len(recipes) > 0
        assert all(r.source.value in ("project", "builtin") for r in recipes)

    def test_list_recipes_bundled_appear_before_project(self, tmp_path: Path) -> None:
        """Non-experimental BUILTIN recipes must appear before PROJECT recipes."""
        recipes_dir = tmp_path / ".autoskillit" / "recipes"
        recipes_dir.mkdir(parents=True)
        (recipes_dir / "aardvark.yaml").write_text(
            "name: aardvark\ndescription: test\nsteps: {}\n"
        )
        result = list_recipes(tmp_path)
        non_exp = [r for r in result.items if not r.experimental]
        seen_project = False
        for r in non_exp:
            if r.source == RecipeSource.PROJECT:
                seen_project = True
            elif r.source == RecipeSource.BUILTIN:
                assert not seen_project, (
                    "A BUILTIN recipe appeared after a PROJECT recipe — ordering is broken"
                )

    def test_list_recipes_alphabetical_within_bundled_tiers(self, tmp_path: Path) -> None:
        """Unregistered core bundled recipes are alphabetical after registered ones.
        Add-on bundled recipes remain alphabetical.
        """
        from autoskillit.core._type_constants import CORE_PACKS
        from autoskillit.recipe.order import BUNDLED_RECIPE_ORDER

        result = list_recipes(tmp_path)
        core_names = [
            r.name
            for r in result.items
            if r.source == RecipeSource.BUILTIN
            and not r.experimental
            and all(p in CORE_PACKS for p in r.requires_packs)
        ]
        addon_names = [
            r.name
            for r in result.items
            if r.source == RecipeSource.BUILTIN
            and not r.experimental
            and not all(p in CORE_PACKS for p in r.requires_packs)
        ]
        # Registered entries appear first; the unregistered tail must be alphabetical
        unregistered_core = [n for n in core_names if n not in BUNDLED_RECIPE_ORDER]
        registered_core = [n for n in core_names if n in BUNDLED_RECIPE_ORDER]
        assert registered_core, (
            "BUNDLED_RECIPE_ORDER is empty at test time — registered_core must be non-empty "
            "for the ordering contract to be verifiable"
        )
        last_registered_idx = core_names.index(registered_core[-1])
        first_unregistered_idx = (
            core_names.index(unregistered_core[0]) if unregistered_core else len(core_names)
        )
        assert last_registered_idx < first_unregistered_idx, (
            "Registered core recipes must appear before unregistered ones"
        )
        assert unregistered_core == sorted(unregistered_core), (
            f"Unregistered core recipes not alphabetical: {unregistered_core}"
        )
        assert addon_names == sorted(addon_names), (
            f"Add-on bundled recipes not in alphabetical order: {addon_names}"
        )

    def test_list_recipes_alphabetical_within_project_tier(self, tmp_path: Path) -> None:
        """Project recipes must be sorted alphabetically by name within their tier."""
        recipes_dir = tmp_path / ".autoskillit" / "recipes"
        recipes_dir.mkdir(parents=True)
        for name in ("zebra", "apple", "mango"):
            (recipes_dir / f"{name}.yaml").write_text(
                f"name: {name}\ndescription: test\nsteps: {{}}\n"
            )
        result = list_recipes(tmp_path)
        project_names = [r.name for r in result.items if r.source == RecipeSource.PROJECT]
        assert project_names == sorted(project_names), (
            f"Project recipes not in alphabetical order: {project_names}"
        )

    def test_list_recipes_excludes_campaign_when_fleet_disabled(self, tmp_path: Path) -> None:
        """list_recipes with exclude_kinds={CAMPAIGN} must omit campaign-kind recipes."""
        recipe_dir = tmp_path / ".autoskillit" / "recipes"
        recipe_dir.mkdir(parents=True)
        (recipe_dir / "my-campaign.yaml").write_text(
            "name: my-campaign\ndescription: test\nkind: campaign\nsteps: {}\n"
        )
        result = list_recipes(tmp_path, exclude_kinds=frozenset({RecipeKind.CAMPAIGN}))
        assert all(r.kind != RecipeKind.CAMPAIGN for r in result.items)

    def test_list_recipes_includes_campaign_when_fleet_enabled(self, tmp_path: Path) -> None:
        """list_recipes with no exclusions must include campaign-kind recipes."""
        recipe_dir = tmp_path / ".autoskillit" / "recipes"
        recipe_dir.mkdir(parents=True)
        (recipe_dir / "my-campaign.yaml").write_text(
            "name: my-campaign\ndescription: test\nkind: campaign\nsteps: {}\n"
        )
        result = list_recipes(tmp_path)
        names = [r.name for r in result.items]
        assert "my-campaign" in names

    def test_recipe_info_kind_field_populated(self, tmp_path: Path) -> None:
        """RecipeInfo.kind must be populated from the YAML kind field."""
        recipe_dir = tmp_path / ".autoskillit" / "recipes"
        recipe_dir.mkdir(parents=True)
        (recipe_dir / "std.yaml").write_text("name: std\ndescription: standard\nsteps: {}\n")
        (recipe_dir / "camp.yaml").write_text(
            "name: camp\ndescription: campaign\nkind: campaign\nsteps: {}\n"
        )
        result = list_recipes(tmp_path)
        kinds = {r.name: r.kind for r in result.items}
        assert kinds["std"] == RecipeKind.STANDARD
        assert kinds["camp"] == RecipeKind.CAMPAIGN

    def test_recipe_info_experimental_field_false_by_default(self, tmp_path: Path) -> None:
        """RecipeInfo.experimental must default to False for standard recipes."""
        recipe_dir = tmp_path / ".autoskillit" / "recipes"
        recipe_dir.mkdir(parents=True)
        (recipe_dir / "plain.yaml").write_text("name: plain\ndescription: plain\nsteps: {}\n")
        result = list_recipes(tmp_path)
        r = next(r for r in result.items if r.name == "plain")
        assert r.experimental is False

    def test_recipe_info_experimental_field_true_when_set(self, tmp_path: Path) -> None:
        """RecipeInfo.experimental must be True when YAML sets experimental: true."""
        recipe_dir = tmp_path / ".autoskillit" / "recipes"
        recipe_dir.mkdir(parents=True)
        (recipe_dir / "research.yaml").write_text(
            "name: research\ndescription: exp\nexperimental: true\nsteps: {}\n"
        )
        result = list_recipes(tmp_path)
        r = next(r for r in result.items if r.name == "research")
        assert r.experimental is True

    def test_list_recipes_bundled_before_family_before_experimental(self, tmp_path: Path) -> None:
        """list_recipes must order: BUILTIN-non-experimental → PROJECT → experimental."""
        from autoskillit.core._type_constants import CORE_PACKS

        recipe_dir = tmp_path / ".autoskillit" / "recipes"
        recipe_dir.mkdir(parents=True)
        (recipe_dir / "proj.yaml").write_text("name: proj\ndescription: p\nsteps: {}\n")
        (recipe_dir / "exp-proj.yaml").write_text(
            "name: exp-proj\ndescription: ep\nexperimental: true\nsteps: {}\n"
        )
        result = list_recipes(tmp_path)
        ranks = []
        for r in result.items:
            if r.experimental:
                rank = 3
            elif r.source == RecipeSource.PROJECT:
                rank = 2
            elif all(p in CORE_PACKS for p in r.requires_packs):
                rank = 0
            else:
                rank = 1
            if not ranks or ranks[-1] != rank:
                ranks.append(rank)
        assert ranks == sorted(ranks), f"Groups interleaved: {ranks}"
        # experimental must be last
        assert 3 in ranks
        assert ranks[-1] == 3

    def test_list_recipes_alphabetical_within_experimental_group(self, tmp_path: Path) -> None:
        """Experimental recipes must be sorted alphabetically by name within their group."""
        recipe_dir = tmp_path / ".autoskillit" / "recipes"
        recipe_dir.mkdir(parents=True)
        for name in ("zebra-exp", "apple-exp", "mango-exp"):
            (recipe_dir / f"{name}.yaml").write_text(
                f"name: {name}\ndescription: test\nexperimental: true\nsteps: {{}}\n"
            )
        result = list_recipes(tmp_path)
        exp_names = [r.name for r in result.items if r.experimental]
        assert exp_names == sorted(exp_names)

    def test_list_recipes_bundled_experimental_sorted_last(self, tmp_path: Path) -> None:
        """A BUILTIN recipe with experimental: true must appear after non-experimental builtins."""
        result = list_recipes(tmp_path)
        non_exp_builtin_indices = [
            i
            for i, r in enumerate(result.items)
            if r.source == RecipeSource.BUILTIN and not r.experimental
        ]
        exp_builtin_indices = [
            i
            for i, r in enumerate(result.items)
            if r.source == RecipeSource.BUILTIN and r.experimental
        ]
        if non_exp_builtin_indices and exp_builtin_indices:
            assert max(non_exp_builtin_indices) < min(exp_builtin_indices)

    def test_recipe_info_has_requires_packs_field(self, tmp_path: Path) -> None:
        """RecipeInfo must have a requires_packs field defaulting to empty list."""
        from autoskillit.recipe.schema import RecipeInfo

        r = RecipeInfo(
            name="x", description="d", source=RecipeSource.BUILTIN, path=tmp_path / "x.yaml"
        )
        assert r.requires_packs == []

    def test_requires_packs_forwarded_to_recipe_info(self, tmp_path: Path) -> None:
        """_collect_recipes must populate RecipeInfo.requires_packs from YAML."""
        recipe_dir = tmp_path / ".autoskillit" / "recipes"
        recipe_dir.mkdir(parents=True)
        (recipe_dir / "custom.yaml").write_text(
            "name: custom\ndescription: d\nrequires_packs: [github, ci]\nsteps: {}\n"
        )
        result = list_recipes(tmp_path)
        r = next(r for r in result.items if r.name == "custom")
        assert r.requires_packs == ["github", "ci"]

    def test_core_bundled_before_addon_bundled(self, tmp_path: Path) -> None:
        """Core bundled recipes (CORE_PACKS only) must sort before add-on bundled recipes."""
        from autoskillit.core._type_constants import CORE_PACKS

        result = list_recipes(tmp_path)
        core_indices = [
            i
            for i, r in enumerate(result.items)
            if r.source == RecipeSource.BUILTIN
            and not r.experimental
            and r.requires_packs
            and all(p in CORE_PACKS for p in r.requires_packs)
        ]
        addon_indices = [
            i
            for i, r in enumerate(result.items)
            if r.source == RecipeSource.BUILTIN
            and not r.experimental
            and r.requires_packs
            and not all(p in CORE_PACKS for p in r.requires_packs)
        ]
        if core_indices and addon_indices:
            assert max(core_indices) < min(addon_indices), (
                "Core bundled recipes must appear before add-on bundled recipes"
            )


class TestBuiltinRecipesDir:
    """Tests for builtin_recipes_dir() function."""

    def test_returns_existing_directory(self) -> None:
        d = builtin_recipes_dir()
        assert d.is_dir(), f"builtin_recipes_dir() {d} is not a directory"

    def test_points_to_recipes(self) -> None:
        d = builtin_recipes_dir()
        assert d.name == "recipes", (
            f"builtin_recipes_dir() should point to 'recipes', got '{d.name}'"
        )

    def test_contains_yaml_files(self) -> None:
        d = builtin_recipes_dir()
        yaml_files = list(d.glob("*.yaml"))
        assert len(yaml_files) > 0, "builtin_recipes_dir() contains no YAML files"


def test_list_recipes_stable_with_project_recipe_added(tmp_path: Path) -> None:
    """Adding a project recipe must not shift the positions of bundled recipes."""
    # Collect bundled positions without any project recipes
    before = [r.name for r in list_recipes(tmp_path).items]

    # Add a project recipe whose name sorts before all bundled recipes
    recipes_dir = tmp_path / ".autoskillit" / "recipes"
    recipes_dir.mkdir(parents=True)
    (recipes_dir / "aaa-custom.yaml").write_text(
        "name: aaa-custom\ndescription: test\nsteps: {}\n"
    )
    after = [r.name for r in list_recipes(tmp_path).items]

    # Bundled names must occupy the same leading positions
    bundled_before = list(before)
    bundled_after = [n for n in after if n in set(bundled_before)]
    assert bundled_after == bundled_before, (
        "Adding a project recipe must not shift bundled recipe positions"
    )


def test_parse_recipe_reads_requires_packs():
    from autoskillit.recipe.io import _parse_recipe

    data = {
        "name": "test",
        "description": "d",
        "requires_packs": ["research", "github"],
    }
    recipe = _parse_recipe(data)
    assert recipe.requires_packs == ["research", "github"]


def test_parse_recipe_requires_packs_defaults_to_empty():
    from autoskillit.recipe.io import _parse_recipe

    recipe = _parse_recipe({"name": "test", "description": "d"})
    assert recipe.requires_packs == []


def test_research_recipe_loads_without_error():
    from autoskillit.core.paths import pkg_root

    path = pkg_root() / "recipes" / "research.yaml"
    recipe = load_recipe(path)
    assert recipe.name == "research"


def test_research_recipe_declares_requires_packs():
    from autoskillit.core.paths import pkg_root

    path = pkg_root() / "recipes" / "research.yaml"
    recipe = load_recipe(path)
    assert recipe.requires_packs == ["research", "exp-lens", "vis-lens"]
