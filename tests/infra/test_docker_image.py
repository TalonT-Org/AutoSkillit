"""Structural contract and pin guard for the tracked multi-target Docker image."""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from autoskillit.core import CLAUDE_CODE_CAPABILITIES
from autoskillit.core.io import load_yaml
from autoskillit.execution.backends._codex_discovery import CODEX_CLI_MIN_VERSION

pytestmark = [pytest.mark.layer("infra"), pytest.mark.small]

_ROOT = Path(__file__).resolve().parents[2]
_DOCKERFILE = _ROOT / "scripts" / "docker" / "Dockerfile"
_WORKFLOWS = _ROOT / ".github" / "workflows"
_TASKFILE = _ROOT / "Taskfile.yml"

_PINS = (
    "UV_VERSION",
    "NODE_VERSION",
    "RUST_VERSION",
    "CLAUDE_VERSION",
    "CODEX_VERSION",
    "GO_TASK_VERSION",
    "PRE_COMMIT_VERSION",
    "RIPGREP_VERSION",
    "JQ_VERSION",
    "GH_VERSION",
)
# CI hosts npm on the runner's own Node and never installs pre-commit, jq or gh,
# so the Dockerfile is the only authority for these pins.
_DOCKERFILE_ONLY_PINS = frozenset(
    {"NODE_VERSION", "PRE_COMMIT_VERSION", "JQ_VERSION", "GH_VERSION"}
)
# `uses:` prefix -> (pin, `with:` input) for the setup actions CI uses.
_SETUP_ACTION_PINS = {
    "astral-sh/setup-uv@": ("UV_VERSION", "version"),
    "arduino/setup-task@": ("GO_TASK_VERSION", "version"),
    "dtolnay/rust-toolchain@": ("RUST_VERSION", "toolchain"),
}
# Agent CLI version literals anywhere in CI or Taskfile text: install specs, version
# checks, matrix entries, artifact names and evidence-contract names.
_AGENT_VERSION_LITERALS = {
    "CLAUDE_VERSION": re.compile(r"claude[\w-]*?[\s@:\"'=-]+(\d+\.\d+\.\d+)"),
    "CODEX_VERSION": re.compile(r"codex[\w-]*?[\s@:\"'=-]+(\d+\.\d+\.\d+)"),
}


def _dockerfile() -> str:
    return _DOCKERFILE.read_text(encoding="utf-8")


def _pins() -> dict[str, str]:
    declared: dict[str, list[str]] = {}
    for name, value in re.findall(r"^ARG (\w+)=(\S+)$", _dockerfile(), re.MULTILINE):
        declared.setdefault(name, []).append(value)
    for name in _PINS:
        assert len(declared.get(name, [])) == 1, f"{name} must have exactly one default"
    return {name: declared[name][0] for name in _PINS}


def _stage(dockerfile: str, name: str) -> str:
    match = re.search(
        rf"^FROM \S+ AS {name}\n(.*?)(?=^FROM |\Z)", dockerfile, re.MULTILINE | re.DOTALL
    )
    assert match is not None, f"Dockerfile must define the {name} stage"
    return match.group(1)


def _workflow_steps() -> Iterator[tuple[str, dict[str, Any]]]:
    for path in sorted(_WORKFLOWS.glob("*.yml")):
        for job_name, job in load_yaml(path).get("jobs", {}).items():
            for step in job.get("steps", []):
                yield f"{path.name}:{job_name}", step


def _last_user(stage: str) -> str:
    return re.findall(r"^USER (\S+)$", stage, re.MULTILINE)[-1]


def test_pins_are_declared_once_before_the_targets() -> None:
    dockerfile = _dockerfile()
    pins = _pins()
    first_target = dockerfile.index("FROM toolchain AS dev")

    for name, value in pins.items():
        assert re.fullmatch(r"\d+\.\d+\.\d+", value), f"{name}={value} is not an exact version"
        assert dockerfile.index(f"ARG {name}=") < first_target, f"{name} is declared in a target"
    assert re.findall(r"^FROM (\S+)(?: AS (\w+))?$", dockerfile, re.MULTILINE) == [
        ("ghcr.io/astral-sh/uv:${UV_VERSION}", "uv"),
        ("node:${NODE_VERSION}-bookworm", "node"),
        ("rust:${RUST_VERSION}-bookworm", "toolchain"),
        ("toolchain", "dev"),
        ("toolchain", "user"),
    ]


def test_every_pin_is_used() -> None:
    dockerfile = _dockerfile()

    for name in _PINS:
        assert f"${{{name}}}" in dockerfile, f"{name} is declared but never used"
    for target in ("dev", "user"):
        stage = _stage(dockerfile, target)
        redeclared = [name for name in _PINS if re.search(rf"^ARG {name}\b", stage, re.MULTILINE)]
        assert not redeclared, f"the {target} stage redeclares {redeclared}"


def test_ci_setup_steps_install_the_dockerfile_pins() -> None:
    pins = _pins()
    found: set[str] = set()
    mismatches: list[str] = []

    for location, step in _workflow_steps():
        uses = str(step.get("uses", ""))
        for prefix, (pin, input_name) in _SETUP_ACTION_PINS.items():
            if uses.startswith(prefix):
                found.add(pin)
                actual = str(step.get("with", {}).get(input_name))
                if actual != pins[pin]:
                    mismatches.append(f"{location} {prefix} {input_name}={actual} != {pin}")
        if "BurntSushi/ripgrep" in str(step.get("run", "")):
            found.add("RIPGREP_VERSION")
            actual = str(step.get("env", {}).get("RG_VERSION"))
            if actual != pins["RIPGREP_VERSION"]:
                mismatches.append(f"{location} RG_VERSION={actual} != RIPGREP_VERSION")

    assert not mismatches, "\n".join(mismatches)
    assert found == set(_PINS) - _DOCKERFILE_ONLY_PINS - set(_AGENT_VERSION_LITERALS)


def test_agent_cli_versions_match_the_dockerfile_pins() -> None:
    pins = _pins()
    found: set[str] = set()
    mismatches: list[str] = []

    for path in [*sorted(_WORKFLOWS.glob("*.yml")), _TASKFILE]:
        text = path.read_text(encoding="utf-8")
        for pin, pattern in _AGENT_VERSION_LITERALS.items():
            for match in pattern.finditer(text):
                found.add(pin)
                if match.group(1) != pins[pin]:
                    line = text.count("\n", 0, match.start()) + 1
                    mismatches.append(f"{path.name}:{line} {match.group(0)!r} != {pin}")

    assert not mismatches, "\n".join(mismatches)
    assert found == set(_AGENT_VERSION_LITERALS)


def test_code_constants_require_the_dockerfile_pins() -> None:
    pins = _pins()

    assert CLAUDE_CODE_CAPABILITIES.min_version == pins["CLAUDE_VERSION"]
    assert CODEX_CLI_MIN_VERSION == pins["CODEX_VERSION"]


def test_dev_target_is_archive_buildable_locked_and_non_root() -> None:
    dockerfile = _dockerfile()
    toolchain = _stage(dockerfile, "toolchain")
    dev = _stage(dockerfile, "dev")

    assert "COPY --chown=verifier:verifier . /workspace" in dev
    assert "uv sync --locked --extra dev" in dev
    assert "UV_PROJECT_ENVIRONMENT=/workspace/.venv" in dev
    assert "PATH=/workspace/.venv/bin:" in dev
    assert "/workspace/.autoskillit/temp" in dev
    assert "task regen-contracts" in dev
    assert "--mount=from=source_history,source=source.bundle,target=/source.bundle" in dev
    assert "git fetch /source.bundle HEAD" in dev
    assert 'git reset --mixed "${SOURCE_SHA}"' in dev
    assert 'test "$(git rev-parse HEAD)" = "${SOURCE_SHA}"' in dev
    assert "COPY .git" not in dev
    assert _last_user(dev) == "verifier"
    assert "uv venv /opt/pre-commit" in toolchain
    assert "https://github.com/BurntSushi/ripgrep/releases/download/" in toolchain
    assert "https://github.com/jqlang/jq/releases/download/" in toolchain
    assert "https://github.com/cli/cli/releases/download/" in toolchain
    assert "uv pip install --system" not in dockerfile


def test_user_target_installs_the_archived_source_like_a_release() -> None:
    user = _stage(_dockerfile(), "user")

    assert "uv tool install" in user
    assert "--mount=type=bind" in user
    for forbidden in ("github.com/", "UV_TOOL_DIR", "UV_TOOL_BIN_DIR", "--mount=type=secret"):
        assert forbidden not in user, f"the user target must not use {forbidden}"
    assert _last_user(user) == "autoskillit"


def test_build_credentials_are_secret_mounted_only() -> None:
    dockerfile = _dockerfile()
    secret_mount = "--mount=type=secret,id=github_token"

    assert secret_mount in _stage(dockerfile, "dev")
    assert dockerfile.count(secret_mount) == _stage(dockerfile, "dev").count(secret_mount)
    assert not re.search(r"^ARG .*?(?:TOKEN|KEY|PASSWORD|CREDENTIAL)", dockerfile, re.MULTILINE)
    assert not re.search(
        r"^COPY .*?(?:\.codex|\.claude|credentials|auth\.json|hosts\.yml|secrets)",
        dockerfile,
        re.MULTILINE,
    )
