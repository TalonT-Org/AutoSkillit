"""Phase 2 tests: session_skills module — allow_only filter and compute_skill_closure."""

from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit.core import FEATURE_REGISTRY
from autoskillit.workspace.session_skills import (
    DefaultSessionSkillManager,
    SkillsDirectoryProvider,
)

pytestmark = [pytest.mark.layer("workspace"), pytest.mark.small]

_FEATURE_SKILL_CATEGORY_PARAMS = [
    pytest.param(feat_name, cat, id=f"{feat_name}-{cat}")
    for feat_name, feat_def in FEATURE_REGISTRY.items()
    for cat in feat_def.skill_categories
]


def _make_synthetic_provider(
    tmp_path: Path,
    skills: dict[str, dict],
):
    """Build a mocked SkillsDirectoryProvider serving synthetic SKILL.md files.

    skills: mapping of name -> {"deps": [...], "categories": [...]}
    """
    from unittest.mock import MagicMock

    from autoskillit.workspace.skills import SkillInfo, SkillSource

    tmp_path.mkdir(parents=True, exist_ok=True)
    skill_infos: list[SkillInfo] = []
    for name, spec in skills.items():
        skill_dir = tmp_path / name
        skill_dir.mkdir()
        deps = spec.get("deps", [])
        categories = spec.get("categories", [])
        fm_lines = [f"name: {name}"]
        if categories:
            fm_lines.append(f"categories: [{', '.join(categories)}]")
        if deps:
            fm_lines.append(f"activate_deps: [{', '.join(deps)}]")
        content = "---\n" + "\n".join(fm_lines) + "\n---\nbody\n"
        (skill_dir / "SKILL.md").write_text(content)
        skill_infos.append(
            SkillInfo(
                name=name,
                source=SkillSource.BUNDLED_EXTENDED,
                path=skill_dir / "SKILL.md",
                categories=frozenset(categories),
            )
        )

    by_name = {info.name: info for info in skill_infos}

    provider = MagicMock()
    provider.list_skills.return_value = skill_infos
    provider.resolver = MagicMock()
    provider.resolver.resolve.side_effect = lambda n: by_name.get(n)

    def _get_content(name: str, *, gated: bool = True) -> str:
        if name not in by_name:
            raise FileNotFoundError(name)
        return by_name[name].path.read_text()

    provider.get_skill_content.side_effect = _get_content
    return provider


class TestInitSessionAllowOnly:
    """Tests for the ``allow_only`` filter on ``DefaultSessionSkillManager.init_session``."""

    def test_allow_only_writes_only_named_skill(self, tmp_path: Path) -> None:
        provider = _make_synthetic_provider(
            tmp_path / "skills",
            {"alpha": {}, "beta": {}, "gamma": {}},
        )
        root = tmp_path / "sessions"
        root.mkdir()
        mgr = DefaultSessionSkillManager(provider, ephemeral_root=root)
        session_path = mgr.init_session("s1", allow_only=frozenset({"alpha"}))

        skills_base = session_path / ".claude" / "skills"
        assert (skills_base / "alpha" / "SKILL.md").exists()
        assert not (skills_base / "beta").exists()
        assert not (skills_base / "gamma").exists()

    def test_allow_only_empty_writes_no_skills(self, tmp_path: Path) -> None:
        provider = _make_synthetic_provider(
            tmp_path / "skills",
            {"alpha": {}, "beta": {}},
        )
        root = tmp_path / "sessions"
        root.mkdir()
        mgr = DefaultSessionSkillManager(provider, ephemeral_root=root)
        session_path = mgr.init_session("s2", allow_only=frozenset())

        skills_base = session_path / ".claude" / "skills"
        assert skills_base.exists()
        assert list(skills_base.glob("*/SKILL.md")) == []

    def test_allow_only_none_preserves_full_injection(self, tmp_path: Path) -> None:
        provider = _make_synthetic_provider(
            tmp_path / "skills",
            {"alpha": {}, "beta": {}, "gamma": {}},
        )
        root = tmp_path / "sessions"
        root.mkdir()
        mgr = DefaultSessionSkillManager(provider, ephemeral_root=root)
        session_path = mgr.init_session("s3", allow_only=None)

        skills_base = session_path / ".claude" / "skills"
        names = {p.parent.name for p in skills_base.glob("*/SKILL.md")}
        assert names == {"alpha", "beta", "gamma"}

    def test_allow_only_does_not_override_explicit_subsets_disabled(self, tmp_path: Path) -> None:
        from tests._helpers import make_subsetsconfig, make_test_config

        provider = _make_synthetic_provider(
            tmp_path / "skills",
            {"alpha": {"categories": ["github"]}, "beta": {}},
        )
        root = tmp_path / "sessions"
        root.mkdir()
        mgr = DefaultSessionSkillManager(provider, ephemeral_root=root)
        config = make_test_config(subsets=make_subsetsconfig(disabled=["github"]))
        session_path = mgr.init_session(
            "s4", config=config, allow_only=frozenset({"alpha", "beta"})
        )

        skills_base = session_path / ".claude" / "skills"
        assert not (skills_base / "alpha").exists()  # disabled wins
        assert (skills_base / "beta" / "SKILL.md").exists()

    def test_allow_only_skips_project_local_overrides(self, tmp_path: Path) -> None:
        provider = _make_synthetic_provider(
            tmp_path / "skills",
            {"alpha": {}, "beta": {}},
        )
        # Simulate a project-local override of "alpha"
        project_dir = tmp_path / "project"
        local_skill = project_dir / ".claude" / "skills" / "alpha"
        local_skill.mkdir(parents=True)
        (local_skill / "SKILL.md").write_text("---\nname: alpha\n---\nlocal\n")

        root = tmp_path / "sessions"
        root.mkdir()
        mgr = DefaultSessionSkillManager(provider, ephemeral_root=root)
        session_path = mgr.init_session(
            "s5",
            project_dir=project_dir,
            allow_only=frozenset({"alpha", "beta"}),
        )

        skills_base = session_path / ".claude" / "skills"
        # alpha is suppressed by channel-dedup (project-local override exists)
        assert not (skills_base / "alpha").exists()
        assert (skills_base / "beta" / "SKILL.md").exists()

    def test_allow_only_logs_debug_skip(self, tmp_path: Path) -> None:
        import structlog

        provider = _make_synthetic_provider(
            tmp_path / "skills",
            {"alpha": {}, "beta": {}, "gamma": {}},
        )
        root = tmp_path / "sessions"
        root.mkdir()
        mgr = DefaultSessionSkillManager(provider, ephemeral_root=root)

        with structlog.testing.capture_logs() as cap_logs:
            mgr.init_session("s6", allow_only=frozenset({"alpha"}))

        skipped = {
            entry["skill"]
            for entry in cap_logs
            if entry.get("event") == "init_session_allow_only_skip"
        }
        assert skipped == {"beta", "gamma"}

    def test_allow_only_overrides_feature_gate_for_explicitly_requested_skills(
        self, tmp_path: Path
    ) -> None:
        from tests._helpers import make_test_config

        provider = _make_synthetic_provider(
            tmp_path / "skills",
            {"planner-skill": {"categories": ["planner"]}},
        )
        root = tmp_path / "sessions"
        root.mkdir()
        mgr = DefaultSessionSkillManager(provider, ephemeral_root=root)
        config = make_test_config(features={}, experimental_enabled=False)
        session_path = mgr.init_session(
            "s-feat-bypass",
            allow_only=frozenset({"planner-skill"}),
            config=config,
        )

        skills_base = session_path / ".claude" / "skills"
        assert (skills_base / "planner-skill" / "SKILL.md").exists(), (
            "planner-skill must be written when allow_only explicitly requests it, "
            "even though the planner feature gate is off (experimental_enabled=False)"
        )

    def test_allow_only_nonempty_but_zero_skills_raises(self, tmp_path: Path) -> None:
        provider = _make_synthetic_provider(
            tmp_path / "skills",
            {"alpha": {}, "beta": {}},
        )
        root = tmp_path / "sessions"
        root.mkdir()
        mgr = DefaultSessionSkillManager(provider, ephemeral_root=root)
        with pytest.raises(RuntimeError, match="allow_only") as exc_info:
            mgr.init_session("s-zero", allow_only=frozenset({"ghost-skill"}))
        assert "zero skills" in str(exc_info.value)

    @pytest.mark.parametrize("feature_name,category", _FEATURE_SKILL_CATEGORY_PARAMS)
    def test_allow_only_overrides_all_feature_registry_entries(
        self, tmp_path: Path, feature_name: str, category: str
    ) -> None:
        from tests._helpers import make_test_config

        provider = _make_synthetic_provider(
            tmp_path / "skills",
            {"test-skill": {"categories": [category]}},
        )
        root = tmp_path / "sessions"
        root.mkdir()
        mgr = DefaultSessionSkillManager(provider, ephemeral_root=root)
        config = make_test_config(features={feature_name: False}, experimental_enabled=False)
        session_path = mgr.init_session(
            "s-feat-registry",
            allow_only=frozenset({"test-skill"}),
            config=config,
        )

        skills_base = session_path / ".claude" / "skills"
        assert (skills_base / "test-skill" / "SKILL.md").exists(), (
            f"test-skill (category={category!r}) must be written when allow_only explicitly "
            f"requests it, even though feature {feature_name!r} is disabled"
        )


_CROSS_AXIS_PARAMS = [
    # (allow_only, feature_enabled, subsets_disabled, cook_session, expected_target_written)
    # allow_only=None cases
    pytest.param(None, True, [], False, True, id="ao-none_feat-on_disabled-no_cook-no"),
    pytest.param(None, True, ["planner"], False, False, id="ao-none_feat-on_disabled-yes_cook-no"),
    pytest.param(None, False, [], False, False, id="ao-none_feat-off_disabled-no_cook-no"),
    pytest.param(
        None, False, ["planner"], False, False, id="ao-none_feat-off_disabled-yes_cook-no"
    ),
    pytest.param(None, True, [], True, True, id="ao-none_feat-on_disabled-no_cook-yes"),
    pytest.param(None, True, ["planner"], True, True, id="ao-none_feat-on_disabled-yes_cook-yes"),
    pytest.param(None, False, [], True, True, id="ao-none_feat-off_disabled-no_cook-yes"),
    pytest.param(
        None, False, ["planner"], True, True, id="ao-none_feat-off_disabled-yes_cook-yes"
    ),
    # allow_only=frozenset({"target","sibling"}) cases
    pytest.param(
        frozenset({"target", "sibling"}),
        True,
        [],
        False,
        True,
        id="ao-set_feat-on_disabled-no_cook-no",
    ),
    pytest.param(
        frozenset({"target", "sibling"}),
        True,
        ["planner"],
        False,
        False,
        id="ao-set_feat-on_disabled-yes_cook-no",
    ),
    pytest.param(
        frozenset({"target", "sibling"}),
        False,
        [],
        False,
        True,
        id="ao-set_feat-off_disabled-no_cook-no",  # THE BUG CASE
    ),
    pytest.param(
        frozenset({"target", "sibling"}),
        False,
        ["planner"],
        False,
        False,
        id="ao-set_feat-off_disabled-yes_cook-no",
    ),
    pytest.param(
        frozenset({"target", "sibling"}),
        True,
        [],
        True,
        True,
        id="ao-set_feat-on_disabled-no_cook-yes",
    ),
    pytest.param(
        frozenset({"target", "sibling"}),
        True,
        ["planner"],
        True,
        True,
        id="ao-set_feat-on_disabled-yes_cook-yes",
    ),
    pytest.param(
        frozenset({"target", "sibling"}),
        False,
        [],
        True,
        True,
        id="ao-set_feat-off_disabled-no_cook-yes",
    ),
    pytest.param(
        frozenset({"target", "sibling"}),
        False,
        ["planner"],
        True,
        True,
        id="ao-set_feat-off_disabled-yes_cook-yes",
    ),
]


class TestCrossAxisGatingMatrix:
    """Cross-axis parametrized test matrix: allow_only × feature_gate × subsets.disabled × cook."""

    @pytest.mark.parametrize(
        "allow_only,feature_enabled,subsets_disabled,cook_session,expected_target_written",
        _CROSS_AXIS_PARAMS,
    )
    def test_cross_axis_gating_matrix(
        self,
        tmp_path: Path,
        allow_only: frozenset[str] | None,
        feature_enabled: bool,
        subsets_disabled: list[str],
        cook_session: bool,
        expected_target_written: bool,
    ) -> None:
        from tests._helpers import make_subsetsconfig, make_test_config

        provider = _make_synthetic_provider(
            tmp_path / "skills",
            {"target": {"categories": ["planner"]}, "sibling": {}},
        )
        root = tmp_path / "sessions"
        root.mkdir()
        mgr = DefaultSessionSkillManager(provider, ephemeral_root=root)

        features = {"planner": True} if feature_enabled else {}
        config = make_test_config(
            features=features,
            experimental_enabled=False,
            subsets=make_subsetsconfig(disabled=subsets_disabled),
        )

        session_path = mgr.init_session(
            "s-matrix",
            config=config,
            allow_only=allow_only,
            cook_session=cook_session,
        )

        skills_base = session_path / ".claude" / "skills"
        target_written = (skills_base / "target").exists()
        assert target_written == expected_target_written, (
            f"target (category=planner) written={target_written!r}, "
            f"expected={expected_target_written!r} for "
            f"allow_only={allow_only!r}, feature_enabled={feature_enabled!r}, "
            f"subsets_disabled={subsets_disabled!r}, cook_session={cook_session!r}"
        )


class TestComputeSkillClosure:
    """Tests for ``compute_skill_closure`` and its top-level helper."""

    def test_closure_standalone_returns_only_self(self, tmp_path: Path) -> None:
        from autoskillit.workspace.session_skills import compute_skill_closure

        provider = _make_synthetic_provider(tmp_path, {"lone": {}})
        assert compute_skill_closure("lone", provider) == frozenset({"lone"})

    def test_closure_pack_dep_expands_to_pack_members(self) -> None:
        from autoskillit.workspace.session_skills import compute_skill_closure

        provider = SkillsDirectoryProvider()
        closure = compute_skill_closure("make-plan", provider)
        assert "make-plan" in closure
        assert "mermaid" in closure  # transitive via arch-lens-* deps
        arch_members = {n for n in closure if n.startswith("arch-lens-")}
        assert len(arch_members) >= 1

    def test_closure_individual_skill_dep(self, tmp_path: Path) -> None:
        from autoskillit.workspace.session_skills import compute_skill_closure

        provider = _make_synthetic_provider(
            tmp_path,
            {"target": {"deps": ["other"]}, "other": {}},
        )
        assert compute_skill_closure("target", provider) == frozenset({"target", "other"})

    def test_closure_two_level_transitive(self, tmp_path: Path) -> None:
        from autoskillit.workspace.session_skills import compute_skill_closure

        provider = _make_synthetic_provider(
            tmp_path,
            {"a": {"deps": ["b"]}, "b": {"deps": ["c"]}, "c": {}},
        )
        assert compute_skill_closure("a", provider) == frozenset({"a", "b", "c"})

    def test_closure_cycle_safe(self, tmp_path: Path) -> None:
        from autoskillit.workspace.session_skills import compute_skill_closure

        provider = _make_synthetic_provider(
            tmp_path,
            {"a": {"deps": ["b"]}, "b": {"deps": ["a"]}},
        )
        assert compute_skill_closure("a", provider) == frozenset({"a", "b"})

    def test_closure_unknown_dep_silently_ignored(self, tmp_path: Path) -> None:
        from autoskillit.workspace.session_skills import compute_skill_closure

        provider = _make_synthetic_provider(
            tmp_path,
            {"target": {"deps": ["ghost"]}},
        )
        assert compute_skill_closure("target", provider) == frozenset({"target"})

    def test_closure_unknown_target_returns_empty_frozenset(self, tmp_path: Path) -> None:
        from autoskillit.workspace.session_skills import compute_skill_closure

        provider = _make_synthetic_provider(tmp_path, {"alpha": {}})
        assert compute_skill_closure("nonexistent", provider) == frozenset()

    def test_closure_pack_dep_with_no_members_returns_only_target(self, tmp_path: Path) -> None:
        from autoskillit.workspace.session_skills import compute_skill_closure

        # 'audit' is a real PACK_REGISTRY key, but no synthetic skills declare it.
        provider = _make_synthetic_provider(
            tmp_path,
            {"target": {"deps": ["audit"]}},
        )
        assert compute_skill_closure("target", provider) == frozenset({"target"})
