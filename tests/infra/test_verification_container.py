"""Structural contract for the tracked live-verification container."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from autoskillit.core import CLAUDE_CODE_CAPABILITIES
from autoskillit.core.io import load_yaml

pytestmark = [pytest.mark.layer("infra"), pytest.mark.small]

_ROOT = Path(__file__).resolve().parents[2]
_DOCKERFILE = _ROOT / "scripts" / "docker" / "verification" / "Dockerfile"


def _docker_arg(dockerfile: str, name: str) -> str:
    match = re.search(rf"^ARG {name}=([^\s]+)$", dockerfile, re.MULTILINE)
    assert match is not None, f"Dockerfile must declare {name}"
    return match.group(1)


def test_cli_pins_match_live_conformance_authorities() -> None:
    dockerfile = _DOCKERFILE.read_text(encoding="utf-8")
    taskfile = load_yaml(_ROOT / "Taskfile.yml")
    workflow = load_yaml(_ROOT / ".github" / "workflows" / "conformance-probes.yml")

    codex_preconditions = taskfile["tasks"]["test-smoke-codex-web-agent-live-gate"][
        "preconditions"
    ]
    codex_version_check = next(
        item["sh"] for item in codex_preconditions if "codex --version" in item["sh"]
    )
    taskfile_codex_pin = re.search(r"codex-cli ([0-9.]+)", codex_version_check)
    assert taskfile_codex_pin is not None

    jobs = workflow["jobs"]
    codex_install = next(
        step["run"]
        for step in jobs["codex-probe"]["steps"]
        if step.get("name") == "Install pinned Codex CLI"
    )
    workflow_codex_pin = re.search(r"@openai/codex@([0-9.]+)", codex_install)
    assert workflow_codex_pin is not None
    claude_pins = {
        entry["claude-version"] for entry in jobs["claude-probe"]["strategy"]["matrix"]["include"]
    }

    assert _docker_arg(dockerfile, "CODEX_VERSION") == taskfile_codex_pin.group(1)
    assert _docker_arg(dockerfile, "CODEX_VERSION") == workflow_codex_pin.group(1)
    assert {_docker_arg(dockerfile, "CLAUDE_VERSION")} == claude_pins
    assert _docker_arg(dockerfile, "CLAUDE_VERSION") == CLAUDE_CODE_CAPABILITIES.min_version


def test_image_is_archive_buildable_locked_and_non_root() -> None:
    dockerfile = _DOCKERFILE.read_text(encoding="utf-8")

    assert "COPY --chown=verifier:verifier . /workspace" in dockerfile
    assert "uv sync --locked --extra dev" in dockerfile
    assert "COPY .git" not in dockerfile
    assert re.findall(r"^USER (\S+)$", dockerfile, re.MULTILINE)[-1] == "verifier"


def test_build_credentials_are_secret_mounted_only() -> None:
    dockerfile = _DOCKERFILE.read_text(encoding="utf-8")

    assert "--mount=type=secret,id=github_token" in dockerfile
    assert not re.search(r"^ARG .*?(?:TOKEN|KEY|PASSWORD|CREDENTIAL)", dockerfile, re.MULTILINE)
    assert not re.search(
        r"^COPY .*?(?:\.codex|\.claude|credentials|auth\.json)",
        dockerfile,
        re.MULTILINE,
    )
