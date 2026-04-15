"""Reject AI-tone banned phrases in every doc.

Phrases derived from REQ-DOC-070. Code spans (backticks), fenced blocks,
file paths, and quoted strings are stripped before matching so a literal
example cannot trigger a false positive.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
DOCS_DIR = REPO_ROOT / "docs"
ROOT_README = REPO_ROOT / "README.md"

BANNED_PHRASES = [
    r"under the hood",
    r"sensible defaults",
    r"fills in the rest",
    r"best technical approach",
    r"magically",
    r"seamlessly",
    r"effortlessly",
    r"works like magic",
    r"just works",
    r"deep dive",
    r"super easy",
    r"out of the box",
    r"battle[- ]tested",
    r"painlessly",
]

# Files exempt from banned-phrase checking. The glossary intentionally lists
# the canonical and banned spellings of every term it covers.
EXEMPT_FILES = {"glossary.md"}

CODE_BLOCK_RE = re.compile(r"```.*?```", re.DOTALL)
INLINE_CODE_RE = re.compile(r"`[^`]*`")
QUOTED_RE = re.compile(r'"[^"]*"|\'[^\']*\'')


def _strip_examples(text: str) -> str:
    text = CODE_BLOCK_RE.sub("", text)
    text = INLINE_CODE_RE.sub("", text)
    text = QUOTED_RE.sub("", text)
    return text


def _doc_files() -> list[Path]:
    files = [p for p in DOCS_DIR.rglob("*.md") if p.name not in EXEMPT_FILES]
    if ROOT_README.exists():
        files.append(ROOT_README)
    return sorted(files)


@pytest.mark.parametrize("md", _doc_files(), ids=lambda p: p.relative_to(REPO_ROOT).as_posix())
def test_no_banned_phrases(md: Path) -> None:
    text = _strip_examples(md.read_text(encoding="utf-8"))
    hits: list[tuple[str, int]] = []
    for line_no, line in enumerate(text.splitlines(), start=1):
        for phrase in BANNED_PHRASES:
            if re.search(phrase, line, flags=re.IGNORECASE):
                hits.append((phrase, line_no))
    assert not hits, f"{md.relative_to(REPO_ROOT)} contains banned phrases: " + ", ".join(
        f"{phrase!r} on line {line}" for phrase, line in hits
    )
