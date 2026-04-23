"""Tests for project-local skill override detection and enforcement (T-OVR-001..011)."""

from __future__ import annotations

import pytest

from autoskillit.core.types import PACK_REGISTRY

pytestmark = [pytest.mark.layer("workspace"), pytest.mark.small]

# Tags for packs that are disabled by default (e.g. research, exp-lens).
# Shared by T-OVR-014 and T-OVR-017 to avoid duplication.
_DEFAULT_DISABLED_TAGS: frozenset[str] = frozenset(
    tag for tag, pack_def in PACK_REGISTRY.items() if not pack_def.default_enabled
)

# ---------------------------------------------------------------------------
# T-OVR-001..006: detect_project_local_overrides() — pure detection function
# ---------------------------------------------------------------------------


def test_detect_project_local_overrides_empty(tmp_path):
    """T-OVR-001: Returns empty frozenset when no override dirs exist."""
    from autoskillit.workspace.skills import detect_project_local_overrides

    result = detect_project_local_overrides(tmp_path)
    assert result == frozenset()


def test_detect_project_local_overrides_claude_skills(tmp_path):
    """T-OVR-002: Detects skill in .claude/skills/<name>/SKILL.md."""
    from autoskillit.workspace.skills import detect_project_local_overrides

    skill_dir = tmp_path / ".claude" / "skills" / "review-pr"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("# review-pr")
    result = detect_project_local_overrides(tmp_path)
    assert result == frozenset({"review-pr"})


def test_detect_project_local_overrides_autoskillit_skills(tmp_path):
    """T-OVR-003: Detects skill in .autoskillit/skills/<name>/SKILL.md."""
    from autoskillit.workspace.skills import detect_project_local_overrides

    skill_dir = tmp_path / ".autoskillit" / "skills" / "open-pr"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("# open-pr")
    result = detect_project_local_overrides(tmp_path)
    assert result == frozenset({"open-pr"})


def test_detect_project_local_overrides_union(tmp_path):
    """T-OVR-004: Returns union from both .claude/skills/ and .autoskillit/skills/."""
    from autoskillit.workspace.skills import detect_project_local_overrides

    for subdir, name in [
        (".claude/skills/review-pr", "review-pr"),
        (".autoskillit/skills/open-pr", "open-pr"),
    ]:
        d = tmp_path / subdir
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text("# skill")
    result = detect_project_local_overrides(tmp_path)
    assert result == frozenset({"review-pr", "open-pr"})


def test_detect_project_local_overrides_ignores_missing_skill_md(tmp_path):
    """T-OVR-005: Directories without SKILL.md are ignored."""
    from autoskillit.workspace.skills import detect_project_local_overrides

    (tmp_path / ".claude" / "skills" / "review-pr").mkdir(parents=True)
    result = detect_project_local_overrides(tmp_path)
    assert result == frozenset()


def test_detect_project_local_overrides_missing_dirs_no_crash(tmp_path):
    """T-OVR-006: Missing parent directories do not raise."""
    from autoskillit.workspace.skills import detect_project_local_overrides

    result = detect_project_local_overrides(tmp_path / "nonexistent")
    assert result == frozenset()


# ---------------------------------------------------------------------------
# T-OVR-007..011: init_session() — project_dir override filtering
# ---------------------------------------------------------------------------


def test_init_session_no_override_when_project_dir_none(tmp_path):
    """T-OVR-007: init_session() with project_dir=None performs no override filtering."""
    from autoskillit.workspace.session_skills import (
        DefaultSessionSkillManager,
        SkillsDirectoryProvider,
    )

    provider = SkillsDirectoryProvider()
    mgr = DefaultSessionSkillManager(provider, tmp_path / "ephemeral")
    skills_dir = mgr.init_session("sess-001", project_dir=None)
    assert (skills_dir / ".claude" / "skills" / "investigate" / "SKILL.md").exists()


def test_init_session_excludes_overridden_skill(tmp_path):
    """T-OVR-008: init_session() excludes bundled skill when project-local override exists."""
    from autoskillit.workspace.session_skills import (
        DefaultSessionSkillManager,
        SkillsDirectoryProvider,
    )

    project_dir = tmp_path / "project"
    project_dir.mkdir()
    override = project_dir / ".claude" / "skills" / "investigate"
    override.mkdir(parents=True)
    (override / "SKILL.md").write_text("# custom investigate")
    mgr = DefaultSessionSkillManager(SkillsDirectoryProvider(), tmp_path / "ephemeral")
    skills_dir = mgr.init_session("sess-002", project_dir=project_dir)
    assert not (skills_dir / ".claude" / "skills" / "investigate" / "SKILL.md").exists()


def test_init_session_includes_non_overridden_skills(tmp_path):
    """T-OVR-009: Non-overridden skills are still included."""
    from autoskillit.workspace.session_skills import (
        DefaultSessionSkillManager,
        SkillsDirectoryProvider,
    )

    project_dir = tmp_path / "project"
    project_dir.mkdir()
    # Override "investigate" only
    override = project_dir / ".claude" / "skills" / "investigate"
    override.mkdir(parents=True)
    (override / "SKILL.md").write_text("# custom")
    mgr = DefaultSessionSkillManager(SkillsDirectoryProvider(), tmp_path / "ephemeral")
    skills_dir = mgr.init_session("sess-003", project_dir=project_dir)
    # "make-plan" must still be present
    assert (skills_dir / ".claude" / "skills" / "make-plan" / "SKILL.md").exists()


def test_init_session_subset_and_override_compose(tmp_path):
    """T-OVR-010: Subset disable and override compose independently."""
    from autoskillit.workspace.session_skills import (
        DefaultSessionSkillManager,
        SkillsDirectoryProvider,
    )
    from tests._helpers import make_subsetsconfig, make_test_config

    project_dir = tmp_path / "project"
    project_dir.mkdir()
    # Project-local override for "review-pr"
    override = project_dir / ".claude" / "skills" / "review-pr"
    override.mkdir(parents=True)
    (override / "SKILL.md").write_text("# custom")
    # Config disables "github" subset (which covers open-pr)
    config = make_test_config(subsets=make_subsetsconfig(disabled=["github"]))
    mgr = DefaultSessionSkillManager(SkillsDirectoryProvider(), tmp_path / "ephemeral")
    skills_dir = mgr.init_session("sess-004", config=config, project_dir=project_dir)
    # "open-pr" absent due to subset; "review-pr" absent due to override
    assert not (skills_dir / ".claude" / "skills" / "review-pr" / "SKILL.md").exists()
    assert not (skills_dir / ".claude" / "skills" / "open-pr" / "SKILL.md").exists()


def test_init_session_logs_override_skip(tmp_path):
    """T-OVR-011: Debug log emitted for each overridden skill skipped."""
    import structlog.testing

    from autoskillit.workspace.session_skills import (
        DefaultSessionSkillManager,
        SkillsDirectoryProvider,
    )

    project_dir = tmp_path / "project"
    project_dir.mkdir()
    override = project_dir / ".claude" / "skills" / "investigate"
    override.mkdir(parents=True)
    (override / "SKILL.md").write_text("# custom")
    mgr = DefaultSessionSkillManager(SkillsDirectoryProvider(), tmp_path / "ephemeral")
    with structlog.testing.capture_logs() as logs:
        mgr.init_session("sess-005", project_dir=project_dir)
    skip_events = [e for e in logs if e.get("event") == "init_session_override_skip"]
    assert any(e.get("skill") == "investigate" for e in skip_events)


def test_init_session_cook_session_excludes_project_local_overrides(tmp_path):
    """T-OVR-012: cook_session=True excludes skills with project-local overrides.

    Project-local overrides are already visible via CWD auto-discovery (Channel 3),
    so the ephemeral dir (Channel 2) must NOT contain them — regardless of session mode.
    """
    from autoskillit.workspace.session_skills import (
        DefaultSessionSkillManager,
        SkillsDirectoryProvider,
    )

    project_dir = tmp_path / "project"
    project_dir.mkdir()
    override = project_dir / ".claude" / "skills" / "investigate"
    override.mkdir(parents=True)
    (override / "SKILL.md").write_text("# custom investigate")

    mgr = DefaultSessionSkillManager(SkillsDirectoryProvider(), tmp_path / "ephemeral")
    skills_dir = mgr.init_session("sess-cook", cook_session=True, project_dir=project_dir)
    assert not (skills_dir / ".claude" / "skills" / "investigate" / "SKILL.md").exists(), (
        "cook_session=True must NOT include 'investigate' when a project-local "
        "override exists — CWD auto-discovery already provides it"
    )


def test_init_session_cook_session_ignores_disabled_subsets(tmp_path):
    """T-OVR-013: cook_session=True includes subset-disabled skills."""
    from autoskillit.workspace.session_skills import (
        DefaultSessionSkillManager,
        SkillsDirectoryProvider,
    )
    from tests._helpers import make_subsetsconfig, make_test_config

    config = make_test_config(subsets=make_subsetsconfig(disabled=["github"]))
    mgr = DefaultSessionSkillManager(SkillsDirectoryProvider(), tmp_path / "ephemeral")
    skills_dir = mgr.init_session("sess-cook2", cook_session=True, config=config)
    assert (skills_dir / ".claude" / "skills" / "compose-pr" / "SKILL.md").exists(), (
        "cook_session=True must include 'compose-pr' even when 'github' subset is disabled"
    )


def test_init_session_cook_full_skill_set_invariant(tmp_path):
    """T-OVR-014: cook_session=True yields all BUNDLED_EXTENDED skills minus
    project-local overrides and default-disabled pack skills — but never BUNDLED
    (Tier 1) skills, which are already served by --plugin-dir.

    The cook bypasses explicit subset-disable filtering but NOT channel deduplication
    and NOT default pack gating.
    """
    from autoskillit.core.types import SkillSource
    from autoskillit.workspace.session_skills import (
        DefaultSessionSkillManager,
        SkillsDirectoryProvider,
    )
    from autoskillit.workspace.skills import DefaultSkillResolver
    from tests._helpers import make_subsetsconfig, make_test_config

    # Override exactly one extended skill to test override exclusion
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    override = project_dir / ".claude" / "skills" / "investigate"
    override.mkdir(parents=True)
    (override / "SKILL.md").write_text("# override")

    # Config disables all known categories — cook should bypass this
    config = make_test_config(
        subsets=make_subsetsconfig(disabled=["github", "audit", "arch-lens", "ci"]),
        features={"franchise": True},
    )
    mgr = DefaultSessionSkillManager(SkillsDirectoryProvider(), tmp_path / "ephemeral")
    skills_dir = mgr.init_session(
        "sess-invariant", cook_session=True, config=config, project_dir=project_dir
    )

    resolver = DefaultSkillResolver()
    all_skills = resolver.list_all()
    # Expected: all BUNDLED_EXTENDED skills except project-local overrides and
    # skills whose categories are entirely in default-disabled packs.
    expected_names = {
        s.name
        for s in all_skills
        if s.source != SkillSource.BUNDLED and not (s.categories & _DEFAULT_DISABLED_TAGS)
    } - {"investigate"}
    skills_base = skills_dir / ".claude" / "skills"
    actual_names = {d.name for d in skills_base.iterdir() if d.is_dir()}
    assert actual_names == expected_names, (
        f"cook_session=True ephemeral dir mismatch.\n"
        f"  Missing: {sorted(expected_names - actual_names)}\n"
        f"  Extra:   {sorted(actual_names - expected_names)}"
    )


# ---------------------------------------------------------------------------
# T-OVR-015..017: Channel-aware exclusion — Tier 1 deduplication
# ---------------------------------------------------------------------------


def test_cook_session_excludes_tier1_from_ephemeral_dir(tmp_path):
    """T-OVR-015: init_session(cook_session=True) must NOT write BUNDLED skills
    to the ephemeral dir — they are already served by --plugin-dir (Channel 1)."""
    from autoskillit.core.types import SkillSource
    from autoskillit.workspace.session_skills import (
        DefaultSessionSkillManager,
        SkillsDirectoryProvider,
    )
    from autoskillit.workspace.skills import DefaultSkillResolver

    mgr = DefaultSessionSkillManager(SkillsDirectoryProvider(), tmp_path / "ephemeral")
    skills_dir = mgr.init_session("sess-tier1", cook_session=True)

    resolver = DefaultSkillResolver()
    tier1_names = {s.name for s in resolver.list_all() if s.source == SkillSource.BUNDLED}
    skills_base = skills_dir / ".claude" / "skills"
    actual_names = {d.name for d in skills_base.iterdir() if d.is_dir()}
    overlap = tier1_names & actual_names
    assert not overlap, (
        f"BUNDLED (Tier 1) skills must NOT appear in ephemeral dir — "
        f"already served by --plugin-dir. Found: {sorted(overlap)}"
    )


def test_cook_session_retains_non_colliding_extended_skills(tmp_path):
    """T-OVR-017: Regression guard — cook_session=True still writes all
    BUNDLED_EXTENDED skills that do NOT have project-local overrides and are
    not in default-disabled packs."""
    from autoskillit.core.types import SkillSource
    from autoskillit.workspace.session_skills import (
        DefaultSessionSkillManager,
        SkillsDirectoryProvider,
    )
    from autoskillit.workspace.skills import DefaultSkillResolver
    from tests._helpers import make_test_config

    # Override exactly one extended skill
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    override = project_dir / ".claude" / "skills" / "investigate"
    override.mkdir(parents=True)
    (override / "SKILL.md").write_text("# custom")

    config = make_test_config(features={"franchise": True})
    mgr = DefaultSessionSkillManager(SkillsDirectoryProvider(), tmp_path / "ephemeral")
    skills_dir = mgr.init_session(
        "sess-retain", cook_session=True, config=config, project_dir=project_dir
    )

    resolver = DefaultSkillResolver()
    expected = {
        s.name
        for s in resolver.list_all()
        if s.source != SkillSource.BUNDLED and not (s.categories & _DEFAULT_DISABLED_TAGS)
    } - {"investigate"}
    skills_base = skills_dir / ".claude" / "skills"
    actual = {d.name for d in skills_base.iterdir() if d.is_dir()}
    missing = expected - actual
    assert not missing, (
        f"cook_session=True must include all non-colliding extended skills. "
        f"Missing: {sorted(missing)}"
    )
