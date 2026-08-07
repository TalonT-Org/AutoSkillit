"""Deterministic diff-size gate for implementation recipes."""

from __future__ import annotations

import subprocess
from pathlib import Path

import regex as re

from autoskillit.core import atomic_write

DIFF_SIZE_GATE_EXCLUDED_PATHSPECS: tuple[str, ...] = (".autoskillit/test-source-map.json",)
DIFF_SIZE_GATE_MAX_CHANGED_FILES = 160


def check_diff_size(
    worktree_path: str = "",
    base_branch: str = "",
    plan_path: str = "",
    default_budget: str = "6000",
    output_dir: str = "",
) -> dict[str, str]:
    """Measure the current part's own diff against an absolute worktree path."""
    worktree = Path(worktree_path)
    if not worktree.is_absolute():
        raise ValueError(f"worktree_path must be absolute, got {worktree_path!r}")
    if plan_path and not Path(plan_path).is_absolute():
        raise ValueError(f"plan_path must be absolute, got {plan_path!r}")
    if output_dir and not Path(output_dir).is_absolute():
        raise ValueError(f"output_dir must be absolute, got {output_dir!r}")

    wp = str(worktree)
    fork_result = subprocess.run(
        ["git", "merge-base", base_branch, "HEAD"],
        cwd=wp,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if fork_result.returncode != 0:
        return {
            "size_verdict": "error",
            "added_lines": "0",
            "changed_files": "0",
            "budget": default_budget,
            "budget_source": "ingredient",
            "split_proposal_path": "",
        }
    fork_point = fork_result.stdout.strip()

    exclude_args: list[str] = []
    for pathspec in DIFF_SIZE_GATE_EXCLUDED_PATHSPECS:
        exclude_args.extend(["--", ".", f":(exclude){pathspec}"])

    diff_cmd = ["git", "diff", "--numstat", "-z", fork_point]
    diff_cmd.extend(exclude_args or ["--", "."])
    diff_result = subprocess.run(
        diff_cmd,
        cwd=wp,
        capture_output=True,
        timeout=60,
    )
    raw = diff_result.stdout
    added_lines = 0
    changed_files = 0

    if raw:
        entries = raw.split(b"\0")
        i = 0
        while i < len(entries):
            entry = entries[i]
            if not entry:
                i += 1
                continue
            parts = entry.split(b"\t", 2)
            if len(parts) < 3:
                i += 1
                continue
            add_str, _del_str, path_or_empty = parts
            changed_files += 1
            if add_str != b"-":
                try:
                    added_lines += int(add_str)
                except ValueError:
                    pass
            i += 3 if path_or_empty == b"" else 1

    untracked_result = subprocess.run(
        ["git", "ls-files", "--others", "--exclude-standard", "-z"],
        cwd=wp,
        capture_output=True,
        timeout=30,
    )
    excluded_set = set(DIFF_SIZE_GATE_EXCLUDED_PATHSPECS)
    if untracked_result.stdout:
        for entry in untracked_result.stdout.split(b"\0"):
            if not entry:
                continue
            rel_path = entry.decode("utf-8", errors="replace")
            if rel_path in excluded_set:
                continue
            changed_files += 1
            try:
                content = (worktree / rel_path).read_bytes()
                if b"\x00" not in content[:8192]:
                    added_lines += content.count(b"\n")
                    if content and not content.endswith(b"\n"):
                        added_lines += 1
            except OSError:
                pass

    budget = int(default_budget)
    budget_source = "ingredient"
    if plan_path:
        try:
            plan_text = Path(plan_path).read_text(encoding="utf-8")
            match = re.search(r"^size_budget\s*=\s*(\d+)\s*$", plan_text, re.MULTILINE)
            if match:
                budget = int(match.group(1))
                budget_source = "plan"
        except OSError:
            pass

    file_limit = DIFF_SIZE_GATE_MAX_CHANGED_FILES
    over = added_lines > budget or changed_files > file_limit
    split_proposal_path = ""
    if over and output_dir:
        report_lines = [
            "# Scope Breach Report",
            "",
            f"**Added lines**: {added_lines} (budget: {budget}, source: {budget_source})",
            f"**Changed files**: {changed_files} (limit: {file_limit})",
            "",
            "The implementation exceeds the size budget. The pipeline will re-enter",
            "the plan step. The re-plan must either:",
            "- Compress the implementation to fit within the budget, or",
            "- Split into multiple parts, each within budget.",
        ]
        report_path = Path(output_dir) / "scope_breach_report.md"
        atomic_write(report_path, "\n".join(report_lines))
        split_proposal_path = str(report_path)

    return {
        "size_verdict": "over_budget" if over else "within_budget",
        "added_lines": str(added_lines),
        "changed_files": str(changed_files),
        "budget": str(budget),
        "budget_source": budget_source,
        "split_proposal_path": split_proposal_path,
    }
