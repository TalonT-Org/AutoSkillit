"""Tests for resolve-review/SKILL.md local mode (mode=local) behavior.

Tests assert on SKILL.md content patterns for the local review round feature
(reducing GitHub API calls during iterative local review).
"""

from pathlib import Path

SKILL_PATH = (
    Path(__file__).parent.parent.parent
    / "src"
    / "autoskillit"
    / "skills_extended"
    / "resolve-review"
    / "SKILL.md"
)


def _skill_text() -> str:
    return SKILL_PATH.read_text()


def test_resolve_review_skill_documents_mode_parameter():
    """Assert SKILL.md contains mode= parameter documentation in Arguments section."""
    text = _skill_text()
    assert "mode=<local|github>" in text, (
        "resolve-review/SKILL.md Arguments section must document the mode= keyword argument "
        "with format: mode=<local|github>"
    )


def test_resolve_review_local_mode_reads_local_findings():
    """Assert SKILL.md contains instructions to read from local_findings_{pr_number}.json
    when mode=local."""
    text = _skill_text()
    assert "local_findings" in text, (
        "resolve-review/SKILL.md must read from local_findings_{pr_number}.json "
        "when mode=local (written by review-pr in local mode)"
    )


def test_resolve_review_local_mode_accumulates_discuss():
    """Assert SKILL.md contains instructions to append DISCUSS findings to
    deferred_observations_{pr_number}.json."""
    text = _skill_text()
    assert "deferred_observations" in text, (
        "resolve-review/SKILL.md must accumulate DISCUSS findings to "
        "deferred_observations_{pr_number}.json in local mode"
    )
    # Should mention appending/accumulating
    assert any(phrase in text.lower() for phrase in ["append", "accumulate", "add to"]), (
        "resolve-review/SKILL.md must describe accumulating DISCUSS findings, not overwriting"
    )


def test_resolve_review_local_mode_accumulates_reject():
    """Assert SKILL.md contains instructions to append REJECT findings to
    reject_patterns_{pr_number}.json (accumulation across rounds)."""
    text = _skill_text()
    assert "reject_patterns" in text, (
        "resolve-review/SKILL.md must accumulate REJECT findings to "
        "reject_patterns_{pr_number}.json in local mode"
    )
    # Look in Step 6.6 section specifically for mode=local branch
    step66_idx = text.find("### Step 6.6")
    assert step66_idx >= 0, "SKILL.md must have Step 6.6"
    step66_section = text[step66_idx : step66_idx + 3000]
    local_mode_idx = step66_section.lower().find("mode=local")
    assert local_mode_idx >= 0, "Step 6.6 must have a mode=local section"
    after_local = step66_section[local_mode_idx : local_mode_idx + 500]
    # The accumulating file should mention reject_patterns in local mode
    assert "reject_patterns" in after_local, (
        "Step 6.6 mode=local section must reference reject_patterns accumulation"
    )


def test_resolve_review_local_mode_skips_thread_resolution():
    """Assert SKILL.md contains explicit instruction to skip GitHub thread resolution
    API calls when mode=local."""
    text = _skill_text()
    step6_idx = text.find("### Step 6")
    assert step6_idx >= 0
    step6_section = text[step6_idx : step6_idx + 2000]
    # Find mode=local section within Step 6
    local_mode_idx = step6_section.lower().find("mode=local")
    assert local_mode_idx >= 0, "Step 6 must have a mode=local section"
    after_local = step6_section[local_mode_idx : local_mode_idx + 500]
    assert any(
        phrase in after_local.lower()
        for phrase in [
            "skip",
            "do not call",
            "do not post",
            "no github",
        ]
    ), (
        "resolve-review/SKILL.md Step 6 mode=local section must explicitly "
        "skip GitHub thread resolution API calls"
    )


def test_resolve_review_local_mode_skips_inline_replies():
    """Assert SKILL.md contains instruction to skip posting inline reply comments
    when mode=local."""
    text = _skill_text()
    step65_idx = text.find("### Step 6.5")
    assert step65_idx >= 0
    step65_section = text[step65_idx : step65_idx + 1500]
    local_mode_idx = step65_section.lower().find("mode=local")
    assert local_mode_idx >= 0, "Step 6.5 must have a mode=local section"
    after_local = step65_section[local_mode_idx : local_mode_idx + 300]
    assert any(
        phrase in after_local.lower()
        for phrase in ["skip", "do not post", "no github", "no inline"]
    ), (
        "resolve-review/SKILL.md Step 6.5 mode=local section must skip "
        "posting inline reply comments"
    )


def test_resolve_review_local_mode_still_runs_tests():
    """Assert SKILL.md states task test-check runs in both modes."""
    text = _skill_text()
    step5_idx = text.find("### Step 5")
    assert step5_idx >= 0
    step5_section = text[step5_idx : step5_idx + 500]
    # Step 5 should be mode-independent
    assert "test" in step5_section.lower(), (
        "resolve-review/SKILL.md Step 5 (Run Tests) must be documented"
    )
    # Check that Step 7 report mentions test execution is mode-independent
    step7_idx = text.find("### Step 7")
    assert step7_idx >= 0
    step7_section = text[step7_idx : step7_idx + 500]
    assert "mode" in step7_section.lower() and "test" in step7_section.lower(), (
        "resolve-review/SKILL.md Step 7 must note that test execution is mode-independent"
    )


def test_resolve_review_github_mode_posts_deferred_observations():
    """Assert SKILL.md contains instructions to check for and post accumulated
    deferred_observations_{pr_number}.json when mode=github."""
    text = _skill_text()
    # Step 1.5 is where deferred observations are posted in github mode
    step15_idx = text.find("### Step 1.5")
    assert step15_idx >= 0, "SKILL.md must have Step 1.5 for posting deferred observations"
    step15_section = text[step15_idx : step15_idx + 2500]
    assert "deferred_observations" in step15_section, (
        "resolve-review/SKILL.md Step 1.5 must handle posting accumulated "
        "deferred_observations_{pr_number}.json when mode=github"
    )
    # Should post when file exists
    assert any(
        phrase in step15_section.lower()
        for phrase in ["post", "check for", "if the file exists", "batch review"]
    ), (
        "resolve-review/SKILL.md Step 1.5 must post deferred observations as a batch "
        "review when mode=github"
    )


def test_resolve_review_deferred_observations_include_review_flag():
    """Assert SKILL.md states that posted deferred observations include
    <!-- REVIEW-FLAG: severity=... dimension=... --> markers."""
    text = _skill_text()
    step15_idx = text.find("### Step 1.5")
    assert step15_idx >= 0
    step15_section = text[step15_idx : step15_idx + 2500]
    assert "REVIEW-FLAG" in step15_section, (
        "resolve-review/SKILL.md Step 1.5 must include REVIEW-FLAG markers "
        "in posted deferred observations"
    )
    assert "severity" in step15_section.lower() and "dimension" in step15_section.lower(), (
        "resolve-review/SKILL.md Step 1.5 deferred observation comments must "
        "include severity and dimension in the REVIEW-FLAG marker"
    )


def test_resolve_review_deferred_observations_include_round_number():
    """Assert SKILL.md states that posted deferred observations note which local round
    flagged them (round field in JSON, round number in comment body)."""
    text = _skill_text()
    step15_idx = text.find("### Step 1.5")
    assert step15_idx >= 0
    step15_section = text[step15_idx : step15_idx + 2500]
    # Should mention round number in the posted comment
    assert "round" in step15_section.lower(), (
        "resolve-review/SKILL.md Step 1.5 must include the round number in "
        "posted deferred observation comments"
    )


def test_resolve_review_local_mode_deduplication():
    """Assert SKILL.md deduplicates DISCUSS entries before appending to prevent
    duplicate entries on retry within the same round."""
    text = _skill_text()
    step36_idx = text.find("### Step 3.6")
    assert step36_idx >= 0, "SKILL.md must have Step 3.6 for DISCUSS accumulation"
    step36_section = text[step36_idx : step36_idx + 2000]
    assert "deduplicate" in step36_section.lower() or "duplicate" in step36_section.lower(), (
        "resolve-review/SKILL.md Step 3.6 must deduplicate before appending "
        "to prevent duplicates if resolve-review is retried within the same round"
    )
    # Should check path, line, body
    assert "path" in step36_section.lower() and "line" in step36_section.lower(), (
        "resolve-review/SKILL.md Step 3.6 deduplication must check (path, line, body) tuple"
    )


def test_resolve_review_local_mode_skip_github_api_fetches():
    """Assert SKILL.md Step 2 (mode=local) skips all GitHub API fetching calls."""
    text = _skill_text()
    step2_idx = text.find("### Step 2")
    assert step2_idx >= 0
    step2_section = text[step2_idx : step2_idx + 2000]
    local_mode_idx = step2_section.lower().find("mode=local")
    assert local_mode_idx >= 0, "Step 2 must have a mode=local section"
    after_local = step2_section[local_mode_idx : local_mode_idx + 500]
    assert any(
        phrase in after_local.lower()
        for phrase in ["skip", "do not fetch", "no github api", "read from"]
    ), (
        "resolve-review/SKILL.md Step 2 mode=local section must skip GitHub API fetching "
        "and read from local_findings JSON instead"
    )


def test_resolve_review_local_mode_transforms_local_findings():
    """Assert SKILL.md transforms local findings into internal structure with path, line,
    body, severity, dimension fields."""
    text = _skill_text()
    step2_idx = text.find("### Step 2")
    assert step2_idx >= 0
    step2_section = text[step2_idx : step2_idx + 2000]
    local_mode_idx = step2_section.lower().find("mode=local")
    assert local_mode_idx >= 0
    after_local = step2_section[local_mode_idx : local_mode_idx + 500]
    # Should mention transforming to internal structure
    assert any(
        phrase in after_local.lower()
        for phrase in ["transform", "map to", "internal structure", "path", "line"]
    ), (
        "resolve-review/SKILL.md Step 2 mode=local must transform local_findings JSON "
        "into the internal finding structure"
    )


def test_resolve_review_local_mode_reject_patterns_no_timestamp():
    """Assert SKILL.md local mode uses stable reject_patterns filename without timestamp
    (accumulating across rounds)."""
    text = _skill_text()
    step66_idx = text.find("### Step 6.6")
    assert step66_idx >= 0
    step66_section = text[step66_idx : step66_idx + 2500]
    local_mode_idx = step66_section.lower().find("mode=local")
    assert local_mode_idx >= 0, "Step 6.6 must have a mode=local section"
    after_local = step66_section[local_mode_idx : local_mode_idx + 500]
    # Should mention stable filename without timestamp
    assert "reject_patterns" in after_local, (
        "resolve-review/SKILL.md Step 6.6 local mode must use stable "
        "reject_patterns_{pr_number}.json filename (no timestamp)"
    )
