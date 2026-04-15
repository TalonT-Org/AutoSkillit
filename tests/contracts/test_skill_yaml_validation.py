"""Contract: YAML workflow examples embedded in SKILL.md files must be valid recipes."""

from __future__ import annotations

import re
from pathlib import Path


def _all_skill_roots() -> list[Path]:
    from autoskillit.workspace.skills import bundled_skills_dir, bundled_skills_extended_dir

    return [bundled_skills_dir(), bundled_skills_extended_dir()]


def test_skill_md_yaml_examples_are_valid_workflows() -> None:
    """YAML workflow examples embedded in SKILL.md files must pass validation."""
    import yaml as _yaml

    from autoskillit.recipe.io import (
        _parse_recipe as _parse_workflow,
    )
    from autoskillit.recipe.validator import (
        validate_recipe as validate_workflow,
    )

    yaml_block_re = re.compile(r"```yaml\n(.*?)```", re.DOTALL)

    for skills_dir in _all_skill_roots():
        for skill_md in skills_dir.rglob("SKILL.md"):
            content = skill_md.read_text()
            for match in yaml_block_re.finditer(content):
                block = match.group(1)
                # Only validate blocks that look like full workflow definitions
                if "steps:" not in block or "name:" not in block:
                    continue
                # Skip format templates that use {placeholder} syntax
                if "{script-name}" in block or "{mcp_tool_name}" in block:
                    continue
                data = _yaml.safe_load(block)
                if not isinstance(data, dict) or "steps" not in data:
                    continue
                wf = _parse_workflow(data)
                errors = [e for e in validate_workflow(wf) if "kitchen_rules" not in e.lower()]
                assert not errors, (
                    f"{skill_md.parent.name}/SKILL.md has invalid YAML example:\n  {errors}"
                )
