"""Shared parser helpers for SKILL.md bash-block and python-block analysis.

Used by:
  - src/autoskillit/recipe/rules/rules_skill_content.py (semantic rules)
  - tests/skills/test_skill_placeholder_contracts.py (structural linter)
  - tests/contracts/test_no_interpreter_writes_in_skills.py (write safety linter)
"""

from __future__ import annotations

import regex as re


def extract_bash_blocks(content: str) -> list[str]:
    return re.findall(r"```bash\s*\n(.*?)```", content, re.DOTALL)


def extract_python_blocks(content: str) -> list[str]:
    return re.findall(r"```python\s*\n(.*?)```", content, re.DOTALL)


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


def extract_validation_rule_block(content: str, rule_label: str) -> str | None:
    """Extract a named validation rule block (e.g., 'V9') from SKILL.md content.

    Returns the full block text from 'V9: ...' to the next V-rule label,
    a '---' separator, or end of string. Returns None if the label is not found.
    """
    for m in _VRULE_RE.finditer(content):
        if m.group(1) == rule_label:
            return m.group(0).strip()
    return None
