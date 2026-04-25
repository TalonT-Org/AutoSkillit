"""Contract tests for the collapse-issues skill SKILL.md."""

from __future__ import annotations

import re

from autoskillit.workspace.skills import bundled_skills_extended_dir

skills_dir = bundled_skills_extended_dir()
COLLAPSE_SKILL = skills_dir / "collapse-issues" / "SKILL.md"
TRIAGE_SKILL = skills_dir / "triage-issues" / "SKILL.md"


def skill_text() -> str:
    return COLLAPSE_SKILL.read_text()


def triage_text() -> str:
    return TRIAGE_SKILL.read_text()


def test_skill_file_exists():
    assert COLLAPSE_SKILL.exists(), "collapse-issues/SKILL.md must exist"


def test_result_block_open_delimiter():
    assert "---collapse-issues-result---" in skill_text()


def test_result_block_close_delimiter():
    assert "---/collapse-issues-result---" in skill_text()


def test_gh_label_create_uses_force():
    text = skill_text()
    # Every `gh label create` call must include --force
    calls = re.findall(r"gh label create[^\n]*", text)
    assert calls, "Must document at least one gh label create call"
    for call in calls:
        assert "--force" in call, f"Missing --force in: {call}"


def test_no_batch_labels_on_combined_issues():
    text = skill_text()
    # batch:N labels must not appear in label creation or issue create commands
    label_lines = re.findall(r"(gh issue create[^\n]*|gh label create[^\n]*|--label[^\n]*)", text)
    for line in label_lines:
        assert "batch:" not in line, f"batch: label must not appear in: {line}"


def test_only_known_recipe_routes():
    text = skill_text()
    recipe_labels = re.findall(r"recipe:(\w[\w-]*)", text)
    known = {"implementation", "remediation"}
    unknown = set(recipe_labels) - known
    assert not unknown, f"Unknown recipe routes found: {unknown}"


def test_dry_run_flag_documented():
    assert "--dry-run" in skill_text()


def test_dry_run_skips_mutations():
    text = skill_text()
    # dry-run section must appear before gh issue create / gh issue close
    dry_idx = text.find("--dry-run")
    create_idx = text.find("gh issue create")
    close_idx = text.find("gh issue close")
    assert dry_idx < create_idx, "--dry-run gate must appear before gh issue create"
    assert dry_idx < close_idx, "--dry-run gate must appear before gh issue close"


def test_min_group_flag_documented():
    assert "--min-group" in skill_text()


def test_max_group_flag_documented():
    assert "--max-group" in skill_text()


def test_originals_superseded_body_edit():
    text = skill_text()
    assert "## Superseded" in text, "Must append ## Superseded section to original body"
    assert "gh issue edit" in text, "Must use gh issue edit to update original body"
    assert "gh issue close" in text, "Must still close originals"


def test_no_gh_issue_comment_in_collapse_issues():
    text = skill_text()
    assert "gh issue comment" not in text, (
        "Must not post issue comments — all updates go to issue body"
    )


def test_combined_issue_references_originals():
    text = skill_text()
    # Combined issue body must include a pattern showing it references the originals
    assert "Collapses:" in text or "collapsed-from" in text or "From #" in text, (
        "Combined issue must reference original issue numbers"
    )


def test_no_subagents_spawned():
    text = skill_text()
    # Grouping is in-context LLM reasoning — no subagents
    assert "subagent" not in text.lower(), "collapse-issues must not use subagents"
    assert "Task tool" not in text, "collapse-issues must not use Task tool"


def test_from_section_in_combined_body():
    text = skill_text()
    # Combined issue body structure: ## From #N: <title>
    assert "## From #" in text or "From #" in text, (
        "Combined issue body must include per-original sections headed by issue number"
    )


def test_triage_issues_references_collapse_issues():
    text = triage_text()
    assert "collapse-issues" in text, "triage-issues must document optional --collapse integration"


def test_collapse_issues_uses_per_issue_fetch():
    """collapse-issues must call fetch_github_issue per-issue before body assembly.

    The bulk gh issue list endpoint truncates bodies. Per-issue fetch via the
    MCP tool hits the REST single-issue endpoint which always returns the full body.
    """
    assert "fetch_github_issue" in skill_text(), (
        "collapse-issues must call fetch_github_issue per-issue before assembling "
        "the combined body — gh issue list truncates bodies"
    )


def test_collapse_issues_never_summarize():
    """collapse-issues NEVER block must explicitly forbid summarizing source bodies.

    Without an explicit NEVER constraint, the LLM defaults to concise output
    when filling in body sections, producing summaries or hyperlinks instead
    of verbatim content.
    """
    text = skill_text()
    lower = text.lower()
    assert "summarize" in lower or "paraphrase" in lower or "abbreviate" in lower, (
        "collapse-issues NEVER block must forbid summarizing, paraphrasing, or "
        "abbreviating source issue body content"
    )


def test_collapse_issues_no_angle_bracket_body_placeholder():
    """collapse-issues must not use angle-bracket syntax for body-copy instructions.

    <full body of issue N, verbatim> is parsed as a fill-in-the-blank template
    by the LLM — the word 'verbatim' inside the angle brackets blends into the
    placeholder label and is not treated as a separate constraint. Explicit
    imperative prose must be used instead.
    """
    text = skill_text()
    # Detect any angle-bracket token that references body content of issues
    pattern = re.compile(r"<[^>]*(body|content)\s+of\s+(issue|#)", re.IGNORECASE)
    matches = pattern.findall(text)
    assert not matches, (
        "collapse-issues must not use angle-bracket placeholder syntax for "
        "body-copy instructions — use explicit imperative language instead"
    )


def test_collapse_issues_gh_issue_create_uses_body_file():
    """collapse-issues gh issue create must use --body-file, not inline --body."""
    text = skill_text()
    create_pos = text.find("gh issue create")
    assert create_pos != -1, "Sanity: 'gh issue create' not found in collapse-issues"
    create_context = text[create_pos : create_pos + 300]
    assert "--body-file" in create_context, (
        "collapse-issues 'gh issue create' must use --body-file for the combined body, "
        "not inline --body — the combined body is large verbatim multi-issue content"
    )


def test_collapse_issues_body_file_uses_autoskillit_temp():
    """collapse-issues must write combined body to AUTOSKILLIT_TEMP/collapse-issues/."""
    text = skill_text()
    assert "AUTOSKILLIT_TEMP" in text, (
        "collapse-issues must write combined body to {{AUTOSKILLIT_TEMP}}/collapse-issues/ "
        "before calling gh issue create --body-file"
    )


def test_collapse_issues_never_inline_body_for_create():
    """collapse-issues CRITICAL CONSTRAINTS must prohibit inline --body for gh issue create."""
    text = skill_text()
    never_pos = text.find("**NEVER:**")
    assert never_pos != -1, "Sanity: '**NEVER:**' block not found"
    always_pos = text.find("**ALWAYS:**", never_pos)
    never_block = (
        text[never_pos:always_pos] if always_pos != -1 else text[never_pos : never_pos + 800]
    )
    lower = never_block.lower()
    assert "--body" in never_block and "inline" in lower, (
        "collapse-issues NEVER block must prohibit inline '--body' "
        "for combined-body issue creation"
    )
