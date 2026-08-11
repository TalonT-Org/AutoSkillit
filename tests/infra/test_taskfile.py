from __future__ import annotations

import re
from pathlib import Path

import pytest

from autoskillit.core.io import load_yaml

pytestmark = [pytest.mark.layer("infra"), pytest.mark.medium]

REPO_ROOT = Path(__file__).parent.parent.parent
TASKFILE = REPO_ROOT / "Taskfile.yml"


class TestTaskfile:
    def _load(self) -> dict:
        return load_yaml(TASKFILE)

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

    def test_tmpdir_setup_delegates_without_recursive_delete(self) -> None:
        task = self._load()["tasks"]["_tmpdir-setup"]
        commands = "\n".join(str(command) for command in task["cmds"])
        assert "python3 scripts/pytest_tmp_lifecycle.py setup" in commands
        assert "rm " not in commands

    def test_pytest_paths_share_generation_template(self) -> None:
        variables = self._load()["vars"]
        assert "{{.PYTEST_RUN_ID}}" in variables["PYTEST_GEN_DIR"]
        assert "{{.PYTEST_GEN_DIR}}" in variables["PYTEST_TMPDIR"]
        assert "{{.PYTEST_GEN_DIR}}" in variables["PYTEST_CACHEDIR"]

    def test_pytest_generation_uses_dynamic_run_and_user_ids(self) -> None:
        variables = self._load()["vars"]
        assert variables["PYTEST_RUN_ID"]["sh"]
        assert variables["AUTOSKILLIT_UID"]["sh"] == "id -u"

    def test_cleanup_shm_delegates_pytest_reaping(self) -> None:
        task = self._load()["tasks"]["cleanup-shm"]
        commands = "\n".join(str(command) for command in task["cmds"])
        assert "python3 scripts/pytest_tmp_lifecycle.py reap" in commands

    def test_refresh_codex_hook_fixture_uses_tmp_lifecycle(self) -> None:
        task = self._load()["tasks"]["refresh-codex-hook-fixture"]
        commands = "\n".join(str(command) for command in task["cmds"])
        assert "_tmpdir-setup" in task["deps"]
        assert task["env"]["TMPDIR"] == "{{.PYTEST_TMPDIR}}"
        assert "--basetemp={{.PYTEST_TMPDIR}}" in commands
        assert "cache_dir={{.PYTEST_CACHEDIR}}" in commands

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

    def test_install_dev_task_uses_develop_branch(self):
        """TF-7 — install-dev installs from @develop and runs autoskillit install."""
        data = self._load()
        task = data["tasks"]["install-dev"]
        cmds = " ".join(str(c) for c in task.get("cmds", []))
        assert "@develop" in cmds, "install-dev must install from @develop branch"
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

    def test_coverage_audit_task_exists(self):
        """TF-11 — coverage-audit task exists in Taskfile.yml."""
        data = self._load()
        assert "coverage-audit" in data["tasks"], "coverage-audit task missing from Taskfile.yml"

    def test_test_local_task_exists(self):
        """TF-13 — test-local task exists in Taskfile.yml."""
        data = self._load()
        assert "test-local" in data["tasks"], "test-local task missing from Taskfile.yml"

    def test_test_local_defaults_to_aggressive(self):
        """TF-14 — test-local defaults AUTOSKILLIT_TEST_FILTER to aggressive."""
        data = self._load()
        assert "cmds" in data["tasks"]["test-local"], "test-local task has no cmds key"
        cmds = " ".join(str(c) for c in data["tasks"]["test-local"]["cmds"])
        assert "AUTOSKILLIT_TEST_FILTER" in cmds
        assert "aggressive" in cmds

    def test_test_local_delegates_to_test_check(self):
        """TF-15 — test-local delegates to test-check."""
        data = self._load()
        assert "cmds" in data["tasks"]["test-local"], "test-local task has no cmds key"
        cmds = " ".join(str(c) for c in data["tasks"]["test-local"]["cmds"])
        assert "test-check" in cmds

    def test_regen_contracts_task_exists(self):
        """TF-12 — regen-contracts task exists in Taskfile.yml."""
        data = self._load()
        assert "regen-contracts" in data["tasks"], "regen-contracts task missing from Taskfile.yml"

    def test_regen_contracts_has_status_block(self):
        """regen-contracts must have a status: block with at least one entry."""
        data = self._load()
        task = data["tasks"]["regen-contracts"]
        assert "status" in task, "regen-contracts must have a status: block"
        assert task["status"], "regen-contracts status: block must not be empty"

    def test_compile_recipes_task_exists(self):
        """compile-recipes task exists in Taskfile.yml."""
        data = self._load()
        assert "compile-recipes" in data["tasks"], "compile-recipes task missing from Taskfile.yml"

    def test_compile_recipes_has_status_block(self):
        """compile-recipes must have a status: block with at least one entry."""
        data = self._load()
        task = data["tasks"]["compile-recipes"]
        assert "status" in task, "compile-recipes must have a status: block"
        assert task["status"], "compile-recipes status: block must not be empty"

    def test_codex_config_parse_target_uses_supported_test_gate(self) -> None:
        data = self._load()
        task = data["tasks"]["test-codex-config-parse"]
        commands = "\n".join(str(command) for command in task.get("cmds", []))
        assert 'PYTEST_TEST_PATHS="tests/execution/backends/test_codex_interactive.py"' in commands
        assert "task test-all" in commands
        assert "python -m pytest" not in commands
        assert re.search(r"(^|\s)pytest(?:\s|$)", commands) is None

    def test_output_budget_e2e_target_selects_credentialed_smoke_test(self) -> None:
        data = self._load()
        task = data["tasks"]["test-smoke-output-budget-e2e"]
        commands = "\n".join(str(command) for command in task.get("cmds", []))
        preconditions = "\n".join(
            str(precondition) for precondition in task.get("preconditions", [])
        )
        assert "tests/server/test_output_budget_e2e.py" in commands
        assert "-m smoke" in commands
        assert ".autoskillit/temp/smoke-output-budget-e2e-" in commands
        assert "CODEX_SMOKE_TEST" in preconditions
        assert "CLAUDE_CODE_SMOKE_TEST" in preconditions

    def test_explorer_gate_consumes_current_attestation_version(self) -> None:
        data = self._load()
        task = data["tasks"]["test-smoke-codex-explorer-gate"]
        commands = "\n".join(str(command) for command in task.get("cmds", []))
        assert "codex-explorer-conformance-v7.json" in commands
        assert "codex-explorer-conformance-v6.json" not in commands

    def test_live_web_agent_gate_is_pinned_non_skippable_and_fresh(self) -> None:
        data = self._load()
        task = data["tasks"]["test-smoke-codex-web-agent-live-gate"]
        commands = "\n".join(str(command) for command in task.get("cmds", []))
        preconditions = "\n".join(
            str(precondition) for precondition in task.get("preconditions", [])
        )

        assert "tests/execution/backends/test_web_agent_live_gate.py" in commands
        assert "test_explorer_live_gate.py" not in commands
        assert "codex-cli 0.147.0" in preconditions
        assert task["env"]["AUTOSKILLIT_WEB_AGENT_LIVE_GATE"] == "1"
        assert 'rm -f "$EVIDENCE"' in commands
        assert "requires exactly one non-skipped test" in commands
        assert "live-web-agent-gate.json" in commands
        assert '"child_web_search":"live"' in commands
        assert '"parent_web_search":"disabled"' in commands

    def test_claude_startup_target_selects_exact_interactive_probe(self) -> None:
        data = self._load()
        task = data["tasks"]["test-smoke-claude-startup"]
        commands = "\n".join(str(command) for command in task.get("cmds", []))
        preconditions = "\n".join(
            str(precondition) for precondition in task.get("preconditions", [])
        )
        assert "tests/execution/backends/test_cli_conformance_probes.py" in commands
        assert "CLAUDE_STARTUP_K:-claude_startup_readiness" in commands
        assert ".autoskillit/temp/smoke-claude-startup-" in commands
        assert task["env"]["CLAUDE_CODE_SMOKE_TEST"] == "1"
        assert task["env"]["CLAUDE_STARTUP_READINESS_SMOKE"] == "1"
        assert "command -v claude" in preconditions
        assert "ANTHROPIC_API_KEY" in preconditions
        assert "CLAUDE_CODE_OAUTH_TOKEN" in preconditions
        assert ".credentials.json" not in preconditions

    def test_test_check_sets_experimental_enabled(self):
        """T16 — test-check must set AUTOSKILLIT_FEATURES__EXPERIMENTAL_ENABLED to "true".

        Guards against accidental removal of the env var. Without it, the test
        suite falls back to is_dev_install() (config/settings.py:513) and silently
        skips all EXPERIMENTAL-lifecycle tests on non-editable installs — a
        fail-open hidden in feature-gate resolution (issue #4385).
        """
        data = self._load()
        env = data["tasks"]["test-check"].get("env", {})
        assert "AUTOSKILLIT_FEATURES__EXPERIMENTAL_ENABLED" in env, (
            "test-check.env must set AUTOSKILLIT_FEATURES__EXPERIMENTAL_ENABLED "
            "so the test scope matches CI regardless of install type"
        )
        assert env["AUTOSKILLIT_FEATURES__EXPERIMENTAL_ENABLED"] == "true", (
            "test-check.env AUTOSKILLIT_FEATURES__EXPERIMENTAL_ENABLED must be 'true'"
        )

    def test_test_all_sets_experimental_enabled(self):
        """T17 — test-all must set AUTOSKILLIT_FEATURES__EXPERIMENTAL_ENABLED to "true".

        Mirrors the test-check guard. Both test entry points must request the
        same feature scope, otherwise local runs diverge from each other.
        """
        data = self._load()
        env = data["tasks"]["test-all"].get("env", {})
        assert "AUTOSKILLIT_FEATURES__EXPERIMENTAL_ENABLED" in env, (
            "test-all.env must set AUTOSKILLIT_FEATURES__EXPERIMENTAL_ENABLED"
        )
        assert env["AUTOSKILLIT_FEATURES__EXPERIMENTAL_ENABLED"] == "true", (
            "test-all.env AUTOSKILLIT_FEATURES__EXPERIMENTAL_ENABLED must be 'true'"
        )


def test_taskfile_pytest_paths_exist() -> None:
    """All pytest file paths in Taskfile.yml must exist."""
    raw = TASKFILE.read_text()
    paths = re.findall(r"tests/[\w/]+\.py", raw)
    for path_str in paths:
        full = REPO_ROOT / path_str
        assert full.exists(), f"Taskfile references {path_str} but it does not exist"
