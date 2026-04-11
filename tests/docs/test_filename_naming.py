"""Encode the 7 naming rules from REQ-DOC-085 as predicates over docs/ filenames."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
DOCS_DIR = REPO_ROOT / "docs"

KEBAB_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*\.md$")

# Files allow-listed against specific rules. Each entry: rule -> {filenames}.
ALLOWLIST = {
    "ing_suffix": {"getting-started.md", "contributing.md", "authoring.md"},
    "segment_count": {"getting-started.md", "end-turn-hazards.md", "research-pipeline.md"},
}

# overrides.md is a list-like file (collection of override sites) and is exempt;
# subsets.md and catalog.md are explicitly list files per REQ-DOC-085 rule 5.
CONCEPT_FILES_MUST_BE_SINGULAR = {
    "visibility",
    "composition",
    "architecture",
    "orchestration",
}


def _all_md_files() -> list[Path]:
    return sorted(p for p in DOCS_DIR.rglob("*.md") if p.name != "README.md")


@pytest.mark.parametrize("md", _all_md_files(), ids=lambda p: p.relative_to(REPO_ROOT).as_posix())
def test_rule_1_kebab_case(md: Path) -> None:
    assert KEBAB_RE.match(md.name), f"{md.name} is not kebab-case"


@pytest.mark.parametrize("md", _all_md_files(), ids=lambda p: p.relative_to(REPO_ROOT).as_posix())
def test_rule_2_noun_phrase(md: Path) -> None:
    if md.name in ALLOWLIST["ing_suffix"]:
        return
    stem = md.stem
    assert not stem.endswith("ing"), f"{md.name} ends in -ing (verb form)"


@pytest.mark.parametrize("md", _all_md_files(), ids=lambda p: p.relative_to(REPO_ROOT).as_posix())
def test_rule_3_drop_topic_prefix_in_subdirs(md: Path) -> None:
    parent = md.parent.name
    if parent in {"recipes", "skills", "execution", "safety", "operations"}:
        first_segment = md.stem.split("-", 1)[0]
        assert first_segment != parent, (
            f"{md.relative_to(REPO_ROOT)}: filename starts with parent dir name '{parent}'"
        )


@pytest.mark.parametrize("md", _all_md_files(), ids=lambda p: p.relative_to(REPO_ROOT).as_posix())
def test_rule_4_max_three_segments(md: Path) -> None:
    if md.name in ALLOWLIST["segment_count"]:
        return
    segments = md.stem.split("-")
    assert len(segments) <= 3, f"{md.name} has {len(segments)} kebab segments (max 3)"


@pytest.mark.parametrize("md", _all_md_files(), ids=lambda p: p.relative_to(REPO_ROOT).as_posix())
def test_rule_5_singular_for_concepts(md: Path) -> None:
    if md.stem in CONCEPT_FILES_MUST_BE_SINGULAR:
        assert not md.stem.endswith("s"), f"{md.name} is a concept file but uses plural form"


@pytest.mark.parametrize("md", _all_md_files(), ids=lambda p: p.relative_to(REPO_ROOT).as_posix())
def test_rule_6_no_coordinating_conjunctions(md: Path) -> None:
    assert "-and-" not in md.stem, f"{md.name} contains -and-"
    assert "-or-" not in md.stem, f"{md.name} contains -or-"


@pytest.mark.parametrize("md", _all_md_files(), ids=lambda p: p.relative_to(REPO_ROOT).as_posix())
def test_rule_7_no_mcp_prefix_in_execution(md: Path) -> None:
    if md.parent.name == "execution":
        assert not md.stem.startswith("mcp-"), (
            f"{md.relative_to(REPO_ROOT)}: starts with redundant 'mcp-' prefix"
        )
