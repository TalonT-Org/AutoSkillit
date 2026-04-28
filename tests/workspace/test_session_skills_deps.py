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
