"""Contract: resolve-review SKILL.md must not contain python3 -c invocations."""

from __future__ import annotations

import re

from tests.contracts.conftest import _all_skill_mds

_RESOLVE_REVIEW_CONTENT: str | None = None
for _name, _content in _all_skill_mds():
    if _name == "resolve-review":
        _RESOLVE_REVIEW_CONTENT = _content
        break


class TestResolveReviewNoInlinePython:
    def test_no_python3_c_invocations(self) -> None:
        """resolve-review SKILL.md must not use python3 -c (causes /tmp scratch writes)."""
        assert _RESOLVE_REVIEW_CONTENT is not None, "resolve-review SKILL.md not found"
        matches = re.findall(r"python3\s+-c\b", _RESOLVE_REVIEW_CONTENT)
        assert matches == [], (
            f"Found {len(matches)} python3 -c invocation(s) in resolve-review SKILL.md. "
            "These cause agents to materialize scratch scripts in /tmp. "
            "Use declarative Write-tool instructions instead."
        )

    def test_python_blocks_no_write_text(self) -> None:
        """```python blocks must not contain .write_text() (agents try to execute them)."""
        assert _RESOLVE_REVIEW_CONTENT is not None, "resolve-review SKILL.md not found"
        # Extract content inside ```python ... ``` fences
        python_blocks = re.findall(r"```python\s*\n(.*?)```", _RESOLVE_REVIEW_CONTENT, re.DOTALL)
        for i, block in enumerate(python_blocks):
            assert ".write_text(" not in block, (
                f"```python block #{i + 1} contains .write_text() — agents may try to "
                "execute this as a script. Use declarative Write-tool instructions instead."
            )

    def test_never_constraint_mentions_tmp(self) -> None:
        """NEVER constraint must explicitly prohibit /tmp scratch files."""
        assert _RESOLVE_REVIEW_CONTENT is not None, "resolve-review SKILL.md not found"
        never_section = re.search(
            r"\*\*NEVER:\*\*\s*\n(.*?)(?:\n\*\*ALWAYS:\*\*|\n##)",
            _RESOLVE_REVIEW_CONTENT,
            re.DOTALL,
        )
        assert never_section is not None, "NEVER section not found in SKILL.md"
        never_text = never_section.group(1)
        assert "/tmp" in never_text, (
            "NEVER constraint must explicitly mention /tmp to prevent scratch-file writes"
        )
