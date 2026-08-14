"""Tests for run_skill command building, timeouts, env, model, and per-invocation markers."""

from __future__ import annotations

import contextlib
import json
import os
from pathlib import Path
from unittest.mock import Mock

import pytest

from autoskillit.config import (
    AutomationConfig,
    RunSkillConfig,
)
from autoskillit.core import AUDIT_ADMISSION_AUTHORITY_PATH_ENV_VAR
from autoskillit.core.claude_conventions import ClaudeDirectoryConventions
from autoskillit.core.types._type_backend import CLAUDE_MODEL_ALIASES
from autoskillit.execution.commands import _inject_completion_directive
from autoskillit.server.tools.tools_execution import run_skill
from tests.conftest import _make_result
from tests.server._pipeline_test_helpers import _ack_direct_run_skill_result
from tests.server.conftest import _SUCCESS_JSON

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


@pytest.mark.anyio
async def test_attested_dispatch_injects_parent_audit_authority_without_retargeting_clone(
    tool_ctx_ready_recipe, tmp_path, monkeypatch
) -> None:
    from tests.server._pipeline_test_helpers import _write_tracker

    ready = tool_ctx_ready_recipe
    with_args = ready.with_args
    work_dir = tmp_path / "clone"
    work_dir.mkdir()
    _write_tracker(
        ready.tool_ctx.project_dir,
        "AB",
        {with_args["step_name"]: {"status": "pending"}},
        {},
        kitchen_id=ready.tool_ctx.kitchen_id,
    )
    monkeypatch.setattr(
        "autoskillit.server.tools.tools_execution.is_feature_enabled",
        lambda *args, **kwargs: True,
    )
    monkeypatch.setattr(
        "autoskillit.server._guards._resolve_provider_profile",
        lambda *args, **kwargs: (
            "bedrock",
            {AUDIT_ADMISSION_AUTHORITY_PATH_ENV_VAR: ("/hostile/provider/ledger.sqlite3")},
        ),
    )
    ready.tool_ctx.runner.push(_make_result(returncode=1))
    ready.tool_ctx.runner.push(
        _make_result(
            0,
            json.dumps(
                {
                    "type": "result",
                    "subtype": "success",
                    "is_error": False,
                    "result": "done",
                    "session_id": "session-1",
                }
            ),
            "",
        )
    )

    await run_skill(
        with_args["skill_command"],
        str(work_dir),
        step_name=with_args["step_name"],
        output_dir=with_args["output_dir"],
        recipe_execution_id=ready.credential["execution_id"],
        invocation_template_digest=ready.template_digest,
        skill_inputs={name: "probe value" for name in with_args["skill_inputs"]},
    )

    _cmd, process_cwd, _timeout, kwargs = ready.tool_ctx.runner.call_args_list[-1]
    env = kwargs["env"]
    expected_authority_path = str(
        ready.tool_ctx.audit_admission_ledger.store_authority.database_path
    )
    assert process_cwd == work_dir.resolve()
    assert env["AUTOSKILLIT_CWD"] == str(work_dir)
    assert env["AUTOSKILLIT_STATE_ROOT"] == str(work_dir)
    assert env[AUDIT_ADMISSION_AUTHORITY_PATH_ENV_VAR] == expected_authority_path


class TestRunSkillPluginDir:
    """T2: run_skill passes --plugin-dir to the claude command."""

    @pytest.mark.anyio
    async def test_run_skill_passes_plugin_dir(self, tool_ctx_kitchen_open, monkeypatch):
        """T2: run_skill passes --plugin-dir to the claude command."""
        from tests.fakes import FakePluginArtifactAuthority

        authority = tool_ctx_kitchen_open.plugin_authority
        assert isinstance(authority, FakePluginArtifactAuthority)
        runner = tool_ctx_kitchen_open.runner
        original_call = type(runner).__call__
        observed_live_binding = False

        async def assert_live_binding(self, *args, **kwargs):
            nonlocal observed_live_binding
            if self is runner and authority.bindings:
                assert authority.bindings[-1].closed is False
                observed_live_binding = True
            return await original_call(self, *args, **kwargs)

        monkeypatch.setattr(type(runner), "__call__", assert_live_binding)
        tool_ctx_kitchen_open.runner.push(_make_result(returncode=1))  # clone guard snapshot
        tool_ctx_kitchen_open.runner.push(
            _make_result(
                0,
                '{"type": "result", "subtype": "success", "is_error": false,'
                ' "result": "done", "session_id": "s1"}',
                "",
            )
        )
        await run_skill("/investigate some-error", "/tmp")

        cmd = tool_ctx_kitchen_open.runner.call_args_list[-1][0]
        assert "--plugin-dir" in cmd
        plugin_dir_idx = cmd.index("--plugin-dir")
        assert authority.bindings
        binding = authority.bindings[-1]
        assert binding.plugin_dir is not None
        assert cmd[plugin_dir_idx + 1] == str(binding.plugin_dir)
        assert observed_live_binding
        assert binding.closed
        assert "--output-format" in cmd
        assert cmd[cmd.index("--output-format") + 1] == "stream-json"
        actual_cwd = tool_ctx_kitchen_open.runner.call_args_list[-1][1]
        assert actual_cwd == Path("/tmp").resolve(), (
            f"Subprocess cwd mismatch: {actual_cwd} != {Path('/tmp').resolve()}"
        )


class TestRunSkillTimeoutFromConfig:
    """run_skill uses configurable timeouts."""

    @pytest.mark.anyio
    async def test_run_skill_timeout_from_config(self, tool_ctx_kitchen_open):
        """run_skill uses _config.run_skill.timeout instead of hardcoded value."""
        cfg = AutomationConfig()
        cfg.run_skill = RunSkillConfig(timeout=120)
        cfg.safety.require_dry_walkthrough = False
        tool_ctx_kitchen_open.config = cfg

        tool_ctx_kitchen_open.runner.push(_make_result(returncode=1))  # clone guard snapshot
        tool_ctx_kitchen_open.runner.push(
            _make_result(
                0,
                '{"type": "result", "subtype": "success", "is_error": false,'
                ' "result": "done", "session_id": "s1"}',
                "",
            )
        )
        await run_skill("/investigate foo", "/tmp")

        assert tool_ctx_kitchen_open.runner.call_args_list[-1][2] == 120.0


class TestRunSkillInjectsCompletionDirective:
    """run_skill injects completion directive into the skill command."""

    @pytest.mark.anyio
    async def test_run_skill_injects_completion_directive(self, tool_ctx_kitchen_open):
        """Skill command passed to claude -p contains the completion marker instruction."""
        cfg = AutomationConfig()
        cfg.safety.require_dry_walkthrough = False
        tool_ctx_kitchen_open.config = cfg

        tool_ctx_kitchen_open.runner.push(_make_result(returncode=1))  # clone guard snapshot
        tool_ctx_kitchen_open.runner.push(
            _make_result(
                0,
                '{"type": "result", "subtype": "success", "is_error": false,'
                ' "result": "done", "session_id": "s1"}',
                "",
            )
        )
        await run_skill("/investigate foo", "/tmp")

        cmd = tool_ctx_kitchen_open.runner.call_args_list[-1][0]
        prompt_idx = cmd.index("--print") + 1 if "--print" in cmd else cmd.index("-p") + 1
        skill_arg = cmd[prompt_idx]
        assert "%%ORDER_UP::" in skill_arg
        assert "ORCHESTRATION DIRECTIVE" in skill_arg

    def test_inject_completion_directive_prohibits_standalone_marker(self):
        """
        The directive wording must explicitly instruct the model to emit the marker
        in the SAME message as its substantive output, not as a standalone message.
        This prevents the model from interpreting the directive as a post-task acknowledgment.
        """
        result = _inject_completion_directive("/audit-impl", "%%ORDER_UP%%")
        lowered = result.lower()
        assert (
            "same message" in lowered
            or "not as a separate" in lowered
            or ("standalone" in lowered and "not" in lowered)
        ), f"Directive must prohibit standalone marker emission. Got: {result!r}"


class TestRunSkillEnvPrefix:
    """run_skill always injects AUTOSKILLIT_HEADLESS=1 and optionally CLAUDE_CODE_EXIT_AFTER_STOP_DELAY via the env kwarg."""  # noqa: E501

    @pytest.mark.anyio
    async def test_default_delay_populates_env(self, tool_ctx_kitchen_open):
        tool_ctx_kitchen_open.runner.push(_make_result(returncode=1))  # clone guard snapshot
        tool_ctx_kitchen_open.runner.push(_make_result(0, _SUCCESS_JSON, ""))
        await run_skill("/investigate something", "/tmp")
        cmd, _cwd, _timeout, kwargs = tool_ctx_kitchen_open.runner.call_args_list[-1]
        assert cmd[0] == "claude"
        env = kwargs["env"]
        assert env["AUTOSKILLIT_HEADLESS"] == "1"
        assert env["CLAUDE_CODE_EXIT_AFTER_STOP_DELAY"] == "2000"

    @pytest.mark.anyio
    async def test_zero_delay_omits_delay_env_var(self, tool_ctx_kitchen_open):
        cfg = AutomationConfig()
        cfg.run_skill = RunSkillConfig(exit_after_stop_delay_ms=0)
        cfg.safety.require_dry_walkthrough = False
        tool_ctx_kitchen_open.config = cfg
        tool_ctx_kitchen_open.runner.push(_make_result(returncode=1))  # clone guard snapshot
        tool_ctx_kitchen_open.runner.push(_make_result(0, _SUCCESS_JSON, ""))
        await run_skill("/investigate something", "/tmp")
        cmd, _cwd, _timeout, kwargs = tool_ctx_kitchen_open.runner.call_args_list[-1]
        assert cmd[0] == "claude"
        env = kwargs["env"]
        assert env["AUTOSKILLIT_HEADLESS"] == "1"
        assert "CLAUDE_CODE_EXIT_AFTER_STOP_DELAY" not in env

    @pytest.mark.anyio
    async def test_custom_delay_value_in_env(self, tool_ctx_kitchen_open):
        cfg = AutomationConfig()
        cfg.run_skill = RunSkillConfig(
            exit_after_stop_delay_ms=60000, natural_exit_grace_seconds=61.0
        )
        cfg.safety.require_dry_walkthrough = False
        tool_ctx_kitchen_open.config = cfg
        tool_ctx_kitchen_open.runner.push(_make_result(returncode=1))  # clone guard snapshot
        tool_ctx_kitchen_open.runner.push(_make_result(0, _SUCCESS_JSON, ""))
        await run_skill("/investigate something", "/tmp")
        cmd, _cwd, _timeout, kwargs = tool_ctx_kitchen_open.runner.call_args_list[-1]
        assert cmd[0] == "claude"
        env = kwargs["env"]
        assert env["AUTOSKILLIT_HEADLESS"] == "1"
        assert env["CLAUDE_CODE_EXIT_AFTER_STOP_DELAY"] == "60000"


class TestRunSkillPassesSessionLogDir:
    """run_skill passes session_log_dir derived from cwd."""

    @pytest.mark.anyio
    async def test_run_skill_passes_session_log_dir(
        self, tool_ctx_kitchen_open, tmp_path, monkeypatch
    ):
        """runner receives session_log_dir derived from cwd."""
        cfg = AutomationConfig()
        cfg.safety.require_dry_walkthrough = False
        tool_ctx_kitchen_open.config = cfg

        cwd = str(tmp_path / "some-project")
        (tmp_path / "some-project").mkdir()

        log_dir = tmp_path / "logs" / "some-project"
        log_dir.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(
            "autoskillit.execution.headless._headless_launch._resolve_session_log_dir",
            lambda cwd, backend: log_dir,
        )

        tool_ctx_kitchen_open.runner.push(_make_result(returncode=1))  # clone guard snapshot
        tool_ctx_kitchen_open.runner.push(
            _make_result(
                0,
                '{"type": "result", "subtype": "success", "is_error": false,'
                ' "result": "done", "session_id": "s1"}',
                "",
            )
        )
        await run_skill("/investigate foo", cwd)

        call_kwargs = tool_ctx_kitchen_open.runner.call_args_list[-1][3]
        assert call_kwargs["session_log_dir"] == log_dir
        assert "some-project" in str(log_dir)


class TestRunSkillModel:
    """Tests for model parameter in run_skill."""

    _MOCK_STDOUT = (
        '{"type": "result", "subtype": "success", "is_error": false, '
        '"result": "done", "session_id": "s1"}'
    )

    # MOD_S1
    @pytest.mark.anyio
    async def test_run_skill_passes_model_flag(self, tool_ctx_kitchen_open):
        tool_ctx_kitchen_open.runner.push(_make_result(returncode=1))  # clone guard snapshot
        tool_ctx_kitchen_open.runner.push(_make_result(0, self._MOCK_STDOUT, ""))
        await run_skill("/investigate error", "/tmp", model="sonnet")
        cmd = tool_ctx_kitchen_open.runner.call_args_list[-1][0]
        assert "--model" in cmd
        assert cmd[cmd.index("--model") + 1] == CLAUDE_MODEL_ALIASES["sonnet"]

    # MOD_S3
    @pytest.mark.anyio
    async def test_run_skill_no_model_flag_when_empty(self, tool_ctx_kitchen_open):
        # Direct assignment bypasses CoreRunConfig.__post_init__ (which rejects empty
        # default_model). Production config is always non-empty; this simulates the
        # path where both the caller-supplied model param and the config default are
        # empty, so _resolve_model returns "" and no --model flag is emitted.
        tool_ctx_kitchen_open.config.model.default_model = ""
        tool_ctx_kitchen_open.runner.push(_make_result(returncode=1))  # clone guard snapshot
        tool_ctx_kitchen_open.runner.push(_make_result(0, self._MOCK_STDOUT, ""))
        await run_skill("/investigate error", "/tmp", model="")
        cmd = tool_ctx_kitchen_open.runner.call_args_list[-1][0]
        assert "--model" not in cmd


class TestRunSkillPerInvocationMarker:
    """Per-invocation completion markers are unique across run_skill calls."""

    @pytest.mark.anyio
    async def test_run_skill_markers_are_unique_per_invocation(self, tool_ctx_kitchen_open):
        """Two run_skill calls must generate different completion_marker values."""
        success_json = (
            '{"type": "result", "subtype": "success", "is_error": false,'
            ' "result": "done", "session_id": "s1"}'
        )
        tool_ctx_kitchen_open.runner.push(
            _make_result(returncode=1)
        )  # clone guard snapshot call 1
        tool_ctx_kitchen_open.runner.push(_make_result(returncode=0, stdout=success_json))
        tool_ctx_kitchen_open.runner.push(
            _make_result(returncode=1)
        )  # clone guard snapshot call 2
        tool_ctx_kitchen_open.runner.push(_make_result(returncode=0, stdout=success_json))

        first = json.loads(await run_skill("/investigate a", cwd="/tmp"))
        _ack_direct_run_skill_result(tool_ctx_kitchen_open, first)
        await run_skill("/investigate b", cwd="/tmp")

        calls = tool_ctx_kitchen_open.runner.call_args_list
        claude_calls = [c for c in calls if c[0][0] == "claude"]
        assert len(claude_calls) >= 2
        marker1 = claude_calls[0][3]["completion_marker"]
        marker2 = claude_calls[1][3]["completion_marker"]
        assert marker1 != marker2
        assert "%%ORDER_UP::" in marker1
        assert "%%ORDER_UP::" in marker2


class TestRunSkillExecutionMarker:
    """Execution marker directory routes through backend session locator."""

    @pytest.mark.anyio
    async def test_launch_registry_selects_exact_caller_session(
        self, tool_ctx_kitchen_open, monkeypatch, tmp_path
    ):
        from autoskillit.core import LAUNCH_ID_ENV_VAR, find_caller_session_id
        from autoskillit.core.runtime.kitchen_state import write_marker
        from autoskillit.core.runtime.session_registry import (
            bridge_claude_session_id,
            read_registry,
            write_registry_entry,
        )
        from autoskillit.server.tools import tools_execution
        from tests.fakes import InMemoryHeadlessExecutor

        project_dir = tmp_path / "project"
        state_root = tmp_path / "state"
        foreign_cwd = tmp_path / "foreign"
        foreign_cwd.mkdir()
        write_registry_entry(project_dir, "launch-a", "cook", None)
        write_registry_entry(project_dir, "launch-b", "cook", None)
        bridge_claude_session_id(project_dir, "launch-a", "session-a")
        bridge_claude_session_id(project_dir, "launch-b", "session-b")

        monkeypatch.setenv("AUTOSKILLIT_STATE_DIR", str(state_root))
        write_marker("session-a", "recipe")
        marker_a = state_root / "kitchen_state" / "session-a.json"
        past = marker_a.stat().st_mtime - 10
        os.utime(marker_a, (past, past))
        write_marker("session-b", "recipe")
        assert find_caller_session_id(project_dir=project_dir) == "session-b"

        monkeypatch.chdir(foreign_cwd)
        monkeypatch.setenv(LAUNCH_ID_ENV_VAR, "launch-a")
        monkeypatch.setattr(
            tools_execution, "read_registry", lambda _project: read_registry(project_dir)
        )
        executor = InMemoryHeadlessExecutor()
        tool_ctx_kitchen_open.executor = executor
        captured: dict[str, str] = {}

        @contextlib.asynccontextmanager
        async def _capture_marker(_marker_dir, session_id, _label):
            captured["session_id"] = session_id
            yield

        monkeypatch.setattr(tools_execution, "execution_marker", _capture_marker)
        tool_ctx_kitchen_open.runner.push(_make_result(returncode=1))

        payload = json.loads(await run_skill("/investigate exact binding", "/tmp"))
        _ack_direct_run_skill_result(tool_ctx_kitchen_open, payload)

        assert captured["session_id"] == "session-a"
        assert executor.calls[0].caller_session_id == "session-a"

    @pytest.mark.anyio
    @pytest.mark.parametrize(
        "registry",
        [
            {},
            {"launch-a": {"claude_session_id": None}},
            {"launch-a": []},
            {"launch-a": {"claude_session_id": " \t"}},
            [],
        ],
        ids=("missing-row", "unbound-row", "malformed-row", "blank-session", "malformed-registry"),
    )
    async def test_invalid_launch_binding_fails_before_execution(
        self, tool_ctx_kitchen_open, monkeypatch, registry
    ):
        from autoskillit.core import LAUNCH_ID_ENV_VAR
        from autoskillit.server.tools import tools_execution
        from tests.fakes import InMemoryHeadlessExecutor

        monkeypatch.setenv(LAUNCH_ID_ENV_VAR, "launch-a")
        monkeypatch.setattr(tools_execution, "read_registry", lambda _project: registry)
        executor = InMemoryHeadlessExecutor()
        tool_ctx_kitchen_open.executor = executor
        marker_entered = False

        @contextlib.asynccontextmanager
        async def _capture_marker(*_args, **_kwargs):
            nonlocal marker_entered
            marker_entered = True
            yield

        monkeypatch.setattr(tools_execution, "execution_marker", _capture_marker)
        tool_ctx_kitchen_open.runner.push(_make_result(returncode=1))

        result = json.loads(await run_skill("/investigate invalid binding", "/tmp"))

        assert result == {
            "success": False,
            "error": "run_skill: current launch has no exact caller session binding: 'launch-a'",
            "stage": "preflight:caller_session",
            "retriable": False,
        }
        assert tool_ctx_kitchen_open.run_skill_completion.admission("open_kitchen") == (
            True,
            "idle",
        )
        assert marker_entered is False
        assert executor.calls == []

    @pytest.mark.anyio
    async def test_no_launch_id_uses_marker_fallback(self, tool_ctx_kitchen_open, monkeypatch):
        from autoskillit.core import LAUNCH_ID_ENV_VAR
        from autoskillit.server.tools import tools_execution
        from tests.fakes import InMemoryHeadlessExecutor

        monkeypatch.delenv(LAUNCH_ID_ENV_VAR, raising=False)
        monkeypatch.setattr(
            tools_execution,
            "find_caller_session_id",
            lambda **_kwargs: "fallback-session",
        )
        monkeypatch.setattr(
            tools_execution,
            "read_registry",
            lambda _project: pytest.fail("registry must not be read without a launch ID"),
        )
        executor = InMemoryHeadlessExecutor()
        tool_ctx_kitchen_open.executor = executor
        captured: dict[str, str] = {}

        @contextlib.asynccontextmanager
        async def _capture_marker(_marker_dir, session_id, _label):
            captured["session_id"] = session_id
            yield

        monkeypatch.setattr(tools_execution, "execution_marker", _capture_marker)
        tool_ctx_kitchen_open.runner.push(_make_result(returncode=1))

        payload = json.loads(await run_skill("/investigate fallback binding", "/tmp"))
        _ack_direct_run_skill_result(tool_ctx_kitchen_open, payload)

        assert captured["session_id"] == "fallback-session"
        assert executor.calls[0].caller_session_id == "fallback-session"

    @pytest.mark.anyio
    async def test_marker_dir_routes_through_session_locator(
        self, tool_ctx_kitchen_open, monkeypatch
    ):
        mock_locator = Mock()
        controlled_path = Path("/controlled/marker/dir")
        mock_locator.project_log_dir.return_value = controlled_path
        mock_backend = Mock()
        mock_backend.session_locator.return_value = mock_locator
        mock_backend.name = "claude-code"
        mock_backend.conventions.skills_subdir = ClaudeDirectoryConventions.ADD_DIR_SKILLS_SUBDIR
        mock_backend.capabilities.mcp_config_capable = False
        mock_backend.capabilities.session_dir_persistent = False
        mock_backend.validate_session_layout.return_value = []
        tool_ctx_kitchen_open.backend = mock_backend

        captured = {}

        @contextlib.asynccontextmanager
        async def _capture_marker(marker_dir, *args, **kwargs):
            captured["marker_dir"] = marker_dir
            yield

        monkeypatch.setattr(
            "autoskillit.server.tools.tools_execution.execution_marker",
            _capture_marker,
        )

        tool_ctx_kitchen_open.runner.push(_make_result(returncode=1))
        tool_ctx_kitchen_open.runner.push(_make_result(0, _SUCCESS_JSON, ""))
        await run_skill("/investigate test", "/tmp")

        assert captured["marker_dir"] == controlled_path

    @pytest.mark.anyio
    async def test_missing_backend_fails_before_execution_marker(
        self, tool_ctx_kitchen_open, monkeypatch
    ):
        import json

        tool_ctx_kitchen_open.backend = None

        captured = {}

        @contextlib.asynccontextmanager
        async def _capture_marker(marker_dir, *args, **kwargs):
            captured["marker_dir"] = marker_dir
            yield

        monkeypatch.setattr(
            "autoskillit.server.tools.tools_execution.execution_marker",
            _capture_marker,
        )

        tool_ctx_kitchen_open.runner.push(_make_result(returncode=1))
        tool_ctx_kitchen_open.runner.push(_make_result(0, _SUCCESS_JSON, ""))
        result = json.loads(await run_skill("/investigate test", "/tmp"))

        assert result["success"] is False
        assert "backend" in result["result"].lower()
        assert captured == {}


class TestRunSkillMcpTimeout:
    """run_skill wraps executor.run with anyio.fail_after(mcp_tool_timeout_sec)."""

    @pytest.mark.anyio
    async def test_run_skill_returns_crashed_on_mcp_timeout(
        self, tool_ctx_kitchen_open, monkeypatch
    ):
        """On TimeoutError, run_skill returns SkillResult.crashed() envelope."""
        import json

        cfg = AutomationConfig()
        cfg.safety.require_dry_walkthrough = False
        tool_ctx_kitchen_open.config = cfg

        tool_ctx_kitchen_open.runner.push(_make_result(returncode=1))  # clone guard

        async def _timeout_run(*args, **kwargs):
            raise TimeoutError("mcp_tool_timeout_sec exceeded")

        monkeypatch.setattr(tool_ctx_kitchen_open.executor, "run", _timeout_run)

        result_json = await run_skill("/investigate something", "/tmp")
        result = json.loads(result_json)
        assert result["success"] is False
        assert result["subtype"] == "crashed"
