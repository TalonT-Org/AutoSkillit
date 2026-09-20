"""Real-subprocess coverage for the installation integrity guard."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from tests.conftest import production_interpreter_env

pytestmark = [pytest.mark.layer("hooks"), pytest.mark.small]

SCRIPT = (
    Path(__file__).resolve().parents[2]
    / "src/autoskillit/hooks/guards/installation_integrity_guard.py"
)


def _run(event: object, *, env: dict[str, str] | None = None) -> tuple[int, str]:
    child_env = production_interpreter_env()
    for key in tuple(child_env):
        if key in {"AUTOSKILLIT_HEADLESS", "AUTOSKILLIT_AGENT_BACKEND"} or key.startswith(
            "AUTOSKILLIT_ALLOWED_WRITE_PREFIX"
        ):
            child_env.pop(key)
    if env:
        child_env.update(env)
    result = subprocess.run(
        [sys.executable, str(SCRIPT)],
        input=json.dumps(event),
        capture_output=True,
        text=True,
        env=child_env,
    )
    return result.returncode, result.stdout


def _decision(stdout: str) -> str:
    return json.loads(stdout)["hookSpecificOutput"]["permissionDecision"]


def _bash(command: str) -> dict[str, object]:
    return {"tool_name": "Bash", "tool_input": {"command": command}}


@pytest.mark.parametrize(
    "command_template",
    [
        "cat > {target} <<'PYEOF'\nPYEOF",
        "cp /tmp/source {target}",
        "mv /tmp/source {target}",
        "patch -i {target}",
        "tee {target} </dev/null",
        "sed -i 's/a/b/' {target}",
        "install /tmp/source {target}",
    ],
)
def test_blocks_install_tree_bash_writes(tmp_path: Path, command_template: str) -> None:
    target = tmp_path / "lib/python3.13/site-packages/autoskillit/__init__.py"
    target.parent.mkdir(parents=True)
    code, stdout = _run(_bash(command_template.format(target=target)))

    assert code == 0
    assert _decision(stdout) == "deny"


@pytest.mark.parametrize("tool_name", ["Write", "Edit"])
def test_blocks_install_tree_direct_writes(tmp_path: Path, tool_name: str) -> None:
    target = tmp_path / "lib/python3.13/site-packages/autoskillit/__init__.py"
    code, stdout = _run({"tool_name": tool_name, "tool_input": {"file_path": str(target)}})

    assert code == 0
    assert _decision(stdout) == "deny"


def test_blocks_install_tree_apply_patch(tmp_path: Path) -> None:
    target = tmp_path / "lib/python3.13/site-packages/autoskillit/__init__.py"
    patch = f"*** Begin Patch\n*** Update File: {target}\n@@\n+x\n*** End Patch"
    code, stdout = _run({"tool_name": "apply_patch", "tool_input": {"command": patch}})

    assert code == 0
    assert _decision(stdout) == "deny"


def test_blocks_shell_local_variable_target(tmp_path: Path) -> None:
    target = tmp_path / "lib/python3.13/site-packages/autoskillit/__init__.py"
    code, stdout = _run(_bash(f"FOO={target.parent}; cat > $FOO/__init__.py <<'EOF'\nEOF"))

    assert code == 0
    assert _decision(stdout) == "deny"


def test_blocks_symlinked_install_target(tmp_path: Path) -> None:
    package = tmp_path / "lib/python3.13/site-packages/autoskillit"
    package.mkdir(parents=True)
    linked = tmp_path / "linked-package"
    linked.symlink_to(package, target_is_directory=True)
    code, stdout = _run(_bash(f"cat > {linked}/new.py <<'EOF'\nEOF"))

    assert code == 0
    assert _decision(stdout) == "deny"


def test_blocks_relative_target_after_cd_into_install_tree(tmp_path: Path) -> None:
    package = tmp_path / "lib/python3.13/site-packages/autoskillit"
    package.mkdir(parents=True)
    code, stdout = _run(_bash(f"cd {package} && cat > __init__.py <<'EOF'\nEOF"))

    assert code == 0
    assert _decision(stdout) == "deny"


def test_blocks_normalized_install_target(tmp_path: Path) -> None:
    target = tmp_path / "lib/python3.13/site-packages/../site-packages/autoskillit/__init__.py"
    code, stdout = _run(_bash(f"cat > {target} <<'EOF'\nEOF"))

    assert code == 0
    assert _decision(stdout) == "deny"


def test_allows_non_install_writes_and_reads(tmp_path: Path) -> None:
    project_file = tmp_path / "project/src/autoskillit/__init__.py"
    package_file = tmp_path / "lib/python3.13/site-packages/autoskillit/__init__.py"
    code, stdout = _run(_bash(f"cat > {project_file} <<'EOF'\nEOF"))

    assert code == 0
    assert stdout == ""

    code, stdout = _run(_bash(f"cat {package_file}"))

    assert code == 0
    assert stdout == ""


def test_allow_non_install_direct_write(tmp_path: Path) -> None:
    code, stdout = _run(
        {"tool_name": "Write", "tool_input": {"file_path": str(tmp_path / "project.py")}}
    )

    assert code == 0
    assert stdout == ""


@pytest.mark.parametrize("command", ["uv tool install autoskillit", "pip install -e ."])
def test_allows_install_commands_without_a_write_target(command: str) -> None:
    code, stdout = _run(_bash(command))

    assert code == 0
    assert stdout == ""


def test_applies_to_codex_and_headless(tmp_path: Path) -> None:
    target = tmp_path / "lib/python3.13/site-packages/autoskillit/__init__.py"
    event = _bash(f"cat > {target} <<'EOF'\nEOF")

    for env in ({"AUTOSKILLIT_AGENT_BACKEND": "codex"}, {"AUTOSKILLIT_HEADLESS": "1"}):
        code, stdout = _run(event, env=env)
        assert code == 0
        assert _decision(stdout) == "deny"
