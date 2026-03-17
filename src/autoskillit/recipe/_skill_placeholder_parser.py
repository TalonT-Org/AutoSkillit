"""Shared parser helpers for SKILL.md bash-block placeholder analysis.

Used by:
  - src/autoskillit/recipe/rules_skill_content.py (semantic rule)
  - tests/skills/test_skill_placeholder_contracts.py (structural linter)
"""
from __future__ import annotations

import re


def extract_bash_blocks(content: str) -> list[str]:
    return re.findall(r"```bash\s*\n(.*?)```", content, re.DOTALL)


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
