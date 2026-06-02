"""Shared parser helpers for SKILL.md bash-block and python-block analysis.

Used by:
  - src/autoskillit/recipe/rules/rules_skill_content.py (semantic rules)
  - tests/skills/test_skill_placeholder_contracts.py (structural linter)
  - tests/skills/test_graphql_invocation_completeness.py (graphql invocation linter)
  - tests/contracts/test_no_interpreter_writes_in_skills.py (write safety linter)
"""

from __future__ import annotations

import regex as re


def extract_fenced_blocks(content: str, language: str) -> list[str]:
    """Extract fenced code blocks for the given language identifier."""
    return re.findall(rf"```{re.escape(language)}\s*\n(.*?)```", content, re.DOTALL)


def extract_bash_blocks(content: str) -> list[str]:
    return extract_fenced_blocks(content, "bash")


def extract_python_blocks(content: str) -> list[str]:
    return extract_fenced_blocks(content, "python")


def extract_graphql_blocks(content: str) -> list[str]:
    return extract_fenced_blocks(content, "graphql")


def extract_bash_placeholders(bash_blocks: list[str]) -> set[str]:
    """Find {identifier} tokens that are NOT shell variable references.

    Excludes ${VAR} (preceded by $) and @{upstream} git notation (preceded by @).
    Only bare {identifier} without a leading $ or @ are template placeholders.
    """
    placeholders: set[str] = set()
    for block in bash_blocks:
        for m in re.finditer(r"(?<![$@])\{([A-Za-z_][A-Za-z0-9_-]*)\}", block):
            name = m.group(1)
            if not name.isupper():
                placeholders.add(name)
    return placeholders


def extract_declared_ingredients(content: str) -> set[str]:
    """Extract ingredient names from ## Arguments / ## Ingredients / ## Parameters
    sections and YAML frontmatter."""
    declared: set[str] = set()
    section_re = re.compile(
        r"^##\s+(?:Arguments|Ingredients|Parameters|Invocation)[^\n]*\n(.*?)(?=^##|\Z)",
        re.MULTILINE | re.DOTALL,
    )
    for sec in section_re.finditer(content):
        body = sec.group(1)
        for m in re.finditer(r"\{([A-Za-z_][A-Za-z0-9_-]*)\}", body):
            declared.add(m.group(1))
        for m in re.finditer(r"`([A-Za-z_][A-Za-z0-9_-]*)`", body):
            declared.add(m.group(1))
    fm = re.match(r"^---\n(.*?)\n---", content, re.DOTALL)
    if fm:
        for m in re.finditer(r"^\s+([A-Za-z_][A-Za-z0-9_-]*):", fm.group(1), re.MULTILINE):
            declared.add(m.group(1))
    return declared


def shell_vars_assigned(bash_blocks: list[str]) -> set[str]:
    """Extract shell variable names assigned in bash blocks (VAR= or VAR=$(...))."""
    assigned: set[str] = set()
    for block in bash_blocks:
        for m in re.finditer(r"^([A-Za-z_][A-Za-z0-9_]*)\s*=", block, re.MULTILINE):
            assigned.add(m.group(1))
            assigned.add(m.group(1).lower())
    return assigned


_STEP_RE = re.compile(r"###\s+(Step \d+(?:\.\d+)?)\b", re.MULTILINE)


def extract_step_sections(content: str) -> dict[str, str]:
    """Parse a SKILL.md into per-step sections keyed by bare step name.

    Returns {'Step 0': '...', 'Step 1': '...', 'Step 2.5': '...', ...}.
    Keys are the bare 'Step N' capture (suffix stripped), so callers can use
    sections['Step 2'] regardless of the full heading text.
    """
    matches = list(_STEP_RE.finditer(content))
    sections: dict[str, str] = {}
    for i, m in enumerate(matches):
        key = m.group(1)
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(content)
        sections[key] = content[start:end]
    return sections


def extract_git_commands(text: str) -> list[str]:
    """Extract git command strings from markdown text.

    Captures from both bash fenced blocks (line-by-line) and inline backtick
    spans.  Inline backtick spans are the primary source for the offending
    HEAD tokens in audit-impl SKILL.md, which appear as ``git diff ...`` spans
    rather than inside fenced blocks.
    """
    commands: list[str] = []
    for block in extract_bash_blocks(text):
        for line in block.splitlines():
            stripped = line.strip()
            if stripped.startswith("git "):
                commands.append(stripped)
    for m in re.finditer(r"`(git [^`\n]+)`", text):
        commands.append(m.group(1))
    return commands


_VRULE_RE = re.compile(
    r"^(V\d+):\s*.+?(?=^V\d+:|\n---|\Z)",
    re.DOTALL | re.MULTILINE,
)


def extract_never_block(content: str) -> str:
    """Return the text of the first NEVER block for semantic rule validation.

    Searches for the NEVER block delimiter (``**NEVER`` or ``## CRITICAL CONSTRAINTS``)
    and extracts to the next ``**ALWAYS`` or ``## `` section header. Returns the
    raw block text (not just list items). Used by semantic rule validation — distinct
    from ``tests._helpers.extract_never_block`` which extracts ``- `` list items only.
    """
    upper = content.upper()
    never_pos = upper.find("\n**NEVER")
    if never_pos == -1:
        never_pos = upper.find("\n## CRITICAL CONSTRAINTS")
    if never_pos == -1:
        return ""
    always_pos = upper.find("\n**ALWAYS", never_pos + 1)
    section_pos = upper.find("\n## ", never_pos + 1)
    end_pos = len(content)
    if always_pos != -1:
        end_pos = min(end_pos, always_pos)
    if section_pos != -1:
        end_pos = min(end_pos, section_pos)
    return content[never_pos:end_pos]


_WRITE_SCOPE_RE = re.compile(
    r"\{\{AUTOSKILLIT_TEMP\}\}/([a-z][a-z0-9_-]+(?:/[a-z0-9_${}. -]+)*)/?",
)


def extract_write_path_declarations(content: str) -> list[str]:
    """Extract {{AUTOSKILLIT_TEMP}}/.../ path patterns from SKILL.md NEVER block.

    For semantic rule validation — extracts the declared write scope directory
    from the NEVER block constraint. Scans only the NEVER block (not the full
    content) to avoid noise from write instruction lines in the Workflow section.
    Returns a list of path portions after {{AUTOSKILLIT_TEMP}}/ (e.g., ['review-pr/']).
    """
    never_block = extract_never_block(content)
    if not never_block:
        return []
    paths = _WRITE_SCOPE_RE.findall(never_block)
    return [m + "/" if not m.endswith("/") else m for m in paths]


_DYNAMIC_WRITE_VAR_RE = re.compile(
    r"\$\{?(REVIEW_OUTPUT_DIR|AUTOSKILLIT_ALLOWED_WRITE_PREFIX)\}?",
)


def has_dynamic_write_path(content: str) -> bool:
    """Return True if the SKILL.md uses a dynamic variable for write paths.

    When a SKILL.md reads AUTOSKILLIT_ALLOWED_WRITE_PREFIX or a derived
    variable at runtime, its write paths adapt to whatever prefix the
    framework provides — static path alignment checking is not applicable.
    Scans from the NEVER block onward (NEVER + ALWAYS + Workflow) to avoid
    false positives from documentation preamble while catching actual usage.
    """
    upper = content.upper()
    never_pos = upper.find("\n**NEVER")
    if never_pos == -1:
        never_pos = upper.find("\n## CRITICAL CONSTRAINTS")
    if never_pos == -1:
        return False
    return bool(_DYNAMIC_WRITE_VAR_RE.search(content[never_pos:]))


def extract_validation_rule_block(content: str, rule_label: str) -> str | None:
    """Extract a named validation rule block (e.g., 'V9') from SKILL.md content.

    Returns the full block text from 'V9: ...' to the next V-rule label,
    a '---' separator, or end of string. Returns None if the label is not found.
    """
    for m in _VRULE_RE.finditer(content):
        if m.group(1) == rule_label:
            return m.group(0).strip()
    return None
