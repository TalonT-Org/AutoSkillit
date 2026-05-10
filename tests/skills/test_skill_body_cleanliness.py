"""Assert that no SKILL.md body references %%ORDER_UP%%."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from autoskillit.core.paths import pkg_root

_SKILLS_DIRS = [pkg_root() / "skills", pkg_root() / "skills_extended"]


def _all_skill_mds() -> list[Path]:
    paths = []
    for d in _SKILLS_DIRS:
        paths.extend(d.glob("*/SKILL.md"))
    return sorted(paths)


@pytest.mark.parametrize("skill_md", _all_skill_mds(), ids=lambda p: p.parent.name)
def test_no_order_up_in_skill_body(skill_md: Path) -> None:
    """No SKILL.md should reference %%ORDER_UP%% — completion is injected at prompt level."""
    text = skill_md.read_text()
    assert "%%ORDER_UP%%" not in text, (
        f"{skill_md.parent.name}/SKILL.md contains %%ORDER_UP%% reference.\n"
        "The completion directive is injected by _inject_completion_directive() "
        "in commands.py. Remove it from the skill body."
    )


_OUTPUT_SECTION_RE = re.compile(r"^## Output\s*$", re.MULTILINE)
_ORCHESTRATION_REF_RE = re.compile(
    r"(?i)(ORCHESTRATION DIRECTIVE|completion marker|end your (final )?response with)",
    re.MULTILINE,
)
_TOKEN_NAME_RE = re.compile(r"^(\w+)\s*=", re.MULTILINE)


@pytest.mark.parametrize("skill_md", _all_skill_mds(), ids=lambda p: p.parent.name)
def test_no_merge_conflict_markers_in_skill_md(skill_md: Path) -> None:
    """SKILL.md must not contain unresolved git merge conflict markers."""
    text = skill_md.read_text()
    lines = text.splitlines()
    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        if (
            stripped.startswith("<<<<<<<")
            or stripped.startswith("=======")
            or stripped.startswith(">>>>>>>")
        ):
            pytest.fail(
                f"{skill_md.parent.name}/SKILL.md line {i} contains an unresolved "
                f"git merge conflict marker: {stripped[:20]!r}"
            )


_KNOWN_VIOLATORS: frozenset[str] = frozenset(
    {
        "compose-pr",
        "compose-research-pr",
        "make-campaign",
        "make-plan",
        "prepare-issue",
        "prepare-pr",
        "prepare-research-pr",
        "rectify",
        "resolve-claims-review",
        "resolve-research-review",
        "resolve-review",
        "review-pr",
        "setup-environment",
        "setup-project",
        "stage-data",
    }
)


@pytest.mark.parametrize("skill_md", _all_skill_mds(), ids=lambda p: p.parent.name)
def test_output_section_compatible_with_orchestration_directive(skill_md: Path) -> None:
    """SKILL.md Output sections with contract tokens should reference the orchestration directive.

    If an Output section declares structured output tokens (e.g., plan_path=), it should
    reference the ORCHESTRATION DIRECTIVE. This prevents the Output section from overriding
    the prompt-level completion marker for non-Claude providers.
    """
    skill_name = skill_md.parent.name
    text = skill_md.read_text()
    output_match = _OUTPUT_SECTION_RE.search(text)
    if not output_match:
        return

    output_start = output_match.end()
    next_section = re.search(r"^##\s+\w", text[output_start:], re.MULTILINE)
    output_end = len(text) if next_section is None else output_start + next_section.start()
    output_text = text[output_start:output_end]

    token_names = set(_TOKEN_NAME_RE.findall(output_text))
    if not token_names:
        return

    has_ref = bool(_ORCHESTRATION_REF_RE.search(output_text))
    if has_ref:
        return

    msg = (
        f"{skill_name}/SKILL.md ## Output section specifies contract tokens "
        f"({sorted(token_names)}) but does not reference the ORCHESTRATION DIRECTIVE. "
        "Add: 'Include the completion marker from the ORCHESTRATION DIRECTIVE'."
    )
    if skill_name in _KNOWN_VIOLATORS:
        pytest.xfail(msg)
    pytest.fail(msg)
