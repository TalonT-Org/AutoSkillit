"""Tests for _llm_triage module."""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from autoskillit.recipe_validator import StaleItem

# ---------------------------------------------------------------------------
# T-P3-4-B: Module importable
# ---------------------------------------------------------------------------


def test_llm_triage_module_importable():
    """_llm_triage module must exist and export triage_staleness."""
    import asyncio

    from autoskillit._llm_triage import triage_staleness

    assert asyncio.iscoroutinefunction(triage_staleness)


# ---------------------------------------------------------------------------
# T-P7-3-A / T-P6-6-A: Structural source assertions
# ---------------------------------------------------------------------------


def test_triage_staleness_uses_run_managed_async():
    """triage_staleness must call run_managed_async, not asyncio.create_subprocess_exec."""
    from autoskillit import _llm_triage

    src = inspect.getsource(_llm_triage)
    assert "run_managed_async" in src
    assert "create_subprocess_exec" not in src


def test_triage_staleness_no_raw_proc_kill():
    """proc.kill() must not appear in _llm_triage; run_managed_async handles cleanup."""
    from autoskillit import _llm_triage

    src = inspect.getsource(_llm_triage)
    assert "proc.kill()" not in src


# ---------------------------------------------------------------------------
# T-P1-7-A: SKILL.md cached per unique skill
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_triage_staleness_reads_skill_md_once_per_unique_skill(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """SKILL.md is read at most once per unique skill name per triage_staleness call."""
    from unittest.mock import AsyncMock

    from autoskillit._llm_triage import triage_staleness
    from autoskillit.process_lifecycle import SubprocessResult, TerminationReason

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
    monkeypatch.setattr("autoskillit._llm_triage.bundled_skills_dir", lambda: tmp_path)

    fake_result = SubprocessResult(
        returncode=0,
        stdout='{"meaningful_change": false, "summary": "no change"}',
        stderr="",
        termination=TerminationReason.NATURAL_EXIT,
        pid=0,
    )
    monkeypatch.setattr(
        "autoskillit._llm_triage.run_managed_async",
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

    @pytest.mark.asyncio
    async def test_triage_staleness_timeout_returns_meaningful_true(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """When run_managed_async returns TIMED_OUT, result is meaningful=True."""
        from unittest.mock import AsyncMock

        from autoskillit._llm_triage import triage_staleness
        from autoskillit.process_lifecycle import SubprocessResult, TerminationReason

        skill_dir = tmp_path / "test-skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text("# Test Skill\nDummy content.")

        monkeypatch.setattr("autoskillit._llm_triage.bundled_skills_dir", lambda: tmp_path)
        monkeypatch.setattr(
            "autoskillit._llm_triage.run_managed_async",
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

    @pytest.mark.asyncio
    async def test_triage_staleness_timeout_is_logged(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """When run_managed_async returns TIMED_OUT, a warning log is emitted."""
        from unittest.mock import AsyncMock

        import structlog

        from autoskillit._llm_triage import triage_staleness
        from autoskillit.process_lifecycle import SubprocessResult, TerminationReason

        skill_dir = tmp_path / "test-skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text("# Test Skill\nDummy content.")

        monkeypatch.setattr("autoskillit._llm_triage.bundled_skills_dir", lambda: tmp_path)
        monkeypatch.setattr(
            "autoskillit._llm_triage.run_managed_async",
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

    @pytest.mark.asyncio
    async def test_triage_staleness_json_decode_error_is_logged(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """On JSONDecodeError, a warning log is emitted and meaningful=True is returned."""
        from unittest.mock import AsyncMock

        import structlog

        from autoskillit._llm_triage import triage_staleness
        from autoskillit.process_lifecycle import SubprocessResult, TerminationReason

        skill_dir = tmp_path / "test-skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text("# Test Skill\nDummy content.")

        monkeypatch.setattr("autoskillit._llm_triage.bundled_skills_dir", lambda: tmp_path)
        monkeypatch.setattr(
            "autoskillit._llm_triage.run_managed_async",
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

    @pytest.mark.asyncio
    async def test_triage_staleness_success(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """On success, meaningful and summary are populated from LLM response."""
        from unittest.mock import AsyncMock

        from autoskillit._llm_triage import triage_staleness
        from autoskillit.process_lifecycle import SubprocessResult, TerminationReason

        skill_dir = tmp_path / "test-skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text("# Test Skill\nDummy content.")

        monkeypatch.setattr("autoskillit._llm_triage.bundled_skills_dir", lambda: tmp_path)
        monkeypatch.setattr(
            "autoskillit._llm_triage.run_managed_async",
            AsyncMock(
                return_value=SubprocessResult(
                    returncode=0,
                    stdout='{"meaningful_change": false, "summary": "ok"}',
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

    @pytest.mark.asyncio
    async def test_triage_staleness_missing_skill_md_returns_meaningful_true(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """When SKILL.md is absent, returns meaningful=True without spawning a subprocess."""
        from unittest.mock import AsyncMock

        from autoskillit._llm_triage import triage_staleness

        # Do NOT create SKILL.md — the directory doesn't exist
        monkeypatch.setattr("autoskillit._llm_triage.bundled_skills_dir", lambda: tmp_path)
        mock_run = AsyncMock()
        monkeypatch.setattr("autoskillit._llm_triage.run_managed_async", mock_run)

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
