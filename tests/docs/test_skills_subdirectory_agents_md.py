"""Sub-directory AGENTS.md coverage for skills/ and skills_extended/.

These tests pin the corrected F-C1-02 remediation: skills/ and
skills_extended/ must carry an AGENTS.md (content) plus a CLAUDE.md
sibling shim containing exactly "@AGENTS.md\n". The pairing is
required by tests/docs/test_sub_claude_md_completeness.py (PR #3901
convention); this test pins the new guides specifically.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("docs"), pytest.mark.medium]

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC = REPO_ROOT / "src" / "autoskillit"

SKILLS_DIR = SRC / "skills"
SKILLS_EXTENDED_DIR = SRC / "skills_extended"
SKILLS_AGENTS_MD = SKILLS_DIR / "AGENTS.md"
SKILLS_CLAUDE_MD = SKILLS_DIR / "CLAUDE.md"
SKILLS_EXTENDED_AGENTS_MD = SKILLS_EXTENDED_DIR / "AGENTS.md"
SKILLS_EXTENDED_CLAUDE_MD = SKILLS_EXTENDED_DIR / "CLAUDE.md"


@pytest.fixture()
def skills_agents_md() -> str:
    return SKILLS_AGENTS_MD.read_text(encoding="utf-8")


@pytest.fixture()
def skills_extended_agents_md() -> str:
    return SKILLS_EXTENDED_AGENTS_MD.read_text(encoding="utf-8")


class TestSkillsAgentsMd:
    def test_agents_md_exists(self) -> None:
        assert SKILLS_AGENTS_MD.is_file(), (
            "skills/AGENTS.md missing (corrected F-C1-02 remediation; see issue #3773)"
        )

    def test_claude_md_shim_exists(self) -> None:
        # Required sibling shim per PR #3901 convention; the sibling-contract
        # test in tests/docs/test_sub_claude_md_completeness.py will fail
        # without it.
        assert SKILLS_CLAUDE_MD.is_file(), (
            "skills/CLAUDE.md sibling shim missing (required by PR #3901 "
            "convention and tests/docs/test_sub_claude_md_completeness.py)"
        )

    def test_claude_md_shim_is_exactly_at_agents_md(self) -> None:
        assert SKILLS_CLAUDE_MD.read_text(encoding="utf-8") == "@AGENTS.md\n"

    def test_agents_md_has_architecture_notes(self, skills_agents_md: str) -> None:
        assert "## Architecture Notes" in skills_agents_md

    def test_agents_md_has_multi_concern_files(self, skills_agents_md: str) -> None:
        assert "## Multi-concern files" in skills_agents_md

    def test_agents_md_documents_open_kitchen(self, skills_agents_md: str) -> None:
        assert "open-kitchen" in skills_agents_md

    def test_agents_md_documents_sous_chef(self, skills_agents_md: str) -> None:
        assert "sous-chef" in skills_agents_md

    def test_agents_md_documents_close_kitchen(self, skills_agents_md: str) -> None:
        assert "close-kitchen" in skills_agents_md

    def test_agents_md_marks_sous_chef_internal(self, skills_agents_md: str) -> None:
        # sous-chef is not user-invocable (it is the orchestrator bootstrap
        # document injected by open_kitchen); this MUST be documented so
        # users do not try to invoke it via /autoskillit:sous-chef.
        text = skills_agents_md.lower()
        assert "internal" in text and "sous-chef" in text

    def test_agents_md_no_claude_md_import(self, skills_agents_md: str) -> None:
        for line in skills_agents_md.splitlines():
            assert not line.strip().startswith("@"), (
                f"skills/AGENTS.md must contain real content, not an @import directive: {line!r}"
            )


class TestSkillsExtendedAgentsMd:
    def test_agents_md_exists(self) -> None:
        assert SKILLS_EXTENDED_AGENTS_MD.is_file(), (
            "skills_extended/AGENTS.md missing (corrected F-C1-02 remediation; see issue #3773)"
        )

    def test_claude_md_shim_exists(self) -> None:
        assert SKILLS_EXTENDED_CLAUDE_MD.is_file(), (
            "skills_extended/CLAUDE.md sibling shim missing (required by "
            "PR #3901 convention and "
            "tests/docs/test_sub_claude_md_completeness.py)"
        )

    def test_claude_md_shim_is_exactly_at_agents_md(self) -> None:
        assert SKILLS_EXTENDED_CLAUDE_MD.read_text(encoding="utf-8") == "@AGENTS.md\n"

    def test_agents_md_has_architecture_notes(self, skills_extended_agents_md: str) -> None:
        assert "## Architecture Notes" in skills_extended_agents_md

    def test_agents_md_has_multi_concern_files(self, skills_extended_agents_md: str) -> None:
        assert "## Multi-concern files" in skills_extended_agents_md

    def test_agents_md_documents_loader_path(self, skills_extended_agents_md: str) -> None:
        # The extended-skills package is materialized into the workspace
        # by workspace/session_skills/; the AGENTS.md must cross-reference it.
        assert "session_skills" in skills_extended_agents_md

    def test_agents_md_documents_skill_count_correctly(
        self, skills_extended_agents_md: str
    ) -> None:
        # Per docs/skills/catalog.md the canonical phrasing is "138 bundled
        # skills" — NOT "138 user-invocable skills", because 1 of the 138
        # (`reload-session`) carries disable-model-invocation: true and is
        # not slash-command-invocable. This test prevents regressing to the
        # wrong framing.
        text = skills_extended_agents_md.lower()
        assert "138" in text
        assert "bundled" in text or "extended" in text
        assert "user-invocable" not in text, (
            "skills_extended/AGENTS.md must not claim all 138 are "
            "user-invocable; reload-session carries "
            "disable-model-invocation: true (see docs/skills/catalog.md)"
        )

    def test_agents_md_no_claude_md_import(self, skills_extended_agents_md: str) -> None:
        for line in skills_extended_agents_md.splitlines():
            assert not line.strip().startswith("@"), (
                f"skills_extended/AGENTS.md must contain real content, not "
                f"an @import directive: {line!r}"
            )

    def test_skills_extended_agents_md_mentions_categories_convention(
        self, skills_extended_agents_md: str
    ) -> None:
        # The categories: frontmatter field groups skills for discovery;
        # the AGENTS.md should reference this so contributors know the
        # field is meaningful (relevant context for F-C1-11 triage).
        assert "categories" in skills_extended_agents_md.lower()
