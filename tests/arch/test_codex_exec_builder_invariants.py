"""Cross-builder invariant matrix: parametrized tests across all four Codex exec builders."""

from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit.core import (
    AGENT_BACKEND_DYNACONF_ENV_VAR,
    CODEX_MCP_ENV_FORWARD_VARS,
    MCP_CLIENT_BACKEND_ENV_VAR,
    DirectInstall,
    OutputFormat,
)
from autoskillit.execution.backends.codex import _IMAGE_GENERATION_DISABLED, CodexBackend
from autoskillit.execution.commands import _HEADLESS_EXCLUSIVE_VARS

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]


def _build_headless():
    return CodexBackend().build_headless_cmd("test prompt")


def _build_skill_session():
    return CodexBackend().build_skill_session_cmd(
        skill_command="/test-skill",
        cwd="/work",
        completion_marker="%%DONE%%",
        model=None,
        plugin_source=None,
        output_format=OutputFormat.JSON,
    )


def _build_food_truck():
    return CodexBackend().build_food_truck_cmd(
        orchestrator_prompt="dispatch",
        plugin_source=DirectInstall(plugin_dir=Path("/pkg")),
        cwd="/work",
        completion_marker="%%DONE%%",
    )


def _build_resume():
    return CodexBackend().build_resume_cmd(resume_session_id="sess-abc", prompt="continue")


ALL_BUILDERS = [_build_headless, _build_skill_session, _build_food_truck, _build_resume]
BUILDER_IDS = ["headless", "skill_session", "food_truck", "resume"]


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AUTOSKILLIT_CAMPAIGN_ID", raising=False)
    monkeypatch.delenv("AUTOSKILLIT_KITCHEN_SESSION_ID", raising=False)


@pytest.mark.parametrize("builder", ALL_BUILDERS, ids=BUILDER_IDS)
def test_all_exec_builders_start_with_codex_exec(builder) -> None:
    spec = builder()
    assert spec.cmd[0] == "codex"
    assert spec.cmd[1] == "exec"


@pytest.mark.parametrize("builder", ALL_BUILDERS, ids=BUILDER_IDS)
def test_all_exec_builders_have_sandbox(builder) -> None:
    spec = builder()
    assert "--sandbox" in spec.cmd


@pytest.mark.parametrize("builder", ALL_BUILDERS, ids=BUILDER_IDS)
def test_all_exec_builders_have_image_generation_disabled(builder) -> None:
    spec = builder()
    assert _IMAGE_GENERATION_DISABLED in spec.cmd


_REINJECTED_BY_BUILDER: dict[str, set[str]] = {
    "headless": set(),
    "skill_session": {
        "AUTOSKILLIT_SESSION_TYPE",
        "MAX_MCP_OUTPUT_TOKENS",
        "AUTOSKILLIT_SKILL_NAME",
        "AUTOSKILLIT_CWD",
    },
    "food_truck": {
        "AUTOSKILLIT_SESSION_TYPE",
        "MAX_MCP_OUTPUT_TOKENS",
        "AUTOSKILLIT_CWD",
        "AUTOSKILLIT_COMPLETION_MARKER",
    },
    "resume": {"MAX_MCP_OUTPUT_TOKENS"},
}


@pytest.mark.parametrize(
    ("builder", "builder_id"), list(zip(ALL_BUILDERS, BUILDER_IDS)), ids=BUILDER_IDS
)
def test_all_exec_builders_filter_headless_exclusive_vars(
    builder, builder_id, monkeypatch
) -> None:
    monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "leaked")
    spec = builder()
    reinjected = _REINJECTED_BY_BUILDER[builder_id]
    leaking = (
        _HEADLESS_EXCLUSIVE_VARS - reinjected - CODEX_MCP_ENV_FORWARD_VARS
    ) & spec.env.keys()
    assert not leaking, f"_HEADLESS_EXCLUSIVE_VARS leaked: {leaking}"


@pytest.mark.parametrize("builder", ALL_BUILDERS, ids=BUILDER_IDS)
def test_all_exec_builders_have_autoskillit_headless(builder) -> None:
    spec = builder()
    assert "AUTOSKILLIT_HEADLESS" in spec.env
    assert spec.env["AUTOSKILLIT_HEADLESS"] == "1"


@pytest.mark.parametrize("builder", ALL_BUILDERS, ids=BUILDER_IDS)
def test_all_exec_builders_have_backend_env_vars(builder) -> None:
    spec = builder()
    assert AGENT_BACKEND_DYNACONF_ENV_VAR in spec.env
    assert MCP_CLIENT_BACKEND_ENV_VAR in spec.env
