"""Deployment topologies for executing rendered hook commands end to end.

``projection_shaped_hook_root`` is the single test-side source of per-session
Codex hook roots. Writers and oracles receive ``root.plugin_dir``; path
assertions compare against ``root.plugin_dir``/``root.hooks_dir`` (both
symlink-free), never against a raw ``tmp_path``-derived path, because ``tmp_path``
lies under a symlink on macOS.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from autoskillit.core import (
    _InstallLock,
    generation_plugin_selector_path,
    managed_home_for,
    pkg_root,
)
from autoskillit.execution.backends._codex_config import _read_codex_config
from autoskillit.execution.backends._codex_hooks import (
    CodexHookCommand,
    is_autoskillit_hook_command,
    iter_codex_hook_commands,
)
from tests.conftest import production_interpreter_env

if TYPE_CHECKING:
    from autoskillit.core import SessionHookRoot

_PLUGIN_REF = "autoskillit"
_COPY_IGNORE = shutil.ignore_patterns("__pycache__", "*.py[co]")
_DEGRADED_DISPATCH_EVENTS = frozenset({"unknown_target", "retired_target_missing", "exec_failure"})


def build_generation_selector_topology(home: Path, version: str = "1.0.0") -> Path:
    """Publish the full plugin tree as a generation and return the plugin-level selector."""
    from autoskillit.workspace import publish_generation

    source_root = home / ".generation-source" / version
    shutil.copytree(pkg_root(), source_root, symlinks=False, ignore=_COPY_IGNORE)
    managed = managed_home_for(home)
    with _InstallLock(managed):
        publish_generation(
            home=managed,
            plugin_ref=_PLUGIN_REF,
            version=version,
            semantic_key=f"autoskillit@autoskillit-local:{version}",
            source_root=source_root,
        )
    return generation_plugin_selector_path(home, _PLUGIN_REF)


def projection_shaped_hook_root(home: Path, key: str = "test") -> SessionHookRoot:
    """Copy the full plugin tree under the projections root and describe it as a session root."""
    from autoskillit.core import SessionHookRoot

    artifact_path = home / ".autoskillit" / "plugin-projections" / key
    if not artifact_path.exists():
        shutil.copytree(pkg_root(), artifact_path, symlinks=False, ignore=_COPY_IGNORE)
    return SessionHookRoot(
        artifact_path=artifact_path,
        plugin_dir=Path(os.path.realpath(artifact_path)),
        semantic_key=key,
    )


def fake_projected_plugin_root(home: Path, key: str = "fake-plugin-artifact") -> Path:
    """Return a projection-shaped plugin root for fake authorities, holding only the dispatcher.

    Codex renders per-session hook commands from a binding's plugin root, so the
    root must sit under a projections root and contain ``hooks/_dispatch.py``.
    Tests that execute the rendered hooks use ``projection_shaped_hook_root``.
    """
    plugin_dir = home / ".autoskillit" / "plugin-projections" / key
    hooks_dir = plugin_dir / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(pkg_root() / "hooks" / "_dispatch.py", hooks_dir / "_dispatch.py")
    return plugin_dir


_CANDIDATE_TOOLS: tuple[tuple[str, dict[str, object]], ...] = (
    ("Bash", {"command": "echo hi"}),
    ("Write", {"file_path": "benign.txt", "content": "x"}),
    ("Read", {"file_path": "benign.txt"}),
    ("Agent", {"prompt": "x"}),
    ("apply_patch", {"input": ""}),
    ("mcp__autoskillit__run_skill", {}),
    ("mcp__autoskillit__run_cmd", {"cmd": "echo hi"}),
    ("mcp__autoskillit__open_kitchen", {}),
    ("mcp__autoskillit__remove_clone", {}),
    ("mcp__autoskillit__merge_worktree", {}),
    ("mcp__autoskillit__push_to_remote", {}),
    ("mcp__autoskillit__wait_for_ci", {}),
    ("mcp__autoskillit__close_kitchen", {}),
    ("mcp__autoskillit__dispatch_food_truck", {}),
    ("mcp__autoskillit__reset_dispatch", {}),
    ("mcp__autoskillit__kitchen_status", {}),
)


def _benign_tool(matcher: str | None) -> tuple[str, dict[str, object]]:
    if not matcher or matcher == ".*":
        return _CANDIDATE_TOOLS[0]
    for name, tool_input in _CANDIDATE_TOOLS:
        if re.fullmatch(matcher, name):
            return name, tool_input
    raise AssertionError(f"no benign tool name satisfies matcher {matcher!r}")


def benign_event_payload(hook: CodexHookCommand, cwd: Path) -> dict[str, object]:
    payload: dict[str, object] = {
        "hook_event_name": hook.event,
        "session_id": "test-session-id",
        "cwd": str(cwd),
    }
    if hook.event in {"PreToolUse", "PostToolUse", "PostToolUseFailure"}:
        tool_name, tool_input = _benign_tool(hook.matcher)
        payload["tool_name"] = tool_name
        payload["tool_input"] = dict(tool_input)
        if hook.event != "PreToolUse":
            payload["tool_response"] = {}
    elif hook.event == "PreCompact":
        payload["trigger"] = hook.matcher or "auto"
    return payload


def codex_command_invocations(
    config_path: Path, cwd: Path
) -> list[tuple[CodexHookCommand, dict[str, object]]]:
    """Pair every AutoSkillit command of a rendered config with a benign payload."""
    result = _read_codex_config(config_path)
    assert not result.is_corrupt, f"rendered config is corrupt: {config_path}"
    return [
        (hook, benign_event_payload(hook, cwd))
        for hook in iter_codex_hook_commands(result.data.get("hooks"))
        if is_autoskillit_hook_command(hook.command)
    ]


def rendered_invocations(
    hooks_table: dict[str, list[dict]], cwd: Path
) -> list[tuple[CodexHookCommand, dict[str, object]]]:
    """Pair every command of an in-memory hooks table with a benign payload."""
    return [
        (hook, benign_event_payload(hook, cwd)) for hook in iter_codex_hook_commands(hooks_table)
    ]


@dataclass(frozen=True)
class HookRun:
    completed: subprocess.CompletedProcess[str]
    degraded_dispatch: tuple[dict[str, object], ...]


def _dispatch_diagnostics(log_dir: Path) -> Iterator[dict[str, object]]:
    for path in log_dir.rglob("hook_dispatch_diagnostics.jsonl"):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                yield json.loads(line)


def run_codex_hook(
    command: str,
    payload: dict[str, object],
    *,
    cwd: Path,
    log_dir: Path,
    env: dict[str, str] | None = None,
) -> HookRun:
    """Execute one rendered command the way Codex does and report dispatch degradation."""
    parts = shlex.split(command)
    if parts[0] == "python3":
        parts[0] = sys.executable
    run_env = production_interpreter_env()
    run_env["AUTOSKILLIT_LOG_DIR"] = str(log_dir)
    run_env.update(env or {})
    log_dir.mkdir(parents=True, exist_ok=True)
    before = sum(1 for _ in _dispatch_diagnostics(log_dir))
    completed = subprocess.run(
        parts,
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        cwd=cwd,
        env=run_env,
        timeout=30,
    )
    records = list(_dispatch_diagnostics(log_dir))[before:]
    degraded = tuple(
        record for record in records if record.get("event_kind") in _DEGRADED_DISPATCH_EVENTS
    )
    return HookRun(completed=completed, degraded_dispatch=degraded)


def hook_run_failures(run: HookRun) -> list[str]:
    """Return every way one hook execution violates the topology contract."""
    failures: list[str] = []
    completed = run.completed
    if completed.returncode != 0:
        failures.append(f"exit {completed.returncode}: {completed.stderr.strip()[-2000:]}")
    stdout = completed.stdout.strip()
    if stdout:
        try:
            json.loads(stdout)
        except json.JSONDecodeError:
            failures.append(f"stdout is not JSON: {stdout[:500]}")
    if "scope_authority_unavailable" in completed.stdout + completed.stderr:
        failures.append("scope authority denied the hook's own identity")
    if run.degraded_dispatch:
        failures.append(f"dispatch degraded: {run.degraded_dispatch}")
    return failures
