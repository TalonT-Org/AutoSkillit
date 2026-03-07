"""Contract tests for the collapse-issues skill SKILL.md."""

from __future__ import annotations

from autoskillit.workspace.skills import bundled_skills_dir

skills_dir = bundled_skills_dir()
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
    import re

    calls = re.findall(r"gh label create[^\n]*", text)
    assert calls, "Must document at least one gh label create call"
    for call in calls:
        assert "--force" in call, f"Missing --force in: {call}"


def test_no_batch_labels_on_combined_issues():
    text = skill_text()
    import re

    # batch:N labels must not appear in label creation or issue create commands
    label_lines = re.findall(r"(gh issue create[^\n]*|gh label create[^\n]*|--label[^\n]*)", text)
    for line in label_lines:
        assert "batch:" not in line, f"batch: label must not appear in: {line}"


def test_only_known_recipe_routes():
    text = skill_text()
    import re

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


def test_originals_closed_with_comment():
    text = skill_text()
    assert "gh issue comment" in text, "Must document closing comment on originals"
    assert "gh issue close" in text, "Must document closing originals"


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
