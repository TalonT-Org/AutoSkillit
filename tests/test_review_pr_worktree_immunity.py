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
        if args[:4] == ["git", "remote", "get-url", "upstream"]:
            return subprocess.CompletedProcess(args, 0, b"git@github.com:Acme/Base.git\n", b"")
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


def _annotation_args(topology: dict[str, Path | str]) -> dict[str, str]:
    return {
        "pr_number": "777",
        "cwd": str(topology["review_worktree"]),
        "output_dir": str(topology["output_dir"]),
        "base_branch": str(topology["base_branch"]),
        "mode": "local",
    }


def _assert_provider_metrics(result: dict[str, str], topology: dict[str, Path | str]) -> None:
    metrics = json.loads(Path(result["diff_metrics_path"]).read_text())
    assert metrics["_head_sha"] == topology["provider_head"]
    assert metrics["_base_sha"] == topology["provider_base"]
    assert metrics["_merge_base_sha"] == topology["provider_merge_base"]
    assert metrics["_base_repo_full_name"] == "Acme/Base"


def _build_linked_review_topology(tmp_path: Path, local_base_state: str) -> dict[str, Path | str]:
    provider_repo = tmp_path / "provider"
    repo = tmp_path / "repo"
    review_worktree = tmp_path / "review-worktree"
    output_dir = tmp_path / "annotation-output"
    provider_repo.mkdir()
    _git(provider_repo, "init", "-b", "develop")
    _git(provider_repo, "config", "user.email", "tests@example.invalid")
    _git(provider_repo, "config", "user.name", "AutoSkillit Tests")
    ancestor_sha = _write_and_commit(provider_repo, "ancestor\n", "ancestor")
    _git(provider_repo, "branch", "review-head")
    provider_base = _write_and_commit(provider_repo, "provider base\n", "provider base")
    _git(provider_repo, "switch", "review-head")
    provider_head = _write_and_commit(provider_repo, "review change\n", "review head")

    subprocess.run(
        [
            "git",
            "clone",
            "--depth",
            "2",
            "--single-branch",
            "--branch",
            "review-head",
            f"file://{provider_repo}",
            str(repo),
        ],
        check=True,
        capture_output=True,
    )
    _git(repo, "config", "user.email", "tests@example.invalid")
    _git(repo, "config", "user.name", "AutoSkillit Tests")
    _git(repo, "remote", "add", "upstream", str(provider_repo))
    _git(repo, "branch", "develop", ancestor_sha)
    _git(repo, "switch", "develop")
    _git(repo, "worktree", "add", str(review_worktree), "review-head")

    if local_base_state == "older":
        base_branch = "develop"
    elif local_base_state == "newer":
        _write_and_commit(repo, "newer local base\n", "newer local base")
        base_branch = "develop"
    else:
        base_branch = "missing-local-base"

    (repo / "tracked.txt").write_text("concurrent dirty primary content\n")
    return {
        "repo": repo,
        "review_worktree": review_worktree,
        "output_dir": output_dir,
        "base_branch": base_branch,
        "provider_repo": provider_repo,
        "provider_head": provider_head,
        "provider_base": provider_base,
        "provider_merge_base": ancestor_sha,
    }


@pytest.fixture
def linked_review_topology(tmp_path: Path, local_base_state: str) -> dict[str, Path | str]:
    return _build_linked_review_topology(tmp_path, local_base_state)


def _stub_for(topology: dict[str, Path | str]):
    return _network_stub(
        subprocess.run,
        provider_head=str(topology["provider_head"]),
        provider_base=str(topology["provider_base"]),
        provider_merge_base=str(topology["provider_merge_base"]),
    )


@pytest.mark.parametrize("local_base_state", ["older", "newer", "absent"])
def test_local_annotation_preserves_primary_linked_worktree_state(
    linked_review_topology: dict[str, Path | str],
) -> None:
    topology = linked_review_topology
    repo = topology["repo"]
    assert isinstance(repo, Path)
    before = _primary_state(repo)

    with patch("subprocess.run", side_effect=_stub_for(topology)):
        result = annotate_pr_diff(**_annotation_args(topology))

    assert result["review_mode"] == "local"
    assert _primary_state(repo) == before
    _assert_provider_metrics(result, topology)


@pytest.mark.parametrize("local_base_state", ["older", "newer", "absent"])
def test_byte_stable_on_failure_injected_after_immutable_fetch(
    linked_review_topology: dict[str, Path | str],
) -> None:
    topology = linked_review_topology
    repo = topology["repo"]
    output_dir = topology["output_dir"]
    assert isinstance(repo, Path)
    assert isinstance(output_dir, Path)
    before = _primary_state(repo)
    base_run = _stub_for(topology)
    fetched = False

    def fail_after_fetch(args, **kwargs):
        nonlocal fetched
        result = base_run(args, **kwargs)
        if not fetched and args[:4] == ["git", "fetch", "--no-write-fetch-head", "upstream"]:
            fetched = True
            raise RuntimeError("injected failure after immutable fetch")
        return result

    with (
        patch("subprocess.run", side_effect=fail_after_fetch),
        pytest.raises(RuntimeError, match="injected failure after immutable fetch"),
    ):
        annotate_pr_diff(**_annotation_args(topology))

    assert fetched
    assert not (output_dir / "metrics_777.json").exists()
    assert _primary_state(repo) == before


@pytest.mark.parametrize("local_base_state", ["older", "newer", "absent"])
def test_byte_stable_on_interruption(
    linked_review_topology: dict[str, Path | str],
) -> None:
    topology = linked_review_topology
    repo = topology["repo"]
    output_dir = topology["output_dir"]
    assert isinstance(repo, Path)
    assert isinstance(output_dir, Path)
    before = _primary_state(repo)
    base_run = _stub_for(topology)

    def interrupt_diff(args, **kwargs):
        if args[:2] == ["git", "diff"]:
            raise KeyboardInterrupt
        return base_run(args, **kwargs)

    with (
        patch("subprocess.run", side_effect=interrupt_diff),
        pytest.raises(KeyboardInterrupt),
    ):
        annotate_pr_diff(**_annotation_args(topology))

    assert not (output_dir / "metrics_777.json").exists()
    assert _primary_state(repo) == before


@pytest.mark.parametrize("local_base_state", ["older", "newer", "absent"])
def test_byte_stable_on_retry(
    linked_review_topology: dict[str, Path | str],
) -> None:
    topology = linked_review_topology
    repo = topology["repo"]
    output_dir = topology["output_dir"]
    assert isinstance(repo, Path)
    assert isinstance(output_dir, Path)
    before = _primary_state(repo)
    base_run = _stub_for(topology)
    fail_once = True

    def fail_first_diff(args, **kwargs):
        nonlocal fail_once
        if fail_once and args[:2] == ["git", "diff"]:
            fail_once = False
            raise RuntimeError("injected first-attempt failure")
        return base_run(args, **kwargs)

    with patch("subprocess.run", side_effect=fail_first_diff):
        with pytest.raises(RuntimeError, match="injected first-attempt failure"):
            annotate_pr_diff(**_annotation_args(topology))
        assert not (output_dir / "metrics_777.json").exists()
        assert _primary_state(repo) == before
        result = annotate_pr_diff(**_annotation_args(topology))

    assert _primary_state(repo) == before
    _assert_provider_metrics(result, topology)


@pytest.mark.parametrize("local_base_state", ["older", "newer", "absent"])
def test_byte_stable_on_downstream_review_failure(
    linked_review_topology: dict[str, Path | str],
) -> None:
    topology = linked_review_topology
    repo = topology["repo"]
    assert isinstance(repo, Path)
    before = _primary_state(repo)
    with patch("subprocess.run", side_effect=_stub_for(topology)):
        result = annotate_pr_diff(**_annotation_args(topology))

    def fail_downstream_review(_result: dict[str, str]) -> None:
        raise RuntimeError("injected downstream review failure")

    with (
        patch("subprocess.run") as run,
        pytest.raises(RuntimeError, match="injected downstream review failure"),
    ):
        fail_downstream_review(result)

    run.assert_not_called()
    assert _primary_state(repo) == before


def test_standalone_clone_can_prepare_immutable_diff(
    tmp_path: Path,
) -> None:
    topology = _build_linked_review_topology(tmp_path, "older")
    repo = topology["repo"]
    assert isinstance(repo, Path)
    with patch("subprocess.run", side_effect=_stub_for(topology)):
        linked_result = annotate_pr_diff(**_annotation_args(topology))

    clone = tmp_path / "standalone-clone"
    clone_output = tmp_path / "standalone-output"
    subprocess.run(
        ["git", "clone", "--branch", "review-head", str(repo), str(clone)],
        check=True,
        capture_output=True,
    )
    _git(clone, "remote", "add", "upstream", str(topology["provider_repo"]))
    clone_topology = {
        **topology,
        "review_worktree": clone,
        "output_dir": clone_output,
    }
    with patch("subprocess.run", side_effect=_stub_for(clone_topology)):
        clone_result = annotate_pr_diff(**_annotation_args(clone_topology))

    assert (clone / ".git").is_dir()
    _assert_provider_metrics(clone_result, clone_topology)
    linked_metrics = json.loads(Path(linked_result["diff_metrics_path"]).read_text())
    clone_metrics = json.loads(Path(clone_result["diff_metrics_path"]).read_text())
    assert clone_metrics["diff_sha256"] == linked_metrics["diff_sha256"]
    assert (
        Path(clone_result["annotated_diff_path"]).read_bytes()
        == Path(linked_result["annotated_diff_path"]).read_bytes()
    )


def _ref_snapshot(repo: Path, namespace: str) -> bytes:
    return _git(
        repo,
        "for-each-ref",
        "--format=%(refname)%00%(objectname)",
        namespace,
    )


def test_sha_only_fetch_does_not_update_local_branches_or_remote_tracking(
    tmp_path: Path,
) -> None:
    topology = _build_linked_review_topology(tmp_path, "older")
    repo = topology["repo"]
    review_worktree = topology["review_worktree"]
    fetched_sha = str(topology["provider_base"])
    assert isinstance(repo, Path)
    assert isinstance(review_worktree, Path)
    heads_before = _ref_snapshot(repo, "refs/heads")
    remotes_before = _ref_snapshot(repo, "refs/remotes")
    fetch_head = repo / ".git" / "FETCH_HEAD"
    assert not fetch_head.exists()
    assert subprocess.run(
        ["git", "cat-file", "-e", f"{fetched_sha}^{{commit}}"],
        cwd=review_worktree,
        capture_output=True,
    ).returncode

    with patch("subprocess.run", side_effect=_stub_for(topology)):
        annotate_pr_diff(**_annotation_args(topology))

    assert _git(review_worktree, "cat-file", "-e", f"{fetched_sha}^{{commit}}") == b""
    assert _ref_snapshot(repo, "refs/heads") == heads_before
    assert _ref_snapshot(repo, "refs/remotes") == remotes_before
    assert not fetch_head.exists()
