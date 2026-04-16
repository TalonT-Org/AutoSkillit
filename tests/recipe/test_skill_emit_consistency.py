"""Consistency guard: every declared skill output must have an emit instruction in SKILL.md."""

from __future__ import annotations

import re

import pytest

from autoskillit.core.paths import pkg_root
from autoskillit.recipe.contracts import load_bundled_manifest

pytestmark = [pytest.mark.layer("recipe")]

# Key pattern: if a contract output pattern requires an absolute path (contains
# \s*=\s*/.+), verify that the SKILL.md does not assign the variable to a
# relative path (i.e., assignments starting with ../ or a non-/ non-$ character).
_ABS_PATH_PATTERN_RE = re.compile(r"\\s\*=\\s\*/\.\+")

# Matches bash variable assignments like WORKTREE_PATH="../..." or PLAN_PATH="worktrees/..."
# Captures: key name and the assigned value (first character after opening quote or directly).
_RELATIVE_ASSIGN_RE = re.compile(
    r"""^([A-Z_]+)=(?:"(\.\.[^"]*)|"([^/$"\n][^"]*)|([^"$\s/][^\s]*))""",
    re.MULTILINE,
)

# Matches a subsequent absolute-resolution assignment: VAR="$(cd ...) or VAR=$(realpath ...)
# These patterns follow a relative initial assignment and canonicalize the path to absolute.
_ABSOLUTE_RESOLVE_RE = re.compile(
    r'^([A-Z_]+)="?\$\(cd\b|^([A-Z_]+)="?\$\(realpath\b',
    re.MULTILINE,
)


def _format_compat_check(
    skill_name: str,
    content: str,
    expected_output_patterns: list[str],
) -> list[str]:
    """Check that SKILL.md bash assignments don't produce relative paths for
    outputs whose contract patterns require an absolute path (/.+).

    A relative initial assignment is acceptable only when immediately followed
    by an absolute-resolution statement (``$(cd ... && pwd)`` or
    ``$(realpath ...)``).  If the relative assignment has no subsequent
    resolution, it is reported as a failure.

    Returns a list of failure strings (empty = no issues).
    """
    failures = []
    for pattern in expected_output_patterns:
        if not _ABS_PATH_PATTERN_RE.search(pattern):
            continue  # pattern doesn't require an absolute path — skip
        # Extract the output key from the pattern (e.g., "worktree_path" from
        # "worktree_path\s*=\s*/.+")
        key_match = re.match(r"^([a-z_]+)", pattern)
        if not key_match:
            continue
        key = key_match.group(1)
        bash_key = key.upper()  # e.g., worktree_path → WORKTREE_PATH
        # Look for relative assignments of this bash variable
        for m in _RELATIVE_ASSIGN_RE.finditer(content):
            var_name = m.group(1)
            if var_name != bash_key:
                continue
            relative_value = m.group(2) or m.group(3) or m.group(4)
            if not relative_value:
                continue
            relative_pos = m.start()
            # Allow relative initial assignment when a subsequent absolute-resolution
            # assignment exists for the same variable (cd+pwd or realpath pattern).
            has_later_resolution = any(
                (rm.group(1) or rm.group(2)) == bash_key and rm.start() > relative_pos
                for rm in _ABSOLUTE_RESOLVE_RE.finditer(content)
            )
            if not has_later_resolution:
                assigned = m.group(0).split("=", 1)[1]
                failures.append(
                    f"skill '{skill_name}': pattern {pattern!r} requires absolute path "
                    f"but SKILL.md assigns {bash_key!r} = '{assigned}' (relative). "
                    "Fix: resolve to absolute with "
                    'WORKTREE_PATH="$(cd "${WORKTREE_PATH}" && pwd)" '
                    "after git worktree add."
                )
    return failures


def test_every_declared_output_has_emit_instruction_in_skill_md() -> None:
    """Every output declared in skill_contracts.yaml must have a key emit line in SKILL.md.

    This is the permanent architectural guard preventing contracts and SKILL.md from
    diverging silently. Any future skill that declares outputs in contracts but omits
    the emit instruction in SKILL.md will fail this test and cannot be merged.

    Accepts both ``key = value`` (canonical) and ``key=value`` (legacy) formats.

    Covers all skill tiers:
    - Tier 1: skills/
    - Tier 2+3: skills_extended/

    Fails with a diagnostic if a skill declared in skill_contracts.yaml has no SKILL.md
    in any tier (rather than silently skipping it).
    """
    manifest = load_bundled_manifest()
    skills_dir = pkg_root() / "skills"
    skills_extended_dir = pkg_root() / "skills_extended"

    failures = []
    for skill_name, contract in manifest.get("skills", {}).items():
        outputs = contract.get("outputs") or []
        expected_output_patterns: list[str] = contract.get("expected_output_patterns") or []
        if not outputs:
            continue  # no declared outputs — nothing to check

        # Search both tiers for the SKILL.md
        skill_md = skills_dir / skill_name / "SKILL.md"
        if not skill_md.exists():
            skill_md = skills_extended_dir / skill_name / "SKILL.md"
        if not skill_md.exists():
            failures.append(
                f"skill '{skill_name}': declared in skill_contracts.yaml with outputs "
                f"but SKILL.md not found in skills/ or skills_extended/. "
                f"Checked: {skills_dir / skill_name / 'SKILL.md'} and "
                f"{skills_extended_dir / skill_name / 'SKILL.md'}"
            )
            continue

        content = skill_md.read_text()
        for output in outputs:
            output_name = output["name"]
            # Accept both spaced and unspaced: key = value OR key=value
            pattern = re.compile(rf"{re.escape(output_name)}\s*=\s*")
            if not pattern.search(content):
                failures.append(
                    f"skill '{skill_name}': declares output '{output_name}' "
                    f"in skill_contracts.yaml but SKILL.md has no emit line "
                    f"'{output_name} = ...'"
                )

        # Format-compatibility check: ensure relative assignments don't violate
        # absolute-path pattern requirements.
        failures.extend(_format_compat_check(skill_name, content, expected_output_patterns))

    assert not failures, "\n".join(failures)
