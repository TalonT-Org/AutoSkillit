"""Tests for agent_backend gating in _llm_triage."""

from __future__ import annotations

import json as _json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from autoskillit._llm_triage import _triage_batch, triage_staleness
from autoskillit.execution.backends import ClaudeCodeBackend, CodexBackend
from autoskillit.execution.process import SubprocessResult, TerminationReason
from autoskillit.recipe.contracts import StaleItem

pytestmark = [pytest.mark.small]


@pytest.mark.anyio
async def test_triage_batch_non_claude_backend_returns_all_meaningful(
    monkeypatch: pytest.MonkeyPatch,
):
    """Non-claude-code backend returns meaningful=True without spawning a subprocess."""

    skill_md_content = "# dummy\nContent."
    cache = {"my-skill": skill_md_content}
    items = [
        StaleItem(
            skill="my-skill",
            reason="hash_mismatch",
            stored_value="aaa",
            current_value="bbb",
        ),
    ]

    mock_run = AsyncMock()
    monkeypatch.setattr("autoskillit._llm_triage.run_managed_async", mock_run)

    results = await _triage_batch(items, cache, backend=CodexBackend())

    assert len(results) == 1
    assert results[0]["meaningful"] is True
    assert "Skipped LLM triage" in results[0]["summary"]
    mock_run.assert_not_called()


@pytest.mark.anyio
async def test_triage_staleness_non_claude_backend_returns_all_meaningful(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """triage_staleness with non-claude-code backend skips subprocess and returns meaningful."""

    skill_dir = tmp_path / "my-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("# dummy\nContent.")

    monkeypatch.setattr("autoskillit._llm_triage.bundled_skills_dir", lambda: tmp_path)
    mock_run = AsyncMock()
    monkeypatch.setattr("autoskillit._llm_triage.run_managed_async", mock_run)

    items = [
        StaleItem(
            skill="my-skill",
            reason="hash_mismatch",
            stored_value="aaa",
            current_value="bbb",
        ),
    ]

    results = await triage_staleness(items, backend=CodexBackend())

    assert len(results) == 1
    assert results[0]["meaningful"] is True
    assert "Skipped LLM triage" in results[0]["summary"]
    mock_run.assert_not_called()


@pytest.mark.anyio
async def test_triage_batch_claude_code_backend_does_call_subprocess(
    monkeypatch: pytest.MonkeyPatch,
):
    """claude-code backend reaches the subprocess call."""

    skill_md_content = "# dummy\nContent."
    cache = {"my-skill": skill_md_content}
    items = [
        StaleItem(
            skill="my-skill",
            reason="hash_mismatch",
            stored_value="aaa",
            current_value="bbb",
        ),
    ]

    ndjson = "\n".join(
        [
            _json.dumps({"type": "assistant", "message": {"content": []}}),
            _json.dumps(
                {
                    "type": "result",
                    "subtype": "success",
                    "result": _json.dumps(
                        [
                            {
                                "index": 1,
                                "skill": "my-skill",
                                "meaningful_change": False,
                                "summary": "no change",
                            }
                        ]
                    ),
                    "session_id": "test-session",
                    "is_error": False,
                }
            ),
        ]
    )
    fake_result = SubprocessResult(
        returncode=0,
        stdout=ndjson,
        stderr="",
        termination=TerminationReason.NATURAL_EXIT,
        pid=0,
    )
    mock_run = AsyncMock(return_value=fake_result)
    monkeypatch.setattr("autoskillit._llm_triage.run_managed_async", mock_run)

    results = await _triage_batch(items, cache, backend=ClaudeCodeBackend())

    mock_run.assert_called_once()
    assert len(results) == 1
    assert results[0]["meaningful"] is False
    assert results[0]["summary"] == "no change"
