"""CLI test fixtures — shared across tests/cli/*.

Auto-patches the worktree guard so tests that call sync_hooks_to_settings()
or _register_all() can run from git worktrees (e.g. during task install-worktree
development). Tests that explicitly test the worktree guard monkeypatch
is_git_worktree to True themselves.
"""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _patch_worktree_guard_for_hooks(monkeypatch: pytest.MonkeyPatch) -> None:
    """Prevent the worktree guard from firing in tests running inside a worktree."""
    import autoskillit.cli._hooks as _hooks_mod
    import autoskillit.cli._marketplace as _mkt_mod
    import autoskillit.core.paths as _core_paths

    monkeypatch.setattr(_hooks_mod, "is_git_worktree", lambda path: False)
    monkeypatch.setattr(_core_paths, "is_git_worktree", lambda path: False)
    monkeypatch.setattr(_mkt_mod, "is_git_worktree", lambda path: False)


@pytest.fixture(autouse=True)
def _stub_detect_mcp_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub detect_autoskillit_mcp_prefix for deterministic PLUGIN_DIR behavior."""
    from autoskillit.core._plugin_ids import DIRECT_PREFIX

    monkeypatch.setattr("autoskillit.core.detect_autoskillit_mcp_prefix", lambda: DIRECT_PREFIX)


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
