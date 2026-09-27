"""Structural contract: one pinned mypy configuration is the typecheck authority.

Every suppression syntax the repository uses must be validated by the checker
that honors it. These tests pin the mypy dependency, its single ``[tool.mypy]``
configuration, the ``task typecheck`` entry point and every gate that runs it,
plus the ruff and Pyright settings that keep their suppressions honest.
"""

from __future__ import annotations

import tomllib
from pathlib import Path, PurePosixPath
from typing import Any

import pytest

from autoskillit.core.io import load_yaml

pytestmark = [pytest.mark.layer("infra"), pytest.mark.small]

REPO_ROOT = Path(__file__).resolve().parents[2]
_HOOKS_ROOT = PurePosixPath("src/autoskillit/hooks")
_CONFIG_OWNED_MYPY_OPTIONS = (
    "--ignore-missing-imports",
    "--enable-error-code",
    "--python-version",
    "--warn-unused-ignores",
)
_STALENESS_REOPENING_KEYS = ("disable_error_code", "ignore_errors")


def _pyproject() -> dict[str, Any]:
    return tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def _mypy_config() -> dict[str, Any]:
    config = _pyproject().get("tool", {}).get("mypy")
    assert isinstance(config, dict), "pyproject.toml must define [tool.mypy]"
    return config


def _pyright_config() -> dict[str, Any]:
    config = _pyproject().get("tool", {}).get("pyright")
    assert isinstance(config, dict), "pyproject.toml must define [tool.pyright]"
    return config


def _tasks() -> dict[str, Any]:
    return load_yaml(REPO_ROOT / "Taskfile.yml")["tasks"]


def _task_script(name: str) -> str:
    return "\n".join(str(cmd) for cmd in _tasks()[name]["cmds"])


def _as_list(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    return list(value) if isinstance(value, list) else []


def _contains(root: PurePosixPath, path: PurePosixPath) -> bool:
    return path.is_relative_to(root)


def test_mypy_is_a_pinned_dev_dependency() -> None:
    dev = _pyproject()["project"]["optional-dependencies"]["dev"]
    assert any(req.startswith("mypy>=") for req in dev), dev
    assert any(req.startswith("types-PyYAML>=") for req in dev), dev


def test_mypy_config_is_the_single_authority() -> None:
    config = _mypy_config()
    floor = _pyproject()["project"]["requires-python"].removeprefix(">=")
    assert config.get("python_version") == floor
    assert (REPO_ROOT / ".python-version").read_text(encoding="utf-8").strip() == floor
    assert config.get("warn_unused_ignores") is True
    assert {"exhaustive-match", "ignore-without-code"} <= set(config.get("enable_error_code", []))
    assert config.get("files"), "[tool.mypy].files must name the checked scope"

    overrides: list[dict[str, Any]] = config.get("overrides", [])
    first_party = [
        override
        for override in overrides
        if {"autoskillit", "autoskillit.*"} <= set(_as_list(override.get("module")))
    ]
    assert any(override.get("ignore_missing_imports") is False for override in first_party), (
        "a [[tool.mypy.overrides]] entry covering autoskillit and autoskillit.* must set "
        "ignore_missing_imports = false so a broken first-party import is never silenced"
    )

    for section in (config, *overrides):
        for key in _STALENESS_REOPENING_KEYS:
            assert key not in section, f"{key} would silently re-open the staleness hole"
        assert section.get("warn_unused_ignores", True) is True, (
            "warn_unused_ignores = false would silently re-open the staleness hole"
        )


def test_typecheck_task_covers_every_platform_target() -> None:
    cmds = [str(cmd) for cmd in _tasks()["typecheck"]["cmds"]]
    mypy_cmds = [cmd for cmd in cmds if ".venv/bin/mypy" in cmd]
    for platform in ("linux", "darwin"):
        matching = [cmd for cmd in mypy_cmds if f"--platform {platform}" in cmd]
        assert len(matching) == 1, f"expected one .venv/bin/mypy run for {platform}: {cmds}"
    assert len(mypy_cmds) == 2, cmds
    for cmd in cmds:
        for option in _CONFIG_OWNED_MYPY_OPTIONS:
            assert option not in cmd, f"{option} belongs in [tool.mypy], not in {cmd!r}"


def test_every_gate_runs_typecheck() -> None:
    assert "task typecheck" in _task_script("test-all")

    precommit = load_yaml(REPO_ROOT / ".pre-commit-config.yaml")
    mypy_hooks = [
        hook
        for repo in precommit["repos"]
        for hook in repo.get("hooks", [])
        if hook.get("id") == "mypy"
    ]
    assert len(mypy_hooks) == 1
    assert mypy_hooks[0]["entry"] == "task typecheck"
    assert mypy_hooks[0].get("pass_filenames") is False

    workflow = load_yaml(REPO_ROOT / ".github" / "workflows" / "tests.yml")
    steps: list[dict[str, Any]] = workflow["jobs"]["test"]["steps"]
    typecheck_idx = [i for i, step in enumerate(steps) if step.get("run") == "task typecheck"]
    assert len(typecheck_idx) == 1, "the test job must run `task typecheck` exactly once"
    install_idx = next(
        i for i, step in enumerate(steps) if step.get("name") == "Install dependencies"
    )
    setup_task_idx = next(
        i
        for i, step in enumerate(steps)
        if str(step.get("uses", "")).startswith("arduino/setup-task")
    )
    assert setup_task_idx < typecheck_idx[0]
    assert install_idx < typecheck_idx[0]


def test_test_all_propagates_typecheck_failure() -> None:
    script = _task_script("test-all")
    sequence = (
        "set +e",
        "task typecheck",
        "TYPECHECK_EXIT=$?",
        "$PYTEST_CMD",
        "PYTEST_EXIT=$?",
        'if [ "$TYPECHECK_EXIT" -ne 0 ]',
        "exit $PYTEST_EXIT",
    )
    last_idx = -1
    for fragment in sequence:
        idx = script.find(fragment, last_idx + 1)
        assert idx > last_idx, (
            f"expected {fragment!r} after the previous fragment in test-all; found at {idx}"
        )
        last_idx = idx

    typecheck_idx = script.index("task typecheck")
    assert script.rfind("set +e", 0, typecheck_idx) > script.rfind("set -e", 0, typecheck_idx), (
        "task typecheck must run with errexit off so pytest still runs after a type error"
    )


def test_verification_container_uses_project_mypy() -> None:
    dockerfile = (REPO_ROOT / "scripts" / "docker" / "verification" / "Dockerfile").read_text(
        encoding="utf-8"
    )
    assert "MYPY_VERSION" not in dockerfile
    assert "TYPES_PYYAML_VERSION" not in dockerfile
    assert "/usr/local/bin/mypy" not in dockerfile


def test_ruff_rejects_unused_noqa() -> None:
    lint = _pyproject()["tool"]["ruff"]["lint"]
    assert "RUF100" in [*lint.get("select", []), *lint.get("extend-select", [])]


def test_pyright_models_hook_boundary_like_mypy() -> None:
    pyright = _pyright_config()
    assert pyright.get("pythonVersion") == _mypy_config()["python_version"]
    assert "reportMissingImports" not in pyright

    envs: list[dict[str, Any]] = pyright.get("executionEnvironments", [])
    hook_indices = [
        i for i, env in enumerate(envs) if PurePosixPath(env.get("root", "")) == _HOOKS_ROOT
    ]
    assert len(hook_indices) == 1, "exactly one execution environment must own the hooks root"
    hook_idx = hook_indices[0]
    assert envs[hook_idx].get("reportMissingImports") == "none"
    for i, env in enumerate(envs):
        if i != hook_idx:
            assert "reportMissingImports" not in env, env
    for env in envs[:hook_idx]:
        assert not _contains(PurePosixPath(env.get("root", ".")), _HOOKS_ROOT), (
            f"{env} precedes the hooks environment and would shadow it"
        )
    for extra in _as_list(pyright.get("extraPaths")):
        assert not _contains(_HOOKS_ROOT, PurePosixPath(extra)), (
            f"extraPaths entry {extra!r} resolves hook modules under a second identity"
        )
