"""Tests for recipe._triage module (relocated from tests/test_llm_triage.py)."""

from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit.recipe.contracts import StaleItem

# ---------------------------------------------------------------------------
# T1: Module importable at new L2 location
# ---------------------------------------------------------------------------


def test_recipe_triage_module_importable():
    """recipe._triage is importable at its new L2 location."""
    from autoskillit.recipe._triage import triage_staleness  # noqa: F401

    assert callable(triage_staleness)


def test_old_llm_triage_module_does_not_exist():
    """Package-root _llm_triage.py is deleted after the move."""
    from autoskillit.core import pkg_root

    old_path = pkg_root() / "_llm_triage.py"
    assert not old_path.exists(), (
        f"_llm_triage.py still exists at {old_path}; should be deleted after move to recipe/_triage.py"
    )


# ---------------------------------------------------------------------------
# T-P1-7-A: SKILL.md cached per unique skill
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_triage_staleness_reads_skill_md_once_per_unique_skill(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """SKILL.md is read at most once per unique skill name per triage_staleness call."""
    from unittest.mock import AsyncMock

    from autoskillit.recipe._triage import triage_staleness
    from autoskillit.execution.process import SubprocessResult, TerminationReason

    skill_dir = tmp_path / "implement-worktree"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("# Implement Worktree\nDummy content.")

    read_calls: list[str] = []
    real_read_text = Path.read_text

    def tracking_read_text(self: Path, *args: object, **kwargs: object) -> str:
        if self.name == "SKILL.md":
            read_calls.append(str(self))
        return real_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", tracking_read_text)
    monkeypatch.setattr("autoskillit.recipe._triage.bundled_skills_dir", lambda: tmp_path)

    fake_result = SubprocessResult(
        returncode=0,
        stdout='{"meaningful_change": false, "summary": "no change"}',
        stderr="",
        termination=TerminationReason.NATURAL_EXIT,
        pid=0,
    )
    monkeypatch.setattr(
        "autoskillit.recipe._triage.run_managed_async",
        AsyncMock(return_value=fake_result),
    )

    items = [
        StaleItem(
            skill="implement-worktree",
            reason="hash_mismatch",
            stored_value="old1",
            current_value="new1",
        ),
        StaleItem(
            skill="implement-worktree",
            reason="hash_mismatch",
            stored_value="old2",
            current_value="new2",
        ),
    ]
    await triage_staleness(items)
    assert len(read_calls) == 1, (
        f"SKILL.md read {len(read_calls)} times; expected exactly 1 (cache hit on second item)"
    )


# ---------------------------------------------------------------------------
# T7: triage_staleness — run_managed_async lifecycle, logging, and error paths
# ---------------------------------------------------------------------------


class TestTriageStaleness:
    """Executable test coverage for triage_staleness failure paths (run_managed_async variant)."""

    @pytest.mark.anyio
    async def test_triage_staleness_timeout_returns_meaningful_true(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """When run_managed_async returns TIMED_OUT, result is meaningful=True."""
        from unittest.mock import AsyncMock

        from autoskillit.recipe._triage import triage_staleness
        from autoskillit.execution.process import SubprocessResult, TerminationReason

        skill_dir = tmp_path / "test-skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text("# Test Skill\nDummy content.")

        monkeypatch.setattr("autoskillit.recipe._triage.bundled_skills_dir", lambda: tmp_path)
        monkeypatch.setattr(
            "autoskillit.recipe._triage.run_managed_async",
            AsyncMock(
                return_value=SubprocessResult(
                    returncode=1,
                    stdout="",
                    stderr="",
                    termination=TerminationReason.TIMED_OUT,
                    pid=0,
                )
            ),
        )

        item = StaleItem(
            skill="test-skill",
            reason="hash_mismatch",
            stored_value="abc123",
            current_value="def456",
        )
        result = await triage_staleness([item])

        assert len(result) == 1
        assert result[0]["meaningful"] is True
        assert result[0]["skill"] == "test-skill"

    @pytest.mark.anyio
    async def test_triage_staleness_timeout_is_logged(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """When run_managed_async returns TIMED_OUT, a warning log is emitted."""
        from unittest.mock import AsyncMock

        import structlog

        from autoskillit.recipe._triage import triage_staleness
        from autoskillit.execution.process import SubprocessResult, TerminationReason

        skill_dir = tmp_path / "test-skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text("# Test Skill\nDummy content.")

        monkeypatch.setattr("autoskillit.recipe._triage.bundled_skills_dir", lambda: tmp_path)
        monkeypatch.setattr(
            "autoskillit.recipe._triage.run_managed_async",
            AsyncMock(
                return_value=SubprocessResult(
                    returncode=1,
                    stdout="",
                    stderr="",
                    termination=TerminationReason.TIMED_OUT,
                    pid=0,
                )
            ),
        )

        item = StaleItem(
            skill="test-skill",
            reason="hash_mismatch",
            stored_value="abc123",
            current_value="def456",
        )

        with structlog.testing.capture_logs() as logs:
            await triage_staleness([item])

        assert any(log["log_level"] == "warning" for log in logs), (
            "A warning log must be emitted on timeout"
        )
        assert any(
            "triage" in log.get("event", "").lower() or "failed" in log.get("event", "").lower()
            for log in logs
        ), "Log event must mention triage or failed"

    @pytest.mark.anyio
    async def test_triage_staleness_json_decode_error_is_logged(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """On JSONDecodeError, a warning log is emitted and meaningful=True is returned."""
        from unittest.mock import AsyncMock

        import structlog

        from autoskillit.recipe._triage import triage_staleness
        from autoskillit.execution.process import SubprocessResult, TerminationReason

        skill_dir = tmp_path / "test-skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text("# Test Skill\nDummy content.")

        monkeypatch.setattr("autoskillit.recipe._triage.bundled_skills_dir", lambda: tmp_path)
        monkeypatch.setattr(
            "autoskillit.recipe._triage.run_managed_async",
            AsyncMock(
                return_value=SubprocessResult(
                    returncode=0,
                    stdout="not json at all",
                    stderr="",
                    termination=TerminationReason.NATURAL_EXIT,
                    pid=0,
                )
            ),
        )

        item = StaleItem(
            skill="test-skill",
            reason="hash_mismatch",
            stored_value="abc123",
            current_value="def456",
        )

        with structlog.testing.capture_logs() as logs:
            result = await triage_staleness([item])

        assert result[0]["meaningful"] is True
        assert any(log["log_level"] == "warning" for log in logs), (
            "A warning log must be emitted on JSONDecodeError"
        )

    @pytest.mark.anyio
    async def test_triage_staleness_success(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """On success, meaningful and summary are populated from LLM response."""
        import json as _json
        from unittest.mock import AsyncMock

        from autoskillit.recipe._triage import triage_staleness
        from autoskillit.core.types import TerminationReason
        from autoskillit.execution.process import SubprocessResult

        skill_dir = tmp_path / "test-skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text("# Test Skill\nDummy content.")

        _ndjson = "\n".join(
            [
                _json.dumps({"type": "assistant", "message": {"content": []}}),
                _json.dumps(
                    {
                        "type": "result",
                        "subtype": "success",
                        "result": _json.dumps({"meaningful_change": False, "summary": "ok"}),
                        "session_id": "test-session",
                        "is_error": False,
                    }
                ),
            ]
        )

        monkeypatch.setattr("autoskillit.recipe._triage.bundled_skills_dir", lambda: tmp_path)
        monkeypatch.setattr(
            "autoskillit.recipe._triage.run_managed_async",
            AsyncMock(
                return_value=SubprocessResult(
                    returncode=0,
                    stdout=_ndjson,
                    stderr="",
                    termination=TerminationReason.NATURAL_EXIT,
                    pid=0,
                )
            ),
        )

        item = StaleItem(
            skill="test-skill",
            reason="hash_mismatch",
            stored_value="abc123",
            current_value="def456",
        )
        result = await triage_staleness([item])

        assert result[0]["meaningful"] is False
        assert result[0]["summary"] == "ok"

    @pytest.mark.anyio
    async def test_triage_staleness_missing_skill_md_returns_meaningful_true(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """When SKILL.md is absent, returns meaningful=True without spawning a subprocess."""
        from unittest.mock import AsyncMock

        from autoskillit.recipe._triage import triage_staleness

        # Do NOT create SKILL.md — the directory doesn't exist
        monkeypatch.setattr("autoskillit.recipe._triage.bundled_skills_dir", lambda: tmp_path)
        mock_run = AsyncMock()
        monkeypatch.setattr("autoskillit.recipe._triage.run_managed_async", mock_run)

        item = StaleItem(
            skill="test-skill",
            reason="hash_mismatch",
            stored_value="abc123",
            current_value="def456",
        )
        result = await triage_staleness([item])

        assert len(result) == 1
        assert result[0]["meaningful"] is True
        assert "not found" in result[0]["summary"].lower()
        assert not mock_run.called, "run_managed_async must NOT be called when SKILL.md is missing"

    @pytest.mark.anyio
    async def test_triage_staleness_parses_ndjson_result_record(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """triage_staleness must extract result content from NDJSON, not call json.loads on raw stdout."""  # noqa: E501
        import json as _json
        from unittest.mock import AsyncMock

        from autoskillit.recipe._triage import triage_staleness
        from autoskillit.core.types import TerminationReason
        from autoskillit.execution.process import SubprocessResult

        skill_dir = tmp_path / "test-skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text("# Test Skill\nDummy content.")

        agent_response = _json.dumps(
            {"meaningful_change": False, "summary": "only whitespace changes"}
        )
        ndjson = "\n".join(
            [
                _json.dumps(
                    {
                        "type": "assistant",
                        "message": {"content": [{"type": "text", "text": "thinking..."}]},
                    }
                ),
                _json.dumps(
                    {
                        "type": "result",
                        "subtype": "success",
                        "result": agent_response,
                        "session_id": "test-session-123",
                        "is_error": False,
                    }
                ),
            ]
        )

        monkeypatch.setattr("autoskillit.recipe._triage.bundled_skills_dir", lambda: tmp_path)
        monkeypatch.setattr(
            "autoskillit.recipe._triage.run_managed_async",
            AsyncMock(
                return_value=SubprocessResult(
                    returncode=0,
                    stdout=ndjson,
                    stderr="",
                    termination=TerminationReason.NATURAL_EXIT,
                    pid=99999,
                )
            ),
        )

        item = StaleItem(
            skill="test-skill",
            reason="hash_mismatch",
            stored_value="abc123",
            current_value="def456",
        )
        result = await triage_staleness([item])

        assert result[0]["meaningful"] is False, (
            f"triage_staleness must parse NDJSON via parse_session_result, not json.loads. "
            f"Got: {result!r}"
        )
        assert "whitespace" in result[0]["summary"]
