"""@-mention structural guard: ensure no SKILL.md hardcodes a GitHub @-mention."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from autoskillit.workspace.skills import (
    bundled_skills_dir,
    bundled_skills_extended_dir,
)

pytestmark = [pytest.mark.layer("workspace"), pytest.mark.small]


def _all_skill_roots() -> list[Path]:
    return [bundled_skills_dir(), bundled_skills_extended_dir()]


def test_no_hardcoded_username_mentions_in_skill_mds() -> None:
    """No SKILL.md may contain a hardcoded GitHub @-mention in prose."""
    # Negative lookbehind prevents matching email local-parts (e.g. noreply@anthropic.com).
    mention_pattern = re.compile(r"(?<![a-zA-Z0-9.])@[A-Za-z][A-Za-z0-9_-]{2,}")
    # Known-safe @-tokens that are not GitHub usernames (e.g. template variables, org names
    # used in documentation context rather than as literal mentions).
    SAFE_TOKENS: frozenset[str] = frozenset({"@anthropic"})
    violations: list[str] = []

    for skills_dir in _all_skill_roots():
        for skill_md in sorted(skills_dir.rglob("SKILL.md")):
            skill_name = skill_md.parent.name
            content = skill_md.read_text()
            in_fence = False
            for lineno, raw_line in enumerate(content.splitlines(), start=1):
                stripped = raw_line.strip()
                if stripped.startswith("```"):
                    in_fence = not in_fence
                    continue
                if in_fence:
                    continue
                # Strip inline code before matching
                prose_line = re.sub(r"`[^`]*`", "", raw_line)
                for match in mention_pattern.finditer(prose_line):
                    token = match.group()
                    if token in SAFE_TOKENS:
                        continue
                    violations.append(f"{skill_name}/SKILL.md:{lineno}: {token!r}")

    assert violations == [], (
        "Hardcoded GitHub @-mentions found in SKILL.md prose. "
        "Use dynamic derivation (e.g., `gh api user -q .login`) instead:\n" + "\n".join(violations)
    )


def test_mention_guard_ignores_python_decorators_in_code_fences(tmp_path: Path) -> None:
    """Python decorators inside code fences must not trigger the @-mention guard."""
    from tests._helpers import strip_markdown_code_regions

    mention_pattern = re.compile(r"(?<![a-zA-Z0-9.])@[A-Za-z][A-Za-z0-9_-]{2,}")
    SAFE_TOKENS: frozenset[str] = frozenset({"@anthropic"})

    skill_md = tmp_path / "SKILL.md"
    skill_md.write_text(
        "---\nname: test-skill\n---\n"
        "# Test Skill\n\n"
        "```python\n"
        "@dataclass\n"
        "class Foo:\n"
        "    pass\n"
        "\n"
        "@pytest.mark.parametrize('x', [1, 2])\n"
        "def test_bar(x):\n"
        "    assert x > 0\n"
        "```\n"
        "\n"
        "Use `@mcp.tool()` for registration.\n"
    )

    prose = strip_markdown_code_regions(skill_md.read_text())
    violations = [
        match.group()
        for line in prose.splitlines()
        for match in mention_pattern.finditer(line)
        if match.group() not in SAFE_TOKENS
    ]
    assert violations == [], f"False positives on code-zone content: {violations}"


def test_mention_guard_catches_prose_at_mention(tmp_path: Path) -> None:
    """A GitHub @-mention in prose (not code) must be caught by the guard."""
    from tests._helpers import strip_markdown_code_regions

    mention_pattern = re.compile(r"(?<![a-zA-Z0-9.])@[A-Za-z][A-Za-z0-9_-]{2,}")
    SAFE_TOKENS: frozenset[str] = frozenset({"@anthropic"})

    skill_md = tmp_path / "SKILL.md"
    skill_md.write_text("---\nname: test-skill\n---\n# Test\n\nContact @SomeUser for help.\n")

    prose = strip_markdown_code_regions(skill_md.read_text())
    violations = [
        match.group()
        for line in prose.splitlines()
        for match in mention_pattern.finditer(line)
        if match.group() not in SAFE_TOKENS
    ]
    assert "@SomeUser" in violations, f"Guard failed to catch prose @-mention; got {violations}"
