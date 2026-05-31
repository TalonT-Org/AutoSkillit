"""Contract: resolve-review SKILL.md must not contain python3 -c invocations."""

from __future__ import annotations

import re

import pytest

from tests.contracts.conftest import _all_skill_mds


@pytest.fixture()
def resolve_review_content() -> str:
    for name, content in _all_skill_mds():
        if name == "resolve-review":
            return content
    pytest.fail("resolve-review SKILL.md not found")


class TestResolveReviewNoInlinePython:
    def test_no_python3_c_invocations(self, resolve_review_content: str) -> None:
        """resolve-review SKILL.md must not use python3 -c (causes /tmp scratch writes)."""
        matches = re.findall(r"python3\s+-c\b", resolve_review_content)
        assert matches == [], (
            f"Found {len(matches)} python3 -c invocation(s) in resolve-review SKILL.md. "
            "These cause agents to materialize scratch scripts in /tmp. "
            "Use declarative Write-tool instructions instead."
        )

    def test_python_blocks_no_write_text(self, resolve_review_content: str) -> None:
        """```python blocks must not contain .write_text() (agents try to execute them)."""
        python_blocks = re.findall(r"```python\s*\n(.*?)```", resolve_review_content, re.DOTALL)
        for i, block in enumerate(python_blocks):
            assert ".write_text(" not in block, (
                f"```python block #{i + 1} contains .write_text() — agents may try to "
                "execute this as a script. Use declarative Write-tool instructions instead."
            )

    def test_never_constraint_mentions_tmp(self, resolve_review_content: str) -> None:
        """NEVER constraint must explicitly prohibit /tmp scratch files."""
        never_section = re.search(
            r"\*\*NEVER:\*\*\s*\n(.*?)(?:\n\*\*ALWAYS:\*\*|\n##|\Z)",
            resolve_review_content,
            re.DOTALL,
        )
        assert never_section is not None, "NEVER section not found in SKILL.md"
        never_text = never_section.group(1)
        assert "/tmp" in never_text, (
            "NEVER constraint must explicitly mention /tmp to prevent scratch-file writes"
        )
