"""Phase 2 tests: session_skills module — activate_deps resolution."""

from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit.workspace.session_skills import DefaultSessionSkillManager

pytestmark = [pytest.mark.layer("workspace"), pytest.mark.small]


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


# ── Tests: activate_skill_deps transitive dependency resolution ──────────────────


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
    def test_activate_skill_deps_resolves_pack_deps(self, tmp_path: Path) -> None:
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
        provider.list_skills.return_value = []

        mgr = DefaultSessionSkillManager(provider, ephemeral_root=tmp_path)
        result = mgr.activate_skill_deps(session_id, "make-plan")
        assert result is True
        assert not _is_gated(tmp_path, session_id, "make-plan")
        for name in ["arch-lens-a", "arch-lens-b", "arch-lens-c"]:
            assert not _is_gated(tmp_path, session_id, name), f"{name} should be ungated"

    def test_activate_skill_deps_resolves_individual_skill_dep(self, tmp_path: Path) -> None:
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
        provider.list_skills.return_value = []

        mgr = DefaultSessionSkillManager(provider, ephemeral_root=tmp_path)
        result = mgr.activate_skill_deps(session_id, "parent-skill")
        assert result is True
        assert not _is_gated(tmp_path, session_id, "mermaid")

    def test_activate_skill_deps_resolves_two_level_transitive(self, tmp_path: Path) -> None:
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
        provider.list_skills.return_value = []

        mgr = DefaultSessionSkillManager(provider, ephemeral_root=tmp_path)
        mgr.activate_skill_deps(session_id, "make-plan")
        assert not _is_gated(tmp_path, session_id, "make-plan")
        assert not _is_gated(tmp_path, session_id, "arch-lens-x")
        assert not _is_gated(tmp_path, session_id, "mermaid")

    def test_activate_skill_deps_handles_circular_deps(self, tmp_path: Path) -> None:
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
        result = mgr.activate_skill_deps(session_id, "skill-a")
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
        result = mgr.activate_skill_deps(session_id, "open-pr")
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
        result = mgr.activate_skill_deps(session_id, "parent-skill")
        assert result is True
        assert not _is_gated(tmp_path, session_id, "parent-skill")

    def test_activate_deps_strips_marker_from_dependency_body(self, tmp_path: Path) -> None:
        """Dependency SKILL.md bodies must have %%ORDER_UP%% stripped after activation."""
        from unittest.mock import MagicMock

        session_id = "test-strip-marker"
        gate = "disable-model-invocation: true"
        marker_body = (
            "# Sub-Skill\n\nDo the work.\n\n"
            "ORCHESTRATION DIRECTIVE: When your task is complete, "
            "your final text output MUST end with: %%ORDER_UP%%\n"
            "CRITICAL: Append %%ORDER_UP%% at the very end of your substantive response, "
            "in the SAME message. Do NOT output %%ORDER_UP%% as a separate standalone message."
        )
        _write_skill_md(
            tmp_path,
            session_id,
            "parent-skill",
            f"---\nname: parent-skill\nactivate_deps: [dep-skill]\n{gate}\n---\n# Parent",
        )
        _write_skill_md(
            tmp_path,
            session_id,
            "dep-skill",
            f"---\nname: dep-skill\n{gate}\n---\n{marker_body}",
        )

        provider = MagicMock()
        provider.resolver.resolve.return_value = None

        mgr = DefaultSessionSkillManager(provider, ephemeral_root=tmp_path)
        mgr.activate_skill_deps(session_id, "parent-skill")

        dep_md = tmp_path / session_id / ".claude" / "skills" / "dep-skill" / "SKILL.md"
        dep_content = dep_md.read_text()
        assert "%%ORDER_UP%%" not in dep_content
        assert "# Sub-Skill" in dep_content
        assert "Do the work." in dep_content

    def test_activate_deps_preserves_marker_in_root_skill(self, tmp_path: Path) -> None:
        """The root (directly targeted) skill keeps its %%ORDER_UP%% body intact."""
        from unittest.mock import MagicMock

        session_id = "test-preserve-root-marker"
        gate = "disable-model-invocation: true"
        _write_skill_md(
            tmp_path,
            session_id,
            "root-skill",
            f"---\nname: root-skill\n{gate}\n---\n# Root\n\n%%ORDER_UP%%",
        )

        provider = MagicMock()
        provider.resolver.resolve.return_value = None

        mgr = DefaultSessionSkillManager(provider, ephemeral_root=tmp_path)
        mgr.activate_skill_deps(session_id, "root-skill")

        root_md = tmp_path / session_id / ".claude" / "skills" / "root-skill" / "SKILL.md"
        root_content = root_md.read_text()
        assert "%%ORDER_UP%%" in root_content


class TestCopyOnActivate:
    def test_copy_on_activate_single_absent_skill(self, tmp_path: Path) -> None:
        """Absence of SKILL.md triggers provider fetch; content is ungated after materialisation."""
        from unittest.mock import MagicMock

        session_id = "test-copy-on-activate"
        provider = MagicMock()

        def get_skill_content(name: str, gated: bool = False) -> str:
            if name == "absent-skill" and gated is False:
                return "---\nname: absent-skill\ndescription: Absent skill for testing.\ndisable-model-invocation: true\n---\n# Body"
            raise FileNotFoundError(name)

        provider.get_skill_content.side_effect = get_skill_content

        mgr = DefaultSessionSkillManager(provider, ephemeral_root=tmp_path)
        result = mgr.activate_skill_deps(session_id, "absent-skill")

        assert result is True
        skill_md = tmp_path / session_id / ".claude" / "skills" / "absent-skill" / "SKILL.md"
        assert skill_md.exists()
        provider.get_skill_content.assert_called_once_with("absent-skill", gated=False)
        assert "disable-model-invocation" not in skill_md.read_text()

    def test_copy_on_activate_unknown_skill_returns_false(self, tmp_path: Path) -> None:
        """Unknown skill raises FileNotFoundError and activate returns False."""
        from unittest.mock import MagicMock

        session_id = "test-unknown-skill"
        provider = MagicMock()
        provider.get_skill_content.side_effect = FileNotFoundError("not found")

        mgr = DefaultSessionSkillManager(provider, ephemeral_root=tmp_path)
        result = mgr.activate_skill_deps(session_id, "nonexistent-skill")

        assert result is False
        skills_dir = tmp_path / session_id / ".claude" / "skills"
        assert not skills_dir.exists() or not any(skills_dir.iterdir())

    def test_copy_on_activate_transitive_absent_dep(self, tmp_path: Path) -> None:
        """Transitive dep absent from disk is fetched and ungated after materialisation."""
        from unittest.mock import MagicMock

        session_id = "test-transitive-absent"
        _write_skill_md(
            tmp_path,
            session_id,
            "root-skill",
            "---\nname: root-skill\nactivate_deps: [dep-skill]\n"
            "disable-model-invocation: true\n---\n# Root",
        )

        def get_skill_content(name: str, gated: bool = False) -> str:
            if name == "dep-skill" and not gated:
                return "---\nname: dep-skill\ndescription: Dep skill for testing.\ndisable-model-invocation: true\n---\n# Dep Body"
            raise FileNotFoundError(name)

        provider = MagicMock()
        provider.get_skill_content.side_effect = get_skill_content
        provider.resolver.resolve.return_value = None

        mgr = DefaultSessionSkillManager(provider, ephemeral_root=tmp_path)
        result = mgr.activate_skill_deps(session_id, "root-skill")

        assert result is True
        dep_md = tmp_path / session_id / ".claude" / "skills" / "dep-skill" / "SKILL.md"
        assert dep_md.exists()
        assert "disable-model-invocation" not in dep_md.read_text()
        root_md = tmp_path / session_id / ".claude" / "skills" / "root-skill" / "SKILL.md"
        assert "disable-model-invocation" not in root_md.read_text()

    def test_copy_on_activate_absent_pack_member(self, tmp_path: Path) -> None:
        """list_skills() discovers pack members absent from disk and copy-on-activates them."""
        from unittest.mock import MagicMock

        from autoskillit.core.types import SkillSource
        from autoskillit.workspace.skills import SkillInfo

        session_id = "test-absent-pack-member"
        _write_skill_md(
            tmp_path,
            session_id,
            "parent-skill",
            "---\nname: parent-skill\nactivate_deps: [arch-lens]\n"
            "disable-model-invocation: true\n---\n# Parent",
        )
        _write_skill_md(
            tmp_path,
            session_id,
            "arch-lens-a",
            "---\nname: arch-lens-a\ncategories: [arch-lens]\n"
            "disable-model-invocation: true\n---\n# Lens A",
        )

        def get_skill_content(name: str, gated: bool = False) -> str:
            if name == "arch-lens-b" and not gated:
                return (
                    "---\nname: arch-lens-b\ndescription: Arch lens B skill for testing.\ncategories: [arch-lens]\n"
                    "disable-model-invocation: true\n---\n# Lens B"
                )
            raise FileNotFoundError(name)

        provider = MagicMock()
        provider.get_skill_content.side_effect = get_skill_content
        provider.list_skills.return_value = [
            SkillInfo(
                name="arch-lens-a",
                source=SkillSource.BUNDLED_EXTENDED,
                path=tmp_path / session_id / ".claude" / "skills" / "arch-lens-a" / "SKILL.md",
                categories=frozenset({"arch-lens"}),
            ),
            SkillInfo(
                name="arch-lens-b",
                source=SkillSource.BUNDLED_EXTENDED,
                path=tmp_path / ".claude" / "skills" / "arch-lens-b" / "SKILL.md",
                categories=frozenset({"arch-lens"}),
            ),
        ]

        def resolve_fn(name: str) -> SkillInfo | None:
            if name == "arch-lens-a":
                return SkillInfo(
                    name="arch-lens-a",
                    source=SkillSource.BUNDLED_EXTENDED,
                    path=tmp_path / session_id / ".claude" / "skills" / "arch-lens-a" / "SKILL.md",
                    categories=frozenset({"arch-lens"}),
                )
            return None

        provider.resolver.resolve.side_effect = resolve_fn

        mgr = DefaultSessionSkillManager(provider, ephemeral_root=tmp_path)
        result = mgr.activate_skill_deps(session_id, "parent-skill")

        assert result is True
        a_md = tmp_path / session_id / ".claude" / "skills" / "arch-lens-a" / "SKILL.md"
        assert a_md.exists()
        assert "disable-model-invocation" not in a_md.read_text()
        b_md = tmp_path / session_id / ".claude" / "skills" / "arch-lens-b" / "SKILL.md"
        assert b_md.exists()
        assert "disable-model-invocation" not in b_md.read_text()

    def test_activate_skill_deps_removes_flag_structural_gating(self, tmp_path: Path) -> None:
        """Materialised skill content has disable-model-invocation removed after ungating."""
        from unittest.mock import MagicMock

        session_id = "test-structural-gating"
        provider = MagicMock()

        def get_skill_content(name: str, gated: bool = False) -> str:
            if name == "gated-skill" and gated is False:
                return "---\nname: gated-skill\ndescription: Gated skill for testing.\ndisable-model-invocation: true\n---\n# Gated Body"
            raise FileNotFoundError(name)

        provider.get_skill_content.side_effect = get_skill_content

        mgr = DefaultSessionSkillManager(provider, ephemeral_root=tmp_path)
        result = mgr.activate_skill_deps(session_id, "gated-skill")

        assert result is True
        skill_md = tmp_path / session_id / ".claude" / "skills" / "gated-skill" / "SKILL.md"
        content = skill_md.read_text()
        assert "disable-model-invocation" not in content
        assert "# Gated Body" in content
