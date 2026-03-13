"""Consistency guard: every declared skill output must have an emit instruction in SKILL.md."""

from __future__ import annotations

import re

from autoskillit.core.paths import pkg_root
from autoskillit.recipe.contracts import load_bundled_manifest


def test_every_declared_output_has_emit_instruction_in_skill_md() -> None:
    """Every output declared in skill_contracts.yaml must have a key emit line in SKILL.md.

    This is the permanent architectural guard preventing contracts and SKILL.md from
    diverging silently. Any future skill that declares outputs in contracts but omits
    the emit instruction in SKILL.md will fail this test and cannot be merged.

    Accepts both ``key = value`` (canonical) and ``key=value`` (legacy) formats.
    """
    manifest = load_bundled_manifest()
    skills_dir = pkg_root() / "skills"

    failures = []
    for skill_name, contract in manifest.get("skills", {}).items():
        outputs = contract.get("outputs") or []
        if not outputs:
            continue  # no declared outputs — nothing to check

        skill_md = skills_dir / skill_name / "SKILL.md"
        if not skill_md.exists():
            continue  # skill not bundled (user-side skill)

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

    assert not failures, "\n".join(failures)
