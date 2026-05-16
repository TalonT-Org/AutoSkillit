"""Contract: retry-worktree SKILL.md must check sidecar before git upstream."""

from pathlib import Path

SKILL_MD = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "autoskillit"
    / "skills_extended"
    / "retry-worktree"
    / "SKILL.md"
)


def test_sidecar_checked_before_git_upstream() -> None:
    """Sidecar file read must appear before @{upstream} lookup in SKILL.md Step 1.

    The sidecar is the authoritative write-time record; git upstream tracking
    is a secondary signal that can silently fail (2>/dev/null).
    """
    content = SKILL_MD.read_text()
    sidecar_pos = content.find('cat "${STORE_FILE}"')
    upstream_pos = content.find("@{upstream}")
    assert sidecar_pos != -1, "SKILL.md missing sidecar cat command"
    assert upstream_pos != -1, "SKILL.md missing @{upstream} lookup"
    assert sidecar_pos < upstream_pos, (
        "Sidecar file read must come BEFORE @{upstream} lookup in Step 1. "
        f"sidecar_pos={sidecar_pos}, upstream_pos={upstream_pos}"
    )
