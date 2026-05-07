"""Verify guard fail-mode matrix documentation accuracy."""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
GUARDS_CLAUDE = REPO_ROOT / "src/autoskillit/hooks/guards/CLAUDE.md"
SAFETY_HOOKS = REPO_ROOT / "docs/safety/hooks.md"


class TestGuardsClaudeMd:
    def test_guards_claude_md_contains_fail_mode_contract_section(self) -> None:
        content = GUARDS_CLAUDE.read_text()
        assert "### Fail-Mode Contract" in content

    def test_guards_claude_md_fail_mode_matrix_lists_three_fail_closed_guards(self) -> None:
        content = GUARDS_CLAUDE.read_text()
        for guard in [
            "skill_command_guard.py",
            "open_kitchen_guard.py",
            "skill_orchestration_guard.py",
        ]:
            assert guard in content

    def test_guards_claude_md_documents_design_principle(self) -> None:
        content = GUARDS_CLAUDE.read_text()
        assert "Garbage-in" in content
        assert "Unknown-tier" in content

    def test_old_fail_mode_sentence_replaced(self) -> None:
        content = GUARDS_CLAUDE.read_text()
        old = "Guards fail-open for malformed input. `skill_command_guard.py` has split error handling"
        assert old not in content


class TestSafetyHooksMd:
    def test_safety_hooks_md_contains_fail_modes_section(self) -> None:
        content = SAFETY_HOOKS.read_text()
        assert "## Fail Modes" in content

    def test_safety_hooks_md_fail_mode_matrix_lists_three_fail_closed_guards(self) -> None:
        content = SAFETY_HOOKS.read_text()
        for guard in [
            "skill_command_guard.py",
            "open_kitchen_guard.py",
            "skill_orchestration_guard.py",
        ]:
            assert guard in content
