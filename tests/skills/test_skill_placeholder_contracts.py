"""
Validate that no SKILL.md bash code block uses an undefined {placeholder} token.

A {placeholder} in a bash block must be either:
  - Declared as an ingredient in ## Arguments / ## Ingredients (passed from outside)
  - Assigned as a shell variable in any bash block in the same skill (captured at runtime)

This test provides structural immunity against the class of bug where a {placeholder}
appears in an executable bash block without a defined source, causing the model to guess
the value from ambient context and produce incorrect shell commands.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).parent.parent.parent
_SKILL_DIRS = [
    _REPO_ROOT / "src" / "autoskillit" / "skills",
    _REPO_ROOT / "src" / "autoskillit" / "skills_extended",
]

# Allowlist for {placeholder} tokens that are explicitly documented as pseudocode
# substitution patterns — i.e., the skill prose immediately before the bash block
# contains "use this command wherever {X} appears" or equivalent wording.
# Extend this only when such explicit prose documentation exists in the skill.
_PSEUDOCODE_ALLOWLIST: frozenset[tuple[str, str]] = frozenset(
    {
        # plan_name: pseudocode for "extract the plan file's stem from {plan_path}".
        # The skill prose (Step 0 path detection + Step 1 worktree naming) makes the
        # inference unambiguous; the model reliably derives it from the declared {plan_path}.
        ("implement-worktree", "plan_name"),
        ("implement-worktree", "test_command"),
        ("implement-worktree-no-merge", "plan_name"),
        ("implement-worktree-no-merge", "test_command"),
        ("resolve-failures", "test_command"),
    }
)


def _all_skill_mds() -> list[tuple[str, Path]]:
    result = []
    for skill_dir in _SKILL_DIRS:
        if not skill_dir.exists():
            continue
        for p in sorted(skill_dir.iterdir()):
            if p.is_dir():
                md = p / "SKILL.md"
                if md.exists():
                    result.append((p.name, md))
    return result


def _extract_bash_blocks(content: str) -> list[str]:
    return re.findall(r"```bash\s*\n(.*?)```", content, re.DOTALL)


def _extract_bash_placeholders(bash_blocks: list[str]) -> set[str]:
    """
    Find {identifier} tokens in bash blocks that are NOT shell variable references.

    Shell variable references (${VAR}) are valid bash syntax and are excluded.
    Only bare {identifier} without a leading $ are template placeholders that
    must have a declared ingredient source.
    """
    placeholders: set[str] = set()
    for block in bash_blocks:
        for m in re.finditer(r"(?<!\$)\{([A-Za-z_][A-Za-z0-9_-]*)\}", block):
            name = m.group(1)
            # Exclude ALL_UPPERCASE identifiers — git @{upstream}, shell env vars
            # written in annotation comments use uppercase by convention.
            if not name.isupper():
                placeholders.add(name)
    return placeholders


def _extract_declared_ingredients(content: str) -> set[str]:
    """
    Extract ingredient names from ## Arguments / ## Ingredients / ## Parameters sections
    and YAML frontmatter.
    """
    declared: set[str] = set()
    # Standard section heading variants
    section_re = re.compile(
        r"^##\s+(?:Arguments|Ingredients|Parameters|Invocation)\s*\n(.*?)(?=^##|\Z)",
        re.MULTILINE | re.DOTALL,
    )
    for sec in section_re.finditer(content):
        body = sec.group(1)
        for m in re.finditer(r"\{([A-Za-z_][A-Za-z0-9_-]*)\}", body):
            declared.add(m.group(1))
        for m in re.finditer(r"`([A-Za-z_][A-Za-z0-9_-]*)`", body):
            declared.add(m.group(1))
    # YAML frontmatter ingredients: key
    fm = re.match(r"^---\n(.*?)\n---", content, re.DOTALL)
    if fm:
        for m in re.finditer(r"^\s+([A-Za-z_][A-Za-z0-9_-]*):", fm.group(1), re.MULTILINE):
            declared.add(m.group(1))
    return declared


def _shell_vars_assigned(bash_blocks: list[str]) -> set[str]:
    """
    Extract shell variable names assigned anywhere in the skill's bash blocks.
    A variable assigned as VAR= or VAR=$() is a runtime-captured value — any
    {placeholder} matching its name (case-insensitive) is implicitly defined.
    """
    assigned: set[str] = set()
    for block in bash_blocks:
        for m in re.finditer(r"^([A-Za-z_][A-Za-z0-9_]*)\s*=", block, re.MULTILINE):
            assigned.add(m.group(1))
            assigned.add(m.group(1).lower())
    return assigned


@pytest.mark.parametrize("skill_name,skill_md", _all_skill_mds())
def test_no_undefined_bash_placeholders(skill_name: str, skill_md: Path) -> None:
    """
    Every {placeholder} in a SKILL.md bash block must be either declared as an
    ingredient or assigned as a shell variable in the same skill.

    This provides structural immunity against the bug class where an undefined
    placeholder causes the model to guess the value from ambient context.
    """
    content = skill_md.read_text(encoding="utf-8")
    bash_blocks = _extract_bash_blocks(content)
    if not bash_blocks:
        return

    used = _extract_bash_placeholders(bash_blocks)
    if not used:
        return

    declared = _extract_declared_ingredients(content)
    assigned = _shell_vars_assigned(bash_blocks)
    defined = declared | assigned

    allowlisted = {name for (sname, name) in _PSEUDOCODE_ALLOWLIST if sname == skill_name}

    undefined = used - defined - allowlisted
    assert not undefined, (
        f"{skill_md.relative_to(_REPO_ROOT)}: bash block uses undefined "
        f"{{placeholder}} syntax: {sorted(undefined)}.\n"
        f"  Declared ingredients: {sorted(declared)}\n"
        f"  Assigned shell vars:  {sorted(assigned)}\n"
        f"Declare the value as an ingredient in ## Arguments, or capture it at "
        f"runtime as a shell variable: VAR=$(command)"
    )
