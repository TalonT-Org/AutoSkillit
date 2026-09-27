"""Behavioral guard tests for review-pr adaptive subagent dispatch."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from autoskillit.smoke_utils import plan_review_audit_slots
from autoskillit.workspace.skills._format import read_skill_frontmatter
from tests.skills._review_pr_gate_helpers import (
    GATE_SCRIPT,
    make_gate_case,
    snapshot,
    write_metrics,
)

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


def _gate_bash_block() -> str:
    section = _section("### Step 2.7", "### Step 2.5")
    return next(
        body
        for body in re.findall(r"```bash\n(.*?)```", section, re.DOTALL)
        if 'review_pr_gate.sh" snapshot' in body
    )


def _plan_slots(
    metrics_marker: object,
    *,
    gate_state: str,
    deletion_merge_base: str = "",
) -> dict[str, object]:
    return plan_review_audit_slots(
        gate_state=gate_state,
        metrics_marker=metrics_marker,
        annotated_diff="",
        valid_diff_lines={},
        deletion_merge_base=deletion_merge_base,
        audit_run_id="a" * 16,
    )


def _slots(planned: dict[str, object]) -> list[dict[str, object]]:
    slots = planned["slots"]
    assert isinstance(slots, list)
    return slots


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
    step3 = _section("### Step 3", "### Step 4")
    assert "AUDIT_SLOTS" in step3
    assert "spawn" in step3.lower() or "task tool" in step3.lower()
    assert "single message" in step3.lower(), (
        "review-pr/SKILL.md Step 3 must contain 'single message' dispatch "
        "instruction to prevent sequential subagent dispatch"
    )


def test_skill_invokes_bundled_gate_without_inline_writes() -> None:
    section = _section("### Step 2.7", "### Step 2.5")
    gate_fence = _gate_bash_block()
    assert 'bash "{{AUTOSKILLIT_SCRIPTS}}/review_pr_gate.sh" snapshot' in section
    for old_shell_operation in ('cp -- "$', 'rm -f -- "$', "mktemp", "degrade_gate"):
        assert old_shell_operation not in gate_fence


def test_snapshot_failure_stops_before_evidence_verdict_and_github() -> None:
    section = _section("### Step 2.7", "### Step 2.5")
    after_call = section[section.index('review_pr_gate.sh" snapshot') :].lower()
    assert "non-zero" in after_call
    assert "needs_human" in after_call
    assert "stop" in after_call
    assert "evidence" in after_call
    assert "verdict" in after_call
    assert "github" in after_call


def test_review_pr_uses_literal_output_paths_and_executable_shell_fences() -> None:
    text = _skill_text()
    assert "${REVIEW_OUTPUT_DIR}" not in text
    assert "REVIEW_OUTPUT_DIR=" not in text
    step_0 = _section("### Step 0", "### Step 1")
    assert "AUTOSKILLIT_ALLOWED_WRITE_PREFIX" in step_0
    assert "printf" in step_0
    assert "mkdir -p" in step_0
    assert "pwd -P" in step_0
    assert "{review_output_dir}" in text
    assert ".tmp-{publish_id}" in text
    assert "mv" in text

    step_2_7 = _section("### Step 2.7", "### Step 2.5")
    for language, body in re.findall(r"```(\w+)\n(.*?)```", step_2_7, re.DOTALL):
        if re.search(r"^\s*[A-Za-z_][A-Za-z0-9_]*=\"?\$\(", body, re.MULTILINE):
            assert language == "bash"
    for body in re.findall(r"```text\n(.*?)```", text, re.DOTALL):
        assert not re.search(r"^\s*[A-Za-z_][A-Za-z0-9_]*=\"?\$\(", body, re.MULTILINE)


def test_live_pr_refs_use_supported_pull_request_api_fields() -> None:
    section = _section("### Step 2.7", "### Step 2.5")
    script = GATE_SCRIPT.read_text()
    assert script.count('gh api "repos/{owner}/{repo}/pulls/${pr_number}"') == 4
    assert "gh pr view" not in script
    assert 'gh api "repos/{owner}/{repo}/pulls/${pr_number}"' not in section


@pytest.mark.parametrize("gate", [True, False])
def test_standard_dispatch_reads_retained_marker_and_preserves_adaptive_selection(
    tmp_path: Path, gate: bool
) -> None:
    case = make_gate_case(tmp_path, gate=gate)
    case["metrics"]["dispatch_agents"] = ["cohesion", "tests"]
    write_metrics(case)
    result = snapshot(case)
    assert result.returncode == 0, result.stderr
    authority = json.loads(result.stdout)
    case["metrics"]["dispatch_agents"] = ["arch"]
    write_metrics(case)
    retained_marker = json.loads(Path(authority["metrics_marker_snapshot_path"]).read_text())
    slots = _plan_slots(
        retained_marker,
        gate_state=authority["state"],
    )
    standard_dimensions = [
        slot["dimension"] for slot in _slots(slots) if slot["kind"] == "standard"
    ]
    assert standard_dimensions == ["tests", "cohesion"]


def test_standard_dispatch_falls_back_to_all_six_for_missing_metrics() -> None:
    slots = _plan_slots({}, gate_state="valid_false")

    standard_dimensions = [
        slot["dimension"] for slot in _slots(slots) if slot["kind"] == "standard"
    ]
    assert standard_dimensions == ["arch", "tests", "defense", "bugs", "cohesion", "slop"]


def test_standard_and_experimental_dispatch_are_separate() -> None:
    section = _section("### Step 2.9", "### Step 3")
    assert "plan_review_audit(" in section
    assert "AUDIT_SLOTS" in section
    assert all(kind in section.lower() for kind in ("standard", "deletion", "experimental"))
    assert "separate authorities" in section.lower()


@pytest.mark.parametrize("gate_state", ["valid_true", "valid_false", "degraded"])
def test_deletion_slot_is_planned_independently_of_gate_state(gate_state: str) -> None:
    slots = _plan_slots({}, gate_state=gate_state, deletion_merge_base="merge-base-sha")

    assert any(slot["kind"] == "deletion" for slot in _slots(slots))


def test_true_gate_dispatches_both_registered_agents_once() -> None:
    section = _section("### Step 3", "### Step 4")
    parsed = read_skill_frontmatter(SKILL_PATH)
    assert parsed.data is not None
    requirements = parsed.data["semantic_requirements"]
    roles = {
        "pr-review-auditor-reachability",
        "pr-review-auditor-abstraction-surface",
    }
    assert {spawn["role"] for spawn in requirements["child_spawns"]} == roles
    assert {policy["role"] for policy in requirements["child_model_policies"]} == roles
    assert {policy["model_class"] for policy in requirements["child_model_policies"]} == {"sonnet"}
    assert "ANNOTATED_DIFF" in section
    assert "VALID_DIFF_LINES" in section
    assert "AUDIT_SLOTS` in order" in section


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
