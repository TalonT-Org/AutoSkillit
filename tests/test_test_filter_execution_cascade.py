"""REQ-EXEC-001..004: module-level cascade map for execution/ submodules."""

from __future__ import annotations

from pathlib import Path

from tests._test_filter import (
    MODULE_CASCADE_EXECUTION,
    FilterMode,
    build_test_scope,
)

_ALL_DIRS = [
    "core",
    "config",
    "execution",
    "pipeline",
    "workspace",
    "recipe",
    "migration",
    "fleet",
    "server",
    "cli",
    "hooks",
    "skills",
    "arch",
    "contracts",
    "infra",
    "docs",
]


def test_all_entries_present() -> None:
    """All documented module stems are present in MODULE_CASCADE_EXECUTION."""
    expected = {
        "anomaly_detection",
        "clone_guard",
        "ci",
        "merge_queue",
        "diff_annotator",
        "pr_analysis",
        "testing",
        "db",
        "recording",
        "github",
        "remote_resolver",
        "session",
        "quota",
        "session_log",
        "linux_tracing",
        "commands",
    }
    assert expected <= set(MODULE_CASCADE_EXECUTION.keys())


class TestBuildTestScopeExecutionCascade:
    """Routing via MODULE_CASCADE_EXECUTION in build_test_scope (CONSERVATIVE mode)."""

    ALL_DIRS = _ALL_DIRS

    def _make_tests_root(self, tmp_path: Path, dirs: list[str]) -> Path:
        tests_root = tmp_path / "tests"
        for d in dirs:
            (tests_root / d).mkdir(parents=True, exist_ok=True)
        return tests_root

    def test_narrow_module_uses_narrow_scope(self, tmp_path: Path) -> None:
        """anomaly_detection.py change → scope is {execution} only (+ always-run)."""
        tests_root = self._make_tests_root(tmp_path, self.ALL_DIRS)
        result = build_test_scope(
            changed_files={"src/autoskillit/execution/anomaly_detection.py"},
            mode=FilterMode.CONSERVATIVE,
            tests_root=tests_root,
        )
        assert result is not None
        dir_names = {p.name for p in result}
        assert "execution" in dir_names
        # Must NOT include any dirs not declared in the narrow entry
        for excluded in [
            "core",
            "cli",
            "server",
            "workspace",
            "migration",
            "pipeline",
            "recipe",
        ]:
            assert excluded not in dir_names, (
                f"narrow cascade for anomaly_detection should not include {excluded}"
            )
        # Always-run dirs must still be present
        assert "arch" in dir_names
        assert "contracts" in dir_names

    def test_narrow_modules_all_resolve_to_execution_only(self, tmp_path: Path) -> None:
        """Each of the narrowest stems maps to frozenset({"execution"})."""
        narrow_stems = [
            "anomaly_detection",
            "clone_guard",
        ]
        tests_root = self._make_tests_root(tmp_path, self.ALL_DIRS)
        for stem in narrow_stems:
            result = build_test_scope(
                changed_files={f"src/autoskillit/execution/{stem}.py"},
                mode=FilterMode.CONSERVATIVE,
                tests_root=tests_root,
            )
            assert result is not None, f"{stem} should return non-None result"
            dir_names = {p.name for p in result}
            assert "execution" in dir_names, f"{stem} should cascade to 'execution'"
            for excluded in ["core", "cli", "server", "workspace", "migration"]:
                assert excluded not in dir_names, (
                    f"{stem} narrow cascade should not include {excluded}"
                )

    def test_unknown_execution_stem_falls_through_to_cascade(self, tmp_path: Path) -> None:
        """headless.py (not in MODULE_CASCADE_EXECUTION) → cascade_map["execution"]."""
        tests_root = self._make_tests_root(tmp_path, self.ALL_DIRS)
        result = build_test_scope(
            changed_files={"src/autoskillit/execution/headless.py"},
            mode=FilterMode.CONSERVATIVE,
            tests_root=tests_root,
        )
        assert result is not None
        dir_names = {p.name for p in result}
        # LAYER_CASCADE_CONSERVATIVE["execution"] includes execution, core, workspace,
        # migration, server, cli, infra, skills
        for pkg in ["execution", "server", "cli", "workspace"]:
            assert pkg in dir_names, f"fail-open cascade for headless.py should include {pkg}"

    def test_medium_scope_module_ci(self, tmp_path: Path) -> None:
        """ci.py → frozenset({"execution"}) (its MODULE_CASCADE_EXECUTION entry)."""
        tests_root = self._make_tests_root(tmp_path, self.ALL_DIRS)
        result = build_test_scope(
            changed_files={"src/autoskillit/execution/ci.py"},
            mode=FilterMode.CONSERVATIVE,
            tests_root=tests_root,
        )
        assert result is not None
        dir_names = {p.name for p in result}
        assert "execution" in dir_names
        for excluded in ["core", "cli", "server", "workspace", "migration"]:
            assert excluded not in dir_names, f"ci narrow cascade should not include {excluded}"

    def test_recording_medium_scope_includes_server_files(self, tmp_path: Path) -> None:
        """recording.py entry includes specific server/ test files."""
        tests_root = self._make_tests_root(tmp_path, self.ALL_DIRS)
        # Create the specific server test file so it resolves in the result
        (tests_root / "server" / "test_factory_recording.py").write_text("")
        result = build_test_scope(
            changed_files={"src/autoskillit/execution/recording.py"},
            mode=FilterMode.CONSERVATIVE,
            tests_root=tests_root,
        )
        assert result is not None
        result_paths = {str(p) for p in result}
        result_names = {p.name for p in result}
        assert "execution" in result_names
        assert "test_factory_recording.py" in result_names, (
            "recording.py cascade should include server/test_factory_recording.py"
        )
        assert str(tests_root / "server" / "test_factory_recording.py") in result_paths

    def test_aggressive_mode_skips_execution_cascade_branch(self, tmp_path: Path) -> None:
        """In AGGRESSIVE mode the execution branch is not taken; maps to {"execution"}."""
        tests_root = self._make_tests_root(tmp_path, self.ALL_DIRS)
        result = build_test_scope(
            changed_files={"src/autoskillit/execution/anomaly_detection.py"},
            mode=FilterMode.AGGRESSIVE,
            tests_root=tests_root,
        )
        assert result is not None
        dir_names = {p.name for p in result}
        assert "execution" in dir_names
        # AGGRESSIVE maps execution → {execution} only; no widening via MODULE_CASCADE_EXECUTION
        for excluded in ["core", "cli", "server", "workspace", "migration"]:
            assert excluded not in dir_names, (
                f"AGGRESSIVE mode should not widen execution cascade to {excluded}"
            )


class TestClosureExecutionNarrowCascade:
    """__init__.py closure expansion for execution package."""

    ALL_DIRS = _ALL_DIRS

    def _make_execution_layout(self, tmp_path: Path, modules: dict[str, str]) -> Path:
        exec_dir = tmp_path / "src" / "autoskillit" / "execution"
        exec_dir.mkdir(parents=True)
        for name, content in modules.items():
            (exec_dir / name).write_text(content)
        tests_root = tmp_path / "tests"
        for d in self.ALL_DIRS:
            (tests_root / d).mkdir(parents=True, exist_ok=True)
        return tests_root

    def test_init_closure_narrow_single_cause(self, tmp_path: Path) -> None:
        """
        Changing anomaly_detection.py triggers closure to add execution/__init__.py.
        Because anomaly_detection is narrow, the __init__ back-propagation
        should still resolve to frozenset({"execution"}).
        """
        tests_root = self._make_execution_layout(
            tmp_path,
            {
                "anomaly_detection.py": "",
                "__init__.py": "from .anomaly_detection import AnomalyDetector\n",
            },
        )
        result = build_test_scope(
            changed_files={"src/autoskillit/execution/anomaly_detection.py"},
            mode=FilterMode.CONSERVATIVE,
            tests_root=tests_root,
        )
        assert result is not None
        dir_names = {p.name for p in result}
        assert "execution" in dir_names
        for excluded in ["core", "cli", "server", "workspace", "migration"]:
            assert excluded not in dir_names, (
                f"narrow closure cascade should not include {excluded}"
            )

    def test_init_closure_mixed_causes_falls_through(self, tmp_path: Path) -> None:
        """
        Changing both anomaly_detection.py (narrow) and headless.py (wide/unknown)
        → __init__ closure must fall through to cascade_map["execution"].
        """
        tests_root = self._make_execution_layout(
            tmp_path,
            {
                "anomaly_detection.py": "",
                "headless.py": "",
                "__init__.py": (
                    "from .anomaly_detection import AnomalyDetector\n"
                    "from .headless import HeadlessSession\n"
                ),
            },
        )
        result = build_test_scope(
            changed_files={
                "src/autoskillit/execution/anomaly_detection.py",
                "src/autoskillit/execution/headless.py",
            },
            mode=FilterMode.CONSERVATIVE,
            tests_root=tests_root,
        )
        assert result is not None
        dir_names = {p.name for p in result}
        # headless is not in MODULE_CASCADE_EXECUTION → fail-open → full execution cascade
        for pkg in ["execution", "server", "cli"]:
            assert pkg in dir_names, f"mixed-cause closure fallback should include {pkg}"

    def test_init_closure_all_narrow_causes_union(self, tmp_path: Path) -> None:
        """
        Changing ci.py + merge_queue.py (both narrow to {"execution"}) →
        __init__ closure union is still {"execution"}.
        """
        tests_root = self._make_execution_layout(
            tmp_path,
            {
                "ci.py": "",
                "merge_queue.py": "",
                "__init__.py": (
                    "from .ci import CIWatcher\nfrom .merge_queue import MergeQueueWatcher\n"
                ),
            },
        )
        result = build_test_scope(
            changed_files={
                "src/autoskillit/execution/ci.py",
                "src/autoskillit/execution/merge_queue.py",
            },
            mode=FilterMode.CONSERVATIVE,
            tests_root=tests_root,
        )
        assert result is not None
        dir_names = {p.name for p in result}
        assert "execution" in dir_names
        for excluded in ["core", "cli", "server", "workspace", "migration"]:
            assert excluded not in dir_names, (
                f"ci+merge_queue union closure should not include {excluded}"
            )
