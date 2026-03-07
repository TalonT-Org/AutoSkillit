"""Contract tests: issue-splitter skill correctness and triage-issues integration."""

from __future__ import annotations

import re

import pytest

from autoskillit.workspace.skills import bundled_skills_dir

skills_dir = bundled_skills_dir()


def skill_text(name: str) -> str:
    return (skills_dir / name / "SKILL.md").read_text()


# 1. Skill file exists
def test_issue_splitter_skill_exists() -> None:
    path = skills_dir / "issue-splitter" / "SKILL.md"
    assert path.exists()


# 2. Output block delimiters documented
def test_output_block_delimiters_documented() -> None:
    text = skill_text("issue-splitter")
    assert "---issue-splitter-result---" in text
    assert "---/issue-splitter-result---" in text


# 3. All gh label create calls include --force
def test_gh_label_creates_include_force() -> None:
    text = skill_text("issue-splitter")
    for line in text.splitlines():
        if "gh label create" in line:
            assert "--force" in line, f"Missing --force: {line}"


# 4. No batch:N labels on GitHub objects
def test_no_batch_labels_on_github_objects() -> None:
    text = skill_text("issue-splitter")
    for line in text.splitlines():
        if ("gh issue edit" in line or "add-label" in line) and re.search(r"batch:\d+", line):
            pytest.fail(f"batch:N label found on GitHub object: {line}")


# 5. Only known recipe routes applied as labels
def test_only_known_recipe_routes_applied() -> None:
    text = skill_text("issue-splitter")
    valid = {"recipe:implementation", "recipe:remediation"}
    for line in text.splitlines():
        if "add-label" in line:
            for match in re.findall(r"recipe:[a-z-]+", line):
                assert match in valid, f"Unknown route label: {match}"


# 6. split label applied to parent on split
def test_split_label_applied_to_parent() -> None:
    text = skill_text("issue-splitter")
    assert '"split"' in text or "'split'" in text
    assert "--add-label" in text


# 7. split-from label applied to sub-issues
def test_split_from_label_on_sub_issues() -> None:
    text = skill_text("issue-splitter")
    assert "split-from:#" in text


# 8. dry-run prevents GitHub mutations
def test_dry_run_flag_documented() -> None:
    text = skill_text("issue-splitter")
    assert "--dry-run" in text


# 9. --no-label skips GitHub write operations
def test_no_label_flag_skips_github_ops() -> None:
    text = skill_text("issue-splitter")
    assert "--no-label" in text


# 10. Max sub-issues cap enforced
def test_max_sub_issues_cap_documented() -> None:
    text = skill_text("issue-splitter")
    assert any(n in text for n in ["max-sub-issues", "max_sub_issues", "cap at", "maximum"])


# 11. Parent issue preserved (not closed)
def test_parent_preserved_as_tracking_issue() -> None:
    text = skill_text("issue-splitter")
    assert "tracking" in text.lower()
    assert "gh issue close" not in text


# 12. triage-issues integrates issue-splitter before classification
def test_triage_issues_calls_issue_splitter() -> None:
    text = skill_text("triage-issues")
    assert "issue-splitter" in text


# 13. Split step appears before classification step in triage-issues
def test_split_step_before_classification_in_triage() -> None:
    text = skill_text("triage-issues")
    split_pos = text.find("issue-splitter")
    classify_pos = text.find("Recipe Classification")
    if classify_pos == -1:
        classify_pos = text.find("recipe route")
    assert split_pos != -1, "issue-splitter must appear in triage-issues"
    assert classify_pos != -1, "Classification step must appear in triage-issues"
    assert split_pos < classify_pos, "split step must appear before classification"
