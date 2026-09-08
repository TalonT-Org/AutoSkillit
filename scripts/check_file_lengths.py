#!/usr/bin/env python3
"""Pre-commit hook: enforce REQ-CNST-010's diff-scoped 750-line hard cap.

Every non-test file under src/autoskillit/ must be <=750 lines, or <=1000 with a
REQ-CNST-010-E<N> entry in _LINE_LIMIT_EXEMPTIONS whose `predicate` callable
verifies True. Test files are exempt by design and are excluded by both callers'
file-selection: a pre-commit `files:` regex (added in a later part) and
test_file_length_diff_gate.py's own path filter here.

Scoped to the files passed as arguments -- a future pre-commit hook only
invokes this with the commit's own diff, so this script never walks the full
tree. tests/arch/test_file_length_diff_gate.py reuses check_file() below
against the PR's changed-file set for the CI-side gate.

This script itself never imports tests._test_filter.git_changed_files or
resolves a base ref -- git-diff scoping is entirely the caller's
responsibility (pre-commit's own file-selection locally, via `files:` +
default `pass_filenames`; test_file_length_diff_gate.py's own
_resolve_base_ref()/git_changed_files call in CI). Do not import the
git-diff helper into this script to "consolidate" the two callers -- that
would introduce a coupling this design deliberately keeps absent.
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_ROOT = PROJECT_ROOT / "src" / "autoskillit"

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# _LINE_LIMIT_EXEMPTIONS is the single existing rationale ledger for files
# permitted past the hard cap; reused here so both gates share one source of truth.
from tests.arch._subpackage_isolation_line_limits import (
    _LINE_LIMIT_EXEMPTIONS,
    LineLimitExemption,
)

HARD_CAP = 750
ABSOLUTE_CAP = 1000


def check_file(path: Path) -> str | None:
    """Return a violation message for `path`, or None when it complies."""
    try:
        rel = str(path.resolve().relative_to(SRC_ROOT))
    except ValueError:
        return None
    line_count = len(path.read_text(encoding="utf-8").splitlines())
    if line_count <= HARD_CAP:
        return None
    exemption: LineLimitExemption | None = _LINE_LIMIT_EXEMPTIONS.get(rel)
    if exemption is None:
        return (
            f"{rel}: {line_count} lines exceeds the {HARD_CAP}-line hard cap "
            f"(REQ-CNST-010) -- decompose the file, or add a REQ-CNST-010-E<N> "
            f"entry to _LINE_LIMIT_EXEMPTIONS with a machine-checkable predicate"
        )
    if exemption.predicate is None:
        rule_id = exemption.rationale.split(":", 1)[0]
        return (
            f"{rel}: {line_count} lines -- exemption {rule_id} has no "
            f"machine-checkable predicate and is voided under REQ-CNST-010's "
            f"diff-scoped gate; decompose the file or add a verifiable predicate"
        )
    if exemption.limit > ABSOLUTE_CAP:
        return (
            f"{rel}: exemption ceiling {exemption.limit} exceeds the "
            f"{ABSOLUTE_CAP}-line absolute maximum permitted by REQ-CNST-010"
        )
    if line_count > exemption.limit:
        return f"{rel}: {line_count} lines exceeds its exemption ceiling of {exemption.limit}"
    if not exemption.predicate():
        rule_id = exemption.rationale.split(":", 1)[0]
        return (
            f"{rel}: {line_count} lines -- exemption predicate for {rule_id} "
            f"returned False; the justification no longer holds"
        )
    return None


def main(argv: list[str]) -> int:
    """Check the supplied files and return a shell-compatible status code."""
    violations: list[str] = []
    for arg in argv:
        path = Path(arg)
        if not path.is_file():
            continue
        message = check_file(path)
        if message:
            violations.append(message)
    if violations:
        print("File-length violations (REQ-CNST-010):\n")
        for violation in violations:
            print(f"  {violation}")
        print(f"\nTotal: {len(violations)} violation(s)")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
