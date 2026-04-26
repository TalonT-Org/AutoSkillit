"""Structural guards for conflict resolution safeguards.

Analogous to tests/recipe/test_merge_prs.py — validates that documented
interfaces exist in SKILL.md files and the merge-prs recipe, preventing
silent regression if sections are accidentally removed.
"""

import re

import pytest
import yaml

from autoskillit.core.paths import pkg_root

PROJECT_ROOT = pkg_root()
SKILLS_ROOT = pkg_root() / "skills_extended"
RECIPE_PATH = pkg_root() / "recipes" / "merge-prs.yaml"


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
        line
        for line in step_35_section.splitlines()
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


def test_merge_prs_captures_escalation_required(recipe):
    """merge_pr step must capture escalation_required from skill output."""
    merge_pr = recipe["steps"]["merge_pr"]
    capture = merge_pr.get("capture", {})
    assert "escalation_required" in capture, (
        "merge_pr capture block must include escalation_required"
    )


def test_merge_prs_routes_escalation_to_stop(recipe):
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
        entry
        for entry in on_result
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
        entry
        for entry in on_result
        if isinstance(entry, dict) and "needs_plan" in entry.get("when", "")
    ]
    if needs_plan_entries:
        needs_plan_idx = on_result.index(needs_plan_entries[0])
        assert escalation_idx < needs_plan_idx, (
            "escalation_required route must appear before needs_plan routes in on_result "
            "so escalation is not shadowed by needs_plan=false matching first"
        )
    # Verify escalate_stop is a defined step in the recipe
    assert "escalate_stop" in recipe["steps"], "escalate_stop must be a defined step in the recipe"


@pytest.fixture(scope="function")
def skill_contracts_yaml():
    from autoskillit.core.io import load_yaml

    contracts_path = pkg_root() / "recipe" / "skill_contracts.yaml"
    return load_yaml(contracts_path)


@pytest.fixture(scope="function")
def merge_prs_recipe():
    from autoskillit.core.io import load_yaml

    return load_yaml(RECIPE_PATH)


def _skill_text(skill_name: str) -> str:
    return (SKILLS_ROOT / skill_name / "SKILL.md").read_text()


# ── New: resolve-merge-conflicts skill structure ────────────────────────────


def test_resolve_merge_conflicts_skill_exists():
    skill_path = SKILLS_ROOT / "resolve-merge-conflicts" / "SKILL.md"
    assert skill_path.exists(), "resolve-merge-conflicts/SKILL.md must exist"


def test_resolve_merge_conflicts_has_goal_analysis():
    text = _skill_text("resolve-merge-conflicts")
    assert "Determine intent" in text, (
        "resolve-merge-conflicts must contain 'Determine intent' section headings "
        "describing goal-aware analysis of ours/theirs sides"
    )


def test_resolve_merge_conflicts_has_confidence_threshold():
    text = _skill_text("resolve-merge-conflicts")
    for level in ("HIGH", "MEDIUM", "LOW"):
        assert level in text, (
            f"resolve-merge-conflicts must define confidence level {level!r} "
            "in its confidence threshold table"
        )


def test_resolve_merge_conflicts_has_escalation_output():
    text = _skill_text("resolve-merge-conflicts")
    assert "escalation_required" in text, (
        "resolve-merge-conflicts must emit escalation_required in its output contract"
    )


def test_resolve_merge_conflicts_aborts_on_low_confidence():
    text = _skill_text("resolve-merge-conflicts")
    assert "git rebase --abort" in text, (
        "resolve-merge-conflicts must call git rebase --abort before escalating"
    )


def test_resolve_merge_conflicts_never_runs_full_test_suite():
    text = _skill_text("resolve-merge-conflicts")
    assert "task test-all" not in text and "task test-check" not in text, (
        "resolve-merge-conflicts must NOT run the full test suite; that is the test step's job"
    )


def test_resolve_merge_conflicts_in_skill_contracts():
    contracts_path = pkg_root() / "recipe" / "skill_contracts.yaml"
    contracts = yaml.safe_load(contracts_path.read_text())
    assert "resolve-merge-conflicts" in contracts.get("skills", {}), (
        "skill_contracts.yaml must declare resolve-merge-conflicts as a key under 'skills'"
    )


def test_resolve_merge_conflicts_emits_conflict_report_path():
    """resolve-merge-conflicts SKILL.md must declare conflict_report_path= emit."""
    skill_md_path = SKILLS_ROOT / "resolve-merge-conflicts" / "SKILL.md"
    text = skill_md_path.read_text()
    assert "conflict_report_path" in text, (
        "resolve-merge-conflicts/SKILL.md must emit conflict_report_path= "
        "as an output token after successful resolution"
    )


def test_resolve_merge_conflicts_writes_decision_report():
    """resolve-merge-conflicts SKILL.md must describe writing a report file."""
    skill_md_path = SKILLS_ROOT / "resolve-merge-conflicts" / "SKILL.md"
    text = skill_md_path.read_text()
    assert "conflict_resolution_report_" in text, (
        "resolve-merge-conflicts/SKILL.md must document writing "
        "conflict_resolution_report_*.md to temp/"
    )


def test_resolve_merge_conflicts_report_has_required_columns():
    """Report table must include all five required columns per REQ-RPT-001."""
    skill_md_path = SKILLS_ROOT / "resolve-merge-conflicts" / "SKILL.md"
    text = skill_md_path.read_text()
    for column in ("File", "Category", "Confidence", "Strategy", "Justification"):
        assert column in text, (
            f"resolve-merge-conflicts/SKILL.md report format must include "
            f"'{column}' column per REQ-RPT-001"
        )


def test_resolve_merge_conflicts_report_has_summary_header():
    """Report must document a summary header with worktree path and counts per REQ-RPT-002."""
    skill_md_path = SKILLS_ROOT / "resolve-merge-conflicts" / "SKILL.md"
    text = skill_md_path.read_text()
    assert "Files Conflicting" in text or "Files Resolved" in text, (
        "resolve-merge-conflicts/SKILL.md must document a summary header "
        "with conflict/resolved file counts per REQ-RPT-002"
    )


def test_resolve_merge_conflicts_contract_has_conflict_report_path(skill_contracts_yaml):
    """skill_contracts.yaml must include conflict_report_path in resolve-merge-conflicts."""
    outputs = skill_contracts_yaml["skills"]["resolve-merge-conflicts"]["outputs"]
    output_names = [o["name"] for o in outputs]
    assert "conflict_report_path" in output_names, (
        "skill_contracts.yaml must declare conflict_report_path as an output "
        "of resolve-merge-conflicts per REQ-CTR-001"
    )


def test_merge_prs_captures_conflict_report_from_resolve_integration(merge_prs_recipe):
    """resolve_integration_conflicts step must capture conflict_report_path per REQ-PIP-001."""
    step = merge_prs_recipe["steps"]["resolve_integration_conflicts"]
    capture_block = {**(step.get("capture") or {}), **(step.get("capture_list") or {})}
    assert any("conflict_report_path" in v for v in capture_block.values()), (
        "merge-prs.yaml resolve_integration_conflicts step must capture "
        "conflict_report_path per REQ-PIP-001"
    )


def test_merge_prs_captures_conflict_report_from_resolve_ejected(merge_prs_recipe):
    """resolve_ejected_conflicts step must also capture conflict_report_path."""
    step = merge_prs_recipe["steps"]["resolve_ejected_conflicts"]
    capture_block = {**(step.get("capture") or {}), **(step.get("capture_list") or {})}
    assert any("conflict_report_path" in v for v in capture_block.values()), (
        "merge-prs.yaml resolve_ejected_conflicts step must capture "
        "conflict_report_path via capture_list"
    )


def test_open_integration_pr_embeds_conflict_resolution_decisions():
    """open-integration-pr SKILL.md must embed Conflict Resolution Decisions section."""
    skill_md_path = SKILLS_ROOT / "open-integration-pr" / "SKILL.md"
    text = skill_md_path.read_text()
    assert "Conflict Resolution Decisions" in text, (
        "open-integration-pr/SKILL.md must describe embedding a "
        "'Conflict Resolution Decisions' section per REQ-PIP-002"
    )


def test_compose_pr_embeds_conflict_resolution_decisions():
    """compose-pr SKILL.md must embed Conflict Resolution Decisions section."""
    skill_md_path = SKILLS_ROOT / "compose-pr" / "SKILL.md"
    text = skill_md_path.read_text()
    assert "Conflict Resolution Decisions" in text, (
        "compose-pr/SKILL.md must describe embedding a "
        "'Conflict Resolution Decisions' section per REQ-PIP-003"
    )


def test_audit_impl_cross_references_conflict_report():
    """audit-impl SKILL.md must describe cross-referencing the conflict resolution report."""
    skill_md_path = SKILLS_ROOT / "audit-impl" / "SKILL.md"
    text = skill_md_path.read_text()
    assert "conflict_report_path" in text or "conflict resolution report" in text.lower(), (
        "audit-impl/SKILL.md must describe cross-referencing conflict_report_paths "
        "against the original plan per REQ-AUD-001"
    )


# ── New: resolve-merge-conflicts REMOTE variable guards ─────────────────────


@pytest.fixture(scope="module")
def skill_md() -> str:
    return (SKILLS_ROOT / "resolve-merge-conflicts" / "SKILL.md").read_text(encoding="utf-8")


def test_resolve_merge_conflicts_remote_variable_is_defined(skill_md: str) -> None:
    """SKILL.md bash blocks must assign REMOTE using the upstream-or-origin fallback pattern."""
    bash_blocks = re.findall(r"```bash\s*\n(.*?)```", skill_md, re.DOTALL)
    assert bash_blocks, (
        "resolve-merge-conflicts SKILL.md has no bash blocks — cannot verify REMOTE assignment"
    )
    joined = "\n".join(bash_blocks)
    # Require the upstream-or-origin pattern, not merely any REMOTE= assignment.
    # Pattern: git remote get-url upstream ... && echo upstream ... || echo origin
    upstream_or_origin = re.search(
        r"REMOTE=\$\(.*?get-url\s+upstream.*?\|\|\s*echo\s+origin\s*\)",
        joined,
        re.DOTALL,
    )
    assert upstream_or_origin is not None, (
        "resolve-merge-conflicts SKILL.md bash blocks must assign REMOTE using the "
        "upstream-or-origin fallback pattern: "
        "REMOTE=$(git remote get-url upstream >/dev/null 2>&1 && echo upstream || echo origin). "
        "A bare 'REMOTE=origin' or similar trivial assignment was found instead."
    )


def test_resolve_merge_conflicts_has_manifest_validation_step(skill_md: str) -> None:
    """SKILL.md must document language-aware manifest validation after pre-commit (Step 5a)."""
    assert "cargo metadata" in skill_md or "manifest validation" in skill_md.lower(), (
        "resolve-merge-conflicts SKILL.md must include language-aware manifest validation "
        "(Step 5a) covering at minimum 'cargo metadata --no-deps' for Rust projects"
    )
    assert "Step 5a" in skill_md, "Manifest validation must be documented as Step 5a in SKILL.md"


def test_resolve_merge_conflicts_has_duplicate_key_scan(skill_md: str) -> None:
    """SKILL.md must document duplicate key scanning in TOML/JSON manifests (Step 5b)."""
    assert "Step 5b" in skill_md, (
        "Duplicate key scanning must be documented as Step 5b in SKILL.md"
    )
    assert "duplicate" in skill_md.lower(), (
        "SKILL.md must use the word 'duplicate' to describe the key scan"
    )


def test_resolve_merge_conflicts_manifest_failure_escalates(skill_md: str) -> None:
    """SKILL.md must escalate (not auto-fix) when manifest validation fails."""
    step_5a_idx = skill_md.find("Step 5a")
    step_5b_idx = skill_md.find("Step 5b", step_5a_idx)
    step_5a_section = (
        skill_md[step_5a_idx:step_5b_idx] if step_5b_idx != -1 else skill_md[step_5a_idx:]
    )
    assert "escalation_required" in step_5a_section, (
        "Step 5a must escalate (emit escalation_required=true) on manifest validation failure; "
        "auto-fixing broken manifests is out of scope for this skill"
    )


def test_resolve_merge_conflicts_manifest_validation_covers_rust(skill_md: str) -> None:
    """SKILL.md manifest validation must cover Rust (cargo metadata --no-deps)."""
    assert "cargo metadata" in skill_md, (
        "resolve-merge-conflicts Step 5a must include 'cargo metadata --no-deps' "
        "as the Rust manifest validation command — this is a fast parse-only check (<1s)"
    )


def test_resolve_merge_conflicts_manifest_validation_covers_python(skill_md: str) -> None:
    """SKILL.md manifest validation must cover Python (tomllib or uv lock --check)."""
    assert "tomllib" in skill_md or "uv lock --check" in skill_md, (
        "resolve-merge-conflicts Step 5a must include Python manifest validation "
        "via tomllib.load() or 'uv lock --check'"
    )


def test_resolve_merge_conflicts_no_hardcoded_origin(skill_md: str) -> None:
    """SKILL.md must not use literal 'origin' as remote in git fetch/rebase/show/log/rev-parse."""
    bash_blocks = re.findall(r"```bash\s*\n(.*?)```", skill_md, re.DOTALL)
    violations = []
    for block in bash_blocks:
        for line in block.splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if not re.search(r"\bgit\s+(?:fetch|rebase|log|show|rev-parse)\b", stripped):
                continue
            # 'origin' as a literal word (not $origin / ${origin})
            if re.search(r"(?<!\$)(?<!\$\{)\borigin\b", stripped):
                violations.append(stripped)
    assert not violations, (
        "resolve-merge-conflicts SKILL.md bash blocks contain hardcoded 'origin' remote "
        "in git commands. Use $REMOTE (resolved via upstream || origin fallback) instead.\n"
        "Violations:\n" + "\n".join(f"  {v}" for v in violations)
    )


def test_resolve_merge_conflicts_step5_handles_version_consistency(skill_md: str) -> None:
    """Step 5 must instruct running sync_versions.py when check-version-consistency fails."""
    step5_idx = skill_md.find("### Step 5 —")
    step5a_idx = skill_md.find("### Step 5a —", step5_idx)
    assert step5_idx != -1, "Step 5 section must be present in SKILL.md"
    assert step5a_idx != -1, "Step 5a section must follow Step 5"
    step5_section = skill_md[step5_idx:step5a_idx]
    assert "sync_versions.py" in step5_section, (
        "Step 5 must instruct running 'python3 scripts/sync_versions.py' "
        "when check-version-consistency fails"
    )


def test_resolve_merge_conflicts_step5_handles_uv_lock(skill_md: str) -> None:
    """Step 5 must instruct running 'uv lock' when uv-lock-check fails."""
    step5_idx = skill_md.find("### Step 5 —")
    step5a_idx = skill_md.find("### Step 5a —", step5_idx)
    assert step5_idx != -1, "Step 5 section must be present in SKILL.md"
    assert step5a_idx != -1, "Step 5a section must follow Step 5"
    step5_section = skill_md[step5_idx:step5a_idx]
    # "uv lock --check" appears in Step 5a (manifest validation); require bare "uv lock"
    # to appear in Step 5 as the fix command (not merely the check)
    assert re.search(r"\buv lock\b(?!\s+--check)", step5_section), (
        "Step 5 must instruct running 'uv lock' (without --check) "
        "when uv-lock-check fails to regenerate the lock file"
    )


def test_resolve_merge_conflicts_step5_escalates_on_nonfixable_hooks(skill_md: str) -> None:
    """Step 5 must escalate when pre-commit still fails after all auto-fixes are applied."""
    step5_idx = skill_md.find("### Step 5 —")
    step5a_idx = skill_md.find("### Step 5a —", step5_idx)
    assert step5_idx != -1, "Step 5 section must be present in SKILL.md"
    assert step5a_idx != -1, "Step 5a section must follow Step 5"
    step5_section = skill_md[step5_idx:step5a_idx]
    assert "escalation_required" in step5_section, (
        "Step 5 must escalate with escalation_required=true when pre-commit still fails "
        "after all auto-fixes (ruff, sync_versions, uv lock) are applied — "
        "remaining failures from non-fixable hooks (mypy, gitleaks) require manual remediation"
    )
