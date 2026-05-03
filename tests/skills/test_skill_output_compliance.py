"""Tests that all SKILL.md output path instructions use HHMMSS-precision timestamps
and structured output tokens use 'key = value' format (spaces around =).
"""

from __future__ import annotations

import re

import pytest
import yaml

from autoskillit.core import pkg_root
from autoskillit.workspace.skills import DefaultSkillResolver

# Skills whose output files are intentionally fixed-name (no timestamp needed).
# These are idempotent by design — the filename IS the identity.
FIXED_NAME_SKILLS: frozenset[str] = frozenset(
    {
        "write-recipe",  # .autoskillit/recipes/{name}.yaml — idempotent
        "make-campaign",  # .autoskillit/recipes/campaigns/{name}.yaml — idempotent
        "migrate-recipes",  # .autoskillit/temp/migrations/{name}.yaml — idempotent
        "mermaid",  # edits existing files, no temp output
        "open-kitchen",  # singleton config file
        "close-kitchen",  # deletes singleton config file
        "sous-chef",  # no file output
        "smoke-task",  # no file output
        "enrich-issues",  # no file output
        "prepare-issue",  # no file output
        "collapse-issues",  # no file output
        "issue-splitter",  # no file output
        "report-bug",  # no file output
    }
)

# Regex matching date-only placeholders in output path instructions.
# Matches {YYYY-MM-DD} NOT followed by _ (which would indicate HHMMSS suffix).
# Also matches the ambiguous {date} placeholder.
DATE_ONLY_PATTERN = re.compile(
    r"\{YYYY-MM-DD\}(?!_)|"  # {YYYY-MM-DD} not followed by _HHMMSS
    r"\{date\}",  # ambiguous {date} placeholder
    re.IGNORECASE,
)

# Lines that contain output file path instructions (write/save directives).
# Matches both legacy literal ``.autoskillit/temp/`` and the placeholder
# ``{{AUTOSKILLIT_TEMP}}/`` form used by SKILL.md files post-substitution.
OUTPUT_PATH_LINE = re.compile(
    r"(?:write|save|output)\s+.*?(?:to|path|file)\s*[:=]?\s*"
    r"`?(?:\.autoskillit/temp/|\{\{AUTOSKILLIT_TEMP\}\}/)",
    re.IGNORECASE,
)

# Shared scratch files that should not be used by any skill.
SHARED_SCRATCH_FILES = {
    ".autoskillit/temp/arch-lens-selection.md",
    ".autoskillit/temp/pr-arch-lens-context.md",
}

# Regex matching structured output tokens: key=value with NO space around =.
# The correct format is: key = value (with spaces).
UNSPACED_OUTPUT_TOKEN = re.compile(
    r"^(?:plan_path|investigation_path|diagnosis_path|report_path|"
    r"review_path|worktree_path|branch_name|groups_path|manifest_path|"
    r"summary_path|analysis_path|config_path|recipe_path|triage_report|"
    r"triage_manifest|pr_order_file|analysis_file|conflict_report_path|"
    r"remediation_path|plan_parts|diagram_path|verdict|group_files|"
    r"pr_url|decision|needs_plan|deletion_regression|pr_number|"
    r"pr_branch|pr_title|total_issues|batch_count|recipe_distribution|"
    r"batch_branch|pr_count|simple_count|needs_check_count|"
    r"ci_blocked_count|review_blocked_count|queue_mode|"
    r"failure_type|is_fixable|escalation_required|escalation_reason|"
    r"execution_map|execution_map_report|group_count|review_approach_candidates|"
    r"merged)=[^\s]",
    re.MULTILINE,
)


def _get_file_producing_skills() -> list[str]:
    """Return skill names whose SKILL.md contains temp/ output path instructions."""
    resolver = DefaultSkillResolver()
    producing = []
    for info in resolver.list_all():
        if info.name not in FIXED_NAME_SKILLS:
            content = info.path.read_text()
            if OUTPUT_PATH_LINE.search(content):
                producing.append(info.name)
    return producing


def _get_skills_with_output_tokens() -> list[str]:
    """Return skill names whose SKILL.md contains structured output tokens."""
    resolver = DefaultSkillResolver()
    token_skills = []
    # Simple check for key = value or key=value pattern in unlabeled code blocks
    token_pattern = re.compile(
        r"^(?:plan_path|investigation_path|diagnosis_path|report_path|"
        r"review_path|worktree_path|branch_name|groups_path|manifest_path|"
        r"summary_path|analysis_path|config_path|recipe_path|triage_report|"
        r"triage_manifest|pr_order_file|analysis_file|conflict_report_path|"
        r"remediation_path|plan_parts|diagram_path|verdict|group_files|"
        r"pr_url|decision|needs_plan|deletion_regression|pr_number|"
        r"pr_branch|pr_title|total_issues|batch_count|recipe_distribution|"
        r"batch_branch|pr_count|simple_count|needs_check_count|"
        r"ci_blocked_count|review_blocked_count|queue_mode|"
        r"failure_type|is_fixable|escalation_required|escalation_reason|"
        r"execution_map|execution_map_report|group_count|review_approach_candidates|"
        r"merged)\s*=\s*",
        re.MULTILINE,
    )
    for info in resolver.list_all():
        content = info.path.read_text()
        if token_pattern.search(content):
            token_skills.append(info.name)
    return token_skills


@pytest.mark.parametrize("skill_name", _get_file_producing_skills())
def test_skill_output_uses_hhmmss_timestamp(skill_name: str) -> None:
    """Every file-producing skill must use {YYYY-MM-DD_HHMMSS} in output paths."""
    resolver = DefaultSkillResolver()
    info = resolver.resolve(skill_name)
    assert info is not None
    content = info.path.read_text()

    # Extract lines that reference temp/ output paths.
    output_lines = [
        line
        for line in content.splitlines()
        if re.search(r"(?:\.autoskillit/temp/|\{\{AUTOSKILLIT_TEMP\}\}/).*\{.*\}", line)
        and not line.strip().startswith("#")
    ]

    for line in output_lines:
        assert not DATE_ONLY_PATTERN.search(line), (
            f"Skill '{skill_name}' uses date-only timestamp in output path.\n"
            f"Line: {line.strip()}\n"
            f"Expected: {{YYYY-MM-DD_HHMMSS}} (second-precision)"
        )


@pytest.mark.parametrize("skill_name", _get_skills_with_output_tokens())
def test_structured_output_tokens_have_spaces(skill_name: str) -> None:
    """Structured output tokens must use 'key = value' format (spaces around =)."""
    resolver = DefaultSkillResolver()
    info = resolver.resolve(skill_name)
    assert info is not None
    content = info.path.read_text()

    matches = UNSPACED_OUTPUT_TOKEN.findall(content)
    assert not matches, (
        f"Skill '{skill_name}' uses unspaced output tokens.\n"
        f"Found: {matches}\n"
        f"Expected format: key = value (with spaces around =)"
    )


@pytest.mark.parametrize("skill_name", _get_file_producing_skills())
def test_no_shared_scratch_files(skill_name: str) -> None:
    """Skills must not write to shared fixed-name scratch files."""
    resolver = DefaultSkillResolver()
    info = resolver.resolve(skill_name)
    assert info is not None
    content = info.path.read_text()

    for scratch_file in SHARED_SCRATCH_FILES:
        assert scratch_file not in content, (
            f"Skill '{skill_name}' writes to shared scratch file '{scratch_file}'.\n"
            f"Use skill-scoped path: .autoskillit/temp/{skill_name}/... with timestamp instead."
        )


@pytest.mark.parametrize("skill_name", _get_file_producing_skills())
def test_no_namespace_prefix_in_output_paths(skill_name: str) -> None:
    """Output paths must not include the autoskillit: namespace prefix."""
    resolver = DefaultSkillResolver()
    info = resolver.resolve(skill_name)
    assert info is not None
    content = info.path.read_text()

    assert ".autoskillit/temp/autoskillit:" not in content, (
        f"Skill '{skill_name}' uses namespace prefix in output path.\n"
        f"Use bare skill name: .autoskillit/temp/{skill_name}/... (no autoskillit: prefix)."
    )
    assert "temp/autoskillit:" not in content, (
        f"Skill '{skill_name}' uses bare namespace prefix in output path.\n"
        f"Use bare skill name: .autoskillit/temp/{skill_name}/... (no autoskillit: prefix)."
    )


@pytest.mark.parametrize("skill_name", _get_file_producing_skills())
def test_file_producing_skills_have_cwd_anchor(skill_name: str) -> None:
    """Every file-producing skill must anchor temp/ writes to the current working directory."""
    resolver = DefaultSkillResolver()
    info = resolver.resolve(skill_name)
    assert info is not None
    content = info.path.read_text()

    assert re.search(r"current working directory", content, re.IGNORECASE), (
        f"Skill '{skill_name}' writes to temp/ but does not anchor paths to the "
        f"current working directory. Add '(relative to the current working directory)' "
        f"after the output path instruction."
    )


def test_output_path_tokens_synchronized() -> None:
    """_OUTPUT_PATH_TOKENS must match the expected path-bearing token set exactly."""
    from autoskillit.execution.headless import _OUTPUT_PATH_TOKENS

    # Static known-set of tokens whose values are filesystem paths.
    # Update this set when adding new path-bearing structured output tokens.
    expected_path_tokens = frozenset(
        {
            "plan_path",
            "plan_parts",
            "investigation_path",
            "diagnosis_path",
            "review_path",
            "groups_path",
            "group_files",
            "manifest_path",
            "summary_path",
            "remediation_path",
            "diagram_path",
            "triage_report",
            "triage_manifest",
            "pr_order_file",
            "analysis_file",
            "conflict_report_path",
            "recipe_path",
            # Research recipe skills (added in #504)
            "scope_report",
            "experiment_plan",
            "results_path",
            "report_path",
            # review-design skill outputs (added in #593)
            "evaluation_dashboard",
            "revision_guidance",
            # prepare-research-pr output (decomposed research-PR flow)
            "prep_path",
            # plan-visualization outputs (groupF Part A)
            "visualization_plan_path",
            "report_plan_path",
            # bundle-local-report output (groupG)
            "html_path",
            # stage-data skill output (resource feasibility report path)
            "resource_report",
            # make-campaign skill output (campaign recipe manifest path)
            "campaign_path",
            # build-execution-map outputs (dependency-aware dispatch map)
            "execution_map",
            "execution_map_report",
            # planner-generate-phases output (planner recipe)
            "phase_manifest_path",
            # planner-elaborate-phase output (parallel worker)
            "elab_result_path",
            # planner-refine-phases output
            "refined_plan_path",
            # planner-refine-assignments output
            "phase_refined_path",
            # planner-refine-wps output
            "refined_wps_path",
            # audit-tests output (bundled full-audit recipe)
            "audit_report_path",
            # validate-audit output (bundled full-audit recipe)
            "validated_report_path",
            # promote-to-main skill output (promote-to-main-wrapper recipe)
            "pr_body_path",
            # planner-validate-task-alignment output
            "alignment_findings_path",
            # planner-assess-review-approach output
            "review_approach_assessment_path",
        }
    )

    assert _OUTPUT_PATH_TOKENS == expected_path_tokens, (
        f"_OUTPUT_PATH_TOKENS mismatch.\n"
        f"Missing: {expected_path_tokens - _OUTPUT_PATH_TOKENS}\n"
        f"Extra: {_OUTPUT_PATH_TOKENS - expected_path_tokens}"
    )


# ---------------------------------------------------------------------------
# Path-capture structured output compliance
# ---------------------------------------------------------------------------

SKILL_CONTRACTS_PATH = pkg_root() / "recipe" / "skill_contracts.yaml"

# Skills with path-capture contracts that must have their token instruction
# in ## Critical Constraints (not only in ## Output or a late workflow step).
PATH_CAPTURE_SKILLS: dict[str, list[str]] = {
    "build-execution-map": ["execution_map", "execution_map_report"],
    "make-plan": ["plan_path"],
    "rectify": ["plan_path"],
    "investigate": ["investigation_path"],
    "make-groups": ["groups_path", "manifest_path", "group_files"],
    "review-approach": ["review_path"],
    "audit-impl": ["remediation_path"],
    "arch-lens-c4-container": ["diagram_path"],
    "arch-lens-process-flow": ["diagram_path"],
    "arch-lens-data-lineage": ["diagram_path"],
    "arch-lens-module-dependency": ["diagram_path"],
    "arch-lens-concurrency": ["diagram_path"],
    "arch-lens-error-resilience": ["diagram_path"],
    "arch-lens-repository-access": ["diagram_path"],
    "arch-lens-operational": ["diagram_path"],
    "arch-lens-security": ["diagram_path"],
    "arch-lens-development": ["diagram_path"],
    "arch-lens-scenarios": ["diagram_path"],
    "arch-lens-state-lifecycle": ["diagram_path"],
    "arch-lens-deployment": ["diagram_path"],
    "vis-lens-always-on": ["diagram_path"],
    "vis-lens-antipattern": ["diagram_path"],
    "vis-lens-caption-annot": ["diagram_path"],
    "vis-lens-chart-select": ["diagram_path"],
    "vis-lens-color-access": ["diagram_path"],
    "vis-lens-domain-norms": ["diagram_path"],
    "vis-lens-figure-table": ["diagram_path"],
    "vis-lens-multi-compare": ["diagram_path"],
    "vis-lens-reproducibility": ["diagram_path"],
    "vis-lens-story-arc": ["diagram_path"],
    "vis-lens-temporal": ["diagram_path"],
    "vis-lens-uncertainty": ["diagram_path"],
    "review-design": ["evaluation_dashboard", "revision_guidance"],
    "planner-assess-review-approach": ["review_approach_assessment_path"],
    "planner-elaborate-assignments": ["phase_assignments_result_dir"],
    "planner-elaborate-phase": ["elab_result_path"],
    "planner-elaborate-wps": ["phase_wps_result_dir"],
    "planner-generate-phases": ["phase_manifest_path"],
    "planner-refine-assignments": ["phase_refined_path"],
    "planner-refine-phases": ["refined_plan_path"],
    "planner-refine-wps": ["refined_wps_path"],
    "planner-validate-task-alignment": ["alignment_findings_path"],
    "audit-tests": ["audit_report_path"],
    "validate-audit": ["validated_report_path"],
    "bundle-local-report": ["html_path"],
}

ABSOLUTE_PATH_KEYWORDS = ("absolute", "/abs", "$(pwd)", "$(cd")


def _read_skill_md(skill_name: str) -> str:
    """Return the content of a skill's SKILL.md file."""
    for rel_dir in ("skills_extended", "skills"):
        path = pkg_root() / rel_dir / skill_name / "SKILL.md"
        if path.exists():
            return path.read_text()
    raise FileNotFoundError(f"SKILL.md not found for skill: {skill_name}")


def _extract_critical_constraints_section(skill_md: str) -> str:
    """Return the text of the ## Critical Constraints section only."""
    m = re.search(r"##\s+Critical Constraints\b(.+?)(?=\n##\s|\Z)", skill_md, re.DOTALL)
    return m.group(1) if m else ""


def _get_contracted_path_capture_skills() -> dict[str, list[str]]:
    """Return {skill_name: [token_names]} for skills with path-capture contracts."""
    raw = yaml.safe_load(SKILL_CONTRACTS_PATH.read_text())
    skills_data = raw.get("skills", {}) if isinstance(raw, dict) else {}
    result: dict[str, list[str]] = {}
    for skill_name, contract in skills_data.items():
        if not isinstance(contract, dict):
            continue
        path_token_names = {
            out["name"]
            for out in contract.get("outputs", [])
            if isinstance(out, dict)
            and (
                out.get("type", "").startswith("file_path") or out.get("type") == "directory_path"
            )
        }
        patterns = contract.get("expected_output_patterns", [])
        tokens = []
        for pattern in patterns:
            m = re.match(r"^(\w+)", pattern)
            if m and m.group(1) in path_token_names:
                tokens.append(m.group(1))
        if tokens:
            result[skill_name] = tokens
    return result


@pytest.mark.parametrize("skill_name,token_names", list(PATH_CAPTURE_SKILLS.items()))
def test_path_capture_token_instruction_in_critical_constraints(
    skill_name: str, token_names: list[str]
) -> None:
    """
    Every skill with a path-capture contract must instruct the model to emit
    the structured output token inside ## Critical Constraints.

    Late-positioned instructions (only in ## Output or a workflow step) are
    systematically under-weighted by the model in long-context sessions.
    """
    skill_md = _read_skill_md(skill_name)
    constraints_section = _extract_critical_constraints_section(skill_md)
    for token_name in token_names:
        assert token_name in constraints_section, (
            f"Skill '{skill_name}': token '{token_name}' must be referenced in "
            f"## Critical Constraints, not only in ## Output or a late workflow step. "
            f"Found section text:\n{constraints_section[:500]}"
        )


def test_every_contracted_skill_has_emit_instruction() -> None:
    """
    Every skill with a path-capture contract in skill_contracts.yaml must have
    an emit instruction for that token somewhere in its SKILL.md.

    A skill with a contract but no instruction will always produce CONTRACT_VIOLATION.
    """
    contracted = _get_contracted_path_capture_skills()
    missing: list[str] = []
    for skill_name, token_names in contracted.items():
        try:
            skill_md = _read_skill_md(skill_name)
        except FileNotFoundError:
            continue  # skill may be internal, skip
        for token_name in token_names:
            if token_name not in skill_md:
                missing.append(f"{skill_name}: missing emit instruction for '{token_name}'")
    assert not missing, (
        "Skills with path-capture contracts but no emit instruction in SKILL.md:\n"
        + "\n".join(f"  - {m}" for m in missing)
    )


def test_contracted_path_capture_skills_includes_backslash_s_patterns() -> None:
    """_get_contracted_path_capture_skills must return all skills with \\S+-terminated patterns."""
    raw = yaml.safe_load(SKILL_CONTRACTS_PATH.read_text())
    skills_data = raw.get("skills", {}) if isinstance(raw, dict) else {}

    # Dynamically find skills whose contracts include \S+-terminated path-capture patterns.
    backslash_s_pattern_re = re.compile(r"\\[Ss]\+\s*$")
    expected: list[str] = []
    for skill_name, contract in skills_data.items():
        if not isinstance(contract, dict):
            continue
        for pattern in contract.get("expected_output_patterns", []):
            if backslash_s_pattern_re.search(pattern):
                expected.append(skill_name)
                break

    assert expected, "No skills with \\S+-terminated patterns found in contracts — test is vacuous"

    contracted = _get_contracted_path_capture_skills()
    missing = [s for s in expected if s not in contracted]
    assert not missing, (
        f"Skills with \\S+-terminated patterns not returned by "
        f"_get_contracted_path_capture_skills: {missing}"
    )


@pytest.mark.parametrize("skill_name,token_names", list(PATH_CAPTURE_SKILLS.items()))
def test_path_capture_token_instruction_mentions_absolute(
    skill_name: str, token_names: list[str]
) -> None:
    """
    The token instruction in ## Critical Constraints must reference absolute path,
    not just say 'write to temp/' (relative path).
    """
    skill_md = _read_skill_md(skill_name)
    constraints_section = _extract_critical_constraints_section(skill_md)
    has_absolute_reference = any(
        kw in constraints_section.lower() for kw in ABSOLUTE_PATH_KEYWORDS
    )
    assert has_absolute_reference, (
        f"Skill '{skill_name}': ## Critical Constraints must mention absolute path "
        f"(e.g., 'absolute', '$(pwd)', etc.) so the model knows to resolve the relative "
        f"save path to an absolute path for the structured output token."
    )
