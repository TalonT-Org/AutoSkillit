"""Verify every doc is reachable from docs/README.md and every subdir has a README."""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DOCS_DIR = REPO_ROOT / "docs"
DOCS_README = DOCS_DIR / "README.md"
ROOT_README = REPO_ROOT / "README.md"

EXPECTED_SUBDIRS = {
    "examples",
    "recipes",
    "skills",
    "execution",
    "safety",
    "operations",
    "developer",
}

LINK_RE = re.compile(r"\[[^\]]*\]\(([^)#]+)(?:#[^)]*)?\)")


def _all_md_files() -> list[Path]:
    return sorted(p for p in DOCS_DIR.rglob("*.md"))


def _links_in(md_path: Path) -> list[Path]:
    """Return absolute resolved paths for every local .md link in md_path."""
    text = md_path.read_text(encoding="utf-8")
    out: list[Path] = []
    for raw in LINK_RE.findall(text):
        if raw.startswith(("http://", "https://", "mailto:")):
            continue
        if not raw.endswith(".md"):
            continue
        target = (md_path.parent / raw).resolve()
        out.append(target)
    return out


def test_docs_readme_exists() -> None:
    assert DOCS_README.exists(), "docs/README.md is missing"


def test_every_subdirectory_has_readme() -> None:
    missing = []
    for sub in EXPECTED_SUBDIRS:
        readme = DOCS_DIR / sub / "README.md"
        if not readme.exists():
            missing.append(str(readme.relative_to(REPO_ROOT)))
    assert not missing, f"Missing subdirectory READMEs: {missing}"


def test_every_doc_reachable_from_index() -> None:
    """BFS from docs/README.md through .md links; every docs/**.md must be visited."""
    if not DOCS_README.exists():
        import pytest

        pytest.skip("docs/README.md not present yet")

    visited: set[Path] = {DOCS_README.resolve()}
    queue: list[Path] = [DOCS_README]
    while queue:
        current = queue.pop()
        for link in _links_in(current):
            if link in visited:
                continue
            if not link.exists() or link.suffix != ".md":
                continue
            try:
                link.relative_to(DOCS_DIR.resolve())
            except ValueError:
                continue
            visited.add(link)
            queue.append(link)

    expected = {p.resolve() for p in _all_md_files()}
    unreachable = expected - visited
    assert not unreachable, "Unreachable from docs/README.md: " + ", ".join(
        sorted(p.relative_to(REPO_ROOT).as_posix() for p in unreachable)
    )


def test_subdir_readme_lists_each_sibling() -> None:
    """Each subdirectory README must mention every sibling .md file by name."""
    for sub in EXPECTED_SUBDIRS:
        sub_dir = DOCS_DIR / sub
        if not sub_dir.exists():
            continue
        readme = sub_dir / "README.md"
        if not readme.exists():
            continue
        readme_text = readme.read_text(encoding="utf-8")
        siblings = sorted(p.name for p in sub_dir.glob("*.md") if p.name != "README.md")
        for sib in siblings:
            assert sib in readme_text, (
                f"{readme.relative_to(REPO_ROOT)} does not list sibling {sib}"
            )


def test_docs_readme_line_count_under_80() -> None:
    if not DOCS_README.exists():
        import pytest

        pytest.skip("docs/README.md not present yet")
    line_count = len(DOCS_README.read_text(encoding="utf-8").splitlines())
    assert line_count <= 80, f"docs/README.md has {line_count} lines (max 80)"


def test_root_readme_line_count_50_to_70() -> None:
    if not ROOT_README.exists():
        import pytest

        pytest.skip("README.md not present yet")
    line_count = len(ROOT_README.read_text(encoding="utf-8").splitlines())
    assert 50 <= line_count <= 70, f"README.md has {line_count} lines (target 50-70)"
