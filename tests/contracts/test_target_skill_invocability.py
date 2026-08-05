"""Contract: the target skill of a run_skill call must be invocable after session setup.

Verifies that the invocation chain (init_session → activate_skill_deps → resolve namespace)
leaves the target skill invocable and all other Tier 2 skills gated.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit.core import SkillSource, extract_skill_name, resolve_target_skill
from autoskillit.recipe import load_recipe
from autoskillit.recipe.io import all_validated_recipe_paths
from autoskillit.workspace import (
    DefaultSkillResolver,
)

pytestmark = [pytest.mark.layer("contracts"), pytest.mark.medium]

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


class TestResolvedNamespaceMatchesSkillLocation:
    """Namespace resolution must match physical skill location."""

    def test_bundled_extended_skill_uses_bare_namespace(self) -> None:
        resolver = DefaultSkillResolver()
        info = resolver.resolve("make-plan")
        assert info is not None
        assert info.source == SkillSource.BUNDLED_EXTENDED
        resolved, name = resolve_target_skill(
            "/autoskillit:make-plan arg1", resolver, _PROJECT_ROOT
        )
        assert name == "make-plan"
        assert resolved == "/make-plan arg1"

    def test_bundled_skill_uses_autoskillit_namespace(self) -> None:
        resolver = DefaultSkillResolver()
        info = resolver.resolve("open-kitchen")
        assert info is not None
        assert info.source == SkillSource.BUNDLED
        resolved, name = resolve_target_skill("/open-kitchen", resolver, _PROJECT_ROOT)
        assert name == "open-kitchen"
        assert resolved == "/autoskillit:open-kitchen"

    def test_already_correct_namespace_is_preserved(self) -> None:
        resolver = DefaultSkillResolver()
        resolved, name = resolve_target_skill("/make-plan arg1 arg2", resolver, _PROJECT_ROOT)
        assert name == "make-plan"
        assert resolved == "/make-plan arg1 arg2"

    def test_non_slash_command_passes_through(self) -> None:
        resolver = DefaultSkillResolver()
        resolved, name = resolve_target_skill("Fix the bug", resolver, _PROJECT_ROOT)
        assert name is None
        assert resolved == "Fix the bug"

    def test_project_override_controls_target_namespace(self, tmp_path: Path) -> None:
        override = tmp_path / ".claude" / "skills" / "open-kitchen" / "SKILL.md"
        override.parent.mkdir(parents=True)
        override.write_text(
            "---\n"
            "name: open-kitchen\n"
            "description: Project-local target.\n"
            "execution_role: session\n"
            "---\n"
            "override\n"
        )

        resolved, name = resolve_target_skill(
            "/autoskillit:open-kitchen",
            DefaultSkillResolver(),
            tmp_path,
        )

        assert name == "open-kitchen"
        assert resolved == "/open-kitchen"

    def test_invalid_project_override_renders_as_unresolved(self, tmp_path: Path) -> None:
        """T12d: an invalid project-local override renders as unresolved
        (no new imports on this IL-0 path — only the invalid_reason field
        already visible on the Protocol) instead of resolving to a
        possibly-wrong namespace.

        Uses a fabricated, local-only skill name (no bundled twin) so
        ``resolve_effective`` cannot fall through to a valid bundled entry —
        the only scenario in which the invalid ``SkillInfo`` still escapes
        and this guard has anything to do. The input already carries the
        ``autoskillit:`` sigil; if the guard were absent, the fall-through
        render would use the invalid candidate's PROJECT_LOCAL source
        (namespace ``""``) and strip that sigil, producing a visibly
        different ``/my-broken`` — manually verified by reverting the guard.
        """
        override = tmp_path / ".claude" / "skills" / "my-broken" / "SKILL.md"
        override.parent.mkdir(parents=True)
        override.write_text(
            '---\nname: my-broken\n---\nSpawn via `Agent(model="sonnet")`.\n',
            encoding="utf-8",
        )

        resolved, name = resolve_target_skill(
            "/autoskillit:my-broken",
            DefaultSkillResolver(),
            tmp_path,
        )

        assert name == "my-broken"
        assert resolved == "/autoskillit:my-broken"


class TestRoleDerivedInvocability:
    def test_process_issues_only_appears_in_orchestrator_catalog(self) -> None:
        from autoskillit.core import SkillExecutionRole

        resolver = DefaultSkillResolver()
        session_names = {
            skill.name
            for skill in resolver.list_effective(_PROJECT_ROOT, SkillExecutionRole.SESSION).skills
        }
        orchestrator_names = {
            skill.name
            for skill in resolver.list_effective(
                _PROJECT_ROOT, SkillExecutionRole.ORCHESTRATOR
            ).skills
        }

        assert "process-issues" not in session_names
        assert "process-issues" in orchestrator_names

    def test_direct_session_invocation_cannot_target_process_issues(self) -> None:
        from autoskillit.core import SkillContractError, SkillExecutionRole

        resolver = DefaultSkillResolver()
        with pytest.raises(SkillContractError, match="process-issues|ORCHESTRATOR"):
            resolver.resolve_invocation(
                "process-issues",
                _PROJECT_ROOT,
                SkillExecutionRole.SESSION,
            )

        assert (
            resolver.resolve_invocation(
                "process-issues",
                _PROJECT_ROOT,
                SkillExecutionRole.ORCHESTRATOR,
            )
            is not None
        )


class TestAllRecipeSkillCommandsInvocable:
    """Every static run_skill target must resolve to an exact SESSION invocation."""

    def test_all_bundled_recipe_targets_resolve_exact_invocations(self) -> None:
        from autoskillit.core import SkillExecutionRole

        resolver = DefaultSkillResolver()
        bundled_recipes = [
            path
            for path in all_validated_recipe_paths(_PROJECT_ROOT)
            if "src/autoskillit/recipes" in str(path)
        ]
        for yaml_path in bundled_recipes:
            recipe = load_recipe(yaml_path)
            for step_name, step in recipe.steps.items():
                if step.tool != "run_skill":
                    continue
                skill_command = step.with_args.get("skill_command", "")
                if "{" in skill_command:
                    continue
                name = extract_skill_name(skill_command)
                if name is None:
                    continue

                invocation = resolver.resolve_invocation(
                    name,
                    _PROJECT_ROOT,
                    SkillExecutionRole.SESSION,
                )

                assert invocation is not None, (
                    f"Recipe {yaml_path.stem!r} step {step_name!r} has "
                    f"unresolvable target {name!r}"
                )
                assert invocation.root.name == name
                assert invocation.root in invocation.closure
