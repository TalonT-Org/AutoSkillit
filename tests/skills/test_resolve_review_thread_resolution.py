from pathlib import Path

import pytest


@pytest.fixture
def resolve_review_skill_md() -> str:
    skill_path = (
        Path(__file__).parent.parent.parent
        / "src"
        / "autoskillit"
        / "skills"
        / "resolve-review"
        / "SKILL.md"
    )
    assert skill_path.exists(), f"SKILL.md not found at expected path: {skill_path}"
    return skill_path.read_text()


def test_skill_fetches_review_threads_via_graphql(resolve_review_skill_md: str) -> None:
    """Step 2 must include a gh api graphql call for reviewThreads."""
    assert "reviewThreads" in resolve_review_skill_md
    assert "gh api graphql" in resolve_review_skill_md


def test_skill_tracks_addressed_thread_ids(resolve_review_skill_md: str) -> None:
    """Step 4 must record thread node IDs for addressed (not skipped) findings."""
    assert "addressed_thread_ids" in resolve_review_skill_md


def test_skill_has_thread_resolution_step(resolve_review_skill_md: str) -> None:
    """A step must call resolveReviewThread for each addressed thread."""
    assert "resolveReviewThread" in resolve_review_skill_md


def test_skill_does_not_resolve_skipped_threads(resolve_review_skill_md: str) -> None:
    """Skipped findings must NOT be added to addressed_thread_ids."""
    # The skip section must explicitly exclude thread resolution.
    assert (
        "not resolve" in resolve_review_skill_md.lower()
        or "do not add" in resolve_review_skill_md.lower()
    )
    # addressed_thread_ids must appear in the apply-fix section (between "apply the fix"
    # and "skip a finding"), confirming tracking is wired to the fix path, not the skip path.
    apply_idx = resolve_review_skill_md.lower().find("apply the fix")
    skip_idx = resolve_review_skill_md.lower().find("skip a finding")
    assert apply_idx != -1, "SKILL.md must contain 'apply the fix'"
    assert skip_idx != -1, "SKILL.md must contain 'skip a finding'"
    apply_to_skip = resolve_review_skill_md[apply_idx:skip_idx]
    assert "addressed_thread_ids" in apply_to_skip, (
        "addressed_thread_ids must appear in the apply-fix flow, not only at initialization"
    )


def test_skill_logs_warning_on_resolve_failure(resolve_review_skill_md: str) -> None:
    """Thread resolution failure must log a warning and continue, not fail."""
    # Narrow checks to the Step 6 (Resolve Addressed Review Threads) section,
    # anchored at resolveReviewThread, to avoid false positives from unrelated sections.
    thread_start = resolve_review_skill_md.find("resolveReviewThread")
    assert thread_start != -1, "SKILL.md must contain resolveReviewThread"
    thread_context = resolve_review_skill_md[thread_start : thread_start + 600]
    thread_context_lower = thread_context.lower()
    assert "warn" in thread_context_lower or "log" in thread_context_lower, (
        "Step 6 must mention warning or logging on resolve failure"
    )
    assert "continue" in thread_context_lower or "proceed" in thread_context_lower, (
        "Step 6 must instruct to continue/proceed past resolve failure"
    )
    assert (
        "do not modify exit code" in thread_context_lower
        or "best-effort" in thread_context_lower
        or "does not affect" in thread_context_lower
    ), (
        "Step 6 must explicitly state thread resolution failure is"
        " best-effort / does not affect exit code"
    )


def test_skill_reports_thread_resolution_count(resolve_review_skill_md: str) -> None:
    """The Step 6 report block must include a threads-resolved line."""
    assert (
        "Threads resolved" in resolve_review_skill_md
        or "threads resolved" in resolve_review_skill_md.lower()
    )
