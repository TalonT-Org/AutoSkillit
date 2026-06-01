"""Contract: no SKILL.md may prescribe interpreter-mediated file writes."""

from __future__ import annotations

import pytest

from autoskillit.hooks import _INTERPRETER_LINE_RE, _WRITE_APIS_RE
from autoskillit.recipe._skill_placeholder_parser import extract_bash_blocks, extract_python_blocks
from autoskillit.recipe.rules.rules_skill_content import INTERPRETER_WRITE_ALLOWLIST
from tests.contracts.conftest import _all_skill_mds

pytestmark = [pytest.mark.layer("contracts"), pytest.mark.small]

_SKILL_MDS = _all_skill_mds()


def _has_interpreter_write_in_bash(block: str) -> bool:
    has_interpreter = False
    for line in block.splitlines():
        stripped = line.lstrip()
        cleaned = stripped.lstrip("$(")
        if _INTERPRETER_LINE_RE.search(cleaned):
            has_interpreter = True
            break
    return has_interpreter and bool(_WRITE_APIS_RE.search(block))


@pytest.mark.parametrize(
    ("skill_name", "content"),
    _SKILL_MDS,
    ids=[name for name, _ in _SKILL_MDS],
)
def test_no_interpreter_mediated_writes_in_bash_blocks(
    skill_name: str,
    content: str,
) -> None:
    if any(sname == skill_name for sname, _ in INTERPRETER_WRITE_ALLOWLIST):
        pytest.skip(f"{skill_name} is in INTERPRETER_WRITE_ALLOWLIST")
    for block in extract_bash_blocks(content):
        assert not _has_interpreter_write_in_bash(block), (
            f"Skill '{skill_name}' SKILL.md bash block contains an interpreter-mediated "
            f"file write (python3 -c / heredoc with .write_text(), open(..., 'w'), etc.). "
            f"Use the Write tool or bash redirects instead."
        )


@pytest.mark.parametrize(
    ("skill_name", "content"),
    _SKILL_MDS,
    ids=[name for name, _ in _SKILL_MDS],
)
def test_no_write_apis_in_python_code_blocks(
    skill_name: str,
    content: str,
) -> None:
    if any(sname == skill_name for sname, _ in INTERPRETER_WRITE_ALLOWLIST):
        pytest.skip(f"{skill_name} is in INTERPRETER_WRITE_ALLOWLIST")
    for i, block in enumerate(extract_python_blocks(content)):
        assert not _WRITE_APIS_RE.search(block), (
            f"Skill '{skill_name}' ```python block #{i + 1} contains a write API call "
            f"(.write_text(), open(..., 'w'), etc.). Agents may try to execute this as a "
            f"script. Use declarative Write-tool instructions instead."
        )


def test_allowlist_entries_still_needed() -> None:
    skill_map = dict(_SKILL_MDS)
    for skill_name, pattern_fragment in INTERPRETER_WRITE_ALLOWLIST:
        content = skill_map.get(skill_name)
        assert content is not None, (
            f"INTERPRETER_WRITE_ALLOWLIST entry ({skill_name!r}, {pattern_fragment!r}) "
            f"references a skill that no longer exists"
        )
        assert pattern_fragment in content, (
            f"INTERPRETER_WRITE_ALLOWLIST entry ({skill_name!r}, {pattern_fragment!r}) "
            f"is stale — pattern fragment not found in {skill_name}/SKILL.md"
        )
