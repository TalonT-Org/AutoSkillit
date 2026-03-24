"""Semantic rule: undefined-bash-placeholder

Validates that every {placeholder} in a SKILL.md bash block is either:
  - Declared as an ingredient in the skill's ## Arguments / ## Ingredients section
  - Assigned as a shell variable in one of the skill's bash blocks
"""

from __future__ import annotations

import re
from pathlib import Path

from autoskillit.core import Severity
from autoskillit.recipe._analysis import ValidationContext
from autoskillit.recipe._skill_placeholder_parser import (
    extract_bash_blocks,
    extract_bash_placeholders,
    extract_declared_ingredients,
    shell_vars_assigned,
)
from autoskillit.recipe.contracts import load_bundled_manifest, resolve_skill_name
from autoskillit.recipe.registry import RuleFinding, semantic_rule

# Search directories for SKILL.md resolution (patchable in tests via patch.object).
# When None (default), uses SkillResolver to find bundled skills.
SKILL_SEARCH_DIRS: list[Path] | None = None

_PSEUDOCODE_ALLOWLIST: frozenset[tuple[str, str]] = frozenset(
    {
        ("implement-worktree", "test_command"),
        ("implement-worktree-no-merge", "test_command"),
        ("resolve-failures", "test_command"),
    }
)


def _resolve_skill_md(skill_name: str) -> Path | None:
    """Resolve a skill name to its SKILL.md path.

    When SKILL_SEARCH_DIRS is set (e.g., in tests), searches those directories.
    Otherwise uses SkillResolver to find the bundled skill.
    """
    if SKILL_SEARCH_DIRS is not None:
        for search_dir in SKILL_SEARCH_DIRS:
            skill_md = search_dir / skill_name / "SKILL.md"
            if skill_md.is_file():
                return skill_md
        return None
    from autoskillit.workspace import SkillResolver  # noqa: PLC0415

    skill_info = SkillResolver().resolve(skill_name)
    if skill_info is None:
        return None
    return skill_info.path  # skill_info.path IS the SKILL.md file


@semantic_rule(
    name="undefined-bash-placeholder",
    description=(
        "A SKILL.md bash block uses a {placeholder} that is not declared as an ingredient "
        "or assigned as a shell variable. The model will guess the value from ambient context."
    ),
)
def _check_undefined_bash_placeholder(ctx: ValidationContext) -> list[RuleFinding]:
    """Fire for any run_skill step whose SKILL.md has undefined bash-block placeholders."""
    findings: list[RuleFinding] = []
    for step_name, step in ctx.recipe.steps.items():
        if step.tool != "run_skill":
            continue
        skill_cmd = step.with_args.get("skill_command", "")
        if not skill_cmd:
            continue

        skill_name = resolve_skill_name(skill_cmd)
        if skill_name is None:
            continue

        skill_md = _resolve_skill_md(skill_name)
        if skill_md is None:
            continue  # unknown-skill-command rule handles missing skills

        try:
            content = skill_md.read_text(encoding="utf-8")
        except OSError:
            continue  # file deleted or unreadable between resolution and read
        bash_blocks = extract_bash_blocks(content)
        if not bash_blocks:
            continue

        used = extract_bash_placeholders(bash_blocks)
        if not used:
            continue

        declared = extract_declared_ingredients(content)
        assigned = shell_vars_assigned(bash_blocks)
        defined = declared | assigned
        allowlisted = {name for (sname, name) in _PSEUDOCODE_ALLOWLIST if sname == skill_name}
        undefined = used - defined - allowlisted

        if undefined:
            findings.append(
                RuleFinding(
                    rule="undefined-bash-placeholder",
                    severity=Severity.WARNING,
                    step_name=step_name,
                    message=(
                        f"Skill '{skill_name}' bash block uses undefined {{placeholder}}: "
                        f"{sorted(undefined)}. Declare as ingredient in ## Arguments, or capture "
                        f"at runtime as VARNAME=$(command)."
                    ),
                )
            )
    return findings


_GIT_REMOTE_COMMAND_RE: re.Pattern[str] = re.compile(
    r"\bgit\b.*?\b(?:fetch|rebase|log|show|rev-parse)\b"
)
# Matches literal 'origin' not immediately preceded by $, {, or - (i.e., not a shell
# variable reference or shell default-value expression like ${REMOTE:-origin}).
_LITERAL_ORIGIN_RE: re.Pattern[str] = re.compile(r"(?<!\$)(?<!\{)(?<!-)\borigin\b")


def _has_hardcoded_origin_in_bash(bash_blocks: list[str]) -> bool:
    """Return True if any non-comment bash line uses literal 'origin' in a git remote command."""
    for block in bash_blocks:
        for line in block.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if not _GIT_REMOTE_COMMAND_RE.search(stripped):
                continue
            if _LITERAL_ORIGIN_RE.search(stripped):
                return True
    return False


@semantic_rule(
    name="hardcoded-origin-remote",
    description=(
        "A SKILL.md bash block uses the literal remote name 'origin' in a git command "
        "that contacts a remote (fetch, rebase, log, show, rev-parse). In clone-isolated "
        "pipelines, clone_repo() sets origin=file://, making this a stale local path. "
        "Use: REMOTE=$(git remote get-url upstream >/dev/null 2>&1 "
        "&& echo upstream || echo origin) and reference $REMOTE throughout."
    ),
)
def _check_hardcoded_origin_remote(ctx: ValidationContext) -> list[RuleFinding]:
    """Fire for any run_skill step whose SKILL.md bash blocks hardcode the 'origin' remote."""
    findings: list[RuleFinding] = []
    for step_name, step in ctx.recipe.steps.items():
        if step.tool != "run_skill":
            continue
        skill_cmd = step.with_args.get("skill_command", "")
        if not skill_cmd:
            continue
        skill_name = resolve_skill_name(skill_cmd)
        if skill_name is None:
            continue
        skill_md = _resolve_skill_md(skill_name)
        if skill_md is None:
            continue  # unknown-skill-command rule handles missing skills
        try:
            content = skill_md.read_text(encoding="utf-8")
        except OSError:
            continue
        bash_blocks = extract_bash_blocks(content)
        if not bash_blocks:
            continue
        if _has_hardcoded_origin_in_bash(bash_blocks):
            findings.append(
                RuleFinding(
                    rule="hardcoded-origin-remote",
                    severity=Severity.WARNING,
                    step_name=step_name,
                    message=(
                        f"Skill '{skill_name}' bash block uses the literal remote name 'origin' "
                        f"in a git fetch/rebase/log/show/rev-parse command. In clone-isolated "
                        f"pipelines (clone_repo sets origin=file://), this fetches from a stale "
                        f"local path. Use: REMOTE=$(git remote get-url upstream 2>/dev/null "
                        f"&& echo upstream || echo origin) and reference $REMOTE throughout."
                    ),
                )
            )
    return findings


_NO_MARKDOWN_DIRECTIVE_PATTERN: re.Pattern[str] = re.compile(
    r"no\s+markdown\s+format|plain\s+text.*token|literal\s+plain\s+text",
    re.IGNORECASE,
)


@semantic_rule(
    name="output-section-no-markdown-directive",
    description=(
        "A SKILL.md output section is missing the no-markdown directive. "
        "Skills with expected_output_patterns depend on plain-text token emission; "
        "the model may emit **token_name** = value if not explicitly instructed otherwise."
    ),
)
def _check_output_section_no_markdown_directive(ctx: ValidationContext) -> list[RuleFinding]:
    """Verify that SKILL.md output sections contain an explicit no-markdown directive.

    Skills with expected_output_patterns depend on the model emitting plain-text
    token names. If the SKILL.md does not explicitly prohibit markdown formatting,
    the model may emit **token_name** = value, causing adjudicated_failure.
    """
    manifest = load_bundled_manifest()
    findings: list[RuleFinding] = []

    for step_name, step in ctx.recipe.steps.items():
        if step.tool != "run_skill":
            continue
        skill_cmd = step.with_args.get("skill_command", "")
        if not skill_cmd:
            continue

        skill_name = resolve_skill_name(skill_cmd)
        if skill_name is None:
            continue

        skill_data = manifest.get("skills", {}).get(skill_name)
        if not skill_data or not skill_data.get("expected_output_patterns"):
            continue  # Only check skills that have contracts with patterns

        skill_md_path = _resolve_skill_md(skill_name)
        if skill_md_path is None:
            continue  # unknown-skill-command rule handles missing skills

        try:
            skill_md = skill_md_path.read_text(encoding="utf-8")
        except OSError:
            continue  # file deleted or unreadable between resolution and read

        output_section_match = re.search(
            r"##\s+Output\b(.+?)(?:^##|\Z)", skill_md, re.DOTALL | re.MULTILINE
        )
        if not output_section_match:
            continue  # No output section — other rules handle this

        output_section = output_section_match.group(1)

        if not _NO_MARKDOWN_DIRECTIVE_PATTERN.search(output_section):
            findings.append(
                RuleFinding(
                    rule="output-section-no-markdown-directive",
                    severity=Severity.WARNING,
                    step_name=step_name,
                    message=(
                        f"SKILL.md for '{skill_name}' has expected_output_patterns but its "
                        f"## Output section does not contain an explicit no-markdown directive. "
                        f"Add: 'Emit the structured output tokens as literal plain text with no "
                        f"markdown formatting on the token names.'"
                    ),
                )
            )
    return findings
