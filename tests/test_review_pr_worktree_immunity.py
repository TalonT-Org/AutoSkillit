"""Real-Git regression coverage for local review annotation in linked worktrees."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from autoskillit.smoke_utils import annotate_pr_diff

pytestmark = [pytest.mark.medium]


def _git(repo: Path, *args: str) -> bytes:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    return result.stdout.strip()


def _write_and_commit(repo: Path, content: str, message: str) -> str:
    (repo / "tracked.txt").write_text(content)
    _git(repo, "add", "tracked.txt")
    _git(repo, "commit", "-m", message)
    return _git(repo, "rev-parse", "HEAD").decode()


def _optional_bytes(path: Path) -> bytes | None:
    return path.read_bytes() if path.exists() else None


def _primary_state(repo: Path) -> dict[str, bytes | None]:
    git_dir = repo / ".git"
    return {
        "develop_oid": _git(repo, "rev-parse", "refs/heads/develop"),
        "develop_loose_ref": _optional_bytes(git_dir / "refs" / "heads" / "develop"),
        "packed_refs": _optional_bytes(git_dir / "packed-refs"),
        "symbolic_head": _git(repo, "symbolic-ref", "HEAD"),
        "head_file": (git_dir / "HEAD").read_bytes(),
        "index_tree": _git(repo, "write-tree"),
        "index_file": (git_dir / "index").read_bytes(),
        "status": subprocess.run(
            ["git", "status", "--porcelain=v1"],
            cwd=repo,
            check=True,
            capture_output=True,
        ).stdout,
        "worktree_content": (repo / "tracked.txt").read_bytes(),
        "fetch_head": _optional_bytes(git_dir / "FETCH_HEAD"),
    }


def _network_stub(
    real_run,
    *,
    provider_head: str,
    provider_base: str,
    provider_merge_base: str,
):
    def run(args, **kwargs):
        if args[:2] != ["gh", "api"]:
            return real_run(args, **kwargs)
        endpoint = args[2]
        if "/compare/" in endpoint:
            payload = {
                "merge_base_commit": {"sha": provider_merge_base},
                "mergeBaseOid": provider_merge_base,
            }
        else:
            payload = {
                "head": {"sha": provider_head},
                "base": {
                    "sha": provider_base,
                    "repo": {"full_name": "Acme/Base"},
                },
                "headRefOid": provider_head,
                "baseRefOid": provider_base,
                "baseRepoFullName": "Acme/Base",
            }
        return subprocess.CompletedProcess(args, 0, json.dumps(payload).encode(), b"")

    return run


@pytest.mark.parametrize("local_base_state", ["older", "newer", "absent"])
def test_local_annotation_preserves_primary_linked_worktree_state(
    tmp_path: Path, local_base_state: str
) -> None:
    repo = tmp_path / "repo"
    review_worktree = tmp_path / "review-worktree"
    output_dir = tmp_path / "annotation-output"
    repo.mkdir()
    _git(repo, "init", "-b", "develop")
    _git(repo, "config", "user.email", "tests@example.invalid")
    _git(repo, "config", "user.name", "AutoSkillit Tests")
    ancestor_sha = _write_and_commit(repo, "ancestor\n", "ancestor")
    provider_base = _write_and_commit(repo, "provider base\n", "provider base")
    _git(repo, "branch", "review-head")
    _git(repo, "worktree", "add", str(review_worktree), "review-head")
    provider_head = _write_and_commit(review_worktree, "review change\n", "review head")

    if local_base_state == "older":
        _git(repo, "reset", "--hard", ancestor_sha)
        base_branch = "develop"
    elif local_base_state == "newer":
        _write_and_commit(repo, "newer local base\n", "newer local base")
        base_branch = "develop"
    else:
        base_branch = "missing-local-base"

    _git(repo, "remote", "add", "upstream", "git@github.com:Acme/Base.git")
    (repo / "tracked.txt").write_text("concurrent dirty primary content\n")
    before = _primary_state(repo)
    real_run = subprocess.run

    with patch(
        "subprocess.run",
        side_effect=_network_stub(
            real_run,
            provider_head=provider_head,
            provider_base=provider_base,
            provider_merge_base=provider_base,
        ),
    ):
        result = annotate_pr_diff(
            pr_number="777",
            cwd=str(review_worktree),
            output_dir=str(output_dir),
            base_branch=base_branch,
            mode="local",
        )

    assert result["review_mode"] == "local"
    assert _primary_state(repo) == before
    metrics = json.loads(Path(result["diff_metrics_path"]).read_text())
    assert metrics["_head_sha"] == provider_head
    assert metrics["_base_sha"] == provider_base
    assert metrics["_merge_base_sha"] == provider_base
    assert metrics["_base_repo_full_name"] == "Acme/Base"
