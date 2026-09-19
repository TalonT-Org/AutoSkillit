"""Sub-directory AGENTS.md coverage for skills/ and skills_extended/.

The paired AGENTS.md (content) + CLAUDE.md (single-line `@AGENTS.md` plus
trailing newline sibling shim) convention is enforced by
tests/docs/test_sub_claude_md_completeness.py.
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


def _read_or_skip(path: Path) -> str:
    """Skip if the file is missing; otherwise return its contents.

    Tests that require the file to exist use this so a missing file
    surfaces as a clean skip (with a clear reason) rather than an
    unhandled FileNotFoundError from the fixture.
    """
    if not path.is_file():
        pytest.skip(f"{path} not present in this checkout")
    return path.read_text(encoding="utf-8")


@pytest.fixture()
def skills_agents_md() -> str:
    return _read_or_skip(SKILLS_AGENTS_MD)


@pytest.fixture()
def skills_extended_agents_md() -> str:
    return _read_or_skip(SKILLS_EXTENDED_AGENTS_MD)


class TestSkillsAgentsMd:
    def test_agents_md_exists(self) -> None:
        assert SKILLS_AGENTS_MD.is_file(), (
            "skills/AGENTS.md missing (corrected F-C1-02 remediation; see issue #3773)"
        )

    def test_claude_md_shim_exists(self) -> None:
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
        # sous-chef is not user-invocable; it is the orchestrator bootstrap
        # document injected by open_kitchen, not a slash command.
        marker_lines = [
            line for line in skills_agents_md.splitlines() if "internal" in line.lower()
        ]
        assert any("sous-chef" in line.lower() for line in marker_lines), (
            "skills/AGENTS.md must mark sous-chef as internal in a single "
            "marker line, not via separate co-occurring keywords"
        )

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
        assert "session_skills" in skills_extended_agents_md

    def test_agents_md_documents_skill_count_correctly(
        self, skills_extended_agents_md: str
    ) -> None:
        text = skills_extended_agents_md.lower()
        assert "138" in text
        assert "bundled" in text or "extended" in text
        assert "user-invocable" not in text, (
            "skills_extended/AGENTS.md must not claim all 138 are "
            "user-invocable; reload-session carries "
            "disable-model-invocation: true (see docs/skills/catalog.md)"
        )

    def test_agents_md_mentions_categories_convention(
        self, skills_extended_agents_md: str
    ) -> None:
        assert "categories" in skills_extended_agents_md.lower()

    def test_agents_md_no_claude_md_import(self, skills_extended_agents_md: str) -> None:
        for line in skills_extended_agents_md.splitlines():
            assert not line.strip().startswith("@"), (
                f"skills_extended/AGENTS.md must contain real content, not "
                f"an @import directive: {line!r}"
            )
