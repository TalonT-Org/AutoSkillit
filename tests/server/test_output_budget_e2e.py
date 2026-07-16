"""Env-gated end-to-end output-budget investigation probes.

Unlike the one-prompt CLI conformance probe, this harness constructs the real
server composition root, opens the kitchen, and dispatches ``/investigate``
through ``run_skill``. It requires isolated environment-provided credentials
and never reads the user's CLI configuration.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from autoskillit.config import (
    AgentBackendConfig,
    AutomationConfig,
    QuotaGuardConfig,
)
from autoskillit.core import DirectInstall, pkg_root
from autoskillit.execution.backends._codex_config import ensure_codex_mcp_registered
from autoskillit.execution.backends._codex_hooks import sync_hooks_to_codex_config
from autoskillit.execution.process import DefaultSubprocessRunner
from autoskillit.hook_registry import generate_hooks_json
from autoskillit.server._factory import make_context
from autoskillit.server.tools.tools_execution import run_skill
from autoskillit.server.tools.tools_kitchen import close_kitchen, open_kitchen

pytestmark = [pytest.mark.layer("server"), pytest.mark.large, pytest.mark.smoke]

_INVESTIGATION_PATH_RE = re.compile(r"investigation_path\s*=\s*(/\S+)")
_REPORT_REQUIRED_SECTIONS = (
    "## Summary",
    "## Affected Components",
    "## Data Flow",
    "## Test Gap Analysis",
    "## Scope Boundary",
    "## Recommendations",
)


def _has_codex_credentials() -> bool:
    return bool(os.environ.get("CODEX_API_KEY") or os.environ.get("OPENAI_API_KEY"))


def _has_claude_credentials() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"))


_BACKEND_CASES = [
    pytest.param(
        "codex",
        marks=pytest.mark.skipif(
            not os.environ.get("CODEX_SMOKE_TEST")
            or not shutil.which("codex")
            or not _has_codex_credentials(),
            reason="Codex E2E requires CODEX_SMOKE_TEST and an environment API key",
        ),
    ),
    pytest.param(
        "claude-code",
        marks=pytest.mark.skipif(
            not os.environ.get("CLAUDE_CODE_SMOKE_TEST")
            or not shutil.which("claude")
            or not _has_claude_credentials(),
            reason="Claude E2E requires CLAUDE_CODE_SMOKE_TEST and an environment credential",
        ),
    ),
]


def _fixture_payload(size: int = 520_000) -> str:
    head = "HEAD-SENTINEL::deep-investigate\n"
    middle = "\nMIDDLE-SENTINEL::deep-investigate\n"
    tail = "\nTAIL-SENTINEL::deep-investigate"
    filler = size - len(head) - len(middle) - len(tail)
    before_middle = filler // 2
    payload = head + ("a" * before_middle) + middle + ("z" * (filler - before_middle)) + tail
    assert len(payload.encode("utf-8")) == size
    return payload


def _init_fixture_repo(path: Path) -> Path:
    path.mkdir()
    payload_path = path / "large-output-fixture.txt"
    payload_path.write_text(_fixture_payload(), encoding="utf-8")
    (path / "README.md").write_text(
        "# Output budget fixture\n\nInvestigate lossless handling of the large fixture.\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "init", "-q"], cwd=path, check=True, timeout=10)
    subprocess.run(["git", "config", "user.email", "probe@example.invalid"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Output Budget Probe"], cwd=path, check=True)
    subprocess.run(["git", "add", "."], cwd=path, check=True)
    subprocess.run(["git", "commit", "-qm", "fixture"], cwd=path, check=True, timeout=10)
    return payload_path


def _configure_isolated_cli(
    backend: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    codex_home = home / ".codex"
    claude_config = tmp_path / "claude-config"
    for directory in (home, codex_home, claude_config):
        directory.mkdir(parents=True)

    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(claude_config))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(home / ".local" / "share"))
    venv_bin = Path(__file__).resolve().parents[2] / ".venv" / "bin"
    monkeypatch.setenv("PATH", f"{venv_bin}{os.pathsep}{os.environ.get('PATH', '')}")
    monkeypatch.setenv("AUTOSKILLIT_FEATURES__EXPERIMENTAL_ENABLED", "true")

    if backend == "codex":
        config_path = codex_home / "config.toml"
        ensure_codex_mcp_registered(config_path=config_path)
        sync_hooks_to_codex_config(config_path=config_path)
    else:
        (claude_config / "settings.json").write_text(
            json.dumps(generate_hooks_json(), indent=2) + "\n",
            encoding="utf-8",
        )


def _assert_investigation_result(raw_result: str, fixture_path: Path) -> None:
    result = json.loads(raw_result)
    assert result["success"] is True, result
    match = _INVESTIGATION_PATH_RE.search(str(result.get("result", "")))
    assert match is not None, f"missing investigation_path token: {result}"
    report_path = Path(match.group(1))
    assert report_path.is_file(), f"investigation report not found: {report_path}"
    report = report_path.read_text(encoding="utf-8")
    for section in _REPORT_REQUIRED_SECTIONS:
        assert section in report, f"report missing coherent synthesis section {section!r}"
    assert str(fixture_path) in report or fixture_path.name in report
    assert "Deep Analysis" in report


@pytest.mark.anyio
@pytest.mark.parametrize("backend", _BACKEND_CASES)
async def test_deep_investigate_completes_with_bounded_large_evidence(
    backend: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Real kitchen + run_skill dispatch completes and emits a validated report path."""
    fixture_repo = tmp_path / "fixture-repo"
    fixture_path = _init_fixture_repo(fixture_repo)
    _configure_isolated_cli(backend, tmp_path, monkeypatch)

    config = AutomationConfig(
        agent_backend=AgentBackendConfig(backend=backend),
        quota_guard=QuotaGuardConfig(enabled=False),
        features={"codex_backend": backend == "codex"},
        experimental_enabled=True,
    )
    tool_ctx = make_context(
        config,
        runner=DefaultSubprocessRunner(),
        plugin_source=DirectInstall(plugin_dir=pkg_root()),
        project_dir=fixture_repo,
    )
    tool_ctx.config.linux_tracing.log_dir = str(tmp_path / "session-logs")
    tool_ctx.config.linux_tracing.tmpfs_path = str(tmp_path / "shm")

    from autoskillit.server import _state

    monkeypatch.setattr(_state, "_ctx", tool_ctx)
    monkeypatch.setattr(_state, "_startup_ready", None)
    mcp_ctx = AsyncMock()

    kitchen_result = json.loads(await open_kitchen(ctx=mcp_ctx))
    assert kitchen_result["success"] is True, kitchen_result
    assert kitchen_result["kitchen"] == "open"

    command = (
        f"/investigate --deep Analyze {fixture_path} and the repository paths that produce "
        "or consume it. Use byte-bounded evidence commands, complete every required deep-mode "
        "batch and validation stage, do not modify tracked files, and write the final report."
    )
    try:
        raw_result = await run_skill(
            command,
            str(fixture_repo),
            step_name="output_budget_deep_investigate_probe",
            idle_output_timeout=0,
            ctx=mcp_ctx,
        )
        _assert_investigation_result(raw_result, fixture_path)
    finally:
        await close_kitchen(ctx=mcp_ctx)
