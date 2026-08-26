"""SKILL.md GitHub-API-safety semantic rules.

Family of @semantic_rule checks that scan SKILL.md sections for GitHub API
misuse patterns: `gh issue comment` invocations (forbidden), missing
`--input -` on Reviews POSTs, and GraphQL mutations not paired with a
guard-compatible shell invocation.

`_resolve_skill_md` is NOT imported at module scope. Every rule body that
calls it performs a function-body-scoped lazy import against
`autoskillit.recipe.rules.rules_skill_content` (the facade) so that
`patch.object(rules_skill_content, "_resolve_skill_md", ...)` continues to
redirect rule-body lookups via the facade namespace.
"""

from __future__ import annotations

import regex as re

from autoskillit.core import Severity
from autoskillit.recipe._analysis import ValidationContext
from autoskillit.recipe._skill_placeholder_parser import (
    extract_bash_blocks,
    extract_fenced_blocks,
    extract_graphql_blocks,
    extract_sections,
    has_prose_graphql_execution,
)
from autoskillit.recipe.contracts import resolve_skill_name
from autoskillit.recipe.registry import RuleFinding, make_finding, semantic_rule

_GH_ISSUE_COMMENT_RE = re.compile(r"\bgh\s+issue\s+comment\b")

_REVIEWS_POST_RE: re.Pattern[str] = re.compile(
    r"pulls/\{[^}]*\}/reviews[^\n]*--method\s+POST"
    r"|--method\s+POST[^\n]*pulls/\{[^}]*\}/reviews"
    r"|POST\s+/repos/\{[^}]*\}/\{[^}]*\}/pulls/\{[^}]*\}/reviews",
    re.IGNORECASE,
)


_LINE_CONTINUATION_RE: re.Pattern[str] = re.compile(r"\\\n\s*")


def _extract_subsections(content: str) -> list[str]:
    """Split SKILL.md content into subsections at the ### level."""
    parts = re.split(r"(?m)^(?=###\s)", content)
    return [p for p in parts if p.strip()]


_GRAPHQL_VARIABLE_RE = re.compile(r"\$([a-zA-Z_]\w*)")
_GH_API_GRAPHQL_BLOCK_RE = re.compile(r"gh\s+api\s+graphql\b")
_GRAPHQL_MUTATION_SECTION_RE = re.compile(
    r"(?i:\bmutation\b)|"
    r"\b(?:add|close|convert|create|delete|mark|merge|remove|reopen|resolve|submit|"
    r"unresolve|update)[A-Z]\w*\b"
)
_GRAPHQL_INPUT_PATH_RE = re.compile(r"--input(?:\s+|=)(?:\"([^\"]+)\"|'([^']+)'|([^\s]+))")
_GRAPHQL_INLINE_LITERAL_MUTATION_RE = re.compile(
    r"(?:-[fF]|--field|--raw-field)\s+query='([^']*\bmutation\b[^']*)'",
    re.IGNORECASE | re.DOTALL,
)
_GRAPHQL_DYNAMIC_TOKEN_RE = re.compile(r"\$|`|\*|\?|\[")
_GRAPHQL_VARIABLES_BLOB_RE = re.compile(
    r"(?:-[fF]|--field|--raw-field)\s+variables=",
    re.IGNORECASE,
)


def _literal_graphql_input_path(block: str) -> str | None:
    match = _GRAPHQL_INPUT_PATH_RE.search(block)
    if match is None:
        return None
    path = next(value for value in match.groups() if value is not None)
    if path == "-" or _GRAPHQL_DYNAMIC_TOKEN_RE.search(path):
        return None
    if not (path.startswith("/") or path.startswith("{{AUTOSKILLIT_TEMP}}/")):
        return None
    prefix = block[: match.start()]
    if re.search(
        rf"(?:^|[\s'\"=<>|&;()]){re.escape(path)}(?=$|[\s'\"<>|&;()])",
        prefix,
    ):
        return None
    return path


def _has_guard_compatible_graphql_mutation(section: str, block: str) -> bool:
    input_path = _literal_graphql_input_path(block)
    if input_path is not None:
        return bool(
            re.search(
                r"(?:separate|prior|earlier)\s+(?:completed\s+)?tool\s+call",
                section,
                re.IGNORECASE,
            )
        )
    match = _GRAPHQL_INLINE_LITERAL_MUTATION_RE.search(block)
    return bool(match and not _GRAPHQL_DYNAMIC_TOKEN_RE.search(match.group(1)))


def _json_payload_binds_variable(section: str, variable: str) -> bool:
    return any(
        re.search(r'"query"\s*:', block)
        and re.search(r'"variables"\s*:', block)
        and re.search(rf'"{re.escape(variable)}"\s*:', block)
        for block in extract_fenced_blocks(section, "json")
    )


@semantic_rule(
    name="skill-no-issue-comments",
    description=(
        "Skill content must not use 'gh issue comment'. "
        "All issue updates belong in the body via 'gh issue edit --body-file'."
    ),
)
def _check_no_gh_issue_comment(ctx: ValidationContext) -> list[RuleFinding]:
    from autoskillit.recipe.rules.rules_skill_content import (
        _resolve_skill_md,  # noqa: PLC0415  # lazy import → facade-mediated patchability
    )

    findings: list[RuleFinding] = []
    for step_name, step in ctx.recipe.steps.items():
        if step.tool != "run_skill":
            continue
        skill_cmd = step.with_args.get("skill_command", "")
        skill_name = resolve_skill_name(skill_cmd)
        if skill_name is None:
            continue
        skill_md = _resolve_skill_md(
            skill_name, project_root=ctx.project_dir, resolver=ctx.skill_resolver
        )
        if skill_md is None:
            continue
        try:
            content = skill_md.read_text(encoding="utf-8")
        except OSError:
            continue
        for block in extract_bash_blocks(content):
            if _GH_ISSUE_COMMENT_RE.search(block):
                findings.append(
                    make_finding(
                        rule_name="skill-no-issue-comments",
                        step_name=step_name,
                        message=(
                            f"Skill '{skill_name}' contains 'gh issue comment'. "
                            "Use 'gh issue edit --body-file' instead."
                        ),
                        severity=Severity.ERROR,
                    )
                )
                break
    return findings


@semantic_rule(
    name="reviews-post-requires-input-flag",
    severity=Severity.ERROR,
    description=(
        "A SKILL.md section mentions POST to the GitHub Reviews API with a comments[] "
        "array but does not contain '--input -'. The --field approach serializes JSON "
        "arrays as string literals, causing HTTP 422."
    ),
)
def _check_reviews_post_requires_input_flag(ctx: ValidationContext) -> list[RuleFinding]:
    """Fire when a SKILL.md ### subsection has a reviews POST endpoint but no --input -."""
    from autoskillit.recipe.rules.rules_skill_content import (
        _resolve_skill_md,  # noqa: PLC0415  # lazy import → facade-mediated patchability
    )

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
        skill_md = _resolve_skill_md(
            skill_name, project_root=ctx.project_dir, resolver=ctx.skill_resolver
        )
        if skill_md is None:
            continue
        try:
            content = skill_md.read_text(encoding="utf-8")
        except OSError:
            continue
        for subsection in _extract_subsections(content):
            collapsed = _LINE_CONTINUATION_RE.sub(" ", subsection)
            if _REVIEWS_POST_RE.search(collapsed) and "--input -" not in collapsed:
                findings.append(
                    make_finding(
                        rule_name="reviews-post-requires-input-flag",
                        step_name=step_name,
                        message=(
                            f"Skill '{skill_name}' has a section that POSTs to the GitHub "
                            f"Reviews endpoint but does not use '--input -'. The --field "
                            f"approach serializes JSON arrays as string literals, causing "
                            f"HTTP 422. Use: jq -n ... | gh api .../reviews "
                            f"--method POST --input -"
                        ),
                    )
                )
    return findings


@semantic_rule(
    name="graphql-query-requires-shell-invocation",
    description=(
        "A SKILL.md contains a ```graphql block with parameterized $variables "
        "but no guard-compatible `gh api graphql` invocation. Mutations must use either "
        "a fully literal inline document or a literal inspected JSON payload written in "
        "a prior tool call; variables use individual fields or that payload's variables object."
    ),
    severity=Severity.ERROR,
)
def _check_graphql_query_requires_shell_invocation(ctx: ValidationContext) -> list[RuleFinding]:
    from autoskillit.recipe.rules.rules_skill_content import (
        _resolve_skill_md,  # noqa: PLC0415  # lazy import → facade-mediated patchability
    )

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

        skill_md = _resolve_skill_md(
            skill_name, project_root=ctx.project_dir, resolver=ctx.skill_resolver
        )
        if skill_md is None:
            continue

        try:
            content = skill_md.read_text(encoding="utf-8")
        except OSError:
            continue

        for section in extract_sections(content):
            section_graphql = extract_graphql_blocks(section)
            section_bash = extract_bash_blocks(section)
            section_bash_graphql = [b for b in section_bash if _GH_API_GRAPHQL_BLOCK_RE.search(b)]
            section_is_mutation = (
                any(_GRAPHQL_MUTATION_SECTION_RE.search(block) for block in section_graphql)
                if section_graphql
                else bool(_GRAPHQL_MUTATION_SECTION_RE.search(section))
            )

            for bash_block in section_bash_graphql:
                if _GRAPHQL_VARIABLES_BLOB_RE.search(bash_block):
                    findings.append(
                        make_finding(
                            rule_name="graphql-query-requires-shell-invocation",
                            step_name=step_name,
                            message=(
                                f"Skill '{skill_name}' uses a single variables blob binding; "
                                "use individual fields or a validated JSON payload "
                                "variables object."
                            ),
                        )
                    )
                block_is_mutation = bool(_GRAPHQL_MUTATION_SECTION_RE.search(bash_block))
                if (
                    block_is_mutation or (section_is_mutation and len(section_bash_graphql) == 1)
                ) and not _has_guard_compatible_graphql_mutation(section, bash_block):
                    findings.append(
                        make_finding(
                            rule_name="graphql-query-requires-shell-invocation",
                            step_name=step_name,
                            message=(
                                f"Skill '{skill_name}' prescribes a GraphQL mutation that is "
                                "not guard-compatible: use a fully literal inline mutation or "
                                "a literal JSON --input path written in a prior tool call."
                            ),
                        )
                    )

            for block in section_graphql:
                if not section_bash_graphql:
                    findings.append(
                        make_finding(
                            rule_name="graphql-query-requires-shell-invocation",
                            step_name=step_name,
                            message=(
                                f"Skill '{skill_name}' has a graphql block in a section "
                                f"with no 'gh api graphql' bash block in the same section."
                            ),
                        )
                    )
                    break

                variable_names = set(_GRAPHQL_VARIABLE_RE.findall(block))
                for var in variable_names:
                    flag_found = any(
                        re.search(rf"-[Ff]\s*{re.escape(var)}=", b) for b in section_bash_graphql
                    )
                    payload_found = any(
                        _literal_graphql_input_path(b) is not None for b in section_bash_graphql
                    ) and _json_payload_binds_variable(section, var)
                    if not flag_found and not payload_found:
                        findings.append(
                            make_finding(
                                rule_name="graphql-query-requires-shell-invocation",
                                step_name=step_name,
                                message=(
                                    f"Skill '{skill_name}' graphql variable '${var}' has no "
                                    f"'-F {var}=' or '-f {var}=' binding in any "
                                    f"same-section `gh api graphql` bash block."
                                ),
                            )
                        )

            if (
                not section_graphql
                and has_prose_graphql_execution(section)
                and not section_bash_graphql
            ):
                findings.append(
                    make_finding(
                        rule_name="graphql-query-requires-shell-invocation",
                        step_name=step_name,
                        message=(
                            f"Skill '{skill_name}' has a section referencing GraphQL "
                            f"execution in prose but no 'gh api graphql' bash block "
                            f"in the same section."
                        ),
                    )
                )

    return findings
