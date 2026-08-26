"""Line-limit ceiling tests for the #4852 rules_skill_content split.

Verifies that the decomposition satisfies three acceptance criteria:

  - Every successor file (`rules_skill_content*.py`) is at most 750 lines.
  - The `REQ-CNST-010-E11` exemption entry is removed from
    `_LINE_LIMIT_EXEMPTIONS`.
  - The architectural line-limit enforcer still passes after E11 removal.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]

WORKTREE_ROOT = Path(__file__).resolve().parents[3]
RULES_DIR = WORKTREE_ROOT / "src" / "autoskillit" / "recipe" / "rules"


def _count_lines(path: Path) -> int:
    return len(path.read_text(encoding="utf-8").splitlines())


def test_each_split_module_under_750_lines() -> None:
    """Every rules_skill_content*.py successor file is ≤ 750 lines."""
    successor_files = sorted(RULES_DIR.glob("rules_skill_content*.py"))
    assert successor_files, "No rules_skill_content*.py files found in recipe/rules/"
    for path in successor_files:
        line_count = _count_lines(path)
        assert line_count <= 750, (
            f"{path.relative_to(WORKTREE_ROOT)} has {line_count} lines, exceeds 750-line ceiling"
        )


def test_e11_entry_removed() -> None:
    """No key resolving to rules_skill_content.py in _LINE_LIMIT_EXEMPTIONS."""
    from tests.arch.test_subpackage_isolation import _LINE_LIMIT_EXEMPTIONS

    keys = list(_LINE_LIMIT_EXEMPTIONS.keys())
    # The bare filename key is no longer present.
    assert "rules_skill_content.py" not in keys, (
        "REQ-CNST-010-E11 should have been removed from _LINE_LIMIT_EXEMPTIONS; found key in dict"
    )
    # And no relative-path key that ends with rules_skill_content.py either.
    matching = [k for k in keys if k.endswith("rules_skill_content.py")]
    assert not matching, (
        f"No key ending with 'rules_skill_content.py' should remain in "
        f"_LINE_LIMIT_EXEMPTIONS; found: {matching}"
    )


def test_no_src_module_exceeds_1000_lines() -> None:
    """The architectural enforcer must still pass after E11 removal."""
    from tests.arch.test_subpackage_isolation import test_no_src_module_exceeds_line_limit

    test_no_src_module_exceeds_line_limit()
