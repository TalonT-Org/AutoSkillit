"""Require bundled skills to name literal write targets in prose."""

from __future__ import annotations

import re

import pytest

from tests.contracts._skill_prose_write_cues import iter_write_cue_targets
from tests.contracts.conftest import _all_skill_mds

pytestmark = [pytest.mark.layer("contracts"), pytest.mark.small]

_EXPANSION = re.compile(r"\$(?:[A-Za-z_][A-Za-z0-9_]*|[0-9]+)|\$\{|\$\(|\$\[")
_BARE_VARIABLE = re.compile(r"^\$(?:([A-Za-z_][A-Za-z0-9_]*)|\{([A-Za-z_][A-Za-z0-9_]*)\})$")


def _command_bodies_replaced(value: str) -> str:
    output: list[str] = []
    index = 0
    while index < len(value):
        if value.startswith("$(", index):
            depth = 1
            cursor = index + 2
            while cursor < len(value) and depth:
                if value[cursor] == "(":
                    depth += 1
                elif value[cursor] == ")":
                    depth -= 1
                cursor += 1
            if depth == 0:
                output.append("COMMAND")
                index = cursor
                continue
        if value[index] == "`":
            closing = value.find("`", index + 1)
            if closing >= 0:
                output.append("COMMAND")
                index = closing + 1
                continue
        output.append(value[index])
        index += 1
    return "".join(output)


def _is_nonliteral_target(content: str, delimiter_length: int) -> bool:
    # Markdown code spans use one padding space at each edge when both are present.
    value = content
    if len(value) >= 2 and value[0] in " \t" and value[-1] in " \t" and value.strip():
        value = value[1:-1]

    shell_expansion = _EXPANSION.search(value) is not None or (
        delimiter_length >= 2 and "`" in value
    )
    if not shell_expansion:
        return False

    bare_variable = _BARE_VARIABLE.fullmatch(value)
    if bare_variable is not None:
        name = bare_variable.group(1) or bare_variable.group(2)
        if re.search(r"(?i)(PATH|DIR|FILE|BODY|OUTPUT)", name):
            return True

    path_candidate = _command_bodies_replaced(value)
    return ("/" in path_candidate or "." in path_candidate) and not any(
        char.isspace() for char in path_candidate
    )


def _find_nonliteral_write_targets(content: str) -> list[tuple[int, int, str, str]]:
    """Return (cue line, target line, instruction, target) for dynamic prose paths."""
    findings: list[tuple[int, int, str, str]] = []
    seen: set[tuple[int, int, str, str]] = set()
    for candidate in iter_write_cue_targets(content):
        if not _is_nonliteral_target(candidate.target, candidate.delimiter_length):
            continue
        finding = (
            candidate.cue_line,
            candidate.target_line,
            candidate.instruction,
            candidate.target,
        )
        if finding not in seen:
            findings.append(finding)
            seen.add(finding)
    return findings


@pytest.mark.parametrize(
    ("skill_name", "content"),
    [pytest.param(name, content, id=name) for name, content in _all_skill_mds()],
)
def test_bundled_skills_use_literal_write_targets(skill_name: str, content: str) -> None:
    failures = _find_nonliteral_write_targets(content)
    assert not failures, "\n".join(
        f"{skill_name}:{cue_line} (target line {target_line}): {instruction!r} "
        f"uses dynamic target {target!r}; see skills_extended/AGENTS.md § Literal write targets"
        for cue_line, target_line, instruction, target in failures
    )


@pytest.mark.parametrize(
    ("prose", "expected"),
    [
        ("Write the report to `${OUT_DIR}/r.md`", True),
        ("Save to `$(dirname $1)/x.json`", True),
        ("Write the updated plan document to `$2/refined_plan.json`.", True),
        ("Write the file to:\n\n`${X_DIR}/a.md`", True),
        ("Path: `$AUDIT_BASE_DIR/v.md`", True),
        ("Write the markdown report to `${OUTPUT_PATH}`.", True),
        ("Write\n`${X_DIR}/b.json`.", True),
        ("write a lens file at\n`${X_DIR}/c.md`", True),
        ("Write `name` JSON to\n`${X_DIR}/d.json`", True),
        ("Output path. Default:\n  `${AUTOSKILLIT_TEMP}/reports/output.md`", True),
        (
            "Resolve the output path:\n"
            "  - Use $2 if provided.\n"
            "  - Otherwise: `${AUTOSKILLIT_TEMP}/reports/output_$(date +%Y-%m-%d_%H%M%S).md`",
            True,
        ),
        ("Save outside the checkout to `${OUTPUT_DIR}/r.md`", True),
        ("Write to `` `date +%s`/report.md ``", True),
        ("- Write the report.\n  - Save it to `${OUTPUT_DIR}/nested.md`", True),
        ("Never write outside `${X}/`", False),
        ("### NEVER\n- Write outside `${X}/`", False),
        (
            "Write `report.md`.\nOutput to terminal:\n"
            "- Worktree path: `${WORKTREE_PATH}`\n- Path: `$AUDIT_BASE_DIR/v.md`",
            False,
        ),
        ("Output to terminal:\n- Worktree path: `${WORKTREE_PATH}`", False),
        (
            "Output to terminal:\n- Worktree path:\n  `${WORKTREE_PATH}`\n"
            "  - Path: `$AUDIT_BASE_DIR/v.md`",
            False,
        ),
        ("Write the report to `{{AUTOSKILLIT_TEMP}}/s/r_{YYYY-MM-DD_HHMMSS}.md`", False),
        ("Rebase onto `$REMOTE/{base_branch}`", False),
        ("1. Write the report to `literal.md`.\n2. Read `${INPUT_DIR}/source.md`.", False),
        ("1. Write the report to `literal.md`.\n\n## Inputs\n`${INPUT_DIR}/source.md`", False),
        ("~~~markdown\n```\nWrite to `${OUT_DIR}/hidden.md`\n~~~", False),
        ("````markdown\n```\nWrite to `${OUT_DIR}/hidden.md`", False),
    ],
)
def test_scanner_controls(prose: str, expected: bool) -> None:
    assert bool(_find_nonliteral_write_targets(prose)) is expected, prose


def test_four_backtick_fence_masks_nested_examples_and_preserves_line_numbers() -> None:
    prose = (
        "````markdown\n"
        "Write to `${OUT_DIR}/hidden.md`\n"
        "```\n"
        "Write to `${OUT_DIR}/also_hidden.md`\n"
        "```\n"
        "````\n"
        "Write to `${OUT_DIR}/visible.md`"
    )

    expected_visible_line = 7
    findings = _find_nonliteral_write_targets(prose)
    assert [(cue_line, target_line, target) for cue_line, target_line, _, target in findings] == [
        (expected_visible_line, expected_visible_line, "${OUT_DIR}/visible.md")
    ]
