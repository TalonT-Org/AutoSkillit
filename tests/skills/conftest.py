import pytest

from autoskillit.core.paths import pkg_root


@pytest.fixture(scope="module")
def skill_text() -> str:
    skill_path = pkg_root() / "skills_extended" / "investigate" / "SKILL.md"
    assert skill_path.exists(), f"SKILL.md not found at {skill_path}"
    return skill_path.read_text()


@pytest.fixture(scope="module")
def deep_workflow_section(skill_text: str) -> str:
    """Extract ## Deep Analysis Mode Workflow section to end-of-file (or next ## at depth ≤ 2)."""
    start_idx = skill_text.find("## Deep Analysis Mode Workflow")
    if start_idx == -1:
        pytest.fail("'## Deep Analysis Mode Workflow' not found in investigate SKILL.md")
    search_from = start_idx + len("## Deep Analysis Mode Workflow")
    next_h2 = -1
    for i in range(search_from, len(skill_text) - 2):
        if skill_text[i : i + 3] == "## " and skill_text[i - 1] == "\n":
            next_h2 = i
            break
    if next_h2 != -1:
        return skill_text[start_idx:next_h2]
    return skill_text[start_idx:]


def _extract_step_section(deep_workflow_section: str, step_name: str) -> str:
    """Helper to extract the text of a specific D-step subsection."""
    start = deep_workflow_section.find(f"### {step_name}")
    if start == -1:
        return ""
    next_step = deep_workflow_section.find("### Step D", start + 1)
    if next_step != -1:
        return deep_workflow_section[start:next_step]
    return deep_workflow_section[start:]


@pytest.fixture(scope="module")
def report_section(skill_text: str) -> str:
    """Extract the Step 4: Write Report section text.

    Uses the next top-level ``## `` heading that is NOT inside a fenced
    code block as the boundary. The report template contains ``## ``
    headings inside a markdown code fence (```markdown ... ```) which must
    be included — so we skip ``## `` occurrences between fence markers.
    """
    step_4_idx = skill_text.find("### Step 4:")
    if step_4_idx == -1:
        pytest.fail("Step 4 not found in investigate SKILL.md")
    in_fence = False
    lines = skill_text[step_4_idx:].split("\n")
    for i, line in enumerate(lines):
        if i == 0:
            continue
        if line.startswith("```"):
            in_fence = not in_fence
            continue
        if not in_fence and line.startswith("## ") and not line.startswith("### "):
            end_idx = step_4_idx + sum(len(prev) + 1 for prev in lines[:i])
            return skill_text[step_4_idx:end_idx]
    return skill_text[step_4_idx:]
