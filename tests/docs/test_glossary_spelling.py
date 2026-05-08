"""Reject banned variants of glossary terms across every doc.

The glossary itself is exempt because it intentionally lists each canonical
form alongside its common-mistake variants.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
DOCS_DIR = REPO_ROOT / "docs"
GLOSSARY = DOCS_DIR / "glossary.md"

# Each entry: (banned regex, canonical form). Matching is case-insensitive
# unless the regex carries an explicit flag.
BANNED_VARIANTS: list[tuple[str, str]] = [
    (r"free-range tool", "free range tools"),
    (r"sous chef\b", "sous-chef"),
    (r"\bRecify\b", "Rectify"),
    (r"\bwork tree\b", "worktree"),
    (r"\btier-1\b", "Tier 1"),
    (r"\btier-2\b", "Tier 2"),
    (r"\btier-3\b", "Tier 3"),
    (r"retry reason\b", "retry_reason"),
]

CODE_BLOCK_RE = re.compile(r"```.*?```", re.DOTALL)
INLINE_CODE_RE = re.compile(r"`[^`]*`")


def _strip_examples(text: str) -> str:
    text = CODE_BLOCK_RE.sub("", text)
    text = INLINE_CODE_RE.sub("", text)
    return text


def _doc_files() -> list[Path]:
    return sorted(p for p in DOCS_DIR.rglob("*.md") if p.name != "glossary.md")


def test_glossary_exists() -> None:
    assert GLOSSARY.exists(), "docs/glossary.md is missing"


def test_glossary_has_at_least_17_terms() -> None:
    if not GLOSSARY.exists():
        pytest.skip("glossary not present")
    headings = re.findall(r"^### .+$", GLOSSARY.read_text(encoding="utf-8"), flags=re.MULTILINE)
    assert len(headings) >= 17, f"Glossary has {len(headings)} terms (need >= 17)"


@pytest.mark.parametrize("md", _doc_files(), ids=lambda p: p.relative_to(REPO_ROOT).as_posix())
def test_no_banned_variants(md: Path) -> None:
    text = _strip_examples(md.read_text(encoding="utf-8"))
    hits: list[tuple[str, str, int]] = []
    for line_no, line in enumerate(text.splitlines(), start=1):
        for pat, canonical in BANNED_VARIANTS:
            if re.search(pat, line, flags=re.IGNORECASE):
                hits.append((pat, canonical, line_no))
    assert not hits, f"{md.relative_to(REPO_ROOT)} contains banned variants: " + ", ".join(
        f"/{pat}/ (use {canonical!r}) on line {line}" for pat, canonical, line in hits
    )
