from __future__ import annotations

import re
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

    def test_install_dev_task_exists(self):
        """TF-6 — install-dev task exists in Taskfile.yml."""
        data = self._load()
        assert "install-dev" in data["tasks"], "install-dev task missing from Taskfile.yml"

    def test_install_dev_task_uses_integration_branch(self):
        """TF-7 — install-dev installs from @integration and runs autoskillit install."""
        data = self._load()
        task = data["tasks"]["install-dev"]
        cmds = " ".join(str(c) for c in task.get("cmds", []))
        assert "@integration" in cmds, "install-dev must install from @integration branch"
        assert "autoskillit install" in cmds, "install-dev must run autoskillit install after uv"

    def test_vendor_mermaid_task_exists(self) -> None:
        """REQ-R741-A02 — vendor-mermaid task must be declared in Taskfile.yml."""
        data = self._load()
        assert "vendor-mermaid" in data["tasks"], "vendor-mermaid task missing from Taskfile.yml"

    def test_vendor_mermaid_task_targets_v11(self) -> None:
        """REQ-R741-A02 — vendor-mermaid task must reference mermaid@11 and the asset path."""
        data = self._load()
        task = data["tasks"]["vendor-mermaid"]
        cmds = " ".join(str(c) for c in task.get("cmds", []))
        assert "mermaid@11" in cmds, "vendor-mermaid must curl mermaid@11"
        assert "assets/mermaid/mermaid.min.js" in cmds, (
            "vendor-mermaid must write to src/autoskillit/assets/mermaid/mermaid.min.js"
        )

    def test_test_filtered_task_exists(self):
        """TF-8 — test-filtered task exists in Taskfile.yml."""
        data = self._load()
        assert "test-filtered" in data["tasks"], "test-filtered task missing from Taskfile.yml"

    def test_test_filtered_delegates_to_test_check(self):
        """TF-9 — test-filtered delegates to test-check."""
        data = self._load()
        cmds = " ".join(str(c) for c in data["tasks"]["test-filtered"].get("cmds", []))
        assert "test-check" in cmds, "test-filtered must delegate to test-check"

    def test_test_filtered_sets_filter_env_default(self):
        """TF-10 — test-filtered defaults AUTOSKILLIT_TEST_FILTER to conservative."""
        data = self._load()
        cmds = " ".join(str(c) for c in data["tasks"]["test-filtered"].get("cmds", []))
        assert "AUTOSKILLIT_TEST_FILTER" in cmds, (
            "test-filtered must reference AUTOSKILLIT_TEST_FILTER"
        )
        assert "conservative" in cmds, (
            "test-filtered must default AUTOSKILLIT_TEST_FILTER to conservative"
        )


def test_taskfile_pytest_paths_exist() -> None:
    """All pytest file paths in Taskfile.yml must exist."""
    raw = TASKFILE.read_text()
    paths = re.findall(r"tests/[\w/]+\.py", raw)
    for path_str in paths:
        full = REPO_ROOT / path_str
        assert full.exists(), f"Taskfile references {path_str} but it does not exist"
