"""Tests for backend-incompatible-skill semantic rule."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from autoskillit.core import Severity, SkillSource
from autoskillit.recipe._analysis import make_validation_context
from autoskillit.recipe.registry import run_semantic_rules
from autoskillit.recipe.schema import Recipe, RecipeStep

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]


def _make_skill_info(
    name: str = "investigate",
    backend_requirements: frozenset[str] = frozenset({"claude-code"}),
    uses_capabilities: frozenset[str] = frozenset(),
) -> SimpleNamespace:
    return SimpleNamespace(
        name=name,
        source=SkillSource.BUNDLED_EXTENDED,
        path=Path("/nonexistent/SKILL.md"),
        categories=frozenset(),
        backend_requirements=backend_requirements,
        uses_capabilities=uses_capabilities,
    )


def _make_recipe_with_skill_step(skill_command: str) -> Recipe:
    steps = {
        "run-skill-step": RecipeStep(
            tool="run_skill",
            with_args={"skill_command": skill_command, "cwd": "/tmp"},
        )
    }
    return Recipe(name="test-recipe", description="test", steps=steps)


def _mock_resolver(skill_info: SimpleNamespace | None) -> MagicMock:
    resolver = MagicMock()
    resolver.resolve.return_value = skill_info
    resolver.list_all.return_value = [skill_info] if skill_info else []
    return resolver


class TestBackendIncompatibleSkillRule:
    def test_incompatible_backend_produces_error(self):
        skill_info = _make_skill_info()
        recipe = _make_recipe_with_skill_step("/investigate something")
        resolver = _mock_resolver(skill_info)
        ctx = make_validation_context(
            recipe,
            backend_name="codex",
            skill_resolver=resolver,
            available_skills=frozenset({"investigate"}),
        )
        findings = run_semantic_rules(ctx)
        compat_findings = [f for f in findings if f.rule == "backend-incompatible-skill"]
        assert len(compat_findings) == 1
        assert compat_findings[0].severity == Severity.ERROR
        assert "investigate" in compat_findings[0].message
        assert "codex" in compat_findings[0].message

    def test_compatible_backend_no_finding(self):
        skill_info = _make_skill_info()
        recipe = _make_recipe_with_skill_step("/investigate something")
        resolver = _mock_resolver(skill_info)
        ctx = make_validation_context(
            recipe,
            backend_name="claude-code",
            skill_resolver=resolver,
            available_skills=frozenset({"investigate"}),
        )
        findings = run_semantic_rules(ctx)
        compat_findings = [f for f in findings if f.rule == "backend-incompatible-skill"]
        assert len(compat_findings) == 0

    def test_none_backend_skips_gracefully(self):
        skill_info = _make_skill_info()
        recipe = _make_recipe_with_skill_step("/investigate something")
        resolver = _mock_resolver(skill_info)
        ctx = make_validation_context(
            recipe,
            backend_name=None,
            skill_resolver=resolver,
            available_skills=frozenset({"investigate"}),
        )
        findings = run_semantic_rules(ctx)
        compat_findings = [f for f in findings if f.rule == "backend-incompatible-skill"]
        assert len(compat_findings) == 0

    def test_empty_backend_requirements_no_finding(self):
        skill_info = _make_skill_info(backend_requirements=frozenset())
        recipe = _make_recipe_with_skill_step("/investigate something")
        resolver = _mock_resolver(skill_info)
        ctx = make_validation_context(
            recipe,
            backend_name="codex",
            skill_resolver=resolver,
            available_skills=frozenset({"investigate"}),
        )
        findings = run_semantic_rules(ctx)
        compat_findings = [f for f in findings if f.rule == "backend-incompatible-skill"]
        assert len(compat_findings) == 0

    def test_no_resolver_skips_gracefully(self):
        recipe = _make_recipe_with_skill_step("/investigate something")
        ctx = make_validation_context(
            recipe,
            backend_name="codex",
            skill_resolver=None,
            available_skills=frozenset({"investigate"}),
        )
        findings = run_semantic_rules(ctx)
        compat_findings = [f for f in findings if f.rule == "backend-incompatible-skill"]
        assert len(compat_findings) == 0

    def test_rules_backend_compat_fires_for_worker_routable_pinned_to_codex(self):
        """A worker_routable skill (empty backend_requirements, only
        uses_capabilities=frozenset({'git_metadata_write'})) explicitly pinned
        via effective_backend_map to a codex backend that lacks the required
        BackendCapabilities.git_metadata_writable property must fire the
        backend-incompatible-skill rule."""
        from autoskillit.core.types._type_backend import BackendCapabilities

        # resolve-review declares uses_capabilities=[..., git_metadata_write, ...]
        # but the /investigate fixture resolver returns a worker_routable
        # skill with empty backend_requirements and only git_metadata_write
        # capability — matches the architecturally interesting case.
        skill_info = _make_skill_info(
            name="resolve-review",
            backend_requirements=frozenset(),
            uses_capabilities=frozenset({"git_metadata_write"}),
        )
        steps = {
            "run-skill-step": RecipeStep(
                tool="run_skill",
                with_args={"skill_command": "/resolve-review", "cwd": "/tmp"},
            )
        }
        recipe = Recipe(name="test-recipe", description="test", steps=steps)
        resolver = _mock_resolver(skill_info)
        codex_caps = BackendCapabilities(git_metadata_writable=False)
        ctx = make_validation_context(
            recipe,
            backend_name="claude-code",
            skill_resolver=resolver,
            effective_backend_map={"run-skill-step": "codex"},
            backend_capabilities_map={"codex": codex_caps},
            available_skills=frozenset({"resolve-review"}),
        )
        findings = run_semantic_rules(ctx)
        compat_findings = [f for f in findings if f.rule == "backend-incompatible-skill"]
        assert len(compat_findings) == 1, (
            f"Expected one backend-incompatible-skill finding for worker_routable "
            f"skill pinned to codex, got {compat_findings}"
        )
        msg = compat_findings[0].message
        assert "git_metadata_writable" in msg, (
            f"Finding must mention git_metadata_writable capability mismatch, got: {msg!r}"
        )
        assert "codex" in msg, f"Finding must reference the pinned codex backend, got: {msg!r}"


_RECIPE_WITH_SKILL_STEP_YAML = """\
name: test-compat
description: recipe with run_skill step
autoskillit_version: "0.2.0"
steps:
  run-skill:
    tool: run_skill
    with:
      skill_command: "/investigate something"
      cwd: /tmp
  stop:
    action: stop
    message: done
"""


class _FakeSkillResolver:
    """Minimal resolver satisfying both SkillLister and SkillResolver protocols."""

    def __init__(self, skill_info: SimpleNamespace | None) -> None:
        self._info = skill_info

    def list_all(self) -> list[SimpleNamespace]:
        return [self._info] if self._info else []

    def resolve(self, name: str) -> SimpleNamespace | None:
        return self._info if self._info and self._info.name == name else None


class TestBackendNameThreadingAPI:
    """Integration tests verifying backend_name reaches semantic rules via API."""

    def test_load_and_validate_threads_backend_name(self, tmp_path, monkeypatch):
        import autoskillit.recipe._api as api_mod
        import autoskillit.recipe._api_cache as cache_mod

        monkeypatch.setattr(cache_mod, "_LOAD_CACHE", cache_mod.LoadCache())

        recipes_dir = tmp_path / ".autoskillit" / "recipes"
        recipes_dir.mkdir(parents=True)
        (recipes_dir / "test-compat.yaml").write_text(_RECIPE_WITH_SKILL_STEP_YAML)

        skill_info = _make_skill_info()
        resolver = _FakeSkillResolver(skill_info)

        result = api_mod.load_and_validate(
            "test-compat", tmp_path, backend_name="codex", lister=resolver
        )

        suggestions = result.get("suggestions", [])
        compat_findings = [f for f in suggestions if f.get("rule") == "backend-incompatible-skill"]
        assert len(compat_findings) == 1
        assert compat_findings[0]["severity"] == "error"

    def test_validate_from_path_threads_backend_name(self, tmp_path):
        from autoskillit.recipe._api_listing import validate_from_path

        recipe_path = tmp_path / "test-compat.yaml"
        recipe_path.write_text(_RECIPE_WITH_SKILL_STEP_YAML)

        skill_info = _make_skill_info()
        resolver = _FakeSkillResolver(skill_info)

        result = validate_from_path(recipe_path, backend_name="codex", lister=resolver)

        findings = result.get("findings", [])
        compat_findings = [f for f in findings if f.get("rule") == "backend-incompatible-skill"]
        assert len(compat_findings) == 1
        assert compat_findings[0]["severity"] == "error"

    def test_cache_key_varies_by_backend_name(self, tmp_path, monkeypatch):
        import autoskillit.recipe._api as api_mod
        import autoskillit.recipe._api_cache as cache_mod

        monkeypatch.setattr(cache_mod, "_LOAD_CACHE", cache_mod.LoadCache())

        recipes_dir = tmp_path / ".autoskillit" / "recipes"
        recipes_dir.mkdir(parents=True)
        (recipes_dir / "test-compat.yaml").write_text(_RECIPE_WITH_SKILL_STEP_YAML)

        skill_info = _make_skill_info()
        resolver = _FakeSkillResolver(skill_info)

        api_mod.load_and_validate("test-compat", tmp_path, backend_name=None, lister=resolver)
        api_mod.load_and_validate("test-compat", tmp_path, backend_name="codex", lister=resolver)

        cache = cache_mod._LOAD_CACHE
        assert len(cache._store) == 2

    def test_cache_key_varies_by_backend_capability_values(self, tmp_path, monkeypatch):
        import autoskillit.recipe._api as api_mod
        import autoskillit.recipe._api_cache as cache_mod
        from autoskillit.core import BackendCapabilities

        monkeypatch.setattr(cache_mod, "_LOAD_CACHE", cache_mod.LoadCache())

        recipes_dir = tmp_path / ".autoskillit" / "recipes"
        recipes_dir.mkdir(parents=True)
        (recipes_dir / "test-compat.yaml").write_text(_RECIPE_WITH_SKILL_STEP_YAML)

        skill_info = _make_skill_info(
            backend_requirements=frozenset(),
            uses_capabilities=frozenset({"git_metadata_write"}),
        )
        resolver = _FakeSkillResolver(skill_info)

        incapable = api_mod.load_and_validate(
            "test-compat",
            tmp_path,
            backend_name="codex",
            backend_capabilities_map={"codex": BackendCapabilities(git_metadata_writable=False)},
            lister=resolver,
        )
        capable = api_mod.load_and_validate(
            "test-compat",
            tmp_path,
            backend_name="codex",
            backend_capabilities_map={"codex": BackendCapabilities(git_metadata_writable=True)},
            lister=resolver,
        )

        assert any(
            finding.get("rule") == "backend-incompatible-skill"
            for finding in incapable["suggestions"]
        )
        assert not any(
            finding.get("rule") == "backend-incompatible-skill"
            for finding in capable["suggestions"]
        )
        assert len(cache_mod._LOAD_CACHE._store) == 2


# ---------------------------------------------------------------------------
# Backend-parameterized bundled recipe validation tests
# ---------------------------------------------------------------------------


class TestBundledRecipeBackendCompat:
    """Validate that bundled recipes fire backend-incompatible-skill for Codex."""

    @pytest.fixture(
        scope="class",
        params=["implementation", "implementation-groups", "remediation"],
        ids=lambda x: x,
    )
    def recipe_name(self, request: pytest.FixtureRequest) -> str:
        return request.param

    def test_codex_backend_fires_for_git_metadata_skills(
        self, recipe_name, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "autoskillit.server.tools._auto_overrides.shutil.which",
            lambda name: "/usr/local/bin/claude" if name == "claude" else None,
        )
        from autoskillit.recipe._analysis import make_validation_context
        from autoskillit.recipe.io import builtin_recipes_dir, load_recipe
        from autoskillit.recipe.registry import run_semantic_rules
        from autoskillit.server.tools._auto_overrides import _compute_effective_backend_map
        from autoskillit.workspace.skills import DefaultSkillResolver

        recipe = load_recipe(builtin_recipes_dir() / f"{recipe_name}.yaml")
        resolver = DefaultSkillResolver()
        eff_map, _ = _compute_effective_backend_map(
            recipe.steps,
            "codex",
            None,
            recipe_name,
            skill_resolver=resolver,
        )
        ctx = make_validation_context(
            recipe,
            backend_name="codex",
            skill_resolver=resolver,
            effective_backend_map=eff_map,
            available_skills=frozenset(s.name for s in resolver.list_all()),
        )
        findings = run_semantic_rules(ctx)
        compat_findings = [f for f in findings if f.rule == "backend-incompatible-skill"]
        assert compat_findings == [], (
            f"Expected zero backend-incompatible-skill findings for {recipe_name} "
            f"on codex backend when capability route is active; "
            f"got {len(compat_findings)} findings"
        )

    def test_codex_backend_no_compat_findings_for_routable_skills(self, recipe_name) -> None:
        from autoskillit.recipe._analysis import make_validation_context
        from autoskillit.recipe.io import builtin_recipes_dir, load_recipe
        from autoskillit.recipe.registry import run_semantic_rules
        from autoskillit.workspace.skills import DefaultSkillResolver

        recipe = load_recipe(builtin_recipes_dir() / f"{recipe_name}.yaml")
        resolver = DefaultSkillResolver()
        ctx = make_validation_context(
            recipe,
            backend_name="codex",
            skill_resolver=resolver,
            available_skills=frozenset(s.name for s in resolver.list_all()),
        )
        findings = run_semantic_rules(ctx)
        compat_findings = [f for f in findings if f.rule == "backend-incompatible-skill"]
        assert len(compat_findings) == 0, (
            f"Expected 0 backend-incompatible-skill findings for {recipe_name} "
            f"on codex backend: git_metadata_write skills are worker_routable=True "
            f"(required_backends=frozenset()) so the compat gate must NOT fire for them; "
            f"got {len(compat_findings)} findings: {compat_findings}"
        )

    def test_claude_code_backend_no_findings(self, recipe_name) -> None:
        from autoskillit.recipe._analysis import make_validation_context
        from autoskillit.recipe.io import builtin_recipes_dir, load_recipe
        from autoskillit.recipe.registry import run_semantic_rules
        from autoskillit.workspace.skills import DefaultSkillResolver

        recipe = load_recipe(builtin_recipes_dir() / f"{recipe_name}.yaml")
        resolver = DefaultSkillResolver()
        ctx = make_validation_context(
            recipe,
            backend_name="claude-code",
            skill_resolver=resolver,
            available_skills=frozenset(s.name for s in resolver.list_all()),
        )
        findings = run_semantic_rules(ctx)
        compat_findings = [f for f in findings if f.rule == "backend-incompatible-skill"]
        assert len(compat_findings) == 0, (
            f"No backend-incompatible-skill findings expected for {recipe_name} "
            f"on claude-code backend, got: {compat_findings}"
        )


# ---------------------------------------------------------------------------
# Severity promotion — guarded steps remain WARNING, unguarded escalate to ERROR
# ---------------------------------------------------------------------------


def _make_guarded_recipe(skip_when_false: str | None = None, optional: bool = False) -> Recipe:
    step: RecipeStep
    if skip_when_false is not None:
        step = RecipeStep(
            tool="run_skill",
            with_args={"skill_command": "/investigate something", "cwd": "/tmp"},
            skip_when_false=skip_when_false,
            optional=optional,
        )
    elif optional:
        step = RecipeStep(
            tool="run_skill",
            with_args={"skill_command": "/investigate something", "cwd": "/tmp"},
            optional=True,
        )
    else:
        step = RecipeStep(
            tool="run_skill",
            with_args={"skill_command": "/investigate something", "cwd": "/tmp"},
        )
    return Recipe(name="test-recipe", description="test", steps={"run-skill-step": step})


class TestSeverityPromotion:
    """All incompatible steps fire ERROR regardless of guards."""

    def test_unguarded_incompatible_step_fires_error(self) -> None:
        recipe = _make_guarded_recipe(skip_when_false=None, optional=False)
        resolver = _mock_resolver(_make_skill_info())
        ctx = make_validation_context(
            recipe,
            backend_name="codex",
            skill_resolver=resolver,
            available_skills=frozenset({"investigate"}),
        )
        findings = run_semantic_rules(ctx)
        compat = [f for f in findings if f.rule == "backend-incompatible-skill"]
        assert len(compat) == 1
        assert compat[0].severity == Severity.ERROR

    def test_guarded_incompatible_step_fires_error(self) -> None:
        recipe = _make_guarded_recipe(skip_when_false="inputs.some_guard", optional=False)
        resolver = _mock_resolver(_make_skill_info())
        ctx = make_validation_context(
            recipe,
            backend_name="codex",
            skill_resolver=resolver,
            available_skills=frozenset({"investigate"}),
        )
        findings = run_semantic_rules(ctx)
        compat = [f for f in findings if f.rule == "backend-incompatible-skill"]
        assert len(compat) == 1
        assert compat[0].severity == Severity.ERROR

    def test_optional_incompatible_step_fires_error(self) -> None:
        recipe = _make_guarded_recipe(skip_when_false=None, optional=True)
        resolver = _mock_resolver(_make_skill_info())
        ctx = make_validation_context(
            recipe,
            backend_name="codex",
            skill_resolver=resolver,
            available_skills=frozenset({"investigate"}),
        )
        findings = run_semantic_rules(ctx)
        compat = [f for f in findings if f.rule == "backend-incompatible-skill"]
        assert len(compat) == 1
        assert compat[0].severity == Severity.ERROR

    def test_skip_when_true_incompatible_step_fires_error(self) -> None:
        step = RecipeStep(
            tool="run_skill",
            with_args={"skill_command": "/investigate something", "cwd": "/tmp"},
            skip_when_true="inputs.some_flag",
        )
        recipe = Recipe(
            name="test-recipe",
            description="test",
            steps={"run-skill-step": step},
        )
        resolver = _mock_resolver(_make_skill_info())
        ctx = make_validation_context(
            recipe,
            backend_name="codex",
            skill_resolver=resolver,
            available_skills=frozenset({"investigate"}),
        )
        findings = run_semantic_rules(ctx)
        compat = [f for f in findings if f.rule == "backend-incompatible-skill"]
        assert len(compat) == 1
        assert compat[0].severity == Severity.ERROR


# ---------------------------------------------------------------------------
# Bundled recipe ingredient declaration tests
# ---------------------------------------------------------------------------


class TestBundledRecipeIngredientDeclaration:
    """Bundled recipes declare backend_supports_git_write as hidden config-authoritative."""

    @pytest.fixture(
        scope="class",
        params=[
            "implementation",
            "implementation-groups",
            "remediation",
            "merge-prs",
            "research-implement",
            "research",
            "research-review",
        ],
        ids=lambda x: x,
    )
    def recipe_name(self, request: pytest.FixtureRequest) -> str:
        return request.param

    def test_bundled_recipes_declare_backend_supports_git_write(self, recipe_name) -> None:
        from autoskillit.recipe.io import builtin_recipes_dir, load_recipe

        recipe = load_recipe(builtin_recipes_dir() / f"{recipe_name}.yaml")
        assert "backend_supports_git_write" in recipe.ingredients, (
            f"Recipe {recipe_name!r} must declare 'backend_supports_git_write' ingredient"
        )
        ing = recipe.ingredients["backend_supports_git_write"]
        assert ing.hidden is True, f"backend_supports_git_write must be hidden in {recipe_name}"
        assert ing.authority == "config", (
            f"backend_supports_git_write must be authority=config in {recipe_name}"
        )
        assert ing.default == "true", (
            f"backend_supports_git_write default must be 'true' in {recipe_name}"
        )


# ---------------------------------------------------------------------------
# Bundled recipe step guard tests
# ---------------------------------------------------------------------------


class TestBundledRecipeStepGuards:
    """Every git_metadata_write step in bundled recipes has a skip_when_false guard."""

    @pytest.fixture(
        scope="class",
        params=[
            "implementation",
            "implementation-groups",
            "remediation",
            "merge-prs",
            "research-implement",
            "research",
            "research-review",
        ],
        ids=lambda x: x,
    )
    def recipe_name(self, request: pytest.FixtureRequest) -> str:
        return request.param

    def test_git_metadata_write_steps_have_skip_guard(self, recipe_name) -> None:
        from autoskillit.core.types._type_helpers import extract_skill_name
        from autoskillit.recipe.io import builtin_recipes_dir, load_recipe
        from autoskillit.workspace.skills import DefaultSkillResolver

        recipe = load_recipe(builtin_recipes_dir() / f"{recipe_name}.yaml")
        resolver = DefaultSkillResolver()
        checked_count = 0
        for step_name, step in recipe.steps.items():
            if step.tool != "run_skill":
                continue
            skill_cmd = step.with_args.get("skill_command", "")
            if not skill_cmd or not skill_cmd.startswith("/"):
                continue
            bare_name = extract_skill_name(skill_cmd)
            if bare_name is None:
                continue
            skill_info = resolver.resolve(bare_name)
            if skill_info is None:
                continue
            if (
                not skill_info.uses_capabilities
                or "git_metadata_write" not in skill_info.uses_capabilities
            ):
                continue
            assert step.skip_when_false is not None, (
                f"Step {step_name!r} in {recipe_name!r} dispatches git_metadata_write skill "
                f"'{skill_info.name}' but has no skip_when_false guard"
            )
            checked_count += 1
        assert checked_count > 0, (
            f"No git_metadata_write steps found in {recipe_name!r} — "
            "test is vacuous (skill resolution may be broken)"
        )
