#!/usr/bin/env python3
"""Validate sub-CLAUDE.md file tables cover all .py files in their directories.

Pre-commit hook (validate-only). Exits 1 with structured messages when a .py
file exists in a directory whose CLAUDE.md does not mention it.
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_ROOT = PROJECT_ROOT / "src" / "autoskillit"
TESTS_ROOT = PROJECT_ROOT / "tests"

SRC_EXPECTED = [
    "core/types/CLAUDE.md",
    "core/runtime/CLAUDE.md",
    "execution/headless/CLAUDE.md",
    "execution/process/CLAUDE.md",
    "execution/session/CLAUDE.md",
    "execution/merge_queue/CLAUDE.md",
    "recipe/rules/CLAUDE.md",
    "server/tools/CLAUDE.md",
    "cli/doctor/CLAUDE.md",
    "cli/fleet/CLAUDE.md",
    "cli/session/CLAUDE.md",
    "cli/ui/CLAUDE.md",
    "cli/update/CLAUDE.md",
    "hooks/guards/CLAUDE.md",
    "hooks/formatters/CLAUDE.md",
    "CLAUDE.md",
    "core/CLAUDE.md",
    "config/CLAUDE.md",
    "pipeline/CLAUDE.md",
    "execution/CLAUDE.md",
    "workspace/CLAUDE.md",
    "planner/CLAUDE.md",
    "recipe/CLAUDE.md",
    "migration/CLAUDE.md",
    "fleet/CLAUDE.md",
    "cli/CLAUDE.md",
    "hooks/CLAUDE.md",
]

TESTS_EXPECTED = [
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


def check_coverage(root: Path, expected: list[str]) -> list[str]:
    """Check that each CLAUDE.md in expected mentions all .py files in its directory.

    Returns a list of failure messages (empty if all coverage is complete).
    """
    failures: list[str] = []
    for rel_path in expected:
        claude_md = root / rel_path
        if not claude_md.exists():
            continue
        content = claude_md.read_text(encoding="utf-8")
        directory = claude_md.parent
        for py_file in directory.glob("*.py"):
            if py_file.name == "__init__.py":
                if "`__init__.py`" not in content:
                    failures.append(f"{rel_path}: missing `__init__.py` in file table")
            else:
                if py_file.name not in content:
                    failures.append(f"{rel_path}: missing {py_file.name}")
    return failures


def main() -> int:
    src_failures = check_coverage(SRC_ROOT, SRC_EXPECTED)
    tests_failures = check_coverage(TESTS_ROOT, TESTS_EXPECTED)
    all_failures = src_failures + tests_failures
    if all_failures:
        print("sub-CLAUDE.md file table gaps found:\n")
        for f in all_failures:
            print(f"  {f}")
        print(f"\nTotal: {len(all_failures)} gap(s)")
        print("\nTo fix: add the missing file(s) to the CLAUDE.md file table in the")
        print("directory where the .py file(s) were added.")
        return 1
    print("All sub-CLAUDE.md file tables are complete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
