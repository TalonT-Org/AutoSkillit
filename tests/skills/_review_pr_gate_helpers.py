"""Isolated checkout, annotation artifacts, and GitHub stub for review-pr gate tests."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path
from typing import Any

from autoskillit.recipe.io import builtin_scripts_dir

GATE_SCRIPT = builtin_scripts_dir() / "review_pr_gate.sh"


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)
    return result.stdout.strip()


def artifact_record(path: Path) -> dict[str, str | int]:
    data = path.read_bytes()
    return {
        "basename": path.name,
        "byte_length": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }


def make_gate_case(tmp_path: Path, *, mode: str = "local", gate: bool = True) -> dict[str, Any]:
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.name", "Test")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "remote", "add", "origin", "https://github.com/Acme/Base.git")
    (repo / "tracked.txt").write_text("base\n")
    git(repo, "add", "tracked.txt")
    git(repo, "commit", "-qm", "base")
    git(repo, "branch", "base")
    (repo / "tracked.txt").write_text("head\n")
    git(repo, "commit", "-qam", "head")

    output_dir = tmp_path / "review-output"
    output_dir.mkdir()
    annotated = output_dir / "annotated_diff_7.txt"
    ranges = output_dir / "hunk_ranges_7.json"
    valid_lines = output_dir / "valid_lines_7.json"
    metrics_path = output_dir / "metrics_7.json"
    annotated.write_text("metadata\n[L1]+old-generation\n\n")
    ranges.write_text('{"tracked.txt":[[1,1]]}\n')
    valid_lines.write_text('{"tracked.txt":[1]}\n')

    head_sha = git(repo, "rev-parse", "HEAD")
    base_sha = git(repo, "rev-parse", "base")
    merge_base_sha = git(repo, "merge-base", base_sha, head_sha)
    profile = (
        {
            "comparison": "merge_base_to_head",
            "context_lines": 3,
            "external_diff": False,
            "kind": "local_git",
            "profile_id": "local_git_pinned_v1",
            "rename_detection": "50%",
            "text_conversion": False,
        }
        if mode == "local"
        else {
            "comparison": "pull_request",
            "context_lines": 3,
            "external_diff": False,
            "kind": "github_pr",
            "profile_id": "github_pr_diff_v1",
            "rename_detection": "provider_default",
            "text_conversion": False,
        }
    )
    metrics: dict[str, Any] = {
        "_head_sha": head_sha,
        "_base_sha": base_sha,
        "_merge_base_sha": merge_base_sha,
        "_base_repo_full_name": "Acme/Base",
        "generation_id": "generation-1",
        "diff_sha256": "a" * 64,
        "diff_byte_length": 17,
        "review_mode": mode,
        "diff_source": profile,
        "artifacts": {
            "annotated_diff": artifact_record(annotated),
            "hunk_ranges": artifact_record(ranges),
            "valid_lines": artifact_record(valid_lines),
        },
        "dispatch_agents": ["tests", "cohesion"],
        "run_overengineering_audits": gate,
    }
    metrics_path.write_text(json.dumps(metrics, sort_keys=True) + "\n")

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_gh = fake_bin / "gh"
    fake_gh.write_text(
        "#!/bin/sh\n"
        '[ "$PWD" = "$FAKE_GH_EXPECTED_CWD" ] || exit 91\n'
        '[ "$(git remote get-url origin)" = "$FAKE_GH_EXPECTED_REMOTE" ] || exit 92\n'
        'printf "%s\\n" "$*" >> "$FAKE_GH_CALL_LOG"\n'
        'if [ "${FAKE_GH_MISSING_AUTHORITY:-}" = 1 ]; then printf "{}\\n"; exit 0; fi\n'
        'case "$*" in\n'
        '  *"/compare/"*) printf "%s\\n" "$FAKE_GH_MERGE_BASE_SHA" ;;\n'
        '  *) printf \'{"headRefOid":"%s","baseRefOid":"%s",'
        '"baseRepoFullName":"Acme/Base"}\\n\' '
        '"$FAKE_GH_HEAD_SHA" "$FAKE_GH_BASE_SHA" ;;\n'
        "esac\n",
        encoding="utf-8",
    )
    fake_gh.chmod(0o755)

    env = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "FAKE_GH_EXPECTED_CWD": str(repo),
        "FAKE_GH_EXPECTED_REMOTE": "https://github.com/Acme/Base.git",
        "FAKE_GH_CALL_LOG": str(tmp_path / "gh-calls.txt"),
        "FAKE_GH_HEAD_SHA": head_sha,
        "FAKE_GH_BASE_SHA": base_sha,
        "FAKE_GH_MERGE_BASE_SHA": merge_base_sha,
    }
    return {
        "repo": repo,
        "env": env,
        "metrics": metrics,
        "metrics_path": metrics_path,
        "annotated": annotated,
        "ranges": ranges,
        "valid_lines": valid_lines,
        "output_dir": output_dir,
        "gh_call_log": tmp_path / "gh-calls.txt",
        "mode": mode,
    }


def write_metrics(case: dict[str, Any]) -> None:
    case["metrics_path"].write_text(json.dumps(case["metrics"], sort_keys=True) + "\n")


def snapshot(case: dict[str, Any], *extra_args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "bash",
            str(GATE_SCRIPT),
            "snapshot",
            str(case["output_dir"]),
            str(case["repo"]),
            case["mode"],
            "7",
            str(case["metrics_path"]),
            str(case["annotated"]),
            str(case["ranges"]),
            str(case["valid_lines"]),
            *extra_args,
        ],
        cwd=case["repo"],
        env=case["env"],
        capture_output=True,
        text=True,
    )


def revalidate(case: dict[str, Any], authority_path: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(GATE_SCRIPT), "revalidate", str(authority_path)],
        cwd=case["repo"],
        env=case["env"],
        capture_output=True,
        text=True,
    )
