"""Owner-scope env var propagation: private-var membership, Codex MCP forwarding,

L1 skill-session re-injection, and L2 food-truck scope precedence over ambient env.
"""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

import pytest

from autoskillit.core import (
    AUTOSKILLIT_PRIVATE_ENV_VARS,
    CODEX_MCP_ENV_FORWARD_VARS,
    OWNER_SCOPE_DIR_ENV_VAR,
    OWNER_SCOPE_ENV_VAR,
)
from autoskillit.execution.backends._codex_config import ensure_codex_mcp_registered
from autoskillit.execution.backends.claude import ClaudeCodeBackend
from tests.execution.backends._generated_home_backend import (
    GeneratedHomeCodexBackend,
    bind_generated_home_backend,
)
from tests.fixtures.codex import codex_skill_add_dirs

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]

CodexBackend = GeneratedHomeCodexBackend


@pytest.fixture(autouse=True)
def _bind_generated_home_backend(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bind_generated_home_backend(tmp_path, monkeypatch)


# ---------------------------------------------------------------------------
# Private-var and Codex-forward membership
# ---------------------------------------------------------------------------


def test_owner_scope_vars_are_private() -> None:
    assert OWNER_SCOPE_ENV_VAR in AUTOSKILLIT_PRIVATE_ENV_VARS
    assert OWNER_SCOPE_DIR_ENV_VAR in AUTOSKILLIT_PRIVATE_ENV_VARS


def test_owner_scope_vars_are_forwarded_to_codex_mcp_server() -> None:
    assert OWNER_SCOPE_ENV_VAR in CODEX_MCP_ENV_FORWARD_VARS
    assert OWNER_SCOPE_DIR_ENV_VAR in CODEX_MCP_ENV_FORWARD_VARS


# ---------------------------------------------------------------------------
# Codex config.toml registration
# ---------------------------------------------------------------------------


@pytest.fixture()
def fake_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr("pathlib.Path.home", lambda: home)
    return home


def test_config_toml_env_vars_include_owner_scope(fake_home: Path) -> None:
    ensure_codex_mcp_registered()
    data = tomllib.loads((fake_home / ".codex" / "config.toml").read_text())
    env_vars = set(data["mcp_servers"]["autoskillit"]["env_vars"])
    assert {OWNER_SCOPE_ENV_VAR, OWNER_SCOPE_DIR_ENV_VAR} <= env_vars


# ---------------------------------------------------------------------------
# L1 skill-session re-injection from ambient env
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "backend_factory",
    [
        pytest.param(ClaudeCodeBackend, id="claude"),
        pytest.param(CodexBackend, id="codex"),
    ],
)
def test_skill_session_cmd_carries_owner_scope_from_ambient_env(
    backend_factory: type[ClaudeCodeBackend] | type[CodexBackend],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(OWNER_SCOPE_ENV_VAR, "dispatch-parent-000000000000")
    monkeypatch.setenv(OWNER_SCOPE_DIR_ENV_VAR, "/ambient/scope/dir")
    backend = backend_factory()

    spec = backend.build_skill_session_cmd(
        "/autoskillit:investigate",
        "/clone",
        completion_marker="DONE",
        add_dirs=codex_skill_add_dirs("/clone"),
    )

    assert spec.env[OWNER_SCOPE_ENV_VAR] == "dispatch-parent-000000000000"
    assert spec.env[OWNER_SCOPE_DIR_ENV_VAR] == "/ambient/scope/dir"


@pytest.mark.parametrize(
    "backend_factory",
    [
        pytest.param(ClaudeCodeBackend, id="claude"),
        pytest.param(CodexBackend, id="codex"),
    ],
)
def test_skill_session_cmd_omits_owner_scope_without_ambient_env(
    backend_factory: type[ClaudeCodeBackend] | type[CodexBackend],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(OWNER_SCOPE_ENV_VAR, raising=False)
    monkeypatch.delenv(OWNER_SCOPE_DIR_ENV_VAR, raising=False)
    backend = backend_factory()

    spec = backend.build_skill_session_cmd(
        "/autoskillit:investigate",
        "/clone",
        completion_marker="DONE",
        add_dirs=codex_skill_add_dirs("/clone"),
    )

    assert OWNER_SCOPE_ENV_VAR not in spec.env
    assert OWNER_SCOPE_DIR_ENV_VAR not in spec.env


# ---------------------------------------------------------------------------
# L2 food-truck dispatch: executor's own scope wins over ambient inheritance
# ---------------------------------------------------------------------------


def _success_stdout(marker: str) -> str:
    return json.dumps(
        {
            "type": "result",
            "subtype": "success",
            "result": f"L3 done {marker}",
            "session_id": "ft-session",
            "is_error": False,
        }
    )


class TestFoodTruckDispatchOwnerScopePrecedence:
    @pytest.mark.anyio
    async def test_dispatch_food_truck_mints_own_scope_over_ambient(
        self,
        minimal_ctx,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from autoskillit.core.types import SubprocessResult, TerminationReason
        from autoskillit.execution.headless import DefaultHeadlessExecutor
        from autoskillit.execution.process import default_tether_dir
        from tests.fakes import MockSubprocessRunner

        ambient_token = "dispatch-ambient-parent-000000000000"
        monkeypatch.setenv(OWNER_SCOPE_ENV_VAR, ambient_token)
        monkeypatch.setenv(OWNER_SCOPE_DIR_ENV_VAR, str(tmp_path / "ambient-scope-dir"))
        monkeypatch.setenv("AUTOSKILLIT_LOG_DIR", str(tmp_path))

        runner = MockSubprocessRunner()
        runner.set_default(
            SubprocessResult(
                returncode=0,
                stdout=_success_stdout("%%FT_DONE%%"),
                stderr="",
                termination=TerminationReason.NATURAL_EXIT,
                pid=55555,
                proc_snapshots=[],
            )
        )
        minimal_ctx.runner = runner
        minimal_ctx.config.linux_tracing.log_dir = str(tmp_path)
        minimal_ctx.backend = ClaudeCodeBackend()

        executor = DefaultHeadlessExecutor(minimal_ctx)
        await executor.dispatch_food_truck(
            "You are an L3 orchestrator",
            str(tmp_path),
            completion_marker="%%FT_DONE%%",
            plugin_authority=minimal_ctx.plugin_authority,
        )

        assert runner.call_args_list, "runner was never called"
        _cmd, _cwd, _timeout, kwargs = runner.call_args_list[0]
        env = kwargs.get("env")
        assert env is not None
        scope_token = env[OWNER_SCOPE_ENV_VAR]
        assert re.fullmatch(r"dispatch-.+", scope_token)
        assert scope_token != ambient_token
        assert env[OWNER_SCOPE_DIR_ENV_VAR] == str(default_tether_dir())
