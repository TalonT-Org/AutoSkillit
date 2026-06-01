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
        from autoskillit.core.types._type_constants_registries import CORE_PACKS
        from autoskillit.recipe.order import BUNDLED_RECIPE_ORDER

        result = list_recipes(tmp_path)
        core_names = [
            r.name
            for r in result.items
            if r.source == RecipeSource.BUILTIN
            and not r.experimental
            and r.requires_packs
            and all(p in CORE_PACKS for p in r.requires_packs)
        ]
        addon_names = [
            r.name
            for r in result.items
            if r.source == RecipeSource.BUILTIN
            and not r.experimental
            and (not r.requires_packs or not all(p in CORE_PACKS for p in r.requires_packs))
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
        from autoskillit.core.types._type_constants_registries import CORE_PACKS

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
            elif r.requires_packs and all(p in CORE_PACKS for p in r.requires_packs):
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
        from autoskillit.core.types._type_constants_registries import CORE_PACKS

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


# ---------------------------------------------------------------------------
# Food-truck kind tests
# ---------------------------------------------------------------------------


def test_food_truck_excluded_from_order_menu(tmp_path: Path) -> None:
    """kind: food-truck recipe is excluded when exclude_kinds includes FOOD_TRUCK."""
    recipes_dir = tmp_path / ".autoskillit" / "recipes"
    recipes_dir.mkdir(parents=True)
    (recipes_dir / "food-truck-test.yaml").write_text(
        "name: food-truck-test\ndescription: test\nkind: food-truck\n"
        "steps:\n  done:\n    action: stop\n    message: sentinel done\n"
    )
    result = list_recipes(
        tmp_path,
        exclude_kinds=frozenset({RecipeKind.CAMPAIGN, RecipeKind.FOOD_TRUCK}),
    )
    names = {r.name for r in result.items}
    assert "food-truck-test" not in names


def test_food_truck_included_when_not_excluded(tmp_path: Path) -> None:
    """kind: food-truck recipe is included when exclude_kinds does not include FOOD_TRUCK."""
    recipes_dir = tmp_path / ".autoskillit" / "recipes"
    recipes_dir.mkdir(parents=True)
    (recipes_dir / "food-truck-test.yaml").write_text(
        "name: food-truck-test\ndescription: test\nkind: food-truck\n"
        "steps:\n  done:\n    action: stop\n    message: sentinel done\n"
    )
    result = list_recipes(tmp_path)
    names = {r.name for r in result.items}
    assert "food-truck-test" in names


def test_list_all_excludes_food_truck_when_fleet_off(tmp_path: Path) -> None:
    """list_all with fleet disabled excludes FOOD_TRUCK kind recipes."""
    from autoskillit.recipe._api import list_all

    recipes_dir = tmp_path / ".autoskillit" / "recipes"
    recipes_dir.mkdir(parents=True)
    (recipes_dir / "my-food-truck.yaml").write_text(
        "name: my-food-truck\ndescription: test\nkind: food-truck\n"
        "steps:\n  done:\n    action: stop\n    message: sentinel done\n"
    )
    result = list_all(tmp_path, features={"fleet": False})
    names = {r["name"] for r in result["recipes"]}
    assert "my-food-truck" not in names


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


# ---------------------------------------------------------------------------
# Campaign discovery gap tests (Step 1 from architecture fix plan)
# ---------------------------------------------------------------------------


def test_find_recipe_by_name_finds_campaign_in_campaigns_subdir(tmp_path: Path) -> None:
    """find_recipe_by_name must discover recipes in campaigns/ subdir."""
    from autoskillit.recipe.io import find_recipe_by_name

    campaigns_dir = tmp_path / ".autoskillit" / "recipes" / "campaigns"
    campaigns_dir.mkdir(parents=True)
    (campaigns_dir / "my-campaign.yaml").write_text(
        "name: my-campaign\nkind: campaign\ndescription: test\nsteps:\n  s1:\n    skill: noop\n"
    )
    result = find_recipe_by_name("my-campaign", tmp_path)
    assert result is not None
    assert result.name == "my-campaign"


def test_list_recipes_includes_campaigns_subdir(tmp_path: Path) -> None:
    """list_recipes must scan campaigns/ subdirectory."""
    campaigns_dir = tmp_path / ".autoskillit" / "recipes" / "campaigns"
    campaigns_dir.mkdir(parents=True)
    (campaigns_dir / "c1.yaml").write_text(
        "name: c1\nkind: campaign\ndescription: test\nsteps:\n  s1:\n    skill: noop\n"
    )
    result = list_recipes(tmp_path)
    names = [r.name for r in result.items]
    assert "c1" in names


def test_all_builtin_recipe_yamls_are_discoverable(tmp_path: Path) -> None:
    """Every .yaml in a RECIPE_SCAN_DIR must appear in list_recipes results.

    This is the structural guard: if a new subdirectory with recipes is added
    but not registered in RECIPE_SCAN_DIRS, this test fails.
    """
    from autoskillit.recipe.io import RECIPE_SCAN_DIRS, builtin_recipes_dir

    base = builtin_recipes_dir()
    expected_paths: set[Path] = set()
    for subdir in RECIPE_SCAN_DIRS:
        scan_dir = base / subdir if subdir else base
        if scan_dir.is_dir():
            # Non-recursive: _collect_recipes scans only one level deep per RECIPE_SCAN_DIR.
            # If recursion is ever added to the implementation, this test must be updated too.
            for f in scan_dir.iterdir():
                if f.suffix in (".yaml", ".yml") and f.is_file():
                    expected_paths.add(f)

    result = list_recipes(tmp_path)
    discovered_paths = {r.path for r in result.items if r.source == RecipeSource.BUILTIN}

    missing = expected_paths - discovered_paths
    assert not missing, f"Recipe YAMLs not discoverable by list_recipes: {missing}"


def test_non_recipe_dirs_covers_all_excluded_subdirs(tmp_path: Path) -> None:
    """Every subdirectory under recipes/ must be in RECIPE_SCAN_DIRS or NON_RECIPE_DIRS.

    Prevents silent omission when a new subdirectory is added.
    """
    from autoskillit.recipe.io import NON_RECIPE_DIRS, RECIPE_SCAN_DIRS, builtin_recipes_dir

    base = builtin_recipes_dir()
    all_subdirs = {d.name for d in base.iterdir() if d.is_dir()}
    registered = {d for d in RECIPE_SCAN_DIRS if d} | NON_RECIPE_DIRS
    unregistered = all_subdirs - registered
    assert not unregistered, (
        f"Subdirectories not in RECIPE_SCAN_DIRS or NON_RECIPE_DIRS: {unregistered}. "
        f"Add each to RECIPE_SCAN_DIRS (if it contains user-facing recipes) "
        f"or NON_RECIPE_DIRS (if not)."
    )


def test_eval_scan_dir_discoverable(tmp_path: Path) -> None:
    """Recipe YAML in .autoskillit/recipes/eval/ must be discoverable via list_recipes."""
    eval_dir = tmp_path / ".autoskillit" / "recipes" / "eval"
    eval_dir.mkdir(parents=True)
    (eval_dir / "test-eval.yaml").write_text(
        "name: test-eval\ndescription: test eval recipe\n"
        "steps:\n  done:\n    action: stop\n    message: test\n"
    )
    result = list_recipes(tmp_path)
    r = next((x for x in result.items if x.name == "test-eval"), None)
    assert r is not None
    assert r.source == RecipeSource.PROJECT


def test_eval_fixture_inventory() -> None:
    """All eval fixture files must exist at their canonical .autoskillit/recipes/eval/ location.

    This is a regression guard: if a file is accidentally deleted or moved back to temp/,
    this test fails immediately rather than silently breaking the eval pipeline.
    """
    repo_root = Path(__file__).resolve().parent.parent.parent
    eval_dir = repo_root / ".autoskillit" / "recipes" / "eval"

    expected_canaries = [
        "C1-task.md",
        "C1-reference.md",
        "C4-task.md",
        "C7-task.md",
        "C7-reference.md",
        "impl_commit_b7fa51e2.patch",
        "RP10-diff.txt",
        "RP10-reference.md",
        "RP11-diff.txt",
        "RP11-reference.md",
        "RP12-diff.txt",
        "RP12-reference.md",
    ]
    expected_overlays = [
        "baseline.md",
        "consumer-contract.md",
        "infrastructure-audit.md",
        "migration-scope.md",
        "adversarial-review.md",
        "full-adversarial.md",
    ]
    expected_manifests = [
        "make-plan-canaries.json",
        "make-plan-variants.json",
        "review-pr-canaries.json",
    ]

    missing = []
    for name in expected_canaries:
        if not (eval_dir / "canaries" / name).exists():
            missing.append(f"canaries/{name}")
    for name in expected_overlays:
        if not (eval_dir / "overlays" / name).exists():
            missing.append(f"overlays/{name}")
    for name in expected_manifests:
        if not (eval_dir / "manifests" / name).exists():
            missing.append(f"manifests/{name}")

    assert not missing, (
        f"Eval fixture files missing from .autoskillit/recipes/eval/: {missing}. "
        "These files must be tracked at this canonical location, not under temp/."
    )


def test_eval_manifest_paths_point_to_recipes_eval() -> None:
    """Manifest JSON files must reference fixture paths under .autoskillit/recipes/eval/,
    not temp/.

    Verifies that after relocation the canary_manifest and variant_manifest path fields
    no longer contain 'temp/' — a stale reference would silently fail at eval runtime.
    """
    import json

    repo_root = Path(__file__).resolve().parent.parent.parent
    manifests_dir = repo_root / ".autoskillit" / "recipes" / "eval" / "manifests"

    canary_manifest = json.loads((manifests_dir / "make-plan-canaries.json").read_text())
    for entry in canary_manifest:
        if entry.get("task_file"):
            assert "temp/" not in entry["task_file"], (
                f"Canary {entry['id']} task_file still points to temp/: {entry['task_file']!r}"
            )
        if entry.get("reference_path"):
            assert "temp/" not in entry["reference_path"], (
                f"Canary {entry['id']} reference_path still points to temp/: "
                f"{entry['reference_path']!r}"
            )

    variant_manifest = json.loads((manifests_dir / "make-plan-variants.json").read_text())
    for entry in variant_manifest:
        if entry.get("overlay_file"):
            assert "temp/" not in entry["overlay_file"], (
                f"Variant {entry['id']} overlay_file still points to temp/: "
                f"{entry['overlay_file']!r}"
            )

    review_canary_manifest = json.loads((manifests_dir / "review-pr-canaries.json").read_text())
    for entry in review_canary_manifest:
        for key, val in entry.get("prompt_vars", {}).items():
            if isinstance(val, str):
                assert "temp/" not in val, (
                    f"Canary {entry['id']} prompt_vars.{key} still points to temp/: {val!r}"
                )
        if entry.get("reference_path"):
            assert "temp/" not in entry["reference_path"], (
                f"Canary {entry['id']} reference_path still points to temp/: "
                f"{entry['reference_path']!r}"
            )
