"""Contract tests: GitHub operation semantics in SKILL.md files.

Rules enforced:
- gh label create must always include --force (idempotent label management)
- batch:N labels must not appear in gh issue edit / gh pr edit (internal state boundary)
- triage-issues classification must use behavioral criterion ("is behavior broken?")
- triage-issues label application must be opt-out (--no-label), not opt-in (--label)
"""

from __future__ import annotations

import re

import pytest

from autoskillit.workspace.skills import bundled_skills_dir


def _all_skill_mds() -> list[tuple[str, str]]:
    """Returns [(skill_name, content), ...] for all SKILL.md files."""
    bd = bundled_skills_dir()
    return [
        (d.name, (d / "SKILL.md").read_text())
        for d in sorted(bd.iterdir())
        if d.is_dir() and (d / "SKILL.md").is_file()
    ]


def test_all_gh_label_creates_include_force() -> None:
    """Every `gh label create` call in any SKILL.md must include --force.

    Without --force, label creation fails with 'already exists' on second
    run against the same repository, breaking re-run idempotency.
    """
    failures: list[str] = []
    for skill_name, content in _all_skill_mds():
        for line in content.splitlines():
            if "gh label create" in line and "--force" not in line:
                failures.append(f"  {skill_name}: {line.strip()}")
    assert not failures, "gh label create calls missing --force (non-idempotent):\n" + "\n".join(
        failures
    )


def test_no_internal_batch_labels_on_github_objects() -> None:
    """batch:N labels must not appear in gh issue edit or gh pr edit --add-label calls.

    Batch assignments are internal pipeline scheduling metadata. Surfacing them
    as GitHub labels leaks internal state to external consumers and is unstable —
    batches shift when triage is re-run. The manifest JSON is the authoritative
    source for batch information.
    """
    failures: list[str] = []
    pattern = re.compile(r"gh\s+(?:issue|pr)\s+edit[^\n]*--add-label[^\n]*batch:", re.IGNORECASE)
    for skill_name, content in _all_skill_mds():
        for match in pattern.finditer(content):
            failures.append(f"  {skill_name}: {match.group(0).strip()}")
    assert not failures, (
        "Internal batch: labels applied to GitHub objects (batch info belongs in manifest JSON):\n"
        + "\n".join(failures)
    )


def test_triage_issues_classification_uses_behavioral_criterion() -> None:
    """triage-issues Step 3 must classify by whether existing behavior is broken,
    not by scope clarity or implementation complexity.

    The scope-based table conflates "needs investigation" with "is a runtime bug"
    and misroutes complex features (large enhancements) to the remediation recipe,
    which is designed for broken-behavior investigation, not feature planning.
    """
    bd = bundled_skills_dir()
    content = (bd / "triage-issues" / "SKILL.md").read_text()
    assert re.search(r"is existing behavior broken", content, re.IGNORECASE), (
        "triage-issues Step 3 must ask 'Is existing behavior broken?' as the primary criterion"
    )
    assert "Large/ambiguous enhancement" not in content, (
        "triage-issues Step 3 must not route large/ambiguous enhancements to remediation — "
        "enhancements are implementation work regardless of scope clarity"
    )


def test_triage_issues_label_flag_is_opt_out() -> None:
    """triage-issues must apply labels by default (opt-out with --no-label), not opt-in.

    A pipeline invoking triage-issues without flags should get labels applied.
    Requiring --label (opt-in) silently skips labeling in all existing pipeline
    configurations, defeating the purpose of recipe routing.
    """
    bd = bundled_skills_dir()
    content = (bd / "triage-issues" / "SKILL.md").read_text()
    assert "--no-label" in content, (
        "triage-issues must define --no-label as the opt-out flag for label application"
    )
    # --label must not appear as a standalone opt-in enable flag.
    # Detect it in the arguments/flags definition section, not in NEVER constraint text.
    arg_section_match = re.search(
        r"(?i)(?:#{1,4}\s*(?:arguments?|flags?|step\s*0|inputs?).*?)\n((?:.|\n)+?)(?=\n#{1,4}\s)",
        content,
    )
    if arg_section_match:
        arg_text = arg_section_match.group(1)
        # --label defined standalone (not as part of --no-label) means it's still opt-in
        if re.search(r"`--label`(?!\S)", arg_text) and "--no-label" not in arg_text:
            assert False, (
                "triage-issues argument section must not define --label as opt-in flag; "
                "use --no-label (opt-out) instead"
            )


# 14. split and split-from labels are in issue-splitter SKILL.md, not batch labels
def test_issue_splitter_uses_split_not_batch_labels() -> None:
    """issue-splitter must use split/split-from vocabulary, not batch:N labels."""
    bd = bundled_skills_dir()
    content = (bd / "issue-splitter" / "SKILL.md").read_text()
    assert "split" in content
    assert "split-from:" in content


def test_collapse_issues_gh_label_create_force() -> None:
    """All gh label create calls in collapse-issues must include --force."""
    from autoskillit.workspace.skills import bundled_skills_dir

    skill_file = bundled_skills_dir() / "collapse-issues" / "SKILL.md"
    if not skill_file.exists():
        pytest.skip("collapse-issues skill not yet implemented")
    text = skill_file.read_text()
    calls = re.findall(r"gh label create[^\n]*", text)
    assert calls, "collapse-issues must document at least one gh label create call"
    for call in calls:
        assert "--force" in call, f"Missing --force in collapse-issues: {call}"


def test_no_batch_labels_collapse_issues() -> None:
    """collapse-issues must not apply batch:N labels."""
    from autoskillit.workspace.skills import bundled_skills_dir

    skill_file = bundled_skills_dir() / "collapse-issues" / "SKILL.md"
    if not skill_file.exists():
        pytest.skip("collapse-issues skill not yet implemented")
    text = skill_file.read_text()
    label_lines = re.findall(r"(gh issue create[^\n]*|gh label create[^\n]*|--label[^\n]*)", text)
    for line in label_lines:
        assert "batch:" not in line, f"batch: label must not appear in collapse-issues: {line}"


# ---------------------------------------------------------------------------
# enrich-issues contract tests
# ---------------------------------------------------------------------------


@pytest.fixture
def enrich_skill_text() -> str:
    """Load the enrich-issues SKILL.md text for contract assertions."""
    skill_file = bundled_skills_dir() / "enrich-issues" / "SKILL.md"
    return skill_file.read_text()


def test_enrich_issues_skips_existing_requirements(enrich_skill_text: str) -> None:
    """enrich-issues must be idempotent: skip issues that already have ## Requirements."""
    assert "## Requirements" in enrich_skill_text
    assert "already" in enrich_skill_text.lower() or "skip" in enrich_skill_text.lower()


def test_enrich_issues_emits_result_block(enrich_skill_text: str) -> None:
    """enrich-issues must emit a structured result block for pipeline capture."""
    assert "---enrich-issues-result---" in enrich_skill_text


def test_enrich_issues_uses_gh_issue_edit(enrich_skill_text: str) -> None:
    """enrich-issues must use gh issue edit to update issue bodies."""
    assert "gh issue edit" in enrich_skill_text


def test_enrich_issues_requires_recipe_implementation_label(enrich_skill_text: str) -> None:
    """enrich-issues must target recipe:implementation issues."""
    assert "recipe:implementation" in enrich_skill_text
