"""Structural tests for per-subfolder CLAUDE.md documentation files under tests/."""

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.small

TESTS_ROOT = Path(__file__).resolve().parents[1]

EXPECTED_SUB_CLAUDE_MDS = [
    "arch/CLAUDE.md",
    "assets/CLAUDE.md",
    "cli/CLAUDE.md",
    "config/CLAUDE.md",
    "contracts/CLAUDE.md",
    "core/CLAUDE.md",
    "docs/CLAUDE.md",
    "execution/CLAUDE.md",
    "fleet/CLAUDE.md",
    "hooks/CLAUDE.md",
    "infra/CLAUDE.md",
    "migration/CLAUDE.md",
    "pipeline/CLAUDE.md",
    "planner/CLAUDE.md",
    "recipe/CLAUDE.md",
    "server/CLAUDE.md",
    "skills/CLAUDE.md",
    "skills_extended/CLAUDE.md",
    "workspace/CLAUDE.md",
]


def test_all_tests_sub_claude_md_files_exist():
    """Every test subdirectory has a CLAUDE.md file."""
    missing = [p for p in EXPECTED_SUB_CLAUDE_MDS if not (TESTS_ROOT / p).is_file()]
    assert not missing, f"Missing tests/ sub-CLAUDE.md files: {missing}"


def test_tests_sub_claude_md_covers_all_py_files():
    """Each sub-CLAUDE.md mentions every .py file in its directory."""
    failures = []
    for rel_path in EXPECTED_SUB_CLAUDE_MDS:
        claude_md = TESTS_ROOT / rel_path
        if not claude_md.is_file():
            failures.append(f"{rel_path}: file does not exist")
            continue
        content = claude_md.read_text()
        directory = claude_md.parent
        py_files = sorted(f.name for f in directory.glob("*.py"))
        for py_file in py_files:
            if py_file == "__init__.py":
                if "`__init__.py`" not in content:
                    failures.append(f"{rel_path}: missing `__init__.py` in file table")
            else:
                if py_file not in content:
                    failures.append(f"{rel_path}: missing {py_file}")
    assert not failures, "tests/ sub-CLAUDE.md coverage gaps:\n" + "\n".join(failures)


def test_tests_sub_claude_md_has_file_table():
    """Each sub-CLAUDE.md uses the standard file table template."""
    failures = []
    for rel_path in EXPECTED_SUB_CLAUDE_MDS:
        claude_md = TESTS_ROOT / rel_path
        if not claude_md.is_file():
            failures.append(f"{rel_path}: file does not exist")
            continue
        content = claude_md.read_text()
        if "| File | Purpose |" not in content:
            failures.append(f"{rel_path}: missing '| File | Purpose |' table header")
    assert not failures, "tests/ sub-CLAUDE.md template violations:\n" + "\n".join(failures)


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
    """The top-level tests/CLAUDE.md references each subdirectory's CLAUDE.md."""
    top_claude_md = TESTS_ROOT / "CLAUDE.md"
    if not top_claude_md.is_file():
        pytest.fail("tests/CLAUDE.md does not exist")
    content = top_claude_md.read_text()
    failures = []
    for rel_path in EXPECTED_SUB_CLAUDE_MDS:
        subdir = rel_path.split("/")[0]
        marker = f"see {subdir}/CLAUDE.md"
        if marker not in content:
            failures.append(f"tests/CLAUDE.md missing reference: '{marker}'")
    assert not failures, "Top-level tree missing sub-CLAUDE.md references:\n" + "\n".join(failures)


def test_top_level_tests_claude_md_no_per_file_subdir_listings():
    """The top-level tests/CLAUDE.md must not contain per-file listings for subdirectories."""
    top_claude_md = TESTS_ROOT / "CLAUDE.md"
    if not top_claude_md.is_file():
        pytest.fail("tests/CLAUDE.md does not exist")
    content = top_claude_md.read_text()
    # Per-file listings look like: │   ├── test_*.py lines inside subdir blocks.
    # After decomposition, subdirectory blocks should be single lines only.
    # Check that no test_*.py filename appears indented under a subdirectory marker.
    indented_test_file_re = re.compile(r"│\s+[├└]── test_\w+\.py")
    matches = indented_test_file_re.findall(content)
    assert not matches, (
        f"tests/CLAUDE.md still contains {len(matches)} per-file listings "
        f"for subdirectories (should be in sub-CLAUDE.md files): {matches[:5]}"
    )
