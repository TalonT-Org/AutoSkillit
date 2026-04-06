"""Phase 2 tests: session_skills module (resolver, provider, manager)."""

from __future__ import annotations

import os
import re
import time
from pathlib import Path

import pytest
import yaml

from autoskillit.workspace.session_skills import (
    DefaultSessionSkillManager,
    SkillsDirectoryProvider,
    resolve_ephemeral_root,
)


def test_resolve_ephemeral_root_returns_writable_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import autoskillit.workspace.session_skills as ss

    monkeypatch.setattr(ss, "_CANDIDATE_ROOTS", [tmp_path])
    root = resolve_ephemeral_root()
    assert root.exists()
    assert root.is_dir()
    test_file = root / "write_test.tmp"
    test_file.write_text("ok")
    test_file.unlink()


def test_resolve_ephemeral_root_fallback(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import autoskillit.workspace.session_skills as ss

    monkeypatch.setattr(ss, "_CANDIDATE_ROOTS", [Path("/nonexistent"), tmp_path])
    root = ss.resolve_ephemeral_root()
    assert root.exists()


def test_skills_directory_provider_lists_all_skills() -> None:
    provider = SkillsDirectoryProvider()
    skills = provider.list_skills()
    names = {s.name for s in skills}
    assert "open-kitchen" in names
    assert "close-kitchen" in names
    assert "implement-worktree" in names
    assert "sous-chef" not in names  # internal, excluded


def test_provider_injects_disable_model_invocation_for_tier2() -> None:
    provider = SkillsDirectoryProvider()
    content = provider.get_skill_content("open-kitchen", gated=True)
    fm_match = re.match(r"^---\n(.*?)\n---", content, re.DOTALL)
    assert fm_match, "Content must have YAML frontmatter"
    fm = yaml.safe_load(fm_match.group(1))
    assert fm.get("disable-model-invocation") is True


def test_provider_does_not_inject_for_cook_session() -> None:
    # Use mermaid (skills_extended/, no flag at rest) to verify that gated=False
    # returns unmodified content without injecting disable-model-invocation.
    # open-kitchen and close-kitchen carry disable-model-invocation: true in their source
    # (human-only skills), so they cannot be used to assert "flag not present".
    provider = SkillsDirectoryProvider()
    content = provider.get_skill_content("mermaid", gated=False)
    fm_match = re.match(r"^---\n(.*?)\n---", content, re.DOTALL)
    assert fm_match, "Content must have YAML frontmatter"
    fm = yaml.safe_load(fm_match.group(1))
    assert fm.get("disable-model-invocation") is not True


def test_session_skill_manager_creates_ephemeral_dir(tmp_path: Path) -> None:
    provider = SkillsDirectoryProvider()
    mgr = DefaultSessionSkillManager(provider, ephemeral_root=tmp_path)
    session_path = mgr.init_session("test-session-abc", cook_session=False)
    assert session_path.exists()
    assert session_path.is_dir()
    skill_files = list(session_path.glob(".claude/skills/*/SKILL.md"))
    assert len(skill_files) > 0


def test_session_manager_injects_disable_for_tier2(tmp_path: Path) -> None:
    """Non-cook init_session injects disable-model-invocation for tier2 skills."""
    from autoskillit.config.settings import AutomationConfig, SkillsConfig

    provider = SkillsDirectoryProvider()
    mgr = DefaultSessionSkillManager(provider, ephemeral_root=tmp_path)
    config = AutomationConfig(
        skills=SkillsConfig(
            tier1=["open-kitchen", "close-kitchen"],
            tier2=["mermaid"],
            tier3=[],
        )
    )
    session_path = mgr.init_session("test-session-xyz", cook_session=False, config=config)
    mermaid_md = session_path / ".claude" / "skills" / "mermaid" / "SKILL.md"
    assert mermaid_md.exists()
    content = mermaid_md.read_text()
    fm_match = re.match(r"^---\n(.*?)\n---", content, re.DOTALL)
    assert fm_match
    fm = yaml.safe_load(fm_match.group(1))
    assert fm.get("disable-model-invocation") is True


def test_session_manager_no_flag_for_cook_session(tmp_path: Path) -> None:
    """Cook session does not inject disable-model-invocation even for tier2 skills."""
    from autoskillit.config.settings import AutomationConfig, SkillsConfig

    provider = SkillsDirectoryProvider()
    mgr = DefaultSessionSkillManager(provider, ephemeral_root=tmp_path)
    config = AutomationConfig(
        skills=SkillsConfig(
            tier1=["open-kitchen", "close-kitchen"],
            tier2=["mermaid"],
            tier3=[],
        )
    )
    session_path = mgr.init_session("cook-session-123", cook_session=True, config=config)
    mermaid_md = session_path / ".claude" / "skills" / "mermaid" / "SKILL.md"
    content = mermaid_md.read_text()
    fm_match = re.match(r"^---\n(.*?)\n---", content, re.DOTALL)
    assert fm_match
    fm = yaml.safe_load(fm_match.group(1))
    assert fm.get("disable-model-invocation") is not True


def test_activate_tier2_removes_flag(tmp_path: Path) -> None:
    from autoskillit.config.settings import AutomationConfig, SkillsConfig

    provider = SkillsDirectoryProvider()
    mgr = DefaultSessionSkillManager(provider, ephemeral_root=tmp_path)
    config = AutomationConfig(
        skills=SkillsConfig(
            tier1=["open-kitchen", "close-kitchen"],
            tier2=["mermaid"],
            tier3=[],
        )
    )
    mgr.init_session("session-toggle", cook_session=False, config=config)
    result = mgr.activate_tier2("session-toggle", "mermaid")
    assert result is True
    mermaid_md = tmp_path / "session-toggle" / ".claude" / "skills" / "mermaid" / "SKILL.md"
    content = mermaid_md.read_text()
    fm_match = re.match(r"^---\n(.*?)\n---", content, re.DOTALL)
    assert fm_match
    fm = yaml.safe_load(fm_match.group(1))
    assert "disable-model-invocation" not in fm or fm.get("disable-model-invocation") is not True


def test_cleanup_stale_removes_old_dirs(tmp_path: Path) -> None:
    provider = SkillsDirectoryProvider()
    mgr = DefaultSessionSkillManager(provider, ephemeral_root=tmp_path)
    stale_dir = tmp_path / "stale-session"
    stale_dir.mkdir()
    os.utime(stale_dir, (time.time() - 90000, time.time() - 90000))  # 25h old
    fresh_dir = tmp_path / "fresh-session"
    fresh_dir.mkdir()
    count = mgr.cleanup_stale(max_age_seconds=86400)
    assert count == 1
    assert not stale_dir.exists()
    assert fresh_dir.exists()


def test_tier2_skills_constant_removed() -> None:
    """TIER2_SKILLS no longer exported from workspace (superseded by config)."""
    import autoskillit.workspace as ws

    assert not hasattr(ws, "TIER2_SKILLS")


def test_init_session_unknown_skill_logs_warning(tmp_path: Path) -> None:
    """Unknown skill name in config.skills.tier2 logs a warning (REQ-TIER-010)."""
    import structlog.testing

    from autoskillit.config.settings import AutomationConfig, SkillsConfig

    config = AutomationConfig(
        skills=SkillsConfig(
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

    from autoskillit.config.settings import AutomationConfig, SubsetsConfig
    from autoskillit.workspace.session_skills import DefaultSessionSkillManager
    from autoskillit.workspace.skills import SkillInfo, SkillSource

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

    config = AutomationConfig(subsets=SubsetsConfig(disabled=["github"]))
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

    from autoskillit.config.settings import AutomationConfig, SubsetsConfig
    from autoskillit.workspace.session_skills import DefaultSessionSkillManager
    from autoskillit.workspace.skills import SkillInfo, SkillSource

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

    config = AutomationConfig(
        subsets=SubsetsConfig(
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

    from autoskillit.config.settings import AutomationConfig, SubsetsConfig
    from autoskillit.workspace.session_skills import DefaultSessionSkillManager
    from autoskillit.workspace.skills import SkillInfo, SkillSource

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

    config = AutomationConfig(subsets=SubsetsConfig(disabled=["github"]))
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


# REQ-PACK-006: Cook sessions skip default-disabled packs
def test_cook_session_skips_default_disabled_packs(tmp_path: Path) -> None:
    """Cook session excludes default-disabled pack skills when packs.enabled=[]."""
    from unittest.mock import MagicMock

    from autoskillit.config.settings import AutomationConfig
    from autoskillit.workspace.skills import SkillInfo, SkillSource

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
    config = AutomationConfig()  # packs.enabled=[] by default
    mgr = DefaultSessionSkillManager(provider, ephemeral_root=root)
    session_path = mgr.init_session("cook-research", cook_session=True, config=config)

    assert not (session_path / ".claude" / "skills" / "research-skill").exists(), (
        "Default-disabled pack skill must not be in cook session when packs.enabled=[]"
    )


# REQ-PACK-005: headless sessions exclude default-disabled packs
def test_headless_session_excludes_default_disabled_pack_skills(tmp_path: Path) -> None:
    """Skills in 'exp-lens' pack are excluded from headless session when packs.enabled=[]."""
    from unittest.mock import MagicMock

    from autoskillit.config.settings import AutomationConfig
    from autoskillit.workspace.skills import SkillInfo, SkillSource

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
    config = AutomationConfig()  # packs.enabled=[] by default
    mgr = DefaultSessionSkillManager(provider, ephemeral_root=root)
    session_path = mgr.init_session("headless-exp", cook_session=False, config=config)

    assert not (session_path / ".claude" / "skills" / "exp-skill").exists(), (
        "Default-disabled pack skill must not be in headless session when packs.enabled=[]"
    )


def test_init_session_recipe_packs_enables_default_disabled(tmp_path: Path) -> None:
    """recipe_packs param enables default-disabled pack skills for this session."""
    from unittest.mock import MagicMock

    from autoskillit.config.settings import AutomationConfig
    from autoskillit.workspace.skills import SkillInfo, SkillSource

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
    config = AutomationConfig()  # packs.enabled=[] by default
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


# ── Tests: _parse_activate_deps ─────────────────────────────────────────────


class TestParseActivateDeps:
    def test_parses_single_dep(self) -> None:
        from autoskillit.workspace.session_skills import _parse_activate_deps

        content = "---\nname: foo\nactivate_deps: [arch-lens]\n---\nBody"
        assert _parse_activate_deps(content) == ["arch-lens"]

    def test_parses_multiple_deps(self) -> None:
        from autoskillit.workspace.session_skills import _parse_activate_deps

        content = "---\nname: foo\nactivate_deps: [arch-lens, mermaid]\n---\nBody"
        assert _parse_activate_deps(content) == ["arch-lens", "mermaid"]

    def test_empty_deps(self) -> None:
        from autoskillit.workspace.session_skills import _parse_activate_deps

        content = "---\nname: foo\nactivate_deps: []\n---\nBody"
        assert _parse_activate_deps(content) == []

    def test_no_activate_deps_field(self) -> None:
        from autoskillit.workspace.session_skills import _parse_activate_deps

        content = "---\nname: foo\n---\nBody"
        assert _parse_activate_deps(content) == []

    def test_no_frontmatter(self) -> None:
        from autoskillit.workspace.session_skills import _parse_activate_deps

        content = "Just body text"
        assert _parse_activate_deps(content) == []


# ── Tests: activate_tier2 transitive dependency resolution ──────────────────


def _write_skill_md(base: Path, session_id: str, skill_name: str, content: str) -> Path:
    """Helper to write a SKILL.md in the ephemeral session layout."""
    skill_dir = base / session_id / ".claude" / "skills" / skill_name
    skill_dir.mkdir(parents=True, exist_ok=True)
    md = skill_dir / "SKILL.md"
    md.write_text(content)
    return md


def _is_gated(base: Path, session_id: str, skill_name: str) -> bool:
    """Return True if the skill has disable-model-invocation: true."""
    md = base / session_id / ".claude" / "skills" / skill_name / "SKILL.md"
    content = md.read_text()
    return "disable-model-invocation: true" in content


class TestActivateDepsResolution:
    def test_activate_tier2_resolves_pack_deps(self, tmp_path: Path) -> None:
        """Activating a skill with activate_deps: [arch-lens] ungates all arch-lens skills."""
        from unittest.mock import MagicMock

        from autoskillit.core.types import SkillSource
        from autoskillit.workspace.skills import SkillInfo

        session_id = "test-pack-deps"
        gate = "disable-model-invocation: true"
        # Parent skill with pack dep
        _write_skill_md(
            tmp_path,
            session_id,
            "make-plan",
            f"---\nname: make-plan\nactivate_deps: [arch-lens]\n{gate}\n---\n# Plan",
        )
        # Three arch-lens skills
        for name in ["arch-lens-a", "arch-lens-b", "arch-lens-c"]:
            _write_skill_md(
                tmp_path,
                session_id,
                name,
                f"---\nname: {name}\ncategories: [arch-lens]\n{gate}\n---\n# Lens",
            )

        provider = MagicMock()
        resolver = MagicMock()
        provider.resolver = resolver

        def resolve_fn(name: str) -> SkillInfo | None:
            if name.startswith("arch-lens-"):
                return SkillInfo(
                    name=name,
                    source=SkillSource.BUNDLED_EXTENDED,
                    path=tmp_path / session_id / ".claude" / "skills" / name / "SKILL.md",
                    categories=frozenset({"arch-lens"}),
                )
            return SkillInfo(
                name=name,
                source=SkillSource.BUNDLED_EXTENDED,
                path=tmp_path / session_id / ".claude" / "skills" / name / "SKILL.md",
                categories=frozenset(),
            )

        resolver.resolve.side_effect = resolve_fn

        mgr = DefaultSessionSkillManager(provider, ephemeral_root=tmp_path)
        result = mgr.activate_tier2(session_id, "make-plan")
        assert result is True
        assert not _is_gated(tmp_path, session_id, "make-plan")
        for name in ["arch-lens-a", "arch-lens-b", "arch-lens-c"]:
            assert not _is_gated(tmp_path, session_id, name), f"{name} should be ungated"

    def test_activate_tier2_resolves_individual_skill_dep(self, tmp_path: Path) -> None:
        """Activating a skill with activate_deps: [mermaid] ungates mermaid specifically."""
        from unittest.mock import MagicMock

        session_id = "test-individual-dep"
        gate = "disable-model-invocation: true"
        _write_skill_md(
            tmp_path,
            session_id,
            "parent-skill",
            f"---\nname: parent-skill\nactivate_deps: [mermaid]\n{gate}\n---\n# Parent",
        )
        _write_skill_md(
            tmp_path,
            session_id,
            "mermaid",
            "---\nname: mermaid\ndisable-model-invocation: true\n---\n# Mermaid",
        )

        provider = MagicMock()
        provider.resolver.resolve.return_value = None

        mgr = DefaultSessionSkillManager(provider, ephemeral_root=tmp_path)
        result = mgr.activate_tier2(session_id, "parent-skill")
        assert result is True
        assert not _is_gated(tmp_path, session_id, "mermaid")

    def test_activate_tier2_resolves_two_level_transitive(self, tmp_path: Path) -> None:
        """make-plan -> arch-lens-* -> mermaid: all three levels get ungated."""
        from unittest.mock import MagicMock

        from autoskillit.core.types import SkillSource
        from autoskillit.workspace.skills import SkillInfo

        session_id = "test-two-level"
        gate = "disable-model-invocation: true"
        _write_skill_md(
            tmp_path,
            session_id,
            "make-plan",
            f"---\nname: make-plan\nactivate_deps: [arch-lens]\n{gate}\n---\n# Plan",
        )
        _write_skill_md(
            tmp_path,
            session_id,
            "arch-lens-x",
            (
                f"---\nname: arch-lens-x\ncategories: [arch-lens]\n"
                f"activate_deps: [mermaid]\n{gate}\n---\n# Lens"
            ),
        )
        _write_skill_md(
            tmp_path,
            session_id,
            "mermaid",
            "---\nname: mermaid\ndisable-model-invocation: true\n---\n# Mermaid",
        )

        provider = MagicMock()
        resolver = MagicMock()
        provider.resolver = resolver

        def resolve_fn(name: str) -> SkillInfo | None:
            cats = frozenset({"arch-lens"}) if name.startswith("arch-lens-") else frozenset()
            return SkillInfo(
                name=name,
                source=SkillSource.BUNDLED_EXTENDED,
                path=tmp_path / session_id / ".claude" / "skills" / name / "SKILL.md",
                categories=cats,
            )

        resolver.resolve.side_effect = resolve_fn

        mgr = DefaultSessionSkillManager(provider, ephemeral_root=tmp_path)
        mgr.activate_tier2(session_id, "make-plan")
        assert not _is_gated(tmp_path, session_id, "make-plan")
        assert not _is_gated(tmp_path, session_id, "arch-lens-x")
        assert not _is_gated(tmp_path, session_id, "mermaid")

    def test_activate_tier2_handles_circular_deps(self, tmp_path: Path) -> None:
        """Circular activate_deps do not cause infinite recursion."""
        from unittest.mock import MagicMock

        session_id = "test-circular"
        gate = "disable-model-invocation: true"
        _write_skill_md(
            tmp_path,
            session_id,
            "skill-a",
            f"---\nname: skill-a\nactivate_deps: [skill-b]\n{gate}\n---\n# A",
        )
        _write_skill_md(
            tmp_path,
            session_id,
            "skill-b",
            f"---\nname: skill-b\nactivate_deps: [skill-a]\n{gate}\n---\n# B",
        )

        provider = MagicMock()
        provider.resolver.resolve.return_value = None

        mgr = DefaultSessionSkillManager(provider, ephemeral_root=tmp_path)
        result = mgr.activate_tier2(session_id, "skill-a")
        assert result is True
        assert not _is_gated(tmp_path, session_id, "skill-a")
        assert not _is_gated(tmp_path, session_id, "skill-b")

    def test_tier3_target_activates_tier2_deps(self, tmp_path: Path) -> None:
        """A tier3 (ungated) skill's activate_deps still triggers tier2 dependency ungating."""
        from unittest.mock import MagicMock

        session_id = "test-tier3-deps"
        # tier3 parent (not gated)
        _write_skill_md(
            tmp_path,
            session_id,
            "open-pr",
            "---\nname: open-pr\nactivate_deps: [mermaid]\n---\n# Open PR",
        )
        # tier2 dep (gated)
        _write_skill_md(
            tmp_path,
            session_id,
            "mermaid",
            "---\nname: mermaid\ndisable-model-invocation: true\n---\n# Mermaid",
        )

        provider = MagicMock()
        provider.resolver.resolve.return_value = None

        mgr = DefaultSessionSkillManager(provider, ephemeral_root=tmp_path)
        result = mgr.activate_tier2(session_id, "open-pr")
        assert result is True
        assert not _is_gated(tmp_path, session_id, "mermaid")

    def test_pack_dep_absent_skills_noop(self, tmp_path: Path) -> None:
        """Pack dep referencing skills not in ephemeral dir does not error."""
        from unittest.mock import MagicMock

        session_id = "test-absent-pack"
        gate = "disable-model-invocation: true"
        _write_skill_md(
            tmp_path,
            session_id,
            "parent-skill",
            f"---\nname: parent-skill\nactivate_deps: [exp-lens]\n{gate}\n---\n# Parent",
        )

        provider = MagicMock()
        provider.resolver.resolve.return_value = None

        mgr = DefaultSessionSkillManager(provider, ephemeral_root=tmp_path)
        result = mgr.activate_tier2(session_id, "parent-skill")
        assert result is True
        assert not _is_gated(tmp_path, session_id, "parent-skill")
