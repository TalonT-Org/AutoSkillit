"""Structural tests for AGENTS.md documentation files under tests/."""

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.small

TESTS_ROOT = Path(__file__).resolve().parents[1]

_DIRECT_CHILD_GUIDE_REFERENCE_RE = re.compile(
    r"\bsee (?P<reference>[A-Za-z0-9_-]+/AGENTS\.md)(?![\w./-])"
)


def test_top_level_tests_agents_md_references_existing_guidance_files():
    """Every direct-child guidance reference in tests/AGENTS.md resolves."""
    top_agents_md = TESTS_ROOT / "AGENTS.md"
    if not top_agents_md.is_file():
        pytest.fail("tests/AGENTS.md does not exist")
    content = top_agents_md.read_text(encoding="utf-8")
    references = sorted(
        {match.group("reference") for match in _DIRECT_CHILD_GUIDE_REFERENCE_RE.finditer(content)}
    )
    assert references, "tests/AGENTS.md contains no direct-child guidance references"

    missing = sorted(
        (TESTS_ROOT / reference).relative_to(TESTS_ROOT.parent).as_posix()
        for reference in references
        if not (TESTS_ROOT / reference).is_file()
    )
    assert not missing, f"tests/AGENTS.md references missing guidance files: {missing}"


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
