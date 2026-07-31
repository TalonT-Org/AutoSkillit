"""CLI test fixtures — shared across tests/cli/*.

Auto-patches the worktree guard so tests that call sync_hooks_to_settings()
or _register_all() can run from git worktrees (e.g. during task install-worktree
development). Tests that explicitly test the worktree guard monkeypatch
is_git_worktree to True themselves.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _patch_worktree_guard_for_hooks(monkeypatch: pytest.MonkeyPatch) -> None:
    """Prevent the worktree guard and plugin-installed check from firing in tests."""
    import autoskillit.cli._hooks as _hooks_mod
    import autoskillit.cli._marketplace as _mkt_mod
    import autoskillit.core.paths as _core_paths

    monkeypatch.setattr(_hooks_mod, "is_git_worktree", lambda path: False)
    monkeypatch.setattr(_core_paths, "is_git_worktree", lambda path: False)
    monkeypatch.setattr(_mkt_mod, "is_git_worktree", lambda path: False)
    monkeypatch.setattr(
        "autoskillit.cli._init_helpers._is_plugin_installed", lambda **kwargs: False
    )


@pytest.fixture(autouse=True)
def _stub_detect_mcp_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub detect_autoskillit_mcp_prefix for deterministic PLUGIN_DIR behavior."""
    from autoskillit.core._plugin_ids import DIRECT_PREFIX

    monkeypatch.setattr(
        "autoskillit.core.detect_autoskillit_mcp_prefix",
        lambda _capabilities: DIRECT_PREFIX,
    )


@pytest.fixture
def _stub_interactive_prelaunch(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Bind legacy CLI tests to hermetic executables below the strict probe boundary."""
    import autoskillit.cli.session._session_launch as _session_launch
    from autoskillit.core import (
        ExecutableLaunchBinding,
        resolve_executable_launch_binding,
    )
    from autoskillit.execution.backends.claude import ClaudeCodeBackend
    from autoskillit.execution.backends.codex import CodexBackend

    binary_dir = tmp_path / "interactive-binaries"
    binary_dir.mkdir()
    binaries: dict[str, Path] = {}
    for name in ("claude", "codex"):
        binary = binary_dir / name
        binary.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        binary.chmod(0o755)
        binaries[name] = binary

    monkeypatch.setattr(
        shutil,
        "which",
        lambda name, **_kwargs: str(binaries[name]) if name in binaries else None,
    )

    def resolve_binding(
        *,
        binary_name: str,
        environment: dict[str, str],
        cwd: Path,
        explicit_path_env: str | None = None,
    ) -> ExecutableLaunchBinding:
        del explicit_path_env
        if shutil.which(binary_name) is None:
            raise ValueError(f"'{binary_name}' not found in the effective PATH")
        with pytest.MonkeyPatch.context() as binding_patch:
            binding_patch.setattr(
                shutil,
                "which",
                lambda name, **_kwargs: str(binaries[name]) if name in binaries else None,
            )
            return resolve_executable_launch_binding(
                binary_name=binary_name,
                environment=environment,
                cwd=cwd,
            )

    monkeypatch.setattr(_session_launch, "resolve_executable_launch_binding", resolve_binding)
    monkeypatch.setattr(
        ClaudeCodeBackend,
        "ensure_pre_launch",
        lambda _self, *, session_dir=None, executable=None: [],
    )
    monkeypatch.setattr(
        CodexBackend,
        "ensure_pre_launch",
        lambda _self, *, session_dir=None, executable=None: [],
    )


_SCRIPT_YAML = """\
name: test-script
description: A test script
summary: Test flow
ingredients:
  target:
    description: Target path
    required: true
steps:
  do-something:
    tool: run_cmd
    with:
      cmd: echo hello
    on_success: done
    on_failure: done
  done:
    action: stop
    message: Finished
kitchen_rules:
  - Only use AutoSkillit MCP tools during pipeline execution
"""

_GITHUB_RECIPE_YAML = """\
name: github-recipe
description: A recipe using github tools
summary: Fetch an issue
steps:
  fetch:
    tool: fetch_github_issue
    with:
      issue_url: https://github.com/example/repo/issues/1
    on_success: done
    on_failure: done
  done:
    action: stop
    message: Done
kitchen_rules:
  - Only use AutoSkillit MCP tools during pipeline execution
"""


@pytest.fixture(autouse=True)
def _fleet_config(
    request: pytest.FixtureRequest, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ensure .autoskillit/config.yaml enables fleet so _require_fleet passes.

    Only activates for tests carrying pytest.mark.feature("fleet").
    """
    marker = request.node.get_closest_marker("feature")
    if marker is None or "fleet" not in marker.args:
        return
    cfg_dir = tmp_path / ".autoskillit"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    cfg_file = cfg_dir / "config.yaml"
    if not cfg_file.exists():
        cfg_file.write_text("features:\n  fleet: true\n")
    monkeypatch.chdir(tmp_path)
