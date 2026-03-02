from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).parent.parent.parent
TASKFILE = REPO_ROOT / "Taskfile.yml"


class TestTaskfile:
    def _load(self) -> dict:
        return yaml.safe_load(TASKFILE.read_text())

    def test_install_worktree_has_status_block(self):
        """T1 — install-worktree has a status: block with at least two entries."""
        data = self._load()
        task = data["tasks"]["install-worktree"]
        assert "status" in task, "install-worktree must have a status: block"
        assert len(task["status"]) >= 2, "status: block must have at least two entries"

    def test_install_worktree_in_test_all_deps(self):
        """T2 — install-worktree is listed in test-all deps."""
        data = self._load()
        deps = data["tasks"]["test-all"].get("deps", [])
        assert "install-worktree" in deps, "test-all.deps must include install-worktree"

    def test_install_worktree_in_test_check_deps(self):
        """T3 — install-worktree is listed in test-check deps."""
        data = self._load()
        deps = data["tasks"]["test-check"].get("deps", [])
        assert "install-worktree" in deps, "test-check.deps must include install-worktree"

    def test_status_uses_local_venv_paths_only(self):
        """T4 — status: sentinels use only local relative paths (no absolute/home paths)."""
        data = self._load()
        status_cmds = data["tasks"]["install-worktree"]["status"]
        for cmd in status_cmds:
            assert not cmd.startswith("/"), f"status cmd must not use absolute path: {cmd!r}"
            assert "~" not in cmd, f"status cmd must not reference home dir: {cmd!r}"
            assert "/home/" not in cmd, f"status cmd must not hardcode /home/: {cmd!r}"

    def test_status_uses_uv_sync_check(self):
        """T5 — at least one status: command uses uv sync --check for staleness detection."""
        data = self._load()
        status_cmds = data["tasks"]["install-worktree"]["status"]
        assert any("uv sync --check" in cmd for cmd in status_cmds), (
            "status: block must contain a 'uv sync --check' command"
        )
