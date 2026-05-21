"""Tests for AGENTS.md content completeness and boundary correctness."""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture()
def agents_md() -> str:
    return (REPO_ROOT / "AGENTS.md").read_text()


# ── Existence and structure ──────────────────────────────────────────


class TestAgentsMdStructure:
    def test_agents_md_exists(self) -> None:
        assert (REPO_ROOT / "AGENTS.md").is_file()

    def test_agents_md_top_heading(self, agents_md: str) -> None:
        assert agents_md.startswith("# **AutoSkillit: Development Guidelines**")

    def test_agents_md_has_proper_heading_hierarchy(self, agents_md: str) -> None:
        headings = [
            ln for ln in agents_md.splitlines() if ln.startswith("## ") or ln.startswith("### ")
        ]
        assert len(headings) >= 9, f"Expected >= 9 section headings, got {len(headings)}"

    def test_agents_md_no_import_directives(self, agents_md: str) -> None:
        for i, line in enumerate(agents_md.splitlines(), 1):
            assert not line.strip().startswith("@"), f"Import directive found on line {i}: {line}"


# ── 7 shared categories present ──────────────────────────────────────

SHARED_SECTION_MARKERS: list[tuple[str, str]] = [
    ("Core Project Goal", "Core Project Goal"),
    ("General Principles", "General Principles"),
    ("Code and Implementation", "Code and Implementation"),
    ("File System", "File System"),
    ("GitHub API Call Discipline", "GitHub API Call Discipline"),
    ("GitHub Issue Body", "GitHub Issue Body"),
    ("Testing Guidelines", "Testing Guidelines"),
    ("Architecture", "Architecture"),
    ("Session Diagnostics", "Session Diagnostics"),
]


class TestAgentsMdSharedCategories:
    @pytest.mark.parametrize(
        ("label", "marker"),
        SHARED_SECTION_MARKERS,
        ids=[m[0] for m in SHARED_SECTION_MARKERS],
    )
    def test_agents_md_has_shared_section(self, agents_md: str, label: str, marker: str) -> None:
        assert marker in agents_md, f"Missing shared section: {label}"


# ── No Claude-specific content ────────────────────────────────────────

FORBIDDEN_TERMS: list[tuple[str, str]] = [
    ("pre-commit run", "Pre-commit hooks section"),
    ("task test-all", "Task runner command"),
    ("task test-check", "Task runner command"),
    ("task test-filtered", "Task runner command"),
    ("task install-worktree", "Task runner command"),
    ("task sync-versions", "Task runner command"),
    ("HOOK_REGISTRY", "Hook rename rules"),
    ("RETIRED_SCRIPT_BASENAMES", "Hook rename rules"),
    ("RETIRED_SKILL_NAMES", "Skill rename rules"),
    ("Pyright", "LSP section"),
    ("goToDefinition", "LSP operations table"),
    ("findReferences", "LSP operations table"),
    ("Skill tool", "Skill invocations section"),
    ("/skill-name", "Skill invocations section"),
    ("grep_pattern_lint_guard", "Grep tool ERE syntax rule"),
    ("CLAUDE_CODE_EXIT_AFTER_STOP_DELAY", "Claude Code env var"),
    ("autoskillit init", "Worktree init prohibition"),
]


class TestAgentsMdNoClaude:
    @pytest.mark.parametrize(
        ("term", "source"),
        FORBIDDEN_TERMS,
        ids=[t[0] for t in FORBIDDEN_TERMS],
    )
    def test_agents_md_no_claude_specific_content(
        self, agents_md: str, term: str, source: str
    ) -> None:
        assert term not in agents_md, (
            f"Claude-specific term leaked into AGENTS.md: '{term}' (from {source})"
        )


# ── Content quality checks ────────────────────────────────────────────


class TestAgentsMdContentQuality:
    def test_agents_md_github_api_has_sleep_rule(self, agents_md: str) -> None:
        assert "sleep 1" in agents_md or "asyncio.sleep(1)" in agents_md

    def test_agents_md_architecture_has_package_table(self, agents_md: str) -> None:
        assert "| Package | IL | Purpose |" in agents_md

    def test_agents_md_has_import_layer_table(self, agents_md: str) -> None:
        assert "| IL-N (single digit)" in agents_md or "Import layer" in agents_md

    def test_agents_md_file_system_has_temp_rule(self, agents_md: str) -> None:
        assert ".autoskillit/temp/" in agents_md

    def test_agents_md_testing_mentions_parallel_safety(self, agents_md: str) -> None:
        assert "parallel" in agents_md.lower()
