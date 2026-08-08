"""Tests for AGENTS.md content completeness and boundary correctness."""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("docs"), pytest.mark.medium]

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
    ("Pyright", "LSP section"),
    ("goToDefinition", "LSP operations table"),
    ("findReferences", "LSP operations table"),
    ("Skill tool", "Skill invocations section"),
    ("/skill-name", "Skill invocations section"),
    ("Skill Invocations Are Orders", "Skill invocations section"),
    ("CLAUDE_CODE_EXIT_AFTER_STOP_DELAY", "Claude Code env var"),
    ("Claude Code session UUID", "Section 5 session diagnostics sentence"),
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


# ── Universal project-rule markers (11 rule families owned by AGENTS.md) ──

UNIVERSAL_PROJECT_RULE_MARKERS: list[tuple[str, str]] = [
    ("Version Bumps", "version-bumps"),
    ("pre-commit run --all-files", "pre-commit"),
    ("RETIRED_SCRIPT_BASENAMES", "hook-renames"),
    ("RETIRED_SKILL_NAMES", "skill-renames"),
    ("RETIRED_INTAKE_RULE_IDS", "intake-rule-retirement"),
    ("POSIX ERE", "search-tool-ere"),
    ('Grep(pattern="foo|bar")', "search-tool-ere"),
    ("autoskillit init", "worktree-init-prohibition"),
    ("task install-worktree", "worktree-init-prohibition"),
    ("Naming convention", "def-vs-spec-suffixes"),
    ("*Def", "def-vs-spec-suffixes"),
    ("*Spec", "def-vs-spec-suffixes"),
    ("git commit --amend", "commit-discipline"),
    ("--fixup", "commit-discipline"),
    ("--squash", "commit-discipline"),
    ("task test-all", "task-runner-tests"),
    ("task test-check", "task-runner-tests"),
    ("task test-filtered", "task-runner-tests"),
]


class TestAgentsMdUniversalProjectRules:
    @pytest.mark.parametrize(
        ("marker", "rule_family"),
        UNIVERSAL_PROJECT_RULE_MARKERS,
        ids=[m[1] for m in UNIVERSAL_PROJECT_RULE_MARKERS],
    )
    def test_agents_md_owns_universal_rule(
        self, agents_md: str, marker: str, rule_family: str
    ) -> None:
        assert marker in agents_md, (
            f"Universal project rule '{rule_family}' missing from AGENTS.md: "
            f"expected marker '{marker}' to be present"
        )


# ── Content quality checks ────────────────────────────────────────────


class TestAgentsMdContentQuality:
    def test_agents_md_does_not_require_tests_after_every_task(self, agents_md: str) -> None:
        assert "Always run tests at end of task" not in agents_md

    def test_agents_md_github_api_has_sleep_rule(self, agents_md: str) -> None:
        assert "sleep 1" in agents_md or "asyncio.sleep(1)" in agents_md

    def test_agents_md_architecture_points_to_discoverable_layout(self, agents_md: str) -> None:
        assert "ls src/autoskillit/" in agents_md
        assert "nearest ancestor guide" in agents_md

    def test_agents_md_has_import_layer_table(self, agents_md: str) -> None:
        assert "| IL-N (single digit)" in agents_md or "Import layer" in agents_md

    def test_agents_md_file_system_has_temp_rule(self, agents_md: str) -> None:
        assert ".autoskillit/temp/" in agents_md

    def test_agents_md_testing_mentions_parallel_safety(self, agents_md: str) -> None:
        assert "parallel" in agents_md.lower()

    def test_agents_md_carries_no_package_index_table(self, agents_md: str) -> None:
        """Trimmed 2026-08: the package index is inferable from the codebase and must
        not creep back into the root instruction surface."""
        assert "an index, not required reading" in agents_md
        assert "| Package | IL | Purpose |" not in agents_md

    def test_agents_md_diagnostics_mentions_codex(self, agents_md: str) -> None:
        parts = agents_md.split("## **6. Session Diagnostics**")
        assert len(parts) > 1, "Session Diagnostics section not found"
        section = parts[1].split("## ", 1)[0]
        assert "Codex" in section
