"""Git and merge-queue helpers for smoke_utils sub-modules."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import regex as re

from autoskillit.core import atomic_write

DIFF_SIZE_GATE_EXCLUDED_PATHSPECS: tuple[str, ...] = (".autoskillit/test-source-map.json",)
DIFF_SIZE_GATE_MAX_CHANGED_FILES = 160


def _diff_size_error(default_budget: str) -> dict[str, str]:
    """Return the structured fail-open result for an unavailable size measurement."""
    return {
        "size_verdict": "error",
        "added_lines": "0",
        "changed_files": "0",
        "budget": default_budget,
        "budget_source": "ingredient",
        "split_proposal_path": "",
    }


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
        return _diff_size_error(default_budget)
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

    try:
        budget = int(default_budget)
    except ValueError:
        return _diff_size_error(default_budget)
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


def check_bug_report_non_empty(workspace: str) -> dict[str, str]:
    """Return {"non_empty": "true"} if bug_report.json exists and is non-empty.

    Called by run_python from the check_summary step in smoke-test.yaml.
    The workspace argument is the root directory initialised by the setup step.
    """
    if not Path(workspace).is_absolute():
        raise ValueError(f"workspace must be absolute, got {workspace!r}")
    report = Path(workspace) / "bug_report.json"
    if not report.exists():
        return {"non_empty": "false"}
    try:
        data = json.loads(report.read_text())
        return {"non_empty": "true" if data else "false"}
    except (json.JSONDecodeError, OSError):
        return {"non_empty": "false"}


def compute_domain_partitions(
    batch_branch: str, base_branch: str, cwd: str, output_dir: str
) -> dict[str, str]:
    """Pre-compute domain partitions for open-integration-pr and write to disk.

    Called by run_python from the compute_domain_partitions step in merge-prs.yaml.
    Runs git diff to get changed files, partitions them by domain, and writes the
    result JSON to output_dir/domain_partitions.json.
    """
    import subprocess  # noqa: PLC0415

    from autoskillit.core import atomic_write  # noqa: PLC0415
    from autoskillit.execution import partition_files_by_domain  # noqa: PLC0415

    if not Path(output_dir).is_absolute():
        raise ValueError(f"output_dir must be absolute, got {output_dir!r}")

    result = subprocess.run(
        ["git", "diff", "--name-only", f"{base_branch}..{batch_branch}"],
        capture_output=True,
        text=True,
        check=True,
        cwd=cwd,
        timeout=60,
    )
    files = [f for f in result.stdout.strip().split("\n") if f]
    partitions = partition_files_by_domain(files)
    out_path = Path(output_dir) / "domain_partitions.json"
    atomic_write(out_path, json.dumps(partitions))
    return {"domain_partitions_path": str(out_path)}


def fetch_merge_queue_data(base_branch: str, cwd: str, output_dir: str) -> dict[str, str]:
    """Fetch and parse GitHub merge queue data server-side for analyze-prs.

    Called by run_python from the fetch_merge_queue_data step in merge-prs.yaml.
    Runs the GraphQL query used in analyze-prs Step 0.5 and parses the response
    with parse_merge_queue_response, writing the result to disk.
    """
    import subprocess  # noqa: PLC0415

    from autoskillit.core import atomic_write  # noqa: PLC0415
    from autoskillit.execution import parse_merge_queue_response  # noqa: PLC0415

    if not Path(output_dir).is_absolute():
        raise ValueError(f"output_dir must be absolute, got {output_dir!r}")

    repo_info = subprocess.run(
        ["gh", "repo", "view", "--json", "owner,name"],
        capture_output=True,
        text=True,
        check=True,
        cwd=cwd,
        timeout=60,
    )
    info = json.loads(repo_info.stdout)
    owner = info["owner"]["login"]
    repo = info["name"]

    query = (
        f'{{repository(owner: "{owner}", name: "{repo}") {{'
        f'mergeQueue(branch: "{base_branch}") {{'
        f"entries(first: 50) {{nodes {{position state pullRequest {{number title}}}}}}"
        f"}}}}}}"
    )
    graphql_result = subprocess.run(
        ["gh", "api", "graphql", "-f", f"query={query}"],
        capture_output=True,
        text=True,
        cwd=cwd,
        timeout=60,
    )
    if graphql_result.returncode != 0:
        entries: list = []
    else:
        try:
            data = json.loads(graphql_result.stdout)
        except (json.JSONDecodeError, ValueError):
            entries = []
        else:
            entries = parse_merge_queue_response(data)

    out_path = Path(output_dir) / "merge_queue_data.json"
    atomic_write(out_path, json.dumps(entries))
    return {"merge_queue_data_path": str(out_path)}


def detect_zero_changes(
    worktree_path: str,
    base_branch: str,
    write_evidence_override: str = "false",
) -> dict[str, str]:
    """Multi-signal change detection: commits, uncommitted changes, and write evidence.

    Always runs git verification — ``write_evidence_override`` is an OR-condition,
    not a bypass. Downstream consumers that inspect ``commit_count`` can detect
    contradictions between git evidence and the override flag (e.g., CodeX sandbox
    scenarios where ``.git/`` is read-only).
    """
    import subprocess  # noqa: PLC0415

    _override_active = str(write_evidence_override).lower() == "true"
    result: dict[str, str] = {}
    if _override_active:
        result["write_evidence_override"] = "true"

    try:
        rev_result = subprocess.run(
            ["git", "rev-list", "--count", f"{base_branch}..HEAD"],
            cwd=worktree_path,
            capture_output=True,
            text=True,
            check=True,
            timeout=60,
        )
        commit_count = rev_result.stdout.strip()
        result["commit_count"] = commit_count

        status_result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=worktree_path,
            capture_output=True,
            text=True,
            check=True,
            timeout=60,
        )
        has_uncommitted = bool(status_result.stdout.strip())
        result["has_uncommitted_changes"] = str(has_uncommitted).lower()

        git_has_changes = int(commit_count) > 0 or has_uncommitted
        result["has_changes"] = str(git_has_changes or _override_active).lower()

    except (subprocess.CalledProcessError, OSError, subprocess.TimeoutExpired, ValueError) as exc:
        result["has_changes"] = "true"
        result["commit_count"] = "error"
        result["has_uncommitted_changes"] = "error"
        result["error"] = str(exc)[:200]

    return result


def check_commits_ahead(cwd: str, base_branch: str) -> dict[str, str]:
    """Return {"has_commits": "true"/"false"} based on commits ahead of base_branch.

    Used by the check_has_commits recipe guard to short-circuit pipelines on
    zero-changes branches (feature already merged).
    """
    if not base_branch:
        raise ValueError("base_branch must be non-empty")
    import subprocess  # noqa: PLC0415

    result = subprocess.run(
        ["git", "rev-list", "--count", f"{base_branch}..HEAD"],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    count = int(result.stdout.strip())
    return {"has_commits": "true" if count > 0 else "false"}


def check_ref_state(worktree_path: str, branch: str) -> dict[str, str]:
    """Report whether ``branch`` is a clean fast-forward of the remote tracking ref.

    Authoritative re-check used by ``verify_ref_push_exhaustion`` after the
    ref-push budget is exhausted. Runs ``git merge-base --is-ancestor`` to test
    whether the local ``branch``'s tip has the remote tracking ref as a
    strict ancestor (i.e. local is ahead of or equal to remote — push is a
    no-op or trivially recoverable). Returns ``{"remote_is_ancestor": "true"|"false"}``.

    Used by the recipe to distinguish a benign ref-push exhaustion (local
    clean ahead of remote — push failure is recoverable) from a genuine
    divergence (local and remote have diverged — escalation required).
    Issue #4274, Part B Step 8.
    """
    import subprocess  # noqa: PLC0415

    # Resolve the local branch tip and its remote tracking ref.
    local_tip = subprocess.run(
        ["git", "rev-parse", "--verify", "--quiet", branch],
        cwd=worktree_path,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    if local_tip.returncode != 0:
        return {"remote_is_ancestor": "false"}
    remote_ref = f"origin/{branch}"
    remote_tip = subprocess.run(
        ["git", "rev-parse", "--verify", "--quiet", remote_ref],
        cwd=worktree_path,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    if remote_tip.returncode != 0:
        return {"remote_is_ancestor": "false"}
    ancestor = subprocess.run(
        [
            "git",
            "merge-base",
            "--is-ancestor",
            remote_tip.stdout.strip(),
            local_tip.stdout.strip(),
        ],
        cwd=worktree_path,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    return {"remote_is_ancestor": "true" if ancestor.returncode == 0 else "false"}


def close_issue_already_done(issue_url: str) -> dict[str, str]:
    """Remove in-progress label and close issue as already-implemented.

    Called by close_issue_already_done recipe step when check_has_commits
    detects zero commits ahead of base (feature already merged).
    """
    import subprocess  # noqa: PLC0415

    subprocess.run(
        ["gh", "issue", "edit", issue_url, "--remove-label", "in-progress"],
        capture_output=True,
        check=False,
        timeout=60,
    )
    subprocess.run(
        [
            "gh",
            "issue",
            "close",
            issue_url,
            "--comment",
            "Closing: branch has zero commits ahead of base — feature already implemented.",
        ],
        capture_output=True,
        check=False,
        timeout=60,
    )
    return {"closed": "true"}
