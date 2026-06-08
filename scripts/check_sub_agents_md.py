#!/usr/bin/env python3
"""Validate sub-AGENTS.md file tables cover all .py files in their directories.

Pre-commit hook (validate-only). Exits 1 with structured messages when a .py
file exists in a directory whose AGENTS.md does not mention it.
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_ROOT = PROJECT_ROOT / "src" / "autoskillit"
TESTS_ROOT = PROJECT_ROOT / "tests"

SRC_EXPECTED = [
    "core/types/AGENTS.md",
    "core/runtime/AGENTS.md",
    "execution/headless/AGENTS.md",
    "execution/process/AGENTS.md",
    "execution/session/AGENTS.md",
    "execution/merge_queue/AGENTS.md",
    "recipe/rules/AGENTS.md",
    "recipe/rules/campaign/AGENTS.md",
    "recipe/rules/ci/AGENTS.md",
    "recipe/rules/dataflow/AGENTS.md",
    "recipe/rules/graph/AGENTS.md",
    "server/tools/AGENTS.md",
    "cli/doctor/AGENTS.md",
    "cli/fleet/AGENTS.md",
    "cli/session/AGENTS.md",
    "cli/ui/AGENTS.md",
    "cli/update/AGENTS.md",
    "hooks/guards/AGENTS.md",
    "hooks/formatters/AGENTS.md",
    "AGENTS.md",
    "core/AGENTS.md",
    "config/AGENTS.md",
    "pipeline/AGENTS.md",
    "execution/AGENTS.md",
    "workspace/AGENTS.md",
    "planner/AGENTS.md",
    "recipe/AGENTS.md",
    "migration/AGENTS.md",
    "fleet/AGENTS.md",
    "cli/AGENTS.md",
    "hooks/AGENTS.md",
    "agents/AGENTS.md",
]

TESTS_EXPECTED = [
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


def check_coverage(root: Path, expected: list[str]) -> list[str]:
    """Check that each AGENTS.md in expected mentions all .py files in its directory.

    Returns a list of failure messages (empty if all coverage is complete).
    """
    failures: list[str] = []
    for rel_path in expected:
        agents_md = root / rel_path
        if not agents_md.exists():
            failures.append(f"{rel_path}: AGENTS.md not found")
            continue
        content = agents_md.read_text(encoding="utf-8")
        directory = agents_md.parent
        for py_file in directory.glob("*.py"):
            if py_file.name == "__init__.py":
                if "`__init__.py`" not in content:
                    failures.append(f"{rel_path}: missing `__init__.py` in file table")
            else:
                if f"`{py_file.name}`" not in content:
                    failures.append(f"{rel_path}: missing {py_file.name}")
    return failures


def main() -> int:
    src_failures = check_coverage(SRC_ROOT, SRC_EXPECTED)
    tests_failures = check_coverage(TESTS_ROOT, TESTS_EXPECTED)
    all_failures = src_failures + tests_failures
    if all_failures:
        print("sub-AGENTS.md file table gaps found:\n")
        for f in all_failures:
            print(f"  {f}")
        print(f"\nTotal: {len(all_failures)} gap(s)")
        print("\nTo fix: add the missing file(s) to the AGENTS.md file table in the")
        print("directory where the .py file(s) were added.")
        return 1
    print("All sub-AGENTS.md file tables are complete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
