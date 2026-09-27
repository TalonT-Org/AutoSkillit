"""Structural contract: one locked ruff binary is the lint authority.

``task lint`` runs the ``uv.lock``-pinned ``.venv/bin/ruff`` over ruff's own file
population. These tests pin the dependency, the task's shape and failure output, every
gate that runs it, and the absence of any other ruff identity in gate commands.
"""

from __future__ import annotations

import re
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any

import pytest

from autoskillit.core.io import load_yaml

pytestmark = [pytest.mark.layer("infra"), pytest.mark.small]

REPO_ROOT = Path(__file__).resolve().parents[2]
_WORKFLOWS_DIR = REPO_ROOT / ".github" / "workflows"
_LOCKED_RUFF_GATE = re.compile(
    r"\.venv/bin/ruff (check --output-format=concise|format --check)\s*$", re.MULTILINE
)
_UNLOCKED_RUFF = re.compile(r"(?<![\w./-])ruff\b")
_ENABLE_ERREXIT = re.compile(r"(?m)^\s*set -e\s*$")
_REMEDIATION = ".venv/bin/ruff check --fix"
_STUB_NAMES = ("Alpha", "Beta", "launch_digest")
_MODULE_SOURCE = """\
class Alpha:
    pass


class Beta:
    pass


def launch_digest() -> None:
    pass
"""


def _pyproject() -> dict[str, Any]:
    return tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def _tasks() -> dict[str, Any]:
    return load_yaml(REPO_ROOT / "Taskfile.yml")["tasks"]


def _task_script(name: str) -> str:
    return "\n".join(str(cmd) for cmd in _tasks()[name]["cmds"])


def _precommit_hooks() -> dict[str, dict[str, Any]]:
    config = load_yaml(REPO_ROOT / ".pre-commit-config.yaml")
    return {hook["id"]: hook for repo in config["repos"] for hook in repo.get("hooks", [])}


def _workflow_run_commands() -> list[tuple[str, str]]:
    commands: list[tuple[str, str]] = []
    for workflow_path in sorted(_WORKFLOWS_DIR.glob("*.yml")):
        workflow = load_yaml(workflow_path)
        for job in workflow.get("jobs", {}).values():
            commands.extend(
                (workflow_path.name, step["run"])
                for step in job.get("steps", [])
                if isinstance(step.get("run"), str)
            )
    return commands


def _task_commands() -> list[tuple[str, str]]:
    commands: list[tuple[str, str]] = []
    for task_name, task in _tasks().items():
        for cmd in task.get("cmds", []):
            text = cmd.get("cmd") if isinstance(cmd, dict) else cmd
            if isinstance(text, str):
                commands.append((f"Taskfile.yml:{task_name}", text))
    return commands


def _precommit_entries() -> list[tuple[str, str]]:
    return [
        (f".pre-commit-config.yaml:{hook_id}", hook["entry"])
        for hook_id, hook in _precommit_hooks().items()
        if "entry" in hook
    ]


def _stub_source(names: tuple[str, ...]) -> str:
    return "".join(f"from .mod import {name} as {name}\n" for name in names)


def _run_lint(cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", "-c", _task_script("lint")],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_ruff_is_a_locked_dev_dependency() -> None:
    dev = _pyproject()["project"]["optional-dependencies"]["dev"]
    assert any(req.startswith("ruff>=") for req in dev), dev
    lock = tomllib.loads((REPO_ROOT / "uv.lock").read_text(encoding="utf-8"))
    locked = [package for package in lock["package"] if package["name"] == "ruff"]
    assert len(locked) == 1 and locked[0].get("version"), locked


def test_lint_task_runs_locked_ruff_over_its_own_population() -> None:
    cmds = _tasks()["lint"]["cmds"]
    assert len(cmds) == 1 and isinstance(cmds[0], str), cmds
    script = cmds[0]
    assert sorted(_LOCKED_RUFF_GATE.findall(script)) == [
        "check --output-format=concise",
        "format --check",
    ], "task lint must run `.venv/bin/ruff check`/`format --check` with no path arguments"
    assert "{{" not in script, "task lint must be runnable verbatim, without templating"
    assert script.lstrip().startswith("set +e"), script
    enabled = _ENABLE_ERREXIT.search(script)
    remediation = script.index(_REMEDIATION)
    assert enabled is None or enabled.start() > remediation, (
        "the fix preview runs `ruff check --diff`, which exits 1 whenever a fix exists; "
        "errexit must stay off until the remediation line prints, or the preview aborts "
        "the gate before naming the fix"
    )


def test_every_gate_runs_lint() -> None:
    assert "task lint" in _task_script("test-all")

    workflow = load_yaml(_WORKFLOWS_DIR / "tests.yml")
    steps: list[dict[str, Any]] = workflow["jobs"]["test"]["steps"]
    lint_idx = [i for i, step in enumerate(steps) if step.get("run") == "task lint"]
    assert len(lint_idx) == 1, "the test job must run `task lint` exactly once"
    assert steps[lint_idx[0]].get("if") == "matrix.shard == 'execution'"
    install_idx = next(
        i for i, step in enumerate(steps) if step.get("name") == "Install dependencies"
    )
    setup_task_idx = next(
        i
        for i, step in enumerate(steps)
        if str(step.get("uses", "")).startswith("arduino/setup-task")
    )
    assert install_idx < lint_idx[0]
    assert setup_task_idx < lint_idx[0]

    preflight = workflow["jobs"]["preflight"]["steps"]
    assert not [step for step in preflight if "ruff" in step.get("run", "")], (
        "preflight has no locked environment; ruff runs through `task lint` in the test job"
    )

    hooks = _precommit_hooks()
    for hook_id in ("ruff", "ruff-format"):
        assert hooks[hook_id]["entry"].startswith(".venv/bin/ruff "), hooks[hook_id]


def test_no_unlocked_ruff_invocation() -> None:
    commands = [*_workflow_run_commands(), *_task_commands(), *_precommit_entries()]
    offenders = [
        f"{source}: {line.strip()}"
        for source, command in commands
        for line in command.splitlines()
        if _UNLOCKED_RUFF.search(line)
    ]
    assert not offenders, (
        "gate commands must run the uv.lock-pinned `.venv/bin/ruff`, never a bare, "
        "`uv run` or `uvx` ruff:\n" + "\n".join(offenders)
    )


def test_test_all_propagates_lint_failure() -> None:
    script = _task_script("test-all")
    sequence = (
        "set +e",
        "task lint",
        "LINT_EXIT=$?",
        "task typecheck",
        "$PYTEST_CMD",
        "PYTEST_EXIT=$?",
        'if [ "$LINT_EXIT" -ne 0 ]',
        'echo "LINT_EXIT_CODE=$LINT_EXIT"',
        "exit $PYTEST_EXIT",
    )
    last_idx = -1
    for fragment in sequence:
        idx = script.find(fragment, last_idx + 1)
        assert idx > last_idx, (
            f"expected {fragment!r} after the previous fragment in test-all; found at {idx}"
        )
        last_idx = idx

    lint_idx = script.index("task lint")
    assert script.rfind("set +e", 0, lint_idx) > script.rfind("set -e", 0, lint_idx), (
        "task lint must run with errexit off so pytest still runs after a lint error"
    )


@pytest.mark.medium
def test_lint_task_failure_output_is_actionable(tmp_path: Path) -> None:
    # The remaining tests are cheap structural-contract assertions that match
    # `test_typecheck_gate.py`'s granularity (module-level `pytest.mark.small`).
    (tmp_path / "pyproject.toml").write_text('[tool.ruff.lint]\nselect = ["I"]\n')
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "mod.py").write_text(_MODULE_SOURCE)
    stub = pkg / "__init__.pyi"
    stub.write_text(_stub_source(("Alpha", "launch_digest", "Beta")))
    locked_ruff = Path(sys.executable).parent / "ruff"
    assert locked_ruff.is_file(), f"no ruff co-located with {sys.executable}"
    venv_bin = tmp_path / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    (venv_bin / "ruff").symlink_to(locked_ruff)

    failing = _run_lint(tmp_path)
    output = failing.stdout + failing.stderr
    lines = set(output.splitlines())
    assert failing.returncode != 0, output
    assert "pkg/__init__.pyi" in output and "I001" in output, output
    assert any(
        f"-from .mod import {name} as {name}" in lines
        and f"+from .mod import {name} as {name}" in lines
        for name in _STUB_NAMES
    ), f"the fix preview must show the moved import:\n{output}"
    assert _REMEDIATION in output, output
    assert len(output) < 20_000, "the failure output must stay bounded"

    stub.write_text(_stub_source(_STUB_NAMES))
    passing = _run_lint(tmp_path)
    assert passing.returncode == 0, passing.stdout + passing.stderr
