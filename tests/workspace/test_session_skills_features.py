"""Phase 2 tests: session_skills module — feature-gate skill filtering."""

from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit.workspace.session_skills import (
    DefaultSessionSkillManager,
    SkillsDirectoryProvider,
)

pytestmark = [pytest.mark.layer("workspace"), pytest.mark.small]


# ── Tests: feature-gate skill filtering ─────────────────────────────────────


def test_skill_disabled_when_feature_off(tmp_path: Path) -> None:
    """make-campaign excluded from session skills when features.fleet=false."""
    from tests._helpers import make_test_config

    config = make_test_config(features={"fleet": False})
    provider = SkillsDirectoryProvider()
    mgr = DefaultSessionSkillManager(provider, ephemeral_root=tmp_path)
    session_path = mgr.init_session("test-fleet-off", cook_session=False, config=config)
    skill_names = {p.parent.name for p in session_path.glob(".claude/skills/*/SKILL.md")}
    assert "make-campaign" not in skill_names, (
        "make-campaign must be excluded when fleet feature is disabled"
    )


def test_skill_enabled_when_feature_on(tmp_path: Path) -> None:
    """make-campaign present in session skills when features.fleet=true."""
    from tests._helpers import make_test_config

    config = make_test_config(features={"fleet": True})
    provider = SkillsDirectoryProvider()
    mgr = DefaultSessionSkillManager(provider, ephemeral_root=tmp_path)
    session_path = mgr.init_session("test-fleet-on", cook_session=False, config=config)
    skill_names = {p.parent.name for p in session_path.glob(".claude/skills/*/SKILL.md")}
    assert "make-campaign" in skill_names, (
        "make-campaign must be present when fleet feature is enabled"
    )


def test_skill_suppressed_when_no_features_config(tmp_path: Path) -> None:
    """Feature-gated skills use default_enabled when config is None (no features dict)."""
    provider = SkillsDirectoryProvider()
    mgr = DefaultSessionSkillManager(provider, ephemeral_root=tmp_path)
    session_path = mgr.init_session("test-no-features-config", cook_session=False, config=None)
    skill_names = {p.parent.name for p in session_path.glob(".claude/skills/*/SKILL.md")}
    assert "make-campaign" not in skill_names, (
        "make-campaign must be absent when no features config is provided "
        "(fleet.default_enabled=False)"
    )


def test_other_skills_unaffected_by_fleet_feature(tmp_path: Path) -> None:
    """Non-fleet skills unaffected when fleet feature is disabled."""
    from tests._helpers import make_test_config

    config_off = make_test_config(features={"fleet": False})
    config_on = make_test_config(features={"fleet": True})
    provider = SkillsDirectoryProvider()
    mgr = DefaultSessionSkillManager(provider, ephemeral_root=tmp_path)
    session_off = mgr.init_session(
        "test-others-unaffected-off", cook_session=False, config=config_off
    )
    session_on = mgr.init_session(
        "test-others-unaffected-on", cook_session=False, config=config_on
    )
    skill_names_off = {p.parent.name for p in session_off.glob(".claude/skills/*/SKILL.md")}
    skill_names_on = {p.parent.name for p in session_on.glob(".claude/skills/*/SKILL.md")}
    assert "make-plan" in skill_names_off
    assert "implement-worktree" in skill_names_off
    # Count invariant: fleet=False removes exactly the fleet-category skills
    assert skill_names_off.issubset(skill_names_on), (
        "fleet=False must only remove skills, not introduce new suppressions"
    )
    suppressed = skill_names_on - skill_names_off
    assert suppressed, "fleet=False must suppress at least one skill"
    assert "make-campaign" in suppressed, "make-campaign must be in the suppressed set"
    assert "make-plan" not in suppressed, "make-plan must not be suppressed by fleet=False"
    assert "implement-worktree" not in suppressed, (
        "implement-worktree must not be suppressed by fleet=False"
    )


# ── Tests: cook session + feature-gate interaction ─────────────────────────


def test_cook_session_bypasses_feature_gate(tmp_path: Path) -> None:
    """Cook sessions see all skills regardless of feature flags."""
    from tests._helpers import make_test_config

    config = make_test_config(features={"fleet": False})
    provider = SkillsDirectoryProvider()
    mgr = DefaultSessionSkillManager(provider, ephemeral_root=tmp_path)
    session_path = mgr.init_session("cook-feat-gate", cook_session=True, config=config)
    skill_names = {p.parent.name for p in session_path.glob(".claude/skills/*/SKILL.md")}
    assert "make-campaign" in skill_names, (
        "cook_session=True should bypass feature gates — "
        "make-campaign must be available even when fleet is disabled"
    )


def test_cook_session_disabled_feature_tags_empty(tmp_path: Path) -> None:
    """disabled_feature_tags is empty frozenset for cook sessions."""
    from tests._helpers import make_test_config

    config = make_test_config(features={"fleet": False})
    provider = SkillsDirectoryProvider()
    mgr = DefaultSessionSkillManager(provider, ephemeral_root=tmp_path)
    session_path = mgr.init_session("cook-tags-empty", cook_session=True, config=config)
    # Verify via the integration effect: fleet-tagged skills are present
    skill_names = {p.parent.name for p in session_path.glob(".claude/skills/*/SKILL.md")}
    # If disabled_feature_tags were non-empty, fleet skills would be suppressed
    # via _resolve_effective_disabled even without the _is_skill_disabled feature loop
    assert "make-campaign" in skill_names


def test_non_cook_session_still_suppresses_feature_gated_skills(tmp_path: Path) -> None:
    """Non-cook sessions with fleet=False still suppress make-campaign."""
    from tests._helpers import make_test_config

    config = make_test_config(features={"fleet": False})
    provider = SkillsDirectoryProvider()
    mgr = DefaultSessionSkillManager(provider, ephemeral_root=tmp_path)
    session_path = mgr.init_session("non-cook-feat", cook_session=False, config=config)
    skill_names = {p.parent.name for p in session_path.glob(".claude/skills/*/SKILL.md")}
    assert "make-campaign" not in skill_names, (
        "Non-cook session with fleet=False must suppress make-campaign"
    )


def test_feature_gate_suppresses_when_allow_only_is_none(tmp_path: Path) -> None:
    """Feature gate still suppresses planner skills when allow_only=None."""
    from tests._helpers import make_test_config

    config = make_test_config(features={}, experimental_enabled=False)
    provider = SkillsDirectoryProvider()
    mgr = DefaultSessionSkillManager(provider, ephemeral_root=tmp_path)
    session_path = mgr.init_session(
        "feat-gate-allow-none", cook_session=False, config=config, allow_only=None
    )
    skill_names = {p.parent.name for p in session_path.glob(".claude/skills/*/SKILL.md")}
    assert "planner-elaborate-phase" not in skill_names, (
        "planner-elaborate-phase must be suppressed by the planner feature gate "
        "when allow_only=None (no orchestrator-requested override)"
    )


# ── Tests: recipe_features parameter ─────────────────────────────────────────


def test_recipe_features_enables_planner_skills(tmp_path: Path) -> None:
    """Planner skills are available when recipe declares requires_features: [planner]."""
    from tests._helpers import make_test_config

    config = make_test_config(features={}, experimental_enabled=False)
    provider = SkillsDirectoryProvider()
    mgr = DefaultSessionSkillManager(provider, ephemeral_root=tmp_path)
    session_path = mgr.init_session(
        "recipe-feat-enables",
        cook_session=False,
        config=config,
        recipe_features=frozenset({"planner"}),
    )
    skill_names = {p.parent.name for p in session_path.glob(".claude/skills/*/SKILL.md")}
    assert "planner-elaborate-phase" in skill_names, (
        "planner-elaborate-phase must be present when recipe_features includes 'planner'"
    )


def test_recipe_features_do_not_override_explicit_user_disable(tmp_path: Path) -> None:
    """Explicit features.planner=False in config wins over recipe_features."""
    from tests._helpers import make_test_config

    config = make_test_config(features={"planner": False}, experimental_enabled=False)
    provider = SkillsDirectoryProvider()
    mgr = DefaultSessionSkillManager(provider, ephemeral_root=tmp_path)
    session_path = mgr.init_session(
        "recipe-feat-no-override",
        cook_session=False,
        config=config,
        recipe_features=frozenset({"planner"}),
    )
    skill_names = {p.parent.name for p in session_path.glob(".claude/skills/*/SKILL.md")}
    assert "planner-elaborate-phase" not in skill_names, (
        "recipe_features must not override explicit user config features.planner=False"
    )


def test_recipe_features_enables_multiple_features(tmp_path: Path) -> None:
    """Recipe declaring requires_features: [planner, fleet] enables both."""
    from tests._helpers import make_test_config

    config = make_test_config(features={}, experimental_enabled=False)
    provider = SkillsDirectoryProvider()
    mgr = DefaultSessionSkillManager(provider, ephemeral_root=tmp_path)
    session_path = mgr.init_session(
        "recipe-feat-multi",
        cook_session=False,
        config=config,
        recipe_features=frozenset({"planner", "fleet"}),
    )
    skill_names = {p.parent.name for p in session_path.glob(".claude/skills/*/SKILL.md")}
    assert "planner-elaborate-phase" in skill_names, (
        "planner-elaborate-phase must be present when recipe_features includes 'planner'"
    )
    assert "make-campaign" in skill_names, (
        "make-campaign must be present when recipe_features includes 'fleet'"
    )


@pytest.mark.parametrize(
    "recipe_features,expected",
    [
        (None, False),
        (frozenset(), False),
        (frozenset({"planner"}), True),
    ],
    ids=["none", "empty", "planner"],
)
def test_recipe_features_cross_axis(
    tmp_path: Path, recipe_features: frozenset | None, expected: bool
) -> None:
    """recipe_features axis interacts correctly with feature gate."""
    from tests._helpers import make_test_config

    config = make_test_config(features={}, experimental_enabled=False)
    provider = SkillsDirectoryProvider()
    mgr = DefaultSessionSkillManager(provider, ephemeral_root=tmp_path)
    session_path = mgr.init_session(
        f"recipe-feat-cross-{id(recipe_features)}",
        cook_session=False,
        config=config,
        recipe_features=recipe_features,
    )
    skill_names = {p.parent.name for p in session_path.glob(".claude/skills/*/SKILL.md")}
    present = "planner-elaborate-phase" in skill_names
    assert present == expected, (
        f"recipe_features={recipe_features!r}: expected planner-elaborate-phase "
        f"present={expected}, got {present}"
    )
