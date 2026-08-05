"""Tests for _llm_triage module."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from autoskillit.execution.backends import ClaudeCodeBackend
from autoskillit.recipe.contracts import StaleItem

pytestmark = [pytest.mark.medium]


def _patch_skill_resolver(
    monkeypatch: pytest.MonkeyPatch,
    bundled_root: Path,
) -> None:
    from autoskillit.workspace.skills import DefaultSkillResolver

    resolver = DefaultSkillResolver()
    resolver._dir = bundled_root
    resolver._extended_dir = bundled_root / "missing-extended"
    monkeypatch.setattr("autoskillit._llm_triage.default_skill_resolver", lambda: resolver)


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
    _patch_skill_resolver(monkeypatch, tmp_path)

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
    await triage_staleness(items, project_root=tmp_path, backend=ClaudeCodeBackend())
    assert len(read_calls) == 1, (
        f"SKILL.md read {len(read_calls)} times; expected exactly 1 (cache hit on second item)"
    )


@pytest.mark.anyio
async def test_triage_staleness_projects_the_project_effective_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from unittest.mock import AsyncMock

    from autoskillit._llm_triage import triage_staleness
    from autoskillit.core import SubprocessResult, TerminationReason

    project_root = tmp_path / "project"
    skill_dir = project_root / ".claude" / "skills" / "implement-worktree"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: implement-worktree\n"
        "description: Project-effective triage override.\n"
        "---\n"
        "PROJECT-EFFECTIVE-TRIAGE-CONTENT\n"
    )
    response = json.dumps(
        [
            {
                "index": 1,
                "skill": "implement-worktree",
                "meaningful_change": False,
                "summary": "project override observed",
            }
        ]
    )
    mock_run = AsyncMock(
        return_value=SubprocessResult(
            returncode=0,
            stdout=(
                json.dumps(
                    {
                        "type": "result",
                        "subtype": "success",
                        "is_error": False,
                        "result": response,
                        "session_id": "triage-project-override",
                    }
                )
                + "\n"
            ),
            stderr="",
            termination=TerminationReason.NATURAL_EXIT,
            pid=1,
        )
    )
    monkeypatch.setattr("autoskillit._llm_triage.run_managed_async", mock_run)

    await triage_staleness(
        [
            StaleItem(
                skill="implement-worktree",
                reason="hash_mismatch",
                stored_value="old",
                current_value="new",
            )
        ],
        project_root=project_root,
        backend=ClaudeCodeBackend(),
    )

    command = mock_run.call_args.kwargs["cmd"]
    assert "PROJECT-EFFECTIVE-TRIAGE-CONTENT" in command[2]
    assert mock_run.call_args.kwargs["cwd"] == project_root.resolve()


async def test_triage_staleness_sees_raw_stale_local_copy_not_bundled_twin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T14: an invalid project-local copy shadowing a valid bundled twin —
    triage still receives the raw, stale project-local content via
    resolve_local_candidate, not the bundled twin resolve_effective's
    fall-through (2.2) would otherwise silently substitute."""
    from unittest.mock import AsyncMock

    from autoskillit._llm_triage import triage_staleness
    from autoskillit.core import SubprocessResult, TerminationReason

    project_root = tmp_path / "project"
    skill_dir = project_root / ".claude" / "skills" / "implement-worktree"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("STALE-PROJECT-LOCAL-CONTENT-NO-FRONTMATTER\n")

    response = json.dumps(
        [
            {
                "index": 1,
                "skill": "implement-worktree",
                "meaningful_change": True,
                "summary": "stale local copy observed",
            }
        ]
    )
    mock_run = AsyncMock(
        return_value=SubprocessResult(
            returncode=0,
            stdout=(
                json.dumps(
                    {
                        "type": "result",
                        "subtype": "success",
                        "is_error": False,
                        "result": response,
                        "session_id": "triage-stale-local",
                    }
                )
                + "\n"
            ),
            stderr="",
            termination=TerminationReason.NATURAL_EXIT,
            pid=1,
        )
    )
    monkeypatch.setattr("autoskillit._llm_triage.run_managed_async", mock_run)

    await triage_staleness(
        [
            StaleItem(
                skill="implement-worktree",
                reason="hash_mismatch",
                stored_value="old",
                current_value="new",
            )
        ],
        project_root=project_root,
        backend=ClaudeCodeBackend(),
    )

    command = mock_run.call_args.kwargs["cmd"]
    assert "STALE-PROJECT-LOCAL-CONTENT-NO-FRONTMATTER" in command[2]


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

        _patch_skill_resolver(monkeypatch, tmp_path)
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
        result = await triage_staleness([item], project_root=tmp_path, backend=ClaudeCodeBackend())

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

        _patch_skill_resolver(monkeypatch, tmp_path)
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
            await triage_staleness([item], project_root=tmp_path, backend=ClaudeCodeBackend())

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

        _patch_skill_resolver(monkeypatch, tmp_path)
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
            result = await triage_staleness(
                [item], project_root=tmp_path, backend=ClaudeCodeBackend()
            )

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
                            [
                                {
                                    "index": 1,
                                    "skill": "test-skill",
                                    "meaningful_change": False,
                                    "summary": "ok",
                                }
                            ]
                        ),
                        "session_id": "test-session",
                        "is_error": False,
                    }
                ),
            ]
        )

        _patch_skill_resolver(monkeypatch, tmp_path)
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
        result = await triage_staleness([item], project_root=tmp_path, backend=ClaudeCodeBackend())

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
        _patch_skill_resolver(monkeypatch, tmp_path)
        mock_run = AsyncMock()
        monkeypatch.setattr("autoskillit._llm_triage.run_managed_async", mock_run)

        item = StaleItem(
            skill="test-skill",
            reason="hash_mismatch",
            stored_value="abc123",
            current_value="def456",
        )
        result = await triage_staleness([item], project_root=tmp_path, backend=ClaudeCodeBackend())

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
            [
                {
                    "index": 1,
                    "skill": "test-skill",
                    "meaningful_change": False,
                    "summary": "only whitespace changes",
                }
            ]
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

        _patch_skill_resolver(monkeypatch, tmp_path)
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
        result = await triage_staleness([item], project_root=tmp_path, backend=ClaudeCodeBackend())

        assert result[0]["meaningful"] is False, (
            f"triage_staleness must parse NDJSON via parse_session_result, not json.loads. "
            f"Got: {result!r}"
        )
        assert "whitespace" in result[0]["summary"]


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

    _patch_skill_resolver(monkeypatch, tmp_path)

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
        StaleItem(
            skill=f"skill-{i}", reason="hash_mismatch", stored_value="old", current_value="new"
        )
        for i in range(n)
    ]
    results = await triage_staleness(items, project_root=tmp_path, backend=ClaudeCodeBackend())

    assert len(results) == n
    assert all(r["meaningful"] is True for r in results), (
        f"All skills must be meaningful=True on length mismatch. Got: {results!r}"
    )


# ---------------------------------------------------------------------------
# CLI flag co-dependency: triage command respects OutputFormat.required_cli_flags
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_triage_command_includes_format_required_flags(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """The triage command must include all flags required by its OutputFormat."""
    from unittest.mock import AsyncMock

    from autoskillit._llm_triage import triage_staleness
    from autoskillit.core.types import OutputFormat, SubprocessResult, TerminationReason

    skill_dir = tmp_path / "test-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("# test skill")
    _patch_skill_resolver(monkeypatch, tmp_path)

    result_payload = json.dumps([{"skill": "test-skill", "meaningful": False, "summary": "ok"}])
    ndjson = (
        json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": result_payload,
                "session_id": "s1",
            }
        )
        + "\n"
    )
    mock_run = AsyncMock(
        return_value=SubprocessResult(0, ndjson, "", TerminationReason.NATURAL_EXIT, pid=1)
    )
    monkeypatch.setattr("autoskillit._llm_triage.run_managed_async", mock_run)

    item = StaleItem(
        skill="test-skill", reason="hash_mismatch", stored_value="old", current_value="new"
    )
    await triage_staleness([item], project_root=tmp_path, backend=ClaudeCodeBackend())

    # Verify the command passed to run_managed_async includes format-required flags
    cmd = mock_run.call_args.kwargs["cmd"]
    fmt = OutputFormat.JSON
    for flag in fmt.required_cli_flags:
        assert flag in cmd, f"Missing required flag {flag!r} in triage command: {cmd}"


@pytest.mark.anyio
async def test_triage_env_excludes_ide_vars(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """triage_staleness must route env through build_agent_env() — no IDE leak."""
    from unittest.mock import AsyncMock

    from autoskillit._llm_triage import triage_staleness
    from autoskillit.execution.process import SubprocessResult, TerminationReason

    monkeypatch.setenv("CLAUDE_CODE_SSE_PORT", "23270")
    monkeypatch.setenv("ENABLE_IDE_INTEGRATION", "true")

    skill_dir = tmp_path / "test-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("# Test\nContent.")
    _patch_skill_resolver(monkeypatch, tmp_path)

    ndjson = (
        json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": json.dumps(
                    [
                        {
                            "index": 1,
                            "skill": "test-skill",
                            "meaningful_change": False,
                            "summary": "",
                        }
                    ]
                ),
                "session_id": "s1",
            }
        )
        + "\n"
    )
    mock_run = AsyncMock(
        return_value=SubprocessResult(0, ndjson, "", TerminationReason.NATURAL_EXIT, pid=1)
    )
    monkeypatch.setattr("autoskillit._llm_triage.run_managed_async", mock_run)

    item = StaleItem(
        skill="test-skill", reason="hash_mismatch", stored_value="old", current_value="new"
    )
    await triage_staleness([item], project_root=tmp_path, backend=ClaudeCodeBackend())

    env = mock_run.call_args.kwargs["env"]
    assert env is not None
    assert "CLAUDE_CODE_SSE_PORT" not in env
    assert "ENABLE_IDE_INTEGRATION" not in env
    assert env["CLAUDE_CODE_AUTO_CONNECT_IDE"] == "0"


# ---------------------------------------------------------------------------
# T-P1-A6-WP1: Binary name must come from backend abstraction
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_triage_command_uses_backend_binary_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """The triage command first element must equal ClaudeCodeBackend().binary_name()."""
    from unittest.mock import AsyncMock

    from autoskillit._llm_triage import triage_staleness
    from autoskillit.execution.process import SubprocessResult, TerminationReason

    skill_dir = tmp_path / "test-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("# test skill")
    _patch_skill_resolver(monkeypatch, tmp_path)

    ndjson = (
        __import__("json").dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": __import__("json").dumps(
                    [
                        {
                            "index": 1,
                            "skill": "test-skill",
                            "meaningful_change": False,
                            "summary": "ok",
                        }
                    ]
                ),
                "session_id": "s1",
            }
        )
        + "\n"
    )
    mock_run = AsyncMock(
        return_value=SubprocessResult(0, ndjson, "", TerminationReason.NATURAL_EXIT, pid=1)
    )
    monkeypatch.setattr("autoskillit._llm_triage.run_managed_async", mock_run)

    item = StaleItem(
        skill="test-skill", reason="hash_mismatch", stored_value="old", current_value="new"
    )
    await triage_staleness([item], project_root=tmp_path, backend=ClaudeCodeBackend())

    cmd = mock_run.call_args.kwargs["cmd"]
    expected_binary = ClaudeCodeBackend().binary_name()
    assert cmd[0] == expected_binary, (
        f"triage_cmd[0] must equal get_backend().binary_name(), got {cmd[0]!r}"
    )


@pytest.mark.anyio
async def test_triage_staleness_does_not_use_pty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """triage_staleness calls run_managed_async with pty_mode=False."""
    from unittest.mock import AsyncMock

    from autoskillit._llm_triage import triage_staleness
    from autoskillit.core.types import SubprocessResult, TerminationReason
    from autoskillit.recipe.contracts import StaleItem

    skill_dir = tmp_path / "test-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("# test skill")
    _patch_skill_resolver(monkeypatch, tmp_path)

    ndjson = (
        json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": json.dumps(
                    [
                        {
                            "index": 1,
                            "skill": "test-skill",
                            "meaningful_change": False,
                            "summary": "ok",
                        }
                    ]
                ),
                "session_id": "s1",
            }
        )
        + "\n"
    )
    mock_run = AsyncMock(
        return_value=SubprocessResult(0, ndjson, "", TerminationReason.NATURAL_EXIT, pid=1)
    )
    monkeypatch.setattr("autoskillit._llm_triage.run_managed_async", mock_run)

    item = StaleItem(
        skill="test-skill", reason="hash_mismatch", stored_value="old", current_value="new"
    )
    await triage_staleness([item], project_root=tmp_path, backend=ClaudeCodeBackend())

    assert mock_run.called
    pty_mode = mock_run.call_args.kwargs.get("pty_mode")
    assert pty_mode is False, (
        f"triage_staleness must call run_managed_async with pty_mode=False, got {pty_mode!r}"
    )
