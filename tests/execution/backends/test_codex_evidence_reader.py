"""Deterministic conformance tests for the sterile Codex reader projection."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

import autoskillit.execution.evidence_reader as launcher
from autoskillit.core import load_bundled_agent_definitions
from autoskillit.execution.evidence_reader import EvidenceReaderLaunchError

pytestmark = [pytest.mark.layer("execution"), pytest.mark.medium]


def _definition():
    return next(
        definition
        for definition in load_bundled_agent_definitions()
        if definition.name == "pr-source-reader"
    )


def _credential(path: Path) -> Path:
    path.write_text(
        json.dumps(
            {
                "auth_mode": "chatgpt",
                "tokens": {"access_token": "access", "account_id": "account"},
            }
        ),
        encoding="utf-8",
    )
    path.chmod(0o600)
    return path


def test_auth_selection_is_exact_and_mode_matched(tmp_path: Path) -> None:
    api = launcher._select_authentication(
        {"OPENAI_API_KEY": "key", "HTTPS_PROXY": "https://proxy.invalid"}, None
    )
    assert api.forced_login_method == "api"
    assert dict(api.environment) == {
        "HTTPS_PROXY": "https://proxy.invalid",
        "OPENAI_API_KEY": "key",
    }
    assert api.credential_text is None

    source = _credential(tmp_path / "auth.json")
    chatgpt = launcher._select_authentication({"HTTPS_PROXY": "proxy"}, source)
    assert chatgpt.forced_login_method == "chatgpt"
    assert json.loads(chatgpt.credential_text or "null")["auth_mode"] == "chatgpt"
    assert dict(chatgpt.environment) == {"HTTPS_PROXY": "proxy"}


def test_provider_projection_rejects_unallowlisted_caller_environment() -> None:
    with pytest.raises(EvidenceReaderLaunchError) as raised:
        launcher._positive_mapping(
            {"OPENAI_API_KEY": "key", "CALLER_SECRET": "must-not-cross"},
            launcher._PROVIDER_ENV,
            "provider_env_invalid",
        )
    assert raised.value.code == "provider_env_invalid"


@pytest.mark.parametrize(
    "name",
    [
        "CODEX_BASE_URL",
        "OPENAI_BASE_URL",
        "OPENAI_ORGANIZATION",
        "OPENAI_ORG_ID",
        "OPENAI_PROJECT_ID",
    ],
)
def test_provider_projection_rejects_custom_provider_routing(name: str) -> None:
    with pytest.raises(EvidenceReaderLaunchError) as raised:
        launcher._positive_mapping(
            {"OPENAI_API_KEY": "key", name: "custom"},
            launcher._PROVIDER_ENV,
            "provider_env_invalid",
        )
    assert raised.value.code == "provider_env_invalid"


@pytest.mark.parametrize(
    ("provider", "with_file", "code"),
    [
        ({}, False, "provider_auth_missing"),
        (
            {"CODEX_API_KEY": "one", "OPENAI_API_KEY": "two"},
            False,
            "provider_auth_ambiguous",
        ),
        ({"OPENAI_API_KEY": "one"}, True, "provider_auth_ambiguous"),
    ],
)
def test_auth_selection_rejects_missing_or_ambiguous_sources(
    tmp_path: Path,
    provider: dict[str, str],
    with_file: bool,
    code: str,
) -> None:
    source = _credential(tmp_path / "auth.json") if with_file else None
    with pytest.raises(EvidenceReaderLaunchError) as raised:
        launcher._select_authentication(provider, source)
    assert raised.value.code == code


@pytest.mark.parametrize("unsafe", ["symlink", "mode", "shape"])
def test_chatgpt_credential_source_is_regular_private_and_supported(
    tmp_path: Path, unsafe: str
) -> None:
    source = tmp_path / "auth.json"
    if unsafe == "symlink":
        target = _credential(tmp_path / "target.json")
        source.symlink_to(target)
    elif unsafe == "mode":
        _credential(source).chmod(0o644)
    else:
        source.write_text('{"auth_mode":"custom"}', encoding="utf-8")
        source.chmod(0o600)
    with pytest.raises(EvidenceReaderLaunchError) as raised:
        launcher._select_authentication({}, source)
    assert raised.value.code == "provider_auth_invalid"


def test_config_command_and_schema_are_exact_sterile_projection(tmp_path: Path) -> None:
    definition = _definition()
    auth = launcher._select_authentication({"OPENAI_API_KEY": "key"}, None)
    transport = {
        "command": "/usr/bin/autoskillit",
        "args": [],
        "env_vars": [
            "AUTOSKILLIT_EVIDENCE_READER_AUTHORITY",
            "AUTOSKILLIT_EVIDENCE_READER_AUTHORITY_PATH",
            "AUTOSKILLIT_EVIDENCE_READER_CAPABILITY",
        ],
    }
    tools = ("get_authorized_artifact_page", "read_authorized_artifact")
    config = launcher._render_config(
        definition,
        transport,
        tools,
        tmp_path / "models.json",
        auth,
        ("HOME", "OPENAI_API_KEY"),
        tmp_path,
    )
    assert 'forced_login_method = "api"' in config
    assert 'approval_policy = "never"' in config
    assert 'sandbox_mode = "read-only"' in config
    assert 'inherit = "none"' in config
    assert "project_root_markers = []" in config
    assert "enabled_tools" in config
    assert f'cwd = "{tmp_path}"' in config
    assert "run_cmd" not in config

    schema_path = tmp_path / "result.schema.json"
    command = launcher._codex_command(
        "/usr/bin/codex",
        definition,
        cwd=tmp_path,
        output_schema_path=schema_path,
        prompt="prompt",
    )
    assert command[:2] == ["/usr/bin/codex", "exec"]
    assert set(
        (
            "--strict-config",
            "--ignore-rules",
            "--ephemeral",
            "--skip-git-repo-check",
            "--sandbox",
            "--output-schema",
            "--json",
            "-C",
        )
    ) <= set(command)
    assert "project_root_markers=[]" in command
    assert "--add-dir" not in command
    assert "--dangerously-bypass-hook-trust" not in command
    schema = json.loads(launcher._result_output_schema())
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == launcher._RESULT_KEYS


def test_conformance_probe_attests_version_help_auth_and_output_schema(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    definition = _definition()
    auth = launcher._select_authentication({"OPENAI_API_KEY": "key"}, None)
    seen: list[tuple[str, ...]] = []

    def run(command, **_kwargs):
        command = tuple(command)
        seen.append(command)
        if command[-1] == "--version":
            return launcher._ProcessOutput(0, b"codex-cli 0.147.0\n", b"")
        if command[-2:] == ("exec", "--help"):
            flags = " ".join(
                (
                    "--strict-config",
                    "--ignore-rules",
                    "--ephemeral",
                    "--skip-git-repo-check",
                    "--output-schema",
                    "--json",
                    "--cd",
                )
            )
            return launcher._ProcessOutput(0, flags.encode(), b"")
        if command[-2:] == ("login", "status"):
            return launcher._ProcessOutput(0, b"Logged in using an API key - redacted\n", b"")
        stream = (
            b'{"type":"thread.started","thread_id":"probe"}\n'
            b'{"type":"item.completed","item":{"type":"agent_message",'
            b'"text":"{\\"probe\\":\\"ok\\"}"}}\n'
            b'{"type":"turn.completed","usage":{}}\n'
        )
        return launcher._ProcessOutput(0, stream, b"")

    monkeypatch.setattr(launcher, "_run_bounded", run)
    version = launcher._probe_conformance(
        "/usr/bin/codex",
        definition,
        auth,
        cwd=tmp_path,
        environment={"OPENAI_API_KEY": "key"},
        probe_schema_path=tmp_path / "probe.schema.json",
        deadline=launcher.time.monotonic() + 30,
    )
    assert version == "codex-cli 0.147.0"
    assert len(seen) == 4
    assert seen[-1][1] == "exec"
    assert "--output-schema" in seen[-1]
    assert os.fspath(tmp_path) in seen[-1]
