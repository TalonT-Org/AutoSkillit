"""Claude skill-session async hardening has one backend-local authority."""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.small

_ROOT = Path(__file__).parents[2] / "src" / "autoskillit" / "execution" / "backends"


def test_claude_skill_hardening_has_no_codex_consumer_or_duplicate_authority() -> None:
    prompt_source = (_ROOT / "_claude_prompt.py").read_text()
    claude_source = (_ROOT / "claude.py").read_text()
    codex_source = (_ROOT / "codex.py").read_text()

    assert prompt_source.count("_CLAUDE_SKILL_SESSION_HARDENING") == 1
    assert claude_source.count("_CLAUDE_SKILL_SESSION_HARDENING") == 4
    assert "_CLAUDE_SKILL_SESSION_HARDENING" not in codex_source
    for key in ("CLAUDE_CODE_DISABLE_BACKGROUND_TASKS", "CLAUDE_CODE_DISABLE_CRON"):
        assert prompt_source.count(key) == 1
        assert key not in claude_source
        assert key not in codex_source
