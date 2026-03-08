"""Structural guards for conflict resolution safeguards.

Analogous to tests/recipe/test_pr_merge_pipeline.py — validates that documented
interfaces exist in SKILL.md files and the pr-merge-pipeline recipe, preventing
silent regression if sections are accidentally removed.
"""
import pytest
import yaml
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.parent
SKILLS_ROOT = PROJECT_ROOT / "src" / "autoskillit" / "skills"
RECIPE_PATH = PROJECT_ROOT / "src" / "autoskillit" / "recipes" / "pr-merge-pipeline.yaml"


@pytest.fixture(scope="module")
def merge_pr_skill_text():
    return (SKILLS_ROOT / "merge-pr" / "SKILL.md").read_text()


@pytest.fixture(scope="module")
def audit_impl_skill_text():
    return (SKILLS_ROOT / "audit-impl" / "SKILL.md").read_text()


@pytest.fixture(scope="module")
def impl_no_merge_skill_text():
    return (SKILLS_ROOT / "implement-worktree-no-merge" / "SKILL.md").read_text()


@pytest.fixture(scope="module")
def recipe():
    return yaml.safe_load(RECIPE_PATH.read_text())


# --- merge-pr SKILL.md guards ---

def test_merge_pr_skill_fetches_all_pr_files(merge_pr_skill_text):
    """Step 3.5 must instruct fetching all files changed on the PR branch via git diff."""
    # Find the Step 3.5 section specifically — the command must appear there, not just anywhere
    step_35_idx = merge_pr_skill_text.find("Step 3.5")
    assert step_35_idx != -1, (
        "merge-pr must contain a 'Step 3.5: Fetch All PR-Changed Files' section"
    )
    step_4_idx = merge_pr_skill_text.find("Step 4", step_35_idx)
    step_35_section = (
        merge_pr_skill_text[step_35_idx:step_4_idx]
        if step_4_idx != -1
        else merge_pr_skill_text[step_35_idx:]
    )
    all_files_diff_lines = [
        line for line in step_35_section.splitlines()
        if "git diff" in line and "--name-only" in line and "--diff-filter" not in line
    ]
    assert all_files_diff_lines, (
        "Step 3.5 of merge-pr must contain a 'git diff --name-only' command without "
        "'--diff-filter' to fetch all PR-changed files, not just conflicted ones"
    )


def test_merge_pr_conflict_report_has_pr_changes_inventory(merge_pr_skill_text):
    """Conflict report template must include a PR Changes Inventory section."""
    assert "PR Changes Inventory" in merge_pr_skill_text


def test_merge_pr_conflict_report_has_three_categories(merge_pr_skill_text):
    """Conflict report must distinguish git conflicts, semantic overlaps, and clean carry-overs."""
    assert "Category A" in merge_pr_skill_text
    assert "Category B" in merge_pr_skill_text
    assert "Category C" in merge_pr_skill_text


def test_merge_pr_conflict_report_has_resolver_contract(merge_pr_skill_text):
    """Conflict report must contain a Resolver Contract section."""
    assert "Resolver Contract" in merge_pr_skill_text


def test_merge_pr_skill_has_escalation_signal(merge_pr_skill_text):
    """merge-pr must document escalation_required in the output contract (Step 5)."""
    step_5_idx = merge_pr_skill_text.find("Step 5")
    assert step_5_idx != -1, (
        "merge-pr must contain a 'Step 5: Return Result' output contract section"
    )
    step_5_section = merge_pr_skill_text[step_5_idx:]
    assert "escalation_required" in step_5_section, (
        "escalation_required output token must be documented in the output contract "
        "section (Step 5) of merge-pr SKILL.md"
    )


# --- audit-impl SKILL.md guards ---

def test_audit_impl_skill_has_conflict_resolution_context_check(audit_impl_skill_text):
    """audit-impl must detect PR Changes Inventory and verify Category C completeness."""
    assert "PR Changes Inventory" in audit_impl_skill_text
    assert "Category C" in audit_impl_skill_text


def test_audit_impl_skill_treats_missing_carryover_as_missing_finding(audit_impl_skill_text):
    """audit-impl must classify missing Category C files as MISSING findings."""
    # MISSING must appear in the conflict-resolution context, not just standard audit flow
    inventory_idx = audit_impl_skill_text.find("PR Changes Inventory")
    assert inventory_idx != -1, "PR Changes Inventory section required in audit-impl SKILL.md"
    assert "MISSING" in audit_impl_skill_text[inventory_idx:], (
        "audit-impl must reference MISSING in the PR Changes Inventory context"
    )


# --- implement-worktree-no-merge SKILL.md guards ---

def test_implement_no_merge_skill_has_completeness_self_check(impl_no_merge_skill_text):
    """implement-worktree-no-merge must verify Category C files before handoff."""
    assert "PR Changes Inventory" in impl_no_merge_skill_text
    assert "Category C" in impl_no_merge_skill_text


# --- recipe YAML guards ---

def test_pr_merge_pipeline_captures_escalation_required(recipe):
    """merge_pr step must capture escalation_required from skill output."""
    merge_pr = recipe["steps"]["merge_pr"]
    capture = merge_pr.get("capture", {})
    assert "escalation_required" in capture, (
        "merge_pr capture block must include escalation_required"
    )


def test_pr_merge_pipeline_routes_escalation_to_stop(recipe):
    """merge_pr routing must send escalation_required=true to escalate_stop as a PRIMARY route.

    The route must be a primary on_result entry (predicate conditions list), not buried in
    a fallthrough that is never reached. escalation_required must be checked before needs_plan
    because merge-pr emits needs_plan=false when escalation_required=true.
    """
    merge_pr = recipe["steps"]["merge_pr"]
    on_result = merge_pr.get("on_result", [])
    assert isinstance(on_result, list), (
        "merge_pr on_result must use predicate conditions format (list) so that "
        "escalation_required is evaluated before needs_plan"
    )
    escalation_entries = [
        entry for entry in on_result
        if isinstance(entry, dict)
        and "escalation_required" in entry.get("when", "")
        and entry.get("route") == "escalate_stop"
    ]
    assert escalation_entries, (
        "merge_pr on_result must contain a primary entry with 'escalation_required' in "
        "its when condition routing to escalate_stop"
    )
    # escalation_required entry must appear before any needs_plan entries
    escalation_idx = on_result.index(escalation_entries[0])
    needs_plan_entries = [
        entry for entry in on_result
        if isinstance(entry, dict) and "needs_plan" in entry.get("when", "")
    ]
    if needs_plan_entries:
        needs_plan_idx = on_result.index(needs_plan_entries[0])
        assert escalation_idx < needs_plan_idx, (
            "escalation_required route must appear before needs_plan routes in on_result "
            "so escalation is not shadowed by needs_plan=false matching first"
        )
    # Verify escalate_stop is a defined step in the recipe
    assert "escalate_stop" in recipe["steps"], (
        "escalate_stop must be a defined step in the recipe"
    )
