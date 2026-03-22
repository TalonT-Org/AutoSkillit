"""Tests that all SKILL.md output path instructions use HHMMSS-precision timestamps
and structured output tokens use 'key = value' format (spaces around =).
"""

from __future__ import annotations

import re

import pytest

from autoskillit.core import pkg_root
from autoskillit.workspace.skills import SkillResolver

# Skills whose output files are intentionally fixed-name (no timestamp needed).
# These are idempotent by design — the filename IS the identity.
FIXED_NAME_SKILLS: frozenset[str] = frozenset(
    {
        "write-recipe",  # .autoskillit/recipes/{name}.yaml — idempotent
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
OUTPUT_PATH_LINE = re.compile(
    r"(?:write|save|output)\s+.*?(?:to|path|file)\s*[:=]?\s*`?\.autoskillit/temp/",
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
    r"integration_branch|pr_count|simple_count|needs_check_count|"
    r"ci_blocked_count|review_blocked_count|queue_mode|"
    r"failure_type|is_fixable|escalation_required|escalation_reason|"
    r"merged)=[^\s]",
    re.MULTILINE,
)


def _get_file_producing_skills() -> list[str]:
    """Return skill names whose SKILL.md contains temp/ output path instructions."""
    resolver = SkillResolver()
    producing = []
    for info in resolver.list_all():
        if info.name not in FIXED_NAME_SKILLS:
            content = info.path.read_text()
            if OUTPUT_PATH_LINE.search(content):
                producing.append(info.name)
    return producing


def _get_skills_with_output_tokens() -> list[str]:
    """Return skill names whose SKILL.md contains structured output tokens."""
    resolver = SkillResolver()
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
        r"integration_branch|pr_count|simple_count|needs_check_count|"
        r"ci_blocked_count|review_blocked_count|queue_mode|"
        r"failure_type|is_fixable|escalation_required|escalation_reason|"
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
    resolver = SkillResolver()
    info = resolver.resolve(skill_name)
    assert info is not None
    content = info.path.read_text()

    # Extract lines that reference temp/ output paths.
    output_lines = [
        line
        for line in content.splitlines()
        if re.search(r"\.autoskillit/temp/.*\{.*\}", line) and not line.strip().startswith("#")
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
    resolver = SkillResolver()
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
    resolver = SkillResolver()
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
    resolver = SkillResolver()
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
    resolver = SkillResolver()
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
            "analysis_path",
            "remediation_path",
            "diagram_path",
            "triage_report",
            "triage_manifest",
            "pr_order_file",
            "analysis_file",
            "conflict_report_path",
            "config_path",
            "recipe_path",
        }
    )

    assert _OUTPUT_PATH_TOKENS == expected_path_tokens, (
        f"_OUTPUT_PATH_TOKENS mismatch.\n"
        f"Missing: {expected_path_tokens - _OUTPUT_PATH_TOKENS}\n"
        f"Extra: {_OUTPUT_PATH_TOKENS - expected_path_tokens}"
    )


def test_resolve_failures_skill_switches_code_index_to_worktree():
    """resolve-failures must set_project_path to worktree_path after env setup."""
    skill_md = (pkg_root() / "skills_extended" / "resolve-failures" / "SKILL.md").read_text()
    # Must contain a set_project_path call with worktree_path as the path argument.
    # Use a regex so minor whitespace or quoting variations don't cause false failures.
    worktree_switch = re.search(r"set_project_path\([^)]*worktree_path[^)]*\)", skill_md)
    assert worktree_switch is not None, (
        "resolve-failures SKILL.md must switch code-index to {worktree_path} after env setup"
    )
    # The worktree switch must come after the initial PROJECT_ROOT init.
    project_root_idx = skill_md.find("PROJECT_ROOT")
    assert project_root_idx != -1, "resolve-failures SKILL.md must reference PROJECT_ROOT"
    assert worktree_switch.start() > project_root_idx, (
        "worktree_path code-index switch must appear after initial PROJECT_ROOT init"
    )
