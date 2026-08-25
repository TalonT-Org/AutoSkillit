"""run_skill failure paths, post-serialization validation, cwd validation tests (#4796)."""

from __future__ import annotations

import json

import pytest

from autoskillit.core.types import ChannelConfirmation, RetryReason
from autoskillit.server.tools.tools_execution import run_cmd, run_skill
from tests.conftest import _make_result

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


class TestRunSkillFailurePaths:
    """run_skill surfaces session outcome on failure."""

    @pytest.mark.anyio
    async def test_returns_subtype_on_incomplete_session(self, tool_ctx_kitchen_open):
        """run_skill includes subtype when session didn't finish."""
        stdout = json.dumps(
            {
                "type": "result",
                "subtype": "error_max_turns",
                "is_error": False,
                "session_id": "s1",
            }
        )
        tool_ctx_kitchen_open.runner.push(_make_result(returncode=1))  # clone guard snapshot
        tool_ctx_kitchen_open.runner.push(_make_result(1, stdout, ""))
        result = json.loads(await run_skill("/investigate error", "/tmp"))
        assert result["session_id"] == "s1"
        assert result["subtype"] == "error_max_turns"

    @pytest.mark.anyio
    async def test_returns_is_error_on_context_limit(self, tool_ctx_kitchen_open):
        """run_skill includes is_error when context limit is hit."""
        stdout = json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": True,
                "result": "Prompt is too long",
                "session_id": "s1",
            }
        )
        tool_ctx_kitchen_open.runner.push(_make_result(returncode=1))  # clone guard snapshot
        tool_ctx_kitchen_open.runner.push(_make_result(1, stdout, ""))
        result = json.loads(await run_skill("/investigate error", "/tmp"))
        assert result["is_error"] is True
        assert result["subtype"] == "context_exhausted"
        assert result["cli_subtype"] == "success"

    @pytest.mark.anyio
    async def test_handles_empty_stdout(self, tool_ctx_kitchen_open):
        """run_skill returns error result when stdout is empty."""
        tool_ctx_kitchen_open.runner.push(_make_result(returncode=1))  # clone guard snapshot
        tool_ctx_kitchen_open.runner.push(
            _make_result(1, "", "segfault", channel_confirmation=ChannelConfirmation.UNMONITORED)
        )
        result = json.loads(await run_skill("/investigate error", "/tmp"))
        assert result["exit_code"] == 1
        assert result["is_error"] is True
        assert result["subtype"] == "empty_output"
        assert result["success"] is False

    @pytest.mark.anyio
    async def test_empty_stdout_exit_zero_is_retriable(self, tool_ctx_kitchen_open):
        """Infrastructure failure (empty stdout, exit 0) is retriable with stderr."""
        tool_ctx_kitchen_open.runner.push(_make_result(returncode=1))  # clone guard snapshot
        tool_ctx_kitchen_open.runner.push(
            _make_result(
                0, "", "session dropped", channel_confirmation=ChannelConfirmation.UNMONITORED
            )
        )
        result = json.loads(await run_skill("/investigate error", "/tmp"))
        assert result["subtype"] == "empty_output"
        assert result["success"] is False
        assert result["needs_retry"] is True
        assert result["retry_reason"] == RetryReason.EMPTY_OUTPUT
        assert result["stderr"] == "session dropped"


@pytest.mark.anyio
async def test_run_skill_returns_structured_error_when_executor_raises(
    tool_ctx_kitchen_open, monkeypatch, tmp_path
) -> None:
    """run_skill returns SkillResult-shaped JSON even if executor.run() raises unexpectedly."""
    from autoskillit.core import SkillResult

    class ExplodingExecutor:
        async def run(self, *args, **kwargs) -> SkillResult:
            raise RuntimeError("unexpected executor failure")

    tool_ctx_kitchen_open.executor = ExplodingExecutor()
    monkeypatch.setattr("autoskillit.server._ctx", tool_ctx_kitchen_open)

    from autoskillit.server.tools.tools_execution import run_skill

    result_json = await run_skill("/test cmd", str(tmp_path))
    data = json.loads(result_json)
    assert data["success"] is False
    assert data["subtype"] == "crashed"
    assert data["needs_retry"] is False
    assert "unexpected executor failure" in data["result"]


@pytest.mark.anyio
async def test_run_skill_returns_structured_result_on_cancelled_error(
    tool_ctx_kitchen_open, monkeypatch, tmp_path
) -> None:
    """run_skill returns SkillResult JSON with subtype=cancelled on asyncio.CancelledError.

    This is the primary gap test: CancelledError from executor.run() must produce a
    structured SkillResult (needs_retry=True, subtype=cancelled) rather than escaping
    the tool handler and dropping the MCP transport session.
    """
    import asyncio

    from autoskillit.core import SkillResult

    class CancellingExecutor:
        async def run(self, *args, **kwargs) -> SkillResult:
            raise asyncio.CancelledError()

    tool_ctx_kitchen_open.executor = CancellingExecutor()
    monkeypatch.setattr("autoskillit.server._ctx", tool_ctx_kitchen_open)

    from autoskillit.server.tools.tools_execution import run_skill

    result_json = await run_skill("/test-skill arg", str(tmp_path))
    data = json.loads(result_json)
    assert data["success"] is False
    assert data["subtype"] == "cancelled"
    assert data["needs_retry"] is True
    assert data["retry_reason"] == "cancelled"
    assert "cancelled" in data["result"].lower()


@pytest.mark.anyio
async def test_run_skill_aborts_completion_when_base_exception_escapes(
    tool_ctx_kitchen_open, monkeypatch, tmp_path
) -> None:
    from autoskillit.core import SkillResult

    class Sentinel(BaseException):
        pass

    sentinel = Sentinel()

    class EscapingExecutor:
        async def run(self, *args, **kwargs) -> SkillResult:
            raise sentinel

    tool_ctx_kitchen_open.executor = EscapingExecutor()
    monkeypatch.setattr("autoskillit.server._ctx", tool_ctx_kitchen_open)

    with pytest.raises(Sentinel) as raised:
        await run_skill("/test cmd", str(tmp_path))

    assert raised.value is sentinel
    assert tool_ctx_kitchen_open.run_skill_completion.admission("kitchen_status") == (
        True,
        "idle",
    )


class TestRunSkillPostSerializationValidation:
    """Post-serialization validation: run_skill must catch degraded SkillResult payloads."""

    @pytest.mark.anyio
    async def test_missing_keys_returns_failure_envelope(
        self, tool_ctx_kitchen_open, monkeypatch, tmp_path
    ) -> None:
        """to_json() omits 'success' and 'exit_code' → run_skill returns retriable envelope."""
        from autoskillit.core import SkillResult
        from autoskillit.core.types import RetryReason

        degraded = SkillResult(
            success=True,
            result="ok",
            session_id="sess-degraded",
            subtype="success",
            is_error=False,
            exit_code=0,
            needs_retry=False,
            retry_reason=RetryReason.NONE,
            stderr="",
        )
        # Shadow to_json to omit both required keys.
        degraded.to_json = lambda: '{"result": "ok"}'  # type: ignore[method-assign]

        class DegradedExecutor:
            async def run(self, *args, **kwargs) -> SkillResult:
                return degraded

        tool_ctx_kitchen_open.executor = DegradedExecutor()
        monkeypatch.setattr("autoskillit.server._ctx", tool_ctx_kitchen_open)

        result_json = await run_skill("/test cmd", str(tmp_path))
        data = json.loads(result_json)
        assert data["success"] is False
        assert data["retriable"] is True
        assert "validate_result:run_skill" in data["stage"]
        _msg = data["error"].lower()
        assert "missing" in _msg or "success" in _msg or "exit_code" in _msg

    @pytest.mark.anyio
    async def test_missing_exit_code_returns_failure_envelope(
        self, tool_ctx_kitchen_open, monkeypatch, tmp_path
    ) -> None:
        """to_json() has 'success' but omits 'exit_code' → run_skill returns retriable envelope."""
        from autoskillit.core import SkillResult
        from autoskillit.core.types import RetryReason

        degraded = SkillResult(
            success=True,
            result="ok",
            session_id="sess-degraded",
            subtype="success",
            is_error=False,
            exit_code=0,
            needs_retry=False,
            retry_reason=RetryReason.NONE,
            stderr="",
        )
        # Shadow to_json to include success but omit exit_code.
        degraded.to_json = lambda: '{"success": true, "result": "ok"}'  # type: ignore[method-assign]

        class DegradedExecutor:
            async def run(self, *args, **kwargs) -> SkillResult:
                return degraded

        tool_ctx_kitchen_open.executor = DegradedExecutor()
        monkeypatch.setattr("autoskillit.server._ctx", tool_ctx_kitchen_open)

        result_json = await run_skill("/test cmd", str(tmp_path))
        data = json.loads(result_json)
        assert data["success"] is False
        assert data["retriable"] is True
        assert "missing" in data["error"].lower() and "exit_code" in data["error"]

    @pytest.mark.anyio
    async def test_valid_to_json_passes_through_unchanged(
        self, tool_ctx_kitchen_open, monkeypatch, tmp_path
    ) -> None:
        """A valid result keeps its fields and gains an opaque receipt."""
        from autoskillit.core import SkillResult
        from autoskillit.core.types import RetryReason

        valid = SkillResult(
            success=True,
            result="ok",
            session_id="sess-valid",
            subtype="success",
            is_error=False,
            exit_code=0,
            needs_retry=False,
            retry_reason=RetryReason.NONE,
            stderr="",
        )
        # Do NOT shadow to_json — use the default implementation.

        class ValidExecutor:
            async def run(self, *args, **kwargs) -> SkillResult:
                return valid

        tool_ctx_kitchen_open.executor = ValidExecutor()
        monkeypatch.setattr("autoskillit.server._ctx", tool_ctx_kitchen_open)

        result_json = await run_skill("/test cmd", str(tmp_path))
        data = json.loads(result_json)
        assert data["success"] is True
        assert data["exit_code"] == 0
        assert "retriable" not in data  # No envelope wrapping
        assert data["subtype"] == "success"
        assert isinstance(data["receipt_id"], str)
        assert data["receipt_id"]


class TestCwdExistenceValidation:
    """run_skill and run_cmd reject non-existent cwd before reaching executor/subprocess."""

    @pytest.mark.anyio
    async def test_run_skill_rejects_nonexistent_cwd(self, tool_ctx_kitchen_open, tmp_path):
        nonexistent = str(tmp_path / "nonexistent_subdir")
        result = json.loads(await run_skill("/investigate foo", nonexistent))
        assert result["success"] is False
        assert "does not exist" in result["error"]

    @pytest.mark.anyio
    async def test_run_cmd_rejects_nonexistent_cwd(self, tool_ctx_kitchen_open, tmp_path):
        nonexistent = str(tmp_path / "nonexistent_subdir")
        result = json.loads(await run_cmd("echo hi", nonexistent))
        assert result["success"] is False
        assert "does not exist" in result.get("error", result.get("stderr", ""))
