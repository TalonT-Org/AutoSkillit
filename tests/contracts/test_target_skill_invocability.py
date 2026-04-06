"""Contract: the target skill of a run_skill call must be invocable after session setup.

Verifies that the invocation chain (init_session → activate_skill_deps → resolve namespace)
leaves the target skill invocable and all other Tier 2 skills gated.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit.config import AutomationConfig, load_config
from autoskillit.core import SkillSource, extract_skill_name, resolve_target_skill
from autoskillit.recipe import load_recipe
from autoskillit.recipe.io import builtin_recipes_dir
from autoskillit.workspace import (
    DefaultSessionSkillManager,
    SkillResolver,
    SkillsDirectoryProvider,
)


def _make_session(
    tmp_path: Path,
    target_skill: str,
    config: AutomationConfig | None = None,
) -> tuple[DefaultSessionSkillManager, str, str]:
    """Create an ephemeral session and activate the target skill if Tier 2.

    Returns (manager, session_id, ephemeral_root_str).
    """
    provider = SkillsDirectoryProvider()
    mgr = DefaultSessionSkillManager(provider, tmp_path)
    if config is None:
        config = load_config()
    session_id = "test-session-001"
    mgr.init_session(
        session_id,
        cook_session=False,
        config=config,
    )
    tier2 = frozenset(config.skills.tier2)
    if target_skill in tier2:
        mgr.activate_skill_deps(session_id, target_skill)
    return mgr, session_id, str(tmp_path)


def _read_skill_md(tmp_path: Path, session_id: str, skill_name: str) -> str:
    """Read the ephemeral SKILL.md for a given skill in a session."""
    return (tmp_path / session_id / ".claude" / "skills" / skill_name / "SKILL.md").read_text()


class TestTargetSkillNotGatedAfterActivation:
    """Tier 2 target skills must have disable-model-invocation removed after activation."""

    def test_tier2_target_skill_not_gated_after_activation(self, tmp_path: Path) -> None:
        config = load_config()
        tier2 = list(config.skills.tier2)
        assert len(tier2) > 0, "No Tier 2 skills configured"
        target = tier2[0]

        _make_session(tmp_path, target, config)
        content = _read_skill_md(tmp_path, "test-session-001", target)
        assert "disable-model-invocation: true" not in content

    def test_other_tier2_skills_remain_gated(self, tmp_path: Path) -> None:
        config = load_config()
        tier2 = list(config.skills.tier2)
        assert len(tier2) > 1, "Need at least 2 Tier 2 skills"
        target = tier2[0]

        _make_session(tmp_path, target, config)
        for other in tier2[1:]:
            path = tmp_path / "test-session-001" / ".claude" / "skills" / other / "SKILL.md"
            if path.exists():
                content = path.read_text()
                assert "disable-model-invocation: true" in content, (
                    f"Non-target Tier 2 skill '{other}' should remain gated"
                )

    def test_tier3_target_skill_never_gated(self, tmp_path: Path) -> None:
        config = load_config()
        tier3 = list(config.skills.tier3)
        if not tier3:
            pytest.skip("No Tier 3 skills configured")
        target = tier3[0]

        _make_session(tmp_path, target, config)
        content = _read_skill_md(tmp_path, "test-session-001", target)
        assert "disable-model-invocation: true" not in content


class TestResolvedNamespaceMatchesSkillLocation:
    """Namespace resolution must match physical skill location."""

    def test_bundled_extended_skill_uses_bare_namespace(self) -> None:
        resolver = SkillResolver()
        info = resolver.resolve("make-plan")
        assert info is not None
        assert info.source == SkillSource.BUNDLED_EXTENDED
        resolved, name = resolve_target_skill("/autoskillit:make-plan arg1", resolver)
        assert name == "make-plan"
        assert resolved == "/make-plan arg1"

    def test_bundled_skill_uses_autoskillit_namespace(self) -> None:
        resolver = SkillResolver()
        info = resolver.resolve("open-kitchen")
        assert info is not None
        assert info.source == SkillSource.BUNDLED
        resolved, name = resolve_target_skill("/open-kitchen", resolver)
        assert name == "open-kitchen"
        assert resolved == "/autoskillit:open-kitchen"

    def test_already_correct_namespace_is_preserved(self) -> None:
        resolver = SkillResolver()
        resolved, name = resolve_target_skill("/make-plan arg1 arg2", resolver)
        assert name == "make-plan"
        assert resolved == "/make-plan arg1 arg2"

    def test_non_slash_command_passes_through(self) -> None:
        resolver = SkillResolver()
        resolved, name = resolve_target_skill("Fix the bug", resolver)
        assert name is None
        assert resolved == "Fix the bug"


class TestDepSkillsNotGatedAfterActivation:
    """After activating a target with activate_deps, dependency skills are also ungated."""

    def test_dep_skills_not_gated_after_activation(self, tmp_path: Path) -> None:
        """After activating make-plan, arch-lens-* and mermaid skills are ungated."""
        config = load_config()
        provider = SkillsDirectoryProvider()
        mgr = DefaultSessionSkillManager(provider, tmp_path)
        session_id = "test-dep-activation"
        mgr.init_session(session_id, cook_session=False, config=config)
        mgr.activate_skill_deps(session_id, "make-plan")

        skills_base = tmp_path / session_id / ".claude" / "skills"
        # Check arch-lens skills are ungated
        for skill_dir in sorted(skills_base.iterdir()):
            if not skill_dir.is_dir():
                continue
            name = skill_dir.name
            if not name.startswith("arch-lens-"):
                continue
            content = (skill_dir / "SKILL.md").read_text()
            assert "disable-model-invocation: true" not in content, (
                f"arch-lens skill '{name}' should be ungated after activating make-plan"
            )

        # Check mermaid is ungated
        mermaid_md = skills_base / "mermaid" / "SKILL.md"
        assert mermaid_md.exists(), "mermaid skill dir should exist after init_session"
        content = mermaid_md.read_text()
        assert "disable-model-invocation: true" not in content, (
            "mermaid should be ungated via transitive dependency from make-plan"
        )


class TestAllRecipeSkillCommandsInvocable:
    """Every run_skill step in bundled recipes must resolve to an invocable form."""

    def test_all_bundled_recipes_skill_commands_invocable_after_init_session(
        self, tmp_path: Path
    ) -> None:
        config = load_config()
        tier2 = frozenset(config.skills.tier2)
        resolver = SkillResolver()
        provider = SkillsDirectoryProvider()

        for yaml_path in sorted(builtin_recipes_dir().glob("*.yaml")):
            recipe = load_recipe(yaml_path)
            for step_name, step in recipe.steps.items():
                if step.tool != "run_skill":
                    continue
                sc = step.with_args.get("skill_command", "")
                if "${{" in sc:
                    continue  # skip dynamic skill commands

                name = extract_skill_name(sc)
                if name is None:
                    continue

                # Verify namespace resolution
                resolved, resolved_name = resolve_target_skill(sc, resolver)
                info = resolver.resolve(name)
                if info is None:
                    continue  # covered by unknown-skill-command rule

                if info.source == SkillSource.BUNDLED_EXTENDED:
                    assert not resolved.startswith("/autoskillit:"), (
                        f"Recipe '{yaml_path.stem}' step '{step_name}': "
                        f"skill '{name}' is BUNDLED_EXTENDED but resolved to "
                        f"'{resolved}' (should use bare /name namespace)"
                    )
                elif info.source == SkillSource.BUNDLED:
                    assert resolved.startswith("/autoskillit:"), (
                        f"Recipe '{yaml_path.stem}' step '{step_name}': "
                        f"skill '{name}' is BUNDLED but resolved to "
                        f"'{resolved}' (should use /autoskillit: namespace)"
                    )

                # Verify activation: after init_session + activate_skill_deps, target is invocable
                session_id = f"test-{yaml_path.stem}-{step_name}"
                mgr = DefaultSessionSkillManager(provider, tmp_path)
                mgr.init_session(session_id, cook_session=False, config=config)
                if name in tier2:
                    mgr.activate_skill_deps(session_id, name)
                skill_md_path = tmp_path / session_id / ".claude" / "skills" / name / "SKILL.md"
                if skill_md_path.exists():
                    content = skill_md_path.read_text()
                    assert "disable-model-invocation: true" not in content, (
                        f"Recipe '{yaml_path.stem}' step '{step_name}': "
                        f"target skill '{name}' still gated after activation"
                    )
