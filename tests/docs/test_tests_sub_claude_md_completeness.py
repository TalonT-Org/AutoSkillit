"""Structural tests for per-subfolder CLAUDE.md documentation files under tests/."""

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.small

TESTS_ROOT = Path(__file__).resolve().parents[1]

EXPECTED_SUB_CLAUDE_MDS = [
    "arch/AGENTS.md",
    "assets/AGENTS.md",
    "cli/AGENTS.md",
    "config/AGENTS.md",
    "contracts/AGENTS.md",
    "core/AGENTS.md",
    "docs/AGENTS.md",
    "execution/AGENTS.md",
    "fleet/AGENTS.md",
    "hooks/AGENTS.md",
    "infra/AGENTS.md",
    "migration/AGENTS.md",
    "pipeline/AGENTS.md",
    "planner/AGENTS.md",
    "recipe/AGENTS.md",
    "server/AGENTS.md",
    "skills/AGENTS.md",
    "skills_extended/AGENTS.md",
    "workspace/AGENTS.md",
]


def test_all_tests_sub_claude_md_files_exist():
    """Every test subdirectory has a CLAUDE.md file."""
    missing = [p for p in EXPECTED_SUB_CLAUDE_MDS if not (TESTS_ROOT / p).is_file()]
    assert not missing, f"Missing tests/ sub-CLAUDE.md files: {missing}"


def test_tests_sub_claude_md_no_main_claude_md_duplication():
    """Sub-CLAUDE.md files must not duplicate numbered sections from the main tests/CLAUDE.md."""
    numbered_section_re = re.compile(r"^## \*{0,2}\d+\.", re.MULTILINE)
    failures = []
    for rel_path in EXPECTED_SUB_CLAUDE_MDS:
        claude_md = TESTS_ROOT / rel_path
        if not claude_md.is_file():
            failures.append(f"{rel_path}: file does not exist")
            continue
        content = claude_md.read_text()
        match = numbered_section_re.search(content)
        if match:
            failures.append(f"{rel_path}: contains '{match.group()}' (main CLAUDE.md section)")
    assert not failures, "tests/ sub-CLAUDE.md files duplicate main sections:\n" + "\n".join(
        failures
    )


def test_top_level_tests_claude_md_references_all_subdirs():
    """The top-level tests/AGENTS.md references each subdirectory's AGENTS.md."""
    top_agents_md = TESTS_ROOT / "AGENTS.md"
    if not top_agents_md.is_file():
        pytest.fail("tests/AGENTS.md does not exist")
    content = top_agents_md.read_text()
    failures = []
    for rel_path in EXPECTED_SUB_CLAUDE_MDS:
        subdir = rel_path.split("/")[0]
        marker = f"see {subdir}/AGENTS.md"
        if marker not in content:
            failures.append(f"tests/AGENTS.md missing reference: '{marker}'")
    assert not failures, "Top-level tree missing sub-AGENTS.md references:\n" + "\n".join(failures)


def test_top_level_tests_claude_md_no_per_file_subdir_listings():
    """The top-level tests/AGENTS.md tree must contain directory landmarks only."""
    top_agents_md = TESTS_ROOT / "AGENTS.md"
    if not top_agents_md.is_file():
        pytest.fail("tests/AGENTS.md does not exist")
    content = top_agents_md.read_text()
    tree_entry_re = re.compile(r"^[│ ]*[├└]──\s+(?P<entry>\S+)", re.MULTILINE)
    file_leaves = [
        match.group("entry")
        for match in tree_entry_re.finditer(content)
        if not match.group("entry").endswith("/")
    ]
    assert not file_leaves, (
        f"tests/AGENTS.md directory landmarks must not contain per-file catalogs: {file_leaves}"
    )
