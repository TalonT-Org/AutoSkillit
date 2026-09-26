"""Behavioral contract for the bundled review-pr snapshot and revalidation script."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from tests.skills._review_pr_gate_helpers import (
    GATE_SCRIPT,
    git,
    make_gate_case,
    revalidate,
    snapshot,
    write_metrics,
)

pytestmark = [pytest.mark.layer("skills"), pytest.mark.medium]

REASONS = {
    "ref_missing",
    "metrics_missing",
    "metrics_invalid_json",
    "manifest_missing",
    "manifest_invalid",
    "profile_invalid",
    "snapshot_mismatch",
    "artifact_missing",
    "artifact_name_mismatch",
    "artifact_length_mismatch",
    "artifact_digest_mismatch",
    "marker_changed",
    "gate_missing",
    "gate_not_boolean",
}


def _authority(case: dict[str, Any]) -> dict[str, Any]:
    result = snapshot(case)
    assert result.returncode == 0, result.stderr
    assert len(result.stdout.splitlines()) == 1
    return json.loads(result.stdout)


def _directory_state(directory: Path) -> dict[str, tuple[bytes, int]]:
    return {
        str(path.relative_to(directory)): (path.read_bytes(), path.stat().st_mtime_ns)
        for path in directory.rglob("*")
        if path.is_file()
    }


@pytest.mark.parametrize("mode", ["local", "github"])
@pytest.mark.parametrize(
    ("gate", "state", "audit_state"),
    [(True, "valid_true", "pending"), (False, "valid_false", "not_required")],
)
def test_snapshot_prints_persisted_authority_and_retains_artifacts(
    tmp_path: Path, mode: str, gate: bool, state: str, audit_state: str
) -> None:
    case = make_gate_case(tmp_path, mode=mode, gate=gate)
    authority = _authority(case)
    assert authority["state"] == state
    assert authority["reason_code"] == "none"
    assert authority["experimental_audit_state"] == audit_state
    assert authority["snapshot"] == {
        "head_sha": case["metrics"]["_head_sha"],
        "base_sha": case["metrics"]["_base_sha"],
        "merge_base_sha": case["metrics"]["_merge_base_sha"],
        "base_repo_full_name": "Acme/Base",
        "diff_sha256": "a" * 64,
        "profile_id": case["metrics"]["diff_source"]["profile_id"],
    }
    assert authority["annotation_generation_id"] == "generation-1"
    assert authority["mode"] == mode
    assert authority["checkout_root"] == str(case["repo"])
    assert str(authority["pr_number"]) == "7"
    for key, expected in (
        ("diff_metrics_path", case["metrics_path"]),
        ("annotated_diff_path", case["annotated"]),
        ("hunk_ranges_path", case["ranges"]),
        ("valid_lines_path", case["valid_lines"]),
    ):
        assert authority[key] == str(expected)

    snapshot_dir = Path(authority["snapshot_dir"])
    assert snapshot_dir.parent == case["output_dir"]
    authority_path = Path(authority["authority_path"])
    assert authority_path.parent == snapshot_dir
    assert json.loads(authority_path.read_text()) == authority
    for key, source in (
        ("metrics_marker_snapshot_path", case["metrics_path"]),
        ("annotated_diff_snapshot_path", case["annotated"]),
        ("hunk_ranges_snapshot_path", case["ranges"]),
        ("valid_lines_snapshot_path", case["valid_lines"]),
    ):
        retained = Path(authority[key])
        assert retained.parent == snapshot_dir
        assert retained.read_bytes() == source.read_bytes()

    annotated_body = (
        Path(authority["annotated_diff_snapshot_path"])
        .read_bytes()
        .split(b"\n", 1)[1]
        .rstrip(b"\n")
    )
    assert (
        hashlib.sha256(annotated_body).hexdigest()
        == hashlib.sha256(b"[L1]+old-generation").hexdigest()
    )
    assert case["gh_call_log"].exists()
    assert "repos/{owner}/{repo}/pulls/7" in case["gh_call_log"].read_text()


@pytest.mark.parametrize("mode", ["local", "github"])
def test_revalidate_is_fresh_and_read_only(tmp_path: Path, mode: str) -> None:
    case = make_gate_case(tmp_path, mode=mode)
    authority = _authority(case)
    snapshot_dir = Path(authority["snapshot_dir"])
    before = _directory_state(snapshot_dir)
    result = revalidate(case, Path(authority["authority_path"]))
    assert result.returncode == 0, result.stderr
    assert result.stdout == "fresh\n"
    assert _directory_state(snapshot_dir) == before


@pytest.mark.parametrize("mode", ["local", "github"])
@pytest.mark.parametrize("change", ["metrics", "checkout_head", "live_head", "live_base"])
def test_revalidate_detects_changed_publishers_and_refs_without_writes(
    tmp_path: Path, mode: str, change: str
) -> None:
    case = make_gate_case(tmp_path, mode=mode)
    authority = _authority(case)
    if change == "metrics":
        case["metrics_path"].write_text(case["metrics_path"].read_text() + "\n")
    elif change == "checkout_head":
        (case["repo"] / "tracked.txt").write_text("new head\n")
        git(case["repo"], "commit", "-qam", "new head")
    elif change == "live_head":
        case["env"]["FAKE_GH_HEAD_SHA"] = "b" * 40
    else:
        case["env"]["FAKE_GH_BASE_SHA"] = "c" * 40
    snapshot_dir = Path(authority["snapshot_dir"])
    before = _directory_state(snapshot_dir)
    result = revalidate(case, Path(authority["authority_path"]))
    assert result.returncode == 0, result.stderr
    assert result.stdout == "stale\n"
    assert _directory_state(snapshot_dir) == before


def test_local_revalidate_detects_live_merge_base_change(tmp_path: Path) -> None:
    case = make_gate_case(tmp_path)
    authority = _authority(case)
    case["env"]["FAKE_GH_MERGE_BASE_SHA"] = "d" * 40
    result = revalidate(case, Path(authority["authority_path"]))
    assert result.returncode == 0, result.stderr
    assert result.stdout == "stale\n"


@pytest.mark.parametrize("artifact", ["annotated", "ranges", "valid_lines"])
def test_revalidate_detects_sidecar_replacement_without_changing_retained_bytes(
    tmp_path: Path, artifact: str
) -> None:
    case = make_gate_case(tmp_path)
    authority = _authority(case)
    snapshot_dir = Path(authority["snapshot_dir"])
    retained_before = _directory_state(snapshot_dir)
    case[artifact].write_bytes(case[artifact].read_bytes() + b"\n")
    result = revalidate(case, Path(authority["authority_path"]))
    assert result.returncode == 0, result.stderr
    assert result.stdout == "stale\n"
    assert _directory_state(snapshot_dir) == retained_before


def test_degraded_authority_revalidates_as_authority_degraded(tmp_path: Path) -> None:
    case = make_gate_case(tmp_path)
    case["metrics_path"].unlink()
    authority = _authority(case)
    assert authority["state"] == "degraded"
    assert authority["reason_code"] == "metrics_missing"
    assert Path(authority["authority_path"]).exists()
    result = revalidate(case, Path(authority["authority_path"]))
    assert result.returncode == 0, result.stderr
    assert result.stdout == "authority_degraded\n"


@pytest.mark.parametrize("damage", ["deleted", "invalid_json", "missing_field"])
def test_malformed_authority_fails_without_claiming_a_state(tmp_path: Path, damage: str) -> None:
    case = make_gate_case(tmp_path)
    authority = _authority(case)
    authority_path = Path(authority["authority_path"])
    if damage == "deleted":
        authority_path.unlink()
    elif damage == "invalid_json":
        authority_path.write_text("{")
    else:
        authority.pop("checkout_root")
        authority_path.write_text(json.dumps(authority))
    before = _directory_state(authority_path.parent)
    result = revalidate(case, authority_path)
    assert result.returncode == 2
    assert result.stdout == ""
    assert result.stderr == "invalid gate authority\n"
    assert _directory_state(authority_path.parent) == before


@pytest.mark.parametrize("reason", sorted(REASONS))
def test_every_gate_degradation_reason_is_preserved(tmp_path: Path, reason: str) -> None:
    case = make_gate_case(tmp_path)
    metrics = case["metrics"]
    if reason == "metrics_missing":
        case["metrics_path"].unlink()
    elif reason == "ref_missing":
        case["env"]["FAKE_GH_MISSING_AUTHORITY"] = "1"
    elif reason == "metrics_invalid_json":
        case["metrics_path"].write_text("{")
    elif reason in {"manifest_missing", "gate_missing"}:
        metrics.pop("artifacts" if reason == "manifest_missing" else "run_overengineering_audits")
        write_metrics(case)
    elif reason in {
        "manifest_invalid",
        "profile_invalid",
        "snapshot_mismatch",
        "gate_not_boolean",
    }:
        target, key, value = {
            "manifest_invalid": (metrics, "diff_byte_length", "17"),
            "profile_invalid": (metrics["diff_source"], "profile_id", "wrong"),
            "snapshot_mismatch": (metrics, "_head_sha", "b" * 40),
            "gate_not_boolean": (metrics, "run_overengineering_audits", "true"),
        }[reason]
        target[key] = value
        write_metrics(case)
    elif reason == "artifact_missing":
        case["annotated"].unlink()
    elif reason == "artifact_name_mismatch":
        replacement = case["output_dir"] / "wrong-name.txt"
        replacement.write_bytes(case["annotated"].read_bytes())
        case["annotated"] = replacement
    elif reason in {"artifact_length_mismatch", "artifact_digest_mismatch"}:
        record = metrics["artifacts"]["annotated_diff"]
        if reason == "artifact_length_mismatch":
            record["byte_length"] += 1
        else:
            record["sha256"] = "0" * 64
        write_metrics(case)
    else:
        real_sha256sum = shutil.which("sha256sum")
        assert real_sha256sum is not None
        wrapper = tmp_path / "sha256sum"
        wrapper.write_text(
            "#!/bin/sh\n"
            'if [ ! -e "$MUTATION_SENTINEL" ]; then\n'
            '  : > "$MUTATION_SENTINEL"\n'
            '  printf "\\n" >> "$MUTATE_MARKER_PATH"\n'
            "fi\n"
            'exec "$REAL_SHA256SUM" "$@"\n'
        )
        wrapper.chmod(0o755)
        case["env"].update(
            {
                "PATH": f"{tmp_path}:{case['env']['PATH']}",
                "MUTATION_SENTINEL": str(tmp_path / "mutated"),
                "MUTATE_MARKER_PATH": str(case["metrics_path"]),
                "REAL_SHA256SUM": real_sha256sum,
            }
        )

    authority = _authority(case)
    assert (authority["state"], authority["reason_code"]) == ("degraded", reason)
    assert authority["experimental_audit_state"] == "not_eligible"


def test_closed_reason_set_matches_script() -> None:
    actual = set(re.findall(r"\bdegrade_gate ([a-z_]+)", GATE_SCRIPT.read_text()))
    assert actual == REASONS


def test_overlapping_sidecar_replacement_preserves_retained_generation(
    tmp_path: Path,
) -> None:
    case = make_gate_case(tmp_path)
    real_sha256sum = shutil.which("sha256sum")
    assert real_sha256sum is not None
    wrapper = tmp_path / "sha256sum"
    wrapper.write_text(
        "#!/bin/sh\n"
        'if [ ! -e "$MUTATION_SENTINEL" ]; then\n'
        '  : > "$MUTATION_SENTINEL"\n'
        '  printf "metadata\\n[L1]+new-generation\\n" > "$MUTATE_SIDECAR_PATH"\n'
        "fi\n"
        'exec "$REAL_SHA256SUM" "$@"\n'
    )
    wrapper.chmod(0o755)
    case["env"].update(
        {
            "PATH": f"{tmp_path}:{case['env']['PATH']}",
            "MUTATION_SENTINEL": str(tmp_path / "mutated"),
            "MUTATE_SIDECAR_PATH": str(case["annotated"]),
            "REAL_SHA256SUM": real_sha256sum,
        }
    )
    authority = _authority(case)
    assert authority["state"] == "valid_true"
    assert Path(authority["annotated_diff_snapshot_path"]).read_bytes() == (
        b"metadata\n[L1]+old-generation\n\n"
    )
    assert b"new-generation" in case["annotated"].read_bytes()
    assert revalidate(case, Path(authority["authority_path"])).stdout == "stale\n"


@pytest.mark.parametrize(
    "bad_argument",
    ["relative_dir", "missing_dir", "file_dir", "missing_arg", "extra_arg", "bad_mode"],
)
def test_snapshot_rejects_invalid_arguments_before_github_call(
    tmp_path: Path, bad_argument: str
) -> None:
    case = make_gate_case(tmp_path)
    args = [
        "bash",
        str(GATE_SCRIPT),
        "snapshot",
        str(case["output_dir"]),
        str(case["repo"]),
        "local",
        "7",
        str(case["metrics_path"]),
        str(case["annotated"]),
        str(case["ranges"]),
        str(case["valid_lines"]),
    ]
    if bad_argument == "relative_dir":
        args[3] = "review-output"
    elif bad_argument == "missing_dir":
        args[3] = str(tmp_path / "missing")
    elif bad_argument == "file_dir":
        args[3] = str(case["metrics_path"])
    elif bad_argument == "missing_arg":
        args.pop()
    elif bad_argument == "extra_arg":
        args.append("extra")
    else:
        args[5] = "LOCAL"
    before = sorted(case["output_dir"].iterdir())
    result = subprocess.run(
        args, cwd=case["repo"], env=case["env"], capture_output=True, text=True
    )
    assert result.returncode != 0
    assert sorted(case["output_dir"].iterdir()) == before
    assert not case["gh_call_log"].exists()


@pytest.mark.parametrize("pr_number", ["7/extra", "0"])
def test_invalid_pr_number_is_rejected_at_both_boundaries(tmp_path: Path, pr_number: str) -> None:
    case = make_gate_case(tmp_path)
    result = subprocess.run(
        [
            "bash",
            str(GATE_SCRIPT),
            "snapshot",
            str(case["output_dir"]),
            str(case["repo"]),
            case["mode"],
            pr_number,
            str(case["metrics_path"]),
            str(case["annotated"]),
            str(case["ranges"]),
            str(case["valid_lines"]),
        ],
        cwd=case["repo"],
        env=case["env"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert result.stdout == ""
    assert result.stderr == "snapshot PR number must be a positive integer\n"
    authority = _authority(case)
    authority["pr_number"] = pr_number
    authority_path = Path(authority["authority_path"])
    authority_path.write_text(json.dumps(authority))
    result = revalidate(case, authority_path)
    assert result.returncode == 2
    assert result.stdout == ""
    assert result.stderr == "invalid gate authority\n"


@pytest.mark.parametrize("field", ["basename", "byte_length", "sha256"])
def test_incomplete_artifact_manifest_is_invalid(tmp_path: Path, field: str) -> None:
    case = make_gate_case(tmp_path)
    case["metrics"]["artifacts"]["annotated_diff"].pop(field)
    write_metrics(case)
    authority = _authority(case)
    assert (authority["state"], authority["reason_code"]) == ("degraded", "manifest_invalid")
