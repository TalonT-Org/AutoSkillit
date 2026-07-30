"""Behavioral guard tests for review-pr adaptive subagent dispatch."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path
from typing import Any

import pytest

pytestmark = [pytest.mark.layer("skills"), pytest.mark.medium]

SKILL_PATH = (
    Path(__file__).parent.parent.parent
    / "src"
    / "autoskillit"
    / "skills_extended"
    / "review-pr"
    / "SKILL.md"
)


def _skill_text() -> str:
    return SKILL_PATH.read_text()


def _section(start_heading: str, end_heading: str) -> str:
    text = _skill_text()
    start = text.index(start_heading)
    end = text.index(end_heading, start)
    return text[start:end]


def _bash_block(start_heading: str, end_heading: str) -> str:
    section = _section(start_heading, end_heading)
    start = section.index("```bash") + len("```bash")
    end = section.index("```", start)
    return section[start:end]


def _gate_script() -> str:
    return (
        _bash_block("### Step 2.7", "### Step 2.5")
        + """
if revalidate_retained_snapshot; then
    REVALIDATE_STATUS=0
else
    REVALIDATE_STATUS=$?
fi
printf '\\nGATE_RESULT=%s|%s|%s|%s\\n' \
    "$GATE_STATE" "$GATE_REASON_CODE" "$EXPERIMENTAL_AUDIT_STATE" "$REVALIDATE_STATUS"
printf '%s' "$ANNOTATED_DIFF" | sha256sum | cut -d' ' -f1 | sed 's/^/ANNOTATED_SHA=/'
"""
    )


def _adaptive_dispatch_script() -> str:
    return (
        _bash_block("### Step 2.7", "### Step 2.5")
        + _bash_block("### Step 2.9", "### Step 3")
        + """
printf 'STANDARD_RESULT=%s\n' "$STANDARD_DISPATCH_AGENTS"
"""
    )


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _artifact_record(path: Path) -> dict[str, str | int]:
    data = path.read_bytes()
    return {
        "basename": path.name,
        "byte_length": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }


def _make_gate_case(tmp_path: Path, *, gate: bool = True) -> dict[str, Any]:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "config", "user.email", "test@example.com")
    (repo / "tracked.txt").write_text("base\n")
    _git(repo, "add", "tracked.txt")
    _git(repo, "commit", "-qm", "base")
    _git(repo, "branch", "base")
    (repo / "tracked.txt").write_text("head\n")
    _git(repo, "commit", "-qam", "head")

    output_dir = tmp_path / "review-output"
    output_dir.mkdir()
    annotated = output_dir / "annotated_diff_7.txt"
    ranges = output_dir / "ranges_7.json"
    valid_lines = output_dir / "valid_lines_7.json"
    metrics_path = output_dir / "metrics_7.json"
    annotated.write_text("metadata\n[L1]+old-generation\n")
    ranges.write_text('{"tracked.txt":[[1,1]]}\n')
    valid_lines.write_text('{"tracked.txt":[1]}\n')

    head_sha = _git(repo, "rev-parse", "HEAD")
    base_sha = _git(repo, "rev-parse", "base")
    merge_base_sha = _git(repo, "merge-base", base_sha, head_sha)
    metrics: dict[str, Any] = {
        "_head_sha": head_sha,
        "_base_sha": base_sha,
        "_merge_base_sha": merge_base_sha,
        "generation_id": "generation-1",
        "diff_sha256": "a" * 64,
        "diff_byte_length": 17,
        "review_mode": "local",
        "diff_source": {
            "comparison": "merge_base_to_head",
            "context_lines": 3,
            "external_diff": False,
            "kind": "local_git",
            "profile_id": "local_git_pinned_v1",
            "rename_detection": "50%",
            "text_conversion": False,
        },
        "artifacts": {
            "annotated_diff": _artifact_record(annotated),
            "hunk_ranges": _artifact_record(ranges),
            "valid_lines": _artifact_record(valid_lines),
        },
        "dispatch_agents": ["tests", "cohesion"],
        "run_overengineering_audits": gate,
    }
    metrics_path.write_text(json.dumps(metrics, sort_keys=True) + "\n")

    env = {
        **os.environ,
        "REVIEW_OUTPUT_DIR": f"{output_dir}/",
        "MODE": "local",
        "base_branch": "base",
        "pr_number": "7",
        "diff_metrics_path": str(metrics_path),
        "annotated_diff_path": str(annotated),
        "hunk_ranges_path": str(ranges),
        "valid_lines_path": str(valid_lines),
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
    }


def _write_metrics(case: dict[str, Any]) -> None:
    case["metrics_path"].write_text(json.dumps(case["metrics"], sort_keys=True) + "\n")


def _run_gate(case: dict[str, Any]) -> tuple[str, str]:
    result = subprocess.run(
        ["bash", "-c", _gate_script()],
        cwd=case["repo"],
        env=case["env"],
        check=True,
        capture_output=True,
        text=True,
    )
    gate_line = next(
        line for line in result.stdout.splitlines() if line.startswith("GATE_RESULT=")
    )
    annotated_sha = next(
        line for line in result.stdout.splitlines() if line.startswith("ANNOTATED_SHA=")
    )
    return gate_line.removeprefix("GATE_RESULT="), annotated_sha.removeprefix("ANNOTATED_SHA=")


def test_skill_accepts_diff_metrics_path_argument():
    text = _skill_text()
    assert "diff_metrics_path" in text


def test_skill_defines_diff_size_gate_step():
    text = _skill_text()
    assert "dispatch_agents" in text


def test_small_diff_skips_defense_bugs_slop():
    text = _skill_text().lower()
    assert "small" in text


def test_small_diff_always_includes_tests_cohesion():
    text = _skill_text().lower()
    assert "tests" in text
    assert "cohesion" in text


def test_full_fanout_for_medium_and_large():
    text = _skill_text()
    for agent in ["arch", "tests", "defense", "bugs", "cohesion", "slop"]:
        assert agent in text


def test_step3_requires_single_message_dispatch():
    """Step 3 must contain explicit single-message parallel dispatch instruction."""
    import re

    text = _skill_text()
    step_blocks = re.split(r"(?m)^#{1,3}\s+Step\s+\d+", text)
    step3_blocks = [
        b
        for b in step_blocks
        if "DISPATCH_AGENTS" in b and ("spawn" in b.lower() or "task tool" in b.lower())
    ]
    assert step3_blocks, "Could not locate Step 3 (dispatch step) in review-pr SKILL.md"
    assert any("single message" in b.lower() for b in step3_blocks), (
        "review-pr/SKILL.md Step 3 must contain 'single message' dispatch "
        "instruction to prevent sequential subagent dispatch"
    )


def test_gate_validation_precedes_boolean_consumption() -> None:
    section = _section("### Step 2.7", "### Step 2.5")
    assert section.index("METRICS_MARKER_BEFORE") < section.index("run_overengineering_audits")
    assert section.index("artifact_digest_mismatch") < section.index("GATE_STATE=valid_true")
    assert 'type == "boolean"' in section
    assert "${mode}" not in section
    assert '[ "$MODE" = "local" ]' in section
    for initialized in (
        'CHECKOUT_MERGE_BASE_SHA=""',
        'LIVE_REFS=""',
        'DIFF_SHA256=""',
        'PROFILE_ID=""',
        'ANNOTATION_GENERATION_ID=""',
        'HTTP_STATUS=""',
    ):
        assert initialized in section


@pytest.mark.parametrize(
    ("gate", "expected_state", "expected_audit_state"),
    [
        (True, "valid_true", "pending"),
        (False, "valid_false", "not_required"),
    ],
)
def test_local_gate_block_executes_mode_authority(
    tmp_path: Path,
    gate: bool,
    expected_state: str,
    expected_audit_state: str,
) -> None:
    case = _make_gate_case(tmp_path, gate=gate)
    result, _ = _run_gate(case)
    assert result == f"{expected_state}|none|{expected_audit_state}|0"


@pytest.mark.parametrize("gate", [True, False])
def test_standard_dispatch_reads_retained_marker_and_preserves_adaptive_selection(
    tmp_path: Path,
    gate: bool,
) -> None:
    case = _make_gate_case(tmp_path, gate=gate)
    result = subprocess.run(
        ["bash", "-c", _adaptive_dispatch_script()],
        cwd=case["repo"],
        env=case["env"],
        check=True,
        capture_output=True,
        text=True,
    )

    dispatch_line = next(
        line for line in result.stdout.splitlines() if line.startswith("STANDARD_RESULT=")
    )
    assert dispatch_line == "STANDARD_RESULT=tests,cohesion"


@pytest.mark.parametrize(
    "reason",
    [
        "metrics_missing",
        "metrics_invalid_json",
        "manifest_missing",
        "manifest_invalid",
        "profile_invalid",
        "ref_missing",
        "snapshot_mismatch",
        "artifact_missing",
        "artifact_name_mismatch",
        "artifact_length_mismatch",
        "artifact_digest_mismatch",
        "marker_changed",
        "gate_missing",
        "gate_not_boolean",
    ],
)
def test_closed_gate_degradation_reasons_execute(tmp_path: Path, reason: str) -> None:
    case = _make_gate_case(tmp_path)
    metrics = case["metrics"]
    if reason == "metrics_missing":
        case["env"]["diff_metrics_path"] = str(case["output_dir"] / "missing.json")
    elif reason == "metrics_invalid_json":
        case["metrics_path"].write_text("{")
    elif reason == "manifest_missing":
        metrics.pop("artifacts")
        _write_metrics(case)
    elif reason == "manifest_invalid":
        metrics["diff_byte_length"] = "17"
        _write_metrics(case)
    elif reason == "profile_invalid":
        metrics["diff_source"]["profile_id"] = "wrong"
        _write_metrics(case)
    elif reason == "ref_missing":
        case["env"]["base_branch"] = "missing-base"
    elif reason == "snapshot_mismatch":
        metrics["_head_sha"] = "b" * 40
        _write_metrics(case)
    elif reason == "artifact_missing":
        case["annotated"].unlink()
    elif reason == "artifact_name_mismatch":
        replacement = case["output_dir"] / "wrong-name.txt"
        replacement.write_bytes(case["annotated"].read_bytes())
        case["env"]["annotated_diff_path"] = str(replacement)
    elif reason == "artifact_length_mismatch":
        metrics["artifacts"]["annotated_diff"]["byte_length"] += 1
        _write_metrics(case)
    elif reason == "artifact_digest_mismatch":
        metrics["artifacts"]["annotated_diff"]["sha256"] = "0" * 64
        _write_metrics(case)
    elif reason == "marker_changed":
        fake_bin = case["output_dir"] / "bin"
        fake_bin.mkdir()
        wrapper = fake_bin / "sha256sum"
        wrapper.write_text(
            "#!/bin/sh\n"
            'if [ ! -e "$MUTATION_SENTINEL" ]; then\n'
            '  : > "$MUTATION_SENTINEL"\n'
            '  printf "\\n" >> "$MUTATE_MARKER_PATH"\n'
            "fi\n"
            'exec /usr/bin/sha256sum "$@"\n'
        )
        wrapper.chmod(0o755)
        case["env"]["PATH"] = f"{fake_bin}:{case['env']['PATH']}"
        case["env"]["MUTATION_SENTINEL"] = str(case["output_dir"] / "mutated")
        case["env"]["MUTATE_MARKER_PATH"] = str(case["metrics_path"])
    elif reason == "gate_missing":
        metrics.pop("run_overengineering_audits")
        _write_metrics(case)
    elif reason == "gate_not_boolean":
        metrics["run_overengineering_audits"] = "true"
        _write_metrics(case)

    result, _ = _run_gate(case)
    state, actual_reason, audit_state, revalidate_status = result.split("|")
    assert (state, actual_reason) == ("degraded", reason)
    assert audit_state != "pending"
    assert revalidate_status == "1"


def test_overlapping_sidecar_replacement_never_reaches_effect_revalidation(tmp_path: Path) -> None:
    case = _make_gate_case(tmp_path)
    old_body = "[L1]+old-generation"
    expected_sha = hashlib.sha256(old_body.encode()).hexdigest()
    fake_bin = case["output_dir"] / "bin"
    fake_bin.mkdir()
    wrapper = fake_bin / "sha256sum"
    wrapper.write_text(
        "#!/bin/sh\n"
        'if [ ! -e "$MUTATION_SENTINEL" ]; then\n'
        '  : > "$MUTATION_SENTINEL"\n'
        '  printf "metadata\\n[L1]+new-generation\\n" > "$MUTATE_SIDECAR_PATH"\n'
        "fi\n"
        'exec /usr/bin/sha256sum "$@"\n'
    )
    wrapper.chmod(0o755)
    case["env"]["PATH"] = f"{fake_bin}:{case['env']['PATH']}"
    case["env"]["MUTATION_SENTINEL"] = str(case["output_dir"] / "mutated")
    case["env"]["MUTATE_SIDECAR_PATH"] = str(case["annotated"])

    result, annotated_sha = _run_gate(case)
    assert result == "valid_true|none|pending|1"
    assert annotated_sha == expected_sha


def test_standard_and_experimental_dispatch_are_separate() -> None:
    section = _section("### Step 2.9", "### Step 3")
    assert "STANDARD_DISPATCH_AGENTS" in section
    assert "EXPERIMENTAL_DISPATCH_AGENTS" in section
    assert "STANDARD_AGENT_ALLOWLIST" in section
    assert "EXPERIMENTAL_AGENT_ALLOWLIST" in section
    assert "intersection" in section.lower()
    assert "deletion_context" in section


def test_true_gate_dispatches_both_registered_agents_once() -> None:
    section = _section("### Step 3", "### Step 4")
    for name in (
        "autoskillit:pr-review-auditor-reachability",
        "autoskillit:pr-review-auditor-abstraction-surface",
    ):
        assert section.count(f'Agent(subagent_type="{name}", model="sonnet")') == 1
    assert "ANNOTATED_DIFF" in section
    assert "VALID_DIFF_LINES" in section
    assert "fixed configured agent order" in section


def test_candidate_validation_requires_non_empty_nested_claims() -> None:
    section = _section("### Step 4", "### Step 4.5")
    assert "`file`, `message`, and `simpler_behavior` are non-empty strings" in section
    assert "every `path`, `role`, and `claim` is a non-empty" in section
    assert "every `path` and `relation` is a non-empty" in section
    assert "every boundary `claim` is a non-empty" in section


def test_parent_adjudication_verifies_every_semantic_claim() -> None:
    section = _section("### Step 4", "### Step 4.5")
    for obligation in (
        "every role-labelled evidence claim",
        "every one of the seven boundary claims",
        "every hop in the complete ordered trace",
        "semantic equivalence",
        "the parent may not accept a sampled subset",
    ):
        assert obligation in section


def test_standard_fallback_never_contains_experimental_agents() -> None:
    section = _section("### Step 2.9", "### Step 3")
    fallback = section[section.index("all six standard agents") :]
    assert "pr-review-auditor-reachability" not in fallback
    assert "pr-review-auditor-abstraction-surface" not in fallback
