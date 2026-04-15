"""Verify every local markdown link resolves and no old flat-layout link survives.

The four `spectral-init` archive PR pairs in
``docs/examples/research-pipeline.md`` are checked against an explicit
allowlist so the example never silently loses one.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
DOCS_DIR = REPO_ROOT / "docs"
ROOT_README = REPO_ROOT / "README.md"
CLAUDE_MD = REPO_ROOT / "CLAUDE.md"

LINK_RE = re.compile(r"\[[^\]]*\]\(([^)]+)\)")

# Old flat-layout paths that must never appear in any link.
FORBIDDEN_OLD_PATHS = {
    "docs/recipes.md",
    "docs/sub-recipe-composition.md",
    "docs/skill-visibility.md",
    "docs/project-local-overrides.md",
    "docs/subset-categories.md",
    "docs/architecture.md",
    "docs/mcp-tool-access.md",
    "docs/hooks-and-safety.md",
    "docs/cli-reference.md",
    "docs/developer/session-diagnostics.md",
    # In-subdirectory bare names that map to old flat layout:
    "recipes.md",
    "sub-recipe-composition.md",
    "skill-visibility.md",
    "project-local-overrides.md",
    "subset-categories.md",
    "mcp-tool-access.md",
    "hooks-and-safety.md",
    "cli-reference.md",
    "session-diagnostics.md",
}

# Spectral-init research/archive PR allowlist for the examples doc. Each entry
# must appear at least once in research-pipeline.md.
SPECTRAL_INIT_ALLOWLIST = [
    "https://github.com/TalonT-Org/spectral-init/pull/233",
    "https://github.com/TalonT-Org/spectral-init/pull/234",
    "https://github.com/TalonT-Org/spectral-init/pull/238",
    "https://github.com/TalonT-Org/spectral-init/pull/239",
    "https://github.com/TalonT-Org/spectral-init/pull/256",
    "https://github.com/TalonT-Org/spectral-init/pull/257",
    "https://github.com/TalonT-Org/spectral-init/pull/263",
    "https://github.com/TalonT-Org/spectral-init/pull/264",
    "https://github.com/TalonT-Org/spectral-init/tree/main/research/2026-03-baseline",
    "https://github.com/TalonT-Org/spectral-init/tree/main/research/2026-03-comparator",
    "https://github.com/TalonT-Org/spectral-init/tree/main/research/2026-03-variance",
    "https://github.com/TalonT-Org/spectral-init/tree/main/research/2026-04-sensitivity",
]


def _all_md_files() -> list[Path]:
    files: list[Path] = list(DOCS_DIR.rglob("*.md"))
    if ROOT_README.exists():
        files.append(ROOT_README)
    return sorted(files)


def _link_targets(md: Path) -> list[str]:
    return LINK_RE.findall(md.read_text(encoding="utf-8"))


@pytest.mark.parametrize("md", _all_md_files(), ids=lambda p: p.relative_to(REPO_ROOT).as_posix())
def test_local_links_resolve(md: Path) -> None:
    failures: list[str] = []
    for raw in _link_targets(md):
        if raw.startswith(("http://", "https://", "mailto:", "#")):
            continue
        target = raw.split("#", 1)[0]
        if not target or not target.endswith(".md"):
            continue
        resolved = (md.parent / target).resolve()
        if not resolved.exists():
            failures.append(f"broken link {raw} -> {resolved}")
    assert not failures, f"{md.relative_to(REPO_ROOT)}:\n  " + "\n  ".join(failures)


@pytest.mark.parametrize("md", _all_md_files(), ids=lambda p: p.relative_to(REPO_ROOT).as_posix())
def test_no_old_flat_layout_links(md: Path) -> None:
    bad: list[str] = []
    for raw in _link_targets(md):
        if raw.startswith(("http://", "https://", "mailto:", "#")):
            continue
        target = raw.split("#", 1)[0]
        # Strip leading ./ and ../ to compare against forbidden bare names.
        normalised = target.lstrip("./")
        if normalised in FORBIDDEN_OLD_PATHS:
            bad.append(raw)
    assert not bad, f"{md.relative_to(REPO_ROOT)} links to forbidden old paths: {bad}"


def test_spectral_init_allowlist_present() -> None:
    examples = DOCS_DIR / "examples" / "research-pipeline.md"
    if not examples.exists():
        pytest.skip("research-pipeline.md not present")
    text = examples.read_text(encoding="utf-8")
    missing = [url for url in SPECTRAL_INIT_ALLOWLIST if url not in text]
    assert not missing, f"research-pipeline.md missing allowlisted URLs: {missing}"
