#!/usr/bin/env python3
"""Enforce REQ-CNST-010's diff-scoped 750-line hard cap.

Every non-test file under src/autoskillit/ must be <=750 lines, or <=1000 with a
REQ-CNST-010-E<N> entry in _LINE_LIMIT_EXEMPTIONS whose `predicate` callable
verifies True. Test files are exempt by design.

Positional arguments are checked directly; test_file_length_diff_gate.py uses
that mode for the CI-side changed-file set. ``--staged`` obtains added, copied,
modified, and renamed source paths from Git's cached index for the local hook.
Both modes route files through check_file() and never walk the full tree.

The pre-commit hook cannot use its filename arguments: the project's mandatory
``pre-commit run --all-files`` command supplies every matching repository file.
The hook therefore disables filename passing and delegates exact staged-file
selection to ``--staged``.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_ROOT = PROJECT_ROOT / "src" / "autoskillit"

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

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
    if argv == ["--staged"]:
        result = subprocess.run(
            ["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z"],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            detail = result.stderr.strip() or "git diff --cached failed without a diagnostic"
            print(f"Unable to select staged files: {detail}", file=sys.stderr)
            return 1

        source_root = SRC_ROOT.resolve()
        paths: list[Path] = []
        for name in result.stdout.split("\0"):
            if not name:
                continue
            path = (PROJECT_ROOT / name).resolve()
            try:
                path.relative_to(source_root)
            except ValueError:
                continue
            if path.suffix == ".py":
                paths.append(path)
        argv = [str(path) for path in paths]

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
