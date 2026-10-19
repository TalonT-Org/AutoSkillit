"""Plan-part templates stay compatible with the allocation verifier."""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("contracts"), pytest.mark.medium]

_ALLOCATION_HEADING = "## Issue Requirement Allocation"


def _allocation_blocks(content: str, skill_path: Path) -> list[str]:
    blocks: list[str] = []
    block_lines: list[str] | None = None
    for line_number, line in enumerate(content.splitlines(), start=1):
        if line.startswith("```"):
            if block_lines is None:
                block_lines = []
            else:
                if _ALLOCATION_HEADING in block_lines:
                    blocks.append("\n".join(block_lines))
                block_lines = None
        else:
            if line == _ALLOCATION_HEADING:
                assert block_lines is not None, (
                    f"{skill_path}:{line_number} has an allocation heading outside a fenced block"
                )
            if block_lines is not None:
                block_lines.append(line)
    if block_lines is not None and _ALLOCATION_HEADING in block_lines:
        blocks.append("\n".join(block_lines))
    return blocks


def _verify_allocation_blocks(content: str, document_path: Path) -> int:
    from autoskillit.core import parse_part_allocation, verify_allocation_evidence

    blocks = _allocation_blocks(content, document_path)
    for block_index, block in enumerate(blocks, start=1):
        try:
            rows = parse_part_allocation(block, part_key="P1")
        except ValueError as exc:
            pytest.fail(f"{document_path} allocation block {block_index} is not parseable: {exc}")
        assert rows, f"{document_path} allocation block {block_index} has no rows"
        defects = verify_allocation_evidence(block, rows)
        assert not defects, (
            f"{document_path} allocation block {block_index} has evidence defects: {defects}"
        )
    return len(blocks)


def test_bundled_plan_part_templates_pass_the_allocation_verifier() -> None:
    from autoskillit.workspace.skills import bundled_skills_dir, bundled_skills_extended_dir
    from autoskillit.workspace.skills._resources import load_skill_resource

    expected_skills: set[str] = set()
    verified_skills: set[str] = set()
    verified_block_count = 0
    for skills_root in (bundled_skills_dir(), bundled_skills_extended_dir()):
        for skill_path in skills_root.rglob("SKILL.md"):
            content = skill_path.read_text(encoding="utf-8")
            if "**Plan-part form.**" in content:
                expected_skills.add(skill_path.parent.name)
            assert "Step {N.M} |" not in content, f"{skill_path} has a stale step placeholder"
            assert r"owned \| shared" not in content, (
                f"{skill_path} escapes the allocation separator"
            )

            verified_blocks = _verify_allocation_blocks(content, skill_path)
            verified_block_count += verified_blocks
            if verified_blocks:
                verified_skills.add(skill_path.parent.name)

    resource = load_skill_resource("plan-part-form")
    resource_path = Path(f"skill_resources/{resource.id}.md")
    resource_blocks = _verify_allocation_blocks(resource.body, resource_path)
    assert resource_blocks == 1, (
        f"{resource_path} must contain one fenced complete stage allocation example"
    )

    assert verified_block_count > 0, "no bundled plan-part allocation template was checked"
    assert expected_skills, "no bundled plan-part authoring skill was found"
    assert expected_skills <= verified_skills
