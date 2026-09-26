"""Tests for review-pr/SKILL.md local mode (mode=local) behavior.

Tests assert on SKILL.md content patterns for the local review round feature
(reducing GitHub API calls during iterative local review).
"""

from pathlib import Path

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


def test_review_pr_skill_documents_mode_parameter():
    """Assert SKILL.md contains mode= parameter documentation in Arguments section."""
    text = _skill_text()
    assert "mode=<local|github>" in text, (
        "review-pr/SKILL.md Arguments section must document the mode= keyword argument "
        "with format: mode=<local|github>"
    )


def test_review_pr_local_mode_writes_local_findings():
    """Assert SKILL.md contains instructions to write findings to local_findings_{pr_number}.json
    when mode=local."""
    text = _skill_text()
    assert "local_findings" in text, (
        "review-pr/SKILL.md must contain 'local_findings' when mode=local, "
        "specifying the output file path pattern: "
        "{{AUTOSKILLIT_TEMP}}/review-pr/local_findings_{pr_number}.json"
    )
    assert "mode=local" in text, "review-pr/SKILL.md must reference 'mode=local' behavior"


def test_review_pr_local_mode_no_github_posts():
    """Assert SKILL.md contains explicit instruction to skip GitHub API calls when mode=local."""
    text = _skill_text()
    # Find the mode=local section
    local_mode_idx = text.lower().find("mode=local")
    assert local_mode_idx >= 0, "SKILL.md must contain 'mode=local'"
    # Check that within the local mode section, GitHub API posting is skipped
    after_local = text[local_mode_idx:]
    # The mode=local section should say to skip GitHub API calls
    assert any(
        phrase in after_local.lower()
        for phrase in [
            "skip all github api",
            "skip github api",
            "no github api",
            "do not post to github",
            "do not call github",
        ]
    ), (
        "review-pr/SKILL.md mode=local section must explicitly instruct to skip "
        "GitHub API calls for posting comments"
    )


def test_review_pr_local_mode_emits_gate_tokens():
    """Assert SKILL.md states that gate tokens (%%REVIEW_GATE::*) are emitted in both modes."""
    text = _skill_text()
    # Gate tokens must be emitted in both modes
    assert "%%REVIEW_GATE::LOOP_REQUIRED%%" in text, (
        "review-pr/SKILL.md must emit %%REVIEW_GATE::LOOP_REQUIRED%% on changes_requested"
    )
    assert "%%REVIEW_GATE::CLEAR%%" in text, (
        "review-pr/SKILL.md must emit %%REVIEW_GATE::CLEAR%% on approved/needs_human"
    )
    # Verify gate tokens are documented as mode-independent
    gate_doc_idx = text.lower().find("gate token")
    assert gate_doc_idx >= 0, "SKILL.md must document gate tokens"
    # Check near gate token documentation that both modes are mentioned
    gate_context = text[max(0, gate_doc_idx - 200) : gate_doc_idx + 400].lower()
    assert "mode" in gate_context, (
        "Gate token documentation must mention that emission is mode-independent"
    )


def test_review_pr_local_mode_default_is_github():
    """Assert SKILL.md states that absent/unrecognized mode defaults to github."""
    text = _skill_text()
    # Find the mode documentation — use a phrase specific to mode default, not generic "default:"
    assert "absent or unrecognized" in text.lower() or 'default to "github"' in text.lower(), (
        "review-pr/SKILL.md must document the default value for mode parameter using a "
        "specific phrase like 'absent or unrecognized' or 'default to \"github\"'"
    )
    # Verify github is the default
    local_mode_idx = text.lower().find("mode=github")
    assert local_mode_idx >= 0
    # The github mode should be described as the default (absent/unrecognized)
    mode_context = text[max(0, local_mode_idx - 300) : local_mode_idx + 200]
    assert "default" in mode_context.lower() or "absent" in mode_context.lower(), (
        "review-pr/SKILL.md must state that mode=github is the default when "
        "mode is absent or unrecognized"
    )


def test_review_pr_local_mode_iteration_tracking():
    """Assert SKILL.md tracks iteration number when writing local_findings for round counting."""
    text = _skill_text()
    assert "iteration" in text.lower(), (
        "review-pr/SKILL.md must track iteration number when writing local_findings.json "
        "to support round counting across local review cycles"
    )


def test_review_pr_local_mode_still_writes_diff_context():
    """Assert SKILL.md still writes diff_context_{pr_number}.json in local mode."""
    text = _skill_text()
    # Find mode=local section
    local_mode_idx = text.lower().find("mode=local")
    assert local_mode_idx >= 0
    after_local = text[local_mode_idx : local_mode_idx + 2000]
    assert "diff_context" in after_local, (
        "review-pr/SKILL.md mode=local section must still write diff_context_{pr_number}.json "
        "(mode-independent handoff file for resolve-review)"
    )


def test_review_pr_local_mode_still_writes_raw_findings():
    """Assert SKILL.md still writes raw_findings_{pr_number}.json in local mode."""
    text = _skill_text()
    local_mode_idx = text.lower().find("mode=local")
    assert local_mode_idx >= 0
    after_local = text[local_mode_idx : local_mode_idx + 2000]
    assert "raw_findings" in after_local, (
        "review-pr/SKILL.md mode=local section must still write raw_findings_{pr_number}.json "
        "(mode-independent)"
    )


def test_review_pr_local_mode_skips_guarded_publication():
    """Assert mode=local preserves findings and bypasses the one publication call."""
    text = _skill_text()
    local_mode_idx = text.lower().find("mode=local")
    assert local_mode_idx >= 0
    after_local = text[local_mode_idx : local_mode_idx + 2000]
    assert "local_findings" in after_local
    assert any(
        phrase in after_local.lower()
        for phrase in ("skip publication", "do not post", "skip github", "no github api")
    ), (
        "review-pr/SKILL.md mode=local must bypass post_pr_review after writing "
        "local_findings while preserving normal verdict emission"
    )


def test_review_pr_local_mode_json_format():
    """Assert SKILL.md specifies the JSON schema for local_findings output."""
    text = _skill_text()
    # Find the local_findings JSON format description
    local_findings_idx = text.find("local_findings")
    assert local_findings_idx >= 0
    after_local_findings = text[local_findings_idx : local_findings_idx + 1500]
    # Should have fields like path, line, body, severity, dimension, verdict, iteration
    assert '"findings"' in after_local_findings or "findings" in after_local_findings, (
        "review-pr/SKILL.md must specify the findings array in local_findings JSON schema"
    )
    assert "iteration" in after_local_findings.lower(), (
        "review-pr/SKILL.md must include iteration field in local_findings JSON schema"
    )


def test_review_pr_step6_mode_branching_header():
    """Step 6 must branch local findings from the single guarded GitHub write."""
    text = _skill_text()
    step6_idx = text.find("### Step 6")
    assert step6_idx >= 0, "SKILL.md must contain Step 6"
    step7_idx = text.find("### Step 7", step6_idx)
    assert step7_idx > step6_idx, "Step 7 must follow Step 6"
    step6_section = text[step6_idx:step7_idx]
    assert "MODE" in step6_section or "mode" in step6_section, (
        "Step 6 must begin with mode branching to separate local vs github behavior"
    )
    assert step6_section.count("post_pr_review") == 1


def test_local_gate_passes_mode_and_checkout_root_to_executable() -> None:
    text = _skill_text()
    step_2_7 = text[
        text.index("### Step 2.7: Deterministic Diff Annotation") : text.index(
            "### Step 2.5: Deletion Context Pre-Computation"
        )
    ]

    assert (
        'review_pr_gate.sh" snapshot "{review_output_dir}" "{checkout_root}" "{mode}"' in step_2_7
    )
    assert "GATE_AUTHORITY" in step_2_7
    assert "authority_path" in step_2_7


def test_standalone_local_mode_prepares_missing_artifacts_once() -> None:
    text = _skill_text()
    step_2_7 = text[
        text.index("### Step 2.7: Deterministic Diff Annotation") : text.index(
            "### Step 2.5: Deletion Context Pre-Computation"
        )
    ]

    assert 'mktemp -d "{review_output_dir}annotation.XXXXXX"' in step_2_7
    assert step_2_7.count('callable="autoskillit.smoke_utils.annotate_pr_diff"') == 1
    assert '"pr_number": pr_number' in step_2_7
    assert '"cwd": "{checkout_root}"' in step_2_7
    assert '"output_dir": "{annotation_output_dir}"' in step_2_7
    assert '"base_branch": base_branch' in step_2_7
    assert '"mode": "local"' in step_2_7
    assert "timeout=120" in step_2_7
    assert 'work_dir="{checkout_root}"' in step_2_7


def test_standalone_preparation_binds_exact_returned_artifact_paths_before_gate() -> None:
    text = _skill_text()
    step_2_7 = text[
        text.index("### Step 2.7: Deterministic Diff Annotation") : text.index(
            "### Step 2.5: Deletion Context Pre-Computation"
        )
    ]
    gate_start = step_2_7.index('review_pr_gate.sh" snapshot')

    preparation = step_2_7[:gate_start]
    binding_start = preparation.index("On success, paste its returned")
    binding_end = preparation.index("as literal paths", binding_start)
    binding = preparation[binding_start:binding_end]
    for field in (
        "diff_metrics_path",
        "annotated_diff_path",
        "hunk_ranges_path",
        "valid_lines_path",
        "anchor_authority_path",
    ):
        assert f"`{field}`" in binding
    assert "Keep `anchor_authority_path` for its later consumer." in preparation
    gate_command = step_2_7[gate_start:].splitlines()[0]
    assert gate_command.endswith(
        '"{diff_metrics_path}" "{annotated_diff_path}" "{hunk_ranges_path}" "{valid_lines_path}"'
    )


def test_standalone_preparation_failure_stops_without_git_repair() -> None:
    text = _skill_text()
    step_2_7 = text[
        text.index("### Step 2.7: Deterministic Diff Annotation") : text.index(
            "### Step 2.5: Deletion Context Pre-Computation"
        )
    ]
    preparation = step_2_7[: step_2_7.index('review_pr_gate.sh" snapshot')]

    assert "needs_human" in preparation
    assert "%%REVIEW_GATE::CLEAR%%" in preparation
    assert "headless" in preparation.lower()
    assert "run_python" in preparation
    for forbidden in ("update-ref", "branch -f", "checkout -B", "switch -C", "reset --hard"):
        assert forbidden not in preparation


def test_review_pr_declares_observational_only_git_state_prohibition() -> None:
    text = _skill_text().lower()
    assert "observational" in text
    assert "must not rewrite refs" in text or "do not rewrite refs" in text
    assert "head" in text
    assert "index" in text
    assert "worktree" in text


def test_snapshot_authority_is_reused_for_revalidation() -> None:
    text = _skill_text()
    step_2_7 = text[
        text.index("### Step 2.7: Deterministic Diff Annotation") : text.index(
            "### Step 2.5: Deletion Context Pre-Computation"
        )
    ]
    assert "GATE_AUTHORITY" in step_2_7
    assert "authority_path" in step_2_7
    assert 'review_pr_gate.sh" revalidate' in step_2_7
    assert "revalidate_retained_snapshot" not in step_2_7


def test_local_handoff_is_generation_bound_and_published_last() -> None:
    text = _skill_text()
    step_6 = text[text.index("### Step 6") : text.index("### Step 7")]

    for field in (
        '"_head_sha"',
        '"_base_sha"',
        '"_merge_base_sha"',
        '"annotation_generation_id"',
        '"review_generation_id"',
    ):
        assert field in step_6
    assert "local_findings_{pr_number}.json` last" in step_6
    assert "same-directory temporary file and atomic rename" in text


def test_stale_snapshot_prevents_local_effect_handoff() -> None:
    text = _skill_text()
    aggregate = text[
        text.index("### Step 4: Aggregate and Deduplicate Findings") : text.index(
            "### Step 4.5: Echo Primary Obligation"
        )
    ]

    assert "FINAL_SNAPSHOT_STATE=stale" in aggregate
    assert "Do not publish diff context, local findings" in aggregate
    assert aggregate.index("FINAL_SNAPSHOT_STATE=stale") < aggregate.index(
        "Do not publish diff context, local findings"
    )
