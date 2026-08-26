"""SKILL.md shell-safety semantic rules.

Family of @semantic_rule checks that scan SKILL.md bash blocks for shell-related
hazards: hardcoded git remotes, blind `git add`, interpreter-mediated writes,
unauthorized `autoskillit` imports, POSIX bracket expressions, and BRE grep
alternation.

See `autoskillit.recipe.rules.rules_skill_content` for the facade-mediated
patchability contract this module participates in.
"""

from __future__ import annotations

import regex as re

from autoskillit.core import Severity
from autoskillit.recipe._analysis import ValidationContext
from autoskillit.recipe._git_helpers import _GIT_REMOTE_COMMAND_RE, _LITERAL_ORIGIN_RE
from autoskillit.recipe._skill_placeholder_parser import extract_bash_blocks, extract_python_blocks
from autoskillit.recipe.contracts import resolve_skill_name
from autoskillit.recipe.registry import RuleFinding, make_finding, semantic_rule

_BLIND_GIT_ADD_RE = re.compile(
    r"(?:^|\s)git\s+(?:-C\s+\S+\s+)?add\s+(?:-A|--all|\.\s*(?:#|&&|;|$))",
)

INTERPRETER_WRITE_ALLOWLIST: frozenset[tuple[str, str]] = frozenset()

_AUTOSKILLIT_IMPORT_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"^\s*from\s+autoskillit[\s.]", re.MULTILINE),
    re.compile(r"^\s*import\s+autoskillit[\s,.]", re.MULTILINE),
    re.compile(r"['\"]autoskillit['\"]"),  # __import__ / importlib string form
]

_GREP_BRE_ALTERNATION_RE: re.Pattern[str] = re.compile(
    r"""
    (?<![=-])       # not preceded by = or - (excludes --grep=)
    grep            # grep command
    (?:\s+[-\w]+)*  # optional flags
    \s+             # whitespace before pattern
    (?:'[^']*\\\|[^']*'|"[^"]*\\\|[^"]*")  # quoted pattern containing \|
    """,
    re.VERBOSE,
)
_GIT_GREP_BRE_RE: re.Pattern[str] = re.compile(r"--grep=[\"'].*\\\|")

_POSIX_CHAR_CLASS_RE: re.Pattern[str] = re.compile(
    r"\[\[:"
    r"(?:alpha|digit|alnum|space|upper|lower|print|punct|blank|cntrl|graph|xdigit)"
    r":\]\]"
)


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
    from autoskillit.recipe.rules.rules_skill_content import (
        _resolve_skill_md,
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
                make_finding(
                    rule_name="hardcoded-origin-remote",
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


@semantic_rule(
    name="blind-git-add-in-skill",
    description=(
        "SKILL.md bash block uses 'git add -A', 'git add --all', or 'git add .' which "
        "stages all files including contamination from prior sessions. Use "
        "'git add -- <file>' or 'git add -u' instead."
    ),
    severity=Severity.ERROR,
)
def _check_blind_git_add_in_skill(ctx: ValidationContext) -> list[RuleFinding]:
    from autoskillit.recipe.rules.rules_skill_content import (
        _resolve_skill_md,
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
        bash_blocks = extract_bash_blocks(content)
        for block in bash_blocks:
            for line in block.splitlines():
                if _BLIND_GIT_ADD_RE.search(line):
                    findings.append(
                        make_finding(
                            rule_name="blind-git-add-in-skill",
                            step_name=step_name,
                            message=(
                                f"Skill '{skill_name}' SKILL.md contains blind "
                                f"'git add' in bash block: {line.strip()!r}"
                            ),
                        )
                    )
    return findings


@semantic_rule(
    name="interpreter-mediated-write-in-skill",
    description=(
        "SKILL.md bash block contains an interpreter-mediated file write "
        "(python3 -c / heredoc with .write_text(), open(..., 'w'), etc.). "
        "These are blocked by write_guard.py at runtime when paths are dynamic. "
        "Use the Write tool or bash redirects instead."
    ),
    severity=Severity.ERROR,
)
def _check_no_interpreter_mediated_writes(ctx: ValidationContext) -> list[RuleFinding]:
    from autoskillit.hooks import _INTERPRETER_LINE_RE, _WRITE_APIS_RE
    from autoskillit.recipe.rules.rules_skill_content import (
        _resolve_skill_md,
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
        if any(sname == skill_name for sname, _ in INTERPRETER_WRITE_ALLOWLIST):
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
        violations: list[str] = []
        for block in extract_bash_blocks(content):
            has_interpreter = False
            for line in block.splitlines():
                stripped = line.lstrip()
                cleaned = stripped.lstrip("$(")
                if _INTERPRETER_LINE_RE.search(cleaned):
                    has_interpreter = True
                    break
            if has_interpreter and _WRITE_APIS_RE.search(block):
                violations.append("bash block")
        for block in extract_python_blocks(content):
            if _WRITE_APIS_RE.search(block):
                violations.append("python block")
        if violations:
            findings.append(
                make_finding(
                    rule_name="interpreter-mediated-write-in-skill",
                    step_name=step_name,
                    message=(
                        f"Skill '{skill_name}' SKILL.md contains interpreter-mediated "
                        f"file write in {len(violations)} block(s): {violations}. "
                        f"Use the Write tool or bash redirects instead."
                    ),
                )
            )
    return findings


@semantic_rule(
    name="no-autoskillit-import-in-skill-python-block",
    severity=Severity.ERROR,
    description=(
        "SKILL.md bash block imports from `autoskillit` package. "
        "Bash blocks in SKILL.md execute inside headless sessions where "
        "the active Python interpreter is not guaranteed to have `autoskillit` "
        "installed. Only stdlib imports are permitted in SKILL.md python3 blocks."
    ),
)
def _check_no_autoskillit_import(ctx: ValidationContext) -> list[RuleFinding]:
    """Fire for any run_skill step whose SKILL.md bash blocks import the autoskillit package."""
    from autoskillit.recipe.rules.rules_skill_content import (
        _resolve_skill_md,
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
        bash_blocks = extract_bash_blocks(content)
        for block in bash_blocks:
            for pattern in _AUTOSKILLIT_IMPORT_PATTERNS:
                match = pattern.search(block)
                if match:
                    findings.append(
                        make_finding(
                            rule_name="no-autoskillit-import-in-skill-python-block",
                            step_name=step_name,
                            message=(
                                f"Skill '{skill_name}' bash block contains `autoskillit` import "
                                f"(matched: {match.group()!r}). "
                                "Use stdlib only in SKILL.md python3 blocks."
                            ),
                        )
                    )
                    break  # one finding per block, avoid duplicate pattern matches
    return findings


@semantic_rule(
    name="posix-char-class-in-skill",
    severity=Severity.ERROR,
    description=(
        "A SKILL.md bash block uses a POSIX bracket expression ([[:space:]], "
        "[[:alnum:]], etc.). These are valid in grep -E but silently mis-parsed "
        "by Python re — [[:space:]] becomes a character class containing "
        "[, :, s, p, a, c, e instead of matching whitespace. "
        "Fix: replace [[:space:]] with [ \\t] or a Python-compatible equivalent."
    ),
)
def _check_no_posix_char_class(ctx: ValidationContext) -> list[RuleFinding]:
    from autoskillit.recipe.rules.rules_skill_content import (
        _resolve_skill_md,
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
        bash_blocks = extract_bash_blocks(content)
        if not bash_blocks:
            continue
        violations: list[str] = []
        for block in bash_blocks:
            for line in block.splitlines():
                if _POSIX_CHAR_CLASS_RE.search(line):
                    violations.append(line.strip())
        if violations:
            findings.append(
                make_finding(
                    rule_name="posix-char-class-in-skill",
                    step_name=step_name,
                    message=(
                        f"Skill '{skill_name}' bash block uses POSIX bracket expression "
                        f"in {len(violations)} line(s). Python re silently mis-parses "
                        f"these — [[:space:]] becomes a character class containing "
                        f"[, :, s, p, a, c, e instead of matching whitespace. "
                        f"Fix: replace [[:space:]] with [ \\t] or a Python-compatible equivalent. "
                        f"Violations: {violations!r}"
                    ),
                )
            )
    return findings


@semantic_rule(
    name="grep-bre-alternation-in-skill",
    severity=Severity.ERROR,
    description=(
        "A SKILL.md bash block uses grep with BRE \\| alternation. "
        "The Grep tool wraps ripgrep (ERE) where | (bare) is alternation. "
        "Models copying \\| from skill bash blocks into Grep tool calls get 0 results silently. "
        "Fix: replace grep 'foo\\|bar' with rg 'foo|bar'. "
        "Exception: --grep= arguments in git log/show commands are legitimate BRE."
    ),
)
def _check_no_grep_bre_alternation(ctx: ValidationContext) -> list[RuleFinding]:
    from autoskillit.recipe.rules.rules_skill_content import (
        _resolve_skill_md,
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
            continue  # unknown-skill-command rule handles missing skills
        try:
            content = skill_md.read_text(encoding="utf-8")
        except OSError:
            continue
        bash_blocks = extract_bash_blocks(content)
        if not bash_blocks:
            continue
        violations: list[str] = []
        for block in bash_blocks:
            for line in block.splitlines():
                if _GIT_GREP_BRE_RE.search(line):
                    continue  # git --grep= BRE context: allowed
                if _GREP_BRE_ALTERNATION_RE.search(line):
                    violations.append(line.strip())
        if violations:
            findings.append(
                make_finding(
                    rule_name="grep-bre-alternation-in-skill",
                    step_name=step_name,
                    message=(
                        f"Skill '{skill_name}' bash block uses grep BRE \\| alternation "
                        f"in {len(violations)} line(s). The Grep tool wraps ripgrep (ERE) "
                        f"where | (bare) is alternation — \\| silently returns 0 results. "
                        f"Fix: replace grep 'foo\\|bar' with rg 'foo|bar'. "
                        f"Violations: {violations!r}"
                    ),
                )
            )
    return findings
