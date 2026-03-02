"""Tests for _llm_triage module."""

from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit.recipe.contracts import StaleItem

# ---------------------------------------------------------------------------
# T-P3-4-B: Module importable
# ---------------------------------------------------------------------------


def test_llm_triage_module_importable():
    """_llm_triage module must exist and export triage_staleness."""
    from autoskillit._llm_triage import triage_staleness  # noqa: F401


# ---------------------------------------------------------------------------
# T-P1-7-A: SKILL.md cached per unique skill
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_triage_staleness_reads_skill_md_once_per_unique_skill(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """SKILL.md is read at most once per unique skill name per triage_staleness call."""
    from unittest.mock import AsyncMock

    from autoskillit._llm_triage import triage_staleness
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

    @pytest.mark.anyio
    async def test_triage_staleness_timeout_returns_meaningful_true(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """When run_managed_async returns TIMED_OUT, result is meaningful=True."""
        from unittest.mock import AsyncMock

        from autoskillit._llm_triage import triage_staleness
        from autoskillit.execution.process import SubprocessResult, TerminationReason

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

    @pytest.mark.anyio
    async def test_triage_staleness_timeout_is_logged(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """When run_managed_async returns TIMED_OUT, a warning log is emitted."""
        from unittest.mock import AsyncMock

        import structlog

        from autoskillit._llm_triage import triage_staleness
        from autoskillit.execution.process import SubprocessResult, TerminationReason

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

    @pytest.mark.anyio
    async def test_triage_staleness_json_decode_error_is_logged(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """On JSONDecodeError, a warning log is emitted and meaningful=True is returned."""
        from unittest.mock import AsyncMock

        import structlog

        from autoskillit._llm_triage import triage_staleness
        from autoskillit.execution.process import SubprocessResult, TerminationReason

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

    @pytest.mark.anyio
    async def test_triage_staleness_success(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """On success, meaningful and summary are populated from LLM response."""
        import json as _json
        from unittest.mock import AsyncMock

        from autoskillit._llm_triage import triage_staleness
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
                        "result": _json.dumps(
                            [{"index": 1, "skill": "test-skill", "meaningful_change": False, "summary": "ok"}]
                        ),
                        "session_id": "test-session",
                        "is_error": False,
                    }
                ),
            ]
        )

        monkeypatch.setattr("autoskillit._llm_triage.bundled_skills_dir", lambda: tmp_path)
        monkeypatch.setattr(
            "autoskillit._llm_triage.run_managed_async",
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

    @pytest.mark.anyio
    async def test_triage_staleness_parses_ndjson_result_record(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """triage_staleness must extract result content from NDJSON, not call json.loads on raw stdout."""  # noqa: E501
        import json as _json
        from unittest.mock import AsyncMock

        from autoskillit._llm_triage import triage_staleness
        from autoskillit.core.types import TerminationReason
        from autoskillit.execution.process import SubprocessResult

        skill_dir = tmp_path / "test-skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text("# Test Skill\nDummy content.")

        agent_response = _json.dumps(
            [{"index": 1, "skill": "test-skill", "meaningful_change": False, "summary": "only whitespace changes"}]
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

        monkeypatch.setattr("autoskillit._llm_triage.bundled_skills_dir", lambda: tmp_path)
        monkeypatch.setattr(
            "autoskillit._llm_triage.run_managed_async",
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


# ---------------------------------------------------------------------------
# T4: Concurrent batch calls via anyio task group
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_triage_staleness_concurrent_calls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Multiple batches are dispatched concurrently via anyio task group."""
    import asyncio
    import json as _json
    from unittest.mock import AsyncMock

    from autoskillit._llm_triage import BATCH_SIZE, triage_staleness
    from autoskillit.core.types import TerminationReason
    from autoskillit.execution.process import SubprocessResult

    # Create enough skills to produce 2 batches (BATCH_SIZE + 1 skills).
    n_skills = BATCH_SIZE + 1
    for i in range(n_skills):
        skill_dir = tmp_path / f"skill-{i}"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(f"# Skill {i}\nContent.")

    monkeypatch.setattr("autoskillit._llm_triage.bundled_skills_dir", lambda: tmp_path)

    concurrent_peak = 0
    active_calls = 0

    async def slow_mock(**_: object) -> SubprocessResult:
        nonlocal concurrent_peak, active_calls
        active_calls += 1
        concurrent_peak = max(concurrent_peak, active_calls)
        await asyncio.sleep(0.005)  # yield so other tasks can start
        active_calls -= 1
        # Return a valid batch response for however many skills are in this batch
        return SubprocessResult(
            returncode=0,
            stdout="",  # parse_session_result will fail → meaningful=True (acceptable)
            stderr="",
            termination=TerminationReason.NATURAL_EXIT,
            pid=0,
        )

    monkeypatch.setattr(
        "autoskillit._llm_triage.run_managed_async",
        slow_mock,
    )

    items = [
        StaleItem(
            skill=f"skill-{i}",
            reason="hash_mismatch",
            stored_value="old",
            current_value="new",
        )
        for i in range(n_skills)
    ]

    results = await triage_staleness(items)

    assert len(results) == n_skills
    assert concurrent_peak == 2, (
        f"Expected peak concurrency of 2 (two batches concurrent), got {concurrent_peak}"
    )


# ---------------------------------------------------------------------------
# T5: 5 skills produce exactly 1 run_managed_async call
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_triage_staleness_batches_five_skills(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """5 hash_mismatch items produce exactly 1 run_managed_async call (one full batch)."""
    import json as _json
    from unittest.mock import AsyncMock

    from autoskillit._llm_triage import triage_staleness
    from autoskillit.core.types import TerminationReason
    from autoskillit.execution.process import SubprocessResult

    n = 5
    for i in range(n):
        skill_dir = tmp_path / f"skill-{i}"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(f"# Skill {i}\nContent.")

    monkeypatch.setattr("autoskillit._llm_triage.bundled_skills_dir", lambda: tmp_path)

    batch_response = _json.dumps(
        [
            {"index": i + 1, "skill": f"skill-{i}", "meaningful_change": False, "summary": "ok"}
            for i in range(n)
        ]
    )
    ndjson = _json.dumps(
        {
            "type": "result",
            "subtype": "success",
            "result": batch_response,
            "session_id": "test",
            "is_error": False,
        }
    )
    mock_run = AsyncMock(
        return_value=SubprocessResult(
            returncode=0,
            stdout=ndjson,
            stderr="",
            termination=TerminationReason.NATURAL_EXIT,
            pid=0,
        )
    )
    monkeypatch.setattr("autoskillit._llm_triage.run_managed_async", mock_run)

    items = [
        StaleItem(skill=f"skill-{i}", reason="hash_mismatch", stored_value="old", current_value="new")
        for i in range(n)
    ]
    results = await triage_staleness(items)

    assert mock_run.call_count == 1, (
        f"Expected 1 run_managed_async call for 5 skills (1 batch), got {mock_run.call_count}"
    )
    assert len(results) == n
    assert all(r["meaningful"] is False for r in results)


# ---------------------------------------------------------------------------
# T6: Malformed batch response → all skills meaningful=True (fallback)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_triage_staleness_batch_fallback_on_malformed_response(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When batch response array has wrong length, all skills in batch → meaningful=True."""
    import json as _json
    from unittest.mock import AsyncMock

    from autoskillit._llm_triage import triage_staleness
    from autoskillit.core.types import TerminationReason
    from autoskillit.execution.process import SubprocessResult

    n = 3
    for i in range(n):
        skill_dir = tmp_path / f"skill-{i}"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(f"# Skill {i}\nContent.")

    monkeypatch.setattr("autoskillit._llm_triage.bundled_skills_dir", lambda: tmp_path)

    # Return an array of length 2 for a batch of 3 → length mismatch → fallback
    truncated_response = _json.dumps(
        [
            {"index": 1, "skill": "skill-0", "meaningful_change": False, "summary": "ok"},
            {"index": 2, "skill": "skill-1", "meaningful_change": False, "summary": "ok"},
        ]
    )
    ndjson = _json.dumps(
        {
            "type": "result",
            "subtype": "success",
            "result": truncated_response,
            "session_id": "test",
            "is_error": False,
        }
    )
    mock_run = AsyncMock(
        return_value=SubprocessResult(
            returncode=0,
            stdout=ndjson,
            stderr="",
            termination=TerminationReason.NATURAL_EXIT,
            pid=0,
        )
    )
    monkeypatch.setattr("autoskillit._llm_triage.run_managed_async", mock_run)

    items = [
        StaleItem(skill=f"skill-{i}", reason="hash_mismatch", stored_value="old", current_value="new")
        for i in range(n)
    ]
    results = await triage_staleness(items)

    assert len(results) == n
    assert all(r["meaningful"] is True for r in results), (
        f"All skills must be meaningful=True on length mismatch. Got: {results!r}"
    )
