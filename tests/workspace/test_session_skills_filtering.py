"""Phase 2 tests: session_skills module — subset/disabled-category and pack filtering."""

from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit.workspace.session_skills import (
    DefaultSessionSkillManager,
    SkillsDirectoryProvider,
)

pytestmark = [pytest.mark.layer("workspace"), pytest.mark.small]


def test_init_session_unknown_skill_logs_warning(tmp_path: Path) -> None:
    """Unknown skill name in config.skills.tier2 logs a warning (REQ-TIER-010)."""
    import structlog.testing

    from tests._helpers import make_skills_config, make_test_config

    config = make_test_config(
        skills=make_skills_config(
            tier1=["open-kitchen", "close-kitchen"],
            tier2=["this-skill-does-not-exist-anywhere"],
            tier3=[],
        )
    )
    mgr = DefaultSessionSkillManager(SkillsDirectoryProvider(), ephemeral_root=tmp_path)
    with structlog.testing.capture_logs() as cap_logs:
        mgr.init_session("test_unknown_warn", cook_session=False, config=config)
    assert any(
        "this-skill-does-not-exist-anywhere" in str(entry.get("event", ""))
        for entry in cap_logs
        if entry.get("log_level") == "warning"
    )


# T-VIS-006
def test_init_session_skips_disabled_builtin_category(tmp_path: Path) -> None:
    """Skills whose SKILL.md categories overlap disabled built-in tags are excluded."""
    from unittest.mock import MagicMock

    from autoskillit.workspace.session_skills import DefaultSessionSkillManager
    from autoskillit.workspace.skills import SkillInfo, SkillSource
    from tests._helpers import make_subsetsconfig, make_test_config

    skill_dir = tmp_path / "skills" / "fake-github-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("---\ncategories:\n  - github\n---\n# Fake GitHub Skill\n")

    provider = MagicMock()
    provider.list_skills.return_value = [
        SkillInfo(
            name="fake-github-skill",
            source=SkillSource.BUNDLED_EXTENDED,
            path=skill_dir / "SKILL.md",
            categories=frozenset({"github"}),
        )
    ]

    config = make_test_config(subsets=make_subsetsconfig(disabled=["github"]))
    root = tmp_path / "sessions"
    root.mkdir()
    mgr = DefaultSessionSkillManager(provider, ephemeral_root=root)
    session_path = mgr.init_session("test-subset-skip", config=config)

    assert not (session_path / ".claude" / "skills" / "fake-github-skill").exists(), (
        "Skill with disabled category 'github' must not be copied to ephemeral dir"
    )


# T-VIS-007
def test_init_session_skips_disabled_custom_tag(tmp_path: Path) -> None:
    """Skills listed under a custom_tag that is disabled are excluded."""
    from unittest.mock import MagicMock

    from autoskillit.workspace.session_skills import DefaultSessionSkillManager
    from autoskillit.workspace.skills import SkillInfo, SkillSource
    from tests._helpers import make_subsetsconfig, make_test_config

    skill_dir = tmp_path / "skills" / "my-custom-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("---\ncategories: []\n---\n# My Custom Skill\n")

    provider = MagicMock()
    provider.list_skills.return_value = [
        SkillInfo(
            name="my-custom-skill",
            source=SkillSource.BUNDLED_EXTENDED,
            path=skill_dir / "SKILL.md",
            categories=frozenset(),
        )
    ]
    provider.get_skill_content.return_value = "---\ncategories: []\n---\n# My Custom Skill\n"

    config = make_test_config(
        subsets=make_subsetsconfig(
            disabled=["data-infra"],
            custom_tags={"data-infra": ["my-custom-skill"]},
        )
    )
    root = tmp_path / "sessions"
    root.mkdir()
    mgr = DefaultSessionSkillManager(provider, ephemeral_root=root)
    session_path = mgr.init_session("test-custom-skip", config=config)

    assert not (session_path / ".claude" / "skills" / "my-custom-skill").exists(), (
        "Skill listed under disabled custom_tag must not be copied"
    )


# T-VIS-008
def test_init_session_includes_non_disabled_skills(tmp_path: Path) -> None:
    """Skills not in any disabled category are still copied to ephemeral dir."""
    from unittest.mock import MagicMock

    from autoskillit.workspace.session_skills import DefaultSessionSkillManager
    from autoskillit.workspace.skills import SkillInfo, SkillSource
    from tests._helpers import make_subsetsconfig, make_test_config

    skill_dir = tmp_path / "skills" / "safe-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("---\ncategories:\n  - audit\n---\n# Safe Skill\n")

    provider = MagicMock()
    provider.list_skills.return_value = [
        SkillInfo(
            name="safe-skill",
            source=SkillSource.BUNDLED_EXTENDED,
            path=skill_dir / "SKILL.md",
            categories=frozenset({"audit"}),
        )
    ]
    provider.get_skill_content.return_value = "---\ncategories:\n  - audit\n---\n# Safe Skill\n"

    config = make_test_config(subsets=make_subsetsconfig(disabled=["github"]))
    root = tmp_path / "sessions"
    root.mkdir()
    mgr = DefaultSessionSkillManager(provider, ephemeral_root=root)
    session_path = mgr.init_session("test-safe-include", config=config)

    assert (session_path / ".claude" / "skills" / "safe-skill").exists(), (
        "Skills in non-disabled categories must be included"
    )


# REQ-EPH-002
def test_cleanup_stale_default_is_72_hours() -> None:
    import inspect

    sig = inspect.signature(DefaultSessionSkillManager.cleanup_stale)
    default = sig.parameters["max_age_seconds"].default
    assert default == 259200, f"Expected 259200 (72h), got {default}"


# REQ-PACK-005 / REQ-PACK-006: _resolve_effective_disabled
def test_resolve_effective_disabled_default_disabled_excluded() -> None:
    from autoskillit.core import PACK_REGISTRY
    from autoskillit.workspace.session_skills import _resolve_effective_disabled

    result = _resolve_effective_disabled(
        explicit_disabled=[],
        pack_registry=PACK_REGISTRY,
        packs_enabled=[],
        recipe_packs=None,
    )
    # Default-disabled packs (research, exp-lens) should be in result
    assert "research" in result
    assert "exp-lens" in result
    # Default-enabled packs should NOT be disabled
    assert "github" not in result


def test_resolve_effective_disabled_packs_enabled_overrides_default() -> None:
    from autoskillit.core import PACK_REGISTRY
    from autoskillit.workspace.session_skills import _resolve_effective_disabled

    result = _resolve_effective_disabled(
        explicit_disabled=[],
        pack_registry=PACK_REGISTRY,
        packs_enabled=["research"],
        recipe_packs=None,
    )
    assert "research" not in result  # enabled by packs.enabled


# REQ-PACK-004: subsets.disabled always overrides packs.enabled
def test_resolve_effective_disabled_explicit_wins_over_pack_enabled() -> None:
    from autoskillit.core import PACK_REGISTRY
    from autoskillit.workspace.session_skills import _resolve_effective_disabled

    result = _resolve_effective_disabled(
        explicit_disabled=["github"],  # explicitly disabled
        pack_registry=PACK_REGISTRY,
        packs_enabled=["github"],  # also in packs.enabled — explicit wins
        recipe_packs=None,
    )
    assert "github" in result  # explicit disable survives


def test_resolve_effective_disabled_recipe_packs_overrides_default() -> None:
    from autoskillit.core import PACK_REGISTRY
    from autoskillit.workspace.session_skills import _resolve_effective_disabled

    result = _resolve_effective_disabled(
        explicit_disabled=[],
        pack_registry=PACK_REGISTRY,
        packs_enabled=[],
        recipe_packs=frozenset(["research"]),
    )
    assert "research" not in result  # enabled by recipe


def test_resolve_effective_disabled_includes_feature_tags() -> None:
    from autoskillit.core import PACK_REGISTRY
    from autoskillit.workspace.session_skills import _resolve_effective_disabled

    result = _resolve_effective_disabled(
        explicit_disabled=[],
        pack_registry=PACK_REGISTRY,
        packs_enabled=[],
        recipe_packs=None,
        disabled_feature_tags=frozenset({"fleet"}),
    )
    assert "fleet" in result


# REQ-PACK-006: Cook sessions skip default-disabled packs
def test_cook_session_skips_default_disabled_packs(tmp_path: Path) -> None:
    """Cook session excludes default-disabled pack skills when packs.enabled=[]."""
    from unittest.mock import MagicMock

    from autoskillit.workspace.skills import SkillInfo, SkillSource
    from tests._helpers import make_test_config

    skill_dir = tmp_path / "skills" / "research-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("---\ncategories:\n  - research\n---\n# Research Skill\n")

    provider = MagicMock()
    provider.list_skills.return_value = [
        SkillInfo(
            name="research-skill",
            source=SkillSource.BUNDLED_EXTENDED,
            path=skill_dir / "SKILL.md",
            categories=frozenset({"research"}),
        )
    ]
    provider.get_skill_content.return_value = (
        "---\ncategories:\n  - research\n---\n# Research Skill\n"
    )

    root = tmp_path / "sessions"
    root.mkdir()
    config = make_test_config()  # packs.enabled=[] by default
    mgr = DefaultSessionSkillManager(provider, ephemeral_root=root)
    session_path = mgr.init_session("cook-research", cook_session=True, config=config)

    assert not (session_path / ".claude" / "skills" / "research-skill").exists(), (
        "Default-disabled pack skill must not be in cook session when packs.enabled=[]"
    )


# REQ-PACK-005: headless sessions exclude default-disabled packs
def test_headless_session_excludes_default_disabled_pack_skills(tmp_path: Path) -> None:
    """Skills in 'exp-lens' pack are excluded from headless session when packs.enabled=[]."""
    from unittest.mock import MagicMock

    from autoskillit.workspace.skills import SkillInfo, SkillSource
    from tests._helpers import make_test_config

    skill_dir = tmp_path / "skills" / "exp-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\ncategories:\n  - exp-lens\n---\n# Experimental Skill\n"
    )

    provider = MagicMock()
    provider.list_skills.return_value = [
        SkillInfo(
            name="exp-skill",
            source=SkillSource.BUNDLED_EXTENDED,
            path=skill_dir / "SKILL.md",
            categories=frozenset({"exp-lens"}),
        )
    ]

    root = tmp_path / "sessions"
    root.mkdir()
    config = make_test_config()  # packs.enabled=[] by default
    mgr = DefaultSessionSkillManager(provider, ephemeral_root=root)
    session_path = mgr.init_session("headless-exp", cook_session=False, config=config)

    assert not (session_path / ".claude" / "skills" / "exp-skill").exists(), (
        "Default-disabled pack skill must not be in headless session when packs.enabled=[]"
    )


def test_init_session_recipe_packs_enables_default_disabled(tmp_path: Path) -> None:
    """recipe_packs param enables default-disabled pack skills for this session."""
    from unittest.mock import MagicMock

    from autoskillit.workspace.skills import SkillInfo, SkillSource
    from tests._helpers import make_test_config

    skill_dir = tmp_path / "skills" / "research-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("---\ncategories:\n  - research\n---\n# Research Skill\n")

    provider = MagicMock()
    provider.list_skills.return_value = [
        SkillInfo(
            name="research-skill",
            source=SkillSource.BUNDLED_EXTENDED,
            path=skill_dir / "SKILL.md",
            categories=frozenset({"research"}),
        )
    ]
    provider.get_skill_content.return_value = (
        "---\ncategories:\n  - research\n---\n# Research Skill\n"
    )

    root = tmp_path / "sessions"
    root.mkdir()
    config = make_test_config()  # packs.enabled=[] by default
    mgr = DefaultSessionSkillManager(provider, ephemeral_root=root)
    session_path = mgr.init_session(
        "headless-recipe-research",
        cook_session=False,
        config=config,
        recipe_packs=frozenset(["research"]),
    )

    assert (session_path / ".claude" / "skills" / "research-skill").exists(), (
        "Default-disabled pack skill should be present when enabled via recipe_packs"
    )
