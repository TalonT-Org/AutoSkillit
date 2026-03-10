"""PR eligibility gate logic: CI status and review status filtering.

Used by the analyze-prs skill to partition PRs into eligible, CI-blocked,
and review-blocked lists before merge ordering.
"""

from __future__ import annotations

_CI_PASSING_CONCLUSIONS = frozenset({"success", "skipped", "neutral"})


def is_ci_passing(checks: list[dict]) -> bool:
    """Return True if all CI checks pass (success/skipped/neutral), False otherwise.

    A PR fails the CI gate if any check has:
    - conclusion=None (in-progress / not yet complete)
    - conclusion not in {success, skipped, neutral} (failure, cancelled, etc.)
    """
    for check in checks:
        conclusion = check.get("conclusion")
        if conclusion is None:
            return False
        if conclusion not in _CI_PASSING_CONCLUSIONS:
            return False
    return True


def is_review_passing(reviews: list[dict]) -> bool:
    """Return True if no unresolved CHANGES_REQUESTED reviews exist."""
    return not any(r.get("state") == "CHANGES_REQUESTED" for r in reviews)


def partition_prs(
    prs: list[dict],
    checks_by_number: dict[int, list[dict]],
    reviews_by_number: dict[int, list[dict]],
) -> dict:
    """Partition PRs into eligible, ci_blocked, and review_blocked lists.

    Each PR in ``prs`` must have at minimum: ``number`` (int) and ``title`` (str).

    Returns a dict with keys:
      - eligible_prs: list of PR dicts that passed both gates
      - ci_blocked_prs: list of {number, title, reason} for CI-failing PRs
      - review_blocked_prs: list of {number, title, reason} for review-blocked PRs
    """
    eligible: list[dict] = []
    ci_blocked: list[dict] = []
    review_blocked: list[dict] = []

    for pr in prs:
        number = pr["number"]
        title = pr.get("title", "")

        checks = checks_by_number.get(number, [])
        if not is_ci_passing(checks):
            failing = sum(
                1
                for c in checks
                if c.get("conclusion") is not None
                and c.get("conclusion") not in _CI_PASSING_CONCLUSIONS
            )
            in_progress = sum(1 for c in checks if c.get("conclusion") is None)
            reason = f"CI failing: {failing} failed, {in_progress} in-progress"
            ci_blocked.append({"number": number, "title": title, "reason": reason})
            continue

        reviews = reviews_by_number.get(number, [])
        if not is_review_passing(reviews):
            count = sum(1 for r in reviews if r.get("state") == "CHANGES_REQUESTED")
            reason = f"{count} unresolved CHANGES_REQUESTED review(s)"
            review_blocked.append({"number": number, "title": title, "reason": reason})
            continue

        eligible.append(pr)

    return {
        "eligible_prs": eligible,
        "ci_blocked_prs": ci_blocked,
        "review_blocked_prs": review_blocked,
    }
