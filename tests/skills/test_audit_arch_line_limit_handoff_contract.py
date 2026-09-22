from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("skills"), pytest.mark.medium]

REPO_ROOT = Path(__file__).parents[2]
AUDIT_ARCH_SKILLS = (
    REPO_ROOT / "src/autoskillit/skills_extended/audit-arch/SKILL.md",
    REPO_ROOT / ".claude/skills/audit-arch/SKILL.md",
)


@pytest.mark.parametrize("skill_path", AUDIT_ARCH_SKILLS)
def test_audit_arch_requires_human_handoff_for_infeasible_line_limit_decomposition(
    skill_path: Path,
) -> None:
    content = skill_path.read_text(encoding="utf-8")

    assert "decomposing the file\nfirst" in content
    assert "human-approved last resort" in content
    assert "stop and give a human the path, measured count, and\njustification" in content
    assert "must not add or relax\n`_LINE_LIMIT_EXEMPTIONS`" in content
    assert "create its `PolicyRelaxationApproval`" in content
