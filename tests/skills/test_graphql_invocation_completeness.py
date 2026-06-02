"""SKILL.md GraphQL invocation completeness contract.

Every ```graphql block that declares parameterized variables ($owner, $repo, etc.)
must have a corresponding ```bash block with a concrete `gh api graphql` invocation
that binds those variables via individual -F flags.

This prevents the "schema without invocation" anti-pattern where agents must
improvise gh api graphql variable-passing syntax — which is non-obvious and
error-prone (the -f variables=<json blob> pattern silently fails).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from autoskillit.core.paths import pkg_root
from autoskillit.recipe._skill_placeholder_parser import (
    extract_bash_blocks,
    extract_graphql_blocks,
)

pytestmark = [pytest.mark.layer("skills"), pytest.mark.medium]

_SKILLS_DIRS = [pkg_root() / "skills", pkg_root() / "skills_extended"]

_GH_API_GRAPHQL_RE = re.compile(r"gh\s+api\s+graphql\b")


def _all_skill_dirs() -> list[Path]:
    dirs = []
    for base in _SKILLS_DIRS:
        if base.exists():
            dirs.extend(d for d in base.iterdir() if d.is_dir())
    return dirs


def test_graphql_blocks_have_matching_bash_invocations() -> None:
    """Every parameterized graphql block must have a matching gh api graphql bash invocation."""
    failures: list[str] = []

    for skill_dir in _all_skill_dirs():
        skill_md = skill_dir / "SKILL.md"
        if not skill_md.exists():
            continue

        content = skill_md.read_text(encoding="utf-8")
        graphql_blocks = extract_graphql_blocks(content)
        if not graphql_blocks:
            continue

        bash_blocks = extract_bash_blocks(content)
        bash_with_graphql = [b for b in bash_blocks if _GH_API_GRAPHQL_RE.search(b)]

        for block in graphql_blocks:
            variable_names = set(re.findall(r"\$([a-zA-Z_]\w*)", block))
            if not variable_names:
                continue

            skill_name = skill_dir.name

            if not bash_with_graphql:
                failures.append(
                    f"{skill_name}: graphql block declares variables "
                    f"{sorted(variable_names)} but no ```bash block contains "
                    f"'gh api graphql'"
                )
                continue

            for var in variable_names:
                flag_found = any(
                    re.search(rf"-[Ff]\s+{re.escape(var)}=", b) for b in bash_with_graphql
                )
                if not flag_found:
                    failures.append(
                        f"{skill_name}: graphql variable '${var}' has no "
                        f"'-F {var}=' or '-f {var}=' binding in any "
                        f"'gh api graphql' bash block"
                    )

    assert not failures, (
        "SKILL.md files with parameterized graphql blocks must have concrete "
        "'gh api graphql' bash invocations binding each variable via -F flags:\n"
        + "\n".join(f"  - {f}" for f in failures)
    )


def test_graphql_blocks_use_individual_F_flags_not_json_blob() -> None:
    """gh api graphql invocations must not use -f variables=<json blob> anti-pattern."""
    failures: list[str] = []

    for skill_dir in _all_skill_dirs():
        skill_md = skill_dir / "SKILL.md"
        if not skill_md.exists():
            continue

        content = skill_md.read_text(encoding="utf-8")
        bash_blocks = extract_bash_blocks(content)

        for block in bash_blocks:
            if not _GH_API_GRAPHQL_RE.search(block):
                continue

            skill_name = skill_dir.name

            if re.search(r"-f\s+variables=", block) or re.search(r"--field\s+variables=", block):
                failures.append(
                    f"{skill_name}: gh api graphql uses '-f variables=' or "
                    f"'--field variables=' (json blob anti-pattern); use individual "
                    f"-F key=value flags instead"
                )

    assert not failures, (
        "gh api graphql invocations must bind variables via individual -F flags, "
        "not a single -f variables=<json blob>:\n" + "\n".join(f"  - {f}" for f in failures)
    )
