#!/usr/bin/env python3
"""Verify documentation counts match source code.

Scans docs/**/*.md and README.md for numeric claims about skill counts,
recipe counts, and tool counts, then verifies them against the source of truth.

Exit 0 if all counts match. Exit 1 with details if any mismatch is found.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

# Project root = parent of scripts/
PROJECT_ROOT = Path(__file__).resolve().parent.parent
SKILLS_DIR = PROJECT_ROOT / "src" / "autoskillit" / "skills"
SKILLS_EXTENDED_DIR = PROJECT_ROOT / "src" / "autoskillit" / "skills_extended"
RECIPES_DIR = PROJECT_ROOT / "src" / "autoskillit" / "recipes"
TYPES_FILE = PROJECT_ROOT / "src" / "autoskillit" / "core" / "_type_constants.py"


def count_skills() -> int:
    """Count public skills in skills/ and skills_extended/.

    Only counts skills whose SKILL.md begins with YAML frontmatter (``---``),
    matching the behaviour of DefaultSkillResolver().list_all() which excludes
    internal bootstrap documents such as sous-chef.
    """
    count = 0
    for skills_dir in (SKILLS_DIR, SKILLS_EXTENDED_DIR):
        if skills_dir.is_dir():
            for d in skills_dir.iterdir():
                skill_md = d / "SKILL.md"
                if d.is_dir() and skill_md.exists():
                    text = skill_md.read_text(encoding="utf-8")
                    if text.startswith("---\n"):
                        count += 1
    return count


def count_recipes() -> int:
    """Count *.yaml files directly in recipes/ (not subdirectories)."""
    return sum(1 for f in RECIPES_DIR.glob("*.yaml") if f.is_file())


def count_tools() -> tuple[int, int]:
    """Parse types.py to extract kitchen-tagged and free-range tool counts.

    Returns (gated_count, ungated_count) where:
    - gated_count = GATED_TOOLS + HEADLESS_TOOLS (all kitchen-tagged tools)
    - ungated_count = FREE_RANGE_TOOLS (always-visible tools)
    """
    content = TYPES_FILE.read_text(encoding="utf-8")

    # Count quoted strings in GATED_TOOLS frozenset (kitchen-only tools)
    gated_match = re.search(
        r"GATED_TOOLS:\s*frozenset\[str\]\s*=\s*frozenset\(\s*\{(.*?)\}\s*\)",
        content,
        re.DOTALL,
    )
    gated_only = len(re.findall(r'"([^"]+)"', gated_match.group(1))) if gated_match else 0

    # HEADLESS_TOOLS carries both kitchen + headless tags — counts toward kitchen total
    headless_match = re.search(
        r"HEADLESS_TOOLS:\s*frozenset\[str\]\s*=\s*frozenset\(\s*\{(.*?)\}\s*\)",
        content,
        re.DOTALL,
    )
    headless = len(re.findall(r'"([^"]+)"', headless_match.group(1))) if headless_match else 0

    # FREE_RANGE_TOOLS are always visible (ungated)
    free_range_match = re.search(
        r"FREE_RANGE_TOOLS:\s*frozenset\[str\]\s*=\s*frozenset\(\s*\{(.*?)\}\s*\)",
        content,
        re.DOTALL,
    )
    free_range = (
        len(re.findall(r'"([^"]+)"', free_range_match.group(1))) if free_range_match else 0
    )

    gated = gated_only + headless  # all kitchen-tagged tools
    ungated = free_range  # always-visible tools
    return gated, ungated


# Patterns that indicate a self-correcting reference (reader directed to
# authoritative source). Matches containing these are exempt from checking.
SELF_CORRECTING = re.compile(
    r"autoskillit\s+skills\s+list|autoskillit\s+recipes\s+list",
    re.IGNORECASE,
)

# Patterns that indicate a sub-family count rather than a global skill total.
# When any of these appear on the same line as an "X skills" claim, the
# claim describes a sub-family (arch-lens, exp-lens, Tier 1/2/3, audit suite,
# rectify doctrine, …) and is exempt from the global-total check.
SUBFAMILY_CONTEXT = re.compile(
    r"arch-lens|exp-lens|Tier\s+[123]|skills_extended"
    r"|src/autoskillit/skills/|audit\s+suite|rectify\s+doctrine|sub-family",
    re.IGNORECASE,
)

# Patterns to find numeric claims in docs
SKILL_COUNT_PAT = re.compile(r"(\d+)\s+(?:bundled\s+)?skills")
RECIPE_COUNT_PAT = re.compile(r"(\d+)\s+recipes")
TOOL_COUNT_PAT = re.compile(r"(\d+)\s+(?:MCP\s+)?tools")

# Specific tier patterns — detect lines that talk about gated/ungated tool subsets
GATED_PAT = re.compile(r"(\d+)\s+(?:kitchen[- ]?(?:gated|tools)|gated)", re.IGNORECASE)
UNGATED_PAT = re.compile(
    r"(?:Always\s+visible|Tier\s+0|ungated)[^(]*\((\d+)\s+tools\)", re.IGNORECASE
)
# Broader check: does the line discuss a tier/subset (not total tools)?
TIER_CONTEXT_PAT = re.compile(
    r"kitchen|gated|always\s+visible|tier\s+[012]|ungated|pipeline\s+tools\s+hidden",
    re.IGNORECASE,
)


def scan_docs() -> list[str]:
    """Scan doc files for count mismatches. Returns list of error messages."""
    actual_skills = count_skills()
    actual_recipes = count_recipes()
    gated, ungated = count_tools()
    total_tools = gated + ungated

    errors: list[str] = []

    doc_files = list((PROJECT_ROOT / "docs").rglob("*.md"))
    readme = PROJECT_ROOT / "README.md"
    if readme.exists():
        doc_files.append(readme)

    for doc_file in sorted(doc_files):
        rel = doc_file.relative_to(PROJECT_ROOT)
        for lineno, line in enumerate(doc_file.read_text(encoding="utf-8").splitlines(), 1):
            # Skip self-correcting references
            if SELF_CORRECTING.search(line):
                continue

            # Check skill counts (skip sub-family contexts like Tier 1/2/3,
            # arch-lens, exp-lens, audit suite — those are not global totals)
            if not SUBFAMILY_CONTEXT.search(line):
                for m in SKILL_COUNT_PAT.finditer(line):
                    claimed = int(m.group(1))
                    if claimed != actual_skills and claimed > 1:
                        errors.append(
                            f"{rel}:{lineno}: claims {claimed} skills, actual is {actual_skills}"
                        )

            # Check recipe counts
            for m in RECIPE_COUNT_PAT.finditer(line):
                claimed = int(m.group(1))
                if claimed != actual_recipes and claimed > 1:
                    errors.append(
                        f"{rel}:{lineno}: claims {claimed} recipes, actual is {actual_recipes}"
                    )

            # Check total tool counts
            for m in TOOL_COUNT_PAT.finditer(line):
                claimed = int(m.group(1))
                # Skip if this line discusses a tier/subset (handled below)
                if TIER_CONTEXT_PAT.search(line):
                    continue
                if claimed != total_tools and claimed > 1:
                    errors.append(
                        f"{rel}:{lineno}: claims {claimed} tools, actual is {total_tools}"
                    )

            # Check gated tool counts
            for m in GATED_PAT.finditer(line):
                claimed = int(m.group(1))
                if claimed != gated:
                    errors.append(
                        f"{rel}:{lineno}: claims {claimed} gated tools, actual is {gated}"
                    )

            # Check ungated tool counts
            for m in UNGATED_PAT.finditer(line):
                claimed = int(m.group(1))
                if claimed != ungated:
                    errors.append(
                        f"{rel}:{lineno}: claims {claimed} ungated tools, actual is {ungated}"
                    )

    return errors


def main() -> int:
    errors = scan_docs()
    if errors:
        print("Documentation count mismatches found:\n")
        for err in errors:
            print(f"  {err}")
        print(f"\nTotal: {len(errors)} mismatch(es)")
        return 1
    print("All documentation counts match source code.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
