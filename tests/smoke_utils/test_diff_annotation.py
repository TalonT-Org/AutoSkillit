"""Smoke-utils tests relocated from the former monolith."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import threading
from pathlib import Path
from unittest.mock import patch

import pytest

from autoskillit.smoke_utils import (
    annotate_pr_diff,
)

pytestmark = [pytest.mark.medium]

_DIFF_OUTPUT = "+++ b/src/app.py\n@@ -1,3 +1,4 @@\n line1\n+added\n"

_SHA = "a" * 40

_BASE_SHA = "b" * 40

_MERGE_BASE_SHA = "c" * 40


def _annotation_run_side_effect(
    diff_output: str = _DIFF_OUTPUT,
    *,
    head_sha: str = _SHA,
    base_sha: str = _BASE_SHA,
    live_base_sha: str | None = None,
    merge_base_sha: str = _MERGE_BASE_SHA,
):
    def _run(args, **_kwargs):
        if args[:2] == ["gh", "api"]:
            assert len(args) == 5
            assert re.fullmatch(r"repos/\{owner\}/\{repo\}/pulls/\d+", args[2])
            assert args[3:] == [
                "--jq",
                "{headRefOid: .head.sha, baseRefOid: .base.sha}",
            ]
            payload = json.dumps(
                {
                    "headRefOid": head_sha,
                    "baseRefOid": live_base_sha or base_sha,
                }
            )
            return subprocess.CompletedProcess(args, 0, payload.encode(), b"")
        if args[:3] == ["gh", "pr", "diff"]:
            return subprocess.CompletedProcess(args, 0, diff_output.encode(), b"")
        if args[:3] == ["git", "rev-parse", "HEAD"]:
            return subprocess.CompletedProcess(args, 0, head_sha.encode(), b"")
        if args[:2] == ["git", "rev-parse"]:
            return subprocess.CompletedProcess(args, 0, base_sha.encode(), b"")
        if args[:2] == ["git", "merge-base"]:
            return subprocess.CompletedProcess(args, 0, merge_base_sha.encode(), b"")
        if args[:2] == ["git", "diff"]:
            return subprocess.CompletedProcess(args, 0, diff_output.encode(), b"")
        raise AssertionError(f"unexpected annotation command: {args}")

    return _run


def _provider_annotation_run_side_effect(
    *,
    checkout_head: str = _SHA,
    provider_head: str = _SHA,
    provider_base: str = _BASE_SHA,
    provider_merge_base: str = _MERGE_BASE_SHA,
    local_base_tip: str | None = "d" * 40,
    local_merge_base: str = _MERGE_BASE_SHA,
    missing_objects: frozenset[str] = frozenset(),
    repository: str = "Acme/Base",
    diff_output: str = _DIFF_OUTPUT,
):
    """Model independent checkout, provider, and optional local observations."""

    def _run(args, **_kwargs):
        if args[:2] == ["gh", "api"]:
            endpoint = args[2]
            if "/compare/" in endpoint:
                payload = {
                    "merge_base_commit": {"sha": provider_merge_base},
                    "mergeBaseOid": provider_merge_base,
                }
            else:
                payload = {
                    "head": {"sha": provider_head},
                    "base": {"sha": provider_base, "repo": {"full_name": repository}},
                    "headRefOid": provider_head,
                    "baseRefOid": provider_base,
                    "baseRepoFullName": repository,
                }
            return subprocess.CompletedProcess(args, 0, json.dumps(payload).encode(), b"")
        if args[:3] == ["gh", "pr", "diff"]:
            return subprocess.CompletedProcess(args, 0, diff_output.encode(), b"")
        if args[:3] == ["git", "rev-parse", "HEAD"]:
            return subprocess.CompletedProcess(args, 0, checkout_head.encode(), b"")
        if args[:3] == ["git", "rev-parse", "--verify"]:
            if local_base_tip is None:
                return subprocess.CompletedProcess(args, 1, b"", b"")
            return subprocess.CompletedProcess(args, 0, local_base_tip.encode(), b"")
        if args[:2] == ["git", "rev-parse"]:
            if local_base_tip is None:
                return subprocess.CompletedProcess(args, 1, b"", b"")
            return subprocess.CompletedProcess(args, 0, local_base_tip.encode(), b"")
        if args[:2] == ["git", "cat-file"]:
            sha = args[-1].removesuffix("^{commit}")
            return subprocess.CompletedProcess(args, 1 if sha in missing_objects else 0, b"", b"")
        if args == ["git", "remote"]:
            return subprocess.CompletedProcess(args, 0, b"upstream\n", b"")
        if args[:3] == ["git", "remote", "get-url"]:
            return subprocess.CompletedProcess(
                args, 0, f"git@github.com:{repository.lower()}.git\n".encode(), b""
            )
        if args[:2] == ["git", "fetch"]:
            return subprocess.CompletedProcess(args, 0, b"", b"")
        if args[:2] == ["git", "merge-base"]:
            return subprocess.CompletedProcess(args, 0, local_merge_base.encode(), b"")
        if args[:2] == ["git", "diff"]:
            return subprocess.CompletedProcess(args, 0, diff_output.encode(), b"")
        raise AssertionError(f"unexpected annotation command: {args}")

    return _run


@pytest.mark.parametrize(
    ("mode", "rounds", "iteration", "expected"),
    [
        (None, "2", "0", "local"),
        (None, "2", "2", "github"),
        ("local", "0", "99", "local"),
        ("github", "9", "0", "github"),
    ],
)
@patch("subprocess.run")
def test_annotate_pr_diff_explicit_or_derived_mode_selection(
    mock_run, tmp_path: Path, mode: str | None, rounds: str, iteration: str, expected: str
) -> None:
    mock_run.side_effect = _provider_annotation_run_side_effect()
    kwargs = {} if mode is None else {"mode": mode}

    result = annotate_pr_diff(
        pr_number="123",
        cwd=str(tmp_path),
        output_dir=str(tmp_path),
        base_branch="main",
        local_review_rounds=rounds,
        current_iteration=iteration,
        **kwargs,
    )

    assert result["review_mode"] == expected


@pytest.mark.parametrize("mode", ["", "LOCAL", "other"])
def test_annotate_pr_diff_rejects_invalid_explicit_mode(tmp_path: Path, mode: str) -> None:
    with pytest.raises((ValueError, RuntimeError), match="mode"):
        annotate_pr_diff(
            pr_number="123",
            cwd=str(tmp_path),
            output_dir=str(tmp_path),
            base_branch="main",
            mode=mode,
        )


def test_annotate_pr_diff_explicit_local_requires_base_branch(tmp_path: Path) -> None:
    with pytest.raises((ValueError, RuntimeError), match="base_branch"):
        annotate_pr_diff(
            pr_number="123",
            cwd=str(tmp_path),
            output_dir=str(tmp_path),
            mode="local",
        )


@patch("subprocess.run")
def test_annotate_pr_diff_returns_review_mode_local(mock_run, tmp_path: Path) -> None:
    """T3.1: iteration < local_rounds → review_mode=local."""
    mock_run.side_effect = _provider_annotation_run_side_effect()
    result = annotate_pr_diff(
        pr_number="123",
        cwd=str(tmp_path),
        output_dir=str(tmp_path),
        local_review_rounds="3",
        current_iteration="0",
        base_branch="main",
    )
    assert result["review_mode"] == "local"


@patch("subprocess.run")
def test_annotate_pr_diff_returns_review_mode_github(mock_run, tmp_path: Path) -> None:
    """T3.2: iteration >= local_rounds → review_mode=github."""
    mock_run.side_effect = _annotation_run_side_effect()
    result = annotate_pr_diff(
        pr_number="123",
        cwd=str(tmp_path),
        output_dir=str(tmp_path),
        local_review_rounds="3",
        current_iteration="3",
    )
    assert result["review_mode"] == "github"


@patch("subprocess.run")
def test_annotate_pr_diff_local_mode_uses_git_diff(mock_run, tmp_path: Path) -> None:
    """T3.3: local mode resolves refs before a pinned git diff."""
    mock_run.side_effect = _provider_annotation_run_side_effect()
    annotate_pr_diff(
        pr_number="123",
        cwd=str(tmp_path),
        output_dir=str(tmp_path),
        local_review_rounds="2",
        current_iteration="0",
        base_branch="main",
    )
    commands = [call[0][0] for call in mock_run.call_args_list]
    diff_command = next(command for command in commands if command[:2] == ["git", "diff"])
    assert diff_command[-2:] == [_MERGE_BASE_SHA, _SHA]
    assert commands.index(diff_command) > commands.index(["git", "merge-base", _BASE_SHA, _SHA])


@patch("subprocess.run")
def test_annotate_pr_diff_github_mode_uses_gh_pr_diff(mock_run, tmp_path: Path) -> None:
    """T3.4: github mode calls gh pr diff."""
    mock_run.side_effect = _annotation_run_side_effect()
    annotate_pr_diff(
        pr_number="123",
        cwd=str(tmp_path),
        output_dir=str(tmp_path),
        local_review_rounds="2",
        current_iteration="2",
        base_branch="",
    )
    commands = [call[0][0] for call in mock_run.call_args_list]
    diff_index = next(
        index for index, command in enumerate(commands) if command[:3] == ["gh", "pr", "diff"]
    )
    assert commands[diff_index - 1][:2] == ["gh", "api"]
    assert commands[diff_index + 1][:2] == ["gh", "api"]


@patch("subprocess.run")
def test_annotate_pr_diff_zero_local_rounds_always_github(mock_run, tmp_path: Path) -> None:
    """T3.5: local_review_rounds=0 → always github."""
    mock_run.side_effect = _annotation_run_side_effect()
    result = annotate_pr_diff(
        pr_number="123",
        cwd=str(tmp_path),
        output_dir=str(tmp_path),
        local_review_rounds="0",
        current_iteration="0",
    )
    assert result["review_mode"] == "github"


@patch("subprocess.run")
def test_annotate_pr_diff_missing_iteration_defaults_zero(mock_run, tmp_path: Path) -> None:
    """T3.6: empty current_iteration defaults to 0 → local mode when local_rounds > 0."""
    mock_run.side_effect = _provider_annotation_run_side_effect()
    result = annotate_pr_diff(
        pr_number="123",
        cwd=str(tmp_path),
        output_dir=str(tmp_path),
        local_review_rounds="3",
        current_iteration="",
        base_branch="main",
    )
    assert result["review_mode"] == "local"


@patch("subprocess.run")
def test_annotate_pr_diff_local_mode_empty_base_branch_falls_back_to_github(
    mock_run, tmp_path: Path
) -> None:
    """T3.8: local mode with empty base_branch falls back to gh pr diff and returns github."""
    mock_run.side_effect = _annotation_run_side_effect()
    result = annotate_pr_diff(
        pr_number="123",
        cwd=str(tmp_path),
        output_dir=str(tmp_path),
        local_review_rounds="3",
        current_iteration="0",
        base_branch="",
    )
    assert result["review_mode"] == "github"
    commands = [call[0][0] for call in mock_run.call_args_list]
    assert any(command[:3] == ["gh", "pr", "diff"] for command in commands)


@patch("subprocess.run")
def test_annotate_pr_diff_backward_compat_no_new_params(mock_run, tmp_path: Path) -> None:
    """T3.7: old 3-arg call works and defaults review_mode=github."""
    mock_run.side_effect = _annotation_run_side_effect()
    result = annotate_pr_diff(
        pr_number="123",
        cwd=str(tmp_path),
        output_dir=str(tmp_path),
    )
    assert "review_mode" in result
    assert result["review_mode"] == "github"


@patch("subprocess.run")
def test_annotate_pr_diff_int_pr_number(mock_run, tmp_path: Path) -> None:
    """annotate_pr_diff handles int pr_number from LLM JSON boundary.

    Without the type coercion fix, passing pr_number=42 (int) causes
    TypeError: argument of type 'int' is not iterable when constructing
    the gh subprocess command list.
    """
    mock_run.side_effect = _annotation_run_side_effect("+diff content\n")
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    result = annotate_pr_diff(pr_number=42, cwd=str(tmp_path), output_dir=str(output_dir))  # type: ignore[arg-type]
    assert result["annotated_diff_path"]

    # Verify the subprocess call received str "42", not int 42
    cmd_list = next(
        call[0][0] for call in mock_run.call_args_list if call[0][0][:3] == ["gh", "pr", "diff"]
    )
    assert "42" in cmd_list, f"Expected '42' in command, got {cmd_list}"


@patch("subprocess.run")
def test_annotate_pr_diff_produces_valid_lines_artifact(mock_run, tmp_path: Path) -> None:
    """annotate_pr_diff writes valid lines alongside the explicitly named hunk ranges."""
    import json

    from autoskillit.execution.diff_annotator import extract_valid_lines

    mock_run.side_effect = _annotation_run_side_effect()
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    result = annotate_pr_diff(
        pr_number="99",
        cwd=str(tmp_path),
        output_dir=str(output_dir),
    )
    assert "valid_lines_path" in result
    vl_path = Path(result["valid_lines_path"])
    assert vl_path.exists()
    content = json.loads(vl_path.read_text())
    expected = extract_valid_lines(_DIFF_OUTPUT)
    assert content == expected


def _churn_diff(*, additions: int, removals: int) -> str:
    return (
        "diff --git a/f.py b/f.py\n"
        "--- a/f.py\n"
        "+++ b/f.py\n"
        f"@@ -1,{removals} +1,{additions} @@\n"
        + "".join(f"-old_{index}\n" for index in range(removals))
        + "".join(f"+new_{index}\n" for index in range(additions))
    )


@pytest.mark.parametrize(
    ("additions", "removals", "expected"),
    [
        (2000, 0, False),
        (2001, 0, True),
        (1000, 1001, True),
        (0, 2001, True),
    ],
)
@patch("subprocess.run")
def test_annotate_pr_diff_writes_native_overengineering_gate(
    mock_run,
    tmp_path: Path,
    additions: int,
    removals: int,
    expected: bool,
) -> None:
    mock_run.side_effect = _annotation_run_side_effect(
        _churn_diff(additions=additions, removals=removals)
    )
    annotate_pr_diff(pr_number="91", cwd=str(tmp_path), output_dir=str(tmp_path))
    gate = json.loads((tmp_path / "metrics_91.json").read_text())["run_overengineering_audits"]
    assert type(gate) is bool
    assert gate is expected


@patch("subprocess.run")
def test_annotate_pr_diff_publishes_snapshot_manifest_last(
    mock_run,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import hashlib

    import autoskillit.core as core

    diff = _churn_diff(additions=2, removals=1)
    mock_run.side_effect = _provider_annotation_run_side_effect(diff_output=diff)
    original_atomic_write = core.atomic_write
    write_order: list[str] = []

    def recording_atomic_write(path: Path, content: str) -> None:
        write_order.append(path.name)
        original_atomic_write(path, content)

    monkeypatch.setattr(core, "atomic_write", recording_atomic_write)
    annotate_pr_diff(
        pr_number="92",
        cwd=str(tmp_path),
        output_dir=str(tmp_path),
        local_review_rounds="1",
        current_iteration="0",
        base_branch="main",
    )
    metrics = json.loads((tmp_path / "metrics_92.json").read_text())
    assert metrics["_head_sha"] == _SHA
    assert metrics["_base_sha"] == _BASE_SHA
    assert metrics["_merge_base_sha"] == _MERGE_BASE_SHA
    assert metrics["diff_sha256"] == hashlib.sha256(diff.encode()).hexdigest()
    assert metrics["diff_byte_length"] == len(diff.encode())
    assert set(metrics["diff_source"]) == {
        "kind",
        "comparison",
        "context_lines",
        "rename_detection",
        "external_diff",
        "text_conversion",
        "profile_id",
    }
    for artifact in metrics["artifacts"].values():
        artifact_bytes = (tmp_path / artifact["basename"]).read_bytes()
        assert artifact["byte_length"] == len(artifact_bytes)
        assert artifact["sha256"] == hashlib.sha256(artifact_bytes).hexdigest()
    assert write_order == [
        "annotated_diff_92.txt",
        "hunk_ranges_92.json",
        "valid_lines_92.json",
        "metrics_92.json",
    ]


@pytest.mark.parametrize(
    "failure_name",
    ["annotated_diff_94.txt", "hunk_ranges_94.json", "valid_lines_94.json"],
)
@patch("subprocess.run")
def test_annotate_pr_diff_never_publishes_manifest_after_sidecar_failure(
    mock_run,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_name: str,
) -> None:
    import autoskillit.core as core

    mock_run.side_effect = _annotation_run_side_effect(_churn_diff(additions=2, removals=1))
    original_atomic_write = core.atomic_write
    write_order: list[str] = []

    def failing_atomic_write(path: Path, content: str) -> None:
        write_order.append(path.name)
        if path.name == failure_name:
            raise OSError("injected sidecar write failure")
        original_atomic_write(path, content)

    monkeypatch.setattr(core, "atomic_write", failing_atomic_write)
    with pytest.raises(OSError, match="injected sidecar write failure"):
        annotate_pr_diff(
            pr_number="94",
            cwd=str(tmp_path),
            output_dir=str(tmp_path),
            base_branch="main",
        )

    assert "metrics_94.json" not in write_order
    assert not (tmp_path / "metrics_94.json").exists()


@patch("subprocess.run")
def test_annotate_pr_diff_preserves_stderr_bytes_when_diff_fails(mock_run, tmp_path: Path) -> None:
    metrics_path = tmp_path / "metrics_93.json"
    metrics_path.write_text('{"generation_id":"stale"}')

    def fail_diff(args, **_kwargs):
        if args[:2] == ["gh", "api"]:
            payload = json.dumps({"headRefOid": _SHA, "baseRefOid": _BASE_SHA})
            return subprocess.CompletedProcess(args, 0, payload.encode(), b"")
        if args[:3] == ["gh", "pr", "diff"]:
            return subprocess.CompletedProcess(args, 1, b"", b"diff failed: \xff")
        raise AssertionError(f"unexpected annotation command: {args}")

    mock_run.side_effect = fail_diff
    with pytest.raises(RuntimeError, match="annotation command failed") as exc_info:
        annotate_pr_diff(pr_number="93", cwd=str(tmp_path), output_dir=str(tmp_path))
    assert "diff failed: \\xff" in str(exc_info.value)
    assert not metrics_path.exists()


@patch("subprocess.run")
def test_annotate_pr_diff_failed_ref_lookup_publishes_no_authority(
    mock_run, tmp_path: Path
) -> None:
    metrics_path = tmp_path / "metrics_96.json"
    metrics_path.write_text('{"generation_id":"stale"}')

    def fail_ref_lookup(args, **_kwargs):
        if args[:2] == ["gh", "api"]:
            return subprocess.CompletedProcess(args, 1, b"", b"ref lookup failed")
        raise AssertionError(f"unexpected annotation command: {args}")

    mock_run.side_effect = fail_ref_lookup
    with pytest.raises(RuntimeError, match="unable to resolve live PR head/base refs"):
        annotate_pr_diff(pr_number="96", cwd=str(tmp_path), output_dir=str(tmp_path))
    assert not metrics_path.exists()


@patch("subprocess.run")
def test_annotation_marker_protocol_detects_overlapping_publication(
    mock_run, tmp_path: Path
) -> None:
    first_diff = _churn_diff(additions=2, removals=1)
    mock_run.side_effect = _annotation_run_side_effect(first_diff)
    annotate_pr_diff(pr_number="97", cwd=str(tmp_path), output_dir=str(tmp_path))

    marker_path = tmp_path / "metrics_97.json"
    marker_retained = threading.Event()
    publisher_finished = threading.Event()
    consumer_result: dict[str, bool] = {}

    def consume_generation(*, overlap: bool) -> bool:
        retained_marker_bytes = marker_path.read_bytes()
        retained_marker = json.loads(retained_marker_bytes)
        if overlap:
            marker_retained.set()
            assert publisher_finished.wait(timeout=10)
        sidecars_match = all(
            artifact["byte_length"] == len((tmp_path / artifact["basename"]).read_bytes())
            and artifact["sha256"]
            == hashlib.sha256((tmp_path / artifact["basename"]).read_bytes()).hexdigest()
            for artifact in retained_marker["artifacts"].values()
        )
        return sidecars_match and retained_marker_bytes == marker_path.read_bytes()

    def overlapping_consumer() -> None:
        consumer_result["accepted"] = consume_generation(overlap=True)

    consumer = threading.Thread(target=overlapping_consumer)
    consumer.start()
    assert marker_retained.wait(timeout=10)

    second_diff = _churn_diff(additions=3, removals=2)
    mock_run.side_effect = _annotation_run_side_effect(second_diff)
    annotate_pr_diff(pr_number="97", cwd=str(tmp_path), output_dir=str(tmp_path))
    publisher_finished.set()
    consumer.join(timeout=10)

    assert not consumer.is_alive()
    assert consumer_result == {"accepted": False}
    assert consume_generation(overlap=False)


@patch("subprocess.run")
def test_annotate_pr_diff_rejects_moving_github_refs(mock_run, tmp_path: Path) -> None:
    ref_reads = 0

    def moving_refs(args, **_kwargs):
        nonlocal ref_reads
        if args[:2] == ["gh", "api"]:
            ref_reads += 1
            head = _SHA if ref_reads == 1 else "d" * 40
            payload = json.dumps({"headRefOid": head, "baseRefOid": _BASE_SHA})
            return subprocess.CompletedProcess(args, 0, payload.encode(), b"")
        if args[:3] == ["gh", "pr", "diff"]:
            return subprocess.CompletedProcess(args, 0, _DIFF_OUTPUT.encode(), b"")
        raise AssertionError(f"unexpected annotation command: {args}")

    mock_run.side_effect = moving_refs
    with pytest.raises(RuntimeError, match="moved during diff acquisition"):
        annotate_pr_diff(pr_number="94", cwd=str(tmp_path), output_dir=str(tmp_path))
    assert not (tmp_path / "metrics_94.json").exists()


@pytest.mark.parametrize("local_base_tip", ["0" * 40, "f" * 40, None])
@patch("subprocess.run")
def test_annotate_pr_diff_local_base_tip_is_observational_only(
    mock_run, tmp_path: Path, local_base_tip: str | None
) -> None:
    mock_run.side_effect = _provider_annotation_run_side_effect(local_base_tip=local_base_tip)

    annotate_pr_diff(
        pr_number="95",
        cwd=str(tmp_path),
        output_dir=str(tmp_path),
        base_branch="main",
        mode="local",
    )

    metrics = json.loads((tmp_path / "metrics_95.json").read_text())
    assert metrics["_head_sha"] == _SHA
    assert metrics["_base_sha"] == _BASE_SHA
    assert metrics["_merge_base_sha"] == _MERGE_BASE_SHA
    assert metrics["_base_repo_full_name"] == "Acme/Base"
    commands = [call.args[0] for call in mock_run.call_args_list]
    diff = next(command for command in commands if command[:2] == ["git", "diff"])
    assert diff[-2:] == [_MERGE_BASE_SHA, _SHA]


@patch("subprocess.run")
def test_annotate_pr_diff_requires_provider_and_checkout_head_agreement(
    mock_run, tmp_path: Path
) -> None:
    mock_run.side_effect = _provider_annotation_run_side_effect(provider_head="e" * 40)

    with pytest.raises(RuntimeError, match="head"):
        annotate_pr_diff(
            pr_number="951",
            cwd=str(tmp_path),
            output_dir=str(tmp_path),
            base_branch="main",
            mode="local",
        )

    assert not (tmp_path / "metrics_951.json").exists()


@patch("subprocess.run")
def test_annotate_pr_diff_uses_base_repository_compare_and_sha_only_fetches(
    mock_run, tmp_path: Path
) -> None:
    mock_run.side_effect = _provider_annotation_run_side_effect(
        missing_objects=frozenset({_BASE_SHA, _MERGE_BASE_SHA})
    )

    annotate_pr_diff(
        pr_number="952",
        cwd=str(tmp_path),
        output_dir=str(tmp_path),
        base_branch="main",
        mode="local",
    )

    commands = [call.args[0] for call in mock_run.call_args_list]
    compare = next(
        command
        for command in commands
        if command[:2] == ["gh", "api"] and "/compare/" in command[2]
    )
    assert "Acme/Base" in compare[2]
    assert _BASE_SHA in compare[2] and _SHA in compare[2]
    fetches = [command for command in commands if command[:2] == ["git", "fetch"]]
    assert fetches == [
        ["git", "fetch", "--no-write-fetch-head", "upstream", _BASE_SHA],
        ["git", "fetch", "--no-write-fetch-head", "upstream", _MERGE_BASE_SHA],
    ]
    assert all(":" not in argument for command in fetches for argument in command)


@patch("subprocess.run")
def test_annotate_pr_diff_refuses_to_guess_canonical_remote(mock_run, tmp_path: Path) -> None:
    side_effect = _provider_annotation_run_side_effect(missing_objects=frozenset({_BASE_SHA}))

    def no_matching_remote(args, **kwargs):
        if args[:3] == ["git", "remote", "get-url"]:
            return subprocess.CompletedProcess(args, 0, b"git@github.com:Other/Repo.git\n", b"")
        return side_effect(args, **kwargs)

    mock_run.side_effect = no_matching_remote
    with pytest.raises(RuntimeError, match="remote"):
        annotate_pr_diff(
            pr_number="953",
            cwd=str(tmp_path),
            output_dir=str(tmp_path),
            base_branch="main",
            mode="local",
        )

    commands = [call.args[0] for call in mock_run.call_args_list]
    assert not any(command[:2] == ["git", "fetch"] for command in commands)


@patch("subprocess.run")
def test_annotate_pr_diff_requires_local_and_provider_merge_base_agreement(
    mock_run, tmp_path: Path
) -> None:
    mock_run.side_effect = _provider_annotation_run_side_effect(local_merge_base="9" * 40)

    with pytest.raises(RuntimeError, match="merge base"):
        annotate_pr_diff(
            pr_number="954",
            cwd=str(tmp_path),
            output_dir=str(tmp_path),
            base_branch="main",
            mode="local",
        )


@patch("subprocess.run")
def test_annotate_pr_diff_revalidates_complete_provider_tuple(mock_run, tmp_path: Path) -> None:
    side_effect = _provider_annotation_run_side_effect()
    pr_reads = 0

    def moving_repository(args, **kwargs):
        nonlocal pr_reads
        result = side_effect(args, **kwargs)
        if args[:2] == ["gh", "api"] and "/compare/" not in args[2]:
            pr_reads += 1
            if pr_reads == 2:
                payload = json.loads(result.stdout)
                payload["base"]["repo"]["full_name"] = "Other/Base"
                payload["baseRepoFullName"] = "Other/Base"
                return subprocess.CompletedProcess(args, 0, json.dumps(payload).encode(), b"")
        return result

    mock_run.side_effect = moving_repository
    with pytest.raises(RuntimeError, match="moved|changed|authority"):
        annotate_pr_diff(
            pr_number="955",
            cwd=str(tmp_path),
            output_dir=str(tmp_path),
            base_branch="main",
            mode="local",
        )
    assert not (tmp_path / "metrics_955.json").exists()


class _AnnotationSentinel(BaseException):
    pass


@pytest.mark.parametrize("exception_type", [RuntimeError, _AnnotationSentinel])
@patch("subprocess.run")
def test_annotate_pr_diff_cleans_commit_marker_on_baseexception_and_retry(
    mock_run, tmp_path: Path, exception_type: type[BaseException]
) -> None:
    metrics_path = tmp_path / "metrics_956.json"
    metrics_path.write_text('{"generation_id":"stale"}')
    side_effect = _provider_annotation_run_side_effect()
    interrupted = False

    def interrupt_acquisition(args, **kwargs):
        nonlocal interrupted
        if not interrupted and args[:2] == ["git", "diff"]:
            interrupted = True
            raise exception_type("injected acquisition interruption")
        return side_effect(args, **kwargs)

    mock_run.side_effect = interrupt_acquisition
    with pytest.raises(exception_type, match="injected"):
        annotate_pr_diff(
            pr_number="956",
            cwd=str(tmp_path),
            output_dir=str(tmp_path),
            base_branch="main",
            mode="local",
        )
    assert not metrics_path.exists()

    mock_run.side_effect = side_effect
    annotate_pr_diff(
        pr_number="956",
        cwd=str(tmp_path),
        output_dir=str(tmp_path),
        base_branch="main",
        mode="local",
    )
    metrics = json.loads(metrics_path.read_text())
    assert metrics["generation_id"] != "stale"
    assert metrics["_base_repo_full_name"] == "Acme/Base"


@patch("subprocess.run")
def test_annotate_pr_diff_embeds_head_sha_in_metrics(mock_run, tmp_path: Path) -> None:
    """T_SHA_1: metrics_{pr}.json must include _head_sha field."""
    mock_run.side_effect = _annotation_run_side_effect()
    annotate_pr_diff(pr_number="999", cwd=str(tmp_path), output_dir=str(tmp_path))
    metrics = json.loads((tmp_path / "metrics_999.json").read_text())
    assert metrics["_head_sha"] == _SHA


@patch("subprocess.run")
def test_annotate_pr_diff_embeds_sha_header_in_diff_text(mock_run, tmp_path: Path) -> None:
    """T_SHA_2: annotated_diff_{pr}.txt first line must be # sha: {sha}."""
    mock_run.side_effect = _annotation_run_side_effect()
    annotate_pr_diff(pr_number="999", cwd=str(tmp_path), output_dir=str(tmp_path))
    first_line = (tmp_path / "annotated_diff_999.txt").read_text().split("\n")[0]
    assert first_line == f"# sha: {_SHA}"


@patch("subprocess.run")
def test_annotate_pr_diff_returns_head_sha(mock_run, tmp_path: Path) -> None:
    """T_SHA_3: Return dict must include head_sha for downstream capture."""
    mock_run.side_effect = _annotation_run_side_effect()
    result = annotate_pr_diff(pr_number="999", cwd=str(tmp_path), output_dir=str(tmp_path))
    assert result["pr_head_sha"] == _SHA
    assert re.fullmatch(r"[0-9a-f]{40}", result["pr_head_sha"])


@patch("subprocess.run")
def test_annotate_pr_diff_valid_lines_flat_schema(mock_run, tmp_path: Path) -> None:
    """T_SHA_4: valid_lines_{pr}.json must be a flat {filepath: [lines]} dict, not wrapped."""
    mock_run.side_effect = _annotation_run_side_effect()
    annotate_pr_diff(pr_number="999", cwd=str(tmp_path), output_dir=str(tmp_path))
    data = json.loads((tmp_path / "valid_lines_999.json").read_text())
    assert "_head_sha" not in data, (
        "valid_lines must not contain _head_sha — breaks SKILL.md Step 4"
    )
    assert all(isinstance(v, list) for v in data.values()), "values must be lists of line numbers"
